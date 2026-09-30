"""HTTP stream playback with X_NUVO_PlayURI (docs/protocol.md, "HTTP stream playback")."""

import pytest

from aionuvo import SOURCE_LINE_IN, SOURCE_STREAM, NuvoError
from aionuvo.didl import parse_metadata, stream_metadata

from .fake_zone import stream_didl
from .test_zone import eventually, zone_of

URL = "http://192.168.1.6:8097/flow/abc/Dining.flac"


def test_parse_metadata_finds_stream_url():
    meta = parse_metadata(stream_didl(URL))
    assert meta["stream_url"] == URL and meta["title"] == URL
    assert parse_metadata(stream_didl("nuvo:tunein:x"))["stream_url"] is None
    assert parse_metadata("")["stream_url"] is None


def test_stream_metadata_escapes():
    didl = stream_metadata("http://h/a.mp3?x=1&y=2", "audio/mpeg", "Tom & Jerry")
    assert "x=1&amp;y=2" in didl and "Tom &amp; Jerry" in didl
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in didl
    assert 'protocolInfo="http-get:*:*:*"' in stream_metadata("http://h/live", None, None)


async def test_play_url(system, fake):
    zone = zone_of(system)
    fake.calls.clear()
    await zone.play_url(URL, title="Song", artist="Band", album="Album", image_url="http://h/art.jpg")
    assert [c[0] for c in fake.calls if not c[0].startswith("Get")] == ["X_NUVO_PlayURI"]
    play = fake.calls[-1][1]
    assert play["CurrentURI"] == URL and 'protocolInfo="http-get:*:audio/flac:*"' in play["CurrentURIMetaData"]
    assert await eventually(lambda: zone.source == SOURCE_STREAM)
    s = zone.state
    assert (s.stream_url, s.media_title, s.media_artist, s.media_album, s.media_image_url) == (
        URL, "Song", "Band", "Album", "http://h/art.jpg")
    assert zone.playback_state == "PLAYING"

    await zone.select_source(SOURCE_LINE_IN)
    assert await eventually(lambda: zone.source == SOURCE_LINE_IN)
    assert zone.state.stream_url is None


async def test_play_url_when_off_creates_group(system, fake):
    zone = zone_of(system)
    await zone.turn_off()
    fake.calls.clear()
    await zone.play_url(URL)
    assert [c[0] for c in fake.calls if not c[0].startswith("Get")] == ["GroupCreate", "X_NUVO_PlayURI"]
    # Without metadata of our own, the zone's (the URL) is shown.
    assert await eventually(lambda: zone.state.media_title == URL)


async def test_unknown_stream_keeps_zone_metadata(system, fake):
    """A URL someone else started (or ours from before a restart) shows the zone's title."""
    zone = zone_of(system)
    await zone.play_url(URL, title="Song")
    fake.metadata = stream_didl("http://elsewhere/x.mp3")
    await zone.async_update()
    assert zone.source == SOURCE_STREAM and zone.state.media_title == "http://elsewhere/x.mp3"


@pytest.mark.parametrize(
    ("url", "mime"),
    [("http://h/tone.wav", None), ("http://h/x", "audio/x-wav"), ("http://h/x.flac", "audio/L16"),
     ("ftp://h/a.mp3", None), ("media-source://x", None), ("/local/a.mp3", None)],
)
async def test_play_url_refuses(system, fake, url, mime):
    zone = zone_of(system)
    fake.calls.clear()
    with pytest.raises(NuvoError):
        await zone.play_url(url, mime)
    assert not [c for c in fake.calls if not c[0].startswith("Get")]


async def test_play_url_on_member_leaves_group_first(amp):
    system, _lounge, dining_fake = amp
    lounge, dining = system.zones["memberId-0025ed1dd983"], system.zones["memberId-0025ed1dd6e1"]
    await system.group(lounge, [dining])
    assert await eventually(lambda: dining.is_joined)
    dining_fake.calls.clear()
    await dining.play_url(URL)
    writes = [c[0] for c in dining_fake.calls if not c[0].startswith("Get")]
    assert writes == ["GroupMemberSetGroup", "GroupCreate", "X_NUVO_PlayURI"]
    assert await eventually(lambda: dining.source == SOURCE_STREAM and not dining.is_joined)


async def test_turn_on_switches_stream_to_line_in(system, fake):
    zone = zone_of(system)
    await zone.play_url(URL)
    assert await eventually(lambda: zone.source == SOURCE_STREAM)
    await zone.turn_on()
    assert fake.uri == fake.line_in_uri
