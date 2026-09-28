"""REST shim over aionuvo: a debugging console and an API for non-HA clients.

Environment:
  NUVO_HOSTS          comma-separated zone IPs for unicast discovery (optional)
  NUVO_CALLBACK_PORT  fixed GENA callback port (default 8096; open it amp VLAN -> shim)
  NUVO_CALLBACK_HOST  IP the zones should call back on (default: auto)
  NUVO_API_TOKEN      if set, every request needs "Authorization: Bearer <token>"
  NUVO_CACHE          path of the LOCATION cache file (optional)
  NUVO_DEBUG=1        enables POST /debug/call (still denylist-enforced)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from aionuvo import DeniedActionError, NuvoActionError, NuvoConnectionError, NuvoError, NuvoSystem, NuvoZone
from aionuvo.const import AVTRANSPORT_SERVICE, CONTENT_DIRECTORY_SERVICE, RENDERING_SERVICE, ZONE_SERVICE
from aionuvo.safety import is_read_only


def _env_list(name: str) -> list[str]:
    return [h.strip() for h in os.environ.get(name, "").split(",") if h.strip()]


class Hub:
    """Holds the NuvoSystem and fans state changes out to SSE clients."""

    def __init__(self) -> None:
        self.system: NuvoSystem | None = None
        self.queues: set[asyncio.Queue] = set()
        self.last_seen: dict[str, float] = {}

    def on_change(self, zone: NuvoZone) -> None:
        if zone.available:
            self.last_seen[zone.member_id] = time.time()
        event = json.dumps(zone_out(zone, self).model_dump())
        for q in list(self.queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


hub = Hub()


def create_system() -> NuvoSystem:
    return NuvoSystem(
        hosts=_env_list("NUVO_HOSTS"),
        callback_host=os.environ.get("NUVO_CALLBACK_HOST") or None,
        callback_port=int(os.environ.get("NUVO_CALLBACK_PORT", "8096")),
        cache_path=os.environ.get("NUVO_CACHE") or None,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    system = app.state.system_factory()
    await system.async_start()
    hub.system = system
    for zone in system.zones.values():
        hub.last_seen[zone.member_id] = time.time()
        zone.subscribe(hub.on_change)
    yield
    hub.system = None
    await system.async_stop()


app = FastAPI(title="Nuvo shim", version="0.1.0", lifespan=lifespan)
app.state.system_factory = create_system

_bearer = HTTPBearer(auto_error=False)


def auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    token = os.environ.get("NUVO_API_TOKEN")
    if token and (creds is None or creds.credentials != token):
        raise HTTPException(401, "Bad or missing bearer token")


# --- models ----------------------------------------------------------------


class ZoneOut(BaseModel):
    id: str
    slug: str
    name: str
    ip: str
    location: str
    available: bool
    last_seen: float | None
    power: bool
    source: str | None
    volume: float | None
    volume_raw: int | None
    muted: bool | None
    group: str
    group_master: str | None
    group_members: list[str]
    playback_state: str
    transport_state: str | None
    transport_actions: list[str]


class NowPlaying(BaseModel):
    title: str | None
    artist: str | None
    album: str | None
    image_url: str | None
    uri: str | None


class VolumeIn(BaseModel):
    level: float = Field(ge=0.0, le=1.0)


class StepIn(BaseModel):
    delta: int = Field(ge=-20, le=20)


class MuteIn(BaseModel):
    muted: bool


class SourceIn(BaseModel):
    source: str


class PowerIn(BaseModel):
    on: bool


class GroupIn(BaseModel):
    members: list[str] = Field(min_length=2, description="Zone ids; the first is the master")


class DebugCallIn(BaseModel):
    service: str = Field(description="Zone, AVTransport, RenderingControl or ContentDirectory")
    action: str
    args: dict[str, Any] = {}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def zone_out(zone: NuvoZone, h: Hub = hub) -> ZoneOut:
    s = zone.state
    return ZoneOut(
        id=zone.member_id,
        slug=slugify(zone.name),
        name=zone.name,
        ip=zone.host,
        location=zone.location,
        available=zone.available,
        last_seen=h.last_seen.get(zone.member_id),
        power=s.is_on,
        source=zone.source,
        volume=zone.volume_level,
        volume_raw=s.volume_raw,
        muted=s.muted,
        group=s.member_group,
        group_master=zone.master.member_id if zone.master else None,
        group_members=[z.member_id for z in zone.group_members],
        playback_state=zone.playback_state,
        transport_state=s.transport_state,
        transport_actions=sorted(s.transport_actions),
    )


def get_system() -> NuvoSystem:
    if hub.system is None:
        raise HTTPException(503, "Not started")
    return hub.system


def get_zone(zone_id: str, system: NuvoSystem = Depends(get_system)) -> NuvoZone:
    if zone_id in system.zones:
        return system.zones[zone_id]
    for zone in system.zones.values():
        if slugify(zone.name) == zone_id or zone.mac == zone_id:
            return zone
    raise HTTPException(404, f"No zone {zone_id!r}")


async def run(coro) -> None:
    try:
        await coro
    except DeniedActionError as err:
        raise HTTPException(403, str(err)) from err
    except NuvoConnectionError as err:
        raise HTTPException(504, str(err)) from err
    except NuvoActionError as err:
        raise HTTPException(502, str(err)) from err
    except NuvoError as err:
        raise HTTPException(409, str(err)) from err


# --- routes ----------------------------------------------------------------

api = [Depends(auth)]


@app.get("/health")
async def health() -> dict:
    system = hub.system
    zones = list(system.zones.values()) if system else []
    ok = any(z.available for z in zones)
    body = {
        "ok": ok,
        "zones": {
            z.member_id: {"available": z.available, "last_seen": hub.last_seen.get(z.member_id), "location": z.location}
            for z in zones
        },
    }
    if not ok:
        raise HTTPException(503, body)
    return body


@app.get("/zones", dependencies=api)
async def list_zones(system: NuvoSystem = Depends(get_system)) -> list[ZoneOut]:
    return [zone_out(z) for z in system.zones.values()]


@app.get("/zones/{zone_id}", dependencies=api)
async def read_zone(zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    return zone_out(zone)


@app.put("/zones/{zone_id}/volume", dependencies=api)
async def set_volume(body: VolumeIn, zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    await run(zone.set_volume(body.level))
    return zone_out(zone)


@app.post("/zones/{zone_id}/volume/step", dependencies=api)
async def step_volume(body: StepIn, zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    await run(zone.volume_step(body.delta))
    return zone_out(zone)


@app.put("/zones/{zone_id}/mute", dependencies=api)
async def set_mute(body: MuteIn, zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    await run(zone.set_mute(body.muted))
    return zone_out(zone)


@app.get("/zones/{zone_id}/sources", dependencies=api)
async def sources(zone: NuvoZone = Depends(get_zone)) -> list[dict]:
    return [{"key": s.key, "label": s.label} for s in zone.sources]


@app.put("/zones/{zone_id}/source", dependencies=api)
async def set_source(body: SourceIn, zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    await run(zone.select_source(body.source))
    return zone_out(zone)


@app.put("/zones/{zone_id}/power", dependencies=api)
async def set_power(body: PowerIn, zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    await run(zone.turn_on() if body.on else zone.turn_off())
    return zone_out(zone)


@app.post("/zones/{zone_id}/transport/{command}", dependencies=api)
async def transport(command: str, zone: NuvoZone = Depends(get_zone)) -> ZoneOut:
    if command not in ("play", "pause", "stop", "next", "previous"):
        raise HTTPException(404, f"Unknown transport command {command!r}")
    await run(getattr(zone, command)())
    return zone_out(zone)


@app.get("/zones/{zone_id}/now_playing", dependencies=api)
async def now_playing(zone: NuvoZone = Depends(get_zone)) -> NowPlaying:
    s = zone.state
    return NowPlaying(
        title=s.media_title, artist=s.media_artist, album=s.media_album, image_url=s.media_image_url, uri=s.current_uri
    )


@app.post("/groups", dependencies=api)
async def create_group(body: GroupIn, system: NuvoSystem = Depends(get_system)) -> list[ZoneOut]:
    """Join zones to the first one's group (turning it on to Line In if it is off)."""
    zones = [get_zone(zid, system) for zid in body.members]
    await run(system.group(zones[0], zones[1:]))
    return [zone_out(z) for z in zones]


