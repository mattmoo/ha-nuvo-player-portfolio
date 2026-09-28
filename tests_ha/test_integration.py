import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.media_player import (
    ATTR_GROUP_MEMBERS,
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MP_DOMAIN,
    SERVICE_JOIN,
    SERVICE_SELECT_SOURCE,
    SERVICE_UNJOIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_DOWN,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    SERVICE_VOLUME_UP,
    STATE_IDLE,
    STATE_OFF,
    STATE_PLAYING,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nuvo_player.const import DOMAIN
from custom_components.nuvo_player.diagnostics import async_get_config_entry_diagnostics

LOUNGE, DINING = "media_player.lounge", "media_player.dining_room"


async def settle(hass, pred, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        await hass.async_block_till_done()
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


async def call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)
    await hass.async_block_till_done()


async def test_entities_and_devices(hass, setup):
    state = hass.states.get(LOUNGE)
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == pytest.approx(0.44)
    assert state.attributes[ATTR_INPUT_SOURCE] == "Line In"
    assert state.attributes["source_list"] == ["Line In"]
    assert hass.states.get(DINING).state == STATE_PLAYING

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup.entry_id)
    assert sorted(d.name for d in devices) == ["Dining Room", "Lounge"]
    assert all(d.suggested_area is None and d.area_id is None for d in devices)
    assert {d.model for d in devices} == {"P4300"}

    ents = er.async_entries_for_config_entry(er.async_get(hass), setup.entry_id)
    assert sorted(e.entity_id for e in ents) == sorted(
        [
            LOUNGE, DINING,
            "number.lounge_bass", "number.lounge_treble", "number.lounge_balance",
            "number.dining_room_bass", "number.dining_room_treble", "number.dining_room_balance",
            "switch.lounge_loudness", "switch.dining_room_loudness",
        ]
    )


async def test_volume_mute(hass, setup, amp):
    lounge, _, _ = amp
    await call(hass, MP_DOMAIN, SERVICE_VOLUME_SET, LOUNGE, **{ATTR_MEDIA_VOLUME_LEVEL: 0.3})
    assert lounge.volume == 30
    await call(hass, MP_DOMAIN, SERVICE_VOLUME_UP, LOUNGE)
    assert lounge.volume == 32
    await call(hass, MP_DOMAIN, SERVICE_VOLUME_DOWN, LOUNGE)
    assert lounge.volume == 30
    await call(hass, MP_DOMAIN, SERVICE_VOLUME_MUTE, LOUNGE, **{ATTR_MEDIA_VOLUME_MUTED: True})
    assert lounge.muted
    assert await settle(hass, lambda: hass.states.get(LOUNGE).attributes[ATTR_MEDIA_VOLUME_MUTED] is True)


async def test_push_update_from_app(hass, setup, amp):
    lounge, _, _ = amp
    await lounge.app_set_volume(12)
    assert await settle(hass, lambda: hass.states.get(LOUNGE).attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.12)


async def test_power_and_source(hass, setup, amp):
    lounge, _, _ = amp
    await call(hass, MP_DOMAIN, SERVICE_TURN_OFF, LOUNGE)
    assert lounge.member_group == ""
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_OFF)
    await call(hass, MP_DOMAIN, SERVICE_TURN_ON, LOUNGE)
    assert lounge.transport == "PLAYING"
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_PLAYING)
    await call(hass, MP_DOMAIN, SERVICE_SELECT_SOURCE, LOUNGE, **{ATTR_INPUT_SOURCE: "Line In"})
    with pytest.raises(ServiceValidationError):
        await call(hass, MP_DOMAIN, SERVICE_SELECT_SOURCE, LOUNGE, **{ATTR_INPUT_SOURCE: "Tidal"})


async def test_idle_after_power_cycle(hass, setup, amp):
    lounge, _, _ = amp
    lounge.transport, lounge.uri = "NO_MEDIA_PRESENT", ""
    await lounge.notify("AVTransport")
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_IDLE)
    assert hass.states.get(LOUNGE).attributes.get(ATTR_INPUT_SOURCE) is None


