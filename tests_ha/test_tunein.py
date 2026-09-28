"""TuneIn in the media browser, and switching to Line In when its feed starts."""

import pytest
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    BrowseError,
)
from homeassistant.const import STATE_IDLE, STATE_PLAYING, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_component import EntityComponent

from custom_components.nuvo_player.const import CONF_LINE_IN_FEEDS

from .test_integration import DINING, LOUNGE, call, settle

S7160 = "tunein:browse?nsdkGuideId=s7160"
CCA = "media_player.cca_lounge"


def entity(hass, entity_id):
    component: EntityComponent = hass.data[MP_DOMAIN]
    return component.get_entity(entity_id)


async def browse(hass, media_id=None):
    return await entity(hass, LOUNGE).async_browse_media(None, media_id)


async def local_radio_station(hass):
    root = await browse(hass)
    local = next(c for c in root.children if c.title == "Local Radio")
    listing = await browse(hass, local.media_content_id)
    return next(c for c in listing.children if c.title.startswith("95bFM"))


async def test_browse_tree(hass, setup, amp):
    root = await browse(hass)
    assert root.title == "TuneIn" and root.can_expand and not root.can_play
    titles = [c.title for c in root.children]
    assert titles[:3] == ["My Favorites", "Local Radio", "Music"] and "Podcasts" not in titles
    assert all(c.can_expand and not c.can_play for c in root.children)
    assert root.children_media_class == "directory"

    station = await local_radio_station(hass)
    assert station.can_play and not station.can_expand
    local = await browse(hass, next(c for c in root.children if c.title == "Local Radio").media_content_id)
    assert local.children_media_class == "channel"
    assert station.thumbnail.startswith("http://cdn-profiles.tunein.com/s7160/")
    assert station.media_content_id.startswith("tunein|tunein:|") and station.media_content_id.endswith("|" + S7160)

    music = await browse(hass, next(c for c in root.children if c.title == "Music").media_content_id)
    assert len(music.children) == 25 and all(c.can_expand for c in music.children)

    favourites = await browse(hass, root.children[0].media_content_id)
    assert favourites.children == []  # only a "No Favorites available" placeholder


async def test_browse_bad_ids(hass, setup, amp):
    with pytest.raises(BrowseError):
        await browse(hass, "media-source://radio_browser")
    with pytest.raises(BrowseError):
        await browse(hass, "tunein|tunein:|tunein:nope")


async def test_play_station(hass, setup, amp):
    lounge, _dining, _ = amp
    station = await local_radio_station(hass)
    await call(hass, MP_DOMAIN, SERVICE_PLAY_MEDIA, LOUNGE,
               **{ATTR_MEDIA_CONTENT_ID: station.media_content_id, ATTR_MEDIA_CONTENT_TYPE: "channel"})
    play = next(a for name, a in reversed(lounge.calls) if name == "X_NUVO_PlayContainerURI")
    assert play["TrackURI"] == "nuvo:tunein:browse?nsdkGuideId=s7160" and play["StartingIndex"] == "2"
    assert "nsdkDisplayName=Local%20Radio" in play["CurrentURIMetaData"]
    assert await settle(hass, lambda: hass.states.get(LOUNGE).attributes.get(ATTR_INPUT_SOURCE) == "TuneIn")


async def test_play_station_cold(hass, setup, amp):
    """An ID saved in an automation plays without browsing first."""
    lounge, _dining, _ = amp
    station = await local_radio_station(hass)
    entity(hass, LOUNGE)._browser._cache.clear()
    await call(hass, MP_DOMAIN, SERVICE_PLAY_MEDIA, LOUNGE,
               **{ATTR_MEDIA_CONTENT_ID: station.media_content_id, ATTR_MEDIA_CONTENT_TYPE: "channel"})
    assert lounge.uri == "nuvo:tunein:browse?nsdkGuideId=s7160"


