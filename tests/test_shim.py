import asyncio
import json

import httpx
import pytest

from shim import main
from shim.main import app

from .conftest import make_system

ZID = "memberId-0025ed1dd983"


@pytest.fixture
async def client(ssdp, fake, monkeypatch):
    monkeypatch.delenv("NUVO_API_TOKEN", raising=False)
    monkeypatch.delenv("NUVO_DEBUG", raising=False)
    app.state.system_factory = lambda: make_system(ssdp)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://shim") as c:
            yield c


async def test_health_and_zones(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["ok"]
    zones = (await client.get("/zones")).json()
    assert [z["id"] for z in zones] == [ZID]
    z = zones[0]
    assert z["slug"] == "lounge" and z["power"] and z["source"] == "line_in" and z["volume_raw"] == 44


@pytest.mark.parametrize("zid", [ZID, "lounge", "0025ed1dd983"])
async def test_zone_lookup(client, zid):
    assert (await client.get(f"/zones/{zid}")).json()["id"] == ZID


async def test_unknown_zone(client):
    assert (await client.get("/zones/kitchen")).status_code == 404


async def test_volume_mute_step(client, fake):
    r = await client.put("/zones/lounge/volume", json={"level": 0.3})
    assert r.status_code == 200 and fake.volume == 30
    assert (await client.put("/zones/lounge/volume", json={"level": 2})).status_code == 422
    await client.post("/zones/lounge/volume/step", json={"delta": -2})
    assert fake.volume == 28
    await client.put("/zones/lounge/mute", json={"muted": True})
    assert fake.muted


async def test_source_power_transport(client, fake):
    assert (await client.get("/zones/lounge/sources")).json() == [{"key": "line_in", "label": "Line In"}]
    r = await client.put("/zones/lounge/power", json={"on": False})
    assert r.status_code == 200 and fake.member_group == ""
    await client.put("/zones/lounge/source", json={"source": "line_in"})
    assert fake.transport == "PLAYING" and fake.member_group
    assert (await client.put("/zones/lounge/source", json={"source": "tidal"})).status_code == 409
    await client.post("/zones/lounge/transport/stop")
    assert fake.transport == "STOPPED"
    assert (await client.post("/zones/lounge/transport/eject")).status_code == 404
    np = (await client.get("/zones/lounge/now_playing")).json()
    assert set(np) == {"title", "artist", "album", "image_url", "uri"}


async def test_device_errors_map_to_http(client, fake):
    fake.faults["SetMute"] = (501, "Action Failed")
    assert (await client.put("/zones/lounge/mute", json={"muted": True})).status_code == 502


async def test_token(client, monkeypatch):
    monkeypatch.setenv("NUVO_API_TOKEN", "s3cret")
    assert (await client.get("/zones")).status_code == 401
    ok = await client.get("/zones", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert (await client.get("/health")).status_code == 200  # health stays open for Docker


async def test_debug_call(client, fake, monkeypatch):
    body = {"service": "RenderingControl", "action": "GetVolume", "args": {"InstanceID": 0, "Channel": "Master"}}
    assert (await client.post("/debug/call/lounge", json=body)).status_code == 404
    monkeypatch.setenv("NUVO_DEBUG", "1")
    assert (await client.post("/debug/call/lounge", json=body)).json() == {"CurrentVolume": 44}
    write = {"service": "RenderingControl", "action": "SetVolume",
             "args": {"InstanceID": 0, "Channel": "Master", "DesiredVolume": 9}}
    assert (await client.post("/debug/call/lounge", json=write)).status_code == 200
    assert fake.volume == 9
    denied = {"service": "Zone", "action": "RestoreFactoryDefaults", "args": {"dummy": "x"}}
    assert (await client.post("/debug/call/lounge", json=denied)).status_code == 403
    assert "RestoreFactoryDefaults" not in [c[0] for c in fake.calls]
    bad = {"service": "Nope", "action": "Get"}
    assert (await client.post("/debug/call/lounge", json=bad)).status_code == 400


async def test_groups(client, fake):
    assert (await client.delete("/groups/gidNope")).status_code == 404
    r = await client.delete(f"/groups/{fake.member_group}")  # single-zone group: nothing to release
    assert r.status_code == 200 and fake.member_group == "gidInitial"
    assert (await client.post("/groups", json={"members": [ZID]})).status_code == 422
    z = (await client.delete("/zones/lounge/group")).json()
    assert z["group_master"] == ZID and z["group_members"] == [ZID] and z["playback_state"] == "PLAYING"


async def test_groups_two_zones(amp):
    system, f_lounge, f_dining = amp
    main.hub.system = system
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://shim") as c:
            r = await c.post("/groups", json={"members": ["lounge", "dining_room"]})
            assert r.status_code == 200
            dining = (await c.get("/zones/dining_room")).json()
            assert dining["group_master"] == ZID and dining["playback_state"] == "PLAYING"
            assert (await c.delete("/groups/gidLounge")).status_code == 200
            assert f_dining.member_group == "" and f_lounge.transport == "PLAYING"
    finally:
        main.hub.system = None


async def test_refresh(client, fake):
    fake.volume = 11
    zones = (await client.post("/discovery/refresh")).json()
    assert zones[0]["volume_raw"] == 11


async def test_sse_stream(ssdp, fake):
    """Uses a real uvicorn server: httpx's ASGI transport cannot stream."""
    import socket

    import uvicorn

    app.state.system_factory = lambda: make_system(ssdp)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    events = []

    async def read():
        async with httpx.AsyncClient() as c, c.stream("GET", f"http://127.0.0.1:{port}/events") as r:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                    if any(e["volume_raw"] == 21 for e in events):
                        return

    try:
        task = asyncio.create_task(read())
        await asyncio.sleep(0.3)
        await fake.app_set_volume(21)
        await asyncio.wait_for(task, 5)
    finally:
        server.should_exit = True
        await serve
    assert events[0]["id"] == ZID
    assert events[-1]["volume_raw"] == 21


def test_slugify():
    assert main.slugify("Ana's bedroom") == "ana_s_bedroom"
