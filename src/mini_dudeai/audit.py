"""Independent honesty audit organ for mini-dudeai.

mini-dudeai reports its own outcomes — actions default to ``ok=True``, the
daemon's ``--once`` always ``return 0``, the dream pass claims "appended N"
unaudited, and memory proposals are write-only (no read-back). A writer
that certifies itself is the false-green trap operators have been burned
by ("we keep saying it's good and it's not").

This module is the INDEPENDENT certifier. It reads mini's real on-disk
artifacts and certifies the self-report against observable reality — it
never trusts mini's own ``ok``/exit claims. It is deliberately a SEPARATE
code path from the engine: the same principle as ``memory_apply``'s
provenance gate (mini cannot self-verify), generalized to the whole loop.

Honesty contract (three-valued, never two):
  * PASS         — reality corroborates the claim.
  * UNVERIFIABLE — honestly cannot be proven either way. Surfaced, NEVER
                   silently counted as healthy. ("I don't know" is honest.)
  * DEGRADED     — reality contradicts a claim (false-green), or a
                   liveness/integrity invariant is broken.

Overall ``healthy`` = zero DEGRADED checks. ``main()`` exits 0 healthy, 2
degraded — so a cron wrapper can be fail-closed (a green genuinely means
something).

Stdlib-only, matching mini-dudeai's dependency-free design. Extend by
adding a function to ``CHECKS`` — that is the scale seam, not a special
case bolted onto the engine.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .history import append_jsonl


# ─────────────────────────────────────────────────────────────────────
# Status vocabulary + artifact names
# ─────────────────────────────────────────────────────────────────────

PASS = "pass"
UNVERIFIABLE = "unverifiable"
DEGRADED = "degraded"

STATE_FILE = "mini_dudeai_state.json"
HISTORY_FILE = "mini_dudeai_history.jsonl"
from .dreams import DELTAS_BASENAME as DELTAS_FILE

# Cap for the append-only honesty ledger (`--ledger`). One verdict line per
# audit run; left unbounded it grows ~30 KB/day with no ceiling. 2 MB is
# generous — many weeks of forensic verdicts — but bounded. Rotation keeps the
# most-recent verdicts (the ones an operator/cron actually cares about).
DEFAULT_LEDGER_MAX_BYTES = 2_000_000  # 2 MB

# A mini --once fire is expected hourly; allow ~1.5 cycles before a stale
# state means "claims state but isn't actually ticking". Overridable.
DEFAULT_FRESHNESS_S = 5400.0
# Recent-window for history/error-rate sampling.
DEFAULT_RECENT = 50
# Proposed memory deltas older than this with no resolution are surfaced
# (proposing != fixing — write-only theater that never gets acted on).
DEFAULT_STALE_PROPOSAL_S = 86400.0


# ─────────────────────────────────────────────────────────────────────
# Result + context
# ─────────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    status: str               # PASS | UNVERIFIABLE | DEGRADED
    detail: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.data:
            out["data"] = self.data
        return out


@dataclass
class AuditReport:
    ts: float
    artifact_dir: str
    checks: List[CheckResult]

    @property
    def degraded(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status == DEGRADED]

    @property
    def unverifiable(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status == UNVERIFIABLE]

    @property
    def healthy(self) -> bool:
        return len(self.degraded) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "artifact_dir": self.artifact_dir,
            "healthy": self.healthy,
            "degraded_count": len(self.degraded),
            "unverifiable_count": len(self.unverifiable),
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class AuditContext:
    """Loaded artifacts + thresholds. Loaders are tolerant: a missing or
    malformed artifact is recorded, never raised — a crash here would be
    its own dishonesty (no verdict at all)."""
    artifact_dir: Path
    now: float
    freshness_s: float = DEFAULT_FRESHNESS_S
    recent: int = DEFAULT_RECENT
    stale_proposal_s: float = DEFAULT_STALE_PROPOSAL_S

    state: Optional[dict] = None
    state_error: Optional[str] = None
    history: List[dict] = field(default_factory=list)
    history_malformed: int = 0
    history_present: bool = False
    deltas: List[dict] = field(default_factory=list)
    deltas_malformed: int = 0
    deltas_present: bool = False

    @classmethod
    def load(cls, artifact_dir: Path, now: float, **kw) -> "AuditContext":
        ctx = cls(artifact_dir=artifact_dir, now=now, **kw)
        ctx.state, ctx.state_error = _load_json(artifact_dir / STATE_FILE)
        (ctx.history, ctx.history_malformed,
         ctx.history_present) = _load_jsonl(artifact_dir / HISTORY_FILE)
        (ctx.deltas, ctx.deltas_malformed,
         ctx.deltas_present) = _load_jsonl(artifact_dir / DELTAS_FILE)
        return ctx


def _load_json(path: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"unreadable: {exc}"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"malformed: {exc}"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def _load_jsonl(path: Path) -> tuple[List[dict], int, bool]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], 0, False
    except OSError:
        return [], 0, False
    rows: List[dict] = []
    malformed = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            malformed += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            malformed += 1
    return rows, malformed, True


# ─────────────────────────────────────────────────────────────────────
# Checks — each (ctx) -> CheckResult. Add one to CHECKS to extend.
# ─────────────────────────────────────────────────────────────────────


def check_liveness(ctx: AuditContext) -> CheckResult:
    """Is mini actually ticking, or just claiming a stale state? The #1
    false-green: looks configured, isn't running."""
    if ctx.state is None:
        return CheckResult(
            "liveness", DEGRADED,
            f"no usable state file ({ctx.state_error}) — mini has never "
            f"ticked here, or its store is missing. It is NOT doing its job.",
            {"state_error": ctx.state_error},
        )
    last = ctx.state.get("last_tick_ts")
    if not isinstance(last, (int, float)):
        return CheckResult(
            "liveness", DEGRADED,
            "state file has no numeric last_tick_ts — cannot prove mini "
            "ever ran a tick.",
            {"last_tick_ts": last},
        )
    age = ctx.now - float(last)
    common = {
        "age_s": round(age, 1),
        "freshness_s": ctx.freshness_s,
        "fire_count": ctx.state.get("fire_count"),
        "error_count": ctx.state.get("error_count"),
    }
    if age > ctx.freshness_s:
        return CheckResult(
            "liveness", DEGRADED,
            f"state is stale ({age:.0f}s old > {ctx.freshness_s:.0f}s) — "
            f"mini claims state but is not ticking. Silent death.",
            common,
        )
    if age < 0:
        return CheckResult(
            "liveness", UNVERIFIABLE,
            f"last_tick_ts is in the future by {-age:.0f}s — clock skew; "
            f"cannot certify freshness.", common,
        )
    return CheckResult(
        "liveness", PASS,
        f"ticking — last tick {age:.0f}s ago, "
        f"fire_count={common['fire_count']} error_count={common['error_count']}.",
        common,
    )


