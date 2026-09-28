"""NuvoSystem: all zones of one installation, discovery upkeep and grouping."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

import aiohttp
from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpSessionRequester
from async_upnp_client.client import UpnpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.exceptions import UpnpError

from .const import POLL_INTERVAL, REDISCOVERY_BACKOFF, SSDP_PORT, ZONE_DEVICE_TYPE, ZONE_SERVICE
from .discovery import DiscoveredZone, LocationCache, ZoneWatcher, async_discover
from .exceptions import NuvoConnectionError, NuvoError
from .webapi import NuvoWebApi
from . import zone as _zone
from .zone import _CONNECTION_ERRORS, NuvoZone

_LOGGER = logging.getLogger(__name__)

TICK = 5.0
# Seconds to wait before polling zones after a group change.
RECONCILE_DELAY = 3.0


def _local_ip_for(host: str) -> str:
    """The local address the OS would use to reach `host` (no packets sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, 1))
        return s.getsockname()[0]


class NuvoSystem:
    """A set of Nuvo zones, keyed by member ID (``memberId-<mac>``)."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None = None,
        requester: UpnpRequester | None = None,
        hosts: Iterable[str] = (),
        callback_host: str | None = None,
        callback_port: int = 0,
        cache_path: str | None = None,
        multicast: bool = True,
        ssdp_port: int = SSDP_PORT,
        search_timeout: int = 4,
        web_port: int = 80,
        system_id: str | None = None,
    ) -> None:
        self._system_id = system_id
        self._web_port = web_port
        self._own_session = session is None and requester is None
        self._session = session
        self._requester = requester
        self.hosts = list(hosts)
        self._callback_host = callback_host
        self._callback_port = callback_port
        self._cache = LocationCache(cache_path) if cache_path else None
        self._multicast = multicast
        self._ssdp_port = ssdp_port
        self._search_timeout = search_timeout
        self._factory: UpnpFactory | None = None
        self._notify_server: AiohttpNotifyServer | None = None
        self._watcher: ZoneWatcher | None = None
        self._task: asyncio.Task | None = None
        self._backoff: dict[str, tuple[int, float]] = {}
        self.zones: dict[str, NuvoZone] = {}
        self._adding: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._zone_added: list[Callable[[NuvoZone], None]] = []

    @classmethod
    async def discover(cls, timeout: int = 4, hosts: Iterable[str] = (), **kwargs) -> NuvoSystem:
        system = cls(hosts=hosts, search_timeout=timeout, **kwargs)
        await system.async_start()
        return system

    @property
    def system_id(self) -> str | None:
        return next((z.state.system_id for z in self.zones.values() if z.state.system_id), None)

    # --- lifecycle ------------------------------------------------------

    async def async_start(
        self, locations: dict[str, str] | None = None, *, subscribe: bool = True, watch: bool = True
    ) -> None:
        """Find zones (known LOCATIONs, cache, then SSDP), load them and subscribe."""
        if self._requester is None:
            if self._session is None:
                self._session = aiohttp.ClientSession()
            self._requester = AiohttpSessionRequester(self._session, with_sleep=True, timeout=5)
        self._factory = UpnpFactory(self._requester, non_strict=True)

        known = dict(locations or {})
        if self._cache:
            known = {**self._cache.load(), **known}
        loaded = await self._load_all(known)
        found = await async_discover(
            self._search_timeout, self.hosts, multicast=self._multicast, port=self._ssdp_port
        )
        missing = {udn: z.location for udn, z in found.items() if udn not in loaded}
        stale = [udn for udn in loaded if udn in found and loaded[udn].location != found[udn].location]
        for udn in stale:
            await loaded[udn].async_relocate(found[udn].location)
        loaded.update(await self._load_all(missing))
        for zone in loaded.values():
            self.zones[zone.member_id] = zone
        if not self.zones:
            raise NuvoConnectionError("No Nuvo zones found")
        self._save_cache()

        if subscribe:
            await self._start_events()
        if watch and self._multicast:
            self._watcher = ZoneWatcher(self._on_ssdp)
            try:
                await self._watcher.async_start()
            except OSError as err:
                _LOGGER.warning("SSDP listener unavailable (%s); relying on unicast search", err)
                self._watcher = None
        self._task = asyncio.create_task(self._maintain())

    async def async_stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._watcher:
            await self._watcher.async_stop()
        for zone in self.zones.values():
            await zone.async_unsubscribe_events()
        if self._notify_server:
            await self._notify_server.async_stop_server()
        if self._own_session and self._session:
            await self._session.close()

    async def _load_all(self, locations: dict[str, str]) -> dict[str, NuvoZone]:
        results = await asyncio.gather(*(self._load(loc) for loc in locations.values()))
        return {z.udn: z for z in results if z is not None}

    async def _load(self, location: str) -> NuvoZone | None:
        assert self._factory is not None
        try:
            device = await self._factory.async_create_device(location)
        except (UpnpError, *_CONNECTION_ERRORS) as err:
            _LOGGER.debug("Cannot load %s: %r", location, err)
            return None
        if device.device_type != ZONE_DEVICE_TYPE or device.find_service(ZONE_SERVICE) is None:
            return None
        web = None
        if self._session is not None and device.serial_number:
            host = urlsplit(location).hostname or ""
            web = NuvoWebApi(self._session, host, device.serial_number, port=self._web_port)
        zone = NuvoZone(device, self._factory, locate=self._locate, system=self, web=web)
        try:
            await zone.async_update()
        except NuvoError as err:
            _LOGGER.debug("Cannot read %s: %r", location, err)
            return None
        if self._system_id and zone.state.system_id != self._system_id:
            _LOGGER.debug("Ignoring %s: belongs to system %s", zone.name, zone.state.system_id)
            return None
        return zone

    async def _start_events(self) -> None:
        assert self._requester is not None
        first = next(iter(self.zones.values()))
        host = self._callback_host or _local_ip_for(first.host)
        self._notify_server = AiohttpNotifyServer(self._requester, source=(host, self._callback_port))
        await self._notify_server.async_start_server()
        _LOGGER.debug("Event callback URL %s", self._notify_server.callback_url)
        for zone in self.zones.values():
            try:
                await zone.async_subscribe_events(self._notify_server.event_handler)
            except (UpnpError, *_CONNECTION_ERRORS) as err:
                _LOGGER.warning("Subscribe to %s failed (%r); polling instead", zone.name, err)

    def _save_cache(self) -> None:
        if self._cache:
            self._cache.save({z.udn: z.location for z in self.zones.values()})

    # --- rediscovery ----------------------------------------------------

    async def _locate(self, zone: NuvoZone) -> str | None:
        hosts = list(dict.fromkeys([zone.host, *self.hosts]))
        found = await async_discover(
            self._search_timeout, hosts, multicast=self._multicast, port=self._ssdp_port
        )
        hit = found.get(zone.udn)
        if hit and hit.location != zone.location:
            asyncio.get_running_loop().call_soon(self._save_cache)
        return hit.location if hit else None

    def _on_ssdp(self, found: DiscoveredZone) -> None:
        self.async_location_seen(found.udn, found.location)

    def async_location_seen(self, udn: str, location: str) -> None:
        """Feed an SSDP sighting (e.g. from Home Assistant's scanner).

        A known zone at a new LOCATION is relocated; an unknown zone of this
        system (for example one that was offline at startup) is added.
        """
        zone = next((z for z in self.zones.values() if z.udn == udn), None)
        if zone is None:
            if udn not in self._adding:
                self._adding.add(udn)
                self._spawn(self._add_zone(udn, location))
        elif zone.location != location:
            _LOGGER.info("SSDP: %s is now at %s", zone.name, location)
            self._spawn(self._relocate(zone, location))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _add_zone(self, udn: str, location: str) -> None:
        try:
            zone = await self._load(location)
            if zone is None or zone.udn != udn or zone.member_id in self.zones:
                return
            if self.system_id and zone.state.system_id != self.system_id:
                return
            if self._notify_server:
                try:
                    await zone.async_subscribe_events(self._notify_server.event_handler)
                except (UpnpError, *_CONNECTION_ERRORS):
                    pass  # the maintenance loop retries
            # Publish and announce together, so listeners never see a half-added zone.
            self.zones[zone.member_id] = zone
            _LOGGER.info("Added zone %s at %s", zone.name, location)
            self._save_cache()
            for cb in list(self._zone_added):
                cb(zone)
        finally:
            self._adding.discard(udn)

    def on_zone_added(self, callback: Callable[[NuvoZone], None]) -> Callable[[], None]:
        """Call `callback(zone)` when a zone is added after startup."""
        self._zone_added.append(callback)
        return lambda: self._zone_added.remove(callback)

    async def _relocate(self, zone: NuvoZone, location: str) -> None:
        if await zone.async_relocate(location):
            self._backoff.pop(zone.udn, None)
            self._save_cache()
            try:
                await zone.async_update()
            except NuvoError:
                pass

    async def _maintain(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            for zone in list(self.zones.values()):
                try:
                    await self._maintain_zone(zone)
                except Exception:  # noqa: BLE001 - keep the loop alive
                    _LOGGER.exception("Maintenance of %s failed", zone.name)

    async def _maintain_zone(self, zone: NuvoZone) -> None:
        now = time.monotonic()
        if not zone.available:
            attempt, due = self._backoff.get(zone.udn, (0, 0.0))
            if now < due:
                return
            if await zone.async_recover():
                self._backoff.pop(zone.udn, None)
                await zone.async_update()
                return
            delay = REDISCOVERY_BACKOFF[min(attempt, len(REDISCOVERY_BACKOFF) - 1)]
            self._backoff[zone.udn] = (attempt + 1, now + delay)
            return
        if zone.subscribed and now - zone.last_renewed >= zone.renew_interval:
            try:
                await zone.async_renew_events()
            except (UpnpError, KeyError, *_CONNECTION_ERRORS) as err:
                _LOGGER.info("Renewal for %s failed (%r); re-subscribing", zone.name, err)
                zone.subscribed = False
                if self._notify_server:
                    try:
                        await zone.async_subscribe_events(self._notify_server.event_handler)
                    except (UpnpError, *_CONNECTION_ERRORS):
                        await zone.async_recover()
        if not zone.subscribed:
            if self._notify_server and zone.available:
                try:
                    await zone.async_subscribe_events(self._notify_server.event_handler)
                except (UpnpError, *_CONNECTION_ERRORS):
                    pass
            if now - zone.last_polled >= POLL_INTERVAL:
                try:
                    await zone.async_update()
                except NuvoError:
                    pass

    # --- grouping (verified on hardware 2026-09-28, docs/protocol.md) ----

    # The amp's Get lags its own events by up to a second or so after a group
    # change (docs/protocol.md), so after a write we set state optimistically,
    # let events confirm it, and only poll once things have settled.

    async def group(self, master: NuvoZone, members: Iterable[NuvoZone]) -> None:
        """Make `members` listen to `master`'s group (GroupMemberSetGroup).

        `master` is turned on to its Line In first if it is off. If `master` itself
        listens to another zone, members join that group instead.
        """
        await master._refresh_group()
        if master.state.is_on:
            gid = master.state.member_group
        else:
            gid = await master.select_source("line_in")
        joining = [m for m in members if m is not master]
        for zone in joining:
            await zone._refresh_group()
        moving = [z for z in joining if z.state.member_group != gid]
        if moving:
            async with master._lock:
                await master._call(
                    ZONE_SERVICE, "GroupMemberSetGroup", memberIDs=json.dumps([z.member_id for z in moving]), groupID=gid
                )
            for zone in moving:
                zone._mark_group_written()
                zone.state.member_group, zone.state.master_group = gid, ""
        self._settled((master, *joining))

    async def ungroup(self, zone: NuvoZone) -> None:
        """Take `zone` out of its group.

        A joined zone leaves and goes off. For a group master, every other member
        leaves (and goes off) while the master keeps playing.
        """
        await zone._refresh_group()
        if not zone.state.is_on:
            return
        if zone.is_joined:
            async with zone._lock:
                await zone._leave_group()
            self._settled((zone,))
            return
        for other in self.zones.values():
            if other is not zone:
                await other._refresh_group()
        others = [z for z in zone.group_members if z is not zone]
        leaving = [z for z in others if z.state.member_group == zone.state.member_group]
        if leaving:
            async with zone._lock:
                await zone._call(
                    ZONE_SERVICE, "GroupMemberSetGroup", memberIDs=json.dumps([z.member_id for z in leaving]), groupID=""
                )
            for z in leaving:
                z._mark_group_written()
                z.state.member_group = z.state.master_group = ""
        self._settled((zone, *others))

    def _settled(self, zones: Iterable[NuvoZone]) -> None:
        """Notify listeners now; reconcile with a poll once the amp has settled."""
        zones = list(zones)
        for zone in zones:
            zone._notify()

        async def _reconcile() -> None:
            await asyncio.sleep(max(RECONCILE_DELAY, _zone.GROUP_READ_LAG))
            for zone in zones:
                try:
                    await zone.async_update()
                except NuvoError:
                    pass

        self._spawn(_reconcile())


async def async_probe(
    session: aiohttp.ClientSession,
    hosts: Iterable[str] = (),
    *,
    multicast: bool = True,
    timeout: int = 4,
    **kwargs,
) -> dict[str, dict[str, object]]:
    """Find Nuvo systems without subscribing. Returns {system_id: {"zones": [...], "hosts": [...], "model": ...}}."""
    system = NuvoSystem(session=session, hosts=hosts, multicast=multicast, search_timeout=timeout, **kwargs)
    try:
        await system.async_start(subscribe=False, watch=False)
    except NuvoConnectionError:
        await system.async_stop()
        return {}
    found: dict[str, dict[str, object]] = {}
    for zone in system.zones.values():
        entry = found.setdefault(
            zone.state.system_id or "", {"zones": [], "hosts": [], "model": zone.state.model}
        )
        entry["zones"].append(zone.name)
        entry["hosts"].append(zone.host)
    await system.async_stop()
    return found
