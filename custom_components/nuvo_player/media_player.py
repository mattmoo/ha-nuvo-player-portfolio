"""Media player per Nuvo zone: power, volume, Line In, TuneIn, HTTP streams and grouping."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_EXTRA,
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.const import STATE_PLAYING, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import slugify

from . import NuvoConfigEntry
from .aionuvo import SOURCE_STREAM, NuvoError, NuvoSystem, NuvoZone
from .browse import TuneInBrowser, is_tunein_id
from .const import CONF_LINE_IN_FEEDS, DOMAIN, ENTITY_ID_PREFIX, SOURCE_LABELS
from .entity import NuvoEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_STATES = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED_PLAYBACK": MediaPlayerState.PAUSED,
    "TRANSITIONING": MediaPlayerState.BUFFERING,
}

_BASE_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.GROUPING
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.STOP
)
# Pause and resume are verified on HTTP streams only (docs/protocol.md).
_STREAM_FEATURES = MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.PLAY


async def async_setup_entry(
    hass: HomeAssistant, entry: NuvoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    system = entry.runtime_data
    feeds: dict[str, str] = entry.options.get(CONF_LINE_IN_FEEDS, {})
    async_add_entities(NuvoMediaPlayer(system, z, feeds.get(z.member_id)) for z in system.zones.values())

    @callback
    def _added(zone: NuvoZone) -> None:
        async_add_entities([NuvoMediaPlayer(system, zone, feeds.get(zone.member_id))])

    entry.async_on_unload(system.on_zone_added(_added))


class NuvoMediaPlayer(NuvoEntity, MediaPlayerEntity):
    _attr_name = None  # the device (zone) name
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, system: NuvoSystem, zone: NuvoZone, line_in_feed: str | None = None) -> None:
        super().__init__(system, zone)
        self._attr_unique_id = zone.member_id
        # Only a suggestion: HA keeps the ID of an entity that is already registered.
        self.entity_id = f"media_player.{ENTITY_ID_PREFIX}_{slugify(zone.name)}"
        self._browser = TuneInBrowser(zone)
        self._line_in_feed = line_in_feed

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._line_in_feed:
            self.async_on_remove(
                async_track_state_change_event(self.hass, [self._line_in_feed], self._feed_changed)
            )

    @callback
    def _feed_changed(self, event: Event[EventStateChangedData]) -> None:
        """The player wired to this zone's Line In started: switch the zone to Line In.

        Only a real start counts, not the player (re)appearing at startup or after
        a dropout, so a zone someone moved to TuneIn is not grabbed back.
        """
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or old is None or new.state != STATE_PLAYING:
            return
        if old.state in (STATE_PLAYING, STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        self.hass.async_create_task(self._switch_to_line_in(), eager_start=True)

    async def _switch_to_line_in(self) -> None:
        try:
            await self.zone.turn_on()
        except NuvoError as err:
            _LOGGER.warning("%s: could not switch to Line In for %s: %s", self.zone.name, self._line_in_feed, err)

    # --- state ------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        playback = self.zone.playback_state
        if playback == "off":
            return MediaPlayerState.OFF
        return _STATES.get(playback, MediaPlayerState.IDLE)

    @property
    def _on_stream(self) -> bool:
        """True while this zone hears an HTTP stream (its own or its group master's)."""
        return self.zone.state.is_on and self.zone.playback_zone.source == SOURCE_STREAM

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        return _BASE_FEATURES | _STREAM_FEATURES if self._on_stream else _BASE_FEATURES

    @property
    def volume_level(self) -> float | None:
        return self.zone.volume_level

    @property
    def is_volume_muted(self) -> bool | None:
        return self.zone.state.muted

    @property
    def source_list(self) -> list[str]:
        return [SOURCE_LABELS.get(s.key, s.label) for s in self.zone.sources]

    @property
    def source(self) -> str | None:
        key = self.zone.source
        return SOURCE_LABELS.get(key, key) if key else None

    @property
    def media_content_id(self) -> str | None:
        """The stream URL; Music Assistant recognises its own playback by it."""
        return self.zone.playback_zone.state.stream_url if self._on_stream else None

    @property
    def media_title(self) -> str | None:
        zone = self.zone.playback_zone
        if not self.zone.state.is_on:
            return None
        title = zone.state.media_title
        if zone is not self.zone and title:
            return f"{title} ({zone.name})"
        return title

    @property
    def media_artist(self) -> str | None:
        return self.zone.playback_zone.state.media_artist if self.zone.state.is_on else None

    @property
    def media_album_name(self) -> str | None:
        return self.zone.playback_zone.state.media_album if self.zone.state.is_on else None

    @property
    def media_image_url(self) -> str | None:
        return self.zone.playback_zone.state.media_image_url if self.zone.state.is_on else None

    @property
    def group_members(self) -> list[str]:
        registry = er.async_get(self.hass)
        members = []
        for zone in self.zone.group_members:
            entity_id = registry.async_get_entity_id("media_player", DOMAIN, zone.member_id)
            if entity_id:
                members.append(entity_id)
        return members

    # --- commands ---------------------------------------------------------

    async def _run(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except NuvoError as err:
            raise HomeAssistantError(f"{self.zone.name}: {err}") from err

    async def async_turn_on(self) -> None:
        await self._run(self.zone.turn_on())

    async def async_turn_off(self) -> None:
        """Turning off a group master also turns off the zones listening to it."""
        await self._run(self.zone.turn_off())

    async def async_set_volume_level(self, volume: float) -> None:
        await self._run(self.zone.set_volume(volume))

    async def async_volume_up(self) -> None:
        await self._run(self.zone.volume_step(2))

    async def async_volume_down(self) -> None:
        await self._run(self.zone.volume_step(-2))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._run(self.zone.set_mute(mute))

    async def async_select_source(self, source: str) -> None:
        keys = {SOURCE_LABELS.get(s.key, s.label): s.key for s in self.zone.sources}
        if source not in keys:
            raise ServiceValidationError(f"Unknown source {source!r}; choose from {list(keys)}")
        await self._run(self.zone.select_source(keys[source]))

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await self._browser.async_browse(media_content_id)

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:
        """Play a TuneIn station from the media browser, a media source, or an HTTP URL.

        URLs go to the zone with X_NUVO_PlayURI (MP3 and FLAC verified; WAV is
        refused, docs/protocol.md). Music Assistant plays through here.
        """
        if is_tunein_id(media_id):

            async def _play() -> None:
                container, station = await self._browser.async_resolve(media_id)
                await self.zone.play_item(container, station)

            await self._run(_play())
            return

        mime = media_type if "/" in str(media_type) else None
        if media_source.is_media_source_id(media_id):
            resolved = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id, mime = resolved.url, resolved.mime_type
        url = async_process_play_media_url(self.hass, media_id)
        if not url.startswith(("http://", "https://")):
            raise ServiceValidationError(
                f"{self.zone.name} plays TuneIn stations, media sources and HTTP URLs, not {media_id!r}"
            )
        extra = kwargs.get(ATTR_MEDIA_EXTRA) or {}
        meta = extra.get("metadata") or {}
        images = meta.get("images") or [{}]
        await self._run(
            self.zone.play_url(
                url,
                mime,
                title=meta.get("title") or extra.get("title"),
                artist=meta.get("artist"),
                album=meta.get("album") or meta.get("albumName"),
                image_url=meta.get("imageUrl") or images[0].get("url") or extra.get("thumb"),
            )
        )

    def _stream_zone(self, command: str) -> NuvoZone:
        if not self._on_stream:
            raise ServiceValidationError(f"{self.zone.name}: {command} works only while an HTTP stream plays")
        return self.zone.playback_zone

    async def async_media_play(self) -> None:
        await self._run(self._stream_zone("play").play())

    async def async_media_pause(self) -> None:
        await self._run(self._stream_zone("pause").pause())

    async def async_media_stop(self) -> None:
        """Stop a stream. Elsewhere a no-op: Music Assistant stops a playing
        player before it plays to it, and a zone on Line In or TuneIn is then
        simply switched to the new stream."""
        if self._on_stream:
            await self._run(self.zone.playback_zone.stop())

    async def async_join_players(self, group_members: list[str]) -> None:
        registry = er.async_get(self.hass)
        by_member_id = {z.member_id: z for z in self.system.zones.values()}
        zones = []
        for entity_id in group_members:
            entry = registry.async_get(entity_id)
            if entry is None or entry.platform != DOMAIN or entry.unique_id not in by_member_id:
                raise ServiceValidationError(f"{entity_id} is not a zone of this Nuvo system")
            zones.append(by_member_id[entry.unique_id])
        await self._run(self.system.group(self.zone, zones))

    async def async_unjoin_player(self) -> None:
        await self._run(self.system.ungroup(self.zone))
