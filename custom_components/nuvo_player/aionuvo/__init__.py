"""Async client for Legrand Nuvo Player Portfolio zones (UPnP/SOAP)."""

from .const import SOURCE_LINE_IN, ZONE_DEVICE_TYPE
from .discovery import DiscoveredZone, async_discover
from .exceptions import DeniedActionError, NuvoActionError, NuvoConnectionError, NuvoError
from .models import Source, ZoneState
from .system import NuvoSystem, async_probe
from .zone import NuvoZone

__all__ = [
    "SOURCE_LINE_IN",
    "ZONE_DEVICE_TYPE",
    "DeniedActionError",
    "DiscoveredZone",
    "NuvoActionError",
    "NuvoConnectionError",
    "NuvoError",
    "NuvoSystem",
    "NuvoZone",
    "Source",
    "ZoneState",
    "async_discover",
    "async_probe",
]

__version__ = "0.1.0"
