"""HTTP streams through play_media, as Music Assistant sends them (docs/protocol.md)."""

from unittest.mock import patch

import pytest
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_ARTIST,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_EXTRA,
    ATTR_MEDIA_TITLE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_STOP,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .test_integration import DINING, LOUNGE, call, settle

URL = "http://192.168.1.6:8097/flow/session/queue/Lounge.mp3"
# What Music Assistant's hass_players provider sends.
MA_EXTRA = {
    "metadata": {
        "title": "Song", "artist": "Band", "metadataType": 3, "album": "Album", "albumName": "Album",
        "images": [{"url": "http://h/art.jpg"}], "imageUrl": "http://h/art.jpg", "duration": None,
    }
}
PAUSE = MediaPlayerEntityFeature.PAUSE


def features(hass, entity_id=LOUNGE):
    return MediaPlayerEntityFeature(hass.states.get(entity_id).attributes[ATTR_SUPPORTED_FEATURES])


async def play(hass, url=URL, entity_id=LOUNGE, **data):
    await call(hass, MP_DOMAIN, SERVICE_PLAY_MEDIA, entity_id,
               **{ATTR_MEDIA_CONTENT_ID: url, ATTR_MEDIA_CONTENT_TYPE: "music", **data})


async def test_music_assistant_play(hass, setup, amp):
    lounge, _dining, _ = amp
    assert PAUSE not in features(hass)
    lounge.calls.clear()
    await play(hass, **{ATTR_MEDIA_ENQUEUE: "replace", ATTR_MEDIA_EXTRA: MA_EXTRA})
    name, args = lounge.calls[-1]
    assert name == "X_NUVO_PlayURI" and args["CurrentURI"] == URL
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in args["CurrentURIMetaData"]

    assert await settle(hass, lambda: hass.states.get(LOUNGE).attributes.get(ATTR_INPUT_SOURCE) == "Stream")
    attrs = hass.states.get(LOUNGE).attributes
    assert attrs[ATTR_MEDIA_TITLE] == "Song" and attrs[ATTR_MEDIA_ARTIST] == "Band"
    assert attrs[ATTR_MEDIA_CONTENT_ID] == URL
    assert PAUSE in features(hass) and MediaPlayerEntityFeature.PLAY in features(hass)


async def test_transport_controls(hass, setup, amp):
    lounge, _dining, _ = amp
    await play(hass)
    assert await settle(hass, lambda: PAUSE in features(hass))
    await call(hass, MP_DOMAIN, SERVICE_MEDIA_PAUSE, LOUNGE)
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_PAUSED)
    await call(hass, MP_DOMAIN, SERVICE_MEDIA_PLAY, LOUNGE)
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_PLAYING)
    await call(hass, MP_DOMAIN, SERVICE_MEDIA_STOP, LOUNGE)
    assert await settle(hass, lambda: hass.states.get(LOUNGE).state == STATE_IDLE)
    assert [n for n, _ in lounge.calls if n in ("Pause", "Play", "Stop")] == ["Pause", "Play", "Stop"]


async def test_stop_elsewhere_is_noop(hass, setup, amp):
    """Music Assistant stops a playing player before playing to it; Line In must survive that."""
    lounge, _dining, _ = amp
    lounge.calls.clear()
    await call(hass, MP_DOMAIN, SERVICE_MEDIA_STOP, LOUNGE)
    assert not [n for n, _ in lounge.calls if not n.startswith("Get")]
    assert hass.states.get(LOUNGE).state == STATE_PLAYING


async def test_joined_member_controls_master(hass, setup, amp):
    lounge, dining, _ = amp
    await play(hass)
    await call(hass, MP_DOMAIN, "join", LOUNGE, group_members=[DINING])
    assert await settle(hass, lambda: PAUSE in features(hass, DINING))
    assert hass.states.get(DINING).attributes[ATTR_MEDIA_CONTENT_ID] == URL
    await call(hass, MP_DOMAIN, SERVICE_MEDIA_PAUSE, DINING)
    assert lounge.calls[-1][0] == "Pause" and not any(n == "Pause" for n, _ in dining.calls)


async def test_media_source(hass, setup, amp):
    lounge, _dining, _ = amp
    await hass.config.async_update(internal_url="http://ha.local:8123")
    resolved = media_source.PlayMedia("/api/tts_proxy/abc.flac", "audio/flac")
    with patch("homeassistant.components.media_source.async_resolve_media", return_value=resolved):
        await play(hass, "media-source://tts/x")
    name, args = lounge.calls[-1]
    assert name == "X_NUVO_PlayURI" and args["CurrentURI"].startswith("http://ha.local:8123/api/tts_proxy/abc.flac")
    assert 'protocolInfo="http-get:*:audio/flac:*"' in args["CurrentURIMetaData"]


@pytest.mark.parametrize(
    ("media_id", "error"),
    [("http://h/tone.wav", HomeAssistantError), ("spotify:track:x", ServiceValidationError)],
)
async def test_play_refuses(hass, setup, amp, media_id, error):
    lounge, _dining, _ = amp
    lounge.calls.clear()
    with pytest.raises(error):
        await play(hass, media_id)
    assert not [n for n, _ in lounge.calls if not n.startswith("Get")]


async def test_pause_needs_stream(hass, setup, amp):
    with pytest.raises(ServiceValidationError):
        await call(hass, MP_DOMAIN, SERVICE_MEDIA_PAUSE, LOUNGE)
