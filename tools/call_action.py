#!/usr/bin/env python3
"""Call one SOAP action on a zone and print the raw response.

Write actions need --allow-write; denylisted actions are always refused
(see docs/safety.md). Arguments are NAME=VALUE; a value of @file reads the
file's contents.

Usage: tools/call_action.py [--allow-write] LOCATION SERVICE ACTION [NAME=VALUE ...]
  SERVICE is the short type name: Zone, AVTransport, RenderingControl, ...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aionuvo.safety import check_action, check_post_url, is_read_only  # noqa: E402
from read_state import DEV, envelope  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--allow-write", action="store_true")
    p.add_argument("location")
    p.add_argument("service")
    p.add_argument("action")
    p.add_argument("args", nargs="*")
    a = p.parse_args()

    check_action(a.action)
    if not is_read_only(a.action) and not a.allow_write:
        sys.exit(f"{a.action} is a write action; pass --allow-write")
    args = {}
    for kv in a.args:
        k, v = kv.split("=", 1)
        args[k] = Path(v[1:]).read_text() if v.startswith("@") else v

    async with httpx.AsyncClient(timeout=15) as client:
        root = ET.fromstring((await client.get(a.location)).raise_for_status().content)
        for svc in root.iter(f"{DEV}service"):
            stype = svc.findtext(f"{DEV}serviceType")
            if stype.split(":")[-2] == a.service:
                control = urljoin(a.location, svc.findtext(f"{DEV}controlURL"))
                break
        else:
            sys.exit(f"no {a.service} service")
        check_post_url(control)
        resp = await client.post(
            control,
            content=envelope(stype, a.action, args),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{stype}#{a.action}"',
            },
        )
        print(resp.status_code)
        print(resp.text)


if __name__ == "__main__":
    asyncio.run(main())
