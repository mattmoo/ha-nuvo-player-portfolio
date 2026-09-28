# Changelog

All notable changes to this integration. Versions follow [Semantic Versioning](https://semver.org/);
while the version is 0.x, a minor bump (0.1 → 0.2) may include breaking changes, which are marked
**Breaking**. Pre-releases (`b1`, `rc1`, ...) are published as GitHub pre-releases, which HACS offers
when "Show beta versions" is on.

## 0.2.1 (2026-09-28)

### Changed
- New zones are named `media_player.nuvo_<zone>` (e.g. `media_player.nuvo_lounge`) instead of
  `media_player.<zone>`, so they no longer collide with other players named after the same room.
  The display name is still the zone's name. Existing entities keep their IDs; to get the new
  names, rename them in Settings → Entities, or delete and re-add the integration.

### Fixed
- Tests pass on Home Assistant 2026.9, and CI now also runs them on the latest HA release.

## 0.2.0 (2026-09-28)

First full release since 0.1.0; the same code as 0.2.0b1.

### Added
- **TuneIn** in each zone's media browser (Local Radio, Music, Talk, Sports, By Location,
  By Language, My Favorites), played by the zone itself, with station logos. Station IDs work in
  `media_player.play_media`. Other media (URLs, media sources) are refused: HTTP playback crashes
  the zone's UPnP server.
- **Line In feeds** (Configure → Line In feeds): per zone, the player wired to its Line In (e.g. a
  Chromecast Audio). When it starts playing, the zone switches to Line In, turning on if needed.
- Zone IP addresses are entered as a **list** (one box per address) and validated, in setup and in
  Configure → Network.
- The Nuvo logo as the integration's icon and logo (HA 2026.3 or later).

### Changed
- Configure is now a menu: **Line In feeds** and **Network**.
- `turn_on` on a zone playing TuneIn switches it to Line In (so Music Assistant's power control
  wins over TuneIn). It is still a no-op on Line In or while joined to another zone's group.

### Removed
- **Breaking:** the bass, treble and balance `number` entities and the loudness `switch`. The amp
  stored these values but never applied them to the sound or showed them in the Nuvo app. The old
  entities are deleted on upgrade; update any automations or dashboards that used them. Use the Nuvo
  app for tone and loudness. See PLAN.md, "Tone and loudness".

## 0.2.0b1 (2026-09-28)

Pre-release of 0.2.0 (same changes).

## 0.1.0 (2026-09-28)

First version: local control of Nuvo Player Portfolio zones over UPnP. Per-zone `media_player`
with power, volume, mute, Line In and grouping; push updates; recovery when the amp changes port;
SSDP/zeroconf discovery; tone and loudness entities (removed in 0.2.0).