async def test_play_refuses_urls(hass, setup, amp):
    lounge, _dining, _ = amp
    lounge.calls.clear()
    with pytest.raises(ServiceValidationError):
        await call(hass, MP_DOMAIN, SERVICE_PLAY_MEDIA, LOUNGE,
                   **{ATTR_MEDIA_CONTENT_ID: "http://example.com/stream.mp3", ATTR_MEDIA_CONTENT_TYPE: "music"})
    assert not any(name == "X_NUVO_PlayContainerURI" for name, _ in lounge.calls)


async def set_feeds(hass, entry, feeds):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "line_in_feeds"})
    assert result["type"] is FlowResultType.FORM
    assert set(result["data_schema"].schema) == {"Lounge", "Dining Room"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], feeds)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # the entry reloads


async def test_line_in_feed_options(hass, setup, amp):
    await set_feeds(hass, setup, {"Lounge": CCA})
    assert setup.options[CONF_LINE_IN_FEEDS] == {"memberId-0025ed1dd983": CCA}
    await set_feeds(hass, setup, {})
    assert setup.options[CONF_LINE_IN_FEEDS] == {}


async def test_feed_start_switches_tunein_to_line_in(hass, setup, amp):
    lounge, _dining, _ = amp
    hass.states.async_set(CCA, STATE_IDLE)
    await set_feeds(hass, setup, {"Lounge": CCA})
    station = await local_radio_station(hass)
    await call(hass, MP_DOMAIN, SERVICE_PLAY_MEDIA, LOUNGE,
               **{ATTR_MEDIA_CONTENT_ID: station.media_content_id, ATTR_MEDIA_CONTENT_TYPE: "channel"})
    assert lounge.uri.startswith("nuvo:tunein:")

    hass.states.async_set(CCA, STATE_PLAYING)
    assert await settle(hass, lambda: lounge.uri == lounge.line_in_uri)
    assert await settle(hass, lambda: hass.states.get(LOUNGE).attributes.get(ATTR_INPUT_SOURCE) == "Line In")


async def test_feed_start_turns_zone_on(hass, setup, amp):
    lounge, _dining, _ = amp
    hass.states.async_set(CCA, STATE_IDLE)
    await set_feeds(hass, setup, {"Lounge": CCA})
    await call(hass, MP_DOMAIN, "turn_off", LOUNGE)
    assert lounge.member_group == ""
    hass.states.async_set(CCA, STATE_PLAYING)
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_PLAYING)
    assert lounge.uri == lounge.line_in_uri


async def test_feed_ignores_reconnects_and_joined_zones(hass, setup, amp):
    lounge, dining, _ = amp
    hass.states.async_set(CCA, STATE_UNAVAILABLE)
    await set_feeds(hass, setup, {"Lounge": CCA, "Dining Room": "media_player.cca_dining"})
    station = await local_radio_station(hass)
    await call(hass, MP_DOMAIN, SERVICE_PLAY_MEDIA, LOUNGE,
               **{ATTR_MEDIA_CONTENT_ID: station.media_content_id, ATTR_MEDIA_CONTENT_TYPE: "channel"})

    # Chromecast coming back already playing (HA restart, network blip): leave TuneIn.
    hass.states.async_set(CCA, STATE_PLAYING)
    await hass.async_block_till_done()
    assert lounge.uri.startswith("nuvo:tunein:")

    # Dining Room was joined to the Lounge on purpose: its feed starting does not pull it out.
    await call(hass, MP_DOMAIN, "join", LOUNGE, group_members=[DINING])
    assert await settle(hass, lambda: dining.member_group == lounge.master_group)
    hass.states.async_set("media_player.cca_dining", STATE_IDLE)
    hass.states.async_set("media_player.cca_dining", STATE_PLAYING)
    await hass.async_block_till_done()
    assert dining.member_group == lounge.master_group and lounge.uri.startswith("nuvo:tunein:")
