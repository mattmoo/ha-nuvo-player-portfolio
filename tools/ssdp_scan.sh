#!/bin/bash
# Read-only SSDP inventory. Sends M-SEARCH (multicast, or unicast to given
# hosts) and prints every reply's full headers as JSON lines.
#
# Usage: tools/ssdp_scan.sh [ST] [TIMEOUT] [HOST...]
#   ST defaults to the Nuvo Zone device type; pass ssdp:all for everything.
set -eu
ST="${1:-urn:schemas-nuvotechnologies-com:device:Zone:1}"
TIMEOUT="${2:-6}"
shift $(( $# > 2 ? 2 : $# ))
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../.venv/bin/python" - "$ST" "$TIMEOUT" "$@" <<'EOF'
import json, socket, sys, time

st, timeout, hosts = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
targets = [(h, 1900) for h in hosts] or [("239.255.255.250", 1900)]
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
for host, port in targets:
    msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: {host}:{port}\r\n"
           f'MAN: "ssdp:discover"\r\nMX: 3\r\nST: {st}\r\n\r\n').encode()
    for _ in range(2):
        sock.sendto(msg, (host, port))
seen = set()
deadline = time.time() + timeout
while (left := deadline - time.time()) > 0:
    sock.settimeout(left)
    try:
        data, addr = sock.recvfrom(65507)
    except socket.timeout:
        break
    if data in seen:
        continue
    seen.add(data)
    lines = data.decode(errors="replace").split("\r\n")
    headers = {"_from": addr[0], "_status": lines[0]}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().upper()] = v.strip()
    print(json.dumps(headers))
EOF
