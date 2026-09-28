"""Constants for the Nuvo Player Portfolio integration."""

from datetime import timedelta

DOMAIN = "nuvo_player"

CONF_HOSTS = "hosts"
CONF_SYSTEM_ID = "system_id"
CONF_CALLBACK_PORT = "callback_port"

# 0 = let the OS pick. Pin it when a firewall sits between the amp and HA.
DEFAULT_CALLBACK_PORT = 0

# Push events are the primary update path; this is a light safety net.
HEARTBEAT_INTERVAL = timedelta(seconds=60)

SOURCE_LABELS = {"line_in": "Line In"}
