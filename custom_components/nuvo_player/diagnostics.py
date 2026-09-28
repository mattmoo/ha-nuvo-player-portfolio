"""Diagnostics: zone state with MACs, serials and system ID redacted."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NuvoConfigEntry
from .const import CONF_HOSTS, CONF_SYSTEM_ID

TO_REDACT = {CONF_SYSTEM_ID, "system_id", "serial", "mac", "member_id", "udn", "location", CONF_HOSTS, "host", "current_uri"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NuvoConfigEntry) -> dict[str, Any]:
    system = entry.runtime_data
    zones = []
    for zone in system.zones.values():
        state = dataclasses.asdict(zone.state)
        state["transport_actions"] = sorted(state["transport_actions"])
        zones.append(
            {
                "name": zone.name,
                "member_id": zone.member_id,
                "udn": zone.udn,
                "location": zone.location,
                "host": zone.host,
                "available": zone.available,
                "subscribed": zone.subscribed,
                "playback_state": zone.playback_state,
                "source": zone.source,
                "group_master": zone.master.name if zone.master else None,
                "group_members": [z.name for z in zone.group_members],
                "web_api": zone.web is not None,
                "state": state,
            }
        )
    return async_redact_data(
        {"entry": {"data": dict(entry.data), "options": dict(entry.options)}, "zones": zones}, TO_REDACT
    )
