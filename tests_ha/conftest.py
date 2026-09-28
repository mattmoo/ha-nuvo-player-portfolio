"""Home Assistant tests: the real integration against fake zones on 127.0.0.1.

Run with the HA venv:  .venv-ha/bin/pytest tests_ha
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuvo_player.aionuvo import NuvoSystem
from custom_components.nuvo_player.const import CONF_HOSTS, CONF_SYSTEM_ID, DOMAIN
from tests.fake_zone import FakeSsdp, FakeZone


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_real_ssdp(hass):
    """Pretend HA's ssdp/zeroconf are loaded; capture the SSDP callback."""
    hass.config.components.update({"ssdp", "zeroconf"})
    captured = {}

    async def _register(hass, cb, match):
        captured["callback"] = cb
        captured["match"] = match
        def _unsub():
            captured.pop("callback", None)

        return _unsub

    with (
        patch("custom_components.nuvo_player.ssdp.async_get_discovery_info_by_st", AsyncMock(return_value=[])),
        patch("custom_components.nuvo_player.ssdp.async_register_callback", _register),
    ):
        yield captured


@pytest.fixture
async def amp(socket_enabled):
    """Lounge + Dining Room on one fake amp, each playing its own Line In."""
    shared: list[FakeZone] = []
    lounge = FakeZone(amp=shared, member_group="gidLounge")
    dining = FakeZone(amp=shared, mac="0025ed1dd6e1", title="Dining Room", volume=36, member_group="gidDining")
    for z in shared:
        await z.start()
    responder = FakeSsdp(shared)
    await responder.start()
    yield lounge, dining, responder
    responder.stop()
    for z in shared:
        await z.stop()


@pytest.fixture
def entry(hass):
    e = MockConfigEntry(
        domain=DOMAIN,
        unique_id="nuvoTEST",
        title="Nuvo P4300",
        data={CONF_SYSTEM_ID: "nuvoTEST", CONF_HOSTS: ["127.0.0.1"]},
    )
    e.add_to_hass(hass)
    return e


@pytest.fixture
async def setup(hass, entry, amp):
    """Set up the entry with a NuvoSystem pointed at the fakes."""
    lounge, dining, responder = amp

    def _create(hass_, entry_):
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return NuvoSystem(
            session=async_get_clientsession(hass_),
            hosts=["127.0.0.1"],
            multicast=False,
            ssdp_port=responder.port,
            callback_host="127.0.0.1",
            search_timeout=1,
            web_port=lounge.port,
            system_id="nuvoTEST",
        )

    with patch("custom_components.nuvo_player.create_system", _create):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
