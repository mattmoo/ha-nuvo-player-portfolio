"""Guard against destructive UPnP actions. See docs/safety.md."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from .exceptions import DeniedActionError

DENIED_ACTIONS = frozenset(
    {
        "RestoreFactoryDefaults",
        "SystemCreate",
        "SystemJoin",
        "SystemConfigureGWs",
    }
)

DENIED_PATHS = frozenset({"/firmware.fcgi", "/firmwareupdate.fcgi"})

# Web UI (port 80) paths that change state even over GET. Any method is refused.
DENIED_HTTP_PATHS = frozenset(
    {
        "/api/setdata",
        "/api/authenticate",
        "/update.fcgi",
        "/firmware.fcgi",
        "/firmwareupdate.fcgi",
        "/diagnostics_execute.fcgi",
        "/diagnostics_speedtest.fcgi",
        "/diagnostics_upload.fcgi",
    }
)

OVERRIDE_ENV = "NUVO_I_KNOW"


def _overridden() -> bool:
    return os.environ.get(OVERRIDE_ENV) == "1"


def is_read_only(action: str) -> bool:
    """True for actions that only read state (Get*, Browse, Search)."""
    return action.startswith("Get") or action in {"Browse", "Search", "ListPresets"}


def check_action(action: str) -> None:
    """Raise DeniedActionError if `action` is denylisted."""
    if action in DENIED_ACTIONS and not _overridden():
        raise DeniedActionError(
            f"{action} is denylisted (docs/safety.md); set {OVERRIDE_ENV}=1 to override"
        )


def check_post_url(url: str) -> None:
    """Raise DeniedActionError for POSTs to denylisted paths, e.g. /firmware.fcgi."""
    path = urlsplit(url).path.rstrip("/").lower()
    if path in DENIED_PATHS and not _overridden():
        raise DeniedActionError(
            f"POST to {path} is denylisted (docs/safety.md); set {OVERRIDE_ENV}=1 to override"
        )


# The only nSDK settings that may be written (docs/safety.md, tone-control exception).
ALLOWED_SETDATA_PATHS = frozenset(
    {
        "settings://mediaPlayer/bass",
        "settings://mediaPlayer/treble",
        "settings://mediaPlayer/balance",
    }
)
READ_ONLY_WEB_PATHS = frozenset({"/api/getdata", "/api/getrows"})


def check_web_request(path: str, params: dict[str, str]) -> None:
    """Allow nSDK reads, the login, and setData on ALLOWED_SETDATA_PATHS only."""
    p = path.rstrip("/").lower()
    if p in READ_ONLY_WEB_PATHS or p == "/api/authenticate":
        return
    if p == "/api/setdata" and params.get("path") in ALLOWED_SETDATA_PATHS:
        return
    raise DeniedActionError(f"web request {path} {params.get('path', '')} is not allowlisted (docs/safety.md)")


def check_http_url(url: str) -> None:
    """Raise DeniedActionError for any request to a state-changing web UI path."""
    path = urlsplit(url).path.rstrip("/").lower()
    if path in DENIED_HTTP_PATHS and not _overridden():
        raise DeniedActionError(
            f"{path} is denylisted (docs/safety.md); set {OVERRIDE_ENV}=1 to override"
        )
