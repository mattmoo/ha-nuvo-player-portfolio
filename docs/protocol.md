# Nuvo P4300 protocol notes

Ground truth from recon on the user's own system. Everything here was observed
directly unless marked **(prior art)** or **(unverified)**.

Prior art credited: [mpdrago/nuvo-zone-keepalive](https://github.com/mpdrago/nuvo-zone-keepalive)
(MIT; P3100 capture) and [d4yz/nuvo_p_series_home_assistant](https://github.com/d4yz/nuvo_p_series_home_assistant).

## Phase 1 status (2026-09-28)

| Step | Status |
|---|---|
| 1. SSDP inventory | Done |
| 2. Device tree + SCPDs | Done, see `fixtures/<udn>/` |
| 3. Prior art | Done |
| 4. Read-only state | Done, see `fixtures/<udn>/soap/` |
| 5. GENA subscribe | Done: initial events plus change events during the write tests (`captures/events-writetest-lounge.jsonl`) |
| 6. App/web UI capture | Web UI and nSDK JSON API read (see below). App capture not needed for H1, H2 or H4. |
| Port change across reboot | **Done 2026-09-28**: all three ports changed; the library recovered from a stale cache |
| Write replay (H1/H2/H4) | **Done on Lounge, 2026-09-28**, with user approval |

## System inventory

One Nuvo system, three zones, **three IPs and three MACs** (static per the user; the zones report DHCP, so these are reservations). Each zone is its own
UPnP root device.

| Zone (Title) | IP | MAC / member ID | Description port | Icon |
|---|---|---|---|---|
| Dining Room | 192.168.1.52 | `memberId-0025ed1dd6e1` | 33891 | `skin:iconZoneDiningRoom` |
| Lounge | 192.168.1.53 | `memberId-0025ed1dd983` | 52976 | `skin:iconZoneLivingRoom` |
| Ana's bedroom | 192.168.1.54 | `memberId-0025ed1dd723` | 40532 | `skin:iconZoneBedroom` |

- Model `p4300`, firmware `2025.1`, API version 15 (`APIVersion` event var).
- SystemID `nuvo2HvKFUspj2CRGUs8` (shared by all zones; use as the HA config-entry unique_id).
- UDN `uuid:00000000-0000-0000-0000-<mac>`; description at `/<udn>.xml`. The filename pattern from d4yz holds.
- friendlyName is boilerplate (`NuVo Zone 00:25:ed:1d:d6:e1`). The real name is `Zone.Get` → `Title`.
- `serialNumber` in the description is the web UI login secret. It is redacted in `fixtures/` and must never be committed.
- Recon host: ubuntu-dev `enp1s0` 192.168.1.43/24, same L2 segment as the amp. No VLAN crossing involved.

## Discovery

### SSDP
- `SERVER: Linux/2.6.37 UPnP/1.0 GUPnP/0.20.8`: confirms gupnp/libsoup, and the random ephemeral port. Observed ports 33891, 40532, 52976; 33891 and 40532 are below 49152, which rules out libupnp.
- `CACHE-CONTROL: max-age=100`: short. Expect frequent `ssdp:alive`.
- Advertised ST/NT per UDN: `upnp:rootdevice`, `uuid:<udn>`, `urn:schemas-nuvotechnologies-com:device:Zone:1`. Nothing else.
- **Targeted M-SEARCH with the Zone device type works** (multicast and unicast to `<ip>:1900`). mpdrago reported targeted searches as unreliable on P3100; not the case here. Keep `ssdp:all` as the fallback anyway.
- Unicast M-SEARCH to each zone IP answers. That is the cross-VLAN fallback.

### mDNS (second discovery path)
| Service | Host | Port | TXT |
|---|---|---|---|
| `_nuvoplayer._tcp`, instance `00000000-0000-0000-0001-<mac>` | each zone | **4747 (fixed)** | `httpPort=80` |
| `_nuvomaster._tcp`, instance `dev0025ed1dd723` | .54 only | 80 | `sId=<SystemID>`, `el=1`, `cts=9000000000000` |
| `_spotify-connect._tcp` | each zone | random | `CPath=/spotify` |

Ana's bedroom (.54) was the system master on 2026-09-28; on 2026-09-30 `_nuvomaster` was on Dining Room (.52), so the role moves. Port 4747 is not probed yet; it is probably the nSDK channel the app uses.

### DLNA overlap
The device description embeds `urn:schemas-upnp-org:device:MediaRenderer:1` and
`MediaServer:1`, but **SSDP does not advertise them**. HA's DLNA DMR integration
matches on SSDP ST, so it should not auto-discover the zones. Verify in Phase 4.

## Services (identical SCPDs on all three zones)

All control paths are fixed. Only the port changes.

| Service | Control | Event |
|---|---|---|
| `urn:schemas-nuvotechnologies-com:service:Zone:1` | `/ZoneService/control` | `/ZoneService/event` |
| `AVTransport:1` | `/AVTransport/control` | `/AVTransport/event` |
| `RenderingControl:1` | `/RenderingControl/control` | `/RenderingControl/event` |
| `ConnectionManager:1` (MR) | `/ConnectionManager-MR/control` | `/ConnectionManager-MR/event` |
| `ConnectionManager:1` (MS) | `/ConnectionManager-MS/control` | `/ConnectionManager-MS/event` |
| `ContentDirectory:1` | `/ContentDirectory/control` | `/ContentDirectory/event` |

Full action and argument listing: `captures/device-tree.txt`.

### Zone:1
`Get` returns FirmwareVersion, SystemID, MemberID, Title, Icon, Active, Connecting,
MasterGroup, MemberGroup, Model, AudioInput (`player|toslink|rca`), PowerState
(`standby|active|learning`). Also `GetActive`, `GetConnecting`, `GetUpdateState`,
`GetFirmwareVersion`, `GetMemberID`, `GetSystemID`, `GroupCreate(memberIDs JSON) → groupID`,
`GroupDisband(groupID)`, `GroupMemberSetGroup(memberIDs, groupID)`, `UserTap → groupID`,
`UserDoubleTap → groupID`, and the denylisted `SystemCreate`, `SystemJoin`,
`SystemConfigureGWs`, `RestoreFactoryDefaults`.

Evented: all of the above plus `HttpPort` (=80), `APIVersion`, `UpdateURL`
(`http://<ip>:80/firmwareupdate.fcgi`, denylisted), `WirelessSupported`, `Language`,
`CompatibleDevices`.

`MasterGroup`/`MemberGroup` are JSON: `{"id":"gid…","sortKey":"…"}`. Currently each
zone is its own master (MasterGroup.id == MemberGroup.id).

### RenderingControl:1
- **Volume: `ui2`, range 0..100, step 1.** Map HA's 0..1 linearly.
- `GetVolume`/`SetVolume`/`GetMute`/`SetMute`/`GetLoudness`/`SetLoudness`/`GetVolumeDB`/`SetVolumeDB`, `ListPresets`, `SelectPreset` (FactoryDefaults only; approval required).
- **`X_NUVO_AdjustVolume(InstanceID, Channel=Master, VolumeAdjustment: i4)`**: signed delta.
- Channel allowed value: `Master` only.
- Observed: Dining 36 (-86.29 dB), Lounge 44, Ana's 34; all unmuted, loudness off.
- `LastChange` events carry Mute/Volume/VolumeDB/Loudness.

### AVTransport:1
Standard actions plus `X_NUVO_PlayURI`, `X_NUVO_PlayContainerURI(InstanceID, CurrentURI,
CurrentURIMetaData, TrackURI, TrackURIMetaData, StartingIndex, UpdateID)`,
`X_NUVO_TogglePause`, `X_NUVO_PauseOrStop`, `X_NUVO_BeginPrevious`, `X_NUVO_Like`,
`X_NUVO_Dislike`. PlayMode adds `X_NUVO_SHUFFLE_REPEAT_ALL`. `LastChange` carries
`X_NUVO_ExtraInfo`, `AVTransportURI`, `AVTransportURIMetaData`, etc.

On Line In: state `PLAYING`, `GetCurrentTransportActions` = `Play,Stop`, no track duration.

### ContentDirectory:1
- `Browse(ObjectID="0")` returns an empty root.
- **`Browse(ObjectID="lineIn:systemLineIns", BrowseDirectChildren)` lists one line-in item per zone** with the full DIDL metadata the app sends. This is the source list, read from the device rather than hard-coded.
- `X_NUVO_Browse`/`X_NUVO_Browse2` (with `Arguments`, `SubscribeQueueID`) are the app's real browse path for favourites/services. Not explored yet.

### Faults seen (read-only)
`ConnectionManager-MR` `GetProtocolInfo`/`GetCurrentConnectionIDs` and
`ContentDirectory.GetSystemUpdateID` return UPnP 401 Invalid Action despite being in
the SCPD. The same values arrive via GENA, so this doesn't matter.

## Current state (2026-09-28, before any writes)

All three zones: `PowerState=active`, `Active=1`, `AudioInput=player`, `PLAYING`, each
on its **own Line Input** (`nuvo:nuvoremote:memberId-<mac>/lineIn_memberId-<mac>`,
title "Line Input (Local)"), each in its own group:

| Zone | Group id | Volume |
|---|---|---|
| Dining Room | `gidjr234fKh6t0l0CrY` | 36 |
| Lounge | `gidfyRoEGEI3bHn9jKP` | 44 |
| Ana's bedroom | `gidbbVQFyMUWQ2vmLbD` | 34 |

User waived recording favourites and app configuration (2026-09-28). After testing,
Lounge's group is `gidKUs3J2HClqBGwq3R`, still on Line In at 44. The other zones were
not touched.

## Hypotheses

| | Status |
|---|---|
| H1 volume = `SetVolume(0, Master, N)`; step = `X_NUVO_AdjustVolume` | **Confirmed.** SetVolume 44→42→44, AdjustVolume +1/−1, SetMute 1/0 all returned 200, read back correctly, and each produced a RenderingControl `LastChange` event within 1 s. |
| H2 line in = `GroupCreate(["memberId-<mac>"])` then `X_NUVO_PlayContainerURI` | **Payload confirmed** from the device itself: `CurrentURIMetaData` = container `lineIn:systemLineIns`; `TrackURI`/`TrackURIMetaData` = that zone's item from the Browse above (matches mpdrago's template byte-for-byte apart from `dc:description`). **Confirmed by replay**: GroupCreate returned a new groupID, the zone moved into it, and its previous single-member group was deleted automatically (a later `GroupDisband` on it faulted `501 Group not found`). PlayContainerURI → transport `NO_MEDIA_PRESENT` → `TRANSITIONING` → `PLAYING` within ~1 s (brief audio gap). **There is one line-in item per zone. Analog vs optical is auto-detected on the combo jack; there is no selector.** |
| H3 favourites = `SetAVTransportURI`/`X_NUVO_PlayURI` with `nuvo:` URIs | Untested. Needs `X_NUVO_Browse` exploration or an app capture. |
| H4 power off = `GroupDisband`/`Stop`; standby by idle timeout | **Confirmed.** Off = `GroupDisband(current MemberGroup.id)`: `MemberGroup.id` becomes `""`, transport `NO_MEDIA_PRESENT`, `PowerState` stays `active`, and volume is retained. On = `GroupCreate` + `X_NUVO_PlayContainerURI`. `standby` is presumably reached via `settings://mediaPlayer/inactivityTimeout` (60). Not observed. |

The zones also expose other zones' line-ins (`Browse` returns all three), so a zone can
presumably play another zone's line input. That is a possible "source" per zone.

## Exact write sequences (verified)

```
# volume / mute  (RenderingControl, InstanceID=0, Channel=Master)
SetVolume DesiredVolume=0..100
X_NUVO_AdjustVolume VolumeAdjustment=±N
SetMute DesiredMute=0|1

# on / select Line In
Zone.GroupCreate memberIDs=["memberId-<mac>"]            -> groupID
AVTransport.X_NUVO_PlayContainerURI InstanceID=0 CurrentURI="" \
    CurrentURIMetaData=<container lineIn:systemLineIns DIDL> \
    TrackURI=nuvo:nuvoremote:memberId-<mac>/lineIn_memberId-<mac> \
    TrackURIMetaData=<item DIDL from Browse(lineIn:systemLineIns)> \
    StartingIndex=1 UpdateID=-1

# off
Zone.GroupDisband groupID=<Get.MemberGroup.id>
```

`tools/call_action.py` reproduces each one. DIDL payloads come from mpdrago's
`lineIn-body-template.xml` with `__MEMBER_MAC__`/`__ZONE_NAME__` substituted.

## Web UI and nSDK JSON API (port 80)

- Login: `POST /api/authenticate` with body `{"serialNumber": "<base64(serial)>"}` sets
  cookie `Authentication=<base64(serial)>`. Dining Room's serial worked on Lounge, so
  the check is system-wide or loose.
- Pages: `home.fcgi` (IP settings, version), `diagnostics.fcgi` (zone list with the
  master flag, board info, firmware details), `update.fcgi`, `firmware.fcgi`. Only
  home and diagnostics were fetched.
- **StreamUnlimited nSDK API**: `GET /api/getData?path=<p>&roles=value` and
  `GET /api/getRows?path=<p>&roles=title,path,type,value&from=0&to=N` are read-only.
  **`GET /api/setData?path=…&value=…` writes over GET** and is hard-denied in `aionuvo.safety`.
  `getRows path=ui:` hung for >5 s (the zone was unaffected).
- `settings:/` tree (54 entries; dump in `captures/http80/api-settings.txt`). Useful values:
  `mediaPlayer/turnOnVolume`=28, `volumeStep`=[2,2], `inactivityTimeout`=60,
  `bass`/`treble`/`balance`=0, `loudness`, `audioMode` stereo|mono,
  `speakerImpedance` EightOhms, `lineoutMode` variable, and `groupMasterId`/`groupMemberId`.
  No input selector exists: `lineIn/` holds only `title` and `bypassAlertNoAgain`.
- Firmware internal version `2025.1-RC3` (`6903a4ab.7e910000.0ad7b4de.04dbd641`).

## Open questions

1. **`AudioInput`** (`player|toslink|rca`) reads `player` while playing analog Line In.
   It may report what the combo jack detected. Check it while an optical source is
   actually playing. It is read-only either way; there is no setter.
2. Bass/treble/balance are only settable via nSDK `setData` (currently denied). A
   possible later feature; would need explicit user approval.
3. **Port 4747 (`_nuvoplayer`)**: likely the app's nSDK transport. Not probed.
4. ~~Port behaviour across an amp reboot~~: observed, see "Power cycle".
5. Streaming favourites (H3): not needed for the initial integration. Would need
   `X_NUVO_Browse` exploration or an app capture (no tshark on ubuntu-dev; no sudo).

## Implementation notes (Phase 2)

- async-upnp-client delivers AVTransport/RenderingControl `LastChange` as one raw
  XML variable. `aionuvo` expands it with `dlna_handle_notify_last_change`.
- Measured push latency (library write → event → state), 2026-09-28: 0.2–0.5 s on all
  three zones for volume, mute, off and on.
- The GENA subscription TIMEOUT granted is 300 s; `aionuvo` renews at 150 s.
- Library-driven write test on all zones: volume −2/restore, ±1 step, mute on/off,
  Line In re-assert; plus off/on on Lounge. All restored.
- The port-probe fallback from PLAN.md is not implemented: unicast M-SEARCH to the
  zone IP works on this firmware, and mDNS `_nuvoplayer._tcp` is a further fallback if needed.
- Grouping is implemented and verified on hardware (see "Grouping").

## Power cycle (2026-09-28)

| Zone | Port before | Port after |
|---|---|---|
| Dining Room | 33891 | 45783 |
| Lounge | 52976 | 47644 |
| Ana's bedroom | 40532 | 59839 |

- IPs, UDNs, SystemID and **group IDs survive** a power cycle.
- **Zones come back grouped but silent** (`NO_MEDIA_PRESENT`, no URI). So "in a group" is
  not the same as "playing". `aionuvo` reports `playback_state` separately from `is_on`, and
  `turn_on()` re-asserts Line In when the zone is grouped but idle. That path is verified:
  it sends only `X_NUVO_PlayContainerURI`, with no `GroupCreate`.
- Volume comes back at `settings://mediaPlayer/turnOnVolume` (28). Dining Room read 49
  shortly after, presumably set from the app.
- `NuvoSystem` started from a cache holding the old ports, found all three zones at their new
  LOCATIONs via SSDP, and rewrote the cache.

## Grouping (verified 2026-09-28, Lounge + Dining Room)

Model: each zone has a **MasterGroup** (the group it hosts, `""` if none) and a
**MemberGroup** (the group it listens to, `""` when off). A zone plays its own source when
the two are equal. A zone that joined another has `MemberGroup` = that zone's `MasterGroup`.
Its own AVTransport then reads `NO_MEDIA_PRESENT`, so its real playback state is the master's.

| Operation | Call | Observed result |
|---|---|---|
| Join | on master: `GroupMemberSetGroup(memberIDs=["memberId-<joiner>"], groupID=<master MemberGroup>)` | Joiner's MemberGroup becomes the master's group. The joiner's own MasterGroup is **auto-disbanded** (`settings://groupAutoDisbandedMasterGroupId`). Works whether the joiner was on or off. |
| Member leaves | on the member: `GroupMemberSetGroup(memberIDs=[self], groupID="")` | Member goes off (both groups `""`); the master is unaffected. |
| Master releases members | on master: `GroupMemberSetGroup(memberIDs=[members], groupID="")` | Members go off; the master keeps playing. |
| Master off | `GroupDisband(master group)` | **Master and every member go off.** |
| Member selects Line In | leave (above), `GroupCreate`, `X_NUVO_PlayContainerURI` | Member plays its own Line In; the old master is unaffected. |

Zone service events carry every change (MemberGroup/MasterGroup), so group state stays
current by push. `captures/group-test-1.txt` and `group-test-2.txt` have the raw runs.
Whether a joined member was actually audible was not checked by ear.

## HTTP stream playback (2026-09-28, revised 2026-09-30)

**Works via `X_NUVO_PlayURI` with MP3 and FLAC** (2026-09-30). **`SetAVTransportURI` + `Play`
with WAV crashed the UPnP process** (2026-09-28, below) and should not be retried.

### `X_NUVO_PlayURI` (2026-09-30, Dining Room, heard by ear at volume 25)

Dining Room was on Line In in its own group, so no `GroupCreate` was needed. Each call was
`X_NUVO_PlayURI(InstanceID=0, CurrentURI=<url>, CurrentURIMetaData=<DIDL item, res
protocolInfo http-get:*:<mime>:*>)`, served from 192.168.1.43. The UPnP port (57147)
never changed, so no test crashed the UPnP process.

| Test | Result |
|---|---|
| 20 s MP3 file (128 kbit/s, Content-Length) | One GET, PLAYING, `RelTime` reached 0:00:20.035, then `PAUSED_PLAYBACK`. |
| 20 s FLAC file | Same: played to 0:00:20.000, then `PAUSED_PLAYBACK`. |
| Endless real-time MP3 (no Content-Length, `Connection: close`) | PLAYING indefinitely; `RelTime` counts up. |
| `Pause`, then `Play` (Speed=1) on the live stream | PAUSED_PLAYBACK, then resumed from the same position. The HTTP connection stayed open while paused, so after resuming the audio lags the live stream by the pause length. |
| `X_NUVO_PlayURI` with a second live URL while playing | Old connection closed, new GET at once, `RelTime` restarted at 0. |
| `Stop` | STOPPED, `RelTime` 0:00:00, connection closed within 1 s. |

- The zone requests with `User-Agent: Nuvo Player` and `icy-metadata: 1`, like an internet-radio client.
- While a URL plays, `CurrentURI` reads `nuvo:`, not the URL.
- Untested: live FLAC, other sample rates and bit depths, AAC, `SetAVTransportURI` + `Play`
  with MP3, and gapless next tracks (the zone has no `SetNextAVTransportURI`).
- Restored afterwards with `select_source(line_in)` and volume 70.
- The zone **ignores the DIDL metadata sent with the URL**: `CurrentURIMetaData` and
  `TrackMetaData` carry `dc:title` = the URL and an `x:x_nuvo_nsdk` JSON with
  `mediaData.resources[0].uri` = the URL (mimeType `audio/unknown`). `aionuvo` reads the URL
  from there (`ZoneState.stream_url`) and shows the metadata given to `play_url` instead.
- AVTransport `LastChange` events carry that metadata, so stream state updates by push
  (checked live with `aionuvo`: under 1 s, including pause, play and stop).
- `Play` after `Stop` fetches the URL again from the start.
- Transport actions while a URL plays: `Play,Stop,Pause,Seek,X_NUVO_SeekRelTime,X_NUVO_SeekTrackNr,Next,Previous,X_NUVO_Repeat`.
- Music Assistant's Home Assistant MediaPlayers provider (checked in its source, 2026-09-30) always uses
  flow mode (one continuous stream per session), defaults to MP3, sends `extra.metadata`
  (`title`, `artist`, `album`, `imageUrl`), calls `media_stop` before `play_media` when the entity
  is playing, and recognises its own playback by `media_content_id` echoing its stream URL.

### `SetAVTransportURI` + `Play` with WAV: crashed (2026-09-28)

`GroupCreate` then `SetAVTransportURI(http://<host>/tone.wav, DIDL with http-get:*:audio/wav:*)`
then `Play` on Dining Room (which was off, at volume 20): the zone **fetched the file**
(`GET /tone.wav` from 192.168.1.52), then **its UPnP HTTP server went away** (connection
refused on the old port). It was back about 15 s later on a new port (57147). The zone
stayed pingable and its web UI kept running, and `versionLastBooted` did not change. So
the UPnP process restarted; the whole zone did not reboot. After the restart it read
`PAUSED_PLAYBACK` with URI `nuvo:`.

It is unknown whether the WAV format or the `SetAVTransportURI` path caused the crash. Do not
retry either without explicit approval.

## Music Assistant / Sendspin landscape (user's network, 2026-09-28)

- Music Assistant on Tower (192.168.1.6), advertising `_sendspin-server._tcp` (port 8927, `/sendspin`).
- Three Chromecast Audios (.220, .223, .224) plus two Chromecast Ultras. The zones are
  on optical Line In; **presumably each CCA feeds one zone (unconfirmed)**.
- The zones advertise no AirPlay, Cast or Sendspin service. Spotify Connect is enabled
  on each zone. The `airplay2` settings exist but are empty and not advertised.

## Get lags behind events after group changes (2026-09-28)

After a `GroupMemberSetGroup`/`GroupDisband`, `Zone.Get` can keep returning the
**previous** MemberGroup for roughly a second or two, while the GENA event with the new
value has already arrived (`captures/unjoin-timeline.txt`). Found through HA: an unjoin
issued ~0.5 s after a join re-read the zone, saw "not in a group", and did nothing.

`aionuvo` therefore:
- does not re-read a zone's group for `GROUP_READ_LAG` (4 s) after writing it, and
  ignores MemberGroup/MasterGroup in polls during that window;
- updates group state optimistically after a write, lets events confirm it, and
  reconciles with a poll once the window has passed.

Raw join/leave with gaps of 0.5, 2 and 5 s never reverted on the amp
(`captures/join-leave-timing.txt`), so the amp applies changes promptly; only the read lags.
The live HA test (`tests_ha/test_live.py`, `NUVO_LIVE=1`) passed 3/3 after the fix,
with the unjoin reflected in HA within about 0.3 s.

## Tone controls via the nSDK API (2026-09-28)

- Login: the zone's `serialNumber` from its UPnP description, so no user input is needed.
  `POST /api/authenticate {"serialNumber": "<base64(serial)>"}`, then cookie `Authentication=<base64(serial)>`.
- Read: `GET /api/getData?path=settings://mediaPlayer/bass&roles=value` returns `[{"double_":0,"type":"double_"}]`.
- Write: `GET /api/setData?path=settings://mediaPlayer/bass&roles=value&value={"type":"double_","double_":3}`
  (the parameter is `roles`; `role` gives "wrong parameters: missing path or roles!").
- Ranges (the device clamps and rounds to whole steps): **bass −6..6, treble −6..6, balance −18..18**.
  Measured on Dining Room while it was off, then restored to 0.
- Loudness uses UPnP `SetLoudness`/`GetLoudness` instead; it is evented via RenderingControl LastChange.

**Correction (2026-09-28, real HA install):** none of this changes the sound. Balance −18 via
`setData` was stored and read back but was inaudible and did not show in the Nuvo app; UPnP
`SetLoudness` likewise. App-side changes do land in `settings://mediaPlayer/*`, so the app applies
tone some other way. The entities were removed; see PLAN.md, "Tone and loudness".

## Favourites (2026-09-28, read-only exploration)

ContentDirectory `Browse`/`X_NUVO_Browse` on Lounge:

| ObjectID | Result |
|---|---|
| `tunein:` | Works: My Favorites (**empty** for this account), Local Radio, Music, Talk, Sports, By Location, By Language, Podcasts |
| `spotify:` | Informational only (Spotify Connect) |
| `sirius:` | Not configured |
| `pandora:` | "Not available in this country" |
| `favorites:`, `presets:`, `playHistory:`, `ui:`, `nuvo:` | Time out (>6 s) |
| `/stable/lineIn/` | The three zones' line inputs |

The Nuvo app's own favourites service does not answer, likely a retired cloud service.

### TuneIn playback: works (2026-09-28, user-approved, Dining Room at volume 50)

Same pattern as Line In: `GroupCreate`, then `X_NUVO_PlayContainerURI` with
- `CurrentURI` = `""`;
- `CurrentURIMetaData` = DIDL wrapping the **parent container element** exactly as returned
  by browsing its parent (e.g. Local Radio from `Browse("tunein:")`);
- `TrackURI` = the item's `<res>` (`nuvo:tunein:browse?nsdkGuideId=s7160`);
- `TrackURIMetaData` = DIDL wrapping the **item element** from `Browse(container)`;
- `StartingIndex` = the item's 1-based position in that listing (2 for 95bFM; the listing
  starts with an empty-id item); `UpdateID` = -1.

