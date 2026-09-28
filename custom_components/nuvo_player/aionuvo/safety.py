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


def check_http_url(url: str) -> None:
    """Raise DeniedActionError for any request to a state-changing web UI path."""
    path = urlsplit(url).path.rstrip("/").lower()
    if path in DENIED_HTTP_PATHS and not _overridden():
        raise DeniedActionError(
            f"{path} is denylisted (docs/safety.md); set {OVERRIDE_ENV}=1 to override"
        )
