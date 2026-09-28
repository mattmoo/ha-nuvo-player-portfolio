# Nuvo Player Portfolio for Home Assistant

> **Scope:** the IP-based Legrand Nuvo **Player Portfolio** (P100/P200/P3100/P3500/P4300/…),
> controlled over the network. This is **not** for the older Nuvo Grand Concerto /
> Essentia (RS-232) systems; see `nuvo_serial` for those.

Local control of a Legrand Nuvo Player Portfolio amp (built and tested on a
3-zone **P4300**, firmware 2025.1) over its UPnP/SOAP interface. It covers
volume, mute, Line In, on/off, grouping and push updates, and it survives the amp
changing its HTTP port on every boot. With Music Assistant, use the zones as the
power and volume control of the players feeding their Line In (see PLAN.md).

This project is largely "vibe-engineered" (I do have a Software Engineering degree). 
Use at your own risk, I'm putting it here in case it comes in handy for someone.

| Part | What it is |
|---|---|
| `custom_components/nuvo_player/aionuvo/` | Async Python library, the only code that talks to the amp. It is bundled in the integration; `pip install -e .` exposes it as `aionuvo`. |
| `shim/` | FastAPI REST + Server-Sent Events wrapper (Docker/Unraid). |
| `custom_components/nuvo_player/` | Home Assistant integration (HACS). |
| `docs/protocol.md` | Protocol notes from recon on real hardware. |
| `docs/safety.md` | Actions the code refuses to send. |

This is unofficial and reverse-engineered. Nuvo is discontinued and offers no
support, so use it at your own risk.

## Library

```python
from aionuvo import NuvoSystem

system = await NuvoSystem.discover(hosts=["192.168.1.52"])  # hosts: optional unicast fallback
zone = system.zones["memberId-0025ed1dd983"]
await zone.set_volume(0.35)          # 0..1, mapped to the device's 0..100
await zone.volume_step(+2)
await zone.set_mute(True)
await zone.select_source("line_in")  # GroupCreate (if off) + X_NUVO_PlayContainerURI
await zone.turn_off()                # GroupDisband (a group master takes its members with it)
await system.group(zone, [system.zones["memberId-0025ed1dd6e1"]])  # Dining Room hears Lounge
await system.ungroup(zone)           # members leave and go off; the master keeps playing
unsub = zone.subscribe(lambda z: print(z.name, z.state.volume_raw))
await system.async_stop()
```

Discovery uses SSDP (`ST: urn:schemas-nuvotechnologies-com:device:Zone:1`),
multicast plus unicast to any configured hosts. When a zone stops answering it
is rediscovered by UDN, re-subscribed, and the command retried.

## Home Assistant

Install with HACS (custom repository, category Integration) or copy
`custom_components/nuvo_player/` into your HA `config/custom_components/`.
Restart HA; the amp is discovered automatically (SSDP/zeroconf), or add
**Nuvo Player Portfolio** manually. The integration's logo (Nuvo's, in
`brand/`) shows on HA 2026.3 or later. Nothing else to install: no add-on or
container, and the library ships inside the integration.

Per zone you get a device (assign each to an area yourself) with:

- `media_player.<zone>`: power, volume, mute, source (Line In) and grouping
  (`media_player.join` / `unjoin`). Turning off a group master also turns off
  the zones listening to it; an amp behaviour.
- `number.<zone>_bass`, `_treble` (−6…6), `_balance` (−18…18), polled every 5 min.
- `switch.<zone>_loudness`.

**TuneIn:** open a zone's media browser to browse the amp's built-in TuneIn
(Local Radio, Music, Talk, Sports, By Location, By Language, My Favorites) and
play a station. The zone streams it itself. Station IDs from the browser work
in `media_player.play_media` automations, but they include the station's
category path, so a station that drops out of that category stops resolving.
Other media (URLs, media sources, Music Assistant streams) are refused, because
pushing HTTP media crashes the zone's UPnP server.

Options (**Configure** on the integration):

- **Line In feeds:** per zone, the player wired to its Line In (for example a
  Chromecast Audio). When that player starts playing, the zone switches to Line
  In, turning on if needed, even from TuneIn. A zone listening to another
  zone's group is left alone, and so is a player that reappears already
  playing (HA restart, network blip).
- **Network:** extra zone IPs for unicast discovery (other VLANs), and a fixed
  event port if a firewall sits between the amp and HA (0 = automatic).

### Music Assistant

The zones cannot be Music Assistant players themselves (see PLAN.md). Treat each
zone as the amplifier of whatever feeds its Line In (e.g. a Chromecast Audio or
a Sendspin receiver): in Music Assistant, open that player's settings and set
**Power control** and **Volume control** to the zone's `media_player` entity.
Playing to the player then turns the zone on to Line In and routes volume to the
Nuvo. If you move a feeder to another zone, change it there. The Line In feeds
option above does the switching without Music Assistant, and the two can be
used together.

## Shim

```bash
docker compose -f shim/docker-compose.yml up -d --build
curl localhost:8095/zones
curl -X PUT localhost:8095/zones/lounge/volume -H 'content-type: application/json' -d '{"level":0.3}'
curl -N localhost:8095/events
```

OpenAPI docs are at `/docs`. The container needs host networking, and the amp
must be able to reach the callback port (8096) for push updates.

## Development

```bash
uv venv -p 3.13 .venv && uv pip install -p .venv -e ".[shim,test]" uvicorn
.venv/bin/pytest --cov                                   # library + shim

uv venv -p 3.13 .venv-ha && uv pip install -p .venv-ha -r requirements_ha_test.txt
.venv-ha/bin/pytest tests_ha -o pythonpath=.             # HA integration
NUVO_LIVE=1 .venv-ha/bin/pytest -s tests_ha/test_live.py -o pythonpath=.   # against the real amp (writes!)
```

The tests run against a fake zone that serves the recorded `fixtures/`. The
`tools/` scripts talk to real hardware; write actions need `--allow-write`.

## Credits

- [mpdrago/nuvo-zone-keepalive](https://github.com/mpdrago/nuvo-zone-keepalive) (MIT):
  the GroupCreate + X_NUVO_PlayContainerURI Line In sequence and payload.
- [d4yz/nuvo_p_series_home_assistant](https://github.com/d4yz/nuvo_p_series_home_assistant):
  early UPnP notes.
