"""Model routing as a harness component (second-brain arc WS-E).

`.claude/rules/model_advisor.md` says: judge each task's tier against the running
model and say so — upshift review/novel-design work, downshift mechanical work,
stay quiet when right-sized. That has been a DISPOSITION a human applies while
reading a rules file. This turns it into a re-derivable, evidence-quoting CALL:

    route(task_kind, env) -> Recommendation

grounded in measured ground truth, never vibes —
  * the LOCAL-brain eval ledger (can tier-L actually do this kind of work?),
  * the calibration ledger folded by model_id (how reliable is the running
    model's own "done" claims?),
  * the capability gradient (qth = frontier+API available; fleet = R+mini+L;
    field = R/L only — project_fable5_window_plan_2026_07_16).

Two hard invariants, both from the operator's standing guidance:
  * ADVISORY, never a gate. route() RECOMMENDS a tier and quotes why; it never
    blocks work. Human judgment still stands — it just stands on ground it
    cannot fabricate. (A weaker-than-frontier router is therefore SAFE: the
    worst case is a suboptimal suggestion a human ignores.)
  * MODEL-AGNOSTIC. route() emits a TIER, never a concrete model id. Tiers
    resolve to models at the edge via env var (`TIER_MODEL_ENV`), so a model
    retirement is a one-env-var change and "Fable is always here" never becomes
    load-bearing.

Self-scoring: `record_routing()` appends each recommendation to the routing
ledger. Verdicts (did routing tier-L for triage HOLD UP?) are re-derived from
the eval ledger by the machinery, never hand-written — the calibration-ledger
convention (an `open` routing is an honest terminal state).

Stdlib only; no LLM in this path.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from ._util import resolve_home
from .history import append_jsonl

# ── the tier ladder (ascending capability) ─────────────────────────────────
# R < L < fast < opus < frontier. The rank is the ONLY ordering used; names are
# stable strings so ledger rows stay comparable across model swaps.
TIERS = ("rules", "local", "fast", "opus", "frontier")
_RANK = {t: i for i, t in enumerate(TIERS)}


def tier_rank(tier: str) -> int:
    """Ladder position (higher = more capable). Unknown tier → -1 (below all)."""
    return _RANK.get(tier, -1)


# ── task kind → the tier the work IDEALLY wants (from model_advisor.md) ─────
TASK_TIER = {
    # frontier-class: adversarial review, novel design, ambiguous-evidence work
    "adversarial_review": "frontier",
    "novel_design": "frontier",
    "incident_forensics": "frontier",
    "security_refactor": "frontier",
    # opus-class: day-to-day dev with test guards, deploys, probes, fleet ops
    "dev": "opus",
    "deploy": "opus",
    "probe_authoring": "opus",
    "docs": "opus",
    "fix_with_tests": "opus",
    "fleet_ops": "opus",
    # fast/haiku-class: mechanical sweeps, formatting, triage, lookups
    "mechanical": "fast",
    "formatting": "fast",
    "log_triage": "fast",
    "single_file_lookup": "fast",
    # local (tier-L, eval-gated): the degraded-brain ladder's own jobs
    "cadence_triage": "local",
    "compile_rule": "local",
    "offline_oracle": "local",
    # everything compiled downward — the always-on tier
    "rules_probe": "rules",
}

# task kind → the eval KIND that measures tier-L competence for it (or None).
# Only these three are things the local tier is ever asked to do, so only these
# can be eval-grounded; every other kind wants a tier the eval never measures.
TASK_EVAL_KIND = {
    "cadence_triage": "triage",
    "compile_rule": "compile",
    "offline_oracle": "oracle",
}

# ── capability gradient → the CEILING tier you can RELY on in each env ──────
# qth (the lab/manager box) may assume a frontier session + API of any size.
# fleet boxes get R+mini+L; API access is incidental, NEVER load-bearing, so the
# reliable ceiling is local. field kits carry R/L ONLY (no API even incidentally).
ENV_CEILING = {"qth": "frontier", "fleet": "local", "field": "local"}
ENVS = tuple(ENV_CEILING.keys())

# The eval gate the weekly cron uses — the same bar for "is tier-L trustworthy".
DEFAULT_EVAL_GATE = 0.85

# Tier → the env var that names the concrete model for that tier (model-agnostic
# edge resolution; the router never emits a model id itself). Documented, not
# consumed here — the CONSUMER (cadence launcher, assistant) reads these.
TIER_MODEL_ENV = {
    "frontier": "MINI_DUDEAI_CADENCE_MODEL",
    "opus": "MINI_DUDEAI_CADENCE_MODEL",
    "fast": "MINI_DUDEAI_HAIKU_MODEL",
    "local": "MINI_DUDEAI_OLLAMA_MODEL",
    "rules": None,
}

ROUTING_LEDGER_BASENAME = "model_routing_ledger.jsonl"
_ROUTING_LEDGER_MAX_BYTES = 1_000_000


@dataclass
class Recommendation:
    task_kind: str
    env: str
    base_tier: str            # the tier the kind ideally wants
    ceiling: str              # the highest tier this env can rely on
    recommended_tier: str     # what to actually use here
    disposition: str          # upshift | downshift | right-sized | capability_gap
    why: str
    evidence: dict = field(default_factory=dict)
    running_tier: Optional[str] = None
    ts: float = 0.0

    def to_row(self) -> dict:
        return asdict(self)


# ── evidence readers ───────────────────────────────────────────────────────

def load_eval_records(path: str) -> list:
    """Eval summary records from the ledger (oldest→newest). Tolerant; [] on any
    read error (absence of evidence, handled by the caller as UNKNOWN)."""
    out: list = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        return []
    return out


def eval_kind_competence(records: list, kind: str) -> tuple:
    """(pass_rate, n) for `kind` from the most-recent record that measured it, or
    (None, 0) when no record covered it — competence UNKNOWN, never a forged 1.0
    (honest_failure_modes #2: absence ≠ healthy)."""
    for rec in reversed(records or []):
        if not isinstance(rec, dict):
            continue
        pk = (rec.get("per_kind") or {}).get(kind)
        if isinstance(pk, dict):
            total = pk.get("total") or 0
            passed = pk.get("passed") or 0
            if total:
                return (round(passed / total, 3), total)
    return (None, 0)


def calib_reliability_by_model(fold_state: dict) -> dict:
    """{model_id: {held, broke, ratio}} from a calibration_ledger.fold() result.
    fold() returns the held/broke claim RECORDS (each carrying model_id), so we
    group them; open claims are not counted (unverified ≠ either)."""
    out: dict = {}
    for bucket, key in (("held", "held"), ("broke", "broke")):
        for rec in (fold_state.get(bucket) or []):
            if not isinstance(rec, dict):
                continue
            mid = rec.get("model_id") or "unknown"
            out.setdefault(mid, {"held": 0, "broke": 0})[key] += 1
    for mid, d in out.items():
        n = d["held"] + d["broke"]
        d["ratio"] = round(d["held"] / n, 3) if n else None
    return out


def detect_env(env_override: Optional[str] = None,
               role: Optional[str] = None) -> str:
    """Best-effort capability-gradient env. Precedence: explicit override →
    MESHFORGE_ROUTER_ENV → primary/manager role = qth → default fleet. A field
    kit must set MESHFORGE_ROUTER_ENV=field explicitly (it is the most-constrained
    env and there is no reliable auto-tell for 'this box is portable')."""
    cand = env_override or os.environ.get("MESHFORGE_ROUTER_ENV")
    if cand in ENVS:
        return cand
    if role == "primary":
        return "qth"
    return "fleet"


# ── the router ─────────────────────────────────────────────────────────────

def route(task_kind: str, env: str, *,
          eval_records: Optional[list] = None,
          model_reliability: Optional[dict] = None,
          running_tier: Optional[str] = None,
          running_model: Optional[str] = None,
          eval_gate: float = DEFAULT_EVAL_GATE,
          now_ts: Optional[float] = None) -> Recommendation:
    """Recommend a tier for `task_kind` in `env`, grounded in measured evidence.

    PURE given the evidence args (eval_records / model_reliability); pass them for
    tests, or let the CLI load them from the ledgers. Never raises on missing
    evidence — unknown competence is surfaced, not averaged into a healthy value.
    """
    now = time.time() if now_ts is None else now_ts
    if env not in ENV_CEILING:
        env = "fleet"  # safest ceiling on an unknown env
    base = TASK_TIER.get(task_kind, "opus")   # unknown kind → conservative opus
    ceiling = ENV_CEILING[env]
    evidence: dict = {"base_tier": base, "ceiling": ceiling, "env": env}

    # 1. Clamp the ideal to what the env can RELY on.
    capability_gap = tier_rank(base) > tier_rank(ceiling)
    recommended = ceiling if capability_gap else base
    why_parts = []
    if capability_gap:
        why_parts.append(
            f"{task_kind} ideally wants {base}, but {env} can only rely on "
            f"{ceiling} — capability gap")
    else:
        why_parts.append(f"{task_kind} is right-sized for {base}")

    # 2. Eval-grounding: never trust tier-L for a kind the eval says it can't do.
    eval_kind = TASK_EVAL_KIND.get(task_kind)
    l_trusted = None
    if recommended == "local" and eval_kind is not None:
        pr, n = eval_kind_competence(eval_records or [], eval_kind)
        evidence["eval_kind"] = eval_kind
        evidence["eval_pass_rate"] = pr
        evidence["eval_n"] = n
        if pr is None:
            l_trusted = None
            why_parts.append(
                f"tier-L competence for {eval_kind} is UNKNOWN (no eval data) — "
                f"treat its output as PROPOSE-only")
        elif pr < eval_gate:
            l_trusted = False
            why_parts.append(
                f"tier-L FAILS the {eval_kind} eval ({pr} < {eval_gate}) — NOT "
                f"trustworthy here; escalate to a higher-env session when possible")
            # On qth we can actually go higher; on fleet/field L is the ceiling.
            if tier_rank(ceiling) > tier_rank("local"):
                recommended = "opus"
        else:
            l_trusted = True
            why_parts.append(
                f"tier-L PASSES the {eval_kind} eval ({pr} ≥ {eval_gate}, n={n})")
        # Always record the verdict when grounding ran — None ("unknown") is a
        # meaningful, surfaced value, not an omission (honest_failure_modes #2).
        evidence["l_trusted"] = l_trusted

    # 3. Surface (do not act on) the running model's own calibration reliability.
    if running_model and model_reliability:
        rel = model_reliability.get(running_model)
        if rel and rel.get("ratio") is not None:
            evidence["running_model_held_ratio"] = rel["ratio"]
            evidence["running_model_n"] = rel["held"] + rel["broke"]

    # 4. Disposition vs the running tier (the model_advisor tell).
    if running_tier:
        evidence["running_tier"] = running_tier
        if tier_rank(recommended) > tier_rank(running_tier):
            disposition = "upshift"
            why_parts.append(
                f"UPSHIFT: recommended {recommended} > running {running_tier} — "
                f"queue for a higher tier (never fake it on the smaller model)")
        elif tier_rank(recommended) < tier_rank(running_tier):
            disposition = "downshift"
            why_parts.append(
                f"DOWNSHIFT: recommended {recommended} < running {running_tier} — "
                f"could batch onto a smaller model / fast mode")
        else:
            disposition = "right-sized"
    else:
        # No running context: a gap is either the env ceiling being too low OR
        # tier-L proving unfit for a kind it was the natural pick for.
        disposition = ("capability_gap"
                       if (capability_gap or l_trusted is False)
                       else "right-sized")

    return Recommendation(
        task_kind=task_kind, env=env, base_tier=base, ceiling=ceiling,
        recommended_tier=recommended, disposition=disposition,
        why="; ".join(why_parts), evidence=evidence,
        running_tier=running_tier, ts=now)


# ── self-scoring ledger (record only; verdicts are re-derived, never written) ─

def routing_ledger_path(home: Optional[str] = None) -> str:
    env = os.environ.get("MODEL_ROUTING_LEDGER_PATH")
    if env:
        return env
    return os.path.join(home or resolve_home(), ROUTING_LEDGER_BASENAME)


def record_routing(rec: Recommendation, path: Optional[str] = None) -> Optional[str]:
    """Append a recommendation to the routing ledger (append-only, rotate-if-over-
    cap via the shared history writer). Returns an error string or None. The row
    is `open` — its verdict is re-derived from the eval ledger later, never
    hand-written (the calibration-ledger convention)."""
    path = path or routing_ledger_path()
    row = {"kind": "routing", **rec.to_row(), "status": "open"}
    return append_jsonl(path, [row], _ROUTING_LEDGER_MAX_BYTES)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _load_default_evidence() -> tuple:
    """(eval_records, model_reliability) from the standard ledgers; empty/{} when
    absent (this box doesn't evaluate the local brain / has no calibration data)."""
    home = resolve_home()
    from .local_brain_eval import EVAL_RESULTS_BASENAME
    eval_records = load_eval_records(os.path.join(home, EVAL_RESULTS_BASENAME))
    reliability: dict = {}
    try:
        from . import calibration_ledger as _cl
        reliability = calib_reliability_by_model(
            _cl.fold(_cl.load_events()))
    except Exception:
        reliability = {}
    return eval_records, reliability


def _detect_role() -> Optional[str]:
    """Best-effort deployment role, GUARDED — the reader is MeshForge-specific, so
    a standalone/MeshAnchor box (or one without deployment.json) simply gets None
    and falls back to the fleet ceiling. Keeps route()/detect_env() generic."""
    try:
        from utils._map_status_endpoints import _read_deployment_role
        return _read_deployment_role("meshforge")
    except Exception:
        return None


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="model-router",
        description="Recommend a compute tier for a task, grounded in the eval + "
                    "calibration ledgers and the capability gradient. Advisory — "
                    "never a gate; emits a TIER, never a model id.")
    ap.add_argument("--task-kind", required=True, choices=sorted(TASK_TIER),
                    help="the kind of work to route.")
    ap.add_argument("--env", default=None, choices=sorted(ENVS),
                    help="capability-gradient env (default: auto-detect).")
    ap.add_argument("--running-tier", default=None, choices=sorted(TIERS),
                    help="the tier of the model running now (for upshift/downshift).")
    ap.add_argument("--running-model", default=None,
                    help="the model id running now (surfaces its calibration ratio).")
    ap.add_argument("--record", action="store_true",
                    help="append the recommendation to the routing ledger.")
    ap.add_argument("--json", action="store_true", help="machine-readable output.")
    args = ap.parse_args(argv)

    env = detect_env(args.env, role=_detect_role())
    eval_records, reliability = _load_default_evidence()
    rec = route(args.task_kind, env, eval_records=eval_records,
                model_reliability=reliability, running_tier=args.running_tier,
                running_model=args.running_model)
    if args.record:
        err = record_routing(rec)
        if err:
            print(f"model-router: routing ledger write FAILED: {err}",
                  file=__import__("sys").stderr)
    if args.json:
        print(json.dumps(rec.to_row(), default=str))
    else:
        print(f"model-router: {args.task_kind} @ {env} -> {rec.recommended_tier} "
              f"({rec.disposition})")
        print(f"  why: {rec.why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
