"""Total-deadline bounded read for HTTP response bodies.

``urllib``'s ``urlopen(timeout=)`` is a PER-SOCKET-OPERATION timeout — it fires
only on a TOTAL stall (no bytes for ``timeout`` seconds). A server that trickles
a large body STEADILY (each ``recv`` arriving within the socket timeout) reads
UNBOUNDED: 2026-06-26 a slow ``map.meshcore.dev`` took 533 s on a single
``resp.read(64MB)`` and held the federator's cold collect at HTTP 503 for ~9 min.

``bounded_read`` caps the TOTAL wall-clock with a MONOTONIC deadline
(clock-step-immune — the fleet's RTC-less Pis step NTP, which would corrupt a
``time.time()`` deadline) and raises ``TimeoutError`` when exceeded.
``TimeoutError`` is an ``OSError`` subclass, so the existing
``except (URLError, OSError, …)`` handler around every external map fetch catches
it for free and serves stale/empty rather than hanging the collect.

Shared by every external map source that reads a potentially-large body
(``_map_collector_meshcore`` + the ``_map_collector_public`` fallbacks) so the
"slow source wedges the cold collect" class is fixed once, not per-instance.
"""

from __future__ import annotations

import time
from typing import List


def bounded_read(resp, max_seconds: float,
                 cap: int = 64 * 1024 * 1024,
                 chunk: int = 256 * 1024) -> bytes:
    """Read up to ``cap`` bytes from ``resp`` (a file-like with ``.read(n)``),
    bounded by a total monotonic deadline of ``max_seconds``.

    Raises ``TimeoutError`` if the deadline is exceeded mid-read — the caller's
    existing ``OSError`` handler then serves stale/empty instead of hanging.
    ``cap`` guards an unbounded body (default 64 MB; the largest known source,
    ~12 MB MeshCore, fits whole and an 8 MB cap would truncate mid-JSON).
    """
    if max_seconds <= 0:
        raise ValueError(f"max_seconds must be positive, got {max_seconds!r}")
    deadline = time.monotonic() + max_seconds
    buf: List[bytes] = []
    total = 0
    while total < cap:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"bounded_read exceeded {max_seconds}s total deadline after "
                f"{total // 1024} KB (slow/trickling source)"
            )
        c = resp.read(chunk)
        if not c:
            break
        buf.append(c)
        total += len(c)
    return b"".join(buf)
