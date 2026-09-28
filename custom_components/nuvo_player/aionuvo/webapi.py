"""Tone controls via the zone's web UI JSON API (StreamUnlimited nSDK, port 80).

This API writes through plain GET requests, so everything outside the
allowlist in aionuvo.safety stays denied. Login is the zone's serial number,
which the zone publishes in its UPnP description.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import aiohttp

from .exceptions import NuvoConnectionError, NuvoError
from .safety import check_web_request

_LOGGER = logging.getLogger(__name__)

TONE_PATHS = {
    "bass": "settings://mediaPlayer/bass",
    "treble": "settings://mediaPlayer/treble",
    "balance": "settings://mediaPlayer/balance",
}
# Whole steps; the device clamps to these (measured on a P4300, docs/protocol.md).
TONE_RANGES = {"bass": (-6, 6), "treble": (-6, 6), "balance": (-18, 18)}


class NuvoWebApi:
    """Minimal, allowlisted client for one zone's nSDK settings."""

    def __init__(self, session: aiohttp.ClientSession, host: str, serial: str, port: int = 80) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"
        self._cookie = base64.b64encode(serial.encode()).decode()
        self._authenticated = False

    async def _request(self, method: str, path: str, params: dict[str, str] | None = None, data: str | None = None):
        check_web_request(path, params or {})
        try:
            async with self._session.request(
                method,
                self._base + path,
                params=params,
                data=data,
                headers={"Cookie": f"Authentication={self._cookie}"},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                return resp.status, await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NuvoConnectionError(f"{self._base}{path}: {err!r}") from err

    async def _authenticate(self) -> None:
        status, _ = await self._request(
            "POST", "/api/authenticate", data=json.dumps({"serialNumber": self._cookie})
        )
        if status != 200:
            raise NuvoError(f"{self._base}: web API login refused ({status})")
        self._authenticated = True

    async def _api(self, path: str, params: dict[str, str]) -> Any:
        if not self._authenticated:
            await self._authenticate()
        status, text = await self._request("GET", path, params)
        if status in (302, 401, 403):
            await self._authenticate()
            status, text = await self._request("GET", path, params)
        if status != 200:
            raise NuvoError(f"{self._base}{path} {params}: HTTP {status} {text[:200]}")
        return json.loads(text) if text.strip() else None

    async def get_tone(self, key: str) -> float:
        result = await self._api("/api/getData", {"path": TONE_PATHS[key], "roles": "value"})
        try:
            return float(result[0]["double_"])
        except (TypeError, KeyError, IndexError, ValueError) as err:
            raise NuvoError(f"Unexpected {key} value {result!r}") from err

    async def set_tone(self, key: str, value: float) -> int:
        """Set a tone control; returns the value sent (rounded and clamped)."""
        lo, hi = TONE_RANGES[key]
        value = max(lo, min(hi, round(value)))
        value_json = json.dumps({"type": "double_", "double_": float(value)}, separators=(",", ":"))
        await self._api("/api/setData", {"path": TONE_PATHS[key], "roles": "value", "value": value_json})
        return value
