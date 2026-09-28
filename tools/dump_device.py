#!/usr/bin/env python3
"""Read-only: save a zone's device description and every SCPD to fixtures/<udn>/
and print services, actions, arguments and allowed values/ranges.

Usage: tools/dump_device.py LOCATION [LOCATION ...]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx

DEV = "{urn:schemas-upnp-org:device-1-0}"
SCPD = "{urn:schemas-upnp-org:service-1-0}"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _txt(el: ET.Element, tag: str) -> str:
    return (el.findtext(tag) or "").strip()


def _describe_scpd(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    vars_ = {}
    for sv in root.iter(f"{SCPD}stateVariable"):
        name = _txt(sv, f"{SCPD}name")
        info = [_txt(sv, f"{SCPD}dataType")]
        if sv.get("sendEvents") == "yes":
            info.append("evented")
        allowed = [a.text for a in sv.iter(f"{SCPD}allowedValue")]
        if allowed:
            info.append("allowed=" + "|".join(allowed))
        rng = sv.find(f"{SCPD}allowedValueRange")
        if rng is not None:
            info.append(
                "range={}..{} step {}".format(
                    _txt(rng, f"{SCPD}minimum"),
                    _txt(rng, f"{SCPD}maximum"),
                    _txt(rng, f"{SCPD}step") or "-",
                )
            )
        vars_[name] = " ".join(info)

    out = []
    for action in root.iter(f"{SCPD}action"):
        out.append(f"    {_txt(action, f'{SCPD}name')}")
        for arg in action.iter(f"{SCPD}argument"):
            rel = _txt(arg, f"{SCPD}relatedStateVariable")
            out.append(
                f"      {_txt(arg, f'{SCPD}direction'):<3} {_txt(arg, f'{SCPD}name')}"
                f"  [{rel}: {vars_.get(rel, '?')}]"
            )
    evented = [n for n, i in vars_.items() if "evented" in i]
    out.append(f"    evented vars: {', '.join(evented) or '-'}")
    return out


async def dump(client: httpx.AsyncClient, location: str) -> None:
    desc = (await client.get(location)).raise_for_status().content
    root = ET.fromstring(desc)
    device = root.find(f"{DEV}device")
    udn = _txt(device, f"{DEV}UDN").removeprefix("uuid:")
    outdir = FIXTURES / udn
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "description.xml").write_bytes(desc)

    print(f"=== {location}")
    for tag in ("deviceType", "friendlyName", "manufacturer", "modelName",
                "modelNumber", "modelDescription", "serialNumber", "UDN"):
        print(f"  {tag}: {_txt(device, DEV + tag)}")
    embedded = device.findall(f"{DEV}deviceList/{DEV}device")
    print(f"  embedded devices: {[ _txt(d, DEV + 'deviceType') for d in embedded ]}")

    for svc in root.iter(f"{DEV}service"):
        stype = _txt(svc, f"{DEV}serviceType")
        print(f"  service {stype}")
        print(f"    id={_txt(svc, DEV + 'serviceId')} control={_txt(svc, DEV + 'controlURL')}"
              f" event={_txt(svc, DEV + 'eventSubURL')} scpd={_txt(svc, DEV + 'SCPDURL')}")
        scpd_url = urljoin(location, _txt(svc, f"{DEV}SCPDURL"))
        scpd = (await client.get(scpd_url)).raise_for_status().content
        (outdir / f"{stype.split(':')[-2]}.scpd.xml").write_bytes(scpd)
        print("\n".join(_describe_scpd(scpd)))


async def main(locations: list[str]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        for loc in locations:
            await dump(client, loc)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
