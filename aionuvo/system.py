"""NuvoSystem: all zones of one installation, discovery upkeep and grouping."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections.abc import Iterable

import aiohttp
from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpSessionRequester
from async_upnp_client.client import UpnpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.exceptions import UpnpError

from .const import POLL_INTERVAL, REDISCOVERY_BACKOFF, SSDP_PORT, ZONE_DEVICE_TYPE, ZONE_SERVICE
from .discovery import DiscoveredZone, LocationCache, ZoneWatcher, async_discover
from .exceptions import NuvoConnectionError, NuvoError
from .zone import _CONNECTION_ERRORS, NuvoZone

_LOGGER = logging.getLogger(__name__)

TICK = 5.0


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
    ) -> None:
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
        zone = NuvoZone(device, self._factory, locate=self._locate, system=self)
        try:
            await zone.async_update()
        except NuvoError as err:
            _LOGGER.debug("Cannot read %s: %r", location, err)
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
        zone = next((z for z in self.zones.values() if z.udn == found.udn), None)
        if zone and zone.location != found.location:
            _LOGGER.info("SSDP: %s is now at %s", zone.name, found.location)
            asyncio.create_task(self._relocate(zone, found.location))

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

    async def group(self, master: NuvoZone, members: Iterable[NuvoZone]) -> None:
        """Make `members` listen to `master`'s group (GroupMemberSetGroup).

        `master` is turned on to its Line In first if it is off. If `master` itself
        listens to another zone, members join that group instead.
        """
        await master._refresh_group()
        if not master.state.is_on:
            await master.select_source("line_in")
        gid = master.state.member_group
        joining = [m for m in members if m is not master]
        for zone in joining:
            await zone._refresh_group()
        ids = [z.member_id for z in joining if z.state.member_group != gid]
        if ids:
            async with master._lock:
                await master._call(ZONE_SERVICE, "GroupMemberSetGroup", memberIDs=json.dumps(ids), groupID=gid)
        for zone in (master, *joining):
            await zone.async_update()

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
            await zone.async_update()
            return
        others = [z for z in zone.group_members if z is not zone]
        for other in others:
            await other._refresh_group()
        ids = [z.member_id for z in others if z.state.member_group == zone.state.member_group]
        if ids:
            async with zone._lock:
                await zone._call(ZONE_SERVICE, "GroupMemberSetGroup", memberIDs=json.dumps(ids), groupID="")
        for z in (zone, *others):
            await z.async_update()
