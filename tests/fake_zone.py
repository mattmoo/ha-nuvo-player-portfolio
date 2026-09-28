"""A fake Nuvo zone for tests: serves the recorded fixtures, keeps state, answers
SOAP, handles GENA SUBSCRIBE and sends NOTIFYs. Plus a fake SSDP responder."""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import aiohttp
from aiohttp import web

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
ZONE_TYPE = "urn:schemas-nuvotechnologies-com:device:Zone:1"
SCPD_FILES = {
    "ZoneService.xml": "Zone.scpd.xml",
    "AVTransport.xml": "AVTransport.scpd.xml",
    "ConnectionManager.xml": "ConnectionManager.scpd.xml",
    "RenderingControl.xml": "RenderingControl.scpd.xml",
    "ContentDirectory.xml": "ContentDirectory.scpd.xml",
}
SERVICE_TYPES = {
    "ZoneService": "urn:schemas-nuvotechnologies-com:service:Zone:1",
    "AVTransport": "urn:schemas-upnp-org:service:AVTransport:1",
    "RenderingControl": "urn:schemas-upnp-org:service:RenderingControl:1",
    "ContentDirectory": "urn:schemas-upnp-org:service:ContentDirectory:1",
    "ConnectionManager-MR": "urn:schemas-upnp-org:service:ConnectionManager:1",
    "ConnectionManager-MS": "urn:schemas-upnp-org:service:ConnectionManager:1",
}
_gid = itertools.count(1)

# ContentDirectory listings recorded from a P4300 (fixtures/tunein), keyed by ObjectID.
TUNEIN_FIXTURES = FIXTURES / "tunein"
_DIDL_ENTRY = re.compile(r"<(item|container)\b.*?</\1>", re.S)
_DIDL_OPEN = re.compile(r"^\s*<DIDL-Lite[^>]*>", re.S)


def _load_listings() -> dict[str, str]:
    root = (TUNEIN_FIXTURES / "root.didl.xml").read_text()
    ids = {name: re.search(rf'<container id="([^"]*nsdkDisplayName={name})"', root).group(1)
           for name in ("My%20Favorites", "Local%20Radio", "Music")}
    return {
        "tunein:": root,
        ids["My%20Favorites"].replace("&amp;", "&"): (TUNEIN_FIXTURES / "favorites.didl.xml").read_text(),
        ids["Local%20Radio"].replace("&amp;", "&"): (TUNEIN_FIXTURES / "local.didl.xml").read_text(),
        ids["Music"].replace("&amp;", "&"): (TUNEIN_FIXTURES / "music.didl.xml").read_text(),
    }


LISTINGS = _load_listings()


@dataclass
class Subscription:
    service: str
    callback: str
    seq: int = 0


