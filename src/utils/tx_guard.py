"""RF egress chokepoint — the antenna's equivalent of the suite's DB gates.

Born 2026-08-09, after the full pytest suite transmitted a test fixture
(``[RNS:abc] retry with bytes``) onto a live statewide public channel from a
fleet radio box. The suite reported 10,535 passed while keying the air, and
the operator found out by looking at his phone — not from a probe, a test, or
CI.

The structural finding: this suite has THREE session-scoped autouse gates
isolating **databases** (``_isolate_node_cache_files``,
``_isolate_operator_data_stores``, ``_isolate_delivery_counters_db``) and had
NONE isolating **egress**. We fenced the disk and left the antenna open.

How the leak worked, and why "point the test at a mock" was not enough: the
e2e harness pointed the bridge at a mock daemon on an ephemeral loopback port
and then deliberately stopped it to force send failures. The primary hop
(``send_text_direct``) duly failed against the dead port — and the handler's
FALLBACK chain then reached the real radio two different ways, neither of
which consults the mock's address at all:

  1. ``get_protobuf_client()`` — a module singleton built from
     ``ProtobufTransportConfig()`` DEFAULTS, i.e. ``localhost:9443``.
  2. ``_send_via_cli`` — ``meshtastic --host 127.0.0.1`` → TCP 4403.

Both are the operator's real meshtasticd. **Loopback is not safety on a radio
box**, which is also why an autouse "block non-loopback sockets" fixture would
not have prevented this.

So the gate goes at the egress sites themselves, and it is fail-closed:
under pytest, a transmit to a target the harness has not explicitly
allowlisted raises. Nothing is sent, and the attempt is recorded.

Design notes worth keeping:

* ``TransmitBlocked`` derives from :class:`BaseException`, NOT ``Exception``.
  Every egress site in this tree sits inside a broad ``except Exception``
  (they must: a downed radio is not a crash). A guard those handlers can
  swallow is not a guard — it would degrade silently back into a send. This
  is the same reason ``KeyboardInterrupt`` and ``SystemExit`` sit off the
  ``Exception`` branch.
* Blocked attempts are RECORDED (:func:`blocked_attempts`). That makes the
  guard an instrument as well as a gate: a suite run enumerates exactly which
  hops would have keyed the radio, without keying it. A gate that has only
  ever produced one outcome is not evidence it works — so this one is drilled
  in ``tests/test_tx_guard.py`` AND reports what it caught.
* Arming is deliberately narrow. A false ARM in production is a silent mesh
  outage, which this project cares about at least as much as a stray
  transmission — so arming keys off pytest's own signals, and the decision is
  logged once so it is observable rather than assumed.

Scope, stated honestly: this guards **Meshtastic RF egress** — the paths that
key the LoRa radio. It does NOT currently guard RNS/LXMF transmission or MQTT
publishes. Those are separate egress surfaces; see the module TODO.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "TransmitBlocked",
    "assert_tx_allowed",
    "assert_cli_args_allowed",
    "TRANSMITTING_CLI_FLAGS",
    "assert_iface_tx_allowed",
    "allow_targets",
    "set_allowed_targets",
    "clear_allowed_targets",
    "blocked_attempts",
    "clear_blocked_attempts",
    "is_armed",
    "probe_connect",
    "in_probe",
    "normalize_target",
    "DEFAULT_MESH_TCP_PORT",
]

# meshtastic CLI / TCPInterface default port. The CLI takes only ``--host``,
# so this is the port that ``meshtastic --host X --sendtext`` actually keys.
DEFAULT_MESH_TCP_PORT = 4403

_ENV_ARM = "MESHFORGE_TX_GUARD"
_ENV_ALLOW = "MESHFORGE_TX_ALLOW"

_lock = threading.Lock()
_allowed: set = set()
_blocked: List[dict] = []
_BLOCKED_CAP = 500
_arm_logged = False


class TransmitBlocked(BaseException):
    """Raised when a guarded RF egress call is refused.

    Derives from :class:`BaseException` on purpose — see the module docstring.
    Every send site in this tree is wrapped in ``except Exception``; a guard
    those handlers could absorb would silently degrade into the very send it
    exists to prevent.
    """


# meshtastic CLI flags that put a packet on the air. Config reads/writes
# (--info, --set, --export-config) touch the device but do NOT key the radio,
# so they stay off this list — a guard that fires on everything gets disabled.
#
# ⚠️ Closed-enum hazard (honest_failure_modes #7): this set is consulted by
# `assert_cli_args_allowed`, and a NEW transmitting flag added to the CLI is
# invisible to it until listed here. `tests/test_tx_guard.py` pins the set.
TRANSMITTING_CLI_FLAGS = frozenset({
    "--sendtext",
    "--sendping",
    "--traceroute",
    "--request-position",
    "--reply",
})


def assert_cli_args_allowed(args, host: Optional[str] = None,
                            port: Optional[int] = None, *,
                            detail: str = "") -> None:
    """Guard a meshtastic CLI invocation by inspecting its ARGUMENTS.

    Guarding the shared runner rather than each caller means a new
    ``--traceroute`` call site is covered the day it is written, instead of
    the day someone remembers to guard it.
    """
    argv = [str(a) for a in (args or [])]
    hit = next((a for a in argv if a in TRANSMITTING_CLI_FLAGS), None)
    if hit is None:
        return
    assert_tx_allowed(host, port or DEFAULT_MESH_TCP_PORT,
                      kind="meshtastic_cli",
                      detail=f"{detail} flag={hit}".strip())


_probe_state = threading.local()


class probe_connect:
    """Declare that a connection is a REACHABILITY PROBE, not a transmission.

    A probe opens a socket to answer "is this port open?" and closes it
    without writing. That is not egress, but an in-process socket patch cannot
    tell the two apart — so the prober declares itself.

    Needed because the tripwire has to keep blocking plain ``connect``:
    meshtastic's ``TCPInterface`` reaches the radio through
    ``socket.create_connection`` -> ``sock.connect``, and that is the unguarded
    path the tripwire exists to catch. ``connect_ex`` is the probe idiom (it
    returns an errno rather than raising, which is its whole purpose) and is
    permitted-but-recorded; this context manager covers the probes that use
    ``connect``/``create_connection`` instead.

    Thread-local: probes run on daemon threads, and one thread declaring a
    probe must not excuse another thread's transmission.
    """

    def __enter__(self) -> "probe_connect":
        _probe_state.depth = getattr(_probe_state, "depth", 0) + 1
        return self

    def __exit__(self, *exc) -> bool:
        _probe_state.depth = max(0, getattr(_probe_state, "depth", 0) - 1)
        return False


def in_probe() -> bool:
    """True when this thread is inside a declared :class:`probe_connect`."""
    return getattr(_probe_state, "depth", 0) > 0


def normalize_target(host: Optional[str], port: Optional[int]) -> str:
    """Canonical ``host:port`` string used for allowlisting and records.

    Non-string hosts and non-integer ports collapse to the LOCAL RADIO rather
    than being rendered as-is. A test that builds its config from a
    ``MagicMock`` otherwise mints a target like
    ``<MagicMock name='...host.strip()' id='140735842580672'>:1`` — unique per
    run, so no allowlist could ever name it and the refusal message advised
    something impossible. Collapsing is also the safe reading: if we cannot
    tell where this send points, assume it points at the operator's radio.
    """
    if not isinstance(host, str):
        host = None
    h = (host or "").strip() or "localhost"
    # Treat the loopback spellings as one target so a harness that allowlists
    # 127.0.0.1 is not defeated by a caller that says "localhost".
    if h in ("localhost", "127.0.0.1", "::1", "[::1]"):
        h = "127.0.0.1"
    # isinstance, NOT try/int(): a MagicMock defines __int__ and answers 1, so
    # a try/except around int() silently produced "127.0.0.1:1" — a plausible
    # target that was never asked for. Caught by the drill, not by review.
    if isinstance(port, bool) or not isinstance(port, (int, str)):
        port = None
    try:
        p = int(port) if port else DEFAULT_MESH_TCP_PORT
    except (TypeError, ValueError):
        p = DEFAULT_MESH_TCP_PORT
    return f"{h}:{p}"


def is_armed() -> bool:
    """True when the guard should refuse un-allowlisted transmissions.

    Arming precedence:
      1. ``MESHFORGE_TX_GUARD=1`` forces ARMED (use to drill on a live box).
      2. ``MESHFORGE_TX_GUARD=0`` forces DISARMED (a deliberate live send from
         within a test process — must be an explicit operator act).
      3. Otherwise: armed iff we are running under pytest.

    ``PYTEST_CURRENT_TEST`` is pytest's own per-item signal and is the tightest
    tell, but it is unset during collection and module import — where an
    import-time send would be just as loud on the air. ``pytest`` in
    ``sys.modules`` covers that window. Production daemons do not import pytest.
    """
    global _arm_logged
    raw = os.environ.get(_ENV_ARM)
    if raw is not None:
        armed = raw.strip() not in ("0", "", "false", "False", "no")
    else:
        armed = (
            "PYTEST_CURRENT_TEST" in os.environ
            or "pytest" in sys.modules
        )
    if armed and not _arm_logged:
        _arm_logged = True
        logger.warning(
            "tx_guard ARMED — RF egress is fail-closed; only explicitly "
            "allowlisted targets may transmit (%s=%r)", _ENV_ARM, raw
        )
    return armed


def _env_allowed() -> set:
    raw = os.environ.get(_ENV_ALLOW, "")
    out = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            host, _, port = chunk.rpartition(":")
            try:
                out.add(normalize_target(host, int(port)))
            except ValueError:
                continue
        else:
            out.add(normalize_target(chunk, DEFAULT_MESH_TCP_PORT))
    return out


def set_allowed_targets(targets: Iterable[str]) -> None:
    """Replace the in-process allowlist with ``targets`` (``host:port``)."""
    with _lock:
        _allowed.clear()
        for t in targets:
            host, _, port = str(t).rpartition(":")
            _allowed.add(normalize_target(host or t, int(port) if port.isdigit() else None))


def clear_allowed_targets() -> None:
    with _lock:
        _allowed.clear()


class allow_targets:
    """Context manager allowlisting one or more ``host:port`` targets.

    Used by the e2e harness, whose mock daemon binds an ephemeral port known
    only at runtime — so the allowlist has to be set in-process, not via env.
    """

    def __init__(self, *targets: str):
        self._targets = [str(t) for t in targets]
        self._previous: set = set()

    def __enter__(self) -> "allow_targets":
        with _lock:
            self._previous = set(_allowed)
            for t in self._targets:
                host, _, port = t.rpartition(":")
                _allowed.add(
                    normalize_target(host or t, int(port) if port.isdigit() else None)
                )
        return self

    def __exit__(self, *exc) -> bool:
        with _lock:
            _allowed.clear()
            _allowed.update(self._previous)
        return False


def blocked_attempts() -> List[dict]:
    """Every transmission this guard refused, newest last.

    This is the instrument half: after a suite run it names the exact hops
    that would have keyed the radio. Capped at ``_BLOCKED_CAP`` entries.
    """
    with _lock:
        return list(_blocked)


def clear_blocked_attempts() -> None:
    with _lock:
        _blocked.clear()


def assert_tx_allowed(
    host: Optional[str],
    port: Optional[int] = None,
    *,
    kind: str,
    detail: str = "",
) -> None:
    """Refuse an RF transmission to an un-allowlisted target when armed.

    Call this at the TOP of every send site, before any socket is opened or
    any subprocess spawned — and OUTSIDE that site's ``try/except``, so the
    refusal cannot be absorbed into a "send failed, fall back" path.

    Args:
        host: destination host for the send.
        port: destination port; defaults to the meshtastic TCP port.
        kind: which egress path is asking (e.g. ``"http_toradio"``,
            ``"meshtastic_cli"``, ``"tcp_sendtext"``). Appears in the record.
        detail: free-text context for the record (caller, payload preview).

    Raises:
        TransmitBlocked: when armed and the target is not allowlisted.
    """
    if not is_armed():
        return

    target = normalize_target(host, port)
    with _lock:
        allowed = target in _allowed
    if not allowed and target in _env_allowed():
        allowed = True
    if allowed:
        return

    record = {
        "target": target,
        "kind": kind,
        "detail": detail,
        "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
    }
    with _lock:
        if len(_blocked) < _BLOCKED_CAP:
            _blocked.append(record)

    _raise_blocked(record)


def assert_iface_tx_allowed(iface, *, kind: str, detail: str = "",
                            default_host: Optional[str] = None) -> None:
    """Guard a send made through a meshtastic interface object.

    The interface already knows where it is pointed, so the call sites stay
    one line. ``TCPInterface`` exposes ``hostname``; a serial//dev radio has
    no host at all, and for guard purposes "the local radio" is exactly the
    thing we must not key by accident — so it normalizes to loopback.
    """
    # Only accept a REAL str/int off the interface. A test double answers
    # every attribute with another mock, which would mint an unpredictable
    # target like "<MagicMock id=...>:4403" that no allowlist could ever name.
    # Falling back to the local radio is both predictable and the safe
    # assumption: if we cannot tell where this interface points, treat it as
    # pointing at the operator's radio.
    host = None
    for attr in ("hostname", "address"):
        value = getattr(iface, attr, None)
        if isinstance(value, str) and value:
            host = value
            break
    if host is None and isinstance(default_host, str) and default_host:
        host = default_host

    port = getattr(iface, "portNumber", None)
    if not isinstance(port, int):
        port = DEFAULT_MESH_TCP_PORT

    assert_tx_allowed(host, port, kind=kind, detail=detail)


def _raise_blocked(record: dict) -> None:
    logger.error(
        "tx_guard BLOCKED an RF transmission: kind=%s target=%s test=%s %s",
        record["kind"], record["target"], record["test"], record["detail"],
    )
    kind, target, detail = record["kind"], record["target"], record["detail"]
    raise TransmitBlocked(
        f"RF egress refused by tx_guard: kind={kind} target={target}. "
        f"This process is running under pytest and {target} is not in the "
        f"harness allowlist. If this is a mock daemon, wrap the send in "
        f"utils.tx_guard.allow_targets('{target}'). If you meant to key a "
        f"real radio, that is the bug this guard exists to stop "
        f"(see src/utils/tx_guard.py). test={record['test']} {detail}"
    )
