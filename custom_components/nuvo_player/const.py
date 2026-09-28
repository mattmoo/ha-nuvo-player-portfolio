"""Constants for the Nuvo Player Portfolio integration."""

from datetime import timedelta

DOMAIN = "nuvo_player"

# New entities are suggested as media_player.nuvo_<zone>, so a zone named after
# its room does not collide with other players named after the same room.
ENTITY_ID_PREFIX = "nuvo"

CONF_HOSTS = "hosts"
CONF_SYSTEM_ID = "system_id"
CONF_CALLBACK_PORT = "callback_port"
# {zone member ID: media_player entity feeding that zone's Line In}
CONF_LINE_IN_FEEDS = "line_in_feeds"

# 0 = let the OS pick. Pin it when a firewall sits between the amp and HA.
DEFAULT_CALLBACK_PORT = 0

# Push events are the primary update path; this is a light safety net.
HEARTBEAT_INTERVAL = timedelta(seconds=60)

SOURCE_LABELS = {"line_in": "Line In", "tunein": "TuneIn"}
