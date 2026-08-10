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

  The sanctioned exception: a DELIBERATE ``except TransmitBlocked`` at a
  periodic or fallback send site, where a refusal means "skip this send" —
  the guard has already recorded and logged the attempt, so skipping is loud
  degradation, not a swallow, and the alternative is a daemon thread dying
  mid-bookkeeping (2026-08-09 review finding: the re-announce loop, the
  auto-ping thread and the MQTT downlink injector all died on a refusal,
  leaving ``_connected_rns`` true and stats stale). What stays banned is
  ACCIDENTAL absorption — ``except BaseException``/bare ``except`` around a
  send, which lint MF003 already refuses.
* Blocked attempts are RECORDED (:func:`blocked_attempts`). That makes the
  guard an instrument as well as a gate: a suite run enumerates exactly which
  hops would have keyed the radio, without keying it. A gate that has only
  ever produced one outcome is not evidence it works — so this one is drilled
  in ``tests/test_tx_guard.py`` AND reports what it caught.
* Arming is deliberately narrow. A false ARM in production is a silent mesh
  outage, which this project cares about at least as much as a stray
  transmission — so arming keys off pytest's own signals, and the decision is
  logged once so it is observable rather than assumed.

Scope: **Meshtastic RF egress** (the paths that key the LoRa radio) **and
RNS/LXMF egress** (announce + LXMF outbound), added 2026-08-09 at the
operator's direction — this domain has put considerable development into
RNS/LXMF and its blast radius is larger than Meshtastic's, not smaller.

Why RNS needed its own treatment rather than the same host:port allowlist:

* An RNS announce or LXMF send does not target a host — it targets **the
  Reticulum network the process is attached to**. On this fleet that network
  reaches public transport nodes AND a physical RNode on LoRa RF (moc3). One
  stray announce propagates across the whole network, so the blast radius is
  wider than a single channel on one radio.
* Measured 2026-08-09: the suite ALREADY attaches to the operator's live
  rnsd — `rns_init: listener preflight OK — @rns/<instance> owned by pid=...
  rnsd` appears in an ordinary e2e run. Nothing in the suite announced, but
  nothing prevented it either.
* So RNS gets a coarse on/off gate (:func:`assert_rns_tx_allowed`) plus a
  structural backstop at the init chokepoint — see :func:`rns_attach_allowed`.

The RNS backstop is deliberately shaped as "behave like a box with no rnsd":
``open_reticulum`` already returns None on a degraded/absent daemon and every
caller handles it, because that is exactly what CI's radio-less runners get.
Making the local suite take the same path removes an ambient-state dependency
(a test must not behave differently on a box that happens to run rnsd) rather
than inventing a new failure mode.
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
    "assert_rns_tx_allowed",
    "allow_rns_egress",
    "rns_egress_allowed",
    "rns_attach_allowed",
    "note_rns_attach_blocked",
    "RNS_TARGET",
    "assert_meshcore_tx_allowed",
    "allow_meshcore_egress",
    "meshcore_egress_allowed",
    "meshcore_attach_allowed",
    "note_meshcore_attach_blocked",
    "MESHCORE_TARGET",
    "probe_connect",
    "in_probe",
    "normalize_target",
    "DEFAULT_MESH_TCP_PORT",
    "UNRESOLVED_TARGET",
]

# meshtastic CLI / TCPInterface default port. The CLI takes only ``--host``,
# so this is the port that ``meshtastic --host X --sendtext`` actually keys.
DEFAULT_MESH_TCP_PORT = 4403

_ENV_ARM = "MESHFORGE_TX_GUARD"
#: Values of ``_ENV_ARM`` that DISARM the guard, compared lower-cased and
#: stripped. Anything else arms (fail-closed on an unknown). An EMPTY or
#: whitespace value is NOT a disarm word: ``MESHFORGE_TX_GUARD= pytest`` (a
#: shell typo) or a CI ``env:`` line whose variable is undefined sets the var
#: to "" — treating that as an explicit operator decision would silently
#: switch the whole stack off under pytest, re-opening the exact 2026-08-09
#: leak. Empty falls through to pytest detection. See is_armed().
_DISARM_VALUES = frozenset({
    "0", "false", "no", "off", "disabled", "none", "null",
})
_ENV_ALLOW = "MESHFORGE_TX_ALLOW"

