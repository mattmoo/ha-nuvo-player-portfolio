#!/usr/bin/env python3
"""Read-only ContentDirectory Browse on a zone.

Usage: tools/browse.py LOCATION OBJECT_ID [--meta] [--raw]
"""

from __future__ import annotations

import asyncio
import html
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_state import envelope  # noqa: E402

CD = "urn:schemas-upnp-org:service:ContentDirectory:1"


async def browse(location: str, oid: str, meta: bool = False) -> str:
    base = location.rsplit("/", 1)[0]
    args = {"ObjectID": oid, "BrowseFlag": "BrowseMetadata" if meta else "BrowseDirectChildren",
            "Filter": "*", "StartingIndex": "0", "RequestedCount": "50", "SortCriteria": ""}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{base}/ContentDirectory/control", content=envelope(CD, "Browse", args),
                         headers={"SOAPAction": f'"{CD}#Browse"', "Content-Type": 'text/xml; charset="utf-8"'})
    m = re.search(r"<Result>(.*?)</Result>", r.text, re.S)
    return html.unescape(m.group(1)) if m else r.text


def summarise(didl: str) -> None:
    for m in re.finditer(r'<(container|item) id="([^"]+)"[^>]*>(.*?)</\1>', didl, re.S):
        kind, oid, body = m.groups()
        title = re.search(r"<dc:title>([^<]*)", body)
        res = re.search(r"<res[^>]*>([^<]*)</res>", body)
        print(f"{kind:9} | {title.group(1) if title else '':40} | {html.unescape(oid)[:90]} | {res.group(1)[:60] if res else ''}")


if __name__ == "__main__":
    loc, oid = sys.argv[1], sys.argv[2]
    didl = asyncio.run(browse(loc, oid, "--meta" in sys.argv))
    print(didl) if "--raw" in sys.argv else summarise(didl)
