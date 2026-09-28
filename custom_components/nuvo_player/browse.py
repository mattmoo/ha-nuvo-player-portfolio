"""TuneIn in HA's media browser, served from a zone's own ContentDirectory.

Media content IDs carry the path the zone needs to play a station: its parent
container's element comes only from the grandparent's listing (BrowseMetadata
on a TuneIn container fails), so a station is `tunein|<grandparent>|<parent>|<station>`
and a container is `tunein|<parent>|<container>`. Object IDs never contain "|".
"""

from __future__ import annotations

import time

from homeassistant.components.media_player import BrowseError, BrowseMedia, MediaClass, MediaType

from .aionuvo import TUNEIN_ROOT, DidlEntry, NuvoError, NuvoZone

PREFIX = "tunein"
SEP = "|"
# Listings are reused this long, so playing from the browser does not re-browse.
# Short, because a listing's order (and so an item's StartingIndex) can change.
CACHE_TTL = 120.0
# The zone lists these, but playing from them is untested (PLAN.md, TuneIn).
HIDDEN_TITLES = frozenset({"Podcasts"})


def content_id(*object_ids: str) -> str:
    return SEP.join((PREFIX, *object_ids))


def is_tunein_id(media_id: str) -> bool:
    return media_id == PREFIX or media_id.startswith(PREFIX + SEP)


class TuneInBrowser:
    """Browses and resolves TuneIn content IDs through one zone."""

    def __init__(self, zone: NuvoZone) -> None:
        self.zone = zone
        self._cache: dict[str, tuple[float, list[DidlEntry]]] = {}

    async def _listing(self, object_id: str) -> list[DidlEntry]:
        hit = self._cache.get(object_id)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        entries = await self.zone.browse_all(object_id)
        self._cache[object_id] = (time.monotonic(), entries)
        return entries

    async def _find(self, parent_id: str, object_id: str) -> DidlEntry:
        entry = next((e for e in await self._listing(parent_id) if e.id == object_id), None)
        if entry is None:
            # Maybe the listing changed since it was cached.
            self._cache.pop(parent_id, None)
            entry = next((e for e in await self._listing(parent_id) if e.id == object_id), None)
        if entry is None:
            raise NuvoError(f"{object_id!r} is no longer listed under {parent_id!r}")
        return entry

    async def async_browse(self, media_id: str | None) -> BrowseMedia:
        parts = (media_id or PREFIX).split(SEP)
        if parts[0] != PREFIX or len(parts) not in (1, 3):
            raise BrowseError(f"Unknown media ID {media_id!r}")
        try:
            if len(parts) == 1:
                object_id, title, thumbnail = TUNEIN_ROOT, "TuneIn", None
            else:
                entry = await self._find(parts[1], parts[2])
                object_id, title, thumbnail = entry.id, entry.title, entry.icon
            children = await self._listing(object_id)
        except NuvoError as err:
            raise BrowseError(f"{self.zone.name}: {err}") from err
        parent = parts[1] if len(parts) == 3 else ""
        items = [c for e in children if (c := self._child(parent, object_id, e))]
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=content_id() if len(parts) == 1 else content_id(parent, object_id),
            media_content_type=MediaType.CHANNELS,
            title=title,
            can_play=False,
            can_expand=True,
            thumbnail=thumbnail,
            children=items,
            # Tells the frontend how to draw children without a thumbnail.
            children_media_class=MediaClass.CHANNEL
            if any(c.can_play for c in items)
            else MediaClass.DIRECTORY,
        )

    @staticmethod
    def _child(grandparent: str, parent: str, entry: DidlEntry) -> BrowseMedia | None:
        if entry.kind == "container" and entry.id and entry.title not in HIDDEN_TITLES:
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=content_id(parent, entry.id),
                media_content_type=MediaType.CHANNELS,
                title=entry.title,
                can_play=False,
                can_expand=True,
                thumbnail=entry.icon,
            )
        if entry.playable:
            return BrowseMedia(
                media_class=MediaClass.CHANNEL,
                media_content_id=content_id(grandparent, parent, entry.id),
                media_content_type=MediaType.CHANNEL,
                title=entry.title,
                can_play=True,
                can_expand=False,
                thumbnail=entry.icon,
            )
        return None  # section headers, "No Favorites available", ...

    async def async_resolve(self, media_id: str) -> tuple[DidlEntry, DidlEntry]:
        """(parent container, station) for a station's content ID."""
        parts = media_id.split(SEP)
        if parts[0] != PREFIX or len(parts) != 4:
            raise NuvoError(f"{media_id!r} is not a TuneIn station")
        _, grandparent, parent, station = parts
        return await self._find(grandparent, parent), await self._find(parent, station)