_lock = threading.Lock()
_allowed: set = set()
_blocked: List[dict] = []
_BLOCKED_CAP = 500
_arm_logged = False
_disarm_logged = False


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
    "--request-telemetry",
    "--reply",
})


def _transmitting_flag_hit(token: str) -> Optional[str]:
    """The transmitting flag ``token`` invokes, or None.

    Matches every spelling argparse accepts, not just the exact two-token
    form (live-drilled 2026-08-09 against meshtastic 2.7.9 — both of these
    parse and transmit):

    * ``--sendtext=hello`` — the ``=`` form of any value-taking option.
    * ``--sendt`` — argparse ``allow_abbrev`` accepts any unambiguous PREFIX
      of a long option. We match ANY prefix: an ambiguous one makes argparse
      error out without transmitting, so over-matching there costs nothing,
      while under-matching is an unguarded send.
    """
    name = token.split("=", 1)[0]
    if name in TRANSMITTING_CLI_FLAGS:
        return name
    if name.startswith("--") and len(name) > 2:
        for flag in TRANSMITTING_CLI_FLAGS:
            if flag.startswith(name):
                return flag
    return None


def assert_cli_args_allowed(args, host: Optional[str] = None,
                            port: Optional[int] = None, *,
                            detail: str = "") -> None:
    """Guard a meshtastic CLI invocation by inspecting its ARGUMENTS.

    Guarding the shared runner rather than each caller means a new
    ``--traceroute`` call site is covered the day it is written, instead of
    the day someone remembers to guard it.
    """
    argv = [str(a) for a in (args or [])]
    hit = next(
        (h for h in (_transmitting_flag_hit(a) for a in argv) if h), None
    )
    if hit is None:
        return
    assert_tx_allowed(host, port or DEFAULT_MESH_TCP_PORT,
                      kind="meshtastic_cli",
                      detail=f"{detail} flag={hit}".strip())


# ---------------------------------------------------------------------------
# RNS / LXMF egress
# ---------------------------------------------------------------------------

#: Coarse target token for RNS egress. There is no host:port here — an announce
#: goes to the whole Reticulum network the process is attached to.
RNS_TARGET = "rns"

_rns_allowed = False


class allow_rns_egress:
    """Allowlist RNS/LXMF transmission for a test that means to exercise it.

    Coarse on/off by design: an announce has no per-destination target to
    allowlist — it floods the network the process is attached to. Granularity
    would be a comforting fiction.
    """

    def __init__(self):
        self._previous = False

    def __enter__(self) -> "allow_rns_egress":
        global _rns_allowed
        with _lock:
            self._previous = _rns_allowed
            _rns_allowed = True
        return self

    def __exit__(self, *exc) -> bool:
        global _rns_allowed
        with _lock:
            _rns_allowed = self._previous
        return False


def rns_egress_allowed() -> bool:
    with _lock:
        return _rns_allowed


def assert_rns_tx_allowed(*, kind: str, detail: str = "") -> None:
    """Refuse an RNS/LXMF transmission when armed and not allowlisted.

    Call at the TOP of every announce / LXMF-outbound site, before the router
    is touched. ``kind`` is e.g. ``"rns_announce"`` or ``"lxmf_outbound"``.

    Raises:
        TransmitBlocked: when armed and RNS egress is not allowlisted.
    """
    if not is_armed() or rns_egress_allowed():
        return
    record = {
        "target": RNS_TARGET,
        "kind": kind,
        "detail": detail,
        "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
    }
    with _lock:
        if len(_blocked) < _BLOCKED_CAP:
            _blocked.append(record)
    _raise_blocked(record)


def rns_attach_allowed() -> bool:
    """Whether this process may hold a LIVE Reticulum handle right now.

    The structural backstop under :func:`assert_rns_tx_allowed`: with no live
    instance there is nothing for an unguarded announce to transmit through.
    Consulted by ``utils.rns_init.open_reticulum`` — the chokepoint that MF019
    and ``TestRNSReticulumChokepoint`` already prove is the ONLY place a real
    ``RNS.Reticulum()`` is constructed, which is what makes one check enough.

    Returns True outside pytest — production is never gated, and that has to
    include the ARMED-BY-ENV drill: ``MESHFORGE_TX_GUARD=1`` on a live
    gateway must make the SEND sites refuse loudly, not sever the RNS attach
    — a reconnect loop getting None from open_reticulum forever is a silent
    RNS outage with a log line blaming pytest in a process running no pytest
    (2026-08-09 review finding). The attach backstop is test isolation, not
    part of the drill surface.
    """
    return (not is_armed()) or (not _under_pytest()) or rns_egress_allowed()