@app.delete("/groups/{group_id}", dependencies=api)
async def delete_group(group_id: str, system: NuvoSystem = Depends(get_system)) -> list[ZoneOut]:
    """Release every member of the group; they go off and the master keeps playing."""
    master = next((z for z in system.zones.values() if z.state.master_group == group_id), None)
    if master is None:
        raise HTTPException(404, f"No zone masters group {group_id!r}")
    members = master.group_members
    await run(system.ungroup(master))
    return [zone_out(z) for z in members]


@app.delete("/zones/{zone_id}/group", dependencies=api)
async def leave_group(zone: NuvoZone = Depends(get_zone), system: NuvoSystem = Depends(get_system)) -> ZoneOut:
    """Take one zone out of its group (a master releases its members instead)."""
    await run(system.ungroup(zone))
    return zone_out(zone)


@app.post("/discovery/refresh", dependencies=api)
async def refresh(system: NuvoSystem = Depends(get_system)) -> list[ZoneOut]:
    for zone in system.zones.values():
        try:
            await zone.async_update()
        except NuvoError:
            await zone.async_recover()
    return [zone_out(z) for z in system.zones.values()]


@app.get("/events", dependencies=api)
async def events(request: Request) -> StreamingResponse:
    """Server-Sent Events: one `zone` event per state change."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    hub.queues.add(queue)

    async def stream():
        try:
            if hub.system:
                for zone in hub.system.zones.values():
                    yield f"event: zone\ndata: {json.dumps(zone_out(zone).model_dump())}\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: zone\ndata: {data}\n\n"
        finally:
            hub.queues.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


_SERVICES = {
    "Zone": ZONE_SERVICE,
    "AVTransport": AVTRANSPORT_SERVICE,
    "RenderingControl": RENDERING_SERVICE,
    "ContentDirectory": CONTENT_DIRECTORY_SERVICE,
}


@app.post("/debug/call/{zone_id}", dependencies=api)
async def debug_call(body: DebugCallIn, zone: NuvoZone = Depends(get_zone)) -> dict:
    """Raw action call for protocol exploration. Needs NUVO_DEBUG=1; denylist still applies."""
    if os.environ.get("NUVO_DEBUG") != "1":
        raise HTTPException(404, "Not found")
    if body.service not in _SERVICES:
        raise HTTPException(400, f"service must be one of {list(_SERVICES)}")
    result: dict = {}

    async def call():
        nonlocal result
        if is_read_only(body.action):
            result = await zone._call(_SERVICES[body.service], body.action, **body.args)
        else:
            result = await zone._write(_SERVICES[body.service], body.action, **body.args)

    await run(call())
    return {k: v if isinstance(v, (str, int, float, bool, type(None))) else str(v) for k, v in result.items()}
