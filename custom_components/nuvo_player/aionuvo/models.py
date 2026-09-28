"""Data models for aionuvo."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    """A selectable input. `key` is stable; `label` is for display."""

    key: str
    label: str


@dataclass
class ZoneState:
    """Last known state of one zone. Updated by polling and by GENA events."""

    title: str | None = None
    model: str | None = None
    firmware: str | None = None
    system_id: str | None = None
    volume_raw: int | None = None
    muted: bool | None = None
    member_group: str = ""
    master_group: str = ""
    power_state: str | None = None
    audio_input: str | None = None
    transport_state: str | None = None
    transport_actions: frozenset[str] = field(default_factory=frozenset)
    current_uri: str | None = None
    media_title: str | None = None
    media_artist: str | None = None
    media_album: str | None = None
    media_image_url: str | None = None
    loudness: bool | None = None
    tone: dict[str, float] = field(default_factory=dict)

    @property
    def is_on(self) -> bool:
        """A zone plays only while it belongs to a group (docs/protocol.md, H4)."""
        return bool(self.member_group)


def group_id(value: str | None) -> str:
    """Extract the id from a MemberGroup/MasterGroup JSON value; "" if none."""
    if not value:
        return ""
    try:
        return json.loads(value).get("id") or ""
    except (ValueError, AttributeError):
        return ""