def note_rns_attach_blocked(configdir) -> None:
    """Record that a live RNS attach was declined under test.

    A refusal that leaves no artifact never happened (honest_failure_modes #9),
    and ``open_reticulum`` returning None is otherwise indistinguishable from
    the ordinary "no rnsd here" path.
    """
    record = {
        "target": RNS_TARGET,
        "kind": "rns_attach",
        "detail": f"open_reticulum declined under pytest (configdir={configdir})",
        "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
    }
    with _lock:
        if len(_blocked) < _BLOCKED_CAP:
            _blocked.append(record)
    logger.warning(
        "tx_guard: declined a LIVE RNS attach under pytest (configdir=%s) — "
        "open_reticulum returns None, the same path a box with no rnsd takes. "
        "Wrap in utils.tx_guard.allow_rns_egress() if this test means to use "
        "a real Reticulum.", configdir,
    )


# ---------------------------------------------------------------------------
# MeshCore companion-radio egress
# ---------------------------------------------------------------------------

#: Coarse target token for MeshCore egress. Same shape as RNS: the companion
#: radio hangs off serial (or TCP:4000) — there is no host:port the socket
#: tripwire could watch on serial, and the send keys a REAL LoRa radio. The
#: 2026-08-09 review found this second radio entirely outside the egress
#: architecture: no refusal, no record, no sweep coverage (MeshAnchor's
#: PRIMARY radio type — port this gate when landing there).
MESHCORE_TARGET = "meshcore"

_meshcore_allowed = False


class allow_meshcore_egress:
    """Allowlist MeshCore transmission for a test that means to exercise it.

    Coarse on/off like :class:`allow_rns_egress`: a serial companion has no
    per-destination target to allowlist.
    """

    def __init__(self):
        self._previous = False

    def __enter__(self) -> "allow_meshcore_egress":
        global _meshcore_allowed
        with _lock:
            self._previous = _meshcore_allowed
            _meshcore_allowed = True
        return self

    def __exit__(self, *exc) -> bool:
        global _meshcore_allowed
        with _lock:
            _meshcore_allowed = self._previous
        return False


def meshcore_egress_allowed() -> bool:
    with _lock:
        return _meshcore_allowed


def assert_meshcore_tx_allowed(*, kind: str, detail: str = "") -> None:
    """Refuse a MeshCore transmission when armed and not allowlisted.

    Call at the TOP of every MeshCore send site, before the companion is
    touched. In-process simulators are not egress and need no guard.

    Raises:
        TransmitBlocked: when armed and MeshCore egress is not allowlisted.
    """
    if not is_armed() or meshcore_egress_allowed():
        return
    record = {
        "target": MESHCORE_TARGET,
        "kind": kind,
        "detail": detail,
        "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
    }
    with _lock:
        if len(_blocked) < _BLOCKED_CAP:
            _blocked.append(record)
    _raise_blocked(record)


def meshcore_attach_allowed() -> bool:
    """Whether this process may open a LIVE MeshCore companion connection.

    Structural backstop with the same shape (and the same pytest-only scope)
    as :func:`rns_attach_allowed`: with no live companion handle there is
    nothing for an unguarded send to key, and an env-armed production drill
    must not sever the gateway's radio.
    """
    return (
        (not is_armed())
        or (not _under_pytest())
        or meshcore_egress_allowed()
    )


def note_meshcore_attach_blocked(target: str) -> None:
    """Record that a live MeshCore attach was declined under test
    (honest_failure_modes #9 — a refusal that leaves no artifact never
    happened)."""
    record = {
        "target": MESHCORE_TARGET,
        "kind": "meshcore_attach",
        "detail": f"MeshCore connect declined under pytest (target={target})",
        "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
    }
    with _lock:
        if len(_blocked) < _BLOCKED_CAP:
            _blocked.append(record)
    logger.warning(
        "tx_guard: declined a LIVE MeshCore attach under pytest (target=%s) "
        "— the handler stays disconnected, the same path a box with no "
        "companion radio takes. Wrap in utils.tx_guard.allow_meshcore_egress() "
        "if this test means to use a real companion.", target,
    )


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


