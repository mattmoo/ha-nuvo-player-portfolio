"""Legrand Nuvo Player Portfolio (P-series) integration."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components import ssdp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .aionuvo import NuvoConnectionError, NuvoError, NuvoSystem
from .aionuvo.const import ZONE_DEVICE_TYPE
from .const import CONF_CALLBACK_PORT, CONF_HOSTS, CONF_SYSTEM_ID, DEFAULT_CALLBACK_PORT, HEARTBEAT_INTERVAL

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SWITCH]

type NuvoConfigEntry = ConfigEntry[NuvoSystem]


def create_system(hass: HomeAssistant, entry: NuvoConfigEntry) -> NuvoSystem:
    """Build the NuvoSystem for an entry (patched in tests)."""
    return NuvoSystem(
        session=async_get_clientsession(hass),
        hosts=entry.options.get(CONF_HOSTS, entry.data.get(CONF_HOSTS, [])),
        callback_port=entry.options.get(CONF_CALLBACK_PORT, DEFAULT_CALLBACK_PORT),
        system_id=entry.data[CONF_SYSTEM_ID],
    )


async def async_setup_entry(hass: HomeAssistant, entry: NuvoConfigEntry) -> bool:
    system = create_system(hass, entry)

    # Locations HA's own SSDP scanner already knows save a search round-trip.
    known = {
        info.ssdp_udn: info.ssdp_location
        for info in await ssdp.async_get_discovery_info_by_st(hass, ZONE_DEVICE_TYPE)
        if info.ssdp_udn and info.ssdp_location
    }
    try:
        # HA's SSDP scanner feeds location changes below, so no second listener.
        await system.async_start(known, watch=False)
    except NuvoConnectionError as err:
        await system.async_stop()
        raise ConfigEntryNotReady(f"No Nuvo zones reachable: {err}") from err
    entry.runtime_data = system

    @callback
    def _ssdp_seen(info: SsdpServiceInfo, change: ssdp.SsdpChange) -> None:
        if change != ssdp.SsdpChange.BYEBYE and info.ssdp_udn and info.ssdp_location:
            system.async_location_seen(info.ssdp_udn, info.ssdp_location)

    entry.async_on_unload(
        await ssdp.async_register_callback(hass, _ssdp_seen, {"deviceType": ZONE_DEVICE_TYPE})
    )

    async def _heartbeat(_now: datetime) -> None:
        for zone in list(system.zones.values()):
            try:
                await zone.async_update()
            except NuvoError as err:
                _LOGGER.debug("Heartbeat for %s failed: %s", zone.name, err)

    entry.async_on_unload(async_track_time_interval(hass, _heartbeat, HEARTBEAT_INTERVAL))
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: NuvoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NuvoConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
