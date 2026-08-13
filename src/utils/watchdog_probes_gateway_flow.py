"""Watchdog probes — gateway delivery-flow observers & exercisers.

Split out of ``watchdog_probes_gateway.py`` 2026-07-14 (MF025 size cap). Holds
the synth-soak, gateway-delivery-degraded, resource-canary, and oracle-delivery
probes — the 2026-06 delivery-flow observers (journal / log / exerciser-output
watchers), distinct from the #63/#74 delivery-counter core that stays in
watchdog_probes_gateway. Import via the ``utils.watchdog_probes`` hub;
watchdog_probes_gateway re-exports these for back-compat.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import List, Optional, Tuple

from utils.user_units import timer_wants_dirs as _shared_wants_dirs
from utils.watchdog_probe_core import (
    Signal,
    _journal_count_match,
    _resolve_main_pid_status,
    _short_unix_ts,
    note_disposition,
    note_unit_presence_gate,
)

logger = logging.getLogger("watchdog")


# ─────────────────────────────────────────────────────────────────────
# Probe: synth soak degraded / silent (2026-06-15)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_SYNTH_SOAK_DEBOUNCE_PATH = "/var/lib/meshforge/synth_soak_debounce.json"
DEFAULT_PROPAGATION_SOAK_DEBOUNCE_PATH = (
    "/var/lib/meshforge/propagation_soak_debounce.json")
# Consecutive-INDETERMINATE envelope counter (keyed by envelope filename, so
# each fire counts once, not once per watchdog tick) — review F2's absorbing
# state: without this, a node refusing every upload, or a box that can never
# finish a stamp, read "indeterminate" forever with every dashboard green.
DEFAULT_PROPAGATION_SOAK_INDET_PATH = (
    "/var/lib/meshforge/propagation_soak_indeterminate.json")

# The propagation soak fires hourly (meshforge-propagation-soak.timer
# OnCalendar=*:37:00). Same ~2.5-cadence staleness bar as the synth soak: two
# missed runs, so one slow/skipped fire never false-alarms.
_PROPAGATION_SOAK_CADENCE_S = 3600.0
_PROPAGATION_SOAK_STALE_AFTER_S = 9000.0

# The synth soak fires hourly (meshforge-synth-soak.timer OnCalendar=*:07:00).
# Treat it as DARK only after ~2.5 cadences with no fresh result — two missed
# runs, so a single skipped/slow fire (RandomizedDelaySec, a long run, a
# Persistent=true catch-up after brief downtime) never false-alarms.
_SYNTH_SOAK_CADENCE_S = 3600.0
_SYNTH_SOAK_STALE_AFTER_S = 9000.0


def _resolve_operator_home() -> Optional[str]:
    """The operator's home dir, root-context safe. None when unresolvable."""
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        return pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None


def _resolve_operator_state_dir(leaf: str) -> Optional[str]:
    """``~<operator>/.local/state/meshforge/<leaf>``, root-context safe.

    Shared by the soak probes: these exercisers run as the operator's
    systemd --user timers, so their output lives under the operator home. The
    watchdog (sandboxed root) derives that home from the operator UID and reads
    it directly, NEVER escalating (the rns_version_drift / cron_verdict lesson).
    None when no operator user is resolvable — indeterminate, never a false
    alarm.
    """
    home = _resolve_operator_home()
    if not home:
        return None
    return os.path.join(home, ".local", "state", "meshforge", leaf)


# The unit whose existence IS the declaration that this box runs the soak on a
# cadence (2026-08-09). See _timer_enrolled.
SYNTH_SOAK_TIMER_UNIT = "meshforge-synth-soak.timer"


def _timer_wants_dirs() -> Optional[List[str]]:
    """The dirs that record user-timer enablement — None if no operator.

    Kept as an injectable seam for the tests (the probe takes
    ``timer_wants_dirs=``); the layout itself is owned by ``utils.user_units``.
    """
    home = _resolve_operator_home()
    if not home:
        return None
    # Discovery is itself tri-state: None means the user-unit root exists but
    # could not be listed. Passing that through as None keeps _timer_enrolled
    # unobservable rather than letting it answer "not enabled".
    return _shared_wants_dirs(home)


def _timer_enrolled(unit: str, wants_dirs: Optional[List[str]]) -> Optional[bool]:
    """Tri-state: is ``unit`` enabled? True / False / None (unobservable).

    Absent is a real observation (the enable-symlink is simply not there);
    a listing that fails for any reason OTHER than "not there" is UNKNOWN and
    must never be flattened into "not enabled" (honest_failure_modes #1).

    Deliberately takes the dirs rather than a home, so a caller can inject
    them; ``scripts/provision_role.py`` asks the same question through
    ``utils.user_units.user_timer_enrolled``. Both resolve "enabled" from the
    same place — two consumers, one definition (honest_failure_modes #5).
    """
    if wants_dirs is None:
        return None
    unknown = False
    for d in wants_dirs:
        try:
            if unit in os.listdir(d):
                return True
        except (FileNotFoundError, NotADirectoryError):
            continue  # absent dir is an observation, not a blind spot
        except OSError:
            unknown = True
    return None if unknown else False


def _resolve_synth_soak_dir() -> Optional[str]:
    """The operator's ``synth_soak`` state dir (see _resolve_operator_state_dir)."""
    return _resolve_operator_state_dir("synth_soak")


def _resolve_propagation_soak_dir() -> Optional[str]:
    """The operator's ``propagation_soak`` state dir."""
    return _resolve_operator_state_dir("propagation_soak")


