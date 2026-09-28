"""TuneIn browsing and playback through the zone's ContentDirectory (docs/protocol.md)."""

import pytest

from aionuvo import TUNEIN_ROOT, NuvoActionError, NuvoError
from aionuvo.didl import parse_listing

from .fake_zone import LISTINGS, TUNEIN_FIXTURES
from .test_zone import eventually, zone_of

S7160 = "tunein:browse?nsdkGuideId=s7160"


def test_parse_listing_keeps_positions_and_raw_xml():
    local = parse_listing((TUNEIN_FIXTURES / "local.didl.xml").read_text())
    header, bfm = local[0], local[1]
    assert (header.kind, header.id, header.playable) == ("item", "", False)
    assert bfm.id == S7160 and bfm.index == 2 and bfm.playable
    assert bfm.title == "95bFM | (College Radio)"
    assert bfm.res == "nuvo:tunein:" + S7160.removeprefix("tunein:")
    assert bfm.icon.startswith("http://cdn-profiles.tunein.com/s7160/")
    assert bfm.description == "The One to Four with Pennie Black"
    assert bfm.xml.startswith(f'<item id="{S7160}"') and bfm.xml.endswith("</item>")
    assert bfm.didl.startswith("<DIDL-Lite ") and bfm.didl.endswith("</item></DIDL-Lite>")


def test_parse_listing_offset_and_garbage():
    root = parse_listing((TUNEIN_FIXTURES / "root.didl.xml").read_text(), start=10)
    assert [e.index for e in root][:2] == [11, 12] and all(e.kind == "container" for e in root)
    assert parse_listing(None) == [] and parse_listing("not xml") == []


async def test_browse_root(system, fake):
    zone = zone_of(system)
    entries, total = await zone.browse(TUNEIN_ROOT)
    assert total == 8 and [e.title for e in entries][:3] == ["My Favorites", "Local Radio", "Music"]
    assert all(e.parent_id == TUNEIN_ROOT for e in entries)
    assert fake.calls[-1][0] == "Browse"


async def test_browse_all_pages(system, fake, monkeypatch):
    import aionuvo.zone

    monkeypatch.setattr(aionuvo.zone, "BROWSE_PAGE", 20)
    zone = zone_of(system)
    local_id = next(k for k in LISTINGS if "c=local" in k)
    entries = await zone.browse_all(local_id)
    assert len(entries) == 49 and [e.index for e in entries] == list(range(1, 50))
    browses = [c[1] for c in fake.calls if c[0] == "Browse"]
    assert [(b["StartingIndex"], b["RequestedCount"]) for b in browses] == [("0", "20"), ("20", "20"), ("40", "20")]
    assert len(await zone.browse_all(local_id, limit=30)) == 30


async def test_browse_error_leaves_zone_available(system, fake):
    zone = zone_of(system)
    with pytest.raises(NuvoActionError):
        await zone.browse("favorites:")
    assert zone.available


async def play_95bfm(zone):
    root = await zone.browse_all(TUNEIN_ROOT)
    local = next(e for e in root if e.title == "Local Radio")
    station = next(e for e in await zone.browse_all(local.id) if e.id == S7160)
    await zone.play_item(local, station)
    return local, station


async def test_play_station(system, fake):
    zone = zone_of(system)
    await zone.turn_off()
    fake.calls.clear()
    local, station = await play_95bfm(zone)
    writes = [c for c in fake.calls if c[0] != "Browse"]
    assert [c[0] for c in writes] == ["GroupCreate", "X_NUVO_PlayContainerURI"]
    play = writes[1][1]
    # The exact payload that played 95bFM on a P4300 (docs/protocol.md, "TuneIn playback").
    assert play["CurrentURI"] == ""
    assert play["CurrentURIMetaData"] == local.didl and 'nsdkDisplayName=Local%20Radio"' in play["CurrentURIMetaData"]
    assert play["TrackURI"] == "nuvo:tunein:browse?nsdkGuideId=s7160"
    assert play["TrackURIMetaData"] == station.didl
    assert play["StartingIndex"] == "2" and play["UpdateID"] == "-1"
    assert await eventually(lambda: zone.source == "tunein")


async def test_play_item_rejects_mismatch(system, fake):
    zone = zone_of(system)
    root = await zone.browse_all(TUNEIN_ROOT)
    local = next(e for e in await zone.browse_all(root[1].id) if e.playable)
    with pytest.raises(NuvoError):
        await zone.play_item(root[2], local)  # Music is not the station's parent
    with pytest.raises(NuvoError):
        await zone.play_item(root[0], root[1])  # a container is not playable
    assert not any(c[0] == "X_NUVO_PlayContainerURI" for c in fake.calls)


async def test_turn_on_switches_tunein_to_line_in(system, fake):
    """Music Assistant's power control calls turn_on when its player starts."""
    zone = zone_of(system)
    await play_95bfm(zone)
    assert await eventually(lambda: zone.source == "tunein")
    fake.calls.clear()
    await zone.turn_on()
    assert [c[0] for c in fake.calls if not c[0].startswith("Get")] == ["X_NUVO_PlayContainerURI"]
    assert fake.uri == fake.line_in_uri