@dataclass
class FakeZone:
    mac: str = "0025ed1dd983"
    title: str = "Lounge"
    volume: int = 44
    muted: bool = False
    member_group: str = "gidInitial"
    master_group: str = ""
    amp: list[FakeZone] | None = field(default=None, repr=False, compare=False)  # one physical amp
    uri: str = ""
    transport: str = "PLAYING"
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    subs: dict[str, Subscription] = field(default_factory=dict)
    faults: dict[str, tuple[int, str]] = field(default_factory=dict)
    reject_renew: bool = False
    # Simulates the amp's lagging Get: report these groups instead of the real ones.
    stale_get_groups: tuple[str, str] | None = None
    port: int = 0
    _runner: web.AppRunner | None = None
    _session: aiohttp.ClientSession | None = None

    def __post_init__(self) -> None:
        if not self.uri:
            self.uri = self.line_in_uri
        if not self.master_group:
            self.master_group = self.member_group
        if self.amp is None:
            self.amp = []
        self.amp.append(self)

    @property
    def member_id(self) -> str:
        return f"memberId-{self.mac}"

    @property
    def udn(self) -> str:
        return f"uuid:00000000-0000-0000-0000-{self.mac}"

    @property
    def line_in_uri(self) -> str:
        return f"nuvo:nuvoremote:{self.member_id}/lineIn_{self.member_id}"

    @property
    def location(self) -> str:
        return f"http://127.0.0.1:{self.port}/00000000-0000-0000-0000-{self.mac}.xml"

    # --- server ---------------------------------------------------------

    async def start(self, port: int = 0) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._session:
            await self._session.close()
            self._session = None

    async def restart_on_new_port(self) -> None:
        """Simulate a reboot: new ephemeral port, all subscriptions lost."""
        old = self.port
        await self.stop()
        self.subs.clear()
        await self.start()
        assert self.port != old

    async def _handle(self, request: web.Request) -> web.Response:
        path = request.path
        if request.method == "GET":
            if path.endswith(f"{self.mac}.xml"):
                body = (FIXTURES / f"00000000-0000-0000-0000-{self.mac}" / "description.xml").read_bytes()
                return web.Response(body=body, content_type="text/xml")
            name = path.rsplit("/", 1)[-1]
            if name in SCPD_FILES:
                body = (FIXTURES / f"00000000-0000-0000-0000-{self.mac}" / SCPD_FILES[name]).read_bytes()
                return web.Response(body=body, content_type="text/xml")
            raise web.HTTPNotFound()
        service = path.strip("/").split("/")[0]
        if request.method == "SUBSCRIBE":
            return self._subscribe(service, request)
        if request.method == "UNSUBSCRIBE":
            self.subs.pop(request.headers.get("SID", ""), None)
            return web.Response()
        if request.method == "POST" and path.endswith("/control"):
            return await self._soap(service, request)
        raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST", "SUBSCRIBE", "UNSUBSCRIBE"])

    # --- GENA -----------------------------------------------------------

    def _subscribe(self, service: str, request: web.Request) -> web.Response:
        sid = request.headers.get("SID")
        if sid:  # renewal
            if self.reject_renew or sid not in self.subs:
                return web.Response(status=412)
        else:
            callback = request.headers["CALLBACK"].strip("<>")
            sid = f"uuid:sub-{self.mac}-{next(_gid)}-{service}"
            self.subs[sid] = Subscription(service, callback)
            asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(self._initial(sid)))
        return web.Response(headers={"SID": sid, "TIMEOUT": "Second-300"})

    async def _initial(self, sid: str) -> None:
        sub = self.subs.get(sid)
        if not sub:
            return
        if sub.service == "ZoneService":
            await self._send(sid, self._zone_vars())
        elif sub.service == "RenderingControl":
            await self._send(sid, {"LastChange": self._rc_last_change()})
        elif sub.service == "AVTransport":
            await self._send(sid, {"LastChange": self._avt_last_change()})

    async def _send(self, sid: str, props: dict[str, str]) -> None:
        sub = self.subs.get(sid)
        if not sub or not self._session:
            return
        body = '<?xml version="1.0"?><e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
        body += "".join(f"<e:property><{k}>{escape(str(v))}</{k}></e:property>" for k, v in props.items())
        body += "</e:propertyset>"
        headers = {"NT": "upnp:event", "NTS": "upnp:propchange", "SID": sid, "SEQ": str(sub.seq),
                   "Connection": "close",
                   "Content-Type": 'text/xml; charset="utf-8"'}
        sub.seq += 1
        try:
            async with self._session.request("NOTIFY", sub.callback, data=body, headers=headers) as r:
                await r.read()
        except aiohttp.ClientError:
            pass

    async def notify(self, service: str) -> None:
        """Push current state for `service` to every subscriber."""
        for sid, sub in list(self.subs.items()):
            if sub.service == service:
                await self._initial(sid)

    def _zone_vars(self) -> dict[str, str]:
        mg = json.dumps({"id": self.member_group, "sortKey": "1"})
        ms = json.dumps({"id": self.master_group, "sortKey": "1"})
        return {"Title": self.title, "MemberID": self.member_id, "SystemID": "nuvoTEST",
                "Model": "p4300", "FirmwareVersion": "2025.1", "PowerState": "active",
                "AudioInput": "player", "MemberGroup": mg, "MasterGroup": ms,
                "Active": "1", "HttpPort": "80"}

    def _rc_last_change(self) -> str:
        return ('<Event xmlns="urn:schemas-upnp-org:metadata-1-0/RCS/"><InstanceID val="0">'
                f'<Mute channel="Master" val="{int(self.muted)}"/>'
                f'<Volume channel="Master" val="{self.volume}"/></InstanceID></Event>')

    def _avt_last_change(self) -> str:
        return ('<Event xmlns="urn:schemas-upnp-org:metadata-1-0/AVT/"><InstanceID val="0">'
                f'<TransportState val="{self.transport}"/>'
                f'<AVTransportURI val="{escape(self.uri, {chr(34): "&quot;"})}"/></InstanceID></Event>')

    # --- app-side changes (simulate the official app) --------------------

    async def app_set_volume(self, volume: int) -> None:
        self.volume = volume
        await self.notify("RenderingControl")

    # --- SOAP -----------------------------------------------------------

    async def _soap(self, service: str, request: web.Request) -> web.Response:
        action = request.headers["SOAPAction"].strip('"').split("#")[1]
        root = ET.fromstring(await request.read())
        body = root.find("{http://schemas.xmlsoap.org/soap/envelope/}Body")[0]
        args = {el.tag: el.text or "" for el in body}
        self.calls.append((action, args))
        if action in self.faults:
            return self._fault(*self.faults.pop(action))
        handler = getattr(self, f"_a_{action}", None)
        if handler is None:
            return self._fault(401, "Invalid Action")
        result = handler(args)
        if isinstance(result, web.Response):
            return result
        stype = SERVICE_TYPES[service]
        out = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in (result or {}).items())
        xml = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
               f'<u:{action}Response xmlns:u="{stype}">{out}</u:{action}Response></s:Body></s:Envelope>')
        if not action.startswith("Get") and action != "Browse":  # real zones only event on change
            for svc in ("ZoneService", "RenderingControl", "AVTransport"):
                asyncio.get_running_loop().call_soon(lambda s=svc: asyncio.ensure_future(self.notify(s)))
        return web.Response(text=xml, content_type="text/xml")

    @staticmethod
    def _fault(code: int, desc: str) -> web.Response:
        xml = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><s:Fault>'
               '<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>'
               '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
               f'<errorCode>{code}</errorCode><errorDescription>{escape(desc)}</errorDescription>'
               '</UPnPError></detail></s:Fault></s:Body></s:Envelope>')
        return web.Response(status=500, text=xml, content_type="text/xml")

    def _a_Get(self, a):
        member, master = self.stale_get_groups or (self.member_group, self.master_group)
        mg = json.dumps({"id": member, "sortKey": "1"})
        ms = json.dumps({"id": master, "sortKey": "1"})
        return {"FirmwareVersion": "2025.1", "SystemID": "nuvoTEST", "MemberID": self.member_id,
                "Title": self.title, "Icon": "skin:iconZoneLivingRoom", "Active": "1", "Connecting": "0",
                "MasterGroup": ms, "MemberGroup": mg, "Model": "p4300", "AudioInput": "player",
                "PowerState": "active"}

    def _a_GetVolume(self, a):
        return {"CurrentVolume": self.volume}

    def _a_SetVolume(self, a):
        self.volume = int(a["DesiredVolume"])

    def _a_X_NUVO_AdjustVolume(self, a):
        self.volume = max(0, min(100, self.volume + int(a["VolumeAdjustment"])))

    def _a_GetMute(self, a):
        return {"CurrentMute": int(self.muted)}

    def _a_SetMute(self, a):
        self.muted = a["DesiredMute"] in ("1", "true", "True")

    def _a_GetTransportInfo(self, a):
        return {"CurrentTransportState": self.transport, "CurrentTransportStatus": "OK", "CurrentSpeed": "1"}

    def _a_GetMediaInfo(self, a):
        return {"NrTracks": 0, "MediaDuration": "NOT_IMPLEMENTED", "CurrentURI": self.uri,
                "CurrentURIMetaData": "", "NextURI": "", "NextURIMetaData": "", "PlayMedium": "UNKNOWN",
                "RecordMedium": "NOT_IMPLEMENTED", "WriteStatus": "NOT_IMPLEMENTED"}

    def _a_GetCurrentTransportActions(self, a):
        return {"Actions": "Play,Stop" if self.member_group else ""}

    # Group semantics as observed on a P4300 (docs/protocol.md, "Grouping").

    def _peer(self, member_id: str) -> FakeZone:
        return next(z for z in self.amp if z.member_id == member_id)

    def _changed(self, *zones: FakeZone) -> None:
        for z in zones:
            if z is not self and z._runner is not None:
                asyncio.get_running_loop().call_soon(lambda z=z: asyncio.ensure_future(z.notify("ZoneService")))

    def _a_GroupCreate(self, a):
        members = json.loads(a["memberIDs"])
        assert members == [self.member_id], members
        self.member_group = self.master_group = f"gidNew{next(_gid)}"
        self.transport = "NO_MEDIA_PRESENT"
        self.uri = ""
        return {"groupID": self.member_group}

    def _a_GroupDisband(self, a):
        gid = a["groupID"]
        if not gid or not any(z.master_group == gid for z in self.amp):
            return self._fault(501, "Group not found")
        hit = [z for z in self.amp if gid in (z.member_group, z.master_group)]
        for z in hit:
            if z.member_group == gid:
                z.member_group = ""
            if z.master_group == gid:
                z.master_group = ""
                z.transport, z.uri = "NO_MEDIA_PRESENT", ""
        self._changed(*hit)

    def _a_GroupMemberSetGroup(self, a):
        gid = a["groupID"]
        if gid and not any(z.master_group == gid for z in self.amp):
            return self._fault(501, "Group not found")
        peers = [self._peer(m) for m in json.loads(a["memberIDs"])]
        for z in peers:
            if z.master_group and z.master_group != gid:
                # The amp auto-disbands the joiner's own group; its listeners go off.
                old = z.master_group
                for other in self.amp:
                    if other is not z and other.member_group == old:
                        other.member_group = ""
                z.master_group = ""
                z.transport, z.uri = "NO_MEDIA_PRESENT", ""
            z.member_group = gid
        self._changed(*self.amp)

    def _a_Browse(self, a):
        if a["BrowseFlag"] != "BrowseDirectChildren" or a["ObjectID"] not in LISTINGS:
            return self._fault(501, f"Node at path '{a['ObjectID']}' does not exist")
        didl = LISTINGS[a["ObjectID"]]
        entries = [m.group(0) for m in _DIDL_ENTRY.finditer(didl)]
        start, count = int(a["StartingIndex"]), int(a["RequestedCount"]) or len(entries)
        page = entries[start : start + count]
        head = _DIDL_OPEN.match(didl).group(0).strip()
        return {"Result": head + "".join(page) + "</DIDL-Lite>", "NumberReturned": len(page),
                "TotalMatches": len(entries), "UpdateID": 1}

    def _a_X_NUVO_PlayContainerURI(self, a):
        if not self.member_group:
            return self._fault(701, "No group")
        self.uri = a["TrackURI"]
        self.transport = "PLAYING"

    def _a_Play(self, a):
        self.transport = "PLAYING"

    def _a_Stop(self, a):
        self.transport = "STOPPED"

    def _a_Pause(self, a):
        self.transport = "PAUSED_PLAYBACK"

    def _a_Next(self, a):
        pass

    def _a_Previous(self, a):
        pass