def check_history_integrity(ctx: AuditContext) -> CheckResult:
    """The fire log must be readable and parse cleanly. Malformed lines
    mean the record itself is untrustworthy."""
    if not ctx.history_present:
        return CheckResult(
            "history_integrity", UNVERIFIABLE,
            "no history file — nothing to corroborate fires against (a "
            "fresh box, or mini never fired a rule).",
        )
    if ctx.history_malformed > 0:
        return CheckResult(
            "history_integrity", DEGRADED,
            f"{ctx.history_malformed} malformed history line(s) — the fire "
            f"record is corrupt and cannot be trusted.",
            {"malformed": ctx.history_malformed, "rows": len(ctx.history)},
        )
    return CheckResult(
        "history_integrity", PASS,
        f"history readable, {len(ctx.history)} clean fire record(s).",
        {"rows": len(ctx.history)},
    )


def check_outcome_truth(ctx: AuditContext) -> CheckResult:
    """Cross-check claimed outcomes vs errors. Reporting errors honestly is
    fine; claiming all-ok while errors pile up is the false-green."""
    if not ctx.history:
        return CheckResult(
            "outcome_truth", UNVERIFIABLE,
            "no fire records in window to evaluate.",
        )
    recent = ctx.history[-ctx.recent:]
    ok = err = unknown = 0
    for e in recent:
        outcome = e.get("outcome")
        if not isinstance(outcome, dict):
            unknown += 1
            continue
        if outcome.get("error"):
            err += 1
        elif outcome.get("ok") is True:
            ok += 1
        elif outcome.get("ok") is False:
            err += 1
        else:
            unknown += 1
    data = {"window": len(recent), "ok": ok, "err": err, "unknown": unknown}
    state_err = (ctx.state or {}).get("error_count")
    if isinstance(state_err, int):
        data["state_error_count"] = state_err
    if recent and err == len(recent):
        return CheckResult(
            "outcome_truth", DEGRADED,
            f"every one of the last {len(recent)} fires reported an error — "
            f"mini is firing but nothing is succeeding.", data,
        )
    if err > 0:
        return CheckResult(
            "outcome_truth", PASS,
            f"{ok} ok / {err} err / {unknown} unknown in last {len(recent)} "
            f"fires — errors are being reported honestly, not hidden.", data,
        )
    return CheckResult(
        "outcome_truth", PASS,
        f"{ok} ok fires in last {len(recent)}, no errors.", data,
    )


