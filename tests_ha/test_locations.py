"""Zone LOCATIONs saved between runs, so zones that miss the startup search still load."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import STATE_PLAYING
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nuvo_player.aionuvo import NuvoSystem

from .test_integration import DINING, LOUNGE, settle


def key(entry):
    return f"nuvo_player.{entry.entry_id}.locations"


def create(responder):
    def _create(hass, _entry):
        return NuvoSystem(
            session=async_get_clientsession(hass), hosts=["127.0.0.1"], multicast=False,
            ssdp_port=responder.port, callback_host="127.0.0.1", search_timeout=1, system_id="nuvoTEST",
        )

    return _create


async def test_locations_saved_and_removed(hass, entry, amp, hass_storage):
    lounge, dining, responder = amp
    with patch("custom_components.nuvo_player.create_system", create(responder)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert hass_storage[key(entry)]["data"] == {lounge.udn: lounge.location, dining.udn: dining.location}
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key(entry) not in hass_storage


async def test_saved_location_loads_zone_ssdp_misses(hass, entry, amp, hass_storage):
    """Dining Room does not answer the startup search but its saved LOCATION works (2026-10-01)."""
    lounge, dining, responder = amp
    responder.zones = [lounge]
    hass_storage[key(entry)] = {"version": 1, "minor_version": 1, "key": key(entry),
                                "data": {dining.udn: dining.location}}
    with patch("custom_components.nuvo_player.create_system", create(responder)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await settle(hass, lambda: hass.states.get(DINING).state == STATE_PLAYING)
        assert hass.states.get(LOUNGE).state == STATE_PLAYING
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
