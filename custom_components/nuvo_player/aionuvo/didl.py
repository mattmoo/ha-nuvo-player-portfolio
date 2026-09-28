"""DIDL-Lite payloads for X_NUVO_PlayContainerURI, and metadata and listing parsing.

The Line In payload is byte-for-byte the one the official app sends, as captured
by mpdrago/nuvo-zone-keepalive (MIT) and replayed on a P4300 (docs/protocol.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

_DIDL_OPEN = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns:x="urn:schemas.nuvotechnologies.com">'
)

LINE_IN_CONTAINER_METADATA = (
    _DIDL_OPEN
    + '<container id="lineIn:systemLineIns" parentID="0" restricted="1">\n'
    "        <dc:title>Line In</dc:title>\n"
    "        <upnp:class>object.container</upnp:class>\n"
    '        <x:x_nuvo_nsdk>{&quot;title&quot;:&quot;Line In&quot;,&quot;containerType&quot;:'
    "&quot;none&quot;,&quot;path&quot;:&quot;lineIn:systemLineIns&quot;,&quot;type&quot;:"
    "&quot;container&quot;,&quot;align&quot;:&quot;left&quot;}</x:x_nuvo_nsdk>\n"
    "    </container></DIDL-Lite>"
)


def line_in_uri(member_id: str) -> str:
    """Track URI of a zone's own line input, e.g. for memberId-0025ed1dd983."""
    return f"nuvo:nuvoremote:{member_id}/lineIn_{member_id}"


def line_in_track_metadata(member_id: str, zone_name: str) -> str:
    """Item DIDL for a zone's line input, as sent by the Nuvo app."""
    m = member_id
    name = escape(zone_name)
    # JSON inside XML text: quotes as &quot;, the zone name JSON-escaped first.
    jname = escape(zone_name.replace("\\", "\\\\").replace('"', '\\"')).replace('"', "&quot;")
    q = "&quot;"
    ctx_json = (
        f"{{{q}containerType{q}:{q}context{q},{q}path{q}:{q}lineIn:lineInContext{m}{q},"
        f"{q}type{q}:{q}container{q},{q}id{q}:{q}{m}{q},{q}align{q}:{q}left{q}}}"
    )
    item_json = (
        f"{{{q}mediaData{q}:{{{q}metaData{q}:{{{q}playLogicPath{q}:{q}nuvoremote:{m}/lineIn:PlayLogic{q},"
        f"{q}groupPlayRemote{q}:true,{q}serviceID{q}:{q}lineIn{q}}}}},{q}title{q}:{q}Line Input{q},"
        f"{q}description{q}:{q}{jname}{q},{q}path{q}:{q}nuvoremote:{m}/lineIn_{m}{q},"
        f"{q}icon{q}:{q}skin:iconLineIn{q},{q}type{q}:{q}audio{q},{q}audioType{q}:{q}audioBroadcast{q},"
        f"{q}id{q}:{q}{m}{q},{q}context{q}:{ctx_json},{q}sortKey{q}:{q}1{q}}}"
    )
    return (
        _DIDL_OPEN
        + f'<item id="nuvoremote:{m}/lineIn_{m}" parentID="/stable/lineIn/" restricted="1">\n'
        "        <dc:title>Line Input</dc:title>\n"
        "        <dc:creator>lineIn</dc:creator>\n"
        f'        <res protocolInfo="nuvo:*:*:*">{line_in_uri(m)}</res>\n'
        "        <upnp:class>object.item.audioItem.audioBroadcast</upnp:class>\n"
        f"        <dc:description>{name}</dc:description>\n"
        "        <upnp:icon>skin:iconLineIn</upnp:icon>\n"
        f'        <x:x_nuvo_context id="lineIn:lineInContext{m}" restricted="1">\n'
        "            <dc:title></dc:title>\n"
        "            <upnp:class>object.container.x_nuvo_contextContainer</upnp:class>\n"
        f"            <x:x_nuvo_itemId>{m}</x:x_nuvo_itemId>\n"
        f"            <x:x_nuvo_nsdk>{ctx_json}</x:x_nuvo_nsdk>\n"
        "        </x:x_nuvo_context>\n"
        f"        <x:x_nuvo_itemId>{m}</x:x_nuvo_itemId>\n"
        f"        <x:x_nuvo_nsdk>{item_json}</x:x_nuvo_nsdk>\n"
        "    </item></DIDL-Lite>"
    )


_NS = {
    "d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
}


def parse_metadata(didl: str | None) -> dict[str, str | None]:
    """Return title/artist/album/image_url from the first item of a DIDL-Lite document."""
    out: dict[str, str | None] = {"title": None, "artist": None, "album": None, "image_url": None}
    if not didl or not didl.strip():
        return out
    try:
        root = ET.fromstring(didl.strip())
    except ET.ParseError:
        return out
    item = root.find("d:item", _NS)
    if item is None:
        return out
    out["title"] = item.findtext("dc:title", None, _NS)
    out["artist"] = item.findtext("upnp:artist", None, _NS) or item.findtext("dc:creator", None, _NS)
    out["album"] = item.findtext("upnp:album", None, _NS)
    # TuneIn stations carry their logo as upnp:icon; Line In has a skin: icon.
    art = item.findtext("upnp:albumArtURI", None, _NS) or item.findtext("upnp:icon", None, _NS)
    out["image_url"] = art if art and art.startswith("http") else None
    return out


# --- ContentDirectory listings -------------------------------------------

# Top-level entries of a Browse result. Items and containers never nest (the
# x:x_nuvo_context inside an item is a different tag), so a lazy match is safe.
_ENTRY_RE = re.compile(r"<(item|container)\b.*?</\1>", re.S)


@dataclass(frozen=True)
class DidlEntry:
    """One item or container from a Browse listing.

    `xml` is the element exactly as the zone sent it: X_NUVO_PlayContainerURI
    wants it back verbatim (docs/protocol.md, "TuneIn playback").
    """

    kind: str  # "item" or "container"
    id: str
    parent_id: str
    title: str
    upnp_class: str
    res: str | None
    icon: str | None
    description: str | None
    index: int  # 1-based position in the parent's listing
    xml: str

    @property
    def playable(self) -> bool:
        return self.kind == "item" and bool(self.id and self.res) and self.upnp_class.startswith(
            "object.item.audioItem"
        )

    @property
    def didl(self) -> str:
        """The element wrapped in a DIDL-Lite document."""
        return wrap_didl(self.xml)


def wrap_didl(fragment: str) -> str:
    return _DIDL_OPEN + fragment + "</DIDL-Lite>"


def parse_listing(didl: str | None, start: int = 0) -> list[DidlEntry]:
    """Parse a Browse result; `start` is the StartingIndex the listing was fetched from."""
    entries = []
    for pos, match in enumerate(_ENTRY_RE.finditer(didl or ""), start=start + 1):
        fragment = match.group(0)
        try:
            el = ET.fromstring(wrap_didl(fragment))[0]
        except ET.ParseError:
            continue
        icon = el.findtext("upnp:icon", None, _NS)
        entries.append(
            DidlEntry(
                kind=match.group(1),
                id=el.get("id", ""),
                parent_id=el.get("parentID", ""),
                title=el.findtext("dc:title", "", _NS),
                upnp_class=el.findtext("upnp:class", "", _NS),
                res=el.findtext("d:res", None, _NS),
                icon=icon if icon and icon.startswith("http") else None,
                description=el.findtext("dc:description", None, _NS),
                index=pos,
                xml=fragment,
            )
        )
    return entries