def check_proposal_readback(ctx: AuditContext) -> CheckResult:
    """Close the write-only theater: a 'proposed' memory delta must be
    readable back, and proposing is NOT fixing — surface proposals that
    have sat unresolved (nobody acted) so they don't masquerade as done."""
    if not ctx.deltas_present:
        return CheckResult(
            "proposal_readback", UNVERIFIABLE,
            "no memory-deltas file — mini has proposed nothing (the apply/"
            "read-back loop is not wired in live code).",
        )
    if ctx.deltas_malformed > 0:
        return CheckResult(
            "proposal_readback", DEGRADED,
            f"{ctx.deltas_malformed} malformed delta line(s) — proposal "
            f"record corrupt.", {"malformed": ctx.deltas_malformed},
        )
    by_status: Dict[str, int] = {}
    inconsistent = 0
    oldest_unresolved_age: Optional[float] = None
    for d in ctx.deltas:
        st = str(d.get("status", "unknown"))
        by_status[st] = by_status.get(st, 0) + 1
        # A delta still 'proposed' but carrying a resolution note/ts is a
        # lie about its own state.
        if st == "proposed" and (d.get("resolved_ts") or d.get("resolved_note")):
            inconsistent += 1
        if st == "proposed":
            ts = d.get("ts")
            if isinstance(ts, (int, float)):
                age = ctx.now - float(ts)
                if oldest_unresolved_age is None or age > oldest_unresolved_age:
                    oldest_unresolved_age = age
    data = {"by_status": by_status}
    if oldest_unresolved_age is not None:
        data["oldest_unresolved_age_s"] = round(oldest_unresolved_age, 1)
    if inconsistent > 0:
        return CheckResult(
            "proposal_readback", DEGRADED,
            f"{inconsistent} delta(s) marked 'proposed' yet carry a "
            f"resolution — status lies about itself.", data,
        )
    if (oldest_unresolved_age is not None
            and oldest_unresolved_age > ctx.stale_proposal_s):
        return CheckResult(
            "proposal_readback", PASS,
            f"proposals readable; oldest unresolved is "
            f"{oldest_unresolved_age / 3600:.1f}h old — proposing is not "
            f"fixing, surfacing for ratification. {by_status}", data,
        )
    return CheckResult(
        "proposal_readback", PASS,
        f"proposals readable and consistent. {by_status}", data,
    )


def check_ai_layer_honesty(ctx: AuditContext) -> CheckResult:
    """State the AI reality plainly. mini is deterministic today; the
    liability is letting anyone believe it does AI work it does not."""
    has_pkg = importlib.util.find_spec("anthropic") is not None
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    data = {"anthropic_installed": has_pkg, "api_key_present": has_key}
    if has_pkg and has_key:
        return CheckResult(
            "ai_layer_honesty", PASS,
            "anthropic available + API key present — AI synthesis is "
            "POSSIBLE (verify it is actually wired before claiming it).",
            data,
        )
    return CheckResult(
        "ai_layer_honesty", PASS,
        "AI layer ABSENT (anthropic "
        + ("installed" if has_pkg else "not installed")
        + ", API key "
        + ("present" if has_key else "absent")
        + ") — mini is DETERMINISTIC ONLY. Honest as long as nothing "
        "claims mini is doing AI/dream synthesis.",
        data,
    )


