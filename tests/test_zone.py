import asyncio
import time
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from aionuvo import DeniedActionError, NuvoActionError, NuvoConnectionError, NuvoError
from aionuvo.const import ZONE_SERVICE
from aionuvo.didl import LINE_IN_CONTAINER_METADATA, line_in_track_metadata

from .conftest import make_system
from .fake_zone import FakeSsdp, FakeZone

ROOT = Path(__file__).resolve().parent.parent


async def eventually(pred, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


def zone_of(system):
    return system.zones["memberId-0025ed1dd983"]


async def test_initial_state(system, fake):
    zone = zone_of(system)
    assert zone.name == "Lounge"
    assert zone.mac == "0025ed1dd983"
    assert zone.host == "127.0.0.1"
    assert system.system_id == "nuvoTEST"
    assert zone.state.volume_raw == 44
    assert zone.volume_level == pytest.approx(0.44)
    assert zone.state.muted is False
    assert zone.state.is_on
    assert zone.source == "line_in"
    assert zone.state.transport_actions == {"Play", "Stop"}
    assert zone.subscribed
    assert [s.key for s in zone.sources] == ["line_in"]


@pytest.mark.parametrize(
    ("level", "raw"), [(0.0, 0), (1.0, 100), (1.5, 100), (-0.2, 0), (0.355, 36), (0.5, 50)]
)
async def test_volume_mapping(system, fake, level, raw):
    await zone_of(system).set_volume(level)
    assert fake.volume == raw
    assert zone_of(system).state.volume_raw == raw


async def test_volume_raw_clamped(system, fake):
    await zone_of(system).set_volume_raw(250)
    assert fake.volume == 100


async def test_volume_level_unknown():
    from aionuvo.models import ZoneState

    assert ZoneState().volume_raw is None


async def test_volume_step_and_mute(system, fake):
    zone = zone_of(system)
    await zone.volume_step(3)
    assert fake.volume == 47
    assert fake.calls[-1] == ("X_NUVO_AdjustVolume", {"InstanceID": "0", "Channel": "Master", "VolumeAdjustment": "3"})
    await zone.set_mute(True)
    assert fake.muted is True
    await zone.set_mute(False)
    assert fake.muted is False


async def test_push_event_from_app(system, fake):
    zone = zone_of(system)
    seen = []
    unsub = zone.subscribe(lambda z: seen.append(z.state.volume_raw))
    await fake.app_set_volume(12)
    assert await eventually(lambda: zone.state.volume_raw == 12, timeout=2)
    assert 12 in seen
    unsub()


async def test_listener_exception_is_contained(system, fake):
    zone = zone_of(system)
    zone.subscribe(lambda z: 1 / 0)
    await zone.set_mute(True)
    await zone.async_update()


async def test_select_source_when_off(system, fake):
    zone = zone_of(system)
    await zone.turn_off()
    assert fake.member_group == ""
    fake.calls.clear()
    await zone.select_source("line_in")
    names = [c[0] for c in fake.calls]
    # Right after our own turn_off the zone's Get is not trusted (amp lag), so no Get.
    assert names == ["GroupCreate", "X_NUVO_PlayContainerURI"]
    assert json.loads(fake.calls[0][1]["memberIDs"]) == ["memberId-0025ed1dd983"]
    play = fake.calls[1][1]
    assert play["CurrentURI"] == ""
    assert play["CurrentURIMetaData"] == LINE_IN_CONTAINER_METADATA
    assert play["TrackURI"] == "nuvo:nuvoremote:memberId-0025ed1dd983/lineIn_memberId-0025ed1dd983"
    assert play["TrackURIMetaData"] == line_in_track_metadata("memberId-0025ed1dd983", "Lounge")
    assert play["StartingIndex"] == "1"
    assert play["UpdateID"] == "-1"
    assert fake.transport == "PLAYING"
    assert await eventually(lambda: zone.source == "line_in")


async def test_select_source_when_on_skips_group_create(system, fake):
    fake.calls.clear()
    await zone_of(system).select_source("line_in")
    assert [c[0] for c in fake.calls] == ["Get", "X_NUVO_PlayContainerURI"]


async def test_select_unknown_source(system):
    with pytest.raises(NuvoError):
        await zone_of(system).select_source("tidal")


async def test_turn_off_and_on(system, fake):
    zone = zone_of(system)
    await zone.turn_off()
    assert fake.calls[-1] == ("GroupDisband", {"groupID": "gidInitial"})
    assert zone.playback_state == "off"
    assert await eventually(lambda: not zone.state.is_on and zone.source is None)
    n = len(fake.calls)
    await zone.turn_off()  # already off: no write
    assert all(c[0].startswith("Get") for c in fake.calls[n:])
    await zone.turn_on()
    assert fake.transport == "PLAYING"
    fake.calls.clear()
    await zone.turn_on()  # already playing: no writes
    assert all(c[0].startswith("Get") for c in fake.calls)


async def test_transport(system, fake):
    zone = zone_of(system)
    await zone.stop()
    assert fake.transport == "STOPPED"
    await zone.play()
    assert fake.calls[-1] == ("Play", {"InstanceID": "0", "Speed": "1"})
    await zone.pause()
    assert fake.transport == "PAUSED_PLAYBACK"
    await zone.next()
    await zone.previous()
    assert [c[0] for c in fake.calls[-2:]] == ["Next", "Previous"]


async def test_soap_fault(system, fake):
    fake.faults["SetVolume"] = (501, "Action Failed")
    with pytest.raises(NuvoActionError) as err:
        await zone_of(system).set_volume(0.1)
    assert err.value.code == 501
    assert zone_of(system).available


@pytest.mark.parametrize("action", ["RestoreFactoryDefaults", "SystemCreate", "SystemJoin", "SystemConfigureGWs"])
async def test_denylist_never_reaches_device(system, fake, action, monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    fake.calls.clear()
    with pytest.raises(DeniedActionError):
        await zone_of(system)._call(ZONE_SERVICE, action)
    assert fake.calls == []


async def test_port_change_mid_session(system, fake):
    """Amp reboot: new port, subscriptions gone. The next command rediscovers and retries."""
    zone = zone_of(system)
    old = zone.location
    await fake.restart_on_new_port()
    await zone.set_volume_raw(20)
    assert fake.volume == 20
    assert zone.location != old and zone.location == fake.location
    assert zone.available
    assert fake.subs, "re-subscribed after relocation"
    await fake.app_set_volume(33)
    assert await eventually(lambda: zone.state.volume_raw == 33)


async def test_command_waits_for_restarting_zone(system, fake):
    """The zone's UPnP process restarts: gone briefly, then back on a new port."""
    zone = zone_of(system)
    await fake.stop()

    async def come_back():
        await asyncio.sleep(0.1)
        await fake.start()

    task = asyncio.create_task(come_back())
    await zone.set_mute(True)
    await task
    assert fake.muted and zone.location == fake.location


async def test_ssdp_alive_with_new_location(system, fake):
    from aionuvo.discovery import DiscoveredZone

    zone = zone_of(system)
    await fake.restart_on_new_port()
    system._on_ssdp(DiscoveredZone(zone.udn, fake.location))
    assert await eventually(lambda: zone.location == fake.location)
    system._on_ssdp(DiscoveredZone("uuid:unknown", "http://127.0.0.1:1/x.xml"))  # ignored


async def test_unreachable_then_recovers(system, fake, ssdp):
    zone = zone_of(system)
    states = []
    zone.subscribe(lambda z: states.append(z.available))
    port = fake.port
    await fake.stop()
    with pytest.raises(NuvoConnectionError):
        await zone.set_mute(True)
    assert zone.available is False and False in states
    await fake.start(port=0)  # back on a new port; maintenance loop should find it
    assert await eventually(lambda: zone.available and zone.location == fake.location, timeout=8)


async def test_dropped_subscription_is_renewed(system, fake):
    zone = zone_of(system)
    fake.subs.clear()  # device forgot us: renewals now get 412
    zone.last_renewed = time.monotonic() - zone.renew_interval - 1  # not 0: CI clocks can be < 150 s
    assert await eventually(lambda: len(fake.subs) == 3 and zone.subscribed, timeout=3)
    await fake.app_set_volume(5)
    assert await eventually(lambda: zone.state.volume_raw == 5)


async def test_polls_when_subscription_impossible(system, fake, monkeypatch):
    zone = zone_of(system)
    monkeypatch.setattr("aionuvo.system.POLL_INTERVAL", 0)
    await zone.async_unsubscribe_events()
    fake.reject_renew = True
    system._notify_server = None  # cannot re-subscribe: must poll
    fake.volume = 7  # changed silently, no NOTIFY
    assert await eventually(lambda: zone.state.volume_raw == 7, timeout=3)


LOUNGE, DINING = "memberId-0025ed1dd983", "memberId-0025ed1dd6e1"


async def test_group_join_and_model(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    assert lounge.group_members == [lounge] and not lounge.is_joined
    await system.group(lounge, [lounge, dining])
    call = next(c for c in f_lounge.calls if c[0] == "GroupMemberSetGroup")
    assert call[1] == {"memberIDs": json.dumps([DINING]), "groupID": "gidLounge"}
    assert f_dining.member_group == "gidLounge" and f_dining.master_group == ""
    assert dining.is_joined and dining.master is lounge
    assert [z.name for z in lounge.group_members] == ["Lounge", "Dining Room"]
    assert [z.name for z in dining.group_members] == ["Lounge", "Dining Room"]
    assert dining.playback_zone is lounge
    assert dining.playback_state == "PLAYING"  # its own transport says NO_MEDIA_PRESENT
    assert await eventually(lambda: dining.state.transport_state == "NO_MEDIA_PRESENT")
    assert lounge.source == "line_in" and dining.source is None
    f_lounge.calls.clear()
    await system.group(lounge, [dining])  # already joined: no call
    assert "GroupMemberSetGroup" not in [c[0] for c in f_lounge.calls]


async def test_group_turns_master_on(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await lounge.turn_off()
    assert lounge.playback_state == "off" and lounge.master is None
    await system.group(lounge, [dining])
    assert f_lounge.transport == "PLAYING" and f_dining.member_group == f_lounge.member_group


async def test_ungroup_member(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    await system.ungroup(dining)
    assert f_dining.calls[-2][0] == "GroupMemberSetGroup" or ("GroupMemberSetGroup", {"memberIDs": json.dumps([DINING]), "groupID": ""}) in f_dining.calls
    assert f_dining.member_group == "" and not dining.state.is_on
    assert f_lounge.transport == "PLAYING" and lounge.group_members == [lounge]
    await system.ungroup(dining)  # already off: no-op


async def test_ungroup_master_releases_members(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    await system.ungroup(lounge)
    assert f_dining.member_group == "" and f_lounge.transport == "PLAYING"
    assert lounge.group_members == [lounge]


async def test_turn_off_member_leaves_group(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    await dining.turn_off()
    assert f_dining.member_group == "" and f_lounge.member_group == "gidLounge"
    assert f_lounge.transport == "PLAYING"


async def test_turn_off_master_turns_off_group(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    await lounge.turn_off()
    assert f_lounge.member_group == "" and f_dining.member_group == ""
    await dining.async_update()
    assert dining.playback_state == "off"


async def test_select_source_on_member_leaves_group_first(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    f_dining.calls.clear()
    await dining.select_source("line_in")
    assert [c[0] for c in f_dining.calls] == ["GroupMemberSetGroup", "GroupCreate", "X_NUVO_PlayContainerURI"]
    assert f_dining.uri == f_dining.line_in_uri and f_lounge.transport == "PLAYING"
    await dining.async_update()
    assert dining.source == "line_in" and not dining.is_joined


async def test_turn_on_joined_member_is_noop(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    f_dining.calls.clear()
    await dining.turn_on()
    assert all(c[0].startswith("Get") for c in f_dining.calls)


async def test_post_reboot_grouped_but_silent(amp):
    """After an amp power cycle zones keep their group but play nothing."""
    system, f_lounge, _ = amp
    lounge = system.zones[LOUNGE]
    f_lounge.transport, f_lounge.uri = "NO_MEDIA_PRESENT", ""
    await lounge.async_update()
    assert lounge.state.is_on and lounge.playback_state == "NO_MEDIA_PRESENT" and lounge.source is None
    f_lounge.calls.clear()
    await lounge.turn_on()
    assert [c[0] for c in f_lounge.calls if not c[0].startswith("Get")] == ["X_NUVO_PlayContainerURI"]
    assert f_lounge.transport == "PLAYING"


async def test_location_cache(ssdp, fake, tmp_path):
    cache = tmp_path / "cache.json"
    s = make_system(ssdp, cache_path=str(cache))
    await s.async_start(subscribe=False)
    await s.async_stop()
    assert json.loads(cache.read_text()) == {fake.udn: fake.location}
    # Stale cached port: startup still finds the zone at its real location.
    cache.write_text(json.dumps({fake.udn: "http://127.0.0.1:1/00000000-0000-0000-0000-0025ed1dd983.xml"}))
    s = make_system(ssdp, cache_path=str(cache))
    await s.async_start(subscribe=False)
    assert zone_of(s).location == fake.location
    await s.async_stop()


async def test_cached_location_changed(ssdp, fake, tmp_path):
    """Cache points at a live but outdated port of the same zone."""
    s = make_system(ssdp)
    old = fake.location
    await s.async_start(locations={fake.udn: old}, subscribe=False)
    assert zone_of(s).location == old
    await s.async_stop()


async def test_no_zones(tmp_path):
    ssdp = FakeSsdp([])
    await ssdp.start()
    s = make_system(ssdp)
    with pytest.raises(NuvoConnectionError):
        await s.async_start()
    await s.async_stop()
    ssdp.stop()


async def test_discover_classmethod(ssdp, fake):
    from aionuvo import NuvoSystem

    s = await NuvoSystem.discover(
        timeout=1, hosts=["127.0.0.1"], multicast=False, ssdp_port=ssdp.port, callback_host="127.0.0.1"
    )
    assert list(s.zones) == ["memberId-0025ed1dd983"]
    assert "Lounge" in repr(zone_of(s))
    await s.async_stop()


async def test_loudness(system, fake):
    zone = zone_of(system)
    await zone.set_loudness(True)
    assert fake.loudness is True
    await zone.async_update_loudness()
    assert zone.state.loudness is True


async def test_tone_controls(system, fake):
    zone = zone_of(system)
    await zone.async_update_tone()
    assert zone.state.tone == {"bass": 0.0, "treble": 0.0, "balance": 0.0}
    await zone.set_tone("bass", 2.4)
    assert fake.tone["bass"] == 2.0 and zone.state.tone["bass"] == 2
    await zone.set_tone("balance", -40)
    assert fake.tone["balance"] == -18.0
    await zone.set_tone("treble", 99)
    assert fake.tone["treble"] == 6.0
    with pytest.raises(NuvoError):
        await zone.set_tone("speakerImpedance", 1)
    paths = {p["path"] for path, p in fake.web_requests if path == "/api/setData"}
    assert paths == {"settings://mediaPlayer/bass", "settings://mediaPlayer/balance", "settings://mediaPlayer/treble"}


async def test_tone_requires_login(system, fake):
    zone = zone_of(system)
    zone.web._cookie = "d3Jvbmc="  # wrong serial
    zone.web._authenticated = False
    with pytest.raises(NuvoError, match="login refused"):
        await zone.async_update_tone()


async def test_tone_without_web_api(system):
    zone = zone_of(system)
    zone.web = None
    with pytest.raises(NuvoError, match="web API unavailable"):
        await zone.set_tone("bass", 1)


async def test_web_api_denies_other_settings(system):
    from aionuvo import DeniedActionError

    with pytest.raises(DeniedActionError):
        await zone_of(system).web._api("/api/setData", {"path": "settings://mediaPlayer/speakerImpedance", "value": "x"})
    with pytest.raises(DeniedActionError):
        await zone_of(system).web._request("GET", "/diagnostics_execute.fcgi")


async def test_probe(ssdp, fake):
    import aiohttp

    from aionuvo import async_probe

    async with aiohttp.ClientSession() as session:
        found = await async_probe(session, ["127.0.0.1"], multicast=False, timeout=1, ssdp_port=ssdp.port)
        assert found == {"nuvoTEST": {"zones": ["Lounge"], "hosts": ["127.0.0.1"], "model": "p4300"}}
        empty = FakeSsdp([])
        await empty.start()
        assert await async_probe(session, ["127.0.0.1"], multicast=False, timeout=1, ssdp_port=empty.port) == {}
        empty.stop()


async def test_system_id_filter(ssdp, fake):
    s = make_system(ssdp, system_id="someOtherSystem")
    with pytest.raises(NuvoConnectionError):
        await s.async_start(subscribe=False)
    await s.async_stop()


async def test_late_zone_is_adopted(ssdp, fake):
    """A zone that was offline at startup is added when SSDP sees it."""
    dining = FakeZone(mac="0025ed1dd6e1", title="Dining Room", member_group="gidDining")
    s = make_system(ssdp, web_port=fake.port)
    await s.async_start()
    added = []
    unsub = s.on_zone_added(added.append)
    await dining.start()
    try:
        s.async_location_seen(dining.udn, dining.location)
        s.async_location_seen(dining.udn, dining.location)  # duplicate sighting while loading
        assert await eventually(lambda: DINING in s.zones)
        assert [z.name for z in added] == ["Dining Room"]
        assert s.zones[DINING].subscribed
        s.async_location_seen(dining.udn, dining.location)  # known, same place: no-op
        unsub()
    finally:
        await s.async_stop()
        await dining.stop()


async def test_unjoin_right_after_join_despite_lagging_get(amp):
    """Found on hardware: Get still reports the pre-join group ~0.5 s after a join,
    which made ungroup() think the zone was already off and do nothing."""
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await dining.turn_off()
    await asyncio.sleep(0.4)  # past the read-lag window of the turn_off
    await system.group(lounge, [dining])
    f_dining.stale_get_groups = ("", "")  # Get lags: still says "off"
    await system.ungroup(dining)
    assert ("GroupMemberSetGroup", {"memberIDs": json.dumps([DINING]), "groupID": ""}) in f_dining.calls
    assert f_dining.member_group == ""
    f_dining.stale_get_groups = None


async def test_lagging_get_does_not_clobber_fresh_group_state(amp):
    system, f_lounge, f_dining = amp
    lounge, dining = system.zones[LOUNGE], system.zones[DINING]
    await system.group(lounge, [dining])
    f_dining.stale_get_groups = ("gidDining", "gidDining")  # pre-join view
    await dining.async_update()
    assert dining.state.member_group == "gidLounge"
    await asyncio.sleep(0.4)  # window over: Get is trusted again
    f_dining.stale_get_groups = None
    await dining.async_update()
    assert dining.state.member_group == "gidLounge"
