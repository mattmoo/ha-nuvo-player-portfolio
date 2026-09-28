"""Find Nuvo zones and keep their (volatile) description LOCATION current.

Zones run GUPnP, which binds a random ephemeral HTTP port on every boot, so
zones are keyed by UDN and the LOCATION is always re-learned from SSDP.
Order: cached/known LOCATION, multicast M-SEARCH, unicast M-SEARCH to known
hosts, then an `ssdp:all` sweep filtered to Nuvo zones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from async_upnp_client.search import async_search
from async_upnp_client.ssdp import SSDP_ST_ALL
from async_upnp_client.ssdp_listener import SsdpDevice, SsdpListener
from async_upnp_client.utils import CaseInsensitiveDict

from .const import SSDP_PORT, ZONE_DEVICE_TYPE

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredZone:
    udn: str
    location: str

    @property
    def host(self) -> str:
        return urlsplit(self.location).hostname or ""


def _parse(headers: CaseInsensitiveDict) -> DiscoveredZone | None:
    """Return a zone for SSDP headers that advertise the Nuvo Zone device type."""
    usn = headers.get("usn", "")
    st = headers.get("st") or headers.get("nt") or ""
    location = headers.get("location")
    if not location or ZONE_DEVICE_TYPE not in (st, usn.partition("::")[2]):
        return None
    udn = usn.partition("::")[0]
    if not udn.startswith("uuid:"):
        return None
    return DiscoveredZone(udn=udn, location=location)


async def _search(
    found: dict[str, DiscoveredZone],
    search_target: str,
    timeout: int,
    target: tuple[str, int] | None,
) -> None:
    async def _on_response(headers: CaseInsensitiveDict) -> None:
        zone = _parse(headers)
        if zone:
            found[zone.udn] = zone

    try:
        await async_search(
            _on_response, timeout=timeout, search_target=search_target, target=target
        )
    except OSError as err:  # e.g. no multicast route
        _LOGGER.debug("SSDP search to %s failed: %s", target or "multicast", err)


async def _resolve(hosts: Iterable[str]) -> list[str]:
    """Resolve host names to IPv4 addresses; unresolvable hosts are skipped."""
    loop = asyncio.get_running_loop()
    ips: list[str] = []
    for host in hosts:
        try:
            info = await loop.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        except (OSError, UnicodeError) as err:
            _LOGGER.warning("Cannot resolve zone host %r: %s", host, err)
            continue
        ips.extend(i[4][0] for i in info if i[4][0] not in ips)
    return ips


async def async_discover(
    timeout: int = 4,
    hosts: Iterable[str] = (),
    *,
    multicast: bool = True,
    port: int = SSDP_PORT,
) -> dict[str, DiscoveredZone]:
    """Search for Nuvo zones. Returns {udn: DiscoveredZone}.

    `hosts` are zone IPs to also query by unicast M-SEARCH, which works across
    VLANs where multicast does not.
    """
    found: dict[str, DiscoveredZone] = {}
    targets: list[tuple[str, int] | None] = [(ip, port) for ip in await _resolve(hosts)]
    if multicast:
        targets.append(None)
    await asyncio.gather(*(_search(found, ZONE_DEVICE_TYPE, timeout, t) for t in targets))
    if not found:
        # Some GUPnP builds only answer ssdp:all (mpdrago, P3100).
        await asyncio.gather(*(_search(found, SSDP_ST_ALL, timeout, t) for t in targets))
    return found


async def async_find_zone(
    udn: str,
    timeout: int = 4,
    hosts: Iterable[str] = (),
    *,
    multicast: bool = True,
    port: int = SSDP_PORT,
) -> DiscoveredZone | None:
    """Rediscover one zone by UDN."""
    zones = await async_discover(timeout, hosts, multicast=multicast, port=port)
    return zones.get(udn)


class LocationCache:
    """Optional JSON file of {udn: location}, so a restart can skip the search."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)}

    def save(self, locations: dict[str, str]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(locations, indent=1, sort_keys=True))
        tmp.replace(self._path)


class ZoneWatcher:
    """Listen for ssdp:alive NOTIFYs and search replies; report LOCATION changes."""

    def __init__(self, on_location: Callable[[DiscoveredZone], None]) -> None:
        self._on_location = on_location
        self._listener: SsdpListener | None = None

    async def async_start(self) -> None:
        self._listener = SsdpListener(callback=self._callback, search_target=ZONE_DEVICE_TYPE)
        await self._listener.async_start()

    async def async_stop(self) -> None:
        if self._listener:
            await self._listener.async_stop()
            self._listener = None

    async def async_search(self) -> None:
        if self._listener:
            await self._listener.async_search()

    def _callback(self, device: SsdpDevice, device_or_service_type: str, source) -> None:
        if device_or_service_type != ZONE_DEVICE_TYPE:
            return
        headers = device.combined_headers(device_or_service_type)
        zone = _parse(headers)
        if zone:
            self._on_location(zone)
