"""Config flow for Nuvo Player Portfolio."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .aionuvo import async_probe
from .const import CONF_CALLBACK_PORT, CONF_HOSTS, CONF_SYSTEM_ID, DEFAULT_CALLBACK_PORT, DOMAIN


def _split_hosts(value: str) -> list[str]:
    return [h.strip() for h in value.replace(";", ",").split(",") if h.strip()]


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
        if user_input is not None:
            hosts = _split_hosts(user_input.get(CONF_HOST, ""))
            found = await self._probe(hosts)
            configured = {e.unique_id for e in self._async_current_entries()}
            new = {sid: info for sid, info in found.items() if sid not in configured}
            if not found:
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
            data_schema=vol.Schema({vol.Optional(CONF_HOST, default=""): str}),
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
    """Zone IPs for unicast discovery, and the event callback port."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_HOSTS: _split_hosts(user_input[CONF_HOSTS]),
                    CONF_CALLBACK_PORT: user_input[CONF_CALLBACK_PORT],
                }
            )
        hosts = entry.options.get(CONF_HOSTS, entry.data.get(CONF_HOSTS, []))
        port = entry.options.get(CONF_CALLBACK_PORT, DEFAULT_CALLBACK_PORT)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_HOSTS, default=", ".join(hosts)): str,
                    vol.Required(CONF_CALLBACK_PORT, default=port): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=65535)
                    ),
                }
            ),
        )
