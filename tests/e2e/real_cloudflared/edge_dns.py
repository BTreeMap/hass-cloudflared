"""Fake DNS server for the real-cloudflared E2E harness.

Answers the Cloudflare edge discovery SRV records with hosts that resolve to a
non-routable sink address, so cloudflared starts, keeps its metrics server up
and retries the edge instead of exiting on a DNS failure. Names given as
name=ip get an A record. Every other name gets NXDOMAIN, which keeps the test
off the real Cloudflare API and edge.

Usage: python3 edge_dns.py <bind ip> <sink ip> [name=ip ...]
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys
import threading

QTYPE_A = 1
QTYPE_SRV = 33
QTYPE_ANY = 255
EDGE_PORT = 7844
EDGE_SRV = frozenset(
    {"_v2-origintunneld._tcp.argotunnel.com", "_origintunneld._tcp.argotunnel.com"}
)
EDGE_HOSTS = ("region1.v2.argotunnel.com", "region2.v2.argotunnel.com")
EDGE_DOMAIN = "argotunnel.com"


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("ascii")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def parse_question(packet: bytes) -> tuple[str, int, int]:
    """Return (name, qtype, offset just past the question)."""
    labels: list[str] = []
    offset = 12
    while packet[offset] != 0:
        length = packet[offset]
        labels.append(packet[offset + 1 : offset + 1 + length].decode("ascii"))
        offset += 1 + length
    qtype = struct.unpack("!H", packet[offset + 1 : offset + 3])[0]
    return ".".join(labels).lower(), int(qtype), offset + 5


def record(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_name(name) + struct.pack("!HHIH", rtype, 1, 60, len(rdata)) + rdata


def a_record(name: str, address: str) -> bytes:
    return record(name, QTYPE_A, ipaddress.IPv4Address(address).packed)


def answer(packet: bytes, sink: str, static: dict[str, str]) -> bytes:
    name, qtype, end = parse_question(packet)
    answers: list[bytes] = []
    additional: list[bytes] = []
    known = True
    if name in EDGE_SRV:
        if qtype in (QTYPE_SRV, QTYPE_ANY):
            for host in EDGE_HOSTS:
                srv = struct.pack("!HHH", 1, 1, EDGE_PORT) + encode_name(host)
                answers.append(record(name, QTYPE_SRV, srv))
                additional.append(a_record(host, sink))
    elif name == EDGE_DOMAIN or name.endswith("." + EDGE_DOMAIN):
        if qtype in (QTYPE_A, QTYPE_ANY):
            answers.append(a_record(name, sink))
    elif name in static:
        if qtype in (QTYPE_A, QTYPE_ANY):
            answers.append(a_record(name, static[name]))
    else:
        known = False
    flags = 0x8180 if known else 0x8183  # response, RD, RA; rcode 0 or 3
    header = packet[:2] + struct.pack(
        "!HHHHH", flags, 1, len(answers), 0, len(additional)
    )
    return header + packet[12:end] + b"".join(answers) + b"".join(additional)


def serve_udp(bind: str, sink: str, static: dict[str, str]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind, 53))
    while True:
        packet, peer = sock.recvfrom(4096)
        try:
            sock.sendto(answer(packet, sink, static), peer)
        except (IndexError, struct.error, UnicodeDecodeError):
            continue


def serve_tcp(bind: str, sink: str, static: dict[str, str]) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((bind, 53))
    server.listen()
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                length = struct.unpack("!H", conn.recv(2))[0]
                reply = answer(conn.recv(length), sink, static)
                conn.sendall(struct.pack("!H", len(reply)) + reply)
            except (OSError, IndexError, struct.error, UnicodeDecodeError):
                continue


def main() -> None:
    bind, sink = sys.argv[1], sys.argv[2]
    static: dict[str, str] = {}
    for item in sys.argv[3:]:
        name, address = item.split("=", 1)
        static[name.rstrip(".").lower()] = address
    threading.Thread(target=serve_tcp, args=(bind, sink, static), daemon=True).start()
    serve_udp(bind, sink, static)


if __name__ == "__main__":
    main()
