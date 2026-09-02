"""Watchdog probe — MeshForge<->MeshAnchor parity drift (lead-repo port debt).

Split out of ``watchdog_probes_drift`` 2026-09-01 (MF025 size cap) when the
probe was retargeted from working-tree bytes onto COMMITTED state. Import via
the ``utils.watchdog_probes`` hub or ``utils.watchdog_probes_drift``, which
re-exports the full surface for back-compat — not from here directly.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Dict, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)


# ─────────────────────────────────────────────────────────────────────
# Probe: MeshForge <-> MeshAnchor parity drift (lead-repo port debt)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_PARITY_DEBOUNCE_PATH = "/var/lib/meshforge/parity_debounce.json"
DEFAULT_PARITY_DIRTY_STATE_PATH = "/var/lib/meshforge/parity_dirty_window.json"

# How long an uncommitted parity edit may sit before the PARKED state is itself
# the finding. Below it a working-tree divergence is an authoring window and says
# nothing about port debt; above it someone edited one twin and walked away —
# worth surfacing, but as a different claim than "forgot to port".
PARITY_UNCOMMITTED_PARK_S = 86400  # 24h

# A dirty window older than this is not a long edit, it is a forged duration
# (RTC-less Pi, fake-hwclock restore, NTP step — honest_failure_modes #6).
# Re-anchor rather than report an absurd age.
PARITY_DIRTY_WINDOW_MAX_S = 30 * 86400

# Last dirty-window anchor this process wrote, per state path. Same contract as
# _streak_mem_fallback in watchdog_probe_core: a broken state dir (the #60
# sandbox-drift class) degrades the anchor to in-process-only rather than
# re-anchoring to `now` every tick, which would push the parked-edit signal out
# to never.
_parity_dirty_mem: Dict[str, dict] = {}


def _parity_label_to_relpath(label: str) -> str:
    """Repo-relative path a parity finding label points at.

    parity_check labels carry tier decoration that a git query must not see:
    ``src/utils/rns_status_parser.py :: timed_out`` (shape / calib-gate tiers),
    ``meshforge:src/utils/watchdog_probes.py :: probe_x`` (probe tier),
    ``requirements/rns.txt (fork-pin block)`` (fork-pin tier). Returns "" when
    nothing path-shaped survives — the caller must not silently treat that as a
    clean file.
    """
    rel = label.split(" :: ", 1)[0].strip()
    for prefix in ("meshforge:", "meshanchor:"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
    rel = re.sub(r"\s*\(fork-pin block\)$", "", rel)
    return rel.strip()


def _git_dirty_paths(root: str, rels, *, timeout_s: int = 15) -> Tuple[set, str]:
    """Which of ``rels`` differ from ``root``'s git HEAD → ``(dirty, status)``.

    ``status`` is ``"ok"`` when git answered, ``"unavailable"`` when it could not
    (git missing, not a checkout, timeout, unreadable index). The caller MUST
    treat ``unavailable`` as indeterminate, never as clean: answering "nothing is
    dirty" from a failed query is the honest_failure_modes #1 shape — a degraded
    read mapped onto a valid-looking value — and here it would convert every
    authoring window straight back into a false port-debt claim.

    ``-c safe.directory=<root>`` because the CONSUMER OF RECORD is a root
    systemd unit reading operator-owned clones. git's dubious-ownership check
    then refuses any repo root's gitconfig doesn't list — and on the box this
    probe runs on, root trusted ``/opt/meshforge`` but not ``/opt/meshanchor``,
    so the live daemon read ``ma=unavailable`` and went blind on the one box
    that can see this class. Found 2026-09-01 by drilling the LIVE unit; the
    same call as the operator user had answered ``ok``, which is why a
    proxy-verification could not have caught it (calibrated_claims #7). Scoping
    the exemption to the exact root being queried keeps this a read-only query
    on a path the probe was configured with, and needs no per-box operator
    setup — a new box is correct on arrival rather than silently blind.

    ``-c core.fileMode=false`` because a mere permission-bit diff would show as
    ``M`` and this probe reads dirty as "authoring window" — i.e. it SUPPRESSES.
    A chmod sweep (the 2026-08-29 identity-key tightening was one) could
    therefore silence the probe rather than merely noise it, which is the blind
    direction this whole retarget exists to avoid. Mirrors the same two flags
    ``_git_tracked_modifications`` in ``watchdog_probes_env`` has carried all
    along — that file had the cure while this one shipped the bug.
    """
    if not rels:
        return set(), "ok"
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={root}",
             "-c", "core.fileMode=false", "-C", root,
             "status", "--porcelain", "-z", "--"] + list(rels),
            capture_output=True, timeout=timeout_s,
        )
    except (OSError, subprocess.SubprocessError):
        return set(), "unavailable"
    if proc.returncode != 0:
        return set(), "unavailable"
    tokens = [t for t in proc.stdout.decode("utf-8", "replace").split("\0") if t]
    dirty, i = set(), 0
    while i < len(tokens):
        rec = tokens[i]
        i += 1
        if len(rec) < 4 or rec[2] != " ":
            continue  # not an "XY <path>" status record — skip rather than guess
        xy, path = rec[:2], rec[3:]
        dirty.add(path)
        if xy[0] in ("R", "C") and i < len(tokens):
            dirty.add(tokens[i])  # rename/copy carries the source path too
            i += 1
    return dirty, "ok"


def _load_parity_dirty_window(state_path: str) -> dict:
    """Read the dirty-window anchor. Prefer this process's own last write."""
    if state_path in _parity_dirty_mem:
        return dict(_parity_dirty_mem[state_path])
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_parity_dirty_window(state_path: str, payload: dict) -> None:
    """Persist the anchor (atomic-rename, never raises).

    Records in-process FIRST so a broken state dir loses only restart survival,
    not the anchor itself (honest_failure_modes #9: the swallow leaves a witness
    the next load can see).
    """
    _parity_dirty_mem[state_path] = dict(payload)
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        return


def probe_parity_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    meshanchor_root: str = "/opt/meshanchor",
    check_fn=None,
    state_path: Optional[str] = None,
    dirty_state_path: Optional[str] = None,
    dirty_fn=None,
    now: Optional[float] = None,
    debounce_ticks: int = 6,
    park_after_s: int = PARITY_UNCOMMITTED_PARK_S,
) -> Optional[Signal]:
    """Surface MeshForge<->MeshAnchor parity drift (lead-repo port debt).

    The two sister NOCs share the fleet's RNS substrate; reliability-critical files
    (the RNS-init chokepoint, the bridge contract, the rns_tree_perms SSOT, the
    fork-pin, lint MF009/MF019, the wedge probes) must stay in lockstep —
    ``scripts/parity_check.py`` is the lead-repo gate. This makes that audit a
    continuously-monitored signal so a divergence (someone edits one repo and
    forgets to port) self-surfaces in /fleet + the mini deep-rollup instead of
    rotting until the next manual run.

    Only meaningful where BOTH repos are present. Returns None when
    ``meshanchor_root`` isn't a directory, when the parity tool can't be loaded,
    when everything's in sync, or when the result is merely ``missing``.

    **COMMITTED state is the subject (retargeted 2026-09-01).** ``check_parity``
    hashes files off DISK, so it compares two WORKING TREES — but "forgot to port"
    is a claim about what is COMMITTED. On the only box that can run this probe
    (the one holding both clones, i.e. the authoring box) the working tree is
    dirty by design during every session, so the old probe reported the interval
    between editing one twin and editing the other as port debt. Measured over the
    92 days to 2026-09-01: **48 episodes, 46 of them authoring windows, 0 genuine
    forgotten ports**, and all 20 dream proposals it raised were dismissed as
    benign — with the later dismissals citing the earlier ones as warrant, the
    exact anti-pattern persistent_issues.md names. A detector whose every fire is
    noise trains the reader to dismiss the one that isn't.

    So the two states are separated before anything fires (honest_failure_modes #1
    — a degraded/in-flight state must not share a value with the real one):

    * a drifted file **dirty in either repo** → in-flight authoring →
      ``indeterminate``, no fire, no streak;
    * both copies **clean at HEAD and still diverging** → real committed port debt
      → fire after ``debounce_ticks`` consecutive COMMITTED ticks;
    * git can't say → ``indeterminate`` with the failing leg named. Never clean.

    **Retarget, not mute.** An uncommitted parity edit that sits past
    ``park_after_s`` (24h) IS a finding — just a different one — and fires with
    ``mode="uncommitted_parked"``. Without that leg this fix would trade a noisy
    detector for a blind one, which is the other half of the same failure class.

    **Debounce**: the committed streak starts when the trees go clean (a dirty tick
    resets it), so it times the port lag specifically. Measured MeshForge-commit →
    MeshAnchor-port lag across the byte-locked files is 26-82s; the default 6 ticks
    (~3 min at the 30s watchdog cadence) rides that out with margin while still
    surfacing a genuinely forgotten port within ~3 minutes.
    """
    if not os.path.isdir(meshanchor_root):
        note_disposition("parity_drift", "inert",
                         reason="no sister repo on this box")
        return None  # both repos required; MeshForge-only box → not applicable
    if state_path is None:
        state_path = DEFAULT_PARITY_DEBOUNCE_PATH
    if dirty_state_path is None:
        dirty_state_path = DEFAULT_PARITY_DIRTY_STATE_PATH
    if now is None:
        now = time.time()
    if check_fn is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "parity_check",
                os.path.join(meshforge_root, "scripts", "parity_check.py"),
            )
            import sys
            mod = importlib.util.module_from_spec(spec)
            # Register before exec: on py3.12+ @dataclass resolves field types via
            # sys.modules[cls.__module__].__dict__ → AttributeError if absent. This
            # silently killed parity_drift + role_drift on the 3.13 fleet (found
            # 2026-06-08 inducing a live role_drift on moc1).
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            check_fn = mod.check_parity
        except Exception:
            note_disposition("parity_drift", "indeterminate",
                             reason="parity tool unavailable")
            return None  # parity tool unavailable → indeterminate, don't alarm
    try:
        findings, overall = check_fn(meshforge_root, meshanchor_root)
    except Exception:
        # Indeterminate — don't let a tool error count toward the streak.
        _save_parity_streak(state_path, 0)
        note_disposition("parity_drift", "indeterminate",
                         reason="parity check raised")
        return None
    if overall != "drift":
        _save_parity_streak(state_path, 0)  # in_sync / missing → streak broken
        _save_parity_dirty_window(dirty_state_path, {})
        if overall == "in_sync":
            note_disposition("parity_drift", "clean")
        else:
            note_disposition("parity_drift", "indeterminate",
                             reason=f"parity result '{overall}' — can't compare")
        return None

    drifted = [f for f in findings if getattr(f, "status", None) == "drift"]
    labels = [f.label for f in drifted]
    items = ", ".join(labels) or "?"
    rels = sorted({r for r in (_parity_label_to_relpath(l) for l in labels) if r})

    # Which side is merely mid-edit? Ask git, not the file's bytes.
    query = dirty_fn or _git_dirty_paths
    mf_dirty, mf_status = query(meshforge_root, rels)
    ma_dirty, ma_status = query(meshanchor_root, rels)
    if mf_status != "ok" or ma_status != "ok" or not rels:
        # Cannot separate an authoring window from committed debt. Blind, and the
        # blindness is the signal — a long-running indeterminate is a finding
        # (feedback_detector_blind_is_a_finding), not a pass.
        reason = (f"git could not say whether the drifted file is committed "
                  f"(mf={mf_status} ma={ma_status}) — authoring window and port "
                  f"debt are indistinguishable here") if rels else (
                  f"no repo-relative path recoverable from drift label(s): {items}")
        _save_parity_streak(state_path, 0)
        note_disposition("parity_drift", "indeterminate", reason=reason)
        return None

    win = _load_parity_dirty_window(dirty_state_path)
    was_dirty = bool(win.get("dirty"))
    dirty = sorted(mf_dirty | ma_dirty)

    if dirty:
        since = win.get("since")
        # Re-anchor on: first sight, a DIFFERENT edit, a clock that went backward,
        # or an age too large to be a real edit (honest_failure_modes #6).
        if (not isinstance(since, (int, float)) or not was_dirty
                or win.get("items") != labels
                or now < since or now - since > PARITY_DIRTY_WINDOW_MAX_S):
            since = now
        _save_parity_dirty_window(dirty_state_path,
                                  {"since": since, "items": labels, "dirty": True})
        _save_parity_streak(state_path, 0)  # the committed clock has not started
        age = max(0.0, now - since)
        if age < park_after_s:
            note_disposition(
                "parity_drift", "indeterminate",
                reason=(f"drift confined to an uncommitted working tree "
                        f"(authoring window, {age / 60.0:.0f}m): "
                        f"{', '.join(dirty)}"))
            return None
        detail = (
            f"MeshForge<->MeshAnchor parity edit PARKED uncommitted for "
            f"{age / 3600.0:.1f}h ({len(drifted)} item(s)): {items} | dirty in "
            f"working tree: {', '.join(dirty)} | not port debt — one twin was "
            f"edited and left uncommitted. Commit and port it, or revert: "
            f"git -C {meshforge_root} status; python3 scripts/parity_check.py"
        )
        return Signal(
            cls="parity_drift",
            subject="meshforge<->meshanchor",
            severity="degraded",
            detail=detail,
            extra={"mode": "uncommitted_parked", "drift_items": labels,
                   "dirty_paths": dirty, "dirty_age_s": int(age)},
        )

    # Both copies clean at HEAD and still diverging → the real thing.
    if was_dirty:
        _save_parity_streak(state_path, 0)  # committed window starts now
    _save_parity_dirty_window(dirty_state_path, {"dirty": False})
    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        note_disposition("parity_drift", "indeterminate",
                         reason="committed drift candidate under debounce")
        return None  # committed drift seen, but not yet across consecutive ticks

    detail = (
        f"MeshForge<->MeshAnchor parity drift ({len(drifted)} item(s)): {items} | "
        f"COMMITTED in both trees, confirmed over {streak} consecutive ticks | "
        f"RNS-reliability files must match (MeshForge is the lead repo). Port the "
        f"change, then verify: python3 scripts/parity_check.py"
    )
    return Signal(
        cls="parity_drift",
        subject="meshforge<->meshanchor",
        severity="degraded",
        detail=detail,
        extra={"mode": "committed_port_debt", "drift_items": labels,
               "debounce_streak": streak},
    )

