# PLAN.md: Home Assistant control of a Legrand Nuvo P4300 (3-zone Player Portfolio amp)

Build one async Python library that talks UPnP/SOAP straight to the amp, and put both the FastAPI shim and the Home Assistant integration on top of it. Volume is almost certainly the standard UPnP RenderingControl service (GetVolume/SetVolume/GetMute/SetMute plus a vendor X_NUVO_AdjustVolume). Source switching is almost certainly Zone GroupCreate followed by AVTransport X_NUVO_PlayContainerURI with a line-in container URI. Neither is in the three SCPDs exported from the web UI. The "rotating port" is a normal dynamic UPnP HTTP port, not obfuscation.

## TL;DR

- **Protocol:** Each zone is its own UPnP root device with `deviceType` **`urn:schemas-nuvotechnologies-com:device:Zone:1`** (confirmed by nmap on the user's network) and service `urn:schemas-nuvotechnologies-com:service:Zone:1`. Use RenderingControl for volume and mute. For input selection, call GroupCreate and then X_NUVO_PlayContainerURI. The line-in payload is mpdrago's captured template, confirmed byte-for-byte on the P4300 (docs/protocol.md).
- **Ports:** The observed description ports (48127, 52657, 58587, 58887, 60707) all fall in the Linux ephemeral range, consistent with a gupnp/libsoup HTTP server bound to port 0 on every boot. Always resolve the port from the SSDP LOCATION header, cache it, and rediscover on any connection failure. SSDP will not cross VLANs unless relayed or the hosts are co-located.
- **Discovery target:** Search with `ST: urn:schemas-nuvotechnologies-com:device:Zone:1` rather than `ssdp:all`. It is faster and returns only Nuvo zones. (Verified: multicast and unicast both answer; `ssdp:all` kept as fallback.)
- **Architecture:** Do not fork d4yz/nuvo_p_series_home_assistant (a single README with commented-out upnpclient snippets, no integration code). Mine it and mpdrago's MIT-licensed scripts for protocol facts, then build fresh: `aionuvo` library, then FastAPI shim, then HA custom integration that imports the library directly (not via the shim).

## Status and revisions (2026-09-28)

| Phase | Status |
|---|---|
| 0 Environment | Done. No sudo on ubuntu-dev: `uv` venv on Python 3.13 (the `upnp-client` CLI breaks on 3.14); no nmap/tshark/gupnp-tools. |
| 1 Recon | Done, including write replay and a power cycle. App traffic capture (step 6) turned out unnecessary. |
| 2 `aionuvo` | Done, and now bundled at `custom_components/nuvo_player/aionuvo/`. Verified live on all zones: volume, mute, step, Line In, on/off, grouping, push events (0.2–0.5 s), recovery after a power cycle. 92 tests, 92% coverage; also passes on HA's pinned async-upnp-client 0.46.2. |
| 3 Shim | Code and tests done; ran against the amp. Docker image not built (no Docker on ubuntu-dev). 48 h soak outstanding. |
| 4 HA integration | **Built.** Config flow (SSDP, zeroconf, manual, options), media_player with grouping, TuneIn browsing and Line In feeds, diagnostics. Tone and loudness were built, then **removed** (see "Tone and loudness"). 21 HA tests; live test against the amp passed 3/3. Outstanding: a real HA install. Repo: https://github.com/mattmoo/ha-nuvo-player-portfolio. |

What changed from the original plan (details in docs/protocol.md):

1. **One source, not three.** There is a single "Line In" per zone. Analog and optical share the combo jack, which auto-detects, and no selector exists anywhere (UPnP or web settings). `AudioInput` (`player|toslink|rca`) is read-only.
2. **On/off (H4) resolved**, with a twist. Off = `GroupDisband`. After a power cycle, zones keep their group but play nothing. So "on" (in a group) and "playing" are separate: `aionuvo` exposes `is_on` and `playback_state`, and `turn_on()` re-asserts Line In when a zone is idle.
3. **Grouping is master/member**, verified on hardware. Join and leave use `GroupMemberSetGroup` (see Phase 2). Disbanding a master's group turns **every member off**, and a joined member's own AVTransport reads `NO_MEDIA_PRESENT`, so its state must come from the master.
4. **The port-scan fallback is dropped.** Unicast M-SEARCH to the zone IP works, and each zone also advertises `_nuvoplayer._tcp` over mDNS on a fixed port (4747), which is a better fallback if ever needed.
5. **Ports:** the shim API is on 8095 and its GENA callback on 8096. The original plan put both on 8095.
6. **Web UI / nSDK JSON API on :80** (serial-number login) exposes the full settings tree: bass, treble, balance, turn-on volume, idle timeout. It **writes via plain GET** (`/api/setData`), so it is hard-denied. Writing tone there saves the value but does not change the sound (see "Tone and loudness").
7. **HTTP playback: `SetAVTransportURI` + `Play` of a WAV crashes the zone's UPnP process** (it restarted on a new port). **`X_NUVO_PlayURI` with MP3 or FLAC works** (2026-09-30, heard by ear): files, endless live streams, pause/play, switching URL and stop, with no crash. So Music Assistant could drive the zones through HA; not implemented yet. See docs/protocol.md, "HTTP stream playback".
9. **The amp's `Get` lags its own events** by a second or two after group changes. Group state is updated optimistically and confirmed by events; reads are ignored briefly after writes.
10. **Tone and loudness are not controllable yet.** The obvious routes store values without applying them; see "Tone and loudness".
11. **Favourites:** the Nuvo favourites service is dead. **TuneIn** browse and playback work through the zone's own ContentDirectory (2026-09-28) and are exposed through HA's media browser; Podcasts are hidden until tested. Playing by guide ID alone (for short automation IDs) is untested.
8. **The DLNA DMR overlap is moot.** The zones embed a MediaRenderer but do not advertise it over SSDP, so HA's DLNA integration should not discover them. Still confirm in Phase 4.

## Key Findings (read before coding)

### What already exists

| Source | What it gives us | Maturity | Verdict |
|---|---|---|---|
| d4yz/nuvo_p_series_home_assistant | README only. Uses sync `upnpclient`. Shows `ZoneService.GetMemberID`, `GroupCreate(memberIDs='["<id1>","<id2>"]')` marked "Works", `GroupMemberSetGroup(memberIDs, groupID)`, `AVTransport.SetAVTransportURI(InstanceID='0', CurrentURI='nuvo:pandora:StationList/station_<id>')`, and references to `RenderingControl` and `ContentDirectory`. Hardcoded description URLs like `http://192.168.1.34:52657/00000000-0000-0000-0000-0025ed1cb9c8.xml`. | Proof-of-concept notes. No P4300 claim. | Do not fork. Use as protocol notes. |
| mpdrago/nuvo-zone-keepalive (MIT) | Reverse-engineered from a decrypted WiFi capture of the Nuvo app. SSDP discovery that reads the description XML for control URLs and re-runs every 60 s to self-heal on IP/port change. `GroupCreate` then `X_NUVO_PlayContainerURI` selects Line In; the second call depends on state set by the first. Zone IDs are `memberId-<mac>` (lowercase, no colons). `Get` returns `Active`/`PowerState` = "active" whether grouped or not; `MemberGroup` is the real "is playing/grouped" signal. `UserTap` behaves like GroupCreate. RenderingControl has standard volume/mute plus `X_NUVO_AdjustVolume`. | Tested on one P3100. Lists P4300 as "very likely" compatible, unverified. | Best reference. Clone and read in Phase 1. |
| Legrand official | "API Request Form" for integrators. Drivers exist for Control4, Crestron, RTI, Vantage, KNX, HomeSeer. RTI driver v1.2 adds zone grouping and direct access to line inputs. HomeSeer plugin discovers players via Zeroconf. Per-zone web pages at `http://<zone-ip>/diagnostics.fcgi` and `/firmware.fcgi`. | Official API is gated. | Optionally submit the request form in parallel. Do not wait on it. |
| sprocket-9/hacs-nuvo-serial, ejonesnospam, openHAB nuvo binding | RS-232 control for Grand Concerto/Essentia. | Mature, different product family. | Not applicable to the protocol. Useful for HA group/join UX patterns. |

The HomeSeer plugin's use of Zeroconf suggests the players also advertise over mDNS, which would give a second discovery path across VLANs via an mDNS reflector.

### Why the port "rotates"

- GUPnP contexts created with port 0 let libsoup pick a random unused port. The `X_NUVO_`/gupnp hints in the SCPDs point to this stack.
- libupnp/npupnp instead picks the first free port at or above 49152. The observed spread (48127 to 60707, including one below 49152) matches kernel ephemeral allocation, i.e. gupnp/libsoup.
- General HA community guidance: DLNA endpoints frequently change listening ports and announce the change only via UPnP discovery.
- Conclusion: expected behaviour. Treat the port as volatile. IP and UDN are the stable identifiers.

### Device model to expect on a P4300

- **Confirmed:** nmap reports `deviceType: urn:schemas-nuvotechnologies-com:device:Zone:1`, so zones are vendor-typed root devices, not embedded children of a single amp device.
- d4yz and mpdrago examples show a different IP per zone, with a MAC-derived UDN and description file `00000000-0000-0000-0000-<mac>.xml`. **Expect three root devices, possibly three IPs and three MACs.** Verify in Phase 1 by counting distinct UDNs answering the Zone device type. This decides how many DHCP reservations are needed and whether HA shows one hub or three devices.
- Legrand's NVP4300NA page states all line inputs and outputs use combination 3.5 mm analog/TOSLINK connectors, matching `AudioInput` allowed values `player`, `toslink`, `rca`.
- Discontinued: replaced by NVP5100NA; Nuvo production ceased in 2021, last firmware fix March 2026, no technical assistance. Expect no protocol changes and no vendor help.

## Recommended architecture

```
aionuvo/            # pip-installable async library (the only code that speaks UPnP)
  discovery.py      # SSDP multicast + unicast M-SEARCH, description cache, rediscovery
  zone.py           # NuvoZone: volume, mute, source, power/group, transport, events
  system.py         # NuvoSystem: set of zones, group ops, member-id mapping
  models.py         # dataclasses: ZoneState, Source, GroupInfo
  const.py          # ZONE_DEVICE_TYPE = "urn:schemas-nuvotechnologies-com:device:Zone:1"
shim/               # FastAPI app importing aionuvo (dev tool + non-HA clients)
custom_components/nuvo_player/   # HA integration importing aionuvo directly
docs/protocol.md    # ground truth from recon, updated every phase
captures/           # pcaps + extracted SOAP bodies (gitignored if they contain secrets)
fixtures/           # recorded XML descriptions, SCPDs, SOAP responses, NOTIFY bodies
```

**Decision: HA talks to the device directly through `aionuvo`, not through the shim.** Reasons:
1. HA's SSDP integration hands the config flow the discovered LOCATION, so rediscovery comes for free.
2. GENA push events need a callback listener, and HA already has that pattern via `async_upnp_client` (the library behind HA's DLNA integration).
3. Routing through the shim adds a container that can fail and a second cache that can go stale.
4. HACS/HA convention: integrations depend on a PyPI library listed in `manifest.json` `requirements`.

The shim remains useful as a debugging console, for curl/Node-RED, and as the Phase 1 experiment harness. Build `aionuvo` on `async_upnp_client` (SSDP search/listen, description parsing, action calls, event handler).

## Phase 0: Environment and safety

**Tooling on ubuntu-dev**
```bash
sudo apt install -y python3-venv tcpdump tshark wireshark nmap gupnp-tools curl jq xmlstarlet
python3 -m venv .venv && . .venv/bin/activate
pip install async-upnp-client httpx fastapi uvicorn pytest pytest-asyncio respx lxml
git clone https://github.com/mpdrago/nuvo-zone-keepalive refs/nuvo-zone-keepalive
```
`gupnp-tools` provides `gssdp-discover` and `gupnp-universal-cp`. `async-upnp-client` installs the `upnp-client` CLI (`search`, `call-action`, `subscribe`, `--debug-traffic`).

**Network prerequisites**
- DHCP reservations for every MAC the amp uses (expect one per zone; confirm in Phase 1).
- Put ubuntu-dev on the amp's VLAN for recon (a second tagged vNIC works), or relay SSDP. Record which VLAN the successful nmap scan ran from: if nmap saw the device type via broadcast discovery, multicast already works on that path.
- Cross-VLAN options, in order of preference:
  1. HA and the amp share a VLAN.
  2. Multicast relay for `239.255.255.250:1900` (e.g. `udpbroadcastrelay --id 1 --port 1900 --dev <ifA> --dev <ifB> --multicast 239.255.255.250`, or `shyndman/multicast-relay`, which also relays mDNS `224.0.0.251:5353`). Firewall must also allow unicast SSDP replies and HTTP/SOAP to the amp's ephemeral ports.
  3. Library's unicast M-SEARCH fallback to `<zone-ip>:1900` with `ST: urn:schemas-nuvotechnologies-com:device:Zone:1`.
- **GENA callbacks go the other way.** The amp must open TCP to HA's/shim's callback port. Pin it (8096 for the shim, whose API is on 8095; configurable for HA, like HA DLNA DMR's "Event listener port") and allow amp VLAN to HA on it.

**Safety rules (hard rules for the agent)**
- NEVER call `RestoreFactoryDefaults`, `SystemCreate`, `SystemJoin`, `SystemConfigureGWs`. Never POST to `/firmware.fcgi`.
- Start read-only: `Get*` actions, description/SCPD GETs, SUBSCRIBE.
- Write actions only after user approval, one at a time, with before-state recorded: `SetVolume` (start low), `SetMute`, `GroupCreate`, `GroupDisband`, `X_NUVO_PlayContainerURI`, `SetAVTransportURI`, `Play`/`Pause`/`Stop`.
- Keep a `docs/safety.md` denylist and enforce it in `aionuvo` with a guard that raises on denylisted actions unless `NUVO_I_KNOW=1`.
- Before any write testing, record the current app configuration (zone names, groups, favourites).

**Acceptance:** venv builds; `gssdp-discover` runs; at least one zone description URL reachable; DHCP reservations confirmed by the user.

## Phase 1: Discovery and reconnaissance (read-only first)

**1. SSDP inventory**
```bash
# Targeted: Nuvo zones only (deviceType confirmed by nmap)
gssdp-discover -i <iface> --timeout=10 -t urn:schemas-nuvotechnologies-com:device:Zone:1 \
  | tee captures/ssdp-zones.txt
# Broad: everything, to spot other advertised types on the same UDNs
gssdp-discover -i <iface> --timeout=10 | tee captures/ssdp-all.txt
upnp-client --pprint search --timeout 10 \
  --search_target urn:schemas-nuvotechnologies-com:device:Zone:1 | tee captures/ssdp-search.json
sudo tcpdump -i <iface> -w captures/ssdp.pcap udp port 1900 &   # run during the above
```
Record, per zone: IP, MAC, LOCATION, USN/UDN, all ST/NT values advertised by that UDN, the SERVER header (identifies the stack), and CACHE-CONTROL max-age. Count distinct UDNs answering the Zone device type (expect 3).

Also:
- Check whether each UDN additionally advertises a standard `urn:schemas-upnp-org:device:MediaRenderer:1`. If so, HA's built-in DLNA DMR integration may claim the zones: either a quick volume-only win or a duplicate to suppress. Record which.
- Test unicast M-SEARCH to each zone IP on 1900 with the Zone device type as ST, and note whether it answers.
- Run `avahi-browse -art` to check for Zeroconf advertisements.

**2. Full device tree and every SCPD**
Write `tools/dump_device.py` (async_upnp_client). For each LOCATION, save the description XML and every SCPD into `fixtures/<udn>/` and print: deviceType, service type, serviceId, controlURL, eventSubURL, and actions with arguments and allowed ranges. Compare against `refs/nuvo-zone-keepalive/list_actions.py`. Confirm RenderingControl exists (and ContentDirectory, if present). Record the Volume `allowedValueRange` (min/max/step) and the full `X_NUVO_AdjustVolume` signature.

**3. Read the prior art in the cloned repo**
From `lineIn-body-template.xml`, `wake_zone.sh`, `common.sh`, `discover.py`, `list_zones.py`, extract:
- the exact `X_NUVO_PlayContainerURI` arguments (container URI, metadata, InstanceID);
- the exact `memberIDs` format for GroupCreate;
- the SSDP ST used (compare against the confirmed Zone device type);
- control URL paths.

Copy into `docs/protocol.md` with attribution.

**4. Read-only state**
Call `ZoneService.Get`, `GetActive`, `GetConnecting`, `GetMemberID`, `GetSystemID`, `GetFirmwareVersion`; `RenderingControl.GetVolume(InstanceID=0, Channel=Master)`, `GetMute`; `AVTransport.GetMediaInfo`, `GetTransportInfo`, `GetPositionInfo`, `GetCurrentTransportActions`; `ConnectionManager.GetProtocolInfo`. Save all responses as fixtures.

**5. GENA subscriptions (read HttpPort)**
```bash
upnp-client --pprint subscribe <LOCATION> '*' | tee captures/events-<zone>.jsonl
```
Leave running while using the app. Capture the initial event containing `HttpPort`; ZoneService `AudioInput`/`PowerState`/`MemberGroup` changes; AVTransport `LastChange`; RenderingControl `LastChange` (volume). If `HttpPort` resolves to a port, GET `/` and common paths on it (read-only) and look for a JSON API or websocket.

**6. Decisive step: capture the official app and web UI**
- Web UI on ubuntu-dev: devtools "Save all as HAR" plus `tcpdump -i <iface> host <zone-ip> -w captures/webui.pcap`.
- Mobile app traffic, options best first:
  - (a) switch port mirror (SPAN) of the amp's port to a ubuntu-dev NIC;
  - (b) `tcpdump` on the router/firewall interface facing the amp VLAN, if the phone is routed;
  - (c) Android emulator or Waydroid on ubuntu-dev running the Nuvo Player app;
  - (d) ARP spoofing on the amp VLAN (bettercap), only with user approval.

  WPA3 captures cannot be decrypted after the fact.
- Scripted scenario, one action per timestamped marker:
  1. volume up one step;
  2. set volume to a specific value;
  3. mute and unmute;
  4. zone 1 to Line In (analog);
  5. zone 1 to optical;
  6. zone 1 to a streaming favourite;
  7. group zones 1 and 2;
  8. ungroup;
  9. zone off/standby;
  10. zone on.
- Extract SOAP: `tshark -r app.pcap -Y 'http.request.method==POST' -T fields -e frame.time -e ip.dst -e tcp.dstport -e http.soap_action -e http.file_data > captures/soap-actions.tsv`. Also grep for `SUBSCRIBE`, `NOTIFY`, websocket upgrades, and non-UPnP ports.

**Hypotheses to confirm or reject in docs/protocol.md**
- H1: volume = `RenderingControl.SetVolume(InstanceID=0, Channel=Master, DesiredVolume=N)`; step buttons use `X_NUVO_AdjustVolume`.
- H2: local input = `GroupCreate(memberIDs=[memberId-<mac>])` then `X_NUVO_PlayContainerURI(<line-in container URI>)`, with distinct URIs/arguments for analog vs TOSLINK.
- H3: streaming favourites = `SetAVTransportURI` / `X_NUVO_PlayURI` with `nuvo:<service>:...` URIs.
- H4: "power off" in the app = `GroupDisband` or `Stop`; `standby` PowerState is set by idle timeout rather than an action. Grouping zones with local line-in may switch the system to "Global line in mode".

**Phase 1 outputs:** `docs/protocol.md` covering the device tree, per-zone identifiers, service table, action signatures, confirmed source and volume call sequences with exact SOAP bodies, event variables and meaning, port behaviour across a power cycle (reboot once and record new ports), whether a MediaRenderer type is also advertised, and open questions. Plus `fixtures/` and `captures/`.

**Acceptance:** all three zones enumerated via the Zone device type; volume and source sequences confirmed and replayed once with user approval; port change across reboot documented.

**STOP AND ASK the user:** after the device tree is dumped (confirm zone count and IP layout), before the first write action, before any ARP spoofing, and if H2 is not confirmed.

## Phase 2: `aionuvo` async client library

**API sketch**
```python
system = await NuvoSystem.discover(timeout=5, hosts=["10.0.30.21", ...])  # hosts = unicast fallback
zone = system.zones["memberId-0025ed1e7c0b"]
await zone.async_update()                  # Get + GetVolume + GetMute + GetTransportInfo + GetMediaInfo
await zone.set_volume(0.35)                # float 0..1 mapped to device range from SCPD
await zone.volume_step(+1)                 # X_NUVO_AdjustVolume if present
await zone.set_mute(True)
await zone.select_source("line_in")        # leave other group if joined, GroupCreate if off, X_NUVO_PlayContainerURI
await zone.turn_on() / zone.turn_off()     # mapping decided in Phase 1 (H4)
await system.group(master, [z2, z3]); await system.ungroup(z2)   # GroupMemberSetGroup (verified)
await zone.play() / pause() / next() / previous()
zone.subscribe(callback)                   # GENA push, auto-renew
```

**Discovery and port resilience (the core requirement)**
- Key zones by UDN or member ID, never by URL. Keep `location` in memory and in an optional cache file.
- Discovery order:
  1. HA-provided LOCATION, or the cache.
  2. Multicast M-SEARCH with `ST: urn:schemas-nuvotechnologies-com:device:Zone:1` (`ssdp:all` filtered by deviceType as fallback).
  3. Unicast M-SEARCH with the same ST to configured hosts on 1900.
  4. ~~Port probe over 32768–60999~~ dropped: unicast M-SEARCH answers reliably. If ever needed, use mDNS `_nuvoplayer._tcp` (fixed port 4747) instead.
- Listen passively for `ssdp:alive` NOTIFY with `NT` equal to the Zone device type. A changed LOCATION for a known UDN triggers an immediate swap.
- On `ClientConnectorError`, timeout, or HTTP 404/412 on control or event URLs: mark the zone stale, rediscover that UDN with backoff (1, 2, 5, 10, 30 s, capped), rebuild service proxies, re-subscribe, retry the pending command once.
- Subscriptions: explicit callback host/port; TIMEOUT renewal at 50% of the granted duration; re-SUBSCRIBE after rediscovery. If events stop for more than 2× the renewal interval, fall back to polling every 30 s.
- Per-zone asyncio lock so GroupCreate and PlayContainerURI are not interleaved with other writes.
- Safety denylist guard in the SOAP call path.
- Source list: `line_in` ("Line In") only. Analog and optical are auto-detected on one jack. Favourites are deferred (H3 untested).
- Grouping (verified): join = `GroupMemberSetGroup(memberIDs=[joiners], groupID=<master MemberGroup>)` on the master; member leaves = `GroupMemberSetGroup([self], "")`; master releases members = `GroupMemberSetGroup([members], "")`; master off = `GroupDisband` (all members go off). A joined member reports the master's playback state.

**Tests:** pytest with a fake device serving recorded `fixtures/` XML over aiohttp on a random port, advertising the real Zone deviceType, and replaying recorded SOAP responses. Cover:
- port change mid-session (restart the fake on a new port, send a new NOTIFY);
- dropped subscriptions;
- SOAP faults;
- denylist enforcement;
- volume mapping at range edges;
- non-Nuvo devices on the network being ignored.

**Acceptance:** against the real amp, set/read volume and switch source on each zone; push events update state within 2 s of an app change; after an amp power cycle the library recovers without restart; ≥85% line coverage on discovery and zone modules.

**STOP AND ASK:** if power semantics (H4) are ambiguous, and before publishing to PyPI.

## Phase 3: FastAPI REST shim

**Endpoints** (zone_id = member ID or slug of Title)
```
GET  /health                     -> 200 if ≥1 zone reachable; per-zone last_seen, location
GET  /zones                      -> id, name, ip, location, power, source, volume, muted, group
GET  /zones/{id}
PUT  /zones/{id}/volume          {"level": 0.0-1.0}  | POST /zones/{id}/volume/step {"delta": 1}
PUT  /zones/{id}/mute            {"muted": true}
PUT  /zones/{id}/source          {"source": "line_in"}
GET  /zones/{id}/sources
PUT  /zones/{id}/power           {"on": true}
POST /zones/{id}/transport/{play|pause|stop|next|previous}
GET  /zones/{id}/now_playing
POST /groups                     {"members": [...]} ; DELETE /groups/{group_id} ; DELETE /zones/{id}/group
POST /discovery/refresh
GET  /events                     -> Server-Sent Events stream of state changes
```
- Pydantic models, OpenAPI at `/docs`, optional bearer token via env var. No arbitrary SOAP passthrough except `/debug/call` behind `NUVO_DEBUG=1`, still denylist-enforced.
- **Docker for Unraid:** `python:3.12-slim`, non-root user, `network_mode: host` (needed for SSDP multicast and GENA callbacks). Env: `NUVO_HOSTS`, `NUVO_CALLBACK_PORT`, `NUVO_API_TOKEN`, `NUVO_INTERFACE`. `HEALTHCHECK CMD curl -fs localhost:8095/health`. Provide an Unraid template XML. If host networking is not possible, use macvlan on the amp's VLAN.
- Tests: FastAPI TestClient against the Phase 2 fake device; contract test per endpoint; SSE test.

**Acceptance:** from another VLAN host, `curl` changes volume and source on all zones; the container survives an amp reboot and reports healthy within 60 s; 48 h on Unraid without leaks or stuck subscriptions.

## Phase 4: Home Assistant custom integration (`nuvo_player`)

- **manifest.json:**
  ```json
  {
    "domain": "nuvo_player",
    "config_flow": true,
    "iot_class": "local_push",
    "integration_type": "hub",
    "dependencies": ["ssdp"],
    "requirements": ["aionuvo==x.y.z"],
    "ssdp": [{"deviceType": "urn:schemas-nuvotechnologies-com:device:Zone:1"}]
  }
  ```
  Matching on `deviceType` makes HA fetch each zone's description, so the config flow receives the zone's friendlyName, UDN and current LOCATION. mDNS is confirmed (`_nuvoplayer._tcp.local.`), so add it as a `zeroconf` matcher too; it helps across VLANs with an mDNS reflector.
- **DLNA DMR overlap:** the MediaRenderer is embedded but not advertised, so HA's DLNA DMR should not discover it. Confirm once in HA; if it does appear, ignore that discovery.
- **Config flow:**
  - `async_step_ssdp`: unique_id = system ID (or first zone UDN); abort if configured; update stored host/location on rediscovery.
  - `async_step_user`: manual entry of one or more zone IPs, via unicast M-SEARCH with the Zone device type.
  - Options flow: rename or exclude sources, volume max cap, callback port.
  - One config entry per Nuvo system; each zone is a HA device.
- **Entities per zone:**
  - `media_player` with VOLUME_SET, VOLUME_STEP, VOLUME_MUTE, SELECT_SOURCE, TURN_ON/OFF, and GROUPING: `join_players` → `system.group(self, members)`, `unjoin_player` → `system.ungroup(self)`, `group_members` = master first. Add PLAY/PAUSE/STOP/NEXT/PREVIOUS only when `GetCurrentTransportActions` allows (it is `Play,Stop` on Line In, where they are meaningless, so leave them off for Line In).
  - State: `off` when not in a group; `playing` when the master's transport is PLAYING; otherwise `idle` (e.g. after a power cycle).
  - Turning off a group master turns off its members; the entity's turn_off docstring and README must say so.
  - Now-playing title, artist, album, art from AVTransport metadata.
  - Optional diagnostic sensors: firmware, IP, location port.
- **Updates:** push first (aionuvo subscriptions dispatch to entities); light `DataUpdateCoordinator` poll (60 s) as heartbeat and fallback; entities unavailable while a zone is stale; HA SSDP callbacks feed new LOCATIONs into `aionuvo`.
- **Packaging:** `hacs.json`; `custom_components/nuvo_player/` with `translations/en.json`; `diagnostics.py` redacting MACs; brand assets; GitHub Actions running hassfest, HACS validation, and pytest (pytest-homeassistant-custom-component with mocked aionuvo).
- **Fork vs fresh:** fresh repo; credit d4yz and mpdrago in the README.

### Music Assistant / Sendspin

MA cannot discover the zones as players: they advertise no AirPlay, Cast, Sendspin or DLNA renderer (re-checked by mDNS 2026-09-30). **Revised 2026-09-30:** HTTP playback via `X_NUVO_PlayURI` works with MP3 and FLAC, so MA could play to a zone through its Home Assistant players provider if the integration accepted URLs. **Built 2026-09-30:** `NuvoZone.play_url` and `play_media` for URLs and media sources; pause/play offered while a stream plays, stop always (a no-op elsewhere, because MA stops a playing player first). No sync with other MA players, so the amplifier pattern below stays the way to do synced multi-room. Not yet tried with a real MA stream. The zones are **amplifiers fed by the MA players**: each Chromecast Audio (or later a Sendspin receiver) feeds a zone's optical Line In.

- MA already supports this: in each MA player's settings, set **Power control** and **Volume control** (and optionally **Mute control**) to the matching `media_player.nuvo_<zone>` HA entity, via MA's Home Assistant plugin. When MA plays, it turns the zone on (to Line In) and routes volume to the Nuvo.
- Requirements this puts on the integration:
  - `turn_on` must be idempotent and fast (about 1 s, verified);
  - `turn_on` must not disturb a zone already joined to another group (implemented: no-op if already playing);
  - volume must be 0..1 with push updates.
- Sendspin is the same pattern. A Sendspin receiver with S/PDIF out on the Line In replaces a CCA; the Nuvo side is unchanged. A native Sendspin client on the zone is impossible (closed firmware).
- Multi-room sync is best left to MA's player groups (CCA/Sendspin sync). Nuvo grouping makes one zone's Line In play in several rooms, which is useful only when the MA feed is on a single CCA.
- Optional later: a small MA-side test that playing to each CCA wakes the right zone.

**Acceptance:** SSDP auto-discovery shows "Discovered: Nuvo" in HA; three media_players with working source select and volume slider; app-side changes appear in HA within 2 s; automatic recovery after an amp reboot (port change); hassfest and HACS validation pass.

**Decisions (user, 2026-09-28):**
- One config entry per Nuvo system. **Each zone is its own HA device** with one `media_player` named after the zone, so each can be put in its own area. The integration never assigns areas and sets no `suggested_area`.
- `aionuvo` is **bundled inside the integration** (no PyPI package, no add-on or extra container). HA core already ships `async-upnp-client`/`aiohttp`.
- Sources: Line In, and **explore streaming favourites** (read-only `X_NUVO_Browse` first; any playback test needs approval, given the HTTP-playback crash).
- ~~Tone controls: a narrow exception to the nSDK `setData` denylist.~~ **Withdrawn 2026-09-28**: the writes never reached the sound. Tone and loudness entities removed (see "Tone and loudness").
- **Optional Line In feed per zone (user decision 2026-09-28).** Options → Line In feeds maps each zone to the player wired to its Line In. When that player goes to `playing` (not from `unavailable`/`unknown`), the zone runs `turn_on`, i.e. switches to Line In, turning on if needed, unless it listens to another zone's group. This works without Music Assistant's power control and overrides TuneIn. MA's power/volume control remains the alternative and needs no mapping here.
- `turn_on` switches a zone playing TuneIn to Line In (so MA power control wins over TuneIn); it stays a no-op on Line In or while joined to another zone's playing group.

**STOP AND ASK:** before any favourites playback test, and before a public HACS release.

## Tone and loudness (removed 2026-09-28; open problem)

**Status:** not supported. Bass, treble, balance (`number`) and loudness (`switch`) were built,
tested against the fake zone, and shipped on `main` up to commit `79f6c3e`. Testing in a real HA
install showed that none of them changes the sound, so they were removed. The integration deletes
their leftover entities at startup (`_remove_retired_entities` in `__init__.py`).

### What was tried, and what we learned

| Control | Route used | Result on a P4300 (Dining Room) |
|---|---|---|
| Bass, treble, balance | nSDK web API on :80, `GET /api/setData?path=settings://mediaPlayer/<key>&roles=value&value={"type":"double_","double_":N}` after `POST /api/authenticate` with the base64 serial | The value is **stored** and reads back, but the sound does not change and the Nuvo app does not show it. Balance −18 held for minutes: no audible change. |
| Loudness | UPnP `RenderingControl.SetLoudness` | Accepted, evented back (`LastChange`), reads back, but **no audible difference** and the app does not update. `settings://mediaPlayer/loudness` stayed `false` while UPnP said `true`: two separate stores. |

The reverse direction works: when the **app** changes bass or balance, the new value appears in
`settings://mediaPlayer/*` (read with `getData`), so HA could show it within one poll. So the app
applies tone through a route we cannot see, and stores the value in the settings tree as a side effect.

Ruled out:
- **UPnP:** no tone actions in any SCPD (only `SetLoudness`, `ListPresets`/`SelectPreset`), and no
  tone events during a 30-minute recording while the app changed bass and balance.
- **Web UI pages:** `home.fcgi` and `diagnostics.fcgi` have no audio controls. The API root is titled
  "Settings (debug)", which suggests `settings:/` is a raw store that bypasses the apply logic.
- **Other nSDK namespaces:** `getRows` on `systemMgmt:`, `player:`, `network:`, `lineIn:` returns
  nothing; `mediaPlayer:`, `nuvo:`, `ui:`, `audio:`, `dsp:`, `svx:`, `zone:`, `av:` time out.
  `roles=activate` exists for action nodes (`systemMgmt:startCaptureNetworkTraffic`), so an
  activate-style tone action is plausible but was not found.
- **HTTP proxy on the phone (mitmproxy):** the Nuvo app ignores the phone's proxy setting for LAN
  traffic, so nothing was captured.

Unexplained, possibly related: at the moment of a balance write, `VolumeDB` once changed
(−80.79 → −81.16 dB) with `Volume` unchanged.

### How to pick this up

The missing piece is the Nuvo app's traffic while it changes a tone. Options, easiest first:

1. **Android phone: PCAPdroid** (free, no root). Choose the Nuvo app as the target and start
   capturing (plain PCAP; no TLS decryption needed for LAN traffic). In the app, change **one**
   control on **one** zone, noting the time (e.g. Dining Room bass +2, then loudness on), then stop
   and export the `.pcap`. Look at traffic to the zone IPs (192.168.1.52–54): HTTP to :80 (nSDK
   `/api/*`), SOAP to the zone's UPnP port, or anything on other ports (e.g. a WebSocket, or 4747,
   the `_nuvoplayer._tcp` mDNS service).
2. **iPhone:** a Mac with Xcode tools, `rvictl -s <UDID>`, then Wireshark on the `rvi0` interface.
3. **At the network:** a switch port mirror (SPAN) of the amp's port, or a capture on the router,
   as in Phase 1. mpdrago/nuvo-zone-keepalive worked this way (a decrypted Wi-Fi capture).

Things to look for: a `setData` with `roles=activate` or a different path; an nSDK path outside
`settings:`; `/api/event/*` queue calls; a Nuvo-specific port; a different value type (e.g. `i32_`
rather than `double_`).

### Rules for the next attempt

- Every write is a hardware write test: **STOP AND ASK**, one zone, record the before-state, restore after.
- Check a zone is **not in use** first (Dining Room was playing when we tested; see docs/protocol.md).
- A route counts as working only if the change is **audible and shows in the Nuvo app**. Reading
  back the stored value proves nothing.
- Any web-API write needs a new, narrow exception in `docs/safety.md` and `aionuvo/safety.py`
  (the old one was withdrawn).
- To restore the entities, start from `79f6c3e`: `number.py`, `switch.py`, `icons.json`,
  `aionuvo/webapi.py`, the tone and loudness parts of `zone.py`, `models.py` and `safety.py`, and their tests.

## Risks and unknowns

| Risk | Impact | Mitigation |
|---|---|---|
| ~~Line-in URI/arguments differ on P4300 vs P3100~~ | Resolved | Payload verified on P4300; analog/optical auto-detected. |
| Source changes only take effect inside a group and groups idle-timeout | Zone silently drops | `select_source` creates a group when needed; expose `playback_state` (not just `MemberGroup`) as the playing indicator. |
| Zones come back grouped but silent after a power cycle | Looks "on" but no audio | `playback_state` = idle; `turn_on` re-asserts Line In. |
| Turning off a group master silences its members | Surprise in other rooms | Documented; members leave individually via `turn_off`/unjoin. |
| Sending HTTP media to a zone | UPnP process restarts (~15 s outage, new port) | Not implemented; recovery path handles it; do not retry without approval. |
| App and HA used side by side cause state fights | Confusing state | Push subscriptions keep HA in sync; test for conflicts. |
| GENA callbacks blocked across VLANs | No push; stale UI | Pinned callback port plus firewall rule; polling fallback. |
| HA's DLNA DMR integration also claims the zones | Duplicate entities | Unlikely: MediaRenderer is not advertised. Confirm in Phase 4. |
| Cloud-dependent features degrade (product discontinued) | Streaming sources may vanish | Prioritise local inputs and volume; favourites optional. |
| Accidental destructive call | Bricked or reset system | Denylist guard, approval gate, pre-change record of app config. |
| Official API terms restrict use | Legal/ToS ambiguity | Local reverse-engineering for personal interoperability; do not redistribute vendor documents. |

## Caveats

- Every call used by `aionuvo` has been confirmed on this P4300 (firmware 2025.1). Other models and firmware remain unverified.
- The port behaviour is confirmed as GUPnP 0.20.8 (SSDP `SERVER` header) and observed across a power cycle.
- Not yet confirmed by ear: that a joined member is audible. Not yet measured: latency of app-side changes reaching HA.
