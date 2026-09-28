"""TuneIn and the Line In feed through HA against the real amp.

Skipped unless NUVO_LIVE=1, and skipped if Dining Room is on (someone may be
listening). Writes, on Dining Room only (volume 50; turned off and its volume
restored afterwards): play 95bFM from the media browser, then a
stand-in "Chromecast" starts playing and the zone must switch to Line In.
"""

import os
import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuvo_player.const import CONF_HOSTS, CONF_LINE_IN_FEEDS, CONF_SYSTEM_ID, DOMAIN

from .test_live import DINING, ZONES, settle, svc

pytestmark = pytest.mark.skipif(os.environ.get("NUVO_LIVE") != "1", reason="needs the real amp (NUVO_LIVE=1)")

DINING_ID = "memberId-0025ed1dd6e1"
FEED = "media_player.standin_cca_dining"


def entity(hass, entity_id):
    return hass.data["media_player"].get_entity(entity_id)


async def test_live_tunein(hass, socket_enabled):
    import pytest_socket

    pytest_socket.socket_allow_hosts(["127.0.0.1", *ZONES])
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="nuvo2HvKFUspj2CRGUs8",
        title="Nuvo P4300",
        data={CONF_SYSTEM_ID: "nuvo2HvKFUspj2CRGUs8", CONF_HOSTS: ZONES},
        options={CONF_LINE_IN_FEEDS: {DINING_ID: FEED}},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(FEED, "idle")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    dining = entry.runtime_data.zones[DINING_ID]
    await dining.async_update()
    before_volume, before_playback, before_source = dining.state.volume_raw, dining.playback_state, dining.source
    print(f"BEFORE dining: playback={before_playback} source={before_source} volume={before_volume} "
          f"uri={dining.state.current_uri!r} joined={dining.is_joined}")
    if before_playback != "off":
        await hass.config_entries.async_unload(entry.entry_id)
        pytest.skip(f"Dining Room is on ({before_source}); not interrupting it")
    try:
        await dining.set_volume_raw(50)

        # 1. Browse to Local Radio and play 95bFM, as the HA frontend would.
        t0 = time.monotonic()
        root = await entity(hass, DINING).async_browse_media(None, None)
        local = next(c for c in root.children if c.title == "Local Radio")
        listing = await entity(hass, DINING).async_browse_media(None, local.media_content_id)
        station = next(c for c in listing.children if c.title.startswith("95bFM"))
        print(f"{time.monotonic()-t0:5.2f} browsed; {len(listing.children)} stations; id={station.media_content_id[:60]}...")
        await svc(hass, "media_player", "play_media", entity_id=DINING,
                  media_content_id=station.media_content_id, media_content_type="channel")
        ok = await settle(hass, lambda: hass.states.get(DINING).state == "playing"
                          and hass.states.get(DINING).attributes.get("source") == "TuneIn", timeout=10)
        s = hass.states.get(DINING)
        print(f"{time.monotonic()-t0:5.2f} TuneIn: ok={ok} state={s.state} source={s.attributes.get('source')} "
              f"title={s.attributes.get('media_title')!r} artist={s.attributes.get('media_artist')!r} "
              f"image={s.attributes.get('entity_picture')!r} uri={dining.state.current_uri!r}")
        assert ok

        # 2. The stand-in feed starts playing: the zone must move to Line In.
        t0 = time.monotonic()
        hass.states.async_set(FEED, "playing")
        ok = await settle(hass, lambda: hass.states.get(DINING).attributes.get("source") == "Line In", timeout=10)
        s = hass.states.get(DINING)
        print(f"{time.monotonic()-t0:5.2f} feed started: ok={ok} state={s.state} source={s.attributes.get('source')} "
              f"uri={dining.state.current_uri!r}")
        assert ok
    finally:
        await svc(hass, "media_player", "turn_off", entity_id=DINING)
        await dining.set_volume_raw(before_volume)
        await dining.async_update()
        print(f"AFTER dining: playback={dining.playback_state} source={dining.source} volume={dining.state.volume_raw}")
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
