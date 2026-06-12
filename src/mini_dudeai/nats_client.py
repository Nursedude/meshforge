"""Minimal stdlib sync NATS client — per-tick request/reply for OpenClaw.

Why not nats-py: it is asyncio-only, which would force a background thread +
event-loop lifecycle into a daemon whose whole model is a bounded synchronous
tick. mini-dudeai's NATS needs are one shape — request/reply against a
WireClaw node's `<device>.tool_exec` (and `_ion.discover`) — which a plain
socket speaks directly. This keeps the core dep-free (the Pi promise) and
every operation timeout-bounded.

Protocol subset (NATS is a CRLF text protocol):
    server → INFO {json}            on connect
    client → CONNECT {json}         (carries auth_token when configured)
    client → SUB <subject> <sid>    subscribe an inbox for replies
    client → PUB <subj> <reply> <n> publish n payload bytes w/ reply-to
    server → MSG <subj> <sid> [reply] <n>   then n payload bytes
    either → PING / PONG            answered inline while waiting

Connections are short-lived: open, request(s), close — no keepalive state to
get wrong. A NatsConnection can serve several requests within one tick so a
multi-sensor poll doesn't reconnect per sensor.

CLI (bring-up needs no natscli install):
    python3 -m mini_dudeai.nats_client req _ion.discover '' --many
    python3 -m mini_dudeai.nats_client req dudeclaw-01.tool_exec \
        '{"tool":"sensor_read","name":"chip_temp"}'
Server/token come from MINI_DUDEAI_NATS_SERVER / MINI_DUDEAI_NATS_TOKEN env
(token never on argv — argv is world-readable in ps).
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import time

DEFAULT_TIMEOUT_S = 5.0
DEFAULT_PORT = 4222
# Replies are small JSON tool results; anything near this is a wrong subject
# or a runaway peer, and slurping it unbounded on a Pi is worse than erroring.
MAX_PAYLOAD_BYTES = 1_000_000


class NatsError(Exception):
    """Transport/protocol failure talking to NATS. Callers convert this into
    their own honest degraded shape (source_error / Outcome ok=False)."""


def parse_server(server: str) -> tuple[str, int]:
    """Accept 'host', 'host:port', or 'nats://host:port' → (host, port)."""
    s = (server or "").strip()
    if not s:
        raise NatsError("empty NATS server address")
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.rstrip("/")
    host, sep, port = s.rpartition(":")
    if sep and port.isdigit():
        return host, int(port)
    return s, DEFAULT_PORT


class NatsConnection:
    """One short-lived NATS connection (context manager).

    Every socket operation is bounded by a deadline — a wedged server can
    cost at most timeout_s, never a hung tick (the #68 lesson, applied at
    write time).
    """

    def __init__(self, server: str, token: str | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.host, self.port = parse_server(server)
        self.token = token or None
        self.timeout_s = float(timeout_s)
        self._sock: socket.socket | None = None
        self._buf = b""
        self._next_sid = 1

    # -- lifecycle ----------------------------------------------------

    def __enter__(self) -> "NatsConnection":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout_s)
        except OSError as e:
            raise NatsError(
                f"connect {self.host}:{self.port} failed: "
                f"{type(e).__name__}: {e}") from e
        line = self._read_line(deadline)
        if not line.startswith(b"INFO "):
            self.close()
            raise NatsError(f"expected INFO, got {line[:80]!r}")
        connect_opts = {
            "verbose": False, "pedantic": False, "name": "mini-dudeai",
            "lang": "python-stdlib", "version": "0", "protocol": 0,
        }
        if self.token:
            connect_opts["auth_token"] = self.token
        self._send(b"CONNECT " + json.dumps(connect_opts).encode() + b"\r\n")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buf = b""

    # -- wire primitives ------------------------------------------------

    def _send(self, data: bytes) -> None:
        if self._sock is None:
            raise NatsError("connection is closed")
        try:
            self._sock.sendall(data)
        except OSError as e:
            raise NatsError(f"send failed: {type(e).__name__}: {e}") from e

    def _recv_into_buf(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NatsError(f"timed out after {self.timeout_s}s")
        if self._sock is None:
            raise NatsError("connection is closed")
        self._sock.settimeout(remaining)
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout as e:
            raise NatsError(f"timed out after {self.timeout_s}s") from e
        except OSError as e:
            raise NatsError(f"recv failed: {type(e).__name__}: {e}") from e
        if not chunk:
            raise NatsError("server closed the connection (EOF) — "
                            "wrong/missing auth token, or server restarting")
        self._buf += chunk

    def _read_line(self, deadline: float) -> bytes:
        while b"\r\n" not in self._buf:
            if len(self._buf) > MAX_PAYLOAD_BYTES:
                raise NatsError("control line exceeded payload cap")
            self._recv_into_buf(deadline)
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, n: int, deadline: float) -> bytes:
        if n > MAX_PAYLOAD_BYTES:
            raise NatsError(f"payload of {n} bytes exceeds cap {MAX_PAYLOAD_BYTES}")
        while len(self._buf) < n + 2:  # payload + trailing CRLF
            self._recv_into_buf(deadline)
        payload, self._buf = self._buf[:n], self._buf[n + 2:]
        return payload

    # -- protocol ------------------------------------------------------

    def _await_msg(self, sid: str, deadline: float) -> bytes:
        """Read frames until a MSG for `sid` arrives; answer PING inline."""
        while True:
            line = self._read_line(deadline)
            if line.startswith(b"MSG "):
                parts = line.decode("utf-8", "replace").split(" ")
                # MSG <subject> <sid> [reply-to] <nbytes>
                if len(parts) < 4:
                    raise NatsError(f"malformed MSG line: {line[:80]!r}")
                msg_sid, nbytes = parts[2], parts[-1]
                try:
                    payload = self._read_exact(int(nbytes), deadline)
                except ValueError:
                    raise NatsError(f"malformed MSG length: {line[:80]!r}")
                if msg_sid == sid:
                    return payload
                # a MSG for another sid on this short-lived conn is a protocol
                # surprise — skip it but keep waiting for ours
                continue
            if line == b"PING":
                self._send(b"PONG\r\n")
                continue
            if line in (b"PONG", b"+OK") or line.startswith(b"INFO "):
                continue
            if line.startswith(b"-ERR"):
                raise NatsError(f"server error: {line.decode('utf-8', 'replace')}")
            # unknown frame: surface, don't guess
            raise NatsError(f"unexpected frame: {line[:80]!r}")

    def request(self, subject: str, payload: str | bytes = b"",
                timeout_s: float | None = None):
        """Request/reply: returns the reply decoded as JSON when possible,
        else the raw text. Raises NatsError on any transport failure or
        timeout — no reply is an ERROR, never an empty success."""
        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        body = payload.encode() if isinstance(payload, str) else payload
        inbox = f"_INBOX.{secrets.token_hex(11)}"
        sid = str(self._next_sid)
        self._next_sid += 1
        self._send(f"SUB {inbox} {sid}\r\n".encode())
        self._send(f"PUB {subject} {inbox} {len(body)}\r\n".encode()
                   + body + b"\r\n")
        raw = self._await_msg(sid, deadline)
        text = raw.decode("utf-8", "replace")
        try:
            return json.loads(text)
        except ValueError:
            return text

    def request_many(self, subject: str, payload: str | bytes = b"",
                     wait_s: float | None = None) -> list:
        """Scatter-gather (e.g. `_ion.discover`): collect every reply that
        arrives within the window. Zero replies is a valid result for
        discovery (an empty network), NOT an error — callers that require a
        specific responder should use request()."""
        window = wait_s or self.timeout_s
        deadline = time.monotonic() + window
        body = payload.encode() if isinstance(payload, str) else payload
        inbox = f"_INBOX.{secrets.token_hex(11)}"
        sid = str(self._next_sid)
        self._next_sid += 1
        self._send(f"SUB {inbox} {sid}\r\n".encode())
        self._send(f"PUB {subject} {inbox} {len(body)}\r\n".encode()
                   + body + b"\r\n")
        replies: list = []
        while True:
            try:
                raw = self._await_msg(sid, deadline)
            except NatsError as e:
                if "timed out" in str(e):
                    return replies  # window closed — return what arrived
                raise
            text = raw.decode("utf-8", "replace")
            try:
                replies.append(json.loads(text))
            except ValueError:
                replies.append(text)

    def publish(self, subject: str, payload: str | bytes = b"") -> None:
        """Fire-and-forget publish (no reply expected)."""
        body = payload.encode() if isinstance(payload, str) else payload
        self._send(f"PUB {subject} {len(body)}\r\n".encode() + body + b"\r\n")


def request(server: str, subject: str, payload: str | bytes = b"", *,
            token: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S):
    """One-shot convenience: connect → request → close."""
    with NatsConnection(server, token=token, timeout_s=timeout_s) as nc:
        return nc.request(subject, payload)


def request_many(server: str, subject: str, payload: str | bytes = b"", *,
                 token: str | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> list:
    """One-shot scatter-gather: connect → request_many → close."""
    with NatsConnection(server, token=token, timeout_s=timeout_s) as nc:
        return nc.request_many(subject, payload)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="python3 -m mini_dudeai.nats_client",
        description="Bring-up helper: NATS request/reply without natscli. "
                    "Server/token from MINI_DUDEAI_NATS_SERVER / "
                    "MINI_DUDEAI_NATS_TOKEN env.")
    sub = p.add_subparsers(dest="cmd", required=True)
    req = sub.add_parser("req", help="request/reply on a subject")
    req.add_argument("subject")
    req.add_argument("payload", nargs="?", default="")
    req.add_argument("--server",
                     default=os.environ.get("MINI_DUDEAI_NATS_SERVER",
                                            "localhost:4222"))
    req.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    req.add_argument("--many", action="store_true",
                     help="collect all replies in the window (_ion.discover)")
    args = p.parse_args(argv)
    token = os.environ.get("MINI_DUDEAI_NATS_TOKEN") or None
    try:
        if args.many:
            out = request_many(args.server, args.subject, args.payload,
                               token=token, timeout_s=args.timeout)
        else:
            out = request(args.server, args.subject, args.payload,
                          token=token, timeout_s=args.timeout)
    except NatsError as e:
        print(f"nats: {e}")
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
