from aionuvo.const import ZONE_DEVICE_TYPE
from aionuvo.discovery import DiscoveredZone, LocationCache, ZoneWatcher, _parse, async_discover
from aionuvo.didl import parse_metadata

from .fake_zone import FakeSsdp


async def test_unicast_discovery_ignores_other_devices(ssdp, fake):
    found = await async_discover(1, ["127.0.0.1"], multicast=False, port=ssdp.port)
    assert found == {fake.udn: DiscoveredZone(fake.udn, fake.location)}
    assert found[fake.udn].host == "127.0.0.1"
    assert ssdp.searches[0] == ZONE_DEVICE_TYPE


async def test_falls_back_to_ssdp_all(fake):
    ssdp = FakeSsdp([fake], only_ssdp_all=True)
    await ssdp.start()
    try:
        found = await async_discover(1, ["127.0.0.1"], multicast=False, port=ssdp.port)
    finally:
        ssdp.stop()
    assert list(found) == [fake.udn]
    assert ssdp.searches == [ZONE_DEVICE_TYPE, "ssdp:all"]


async def test_search_to_bad_host_is_harmless():
    assert await async_discover(1, ["256.1.1.1"], multicast=False) == {}


def test_parse_headers():
    loc = "http://10.0.0.5:4000/x.xml"
    good = {"st": ZONE_DEVICE_TYPE, "usn": f"uuid:abc::{ZONE_DEVICE_TYPE}", "location": loc}
    assert _parse(good) == DiscoveredZone("uuid:abc", loc)
    nt = {"nt": ZONE_DEVICE_TYPE, "usn": f"uuid:abc::{ZONE_DEVICE_TYPE}", "location": loc}
    assert _parse(nt) == DiscoveredZone("uuid:abc", loc)
    assert _parse({**good, "location": None}) is None
    assert _parse({**good, "st": "upnp:rootdevice", "usn": "uuid:abc::upnp:rootdevice"}) is None
    assert _parse({**good, "usn": f"abc::{ZONE_DEVICE_TYPE}"}) is None


def test_location_cache_roundtrip(tmp_path):
    cache = LocationCache(tmp_path / "c.json")
    assert cache.load() == {}
    cache.save({"uuid:a": "http://x/1.xml"})
    assert cache.load() == {"uuid:a": "http://x/1.xml"}
    (tmp_path / "c.json").write_text("not json")
    assert cache.load() == {}


def test_watcher_callback_filters():
    seen = []
    w = ZoneWatcher(seen.append)

    class Dev:
        def combined_headers(self, t):
            return {"nt": t, "usn": f"uuid:z::{t}", "location": "http://h:1/d.xml"}

    w._callback(Dev(), "upnp:rootdevice", None)
    w._callback(Dev(), ZONE_DEVICE_TYPE, None)
    assert seen == [DiscoveredZone("uuid:z", "http://h:1/d.xml")]


async def test_watcher_start_stop():
    w = ZoneWatcher(lambda z: None)
    try:
        await w.async_start()
        await w.async_search()
    except OSError:
        pass  # no multicast in some CI sandboxes
    await w.async_stop()


def test_parse_metadata():
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        "<item><dc:title>Song</dc:title><upnp:artist>Band</upnp:artist><upnp:album>LP</upnp:album>"
        "<upnp:albumArtURI>http://img/a.jpg</upnp:albumArtURI></item></DIDL-Lite>"
    )
    assert parse_metadata(didl) == {"title": "Song", "artist": "Band", "album": "LP", "image_url": "http://img/a.jpg",
                                   "stream_url": None}
    empty = {"title": None, "artist": None, "album": None, "image_url": None, "stream_url": None}
    assert parse_metadata("") == empty
    assert parse_metadata("<not xml") == empty
    assert parse_metadata('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"/>') == empty
