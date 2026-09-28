"""Constants for the Nuvo Player Portfolio UPnP protocol. See docs/protocol.md."""

ZONE_DEVICE_TYPE = "urn:schemas-nuvotechnologies-com:device:Zone:1"

ZONE_SERVICE = "urn:schemas-nuvotechnologies-com:service:Zone:1"
AVTRANSPORT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"
CONTENT_DIRECTORY_SERVICE = "urn:schemas-upnp-org:service:ContentDirectory:1"

SUBSCRIBED_SERVICES = (ZONE_SERVICE, AVTRANSPORT_SERVICE, RENDERING_SERVICE)

SSDP_PORT = 1900

MEMBER_ID_PREFIX = "memberId-"

SOURCE_LINE_IN = "line_in"

# Rediscovery backoff after a zone stops answering, in seconds.
REDISCOVERY_BACKOFF = (1, 2, 5, 10, 30)

# Requested GENA subscription length; renewed at half the granted duration.
SUBSCRIPTION_TIMEOUT = 300
# Poll interval used while push events are not arriving.
POLL_INTERVAL = 30
