"""Config flow for Nuvo Player Portfolio."""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .aionuvo import async_probe
from .const import (
    CONF_CALLBACK_PORT,
    CONF_HOSTS,
    CONF_LINE_IN_FEEDS,
    CONF_SYSTEM_ID,
    DEFAULT_CALLBACK_PORT,
    DOMAIN,
)


_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.?$", re.I)

# One text box per zone address, with add/remove buttons.
HOSTS_SELECTOR = TextSelector(TextSelectorConfig(multiple=True))


def _valid_host(host: str) -> bool:
    try:
        ip_address(host)
    except ValueError:
        # "10.0.0.5x3" is a legal hostname, but really a mistyped IPv4 address.
        looks_ipv4 = all(label.isdigit() for label in host.split(".")[:3])
        return bool(_HOSTNAME.match(host)) and not looks_ipv4
    return True


def _clean_hosts(values: list[str] | str | None) -> tuple[list[str], bool]:
    """(unique hosts in order, all valid). A row may still hold "a, b" pasted in."""
    if isinstance(values, str):
        values = [values]
    hosts: list[str] = []
    for value in values or []:
        for host in re.split(r"[,;\s]+", value):
            if host and host not in hosts:
                hosts.append(host)
    return hosts, all(_valid_host(h) for h in hosts)


class NuvoConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per Nuvo system (keyed by SystemID); each zone becomes a device."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, dict[str, Any]] = {}
        self._system_id: str | None = None

    async def _probe(self, hosts: list[str], multicast: bool = True) -> dict[str, dict[str, Any]]:
        return await async_probe(async_get_clientsession(self.hass), hosts, multicast=multicast)

    async def _discovered(self, host: str) -> ConfigFlowResult:
        found = await self._probe([host])
        system_id = next((sid for sid, info in found.items() if host in info["hosts"]), None)
        if system_id is None:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(system_id)
        self._abort_if_unique_id_configured()
        self._found, self._system_id = found, system_id
        info = found[system_id]
        self.context["title_placeholders"] = {"name": f"Nuvo {str(info['model'] or '').upper()}".strip()}
        return await self.async_step_confirm()

    async def async_step_ssdp(self, discovery_info: SsdpServiceInfo) -> ConfigFlowResult:
        host = urlsplit(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="cannot_connect")
        return await self._discovered(host)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        return await self._discovered(str(discovery_info.ip_address))

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._system_id is not None
        info = self._found[self._system_id]
        if user_input is not None:
            return self._create(self._system_id, info)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"zones": ", ".join(sorted(info["zones"]))},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        hosts: list[str] = []
        if user_input is not None:
            hosts, valid = _clean_hosts(user_input.get(CONF_HOSTS))
            found = await self._probe(hosts) if valid else {}
            configured = {e.unique_id for e in self._async_current_entries()}
            new = {sid: info for sid, info in found.items() if sid not in configured}
            if not valid:
                errors[CONF_HOSTS] = "invalid_host"
            elif not found:
                errors["base"] = "no_zones"
            elif not new:
                return self.async_abort(reason="already_configured")
            else:
                system_id, info = next(iter(new.items()))
                await self.async_set_unique_id(system_id)
                self._abort_if_unique_id_configured()
                return self._create(system_id, info)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Optional(CONF_HOSTS): HOSTS_SELECTOR}), {CONF_HOSTS: hosts}
            ),
            errors=errors,
        )

    def _create(self, system_id: str, info: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"Nuvo {str(info['model'] or '').upper()}".strip(),
            data={CONF_SYSTEM_ID: system_id, CONF_HOSTS: sorted(set(info["hosts"]))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NuvoOptionsFlow()


class NuvoOptionsFlow(OptionsFlow):
    """Network settings, and which player feeds each zone's Line In."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["line_in_feeds", "network"])

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_line_in_feeds(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Per zone, the player (e.g. a Chromecast Audio) wired to its Line In.

        When that player starts playing, the zone switches to Line In.
        """
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        zones = sorted(entry.runtime_data.zones.values(), key=lambda z: z.name)
        # Field keys are shown as labels, so use zone names (made unique) and map back.
        fields: dict[str, str] = {}
        for zone in zones:
            key = zone.name if zone.name not in fields else f"{zone.name} ({zone.member_id})"
            fields[key] = zone.member_id
        if user_input is not None:
            feeds = {fields[k]: v for k, v in user_input.items() if k in fields and v}
            return self._save(**{CONF_LINE_IN_FEEDS: feeds})
        current = entry.options.get(CONF_LINE_IN_FEEDS, {})
        ours = er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
        selector = EntitySelector(
            EntitySelectorConfig(domain="media_player", exclude_entities=[e.entity_id for e in ours])
        )
        return self.async_show_form(
            step_id="line_in_feeds",
            data_schema=vol.Schema(
                {
                    vol.Optional(key, description={"suggested_value": current.get(member_id)}): selector
                    for key, member_id in fields.items()
                }
            ),
        )

    async def async_step_network(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Zone IPs for unicast discovery, and the event callback port."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        hosts = entry.options.get(CONF_HOSTS, entry.data.get(CONF_HOSTS, []))
        port = entry.options.get(CONF_CALLBACK_PORT, DEFAULT_CALLBACK_PORT)
        if user_input is not None:
            hosts, valid = _clean_hosts(user_input.get(CONF_HOSTS))
            port = user_input[CONF_CALLBACK_PORT]
            if valid:
                return self._save(**{CONF_HOSTS: hosts, CONF_CALLBACK_PORT: port})
            errors[CONF_HOSTS] = "invalid_host"
        return self.async_show_form(
            step_id="network",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_HOSTS): HOSTS_SELECTOR,
                        vol.Required(CONF_CALLBACK_PORT): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
                    }
                ),
                {CONF_HOSTS: hosts, CONF_CALLBACK_PORT: port},
            ),
            errors=errors,
        )
