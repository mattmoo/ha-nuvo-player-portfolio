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
SOURCE_TUNEIN = "tunein"
# An HTTP stream sent with X_NUVO_PlayURI; not selectable.
SOURCE_STREAM = "stream"
# A zone's AVTransportURI while it plays such a stream.
STREAM_URI = "nuvo:"

# SetAVTransportURI + Play of a WAV crashed the zone's UPnP process, and WAV via
# X_NUVO_PlayURI is untested (docs/protocol.md, "HTTP stream playback").
REFUSED_STREAM_MIMES = frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave", "audio/l16"})

# TuneIn in the zone's ContentDirectory, and its track URIs (docs/protocol.md).
TUNEIN_ROOT = "tunein:"
TUNEIN_URI_PREFIX = "nuvo:tunein:"
# Browse page size, and the most entries read from one listing.
BROWSE_PAGE = 100
BROWSE_LIMIT = 500

# Rediscovery backoff after a zone stops answering, in seconds.
REDISCOVERY_BACKOFF = (1, 2, 5, 10, 30)

# Requested GENA subscription length; renewed at half the granted duration.
SUBSCRIPTION_TIMEOUT = 300
# Poll interval used while push events are not arriving.
POLL_INTERVAL = 30