Result: `PLAYING` within ~2 s, and `media_title` "95bFM | (College Radio)". The zone stayed
up (same UPnP port) and was still playing minutes later. The zone resolves the stream
itself through `opml.radiotime.com/Tune.ashx`, which is why this path is safe while
pushing an HTTP URL (`SetAVTransportURI`) crashed the UPnP process. Items carry
`upnp:icon` (station logo) and `dc:description` (the current show).
`BrowseMetadata` on a TuneIn container fails (501 "does not exist"), so the container
DIDL must come from the parent's `BrowseDirectChildren` listing. Implemented as `NuvoZone.browse`/`play_item`.

`BrowseMetadata` on a station's own id (`tunein:browse?nsdkGuideId=s7160`) answers with a generic
"TuneIn" container (`parentID="0"`), not the station; `BrowseDirectChildren` on it fails (501). The
station's `tunein:player_context?…` and `tunein:browser_context_ex?…` children are app menus
(Play Now, Add to Favorites, Choose stream, Sleep Timer). Whether a station plays from its guide ID
alone, without the listing it came from, is untested.

### TuneIn and Line In feed through HA (2026-09-28, user-approved, Dining Room)

`tests_ha/test_live_tunein.py`: browsing Local Radio from the media browser took ~1 s (48 stations);
`play_media` of 95bFM reached `playing`/source TuneIn in ~2.7 s, with title "95bFM | (College Radio)"
and the station logo as the entity picture. A stand-in feed entity going `idle` → `playing` switched
the zone to Line In in ~1.1 s (briefly `TRANSITIONING`, shown as buffering).

Caution: Dining Room was not off but playing TuneIn (Radio Hauraki Auckland 99.0, volume 44), so the
run interrupted it for ~15 s; it was put back via `play_item`. The live test now skips a zone that is on.

### Other services (read-only browse, 2026-09-28)

| Service | State |
|---|---|
| Spotify Connect | Enabled on every zone (mDNS `_spotify-connect._tcp`). Controlled from Spotify clients, not browsable here. |
| Deezer, iHeart, Napster | "Not configured / Fix it!": needs an account linked in the Nuvo app. Napster (ex-Rhapsody) and iHeart are likely dead or not offered in NZ. |
| Amazon Music, SiriusXM (`siriusxm2:`), `top10:`, `alexa:` | Empty. |
| `iheart:`, `rhapsody:`, `sxZone:`, `testStreams:`, `airplay2:` | Time out. |
| Pandora | Not available in NZ. |
| AirPlay 2 | Settings exist but nothing is advertised. |

## Idle drop observed

Ana's bedroom came back from the power cycle grouped but silent, and about an hour later
was found with no group (off) without anything having touched it. This matches
`settings://mediaPlayer/inactivityTimeout` = 60 and the idle drop mpdrago's project works
around. HA shows the zone as off when this happens; `turn_on` restores Line In.
