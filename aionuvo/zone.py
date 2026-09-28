"""One Nuvo zone: volume, mute, source, power, transport and push events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import aiohttp
from async_upnp_client.client import UpnpDevice, UpnpService, UpnpStateVariable
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.event_handler import UpnpEventHandler
from async_upnp_client.exceptions import UpnpActionError, UpnpCommunicationError, UpnpError
from async_upnp_client.profiles.dlna import dlna_handle_notify_last_change

from .const import (
    AVTRANSPORT_SERVICE,
    MEMBER_ID_PREFIX,
    RENDERING_SERVICE,
    SOURCE_LINE_IN,
    SUBSCRIBED_SERVICES,
    SUBSCRIPTION_TIMEOUT,
    ZONE_SERVICE,
)
from .didl import LINE_IN_CONTAINER_METADATA, line_in_track_metadata, line_in_uri, parse_metadata
from .exceptions import NuvoActionError, NuvoConnectionError, NuvoError
from .models import Source, ZoneState, group_id
from .safety import check_action, check_post_url, is_read_only

if TYPE_CHECKING:
    from .system import NuvoSystem

_LOGGER = logging.getLogger(__name__)

_CONNECTION_ERRORS = (UpnpCommunicationError, aiohttp.ClientError, asyncio.TimeoutError, OSError)

Listener = Callable[["NuvoZone"], None]

# A command that hits a restarting zone waits this long for it to reappear.
COMMAND_RETRY_DELAYS = (3.0,)


class NuvoZone:
    """A single zone. Keyed by member ID; its LOCATION may change at any time."""

    def __init__(
        self,
        device: UpnpDevice,
        factory: UpnpFactory,
        *,
        locate: Callable[[NuvoZone], Awaitable[str | None]] | None = None,
        system: NuvoSystem | None = None,
    ) -> None:
        self._device = device
        self._factory = factory
        self._locate = locate
        self._system = system
        self._lock = asyncio.Lock()
        self._recover_lock = asyncio.Lock()
        self._listeners: list[Listener] = []
        self._event_handler: UpnpEventHandler | None = None
        self.state = ZoneState()
        self.available = True
        self.subscribed = False
        self.renew_interval = SUBSCRIPTION_TIMEOUT / 2
        self.last_renewed = 0.0
        self.last_polled = 0.0

    def __repr__(self) -> str:
        return f"<NuvoZone {self.name!r} {self.member_id} {self.location}>"

    # --- identity -------------------------------------------------------

    @property
    def udn(self) -> str:
        return self._device.udn

    @property
    def mac(self) -> str:
        """Lowercase MAC without separators, taken from the UDN."""
        return self.udn.rsplit("-", 1)[-1].lower()

    @property
    def member_id(self) -> str:
        return MEMBER_ID_PREFIX + self.mac

    @property
    def location(self) -> str:
        return self._device.device_url

    @property
    def host(self) -> str:
        return urlsplit(self.location).hostname or ""

    @property
    def name(self) -> str:
        return self.state.title or self._device.friendly_name

    # --- derived state --------------------------------------------------

    @property
    def volume_range(self) -> tuple[int, int]:
        var = self._service(RENDERING_SERVICE).state_variable("Volume")
        return int(var.min_value or 0), int(var.max_value or 100)

    @property
    def volume_level(self) -> float | None:
        """Volume as 0..1, mapped from the device range (0..100 on a P4300)."""
        if self.state.volume_raw is None:
            return None
        lo, hi = self.volume_range
        return (self.state.volume_raw - lo) / (hi - lo)

    @property
    def sources(self) -> list[Source]:
        return [Source(SOURCE_LINE_IN, "Line In")]

    @property
    def source(self) -> str | None:
        """Key of this zone's own source, or None if off, joined to another zone, or unknown."""
        if not self.state.is_on or self.master is not self:
            return None
        if self.state.current_uri == line_in_uri(self.member_id):
            return SOURCE_LINE_IN
        return None

    # Group model (docs/protocol.md, "Grouping"): every playing zone masters its own
    # group (MasterGroup) and listens to exactly one group (MemberGroup). A zone that
    # joined another is heard through that zone's AVTransport, not its own.

    @property
    def master(self) -> NuvoZone | None:
        """The zone whose group this zone listens to (possibly itself); None if off."""
        gid = self.state.member_group
        if not gid:
            return None
        if self.state.master_group == gid or self._system is None:
            return self
        return next((z for z in self._system.zones.values() if z.state.master_group == gid), None)

    @property
    def playback_zone(self) -> NuvoZone:
        """Zone whose transport and metadata describe what this zone plays."""
        return self.master or self

    @property
    def playback_state(self) -> str:
        """"off", or the group master's AVTransport state (PLAYING, NO_MEDIA_PRESENT, ...)."""
        if not self.state.is_on:
            return "off"
        return self.playback_zone.state.transport_state or "NO_MEDIA_PRESENT"

    @property
    def is_joined(self) -> bool:
        """True if this zone listens to another zone's group."""
        return self.state.is_on and self.state.member_group != self.state.master_group

    @property
    def group_members(self) -> list[NuvoZone]:
        """All zones listening to this zone's group, master first; [] when off."""
        if not self.state.member_group:
            return []
        if self._system is None:
            return [self]
        gid = self.state.member_group
        members = [z for z in self._system.zones.values() if z.state.member_group == gid]
        return sorted(members, key=lambda z: z.state.master_group != gid)

    # --- listeners ------------------------------------------------------

    def subscribe(self, callback: Listener) -> Callable[[], None]:
        """Call `callback(zone)` on every state or availability change."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self)
            except Exception:  # noqa: BLE001 - never let a listener break us
                _LOGGER.exception("Listener raised")

    def _set_available(self, available: bool) -> None:
        if available != self.available:
            self.available = available
            _LOGGER.info("%s is %s", self.name, "available" if available else "unavailable")
            self._notify()

    # --- SOAP -----------------------------------------------------------

    def _service(self, service_type: str) -> UpnpService:
        service = self._device.find_service(service_type)
        if service is None:
            raise NuvoError(f"{self.name} has no {service_type}")
        return service

    async def _call(self, service_type: str, action: str, **args: Any) -> dict[str, Any]:
        """Call an action, rediscovering the zone and retrying once on connection errors."""
        check_action(action)
        for attempt in (1, 2):
            service = self._service(service_type)
            check_post_url(service.control_url)
            try:
                result = await service.action(action).async_call(**args)
            except UpnpActionError as err:
                raise NuvoActionError(action, err.error_code, err.error_desc) from err
            except _CONNECTION_ERRORS as err:
                _LOGGER.debug("%s.%s on %s failed: %r", service_type, action, self.name, err)
                if attempt == 2 or not await self.async_recover(COMMAND_RETRY_DELAYS):
                    self._set_available(False)
                    raise NuvoConnectionError(f"{self.name}: {action} failed: {err!r}") from err
                continue
            self._set_available(True)
            return result
        raise AssertionError("unreachable")

    async def _write(self, service_type: str, action: str, **args: Any) -> dict[str, Any]:
        assert not is_read_only(action)
        async with self._lock:
            return await self._call(service_type, action, **args)

    # --- recovery -------------------------------------------------------

    async def async_recover(self, retry_delays: Sequence[float] = ()) -> bool:
        """Re-find this zone by UDN, rebuild service proxies and re-subscribe.

        `retry_delays` adds further attempts; the zone's UPnP process can take a
        few seconds to come back after it restarts (docs/protocol.md).
        """
        if self._locate is None:
            return False
        async with self._recover_lock:
            for delay in (0, *retry_delays):
                if delay:
                    await asyncio.sleep(delay)
                location = await self._locate(self)
                if location and await self.async_relocate(location):
                    return True
            return False

    async def async_relocate(self, location: str) -> bool:
        """Point this zone at a new description LOCATION (e.g. after a reboot)."""
        try:
            device = await self._factory.async_create_device(location)
        except (UpnpError, *_CONNECTION_ERRORS) as err:
            _LOGGER.debug("Cannot load %s: %r", location, err)
            return False
        if device.udn != self.udn:
            _LOGGER.warning("%s now serves %s, expected %s", location, device.udn, self.udn)
            return False
        _LOGGER.info("%s moved from %s to %s", self.name, self.location, location)
        handler = self._event_handler
        if handler and self.subscribed:
            for service in self._subscribed_services():
                try:
                    await handler.async_unsubscribe(service)
                except (UpnpError, KeyError, *_CONNECTION_ERRORS):
                    pass
        self._device = device
        self.subscribed = False
        if handler:
            try:
                await self.async_subscribe_events(handler)
            except (UpnpError, *_CONNECTION_ERRORS) as err:
                _LOGGER.warning("Re-subscribe to %s failed: %r", self.name, err)
        self._set_available(True)
        return True

    # --- state ----------------------------------------------------------

    async def async_update(self) -> None:
        """Poll the full state."""
        z = await self._call(ZONE_SERVICE, "Get")
        rc = {"InstanceID": 0, "Channel": "Master"}
        vol = await self._call(RENDERING_SERVICE, "GetVolume", **rc)
        mute = await self._call(RENDERING_SERVICE, "GetMute", **rc)
        ti = await self._call(AVTRANSPORT_SERVICE, "GetTransportInfo", InstanceID=0)
        mi = await self._call(AVTRANSPORT_SERVICE, "GetMediaInfo", InstanceID=0)
        ta = await self._call(AVTRANSPORT_SERVICE, "GetCurrentTransportActions", InstanceID=0)
        self._apply(
            {
                **z,
                "Volume": vol["CurrentVolume"],
                "Mute": mute["CurrentMute"],
                "TransportState": ti["CurrentTransportState"],
                "AVTransportURI": mi["CurrentURI"],
                "AVTransportURIMetaData": mi["CurrentURIMetaData"],
                "CurrentTransportActions": ta["Actions"],
            }
        )
        self.last_polled = time.monotonic()
        self._notify()

    def _apply(self, values: dict[str, Any]) -> None:
        s = self.state
        simple = {
            "Title": "title",
            "Model": "model",
            "FirmwareVersion": "firmware",
            "SystemID": "system_id",
            "PowerState": "power_state",
            "AudioInput": "audio_input",
            "TransportState": "transport_state",
            "AVTransportURI": "current_uri",
        }
        for key, value in values.items():
            if key in simple:
                setattr(s, simple[key], value)
            elif key == "Volume":
                s.volume_raw = int(value)
            elif key == "Mute":
                s.muted = value in (True, 1, "1", "true", "TRUE", "True")
            elif key == "MemberGroup":
                s.member_group = group_id(value)
            elif key == "MasterGroup":
                s.master_group = group_id(value)
            elif key == "CurrentTransportActions":
                s.transport_actions = frozenset(a.strip() for a in (value or "").split(",") if a.strip())
            elif key == "AVTransportURIMetaData":
                meta = parse_metadata(value)
                s.media_title = meta["title"]
                s.media_artist = meta["artist"]
                s.media_album = meta["album"]
                s.media_image_url = meta["image_url"]

    # --- events ---------------------------------------------------------

    def _subscribed_services(self) -> list[UpnpService]:
        return [s for st in SUBSCRIBED_SERVICES if (s := self._device.find_service(st))]

    async def async_subscribe_events(self, handler: UpnpEventHandler) -> None:
        """Subscribe to Zone, AVTransport and RenderingControl events."""
        self._event_handler = handler
        granted = []
        for service in self._subscribed_services():
            service.on_event = self._on_event
            _sid, timeout = await handler.async_subscribe(
                service, timeout=timedelta(seconds=SUBSCRIPTION_TIMEOUT)
            )
            granted.append(timeout.total_seconds())
        self.renew_interval = max(10.0, min(granted, default=SUBSCRIPTION_TIMEOUT) / 2)
        self.last_renewed = time.monotonic()
        self.subscribed = True

    async def async_renew_events(self) -> None:
        """Renew subscriptions; raises on failure so the caller can recover."""
        assert self._event_handler is not None
        for service in self._subscribed_services():
            await self._event_handler.async_resubscribe(
                service, timeout=timedelta(seconds=SUBSCRIPTION_TIMEOUT)
            )
        self.last_renewed = time.monotonic()

    async def async_unsubscribe_events(self) -> None:
        if not (self._event_handler and self.subscribed):
            return
        for service in self._subscribed_services():
            try:
                await self._event_handler.async_unsubscribe(service)
            except (UpnpError, KeyError, *_CONNECTION_ERRORS):
                pass
        self.subscribed = False

    def _on_event(self, service: UpnpService, variables: Sequence[UpnpStateVariable]) -> None:
        for var in variables:
            if var.name == "LastChange":
                # Expands into a second on_event call carrying the changed variables.
                try:
                    dlna_handle_notify_last_change(var)
                except (UpnpError, ValueError) as err:
                    _LOGGER.debug("Bad LastChange from %s: %r", self.name, err)
        values = {v.name: v.value for v in variables if v.name != "LastChange"}
        if not values:
            return
        self._apply(values)
        self._set_available(True)
        self._notify()

    # --- commands -------------------------------------------------------

    async def set_volume(self, level: float) -> None:
        """Set volume from 0..1."""
        lo, hi = self.volume_range
        raw = round(lo + max(0.0, min(1.0, level)) * (hi - lo))
        await self.set_volume_raw(raw)

    async def set_volume_raw(self, raw: int) -> None:
        lo, hi = self.volume_range
        raw = max(lo, min(hi, int(raw)))
        await self._write(RENDERING_SERVICE, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=raw)
        self.state.volume_raw = raw

    async def volume_step(self, delta: int) -> None:
        """Adjust volume by `delta` device steps (X_NUVO_AdjustVolume)."""
        await self._write(
            RENDERING_SERVICE, "X_NUVO_AdjustVolume", InstanceID=0, Channel="Master", VolumeAdjustment=int(delta)
        )

    async def set_mute(self, muted: bool) -> None:
        await self._write(RENDERING_SERVICE, "SetMute", InstanceID=0, Channel="Master", DesiredMute=bool(muted))
        self.state.muted = bool(muted)

    async def _refresh_group(self) -> None:
        z = await self._call(ZONE_SERVICE, "Get")
        self._apply({"MemberGroup": z["MemberGroup"], "MasterGroup": z["MasterGroup"], "Title": z["Title"]})

    async def _leave_group(self) -> None:
        """Stop listening to another zone's group; this zone ends up off."""
        await self._call(ZONE_SERVICE, "GroupMemberSetGroup", memberIDs=json.dumps([self.member_id]), groupID="")
        self.state.member_group = ""
        self.state.master_group = ""

    async def select_source(self, source: str) -> None:
        """Select a source: leave any other zone's group, create our own if needed
        (GroupCreate), then X_NUVO_PlayContainerURI. Zones that joined us keep listening."""
        if source != SOURCE_LINE_IN:
            raise NuvoError(f"Unknown source {source!r}; known: {[s.key for s in self.sources]}")
        async with self._lock:
            await self._refresh_group()
            if self.is_joined:
                await self._leave_group()
            if not self.state.member_group:
                result = await self._call(ZONE_SERVICE, "GroupCreate", memberIDs=json.dumps([self.member_id]))
                self.state.member_group = result.get("groupID", "")
            await self._call(
                AVTRANSPORT_SERVICE,
                "X_NUVO_PlayContainerURI",
                InstanceID=0,
                CurrentURI="",
                CurrentURIMetaData=LINE_IN_CONTAINER_METADATA,
                TrackURI=line_in_uri(self.member_id),
                TrackURIMetaData=line_in_track_metadata(self.member_id, self.name),
                StartingIndex=1,
                UpdateID=-1,
            )

    async def turn_on(self) -> None:
        """Turn on to the zone's own Line In. No-op if already playing (own or joined group)."""
        await self.async_update()
        master = self.master
        if master is not None and master is not self and self._system is not None:
            await master.async_update()
        if self.playback_state == "PLAYING":
            return
        await self.select_source(SOURCE_LINE_IN)

    async def turn_off(self) -> None:
        """Turn the zone off.

        A zone joined to another zone's group just leaves it. A group master
        disbands its group, which the amp turns off for every member too
        (observed on hardware, docs/protocol.md).
        """
        async with self._lock:
            await self._refresh_group()
            if not self.state.member_group:
                return
            if self.is_joined:
                await self._leave_group()
                return
            await self._call(ZONE_SERVICE, "GroupDisband", groupID=self.state.member_group)
            self.state.member_group = ""
            self.state.master_group = ""

    async def play(self) -> None:
        await self._write(AVTRANSPORT_SERVICE, "Play", InstanceID=0, Speed="1")

    async def pause(self) -> None:
        await self._write(AVTRANSPORT_SERVICE, "Pause", InstanceID=0)

    async def stop(self) -> None:
        await self._write(AVTRANSPORT_SERVICE, "Stop", InstanceID=0)

    async def next(self) -> None:
        await self._write(AVTRANSPORT_SERVICE, "Next", InstanceID=0)

    async def previous(self) -> None:
        await self._write(AVTRANSPORT_SERVICE, "Previous", InstanceID=0)
