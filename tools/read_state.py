#!/usr/bin/env python3
"""Read-only: call every Get* action on a zone and save raw SOAP responses to
fixtures/<udn>/soap/<Service>.<Action>.xml. Also browses the ContentDirectory
root and the line-in container. Refuses anything aionuvo.safety does not
consider read-only.

Usage: tools/read_state.py LOCATION [LOCATION ...]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aionuvo.safety import check_action, is_read_only  # noqa: E402

DEV = "{urn:schemas-upnp-org:device-1-0}"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
RC = {"InstanceID": "0", "Channel": "Master"}
IID = {"InstanceID": "0"}

CALLS: list[tuple[str, str, dict[str, str]]] = [
    ("Zone", "Get", {}),
    ("Zone", "GetActive", {}),
    ("Zone", "GetConnecting", {}),
    ("Zone", "GetUpdateState", {}),
    ("Zone", "GetFirmwareVersion", {}),
    ("Zone", "GetMemberID", {}),
    ("Zone", "GetSystemID", {}),
    ("RenderingControl", "GetVolume", RC),
    ("RenderingControl", "GetVolumeDB", RC),
    ("RenderingControl", "GetMute", RC),
    ("RenderingControl", "GetLoudness", RC),
    ("RenderingControl", "ListPresets", IID),
    ("AVTransport", "GetMediaInfo", IID),
    ("AVTransport", "GetTransportInfo", IID),
    ("AVTransport", "GetPositionInfo", IID),
    ("AVTransport", "GetTransportSettings", IID),
    ("AVTransport", "GetDeviceCapabilities", IID),
    ("AVTransport", "GetCurrentTransportActions", IID),
    ("ConnectionManager", "GetProtocolInfo", {}),
    ("ConnectionManager", "GetCurrentConnectionIDs", {}),
    ("ContentDirectory", "GetSystemUpdateID", {}),
    ("ContentDirectory", "GetSearchCapabilities", {}),
    ("ContentDirectory", "GetSortCapabilities", {}),
]
BROWSE_IDS = ["0", "lineIn:systemLineIns"]


def envelope(stype: str, action: str, args: dict[str, str]) -> str:
    body = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in args.items())
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
        'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        f'<u:{action} xmlns:u="{stype}">{body}</u:{action}></s:Body></s:Envelope>'
    )


async def soap(client, services, short, action, args) -> tuple[int, str]:
    check_action(action)
    if not is_read_only(action):
        raise RuntimeError(f"{action} is not read-only; refusing")
    stype, control = services[short]
    resp = await client.post(
        control,
        content=envelope(stype, action, args),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{stype}#{action}"',
        },
    )
    return resp.status_code, resp.text


async def read_zone(client: httpx.AsyncClient, location: str) -> None:
    root = ET.fromstring((await client.get(location)).raise_for_status().content)
    udn = root.findtext(f"{DEV}device/{DEV}UDN").removeprefix("uuid:")
    services = {}
    for svc in root.iter(f"{DEV}service"):
        stype = svc.findtext(f"{DEV}serviceType")
        control = urljoin(location, svc.findtext(f"{DEV}controlURL"))
        # Two ConnectionManagers (-MR and -MS); keep the MediaRenderer one.
        services.setdefault(stype.split(":")[-2], (stype, control))
    outdir = FIXTURES / udn / "soap"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"=== {udn} {location}")

    calls = list(CALLS) + [
        ("ContentDirectory", "Browse", {
            "ObjectID": oid, "BrowseFlag": "BrowseDirectChildren", "Filter": "*",
            "StartingIndex": "0", "RequestedCount": "100", "SortCriteria": "",
        })
        for oid in BROWSE_IDS
    ]
    for short, action, args in calls:
        status, text = await soap(client, services, short, action, args)
        name = action if action != "Browse" else f"Browse[{args['ObjectID'].replace(':', '_')}]"
        (outdir / f"{short}.{name}.xml").write_text(text)
        print(f"  {status} {short}.{name} ({len(text)}b)")


async def main(locations: list[str]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        for loc in locations:
            await read_zone(client, loc)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
