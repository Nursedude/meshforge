"""ONE process-wide guard for off-main-thread signal.signal() suppression.

RNS.Reticulum() and LXMF.LXMRouter() register SIGINT/SIGTERM handlers in
their constructors; Python only allows that from the main thread. Background
attaches (the bridge's RNS/LXMF boot, the broadcast bridge, the node
tracker's attach retry) therefore swap signal.signal for a no-op while they
construct.

Until 2026-10-09 there were three independent copies of that swap. Two
overlapping swaps interleave save/restore — A saves the real function, B
saves A's no-op, A restores the real one, B restores the NO-OP — and every
later signal.signal() in the process silently does nothing, including the
handlers bridge_cli re-installs for clean shutdown (review of the
node-tracker retry, 2026-10-09). This guard refcounts under a lock: the first
entrant saves the real function, the last one out restores it.
"""

import signal as _signal_mod
import threading
from contextlib import contextmanager

_lock = threading.Lock()
_depth = 0
_original = None


def _safe_signal(signalnum, handler):
    # Cannot register signal handlers from a non-main thread; report the
    # default disposition. The process's own shutdown handlers live on the
    # main thread and are installed outside any suppression window.
    return _signal_mod.SIG_DFL


@contextmanager
def suppress_signal_off_main():
    """No-op on the main thread; elsewhere, signal.signal is a no-op for the
    duration of the block. Safe to nest and to overlap across threads."""
    global _depth, _original
    if threading.current_thread() is threading.main_thread():
        yield
        return
    with _lock:
        if _depth == 0:
            _original = _signal_mod.signal
            _signal_mod.signal = _safe_signal
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0:
                _signal_mod.signal = _original
                _original = None
