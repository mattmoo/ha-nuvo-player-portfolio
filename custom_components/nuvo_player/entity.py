"""Base entity: one HA device per Nuvo zone."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity

from .aionuvo import NuvoSystem, NuvoZone
from .const import DOMAIN


class NuvoEntity(Entity):
    """Pushes state on any zone change (group members depend on other zones' state)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, system: NuvoSystem, zone: NuvoZone) -> None:
        self.system = system
        self.zone = zone
        mac = ":".join(zone.mac[i : i + 2] for i in range(0, 12, 2))
        # No suggested_area: areas are the user's to assign per device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zone.member_id)},
            connections={(CONNECTION_NETWORK_MAC, format_mac(mac))},
            name=zone.name,
            manufacturer="Legrand Nuvo",
            model=(zone.state.model or "").upper() or None,
            sw_version=zone.state.firmware,
            configuration_url=f"http://{zone.host}/",
        )

    @property
    def available(self) -> bool:
        return self.zone.available

    async def async_added_to_hass(self) -> None:
        for zone in self.system.zones.values():
            self.async_on_remove(zone.subscribe(self._zone_changed))

    @callback
    def _zone_changed(self, _zone: NuvoZone) -> None:
        self.async_write_ha_state()