async def test_grouping(hass, setup, amp):
    lounge, dining, _ = amp
    await call(hass, MP_DOMAIN, SERVICE_JOIN, LOUNGE, **{ATTR_GROUP_MEMBERS: [DINING]})
    assert dining.member_group == "gidLounge"
    assert await settle(hass, lambda: hass.states.get(DINING).attributes.get(ATTR_GROUP_MEMBERS) == [LOUNGE, DINING])
    assert hass.states.get(LOUNGE).attributes[ATTR_GROUP_MEMBERS] == [LOUNGE, DINING]
    assert hass.states.get(DINING).state == STATE_PLAYING  # via the master's transport
    assert hass.states.get(DINING).attributes.get(ATTR_INPUT_SOURCE) is None

    await call(hass, MP_DOMAIN, SERVICE_UNJOIN, DINING)
    assert dining.member_group == ""
    assert await settle(hass, lambda: hass.states.get(DINING).state == STATE_OFF)
    assert hass.states.get(LOUNGE).state == STATE_PLAYING

    with pytest.raises(ServiceValidationError):
        await call(hass, MP_DOMAIN, SERVICE_JOIN, LOUNGE, **{ATTR_GROUP_MEMBERS: ["media_player.not_nuvo"]})


async def test_tone_numbers(hass, setup, amp):
    lounge, _, _ = amp
    assert hass.states.get("number.lounge_bass").state == "0.0"
    attrs = hass.states.get("number.lounge_balance").attributes
    assert (attrs["min"], attrs["max"], attrs["step"]) == (-18, 18, 1)
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: "number.lounge_bass", "value": 3}, blocking=True
    )
    assert lounge.tone["bass"] == 3.0
    assert float(hass.states.get("number.lounge_bass").state) == 3


async def test_loudness_switch(hass, setup, amp):
    lounge, _, _ = amp
    assert hass.states.get("switch.lounge_loudness").state == "off"
    await call(hass, "switch", SERVICE_TURN_ON, "switch.lounge_loudness")
    assert lounge.loudness is True
    assert hass.states.get("switch.lounge_loudness").state == "on"


async def test_zone_unavailable_and_back(hass, setup, amp):
    lounge, _, _ = amp
    await lounge.stop()
    with pytest.raises(Exception):
        await call(hass, MP_DOMAIN, SERVICE_VOLUME_SET, LOUNGE, **{ATTR_MEDIA_VOLUME_LEVEL: 0.2})
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == "unavailable")
    await lounge.start()  # new port, like a UPnP restart
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_PLAYING, timeout=15)


async def test_ssdp_callback_relocates(hass, setup, amp, no_real_ssdp):
    from homeassistant.components.ssdp import SsdpChange
    from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

    lounge, _, _ = amp
    system = setup.runtime_data
    await lounge.restart_on_new_port()
    cb = no_real_ssdp["callback"]
    assert no_real_ssdp["match"] == {"deviceType": "urn:schemas-nuvotechnologies-com:device:Zone:1"}
    cb(
        SsdpServiceInfo(ssdp_usn="x", ssdp_st="x", upnp={}, ssdp_udn=lounge.udn, ssdp_location=lounge.location),
        SsdpChange.ALIVE,
    )
    assert await settle(hass, lambda: system.zones["memberId-0025ed1dd983"].location == lounge.location)


async def test_diagnostics_redacts(hass, setup):
    diag = await async_get_config_entry_diagnostics(hass, setup)
    text = str(diag)
    assert "0025ed1dd983" not in text and "nuvoTEST" not in text and "127.0.0.1" not in text
    assert {z["name"] for z in diag["zones"]} == {"Lounge", "Dining Room"}


async def test_unload(hass, setup):
    assert await hass.config_entries.async_unload(setup.entry_id)
    await hass.async_block_till_done()
    assert setup.state is ConfigEntryState.NOT_LOADED


async def test_not_ready_without_zones(hass, entry):
    from custom_components.nuvo_player.aionuvo import NuvoConnectionError

    system = AsyncMock()
    system.async_start.side_effect = NuvoConnectionError("none")
    with patch("custom_components.nuvo_player.create_system", return_value=system):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    system.async_stop.assert_awaited()


async def test_entity_icons(hass, setup):
    from homeassistant.helpers.icon import async_get_icons

    icons = (await async_get_icons(hass, "entity", integrations=[DOMAIN]))[DOMAIN]
    assert icons["number"]["bass"]["default"] == "mdi:music-clef-bass"
    assert icons["switch"]["loudness"]["state"]["off"] == "mdi:volume-medium"