#: Target token minted when a send's destination cannot be determined
#: (non-string host, unparseable port). It is refused UNCONDITIONALLY when
#: armed — no allowlist entry can ever name it. The old behaviour collapsed
#: unknowns to the local radio, which is the single most-allowlisted target in
#: the tree: an e2e harness that allowlisted 127.0.0.1:4403 for its mock
#: daemon thereby blessed every send whose destination the guard could not
#: even read (2026-08-09 review finding, fail-open in the unknown direction).
UNRESOLVED_TARGET = "<unresolved>"


def normalize_target(host: Optional[str], port: Optional[int]) -> str:
    """Canonical ``host:port`` string used for allowlisting and records.

    A non-string host or an unparseable port yields :data:`UNRESOLVED_TARGET`,
    which :func:`assert_tx_allowed` refuses unconditionally. A test that
    builds its config from a ``MagicMock`` otherwise mints a target like
    ``<MagicMock name='...host.strip()' id='140735842580672'>:1`` — unique per
    run, so no allowlist could ever name it and the refusal message advised
    something impossible. Refusing outright is the fail-closed reading: if we
    cannot tell where this send points, it may point ANYWHERE — including at
    the operator's radio — so no allowlist entry may cover it.

    ``host=None`` stays distinct from garbage: it is the documented "local
    radio" default that real call sites pass deliberately (the meshtastic CLI
    with no ``--host`` keys localhost), so it normalizes to loopback.
    """
    if host is not None and not isinstance(host, str):
        return UNRESOLVED_TARGET
    h = (host or "").strip() or "localhost"
    # Treat the loopback spellings as one target so a harness that allowlists
    # 127.0.0.1 is not defeated by a caller that says "localhost".
    if h in ("localhost", "127.0.0.1", "::1", "[::1]"):
        h = "127.0.0.1"
    # isinstance, NOT try/int(): a MagicMock defines __int__ and answers 1, so
    # a try/except around int() silently produced "127.0.0.1:1" — a plausible
    # target that was never asked for. Caught by the drill, not by review.
    if port is None or (isinstance(port, str) and not port.strip()):
        return f"{h}:{DEFAULT_MESH_TCP_PORT}"
    if isinstance(port, bool) or not isinstance(port, (int, str)):
        return UNRESOLVED_TARGET
    try:
        p = int(port)
    except (TypeError, ValueError):
        return UNRESOLVED_TARGET
    return f"{h}:{p}"


def _parse_allowlist_target(t) -> str:
    """Parse ONE allowlist entry, refusing to guess on a malformed port.

    The old inline parse used ``int(port) if port.isdigit() else None``, so a
    typo'd entry like ``'127.0.0.1:9x43'`` silently resolved to the DEFAULT
    port — 4403, the operator's real radio. A typo must never mint permission
    for the one target this guard exists to protect (honest_failure_modes #3:
    validators reject what the author cannot have meant).

    A bare host with no ``:`` keeps the documented convenience of defaulting
    to the meshtastic TCP port — that is an omission, not a malformation.
    """
    entry = str(t).strip()
    if not entry:
        raise ValueError("tx_guard allowlist entry is empty")
    host, sep, port = entry.rpartition(":")
    if not sep:
        target = normalize_target(entry, None)
    elif not port.isdigit():
        raise ValueError(
            f"tx_guard allowlist entry {entry!r}: port {port!r} is not "
            f"numeric — refusing to guess (a typo must not allowlist the "
            f"default radio port)"
        )
    else:
        target = normalize_target(host, int(port))
    if target == UNRESOLVED_TARGET:
        raise ValueError(f"tx_guard allowlist entry {entry!r} does not parse")
    return target


