"""Bass, treble and balance per zone (via the zone's web API; see docs/safety.md)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NuvoConfigEntry
from .aionuvo import NuvoError, NuvoSystem, NuvoZone
from .aionuvo.webapi import TONE_RANGES
from .entity import NuvoEntity

# Tone settings are not evented; changes made in the Nuvo app show up within this.
SCAN_INTERVAL = timedelta(minutes=5)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: NuvoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    system = entry.runtime_data

    def _entities(zone: NuvoZone) -> list[NuvoToneNumber]:
        if zone.web is None:
            return []
        return [NuvoToneNumber(system, zone, key) for key in TONE_RANGES]

    async_add_entities([e for z in system.zones.values() for e in _entities(z)], update_before_add=True)

    @callback
    def _added(zone: NuvoZone) -> None:
        async_add_entities(_entities(zone), update_before_add=True)

    entry.async_on_unload(system.on_zone_added(_added))


class NuvoToneNumber(NuvoEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_should_poll = True

    def __init__(self, system: NuvoSystem, zone: NuvoZone, key: str) -> None:
        super().__init__(system, zone)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{zone.member_id}_{key}"
        self._attr_native_min_value, self._attr_native_max_value = TONE_RANGES[key]

    async def async_added_to_hass(self) -> None:
        # Tone changes only come from this entity or from polling; no need to
        # redraw on every zone event.
        pass

    @property
    def native_value(self) -> float | None:
        return self.zone.state.tone.get(self._key)

    async def async_update(self) -> None:
        try:
            await self.zone.async_update_tone([self._key])
        except NuvoError:
            self.zone.state.tone.pop(self._key, None)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.zone.set_tone(self._key, value)
        except NuvoError as err:
            raise HomeAssistantError(f"{self.zone.name}: {err}") from err
        self.async_write_ha_state()