class FakeSsdp(asyncio.DatagramProtocol):
    """Answers M-SEARCH on 127.0.0.1:<port> for the given zones, plus a non-Nuvo device."""

    def __init__(self, zones: list[FakeZone], *, only_ssdp_all: bool = False) -> None:
        self.zones = zones
        self.only_ssdp_all = only_ssdp_all
        self.searches: list[str] = []
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=("127.0.0.1", 0), family=socket.AF_INET
        )
        self.port = self.transport.get_extra_info("sockname")[1]

    def stop(self) -> None:
        if self.transport:
            self.transport.close()

    def datagram_received(self, data: bytes, addr) -> None:
        text = data.decode()
        m = re.search(r"^ST:\s*(.+?)\s*$", text, re.M | re.I)
        st = m.group(1) if m else ""
        self.searches.append(st)
        replies = [
            ("upnp:rootdevice", "uuid:not-a-nuvo::upnp:rootdevice", "http://127.0.0.1:9/tv.xml"),
            ("urn:schemas-upnp-org:device:MediaRenderer:1",
             "uuid:not-a-nuvo::urn:schemas-upnp-org:device:MediaRenderer:1", "http://127.0.0.1:9/tv.xml"),
        ]
        for z in self.zones:
            if z._runner is not None:
                replies.append((ZONE_TYPE, f"{z.udn}::{ZONE_TYPE}", z.location))
        for rst, usn, loc in replies:
            if st == "ssdp:all" or (st == rst and not self.only_ssdp_all):
                msg = (f"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=100\r\nEXT:\r\nLOCATION: {loc}\r\n"
                       f"SERVER: Linux/2.6.37 UPnP/1.0 GUPnP/0.20.8\r\nST: {rst}\r\nUSN: {usn}\r\n\r\n")
                self.transport.sendto(msg.encode(), addr)