def _under_pytest() -> bool:
    """Whether this process is actually running under pytest.

    ``PYTEST_CURRENT_TEST`` is pytest's own per-item signal and is the
    tightest tell, but it is unset during collection and module import —
    ``pytest`` in ``sys.modules`` covers that window. Production daemons do
    not import pytest.
    """
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


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

    ⚠️ The disarm vocabulary is matched CASE-INSENSITIVELY and covers every
    spelling an operator plausibly means by "off". It used to be the literal
    tuple ``("0", "", "false", "False", "no")``, which armed on ``FALSE``,
    ``NO``, ``off``, ``OFF``, ``disabled``, ``none`` and ``null`` (measured
    2026-08-09, all seven). That asymmetry pointed the WRONG way: on a live box
    ARMED is the dangerous state, because a blocked send is a silent mesh
    outage, and this variable's whole purpose is the deliberate-disarm path
    someone reaches for while standing at a radio. An unrecognised value still
    ARMS — fail-closed is right for an unknown — but a value that plainly reads
    as "off" must not be the unrecognised one.

    ⚠️ An EMPTY (or whitespace) value is nobody's decision — it is what a
    shell typo (``MESHFORGE_TX_GUARD= pytest``) or an undefined CI variable
    produces. It falls through to pytest detection instead of disarming
    (2026-08-09 review finding: "" in the disarm set silently switched the
    whole stack off with no witness). And because DISARMED-under-pytest is
    the dangerous state on a radio box, an explicit disarm while pytest is
    detected is logged loudly once — the state must leave a witness.
    """
    global _arm_logged, _disarm_logged
    raw = os.environ.get(_ENV_ARM)
    under_pytest = _under_pytest()
    if raw is not None and raw.strip():
        armed = raw.strip().lower() not in _DISARM_VALUES
    else:
        armed = under_pytest
    if armed and not _arm_logged:
        _arm_logged = True
        logger.warning(
            "tx_guard ARMED — RF egress is fail-closed; only explicitly "
            "allowlisted targets may transmit (%s=%r)", _ENV_ARM, raw
        )
    if not armed and under_pytest and not _disarm_logged:
        _disarm_logged = True
        logger.warning(
            "tx_guard DISARMED under pytest by %s=%r — this process can key "
            "the operator's radio. Only do this deliberately, standing at "
            "the box (2026-08-09).", _ENV_ARM, raw
        )
    return armed


_env_malformed_warned: set = set()


def _env_allowed() -> set:
    raw = os.environ.get(_ENV_ALLOW, "")
    out = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(_parse_allowlist_target(chunk))
        except ValueError:
            # Fail CLOSED (the entry allowlists nothing) but leave a witness —
            # the operator who typo'd MESHFORGE_TX_ALLOW otherwise sees only a
            # confusing block on the target they thought they had allowed.
            if chunk not in _env_malformed_warned:
                _env_malformed_warned.add(chunk)
                logger.warning(
                    "tx_guard: ignoring malformed %s entry %r — it allowlists "
                    "NOTHING (port must be numeric)", _ENV_ALLOW, chunk
                )
            continue
    return out


def set_allowed_targets(targets: Iterable[str]) -> None:
    """Replace the in-process allowlist with ``targets`` (``host:port``).

    Raises:
        ValueError: on a malformed entry — a typo must not silently resolve
            to the default (real radio) port.
    """
    parsed = [_parse_allowlist_target(t) for t in targets]
    with _lock:
        _allowed.clear()
        _allowed.update(parsed)


def clear_allowed_targets() -> None:
    with _lock:
        _allowed.clear()


class allow_targets:
    """Context manager allowlisting one or more ``host:port`` targets.

    Used by the e2e harness, whose mock daemon binds an ephemeral port known
    only at runtime — so the allowlist has to be set in-process, not via env.
    """

    def __init__(self, *targets: str):
        # Parse at CONSTRUCTION so a typo'd entry raises where it was written,
        # not when (or whether) the context is entered.
        self._targets = [_parse_allowlist_target(t) for t in targets]
        self._previous: set = set()

    def __enter__(self) -> "allow_targets":
        with _lock:
            self._previous = set(_allowed)
            _allowed.update(self._targets)
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
    if target != UNRESOLVED_TARGET:
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
    if target == UNRESOLVED_TARGET:
        raise TransmitBlocked(
            f"RF egress refused by tx_guard: kind={kind} target={target}. "
            f"The destination of this send could not be determined (non-string "
            f"host or unparseable port — often a MagicMock leaking into config), "
            f"and an unresolvable target is refused UNCONDITIONALLY: no "
            f"allowlist entry can cover a send that may point anywhere. Fix the "
            f"test to hand the code a real host:port "
            f"(see src/utils/tx_guard.py). test={record['test']} {detail}"
        )
    raise TransmitBlocked(
        f"RF egress refused by tx_guard: kind={kind} target={target}. "
        f"This process is running under pytest and {target} is not in the "
        f"harness allowlist. If this is a mock daemon, wrap the send in "
        f"utils.tx_guard.allow_targets('{target}'). If you meant to key a "
        f"real radio, that is the bug this guard exists to stop "
        f"(see src/utils/tx_guard.py). test={record['test']} {detail}"
    )