def check_provenance_gate(ctx: AuditContext) -> CheckResult:
    """mini must never self-verify. A delta in a ratified/accepted state is
    only honest if it carries EXTERNAL resolution evidence — in the real
    deltas schema that is ``resolved_ts`` and/or ``resolved_note`` (written
    by a cloud session or operator, never by the deterministic engine,
    which only ever writes status='proposed'). A ratified delta with NO
    resolution evidence would be mini certifying its own work.

    (This check is deliberately keyed on the ACTUAL on-disk schema —
    ``resolved_ts``/``resolved_note`` — not an invented field. An audit
    organ that flags against criteria the data never used is itself a
    liability; that lesson is the whole point of this module.)
    """
    if not ctx.deltas:
        return CheckResult(
            "provenance_gate", UNVERIFIABLE,
            "no deltas to inspect for self-verification.",
        )
    ratified_states = ("ratified", "accepted", "verified")
    unresolved_ratified = 0
    resolved_ratified = 0
    for d in ctx.deltas:
        if str(d.get("status", "")) not in ratified_states:
            continue
        has_evidence = bool(d.get("resolved_ts")) or bool(d.get("resolved_note")) \
            or bool(d.get("resolved_by"))
        if has_evidence:
            resolved_ratified += 1
        else:
            unresolved_ratified += 1
    data = {"resolved_ratified": resolved_ratified,
            "unresolved_ratified": unresolved_ratified}
    if unresolved_ratified > 0:
        return CheckResult(
            "provenance_gate", DEGRADED,
            f"{unresolved_ratified} delta(s) are ratified but carry NO "
            f"external resolution evidence (resolved_ts/resolved_note) — "
            f"mini may be certifying its own work. Provenance gate at risk.",
            data,
        )
    return CheckResult(
        "provenance_gate", PASS,
        f"all {resolved_ratified} ratified delta(s) carry external "
        f"resolution evidence — mini is not certifying its own work.",
        data,
    )


# Registry — the scale seam. Add a function here and it runs everywhere.
CHECKS: List[Callable[[AuditContext], CheckResult]] = [
    check_liveness,
    check_history_integrity,
    check_outcome_truth,
    check_proposal_readback,
    check_ai_layer_honesty,
    check_provenance_gate,
]


def run_audit(ctx: AuditContext) -> AuditReport:
    """Run every check. A check that itself raises is recorded as DEGRADED
    (an audit organ that crashes is its own dishonesty)."""
    results: List[CheckResult] = []
    for fn in CHECKS:
        try:
            results.append(fn(ctx))
        except Exception as exc:  # noqa: BLE001 — audit must never crash silently
            results.append(CheckResult(
                getattr(fn, "__name__", "check"), DEGRADED,
                f"check raised {type(exc).__name__}: {exc}",
            ))
    return AuditReport(ts=ctx.now, artifact_dir=str(ctx.artifact_dir),
                       checks=results)


# ─────────────────────────────────────────────────────────────────────
# Rendering + CLI
# ─────────────────────────────────────────────────────────────────────

_GLYPH = {PASS: "OK ", UNVERIFIABLE: "?? ", DEGRADED: "XX "}


def render_text(report: AuditReport) -> str:
    lines = [
        f"mini-dudeai honesty audit — {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report.ts))}",
        f"verdict: {'HONEST/HEALTHY' if report.healthy else 'DEGRADED'} "
        f"({len(report.degraded)} degraded, {len(report.unverifiable)} unverifiable)",
        f"artifacts: {report.artifact_dir}",
        "",
    ]
    for c in report.checks:
        lines.append(f"  [{_GLYPH.get(c.status, '?')}] {c.name}: {c.detail}")
    return "\n".join(lines)


def resolve_artifact_dir(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    # Shared resolver ($MINI_DUDEAI_HOME → ~): the audit used to be the ONLY
    # consumer honoring the env var, so it could certify a different artifact
    # dir than the one the daemon wrote.
    from ._util import resolve_home
    return Path(resolve_home())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Independent honesty audit of mini-dudeai (certifies "
                    "its self-report against on-disk reality).")
    parser.add_argument("--dir", default=None,
                        help="Artifact dir (default $MINI_DUDEAI_HOME or ~).")
    parser.add_argument("--freshness", type=float, default=DEFAULT_FRESHNESS_S,
                        help=f"Max state age before stale (default "
                             f"{DEFAULT_FRESHNESS_S:.0f}s).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text.")
    parser.add_argument("--ledger", default=None,
                        help="Append the verdict as one JSON line to this "
                             "path (the truthful ledger).")
    args = parser.parse_args(argv)

    artifact_dir = resolve_artifact_dir(args.dir)
    ctx = AuditContext.load(artifact_dir, now=time.time(),
                            freshness_s=args.freshness)
    report = run_audit(ctx)

    if args.ledger:
        ledger_path = os.path.expanduser(args.ledger)
        # Shared append posture (rotate-if-over-cap + torn-tail repair +
        # swallowed OSError) — same implementation as history/dreams.
        err = append_jsonl(ledger_path, [report.to_dict()],
                           DEFAULT_LEDGER_MAX_BYTES)
        if err:
            print(f"warning: could not append ledger: {err}", file=sys.stderr)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_text(report))

    # Fail-closed: degraded → nonzero so a cron wrapper can act.
    return 0 if report.healthy else 2


if __name__ == "__main__":
    sys.exit(main())
