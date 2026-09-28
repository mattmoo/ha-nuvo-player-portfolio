from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuvo_player.const import CONF_CALLBACK_PORT, CONF_HOSTS, CONF_SYSTEM_ID, DOMAIN

FOUND = {"nuvoTEST": {"zones": ["Lounge", "Dining Room"], "hosts": ["10.0.0.53", "10.0.0.52"], "model": "p4300"}}
PROBE = "custom_components.nuvo_player.config_flow.async_probe"
SETUP = "custom_components.nuvo_player.async_setup_entry"


async def test_user_flow(hass):
    with patch(PROBE, AsyncMock(return_value=FOUND)) as probe, patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "10.0.0.53, 10.0.0.52"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Nuvo P4300"
    assert result["data"] == {CONF_SYSTEM_ID: "nuvoTEST", CONF_HOSTS: ["10.0.0.52", "10.0.0.53"]}
    assert result["result"].unique_id == "nuvoTEST"
    assert probe.await_args.args[1] == ["10.0.0.53", "10.0.0.52"]


async def test_user_flow_no_zones(hass):
    with patch(PROBE, AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: ""})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_zones"}


async def test_user_flow_already_configured(hass):
    MockConfigEntry(domain=DOMAIN, unique_id="nuvoTEST", data={}).add_to_hass(hass)
    with patch(PROBE, AsyncMock(return_value=FOUND)):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: ""})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _ssdp(host="10.0.0.53"):
    return SsdpServiceInfo(
        ssdp_usn="uuid:00000000-0000-0000-0000-0025ed1dd983::urn:schemas-nuvotechnologies-com:device:Zone:1",
        ssdp_st="urn:schemas-nuvotechnologies-com:device:Zone:1",
        ssdp_location=f"http://{host}:47644/00000000-0000-0000-0000-0025ed1dd983.xml",
        upnp={},
    )


async def test_ssdp_flow(hass):
    with patch(PROBE, AsyncMock(return_value=FOUND)), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp())
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
        assert result["description_placeholders"] == {"zones": "Dining Room, Lounge"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SYSTEM_ID] == "nuvoTEST"


async def test_ssdp_flow_second_zone_aborts(hass):
    MockConfigEntry(domain=DOMAIN, unique_id="nuvoTEST", data={}).add_to_hass(hass)
    with patch(PROBE, AsyncMock(return_value=FOUND)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp("10.0.0.52")
        )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_ssdp_flow_unreadable(hass):
    with patch(PROBE, AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp())
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "cannot_connect"


async def test_zeroconf_flow(hass):
    info = ZeroconfServiceInfo(
        ip_address=ip_address("10.0.0.53"),
        ip_addresses=[ip_address("10.0.0.53")],
        hostname="nuvo.local.",
        name="00000000-0000-0000-0001-0025ed1dd983._nuvoplayer._tcp.local.",
        port=4747,
        properties={"httpPort": "80"},
        type="_nuvoplayer._tcp.local.",
    )
    with patch(PROBE, AsyncMock(return_value=FOUND)):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"


async def test_options_flow(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="nuvoTEST", data={CONF_SYSTEM_ID: "nuvoTEST", CONF_HOSTS: ["10.0.0.52"]})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "network"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "network"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HOSTS: "10.0.0.52; 10.0.0.53", CONF_CALLBACK_PORT: 8096}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_HOSTS: ["10.0.0.52", "10.0.0.53"], CONF_CALLBACK_PORT: 8096}


async def test_line_in_feeds_need_loaded_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="nuvoTEST", data={CONF_SYSTEM_ID: "nuvoTEST", CONF_HOSTS: ["10.0.0.52"]})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "line_in_feeds"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_loaded"
