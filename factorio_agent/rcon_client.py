"""Minimal Source-RCON client (the protocol Factorio's --rcon-port speaks).

Implemented directly against the protocol spec rather than a third-party
package so behavior is fully known/verifiable: 4-byte little-endian length
prefix, 4-byte request id, 4-byte packet type, null-terminated body, extra
null terminator.
"""

from __future__ import annotations

import socket
import struct

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RCONError(RuntimeError):
    pass


class RCONClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._request_id = 0
        self._authenticate(password)

    def _send_packet(self, packet_type: int, body: str) -> None:
        self._request_id += 1
        payload = struct.pack("<ii", self._request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        self._sock.sendall(struct.pack("<i", len(payload)) + payload)

    def _read_packet(self) -> tuple[int, int, str]:
        length_bytes = self._recv_exact(4)
        (length,) = struct.unpack("<i", length_bytes)
        payload = self._recv_exact(length)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, body

    def _recv_exact(self, n: int) -> bytes:
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise RCONError("connection closed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _authenticate(self, password: str) -> None:
        self._send_packet(SERVERDATA_AUTH, password)
        request_id, packet_type, _ = self._read_packet()
        if request_id == -1:
            raise RCONError("RCON authentication failed (wrong password)")

    def send_command(self, command: str) -> str:
        self._send_packet(SERVERDATA_EXECCOMMAND, command)
        _, _, body = self._read_packet()
        return body

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> "RCONClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
