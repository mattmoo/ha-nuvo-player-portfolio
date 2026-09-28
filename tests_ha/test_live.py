"""The integration inside HA's test harness against the real amp.

Skipped unless NUVO_LIVE=1. Writes: Lounge volume -2 and back; Dining Room
joined to Lounge and released (at volume 30, restored afterwards).
"""

import asyncio
import os

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuvo_player.const import CONF_HOSTS, CONF_SYSTEM_ID, DOMAIN

ZONES = ["192.168.1.52", "192.168.1.53", "192.168.1.54"]
pytestmark = pytest.mark.skipif(os.environ.get("NUVO_LIVE") != "1", reason="needs the real amp (NUVO_LIVE=1)")

LOUNGE, DINING = "media_player.lounge", "media_player.dining_room"


async def settle(hass, pred, timeout=5.0):
    for _ in range(int(timeout / 0.1)):
        await hass.async_block_till_done()
        if pred():
            return True
        await asyncio.sleep(0.1)
    return pred()


async def svc(hass, domain, service, **data):
    await hass.services.async_call(domain, service, data, blocking=True)


async def test_live(hass, socket_enabled):
    import pytest_socket

    pytest_socket.socket_allow_hosts(["127.0.0.1", *ZONES])
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="nuvo2HvKFUspj2CRGUs8",
        title="Nuvo P4300",
        data={CONF_SYSTEM_ID: "nuvo2HvKFUspj2CRGUs8", CONF_HOSTS: ZONES},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        for eid in (LOUNGE, DINING, "media_player.ana_s_bedroom"):
            s = hass.states.get(eid)
            print(eid, s.state, s.attributes.get("volume_level"), s.attributes.get("source"), s.attributes.get("group_members"))

        # Volume through HA; the new value must come back by push.
        before = hass.states.get(LOUNGE).attributes["volume_level"]
        system = entry.runtime_data
        await svc(hass, "media_player", "volume_set", entity_id=LOUNGE, volume_level=round(before - 0.02, 2))
        system.zones["memberId-0025ed1dd983"].state.volume_raw = None  # prove the event updates it
        assert await settle(hass, lambda: hass.states.get(LOUNGE).attributes.get("volume_level") == round(before - 0.02, 2))
        await svc(hass, "media_player", "volume_set", entity_id=LOUNGE, volume_level=before)
        print("volume via push: ok")

        # Grouping through HA.
        dining = system.zones["memberId-0025ed1dd6e1"]
        dining_before, dining_state = dining.state.volume_raw, dining.playback_state
        print("dining before test:", dining_state, dining_before, "HA:", hass.states.get(DINING).state)
        await svc(hass, "media_player", "volume_set", entity_id=DINING, volume_level=0.30)
        await svc(hass, "media_player", "join", entity_id=LOUNGE, group_members=[DINING])
        assert await settle(hass, lambda: hass.states.get(DINING).attributes.get("group_members") == [LOUNGE, DINING])
        print("joined:", hass.states.get(DINING).state, hass.states.get(DINING).attributes.get("media_title"))
        assert hass.states.get(DINING).state == "playing"
        import time
        t0 = time.monotonic()
        unsub = dining.subscribe(lambda z: print(f"{time.monotonic()-t0:5.2f} dining member={z.state.member_group or '-'} playback={z.playback_state} HA={hass.states.get(DINING).state}"))
        try:
            await svc(hass, "media_player", "unjoin", entity_id=DINING)
            print(f"{time.monotonic()-t0:5.2f} unjoin returned; HA={hass.states.get(DINING).state}")
            ok = await settle(hass, lambda: hass.states.get(DINING).state == "off", timeout=8)
            print(f"{time.monotonic()-t0:5.2f} settled={ok} HA={hass.states.get(DINING).state} zone={dining.playback_state}")
        finally:
            unsub()
            await dining.set_volume_raw(dining_before if dining_before is not None else 49)
        assert ok
        assert hass.states.get(LOUNGE).state == "playing"
        await dining.set_volume_raw(dining_before)
        assert dining_state == "off", f"Dining Room was {dining_state} before the test; it is now off"
        print("grouping via HA: ok")
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
