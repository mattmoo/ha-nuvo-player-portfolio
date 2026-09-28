import pytest

import aionuvo.system
import aionuvo.zone
from aionuvo import NuvoSystem

from .fake_zone import FakeSsdp, FakeZone


@pytest.fixture(autouse=True)
def fast_maintenance(monkeypatch):
    monkeypatch.setattr(aionuvo.system, "TICK", 0.05)
    monkeypatch.setattr(aionuvo.system, "REDISCOVERY_BACKOFF", (0.05, 0.1))
    monkeypatch.setattr(aionuvo.zone, "COMMAND_RETRY_DELAYS", (0.3,))


@pytest.fixture
async def fake():
    zone = FakeZone()
    await zone.start()
    yield zone
    await zone.stop()


@pytest.fixture
async def ssdp(fake):
    responder = FakeSsdp([fake])
    await responder.start()
    yield responder
    responder.stop()


def make_system(ssdp, **kwargs) -> NuvoSystem:
    return NuvoSystem(
        hosts=["127.0.0.1"],
        multicast=False,
        ssdp_port=ssdp.port,
        callback_host="127.0.0.1",
        search_timeout=1,
        **kwargs,
    )


@pytest.fixture
async def system(ssdp):
    s = make_system(ssdp)
    await s.async_start()
    yield s
    await s.async_stop()


@pytest.fixture
async def amp():
    """Two zones on one amp: Lounge and Dining Room, each playing its own Line In."""
    shared: list[FakeZone] = []
    lounge = FakeZone(amp=shared, member_group="gidLounge")
    dining = FakeZone(amp=shared, mac="0025ed1dd6e1", title="Dining Room", volume=36, member_group="gidDining")
    for z in shared:
        await z.start()
    responder = FakeSsdp(shared)
    await responder.start()
    system = make_system(responder)
    await system.async_start()
    yield system, lounge, dining
    await system.async_stop()
    responder.stop()
    for z in shared:
        await z.stop()
