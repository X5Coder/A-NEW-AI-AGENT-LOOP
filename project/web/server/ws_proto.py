"""Raw WebSocket transport (RFC 6455) — stdlib only, no dependencies.

Single owner of the wire protocol for the console's ONE connection:
handshake detection (MSG_PEEK, never consumes bytes), the 101 upgrade,
and masked-client/unmasked-server text frames + ping/pong/close.

Text messages only (JSON). Binary opcode is refused with 1003.
One message cap: WS_MAX_MESSAGE_BYTES (mirrors the old HTTP body cap).
Auth is NOT done here — the hub requires the first message to be the
auth op and closes anything else (see web/server/ws_hub.py).
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading

WS_GUID = "258EAFA0-E76C-11E2-9D74-100C4E671A97"
WS_PATH = "/ws"
PEEK_CAP = 8192
HEADER_CAP = 8192
WS_MAX_MESSAGE_BYTES = 170 * 1024 * 1024  # ~160MB JSON body cap + headroom
CLOSE_TIMEOUT = 2.0

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WsError(Exception):
    """Wire-level failure (peer closed, protocol violation, oversize)."""


def looks_like_ws_upgrade(sock: socket.socket) -> bool:
    """True when the pending bytes start a WS upgrade to /ws. Never consumes.

    Peeks until the request line ends (or PEEK_CAP). Any socket error or a
    non-WS request line returns False so the caller falls back to HTTP.
    """
    try:
        buf = b""
        while b"\r\n" not in buf:
            try:
                chunk = sock.recv(4096, socket.MSG_PEEK)
            except (BlockingIOError, InterruptedError):
                continue
            if not chunk:
                return False
            buf = chunk[:PEEK_CAP]
            if len(chunk) >= PEEK_CAP:
                break
        try:
            line = buf.split(b"\r\n", 1)[0].decode("latin-1")
        except Exception:
            return False
        parts = line.strip().split()
        if len(parts) < 2:
            return False
        method, target = parts[0].upper(), parts[1]
        path = target.split("?", 1)[0]
        return method == "GET" and path == WS_PATH
    except Exception:
        return False


def _read_headers(rfile) -> tuple[str, str, dict]:
    """Read the HTTP upgrade request. Returns (method, path, headers)."""
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = rfile.readline(HEADER_CAP)
        if not chunk:
            raise WsError("eof during handshake")
        raw += chunk
        if len(raw) > HEADER_CAP:
            raise WsError("handshake too large")
    try:
        text = raw.decode("latin-1")
    except Exception:
        raise WsError("bad handshake encoding")
    lines = text.split("\r\n")
    request_line = lines[0].strip().split()
    if len(request_line) < 2:
        raise WsError("bad request line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return request_line[0].upper(), request_line[1], headers


def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _read_exact(rfile, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = rfile.read(n - len(buf))
        if not chunk:
            raise WsError("connection closed")
        buf += chunk
    return buf


class WsConnection:
    """One upgraded socket. recv_text() yields complete text messages.

    Fragmentation (continuation frames) is reassembled. Ping is answered
    with pong automatically. Close is echoed once, then recv raises.
    """

    def __init__(self, sock: socket.socket, peer: str = ""):
        self.sock = sock
        self.peer = peer or ""
        self.rfile = sock.makefile("rb")
        self.wfile = sock.makefile("wb")
        self._send_lock = threading.Lock()
        self._closed = False
        self.last_pong = 0.0

    # -- handshake ------------------------------------------------------
    def handshake(self) -> dict:
        """Perform the server side of the upgrade. Returns request headers."""
        method, target, headers = _read_headers(self.rfile)
        path = target.split("?", 1)[0]
        if method != "GET" or path != WS_PATH:
            raise WsError("not a websocket upgrade")
        upgrade = headers.get("upgrade", "").lower()
        if "websocket" not in upgrade:
            raise WsError("missing Upgrade: websocket")
        key = headers.get("sec-websocket-key", "")
        if not key:
            raise WsError("missing Sec-WebSocket-Key")
        version = headers.get("sec-websocket-version", "").strip()
        if version and version != "13":
            raise WsError("unsupported websocket version")
        accept = _accept_key(key)
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode("latin-1")
        try:
            self.wfile.write(resp)
            self.wfile.flush()
        except Exception as e:
            raise WsError(f"handshake write failed: {e}")
        return headers

    # -- receiving ------------------------------------------------------
    def _read_frame(self) -> tuple[int, bytes]:
        """Read one frame. Returns (opcode, payload). Never raises silently."""
        head = _read_exact(self.rfile, 2)
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(self.rfile, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(self.rfile, 8))[0]
        if length > WS_MAX_MESSAGE_BYTES:
            raise WsError("frame too large")
        mask = _read_exact(self.rfile, 4) if masked else b""
        payload = _read_exact(self.rfile, length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode in (OP_TEXT, OP_BINARY, OP_CONT) and not masked:
            # Clients MUST mask (RFC 6455 §5.1) — unmasked = violation.
            raise WsError("unmasked client frame")
        if opcode == OP_PING:
            try:
                self._write_frame(OP_PONG, payload)
            except Exception:
                pass
            return self._read_frame()
        if opcode == OP_PONG:
            try:
                import time as _t
                self.last_pong = _t.monotonic()
            except Exception:
                pass
            return self._read_frame()
        if opcode == OP_CLOSE:
            try:
                self._write_frame(OP_CLOSE, payload[:124])
            except Exception:
                pass
            raise WsError("peer closed")
        if opcode == OP_CONT:
            raise WsError("unexpected continuation")
        if opcode not in (OP_TEXT, OP_BINARY):
            raise WsError(f"unsupported opcode {opcode}")
        if not fin:
            # Reassemble fragments (bounded by the message cap).
            parts = [payload]
            total = len(payload)
            first_op = opcode
            while True:
                head = _read_exact(self.rfile, 2)
                b1, b2 = head[0], head[1]
                fin = bool(b1 & 0x80)
                op = b1 & 0x0F
                if op != OP_CONT:
                    raise WsError("expected continuation")
                masked = bool(b2 & 0x80)
                if not masked:
                    raise WsError("unmasked client frame")
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack("!H", _read_exact(self.rfile, 2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", _read_exact(self.rfile, 8))[0]
                total += length
                if total > WS_MAX_MESSAGE_BYTES:
                    raise WsError("message too large")
                mask = _read_exact(self.rfile, 4)
                chunk = _read_exact(self.rfile, length) if length else b""
                parts.append(bytes(b ^ mask[i % 4] for i, b in enumerate(chunk)))
                if fin:
                    return first_op, b"".join(parts)
        return opcode, payload

    def recv_text(self, max_bytes: int = WS_MAX_MESSAGE_BYTES) -> str:
        """Next complete text message as str. Binary opcode is refused."""
        opcode, payload = self._read_frame()
        if opcode == OP_BINARY:
            try:
                self.close(1003, "binary not supported")
            except Exception:
                pass
            raise WsError("binary frames not supported")
        if len(payload) > max_bytes:
            raise WsError("message too large")
        try:
            return payload.decode("utf-8")
        except Exception:
            raise WsError("message is not utf-8")

    # -- sending --------------------------------------------------------
    def _write_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise WsError("connection closed")
        head = bytes([0x80 | (opcode & 0x0F)])
        n = len(payload)
        if n < 126:
            head += bytes([n])
        elif n < (1 << 16):
            head += bytes([126]) + struct.pack("!H", n)
        else:
            head += bytes([127]) + struct.pack("!Q", n)
        with self._send_lock:
            self.wfile.write(head + payload)
            self.wfile.flush()

    def send_text(self, text: str | bytes) -> None:
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if len(data) > WS_MAX_MESSAGE_BYTES:
            raise WsError("message too large")
        self._write_frame(OP_TEXT, data)

    def ping(self) -> None:
        try:
            self._write_frame(OP_PING, os.urandom(4))
        except Exception:
            raise WsError("ping failed")

    def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            body = struct.pack("!H", code) + reason.encode("utf-8")[:120]
            try:
                self._write_frame(OP_CLOSE, body)
            except Exception:
                pass
        finally:
            self._closed = True
            for stream in (self.rfile, self.wfile):
                try:
                    stream.close()
                except Exception:
                    pass
            try:
                self.sock.close()
            except Exception:
                pass
