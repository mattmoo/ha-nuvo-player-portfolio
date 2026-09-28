"""Loudness per zone (UPnP RenderingControl; pushed via LastChange events)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NuvoConfigEntry
from .aionuvo import NuvoError, NuvoSystem, NuvoZone
from .entity import NuvoEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: NuvoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    system = entry.runtime_data
    for zone in system.zones.values():
        try:
            await zone.async_update_loudness()
        except NuvoError:
            pass
    async_add_entities(NuvoLoudnessSwitch(system, z) for z in system.zones.values())

    @callback
    def _added(zone: NuvoZone) -> None:
        async_add_entities([NuvoLoudnessSwitch(system, zone)])

    entry.async_on_unload(system.on_zone_added(_added))


class NuvoLoudnessSwitch(NuvoEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "loudness"

    def __init__(self, system: NuvoSystem, zone: NuvoZone) -> None:
        super().__init__(system, zone)
        self._attr_unique_id = f"{zone.member_id}_loudness"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.zone.subscribe(self._zone_changed))

    @property
    def is_on(self) -> bool | None:
        return self.zone.state.loudness

    async def _set(self, on: bool) -> None:
        try:
            await self.zone.set_loudness(on)
        except NuvoError as err:
            raise HomeAssistantError(f"{self.zone.name}: {err}") from err
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
