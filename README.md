# nuvo-ha

Local control of a Legrand Nuvo Player Portfolio amp (built and tested on a
3-zone **P4300**, firmware 2025.1) over its UPnP/SOAP interface. It covers
volume, mute, Line In, on/off, grouping and push updates, and it survives the amp
changing its HTTP port on every boot. With Music Assistant, use the zones as the
power and volume control of the players feeding their Line In (see PLAN.md).

| Part | What it is |
|---|---|
| `aionuvo/` | Async Python library. The only code that talks to the amp. |
| `shim/` | FastAPI REST + Server-Sent Events wrapper (Docker/Unraid). |
| `custom_components/nuvo_player/` | Home Assistant integration (in progress). |
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
.venv/bin/pytest --cov
```

The tests run against a fake zone that serves the recorded `fixtures/`. The
`tools/` scripts talk to real hardware; write actions need `--allow-write`.

## Credits

- [mpdrago/nuvo-zone-keepalive](https://github.com/mpdrago/nuvo-zone-keepalive) (MIT):
  the GroupCreate + X_NUVO_PlayContainerURI Line In sequence and payload.
- [d4yz/nuvo_p_series_home_assistant](https://github.com/d4yz/nuvo_p_series_home_assistant):
  early UPnP notes.