def _load_synth_streak(state_path: str) -> int:
    """Read the consecutive-degraded streak. Any error → 0 (favour silence on
    uncertainty — a missing/garbage state suppresses a first-seen fire)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_synth_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def _newest_synth_file(state_dir: str,
                      prefix: str = "synth-") -> Optional[Tuple[str, float]]:
    """``(path, mtime)`` of the newest ``<prefix>*.json`` in ``state_dir``, else None.

    None distinguishes 'no output exists' (inert — box never produced a
    result) from a stale-but-present file (the silence signal). A listing error
    after the dir existed is a transient race → None (caller holds the streak).

    ``prefix`` is parameterised so the propagation soak (``prop-*.json``)
    reuses this rather than growing a near-identical copy that would drift
    (honest_failure_modes #5: two consumers of one artifact share ONE
    implementation). Default preserves the synth soak's behaviour exactly.
    """
    try:
        entries = [
            e for e in os.listdir(state_dir)
            if e.startswith(prefix) and e.endswith(".json")
        ]
    except OSError:
        return None
    # Tie-break equal mtimes by NAME (stamp-named files sort chronologically),
    # never by listdir order: strict `>` used to let the filesystem's
    # directory-hash order pick the winner among same-mtime files, which is
    # per-box/per-VM state — CI 3.11 flaked on exactly that 2026-08-07 while
    # 3.9 and local passed the identical tree.
    newest_path: Optional[str] = None
    newest_key: Tuple[float, str] = (-1.0, "")
    for name in entries:
        p = os.path.join(state_dir, name)
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if (m, name) > newest_key:
            newest_key = (m, name)
            newest_path = p
    if newest_path is None:
        return None
    return newest_path, newest_key[0]


def _worst_synth_pair(pair_results) -> Optional[str]:
    """Compact ``<user>-><peer> ok/samples (N% fail)`` for the worst pair.

    None when pair_results is absent/empty/misshaped or nothing failed
    (never raises — a degraded summary must not itself crash the probe)."""
    if not isinstance(pair_results, list):
        return None
    worst = None
    worst_fail = -1.0
    for pr in pair_results:
        if not isinstance(pr, dict):
            continue
        try:
            fail = float(pr.get("fail_pct", 0) or 0)
        except (TypeError, ValueError):
            continue
        if fail > worst_fail:
            worst_fail = fail
            worst = pr
    if worst is None or worst_fail <= 0:
        return None
    return (
        f"{worst.get('user', '?')}->{worst.get('peer', '?')} "
        f"{worst.get('ok', '?')}/{worst.get('samples', '?')} ok "
        f"({worst_fail:.0f}% fail)"
    )


def probe_synth_soak_degraded(
    *,
    state_dir: Optional[str] = None,
    now: Optional[float] = None,
    stale_after_s: float = _SYNTH_SOAK_STALE_AFTER_S,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
    timer_wants_dirs: Optional[List[str]] = None,
) -> Optional[Signal]:
    """The synth soak's delivery envelope failed, or the soak went DARK.

    The hourly LXMF multi-user synth soak (``meshforge-synth-soak.timer`` ->
    ``lab_synth_soak_fire.sh``) exercises the gateway's REAL round-trip delivery
    path and writes a pass/fail envelope per run — but the fire script always
    exits 0 and nothing consumed the result, so a delivery regression (envelope
    below its ok-ratio threshold) OR the timer going silent was invisible to the
    fleet. This closes that gap: the synth canary is now itself watched.

    Two legs, ``degraded`` only — a synth dip is a warning, the gateway may still
    be serving real traffic, and queue_backlog / delivery_confirmation_stall /
    delivery_write_canary own the hard-failure surface:

      - SILENCE: newest ``synth-*.json`` older than ``stale_after_s`` (~2.5x the
        hourly cadence) — the exerciser stopped (timer dead, fire script broken,
        box wedged). Here silence IS the failure mode (a fixed-cadence generator
        going quiet is unambiguous — the inverse of delivery_confirmation_stall).
        **Gated on the timer actually being enabled here** (2026-08-09): the
        staleness bar is DERIVED from ``OnCalendar=*:07:00``, so with no timer
        enrolled there is no cadence for the output to have fallen behind, and
        the age of a leftover artifact means nothing. See below.
      - ENVELOPE: newest result has ``pass_envelope`` false — round-trip delivery
        dropped below the run's ok-ratio threshold; the worst pair is surfaced.

    Honest-failure self-guards (favour silence on uncertainty):
      - state dir unresolvable / absent → None (INERT: this box doesn't run the
        synth soak — the common case; unobservable != degraded).
      - state dir PRESENT but ``meshforge-synth-soak.timer`` not enabled here →
        None (INERT). 2026-08-09: meshanchor-server sat ``degraded`` for 78 days
        blaming a timer **that has never existed on that box** — the artifacts
        were seven hand-fired lab runs from May (irregular clock times, the
        first one a ``ModuleNotFoundError``), not a cadence that stopped. The
        old inert test — "state dir absent" — is a PROXY for "does this box run
        the soak", and a box that ran the exerciser by hand once fails the proxy
        forever. The enable-symlink is the actual declaration, so ask it. This
        deliberately does NOT widen the artifact test to swallow a
        stale-but-present dir: a box WITH the timer still fires on silence.
      - wants-dir unreadable → indeterminate (held). Cannot tell whether the
        soak is scheduled here; unknown is never "not scheduled".
      - no ``synth-*.json`` present → None (never ran / freshly installed) — held,
        distinct from a stale present file which fires.
      - newest file unreadable/garbage → a degraded candidate, but RIDDEN OUT by
        the debounce: a torn mid-write file is whole again by the next 30s tick,
        so only a persistently-unreadable result fires.
      - ``pass_envelope`` absent on a parseable fresh file → indeterminate (held;
        neither fires nor resets) — a shape regression must not read as healthy.
      - a candidate must persist ``debounce_ticks`` consecutive ticks before
        firing; only an explicit healthy+fresh observation resets the streak;
        indeterminate observations HOLD it.
    """
    import time as _time
    now = _time.time() if now is None else now

    sdir = state_dir or _resolve_synth_soak_dir()
    if not sdir:
        # G2 (2026-07-18): sdir is ALSO None when the operator user is
        # unresolvable (early-boot / no user bus) — on a soak-running box
        # that window must not read "doesn't run it". Mirrors the oracle
        # probe's split below.
        note_disposition("synth_soak_degraded", "indeterminate",
                         reason="operator unresolvable — state dir unknown")
        return None
    if not os.path.isdir(sdir):
        note_disposition("synth_soak_degraded", "inert",
                         reason="synth-soak state dir absent — box doesn't run it")
        return None  # INERT: box doesn't run the synth soak

    sp = debounce_path or DEFAULT_SYNTH_SOAK_DEBOUNCE_PATH

    newest = _newest_synth_file(sdir)
    if newest is None:
        note_disposition("synth_soak_degraded", "indeterminate",
                         reason="no synth result file yet / listing race — held")
        return None  # no result file / transient listing race — hold streak

    newest_path, newest_mtime = newest
    age = now - newest_mtime
    extra: dict = {"newest": os.path.basename(newest_path), "age_s": round(age, 1)}

    candidate_detail: Optional[str] = None
    definitively_healthy = False

    if age > stale_after_s:
        # The staleness bar is derived from the TIMER's cadence, so it only
        # means anything where the timer is enabled. Ask the declaration
        # (the enable-symlink), not the artifacts (2026-08-09).
        enrolled = _timer_enrolled(
            SYNTH_SOAK_TIMER_UNIT,
            _timer_wants_dirs() if timer_wants_dirs is None else timer_wants_dirs)
        if enrolled is False:
            note_disposition(
                "synth_soak_degraded", "inert",
                reason=(f"{SYNTH_SOAK_TIMER_UNIT} not enabled here — no cadence "
                        f"to fall behind; the synth-*.json present are leftovers "
                        f"from manual runs, not a stopped organ"))
            return None
        if enrolled is None:
            note_disposition(
                "synth_soak_degraded", "indeterminate",
                reason=(f"cannot read timers.target.wants — unable to tell "
                        f"whether {SYNTH_SOAK_TIMER_UNIT} is scheduled here; "
                        f"DARK leg held"))
            return None
        candidate_detail = (
            f"synth soak went DARK: newest result is {age / 3600.0:.1f}h old "
            f"(cadence ~1h) — the LXMF round-trip exerciser stopped producing "
            f"output. Check meshforge-synth-soak.timer (systemd --user) + its "
            f"fire log."
        )
    else:
        try:
            with open(newest_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            candidate_detail = (
                f"synth soak newest result unreadable "
                f"({os.path.basename(newest_path)}) — the run wrote no "
                f"parseable envelope."
            )
        elif doc.get("pass_envelope") is True:
            definitively_healthy = True
        elif doc.get("pass_envelope") is False:
            ok_ratio = doc.get("ok_ratio")
            threshold = doc.get("ok_ratio_threshold")
            total_ok = doc.get("total_ok")
            total = doc.get("total_samples")
            worst = _worst_synth_pair(doc.get("pair_results"))
            extra.update({
                "ok_ratio": ok_ratio, "ok_ratio_threshold": threshold,
                "total_ok": total_ok, "total_samples": total,
                "worst_pair": worst,
            })
            try:
                ratio_s = f"{float(ok_ratio):.2f}" if ok_ratio is not None else "?"
            except (TypeError, ValueError):
                ratio_s = "?"
            candidate_detail = (
                f"synth soak FAILED its delivery envelope: ok_ratio={ratio_s} "
                f"(threshold {threshold}); {total_ok}/{total} round-trips OK"
                + (f". Worst pair: {worst}" if worst else "")
                + ". Gateway/LXMF round-trip delivery is degrading — check RNS "
                "paths to the fan-out peers + /api/gateway/delivery drop_reasons."
            )
        # else: pass_envelope absent/None on a parseable file → indeterminate
        #       (held below — neither a fire candidate nor a healthy reset).

    if candidate_detail is not None:
        streak = _load_synth_streak(sp) + 1
        _save_synth_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("synth_soak_degraded", "indeterminate",
                             reason="degraded candidate held by debounce")
            return None
        return Signal(
            cls="synth_soak_degraded",
            subject="meshforge-gateway",
            severity="degraded",
            detail=candidate_detail,
            extra=extra,
        )

    if definitively_healthy:
        _save_synth_streak(sp, 0)  # explicit healthy → reset the streak
        note_disposition("synth_soak_degraded", "clean")
    else:
        note_disposition("synth_soak_degraded", "indeterminate",
                         reason="pass_envelope absent on parseable envelope")
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: gateway delivery degraded (2026-06-20; the gateway-reliability
# arc A2 — OUTCOME-based monitoring, not shape-enumeration)
# ─────────────────────────────────────────────────────────────────────
#
# The spine was SHAPE-based (probes for KNOWN failure shapes) + LIVENESS-
# based. The 2026-06-20 wx-total-loss was a NEW shape (RNS Resource EROFS:
# the gateway, a shared-instance RNS client, tried to write an assembled
# multi-chunk Resource under /etc/reticulum/storage/resources/ but
# ProtectSystem=strict omitted that path → [Errno 30] EROFS → the reply was
# silently dropped while the gateway read "active / RNS: connected"). The
# error HAD a witness — the EROFS line was in moc's journal the whole time —
# but NO probe consumed it (honest_failure_modes #9 at the spine level).
# Enumerating shapes is a treadmill. A2's lever is to prove the gateway DOES
# ITS JOB from its OWN self-report, shape-agnostically:
#
#   Leg 1 (delivery gap): the att/del/drop counter block bridge_cli prints
#     to the journal every 30s. A WINDOWED delivered/attempted ratio (delta
#     across the window, NOT lifetime — recent-sensitive, the operator's
#     "things fall silent") below a conservative floor with real volume.
#   Leg 2 (RNS error-spike): a count of the gateway's own RNS resource/
#     forward error lines (EROFS / resource-assembly / forward-to-secondary)
#     — the witness class that today had no consumer. This is the leg that
#     directly catches the EROFS shape: those failures happen during RNS
#     Resource assembly, BEFORE the message reaches the att/del counters, so
#     leg 1 cannot see them — only the error channel can.
#
# Deliberately additive, not a duplicate: probe_delivery_confirmation_stall
# judges the CLEAN reason-split confirmation rate of CONFIRMABLE protocols
# (RNS only, recent-ring) via the API; probe_delivery_write_canary watches
# the SQLite write health; probe_queue_backlog watches depth/dead-letter.
# A2 watches the GROSS journal self-report (both directions, Meshtastic
# included) + the RNS error channel none of those see.

# Matches the att/del/drop line in BOTH bridge_cli formats — single-bridge
# ("  attempted/delivered/dropped — M->R: a/d/x  R->M: a/d/x") and
# multi-bridge ("      att/del/drop — M->R: a/d/x  R->M: a/d/x"). The
# slash triplet after "M->R:" is the discriminator: the "Messages bridged:
# N (M->R: a, R->M: b)" line uses a COMMA, so it never matches. ERE for
# journalctl -g.
GATEWAY_DELIVERY_BLOCK_GREP = r"M->R: [0-9]+/[0-9]+/[0-9]+"
_GATEWAY_DELIVERY_BLOCK_RE = re.compile(
    r"M->R:\s*(\d+)/(\d+)/(\d+)\s+R->M:\s*(\d+)/(\d+)/(\d+)")

# The RNS error-channel witnesses (ERE for journalctl -g). EROFS is the
# 2026-06-20 wx class; the other two are the adjacent resource/forward
# failure shapes. Deliberately concrete strings — NOT a bare "Resource"
# match, which would false-fire on benign "Resource" log lines.
GATEWAY_RNS_ERROR_GREP = (
    r"EROFS|Error while assembling received resource|"
    r"Failed to forward to secondary")

DEFAULT_GATEWAY_DELIVERY_STATE_PATH = (
    "/var/lib/meshforge/gateway_delivery_debounce.json")


def _parse_delivery_block(
    line: str,
) -> Optional[Tuple[float, int, int, int, int, int, int]]:
    """Parse one ``-o short-unix`` att/del/drop journal line.

    Returns ``(ts, m2r_att, m2r_del, m2r_drop, r2m_att, r2m_del, r2m_drop)``
    or None when the epoch or the six counters don't parse (a torn line, a
    format that doesn't match) — None is dropped by the caller, never read as
    a zeroed block.
    """
    ts = _short_unix_ts(line)
    if ts is None:
        return None
    m = _GATEWAY_DELIVERY_BLOCK_RE.search(line)
    if m is None:
        return None
    try:
        n = [int(x) for x in m.groups()]
    except (ValueError, TypeError):
        return None
    return (ts, n[0], n[1], n[2], n[3], n[4], n[5])


def _gateway_delivery_blocks(
    unit: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[List[Tuple[float, int, int, int, int, int, int]]]:
    """All att/del/drop counter blocks for ``unit`` within ``lookback``.

    Returns the parsed block list (``[]`` = the gateway printed no att/del
    block in the window — idle / just-started, a genuine *observed* state),
    or **None** on journalctl unavailable / timeout / rc∉(0,1) — the honest
    *unobservable* answer. The caller must never read None as ``[]`` (empty ≠
    error — honest_failure_modes #1), or a journalctl wedge would mask the
    very delivery collapse this measures.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", GATEWAY_DELIVERY_BLOCK_GREP, "-o", "short-unix",
                "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    out = proc.stdout
    if not out:
        return []
    blocks: List[Tuple[float, int, int, int, int, int, int]] = []
    for ln in out.splitlines():
        if not ln:
            continue
        parsed = _parse_delivery_block(ln)
        if parsed is not None:
            blocks.append(parsed)
    return blocks


def _window_delivery_gap(
    blocks: List[Tuple[float, int, int, int, int, int, int]],
    *,
    min_volume: int,
    ratio_floor: float,
) -> List[Tuple[str, int, int, float]]:
    """Per-direction windowed delivered/attempted gap.

    Returns ``[(label, d_att, d_del, ratio), ...]`` for each direction whose
    WINDOWED delivery (newest counter minus oldest in the window) fell below
    ``ratio_floor`` with at least ``min_volume`` attempts — the recent-drop
    lens, not the lifetime-cumulative one (which would mask a fresh collapse
    on a long-uptime box). A counter going BACKWARD across the window means
    the gateway restarted mid-window (counters are in-memory); the earliest
    baseline is then taken as zero so we measure since-the-restart rather than
    reading a bogus negative delta.

    Needs ≥2 blocks to form a delta; fewer → ``[]`` (can't judge — the caller
    treats that as *no finding*, not *healthy*, and the volume gate keeps a
    quiet box silent regardless).

    NOTE (calibrated): the journal exposes only the TOTAL dropped count, which
    on the Mesh→RNS direction folds in benign best-effort broadcast-to-no-peer
    misses alongside real failures (RNS→Mesh dropped is clean — failures only).
    That is why the floor is conservative (a true majority-failure collapse,
    far below any benign-broadcast steady state) and why the precise,
    reason-split moderate-gap detection is delivery_confirmation_stall's job,
    not this leg's. Leg 1 is the gross-collapse backstop.
    """
    if len(blocks) < 2:
        return []
    ordered = sorted(blocks, key=lambda b: b[0])
    earliest, latest = ordered[0], ordered[-1]
    findings: List[Tuple[str, int, int, float]] = []
    # tuple indices: ts=0; M->R att/del/drop = 1/2/3; R->M att/del/drop = 4/5/6
    for label, att_i, del_i in (("Mesh->RNS", 1, 2), ("RNS->Mesh", 4, 5)):
        att_l, del_l = latest[att_i], latest[del_i]
        att_e, del_e = earliest[att_i], earliest[del_i]
        if att_l < att_e:                 # counter reset → measure since reset
            base_att, base_del = 0, 0
        else:
            base_att, base_del = att_e, del_e
        d_att = att_l - base_att
        d_del = del_l - base_del
        if d_att < min_volume:
            continue
        ratio = max(0.0, min(1.0, d_del / d_att))
        if ratio < ratio_floor:
            findings.append((label, d_att, d_del, ratio))
    return findings


def _load_gateway_delivery_streak(state_path: str) -> int:
    """Consecutive-candidate streak; any error → 0 (favour silence)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_gateway_delivery_streak(state_path: str, streak: int) -> None:
    """Persist the debounce streak (atomic-rename, never raises).

    A persistent write failure pins the streak below the debounce floor → the
    probe would silently never fire during a real collapse, so a swallowed
    OSError leaves a WITNESS in the watchdog journal (honest_failure_modes #9).
    """
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as exc:
        logger.warning(
            "gateway_delivery_degraded: could not persist debounce streak to "
            "%s (%s) — the probe may not advance past its debounce floor; "
            "check %s is writable.",
            state_path, exc, os.path.dirname(state_path) or state_path,
        )


def probe_gateway_delivery_degraded(
    *,
    unit: str = "meshforge-gateway.service",
    lookback: str = "30min",
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    blocks_fn=None,
    error_count_fn=None,
    min_volume: int = 20,
    ratio_floor: float = 0.50,
    error_degraded_n: int = 3,
    error_wedge_n: int = 10,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when the gateway's OWN self-report shows it is NOT delivering —
    the gateway-reliability arc A2 (2026-06-20). OUTCOME monitoring: prove the
    gateway does its job, don't enumerate how it fails.

    Two legs, max severity wins (see the module-level block comment for the
    arc rationale and the EROFS-class origin):

    * **Leg 1 — delivery gap** (degraded): the windowed delivered/attempted
      ratio from the att/del/drop block ``bridge_cli`` prints every 30s falls
      below ``ratio_floor`` (default 0.50) with ≥ ``min_volume`` attempts in
      the window — a recent, high-volume collapse. Conservative by design
      (the journal's total-dropped count folds in benign Mesh→RNS broadcast
      misses; the precise moderate-gap lens is delivery_confirmation_stall).
    * **Leg 2 — RNS error-spike** (degraded ≥ ``error_degraded_n``, wedge ≥
      ``error_wedge_n``): the count of the gateway's own RNS resource/forward
      error lines (EROFS / resource-assembly / forward-to-secondary) in the
      window. THIS is the leg that catches the 2026-06-20 EROFS shape, whose
      failures never reach the att/del counters (they fail during Resource
      assembly, before the message becomes bridgeable).

    Self-guards (honest_failure_modes):

    * gateway absent or stopped on this box (``_resolve_main_pid_status`` →
      ``absent``/``down``) → None (INERT — the "does this box run the
      gateway" gate; moc/moc3 only). A unit state we could not READ is
      ``indeterminate`` instead (2026-08-12): unobservable ≠ "no gateway".
    * BOTH legs unobservable (journalctl wedged/absent for the block fetch AND
      the error count) → None, HOLDING the debounce streak — unobservable ≠
      healthy, and a journalctl hiccup must not erase a real in-progress
      signal (#1/#2). An OBSERVED-clean tick (≥1 leg read, nothing crossed a
      threshold) resets the streak; a candidate must persist ``debounce_ticks``
      consecutive ticks before firing, so a torn block / one slow window can't
      flap it. Never raises.

    Recovery: read the gateway journal for the failing destination /
    ``EROFS``; an EROFS spike is the #60 sandbox class — confirm
    ``meshforge-gateway.service`` ``ReadWritePaths`` includes
    ``/etc/reticulum/storage`` (the 2026-06-20 fix).
    """
    try:
        sp = state_path or DEFAULT_GATEWAY_DELIVERY_STATE_PATH

        if main_pid is not None:
            gw_status, gw_pid = "ok", main_pid
        else:
            gw_status, gw_pid = _resolve_main_pid_status(
                unit, systemctl_path=systemctl_path)
        if gw_pid is None:
            # 2026-08-12, same split as its sibling in watchdog_probes_gateway:
            # absent/stopped are inert (service_inactive owns a dead unit); a
            # systemctl we could not run is unobservable, not "no gateway".
            # Policy in ONE place (2026-08-12 review).
            note_unit_presence_gate(
                "gateway_delivery_degraded", gw_status,
                stopped_is_inert=True,
                absent_reason=f"gateway not running on this box ({gw_status})",
                unresolved_reason=(f"{unit} state unobservable; cannot tell "
                                   f"whether a gateway organ exists here"))
            return None  # INERT (or indeterminate on unknown): see gate

        if blocks_fn is None:
            def blocks_fn():
                return _gateway_delivery_blocks(
                    unit, lookback, journalctl_path=journalctl_path)
        if error_count_fn is None:
            def error_count_fn():
                return _journal_count_match(
                    unit, GATEWAY_RNS_ERROR_GREP, lookback,
                    journalctl_path=journalctl_path)

        blocks = blocks_fn()           # Optional[List]: None=unobservable
        err = error_count_fn()         # Optional[int]: None=unobservable

        if blocks is None and err is None:
            # Fully unobservable — hold the streak (do NOT reset to a healthy
            # 0, do NOT fire). honest_failure_modes #2.
            note_disposition("gateway_delivery_degraded", "indeterminate",
                             reason="journalctl unobservable for both legs")
            return None

        findings: List[Tuple[str, str]] = []  # (severity, fragment)
        extra: dict = {
            "lookback": lookback, "min_volume": min_volume,
            "ratio_floor": ratio_floor,
            "error_degraded_n": error_degraded_n,
            "error_wedge_n": error_wedge_n,
        }

        # Leg 1 — windowed delivery gap (needs ≥2 blocks; observed = blocks
        # is not None, i.e. journalctl worked).
        if blocks is not None:
            for label, d_att, d_del, ratio in _window_delivery_gap(
                blocks, min_volume=min_volume, ratio_floor=ratio_floor
            ):
                findings.append((
                    "degraded",
                    f"{label} delivered {d_del}/{d_att} ({ratio:.0%}) over the "
                    f"last {lookback}",
                ))
                extra[f"gap_{label.replace('->', '_')}"] = {
                    "attempted": d_att, "delivered": d_del,
                    "ratio": round(ratio, 3),
                }

        # Leg 2 — RNS error-channel spike (the EROFS catcher).
        if err is not None:
            extra["rns_error_count"] = err
            if err >= error_degraded_n:
                sev = "wedge" if err >= error_wedge_n else "degraded"
                findings.append((
                    sev,
                    f"{err} RNS resource/forward errors (EROFS / "
                    f"resource-assembly / forward-to-secondary) in the last "
                    f"{lookback}",
                ))

        if not findings:
            # Observed at least one leg and nothing crossed a threshold →
            # reset the debounce streak (explicit healthy observation).
            _save_gateway_delivery_streak(sp, 0)
            if blocks is not None and err is not None:
                note_disposition("gateway_delivery_degraded", "clean")
            else:
                note_disposition("gateway_delivery_degraded", "indeterminate",
                                 reason="one journal leg unobservable this tick")
            return None

        streak = min(_load_gateway_delivery_streak(sp) + 1, debounce_ticks)
        _save_gateway_delivery_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("gateway_delivery_degraded", "indeterminate",
                             reason="degraded candidate held by debounce")
            return None

        severity = "wedge" if any(s == "wedge" for s, _ in findings) else "degraded"
        extra["debounce_streak"] = streak
        return Signal(
            cls="gateway_delivery_degraded",
            subject="meshforge-gateway",
            severity=severity,
            detail=(
                "Gateway self-report shows degraded delivery: "
                + "; ".join(frag for _, frag in findings)
                + ". OUTCOME monitor (gateway-reliability arc A2) — the gateway "
                "may read 'active / RNS: connected' while replies silently "
                "drop. Check the gateway journal for the failing destination + "
                "any EROFS lines; an EROFS spike is the #60 sandbox class — "
                "confirm meshforge-gateway.service ReadWritePaths includes "
                "/etc/reticulum/storage (the 2026-06-20 fix)."
            ),
            extra=extra,
        )
    except Exception:
        note_disposition("gateway_delivery_degraded", "indeterminate",
                         reason="probe raised — unobservable this tick")
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: resource canary degraded / silent (2026-06-20; the gateway-
# reliability arc A1 — the OUTCOME source of truth)
# ─────────────────────────────────────────────────────────────────────
#
# A2 (above) consumes the gateway's OWN self-report; A1 is the active
# exerciser that PROVES the gateway delivers a RESOURCE-sized round-trip —
# the multi-chunk RNS Resource path the 2026-06-20 wx-total-loss EROFS broke
# while single-packet replies kept working (so every shape/liveness probe and
# the single-packet gateway_rt_canary read green). src/lab/gateway_resource_canary
# fires a control PING + a PINGBIG whose reply is resource-sized, on a timer,
# and writes a verdict envelope (last.json); this probe consumes it. The
# canary's own FAIL verdict — "control back, resource NOT" — is the EROFS
# signature; the probe simply surfaces it (and the canary going DARK) into
# mini/+/fleet, the same "the canary itself must be watched" pattern as
# synth_soak_degraded. degraded only: a resource-canary dip is a warning, and
# gateway_delivery_degraded / delivery_confirmation_stall own the gateway's
# self-reported hard-failure surface.

# The verdict-envelope dir leaf, kept byte-identical to the canary's
# STATE_DIR_LEAF and the wrapper's STATE_DIR default (TestStateDirContract pins
# the trio — honest_failure_modes #5: two consumers of one path WILL drift).
RESOURCE_CANARY_STATE_LEAF = "gateway_resource_canary"
DEFAULT_RESOURCE_CANARY_DEBOUNCE_PATH = (
    "/var/lib/meshforge/resource_canary_debounce.json")

# The canary fires hourly (meshforge-gateway-resource-canary.timer
# OnCalendar=*:43:00). DARK only after ~2.5 cadences with no fresh envelope —
# two missed fires, so one slow/skipped run never false-alarms.
_RESOURCE_CANARY_CADENCE_S = 3600.0
_RESOURCE_CANARY_STALE_AFTER_S = 9000.0


def _resolve_resource_canary_dir() -> Optional[str]:
    """The operator's resource-canary state dir, root-context safe.

    The canary runs as the operator's systemd --user timer, so its envelope
    lives under the operator home — the sandboxed-root watchdog derives that
    home from the operator UID and reads it directly, never escalating (the
    same pattern as _resolve_synth_soak_dir). None when no operator user is
    resolvable (indeterminate — never a false alarm)."""
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        home = pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None
    return os.path.join(home, ".local", "state", "meshforge",
                        RESOURCE_CANARY_STATE_LEAF)


def _load_resource_canary_streak(state_path: str) -> int:
    """Read the consecutive-candidate streak. Any error → 0 (favour silence)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_resource_canary_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_resource_canary_degraded(
    *,
    state_dir: Optional[str] = None,
    now: Optional[float] = None,
    stale_after_s: float = _RESOURCE_CANARY_STALE_AFTER_S,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """The gateway resource round-trip canary FAILED, or went DARK.

    The hourly resource canary (meshforge-gateway-resource-canary.timer →
    gateway_resource_canary.py) drives a control PING + a PINGBIG whose reply is
    RESOURCE-sized through the real gateway path and writes ``last.json``. This
    probe consumes it — closing the same "the canary itself is unwatched" gap
    synth_soak_degraded closed for the LXMF soak.

    Two legs, ``degraded`` only:

      - SILENCE: ``last.json`` older than ``stale_after_s`` (~2.5x the hourly
        cadence) — the exerciser stopped (timer dead, fire script broken, box
        wedged). Silence IS the failure mode for a fixed-cadence canary.
      - VERDICT: the envelope's ``verdict`` is FAIL or CONCERN — the canary's
        own honest classification. A FAIL whose reason carries "EROFS
        signature" (control back, resource NOT) is the 2026-06-20 class.

    Honest-failure self-guards (favour silence on uncertainty):
      - state dir unresolvable / absent → None (INERT: this box doesn't run the
        canary — the common case; unobservable != degraded).
      - no ``last.json`` present → None (never ran / freshly installed) — held,
        distinct from a stale present file which fires.
      - file unreadable/garbage → a degraded candidate RIDDEN OUT by the
        debounce (a torn mid-write is whole by the next tick — though the canary
        writes atomically, so this should never happen).
      - ``verdict`` OK on a fresh file → definitively healthy (resets streak).
      - ``verdict`` absent/unknown on a parseable fresh file → indeterminate
        (held; neither fires nor resets — a shape regression must not read as
        healthy).
      - a candidate must persist ``debounce_ticks`` consecutive ticks before
        firing; only an explicit healthy+fresh observation resets the streak.
    """
    import time as _time
    now = _time.time() if now is None else now

    sdir = state_dir or _resolve_resource_canary_dir()
    if not sdir:
        # G2 (2026-07-18): sdir None = operator unresolvable (early-boot /
        # no user bus), NOT a positively-observed absence. Mirrors the
        # oracle probe's split.
        note_disposition("resource_canary_degraded", "indeterminate",
                         reason="operator unresolvable — state dir unknown")
        return None
    if not os.path.isdir(sdir):
        note_disposition("resource_canary_degraded", "inert",
                         reason="canary state dir absent — box doesn't run it")
        return None  # INERT: box doesn't run the resource canary

    envelope = os.path.join(sdir, "last.json")
    try:
        mtime = os.path.getmtime(envelope)
    except OSError:
        note_disposition("resource_canary_degraded", "indeterminate",
                         reason="no last.json yet / transient race — held")
        return None  # no last.json yet (never fired) / transient race — hold

    sp = debounce_path or DEFAULT_RESOURCE_CANARY_DEBOUNCE_PATH
    age = now - mtime
    extra: dict = {"age_s": round(age, 1)}

    candidate_detail: Optional[str] = None
    definitively_healthy = False

    if age > stale_after_s:
        candidate_detail = (
            f"resource canary went DARK: last.json is {age / 3600.0:.1f}h old "
            f"(cadence ~1h) — the gateway resource round-trip exerciser stopped "
            f"producing a verdict. Check "
            f"meshforge-gateway-resource-canary.timer (systemd --user) + its "
            f"fire log."
        )
    else:
        try:
            with open(envelope, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            candidate_detail = (
                "resource canary newest envelope unreadable (last.json) — "
                "the fire wrote no parseable verdict."
            )
        else:
            verdict = doc.get("verdict")
            extra.update({
                "verdict": verdict,
                "seq": doc.get("seq"),
                "reply_bytes": doc.get("reply_bytes"),
                "control_back": doc.get("control_back"),
                "resource_back": doc.get("resource_back"),
                "peer": doc.get("peer"),
            })
            if verdict == "OK":
                definitively_healthy = True
            elif verdict in ("FAIL", "CONCERN"):
                reason = doc.get("reason") or "(no reason recorded)"
                candidate_detail = (
                    f"resource round-trip canary {verdict}: {reason}"
                )
            # else: verdict absent/unknown on a parseable file → indeterminate
            #       (held below — neither a fire candidate nor a healthy reset).

    if candidate_detail is not None:
        streak = min(_load_resource_canary_streak(sp) + 1, debounce_ticks)
        _save_resource_canary_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("resource_canary_degraded", "indeterminate",
                             reason="degraded candidate held by debounce")
            return None
        extra["debounce_streak"] = streak
        return Signal(
            cls="resource_canary_degraded",
            subject="meshforge-gateway",
            severity="degraded",
            detail=candidate_detail,
            extra=extra,
        )

    if definitively_healthy:
        _save_resource_canary_streak(sp, 0)  # explicit healthy → reset streak
        note_disposition("resource_canary_degraded", "clean")
    else:
        note_disposition("resource_canary_degraded", "indeterminate",
                         reason="verdict absent/unknown on parseable envelope")
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: oracle delivery degraded (2026-06-22; the mesh-oracle health
# leg). The read-only "ask dude-AI over the mesh" responder (src/oracle)
# answers a NOC-state query over the mesh and appends one JSONL audit
# record per handled query to ~/.local/share/meshforge/mesh_oracle_log.jsonl
# (oracle.oracle_log_path; rotates at 2 MB). It had NO automated probe — a
# blind spot for a service whose whole ethos is "silence is the failure
# mode." v1 watches the DELIVERY-RATE leg only — the unambiguous one — over
# a recent ts window:
#
#     rate = delivered_true / (delivered_true + real_failures)
#
# real_failures counts ONLY records whose `reason` marks a real send
# EXCEPTION (reason startswith "send_error"). Three buckets are
# DELIBERATELY EXCLUDED from the rate — each would false-alarm:
#   - declines (reason in {cooldown, not_allowlisted}): the oracle CORRECTLY
#     refused (rate-limit / not on the allowlist), not a failure — THE trap
#     (honest_failure_modes #1).
#   - benign non-delivery (delivered=false, NO send_error reason): the RNS
#     leg's no-path-to-an-unannounced-ephemeral-identity (a 32-hex hash with
#     no announce) + the MeshCore first-send-post-restart race. Expected,
#     not defects. Counted + surfaced in `extra`, never hidden (#9).
#
# WITNESSED v1 blind spot: the RNS leg's send_fn (bridge_send_mixin.
# send_to_rns) catches real send EXCEPTIONS internally and returns a bare
# False, so an RNS *send error* lands in the benign bucket, not send_error —
# v1 cannot yet tell an RNS no-path from an RNS crash. Closing that needs
# send_to_rns to distinguish the two (a change to the LIVE RNS send path,
# deliberately deferred out of the mf.5 RNS-fork soak). The Meshtastic /
# MQTT / MeshCore legs DO surface send_error (their send_fn lets the
# exception reach the responder), so v1 already covers 3 of the 4 legs; the
# benign-bucket count makes the RNS gap visible rather than silent.
#
# degraded ONLY (a low oracle rate is a warning — the oracle is read-only,
# low-traffic, operator-test-only today; the hard delivery surface is owned
# by gateway_delivery_degraded / synth_soak / resource_canary). Fires when
# rate < threshold over the last N confirmable answers AND at least one of the
# failures is FRESH (else pass@small-N noise and stale-history pages; #6/#2).
# Silence (no queries) is NOT a failure for a reactive service — nobody asked
# — so there is no silence leg (a v2 silence leg ties to channel_feed_dark,
# not a naive "no log for Xh" that false-alarms every quiet night). INERT
# (None) off a box where the oracle never wrote a log (disabled / never
# queried), and INERT with the measurement in the reason when the sample is
# stale. Reads the operator home directly (root-context safe); 2-tick
# debounce. Mirrors synth_soak_degraded.
#
# ⚠️ v2 (2026-08-08) replaced the 6h TIME window with a COUNT window + a
# staleness guard. v1's gate (≥8 confirmable inside 6h) could not be met by
# the fleet's only oracle box — moc3 answers ~5 queries a month, in bursts
# weeks apart — so the probe was structurally unable to judge anything. The
# guard, not the window, is what keeps old evidence from being spoken about
# as current health.

# COUNT window, not time window (2026-08-08) — see probe_oracle_delivery_degraded.
_ORACLE_SAMPLE_N = 8                         # rate over the last 8 confirmable answers
_ORACLE_FRESH_S = 24 * 3600.0                # staleness guard: evidence currency bound
_ORACLE_RATE_THRESHOLD = 0.8                 # fire when the confirmable rate < 0.8
_ORACLE_LOG_READ_BYTES = 4 * 1024 * 1024     # bounded tail read (log rotates at 2 MB)
_ORACLE_TS_FUTURE_SLOP_S = 300.0             # tolerate small forward clock skew on ts
DEFAULT_ORACLE_DELIVERY_DEBOUNCE_PATH = (
    "/var/lib/meshforge/oracle_delivery_debounce.json")


def _resolve_oracle_log_path() -> Optional[str]:
    """Operator-home path to the oracle audit log, root-context safe.

    The oracle runs inside meshforge-gateway as the operator and appends to
    ``~/.local/share/meshforge/mesh_oracle_log.jsonl`` (== ``oracle.oracle_log_path``
    = ``MeshForgePaths.get_data_dir()``). The watchdog (sandboxed root) derives the
    operator home from the operator UID and reads it directly — never escalating
    (the synth_soak / rns_version_drift lesson). None when no operator is
    resolvable (indeterminate — never a false alarm).
    """
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        home = pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None
    return os.path.join(home, ".local", "share", "meshforge", "mesh_oracle_log.jsonl")


def _load_oracle_streak(state_path: str) -> int:
    """Read the consecutive-degraded streak. Any error → 0 (favour silence on
    uncertainty — a missing/garbage state suppresses a first-seen fire)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_oracle_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def _classify_oracle_record(rec: dict) -> Optional[str]:
    """Bucket one audit record, or None if misshaped (skipped, never counted).

    Order matters — a delivered record never carries a reason, and a decline is
    a non-delivery WITH a known reason, so check the specific cases first:
      - ``delivered is True``                       → 'delivered'  (success)
      - ``reason`` startswith 'send_error'          → 'send_error' (real failure)
      - ``reason`` a named RNS infrastructure fault → 'send_error' (real failure)
      - ``reason`` a structural/policy decline      → 'decline'    (excluded)
      - ``delivered is False`` (no_path / absent reason) → 'benign' (excluded, counted)

    The named-reason sets are pinned against the producer's closed vocabulary
    (``gateway.bridge_send_mixin.RNS_SEND_REASONS``) by the lockstep test —
    2026-07-21 review (C1): before that pin, circuit_open/not_connected/
    no_lxmf_source fell through to 'benign', so a gateway whose RNS leg failed
    every reply read as a HEALTHY delivery rate with a shrinking ambiguity
    count (honest_failure_modes #1 wearing the row-2 fix's own clothes).
    """
    if not isinstance(rec, dict):
        return None
    delivered = rec.get("delivered")
    reason = rec.get("reason")
    if delivered is True:
        return "delivered"
    if isinstance(reason, str) and reason.startswith("send_error"):
        return "send_error"
    if reason in ("circuit_open", "not_connected", "no_lxmf_source"):
        return "send_error"  # named infrastructure faults, never benign
    if reason in ("cooldown", "not_allowlisted", "broadcast_unsupported"):
        return "decline"
    if delivered is False:
        return "benign"
    return None  # not a record we recognise (neither delivered nor a non-delivery)


def _read_oracle_recent(
    log_path: str, now: float, sample_n: int, read_bytes: int,
) -> Optional[dict]:
    """Parse the audit log's tail into the LAST ``sample_n`` confirmable records.

    Returns a dict, or None when the file is unreadable (caller HOLDS the streak
    — unobservable ≠ healthy)::

        {"total_records":      classified records seen in the tail (any bucket),
         "confirmable_total":  delivered + send_error seen in the tail,
         "sample":             [(bucket, ts), …] — the last ``sample_n``
                               confirmable records, OLDEST → NEWEST,
         "counts":             {delivered, send_error, decline, benign,
                                benign_rns} — the confirmable pair over the
                               sample, the excluded buckets over the SPAN the
                               sample covers}

    **Count window, not time window (2026-08-08).** The 6 h/min-8 time window
    this replaced could never be satisfied by the fleet's only oracle box
    (moc3: 99 confirmable answers in its LIFETIME, arriving in bursts weeks
    apart), so the probe could never judge a rate at all. Rating the last N
    answers *whenever they were given* is a measure the traffic can actually
    support; the caller's staleness guard is what keeps old evidence from
    being spoken about as current health.

    ORDER IS FILE ORDER, not ts order — the append sequence is causal and a
    stepped clock cannot scramble it (honest_failure_modes #6). ``ts`` is used
    only for freshness and for clamping forgery: non-numeric, ≤ 0, or
    far-future timestamps are skipped, exactly as the time window used to do.

    The log rotates at 2 MB; at most ``read_bytes`` is read from the END, so a
    busy log stays bounded — and on a busy box the last N confirmable records
    are in the final few KB regardless.
    """
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "rb") as fh:
            if size > read_bytes:
                fh.seek(size - read_bytes)
                fh.readline()  # discard the partial line after the byte-seek
            raw = fh.read()
    except OSError:
        return None
    # ``benign_rns`` is a SUBSET of ``benign``, split out 2026-07-19 (row 2).
    # On the Meshtastic/MQTT/MeshCore legs a reason-less non-delivery really is
    # benign: their send_fn lets a real exception reach the responder, so a
    # genuine failure arrives as send_error. On the RNS leg it is AMBIGUOUS —
    # send_to_rns catches exceptions and returns a bare False, so a crash is
    # indistinguishable from a no-path. Blending the two legs into one "benign"
    # number averages a known blind spot into a clean-looking figure; counting
    # the ambiguous leg separately makes the blind spot's SIZE visible while
    # the root fix waits on the RNS roll (honest_failure_modes #5).
    hi = now + _ORACLE_TS_FUTURE_SLOP_S
    # (bucket, ts, is_ambiguous_rns) in file order — every classified record in
    # the tail, at any age. The sample is sliced out of this below.
    records: List[Tuple[str, float, bool]] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        try:
            ts = float(rec.get("ts"))
        except (TypeError, ValueError):
            continue
        if ts <= 0 or ts > hi:
            continue  # forged / unset clock — skip, never rate it
        bucket = _classify_oracle_record(rec)
        if bucket is None:
            continue
        # AMBIGUOUS = a benign-bucket RNS non-delivery with NO stated reason —
        # the row-2 blind spot proper. Once send_to_rns returns an RnsSendResult
        # the record names its reason (no_path / circuit_open / ...), which is
        # benign AND explained, so it must stop counting here or the measure
        # would never shrink as the blind spot closes.
        ambiguous = (bucket == "benign"
                     and str(rec.get("transport") or "").lower() == "rns"
                     and not str(rec.get("reason") or "").strip())
        records.append((bucket, ts, ambiguous))

    counts = {"delivered": 0, "send_error": 0, "decline": 0, "benign": 0,
              "benign_rns": 0}
    conf_idx = [i for i, (b, _, _) in enumerate(records)
                if b in ("delivered", "send_error")]
    out = {"total_records": len(records), "confirmable_total": len(conf_idx),
           "sample": [], "counts": counts}
    if len(conf_idx) < sample_n:
        return out  # caller decides: idle (nothing at all) vs small-N

    # The span the sample covers = from the OLDEST sampled confirmable record
    # to the end of the tail, in file order. Declines and benign non-deliveries
    # are surfaced over exactly that span, so the excluded numbers describe the
    # same stretch of the log as the rate does (never a different window).
    start = conf_idx[-sample_n]
    for bucket, ts, ambiguous in records[start:]:
        counts[bucket] += 1
        if ambiguous:
            counts["benign_rns"] += 1
        if bucket in ("delivered", "send_error"):
            out["sample"].append((bucket, ts))
    return out


def probe_oracle_delivery_degraded(
    *,
    log_path: Optional[str] = None,
    now: Optional[float] = None,
    sample_n: int = _ORACLE_SAMPLE_N,
    fresh_s: float = _ORACLE_FRESH_S,
    threshold: float = _ORACLE_RATE_THRESHOLD,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """The mesh oracle's confirmable delivery rate fell below threshold.

    Over the **last ``sample_n`` confirmable answers** — however long ago they
    were given — compute the #74 confirmation view:
    ``rate = delivered / (delivered + send_errors)``, where declines (cooldown /
    not_allowlisted) and benign non-deliveries (reason-less delivered:false —
    RNS no-path / MeshCore restart race) are EXCLUDED from the failure set.

    **Count window + staleness guard (2026-08-08).** v1 rated a 6 h *time*
    window and needed ≥8 confirmable inside it. The fleet's only oracle box
    answers ~5 queries a month in bursts, so that gate was unmeetable and the
    probe could never say anything at all. A count window is a measure this
    traffic supports; the staleness guard is what stops old evidence from
    being spoken about as current health:

      - the sample is **fresh** when its newest confirmable record is within
        ``fresh_s``. Only a fresh sample can be called ``clean``, and only a
        fresh sample can fire.
      - a **stale** sample is reported ``inert`` with its rate and age in the
        reason — the finding stays visible in coverage without paging about
        answers given weeks ago at a 30 s cadence.
      - firing also requires a **send_error inside ``fresh_s``**. Without it,
        every failure in the sample is history and the recent answers all
        landed; paging on a rate dragged down by old failures would be a page
        nobody can act on. (This is the pollution the count window would
        otherwise introduce, and the guard is on ACTION, not on the measure.)

    Honest self-guards (favour silence on uncertainty):
      - operator unresolvable → None (INDETERMINATE: cannot locate the log).
      - log file absent → None (INERT: the oracle never answered on this box).
      - log tail unreadable (transient/torn) → None, HOLDING the streak (neither
        fires nor resets).
      - zero records in the tail → None (INERT: enrolled but idle — the
        observation SUCCEEDED and says nobody asked; not a blind detector).
      - fewer than ``sample_n`` confirmable answers EVER recorded → None
        (INDETERMINATE): a rate over a handful of queries is pass@small-N noise.
        Unlike v1's per-window guard this resolves permanently once the oracle
        has answered ``sample_n`` times, instead of re-arming every window.
      - rate ≥ threshold on a fresh sample → explicit healthy → reset the streak.
      - a degraded candidate must persist ``debounce_ticks`` consecutive ticks.
    """
    import time as _time
    now = _time.time() if now is None else now

    lp = log_path or _resolve_oracle_log_path()
    if not lp:
        note_disposition("oracle_delivery_degraded", "indeterminate",
                         reason="operator unresolvable — cannot locate oracle log")
        return None  # operator unresolvable — indeterminate, never a false alarm
    if not os.path.exists(lp):
        note_disposition("oracle_delivery_degraded", "inert",
                         reason="oracle never wrote a log (disabled/never queried)")
        return None  # INERT: the oracle never wrote a log here (disabled/never queried)

    sp = debounce_path or DEFAULT_ORACLE_DELIVERY_DEBOUNCE_PATH

    parsed = _read_oracle_recent(lp, now, sample_n, _ORACLE_LOG_READ_BYTES)
    if parsed is None:
        note_disposition("oracle_delivery_degraded", "indeterminate",
                         reason="oracle log tail unreadable — streak held")
        return None  # unreadable tail — HOLD the streak (don't reset, don't fire)
    counts = parsed["counts"]

    # IDLE ≠ UNOBSERVABLE (2026-08-08, Tier-3 re-decide). The observation
    # SUCCEEDED and its answer is "the oracle has answered nothing" — a
    # reactive service nobody queried. Reporting that as ``indeterminate``
    # made the cell read detector-blind FOREVER on the only box that can see
    # (moc3) — the standing-indeterminate-is-a-finding trap, one layer up.
    if parsed["total_records"] == 0:
        note_disposition(
            "oracle_delivery_degraded", "inert",
            reason="oracle enrolled but idle — no answers recorded at all")
        return None

    # ⚠️ Records but too few CONFIRMABLE ones is NOT idle. A log of nothing but
    # declines, or nothing but reason-less RNS non-deliveries, means the oracle
    # IS being exercised — and the all-benign-RNS shape is exactly the row-2
    # blind spot. Calling that "nothing to watch" would hide the failure this
    # probe exists to size (honest_failure_modes #2).
    if parsed["confirmable_total"] < sample_n:
        note_disposition(
            "oracle_delivery_degraded", "indeterminate",
            reason=(f"only {parsed['confirmable_total']} confirmable answer(s) "
                    f"ever recorded (< {sample_n}) — cannot judge a rate"))
        return None

    sample = parsed["sample"]
    delivered = counts["delivered"]
    real_failures = counts["send_error"]
    confirmable = delivered + real_failures       # == sample_n by construction
    rate = delivered / confirmable

    newest_ts = sample[-1][1]
    oldest_ts = sample[0][1]
    newest_age_s = max(0.0, now - newest_ts)
    # Span from the sample's own endpoints. A stepped clock can make this
    # negative (file order is causal, ts is not); clamp rather than print a
    # negative duration (honest_failure_modes #6).
    span_s = max(0.0, newest_ts - oldest_ts)
    fresh_errors = sum(1 for b, ts in sample
                       if b == "send_error" and (now - ts) <= fresh_s)

    extra = {
        "sample_n": sample_n,
        "confirmable": confirmable,
        "delivered": delivered,
        "send_errors": real_failures,
        "fresh_send_errors": fresh_errors,
        "newest_age_h": round(newest_age_s / 3600.0, 1),
        "sample_span_h": round(span_s / 3600.0, 1),
        "fresh_h": round(fresh_s / 3600.0, 1),
        "declines_excluded": counts["decline"],
        "benign_nondeliveries_excluded": counts["benign"],
        # The ambiguous slice of the line above: RNS-leg non-deliveries that
        # could be a benign no-path OR a send exception swallowed to a bare
        # False. NOT a failure count — a measure of what we cannot yet tell
        # apart. Non-zero means the row-2 blind spot is live at that size.
        "benign_rns_ambiguous": counts["benign_rns"],
        "rate": round(rate, 3),
        "threshold": threshold,
    }

    # THE STALENESS GUARD. Old answers are evidence about the past, never a
    # claim about now — so a stale sample can be neither `clean` (that would be
    # absence-of-evidence dressed as health) nor a page (nobody can act on a
    # failure from three weeks ago, and at 30 s a tick it would never stop).
    # It is `inert` with the measurement in the reason, so the finding stays
    # readable in coverage. This is moc3's steady state.
    if newest_age_s > fresh_s:
        _save_oracle_streak(sp, 0)
        verdict = ("rate %.2f BELOW threshold %.2f" % (rate, threshold)
                   if rate < threshold else "rate %.2f" % rate)
        note_disposition(
            "oracle_delivery_degraded", "inert",
            reason=(f"oracle idle — last {confirmable} answers {verdict}, "
                    f"newest {newest_age_s / 86400.0:.1f}d ago "
                    f"(stale > {fresh_s / 3600.0:.0f}h; not paged)"))
        return None

    if rate >= threshold:
        _save_oracle_streak(sp, 0)  # explicit healthy observation → reset
        note_disposition("oracle_delivery_degraded", "clean")
        return None

    # Fresh sample, degraded rate — but if every send_error in it is OLD, the
    # recent answers all landed and the rate is being dragged down by history.
    # Report the healthy fresh evidence; do not page about the past.
    if fresh_errors == 0:
        _save_oracle_streak(sp, 0)
        note_disposition(
            "oracle_delivery_degraded", "clean",
            reason=(f"last {confirmable} answers rate {rate:.2f} (below "
                    f"{threshold:.2f}) but every send-error is older than "
                    f"{fresh_s / 3600.0:.0f}h — recent answers all landed"))
        return None

    streak = min(_load_oracle_streak(sp) + 1, debounce_ticks)
    _save_oracle_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition("oracle_delivery_degraded", "indeterminate",
                         reason="degraded candidate held by debounce")
        return None
    extra["debounce_streak"] = streak
    detail = (
        f"oracle delivery degraded: {rate:.2f} rate (threshold {threshold:.2f}) "
        f"over its last {confirmable} answers — {delivered} delivered / "
        f"{real_failures} send-error, {fresh_errors} of those failures within "
        f"{extra['fresh_h']:.0f}h ({counts['decline']} declines + "
        f"{counts['benign']} benign non-deliveries excluded). The sample spans "
        f"~{extra['sample_span_h']:.0f}h, newest answer "
        f"{extra['newest_age_h']:.1f}h ago. The oracle answered but its replies "
        f"are not landing — check the gateway's RNS/Meshtastic send path + the "
        f"oracle audit log (~/.local/share/meshforge/mesh_oracle_log.jsonl)."
    )
    return Signal(
        cls="oracle_delivery_degraded",
        subject="mesh-oracle",
        severity="degraded",
        detail=detail,
        extra=extra,
    )

