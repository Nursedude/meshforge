"""Shared kernel accept-queue probe + listener vocabulary for the backlog tests
(MAP-H H13). ONE copy: test_map_http_backlog and
test_listen_backlog_every_listener both import it (hfm #5). Not collected."""
from __future__ import annotations

import errno
import selectors
import socket
import sys
import time
from pathlib import Path

import pytest

BURST = 50


def _somaxconn():
    try:
        return int(Path("/proc/sys/net/core/somaxconn").read_text().split()[0])
    except (OSError, ValueError):
        return None


# The kernel caps any backlog at somaxconn: on a box (or CI image) that set it
# below the burst, the map is correct and the probe cannot show it — skip with
# the reason, never a red that blames the code (ambient state, pinned here).
_SOMAXCONN = _somaxconn()
# Applied to the KERNEL-PROBE tests only, never module-wide: a module skip
# also silenced the AST guards, which need no kernel (Fable review 2026-11-07).
_ON_LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"),
                               reason="accept-queue semantics measured on Linux (the fleet)")
_DEEP_ENOUGH = pytest.mark.skipif((_SOMAXCONN or 0) < BURST,
                                  reason=f"net.core.somaxconn={_SOMAXCONN} < {BURST}: the kernel caps the backlog")


def needs_accept_queue_probe(fn):
    return _ON_LINUX(_DEEP_ENOUGH(fn))


def _completed_connects(server, n=BURST, wait_s=0.5) -> int:
    """How many of n simultaneous connects the kernel completes while nobody accepts.
    selectors (epoll), not select(): a full-suite run can hand out fds >= 1024."""
    host, port = server.server_address[:2]
    socks, done = [], 0
    sel = selectors.DefaultSelector()
    try:
        for _ in range(n):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError as e:
                if e.errno in (errno.EMFILE, errno.ENFILE):
                    pytest.skip(f"fd limit below the {n}-connect burst ({e})")
                raise
            s.setblocking(False)
            rc = s.connect_ex((host, port))
            assert rc in (0, errno.EINPROGRESS), rc
            socks.append(s)
            sel.register(s, selectors.EVENT_WRITE)
        pending = len(socks)
        deadline = time.monotonic() + wait_s
        while pending and time.monotonic() < deadline:
            for key, _ in sel.select(timeout=max(0.0, deadline - time.monotonic())):
                sel.unregister(key.fileobj)
                pending -= 1
                if key.fileobj.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    done += 1
        return done
    finally:
        sel.close()
        for s in socks:
            s.close()


# Every stdlib server class a first-party listener could be built from.
STOCK_LISTENERS = frozenset({
    "HTTPServer", "ThreadingHTTPServer", "TCPServer", "ThreadingTCPServer",
    "ForkingTCPServer", "UnixStreamServer", "ThreadingUnixStreamServer",
    "UDPServer", "ThreadingUDPServer",
    # stdlib listener SUBCLASSES, also on queue 5 (Fable review 2026-11-07 #3)
    "WSGIServer", "SimpleXMLRPCServer", "DocXMLRPCServer",
    "make_server",  # wsgiref.simple_server.make_server() builds a WSGIServer
})
# Datagram servers have no accept queue: constructing one is flagged, but a
# subclass is not required to set request_queue_size.
STREAM_LISTENERS = STOCK_LISTENERS - {"UDPServer", "ThreadingUDPServer"}


def nm(node):
    return getattr(node, "id", None) or getattr(node, "attr", None)
