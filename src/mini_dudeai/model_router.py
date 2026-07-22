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

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from ._util import log_warning, resolve_home
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

# Ledger event kinds. A ``routing`` row is a recommendation (status ``open``); a
# ``routing_verdict`` is a later re-derivation of whether it HELD, minted only by
# the re-derivation machinery — never hand-written (the calibration convention).
ROUTING_KIND = "routing"
ROUTING_VERDICT_KIND = "routing_verdict"
_ROUTING_DEFINITIVE = ("held", "broke")

# Haiku-watcher promotion (haiku_watcher_eval.md decision gate) — the brain-tier
# markers the two calibration histories carry, and the pass_rate margins that
# decide whether api_small (Haiku via API) has EARNED a cadence rung over Ollama.
HAIKU_TIER = "api_small"
LOCAL_BRAIN_TIER = "local"
HAIKU_EARN_MARGIN = 0.10     # api_small ≥ local + this → earned (gate step 4)
HAIKU_REJECT_MARGIN = 0.05   # api_small ≤ local + this → local tier sufficient


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

def _load_jsonl_dicts(path: str) -> list:
    """Parse a JSONL ledger into dict rows (oldest→newest). Tolerant: skips blank,
    torn, and non-dict lines; [] on any read error (absence of evidence, handled
    by the caller as UNKNOWN — never a forged value)."""
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


def load_eval_records(path: str) -> list:
    """Eval summary records from the local-brain eval ledger (oldest→newest)."""
    return _load_jsonl_dicts(path)


def load_routing_events(path: str) -> list:
    """Routing + routing-verdict events from the routing ledger (oldest→newest)."""
    return _load_jsonl_dicts(path)


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


# ── warm-brief consumer: standing routing orientation ──────────────────────

def routing_context_block(now_ts: Optional[float] = None, *,
                          env: Optional[str] = None,
                          eval_records: Optional[list] = None,
                          role: Optional[str] = None) -> str:
    """Render the SessionStart routing-orientation block, or "" when there is
    nothing to say. Surfaces the capability-gradient env + the MEASURED tier-L
    competence (so a session knows what it can safely delegate to local) — the
    data-backed replacement for model_advisor.md's hand-narration. Renders ONLY
    where tier-L is actually evaluated (an eval ledger exists — the manager box);
    a mini-less / non-manager box has no competence to report, so "".
    """
    if env is None:
        env = detect_env(role=role if role is not None else _detect_role())
    if eval_records is None:
        home = resolve_home()
        from .local_brain_eval import EVAL_RESULTS_BASENAME
        eval_records = load_eval_records(
            os.path.join(home, EVAL_RESULTS_BASENAME))
    if not eval_records:
        return ""   # tier-L not evaluated here — nothing measured to say
    ceiling = ENV_CEILING.get(env, "local")
    parts = []
    for kind in ("triage", "compile", "oracle"):
        pr, n = eval_kind_competence(eval_records, kind)
        if pr is None:
            parts.append(f"{kind} —")
        else:
            parts.append(f"{kind} {pr}{'✓' if pr >= DEFAULT_EVAL_GATE else '✗'}")
    lines = [
        f"## 🧭 routing context — env **{env}** (reliable ceiling: {ceiling})",
        f"- tier-L competence (eval): {' · '.join(parts)} — delegate to local "
        f"only where ✓ (model_router grounds this, not vibes)",
        "- route a task: `python3 -m mini_dudeai.model_router --task-kind X` "
        "(advisory; emits a tier, never a model id)",
    ]
    # Routing self-score (WS-E-2b): how the router's own past recommendations held
    # up once the eval ledger could re-derive them. Fail-safe; only on this box.
    try:
        track = format_routing_track_record(
            fold_routing(load_routing_events(routing_ledger_path())))
        if track:
            lines.append(track)
    except Exception:  # noqa: BLE001 — self-score render must never break the brief
        pass
    # Haiku-watcher promotion: is api_small measured-ready for a cadence rung?
    try:
        haiku = format_haiku_promotion(
            haiku_promotion_recommendation(eval_records))
        if haiku:
            lines.append(haiku)
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


# ── self-scoring ledger (record only; verdicts are re-derived, never written) ─

def routing_ledger_path(home: Optional[str] = None) -> str:
    env = os.environ.get("MODEL_ROUTING_LEDGER_PATH")
    if env:
        return env
    return os.path.join(home or resolve_home(), ROUTING_LEDGER_BASENAME)


def make_routing_id(ts: float, task_kind: str, env: str,
                    recommended_tier: str) -> str:
    """Stable short id from the recommendation's identity (deterministic given
    inputs, so tests are not clock/random dependent). A verdict references it."""
    h = hashlib.sha1(
        f"{ts}\x00{task_kind}\x00{env}\x00{recommended_tier}".encode(
            "utf-8", "replace")).hexdigest()
    return h[:12]


def record_routing(rec: Recommendation, path: Optional[str] = None) -> Optional[str]:
    """Append a recommendation to the routing ledger (append-only, rotate-if-over-
    cap via the shared history writer). Returns an error string or None. The row
    carries a stable `id` and status `open` — its verdict is re-derived from the
    eval ledger later, never hand-written (the calibration-ledger convention)."""
    path = path or routing_ledger_path()
    rid = make_routing_id(rec.ts, rec.task_kind, rec.env, rec.recommended_tier)
    row = {"kind": ROUTING_KIND, "id": rid, **rec.to_row(), "status": "open"}
    return append_jsonl(path, [row], _ROUTING_LEDGER_MAX_BYTES)


# ── verdict re-derivation: fold the routing ledger against the eval ledger ────

def competence_after(records: list, kind: str, after_ts: float,
                     *, tier: str = LOCAL_BRAIN_TIER) -> tuple:
    """(pass_rate, n) for `kind` from the most-recent LOCAL-tier eval record
    STRICTLY newer than `after_ts`, or (None, 0). Independent later evidence only
    — a routing is never re-derived against the same eval record that produced it
    (that would be circular; mirrors the calibration ledger requiring a fresh
    honest_status run on the claimed head)."""
    best = None
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        if (rec.get("brain_tier") or LOCAL_BRAIN_TIER) != tier:
            continue
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)) or ts <= after_ts:
            continue
        pk = (rec.get("per_kind") or {}).get(kind)
        if isinstance(pk, dict):
            total = pk.get("total") or 0
            passed = pk.get("passed") or 0
            if total and (best is None or ts > best[0]):
                best = (ts, round(passed / total, 3), total)
    return (best[1], best[2]) if best else (None, 0)


def fold_routing(events: list) -> dict:
    """Reduce the routing event log to current self-score state. Each routing
    row's LATEST verdict (by ts) decides its bucket: `held`, `broke`, or — with
    no definitive verdict — `open` (unverified, NOT counted as either). The ratio
    is held/(held+broke) over routings that GOT re-derived, or None when none
    have (honest denominator; never a fabricated 100% from an empty set). Legacy
    rows without an `id` cannot be verdicted and are excluded."""
    routings: dict = {}
    latest_verdict: dict = {}
    for ev in events:
        k = ev.get("kind")
        if k == ROUTING_KIND:
            rid = ev.get("id")
            if isinstance(rid, str):
                routings[rid] = ev
        elif k == ROUTING_VERDICT_KIND:
            rid = ev.get("routing_id")
            outcome = ev.get("outcome")
            ts = ev.get("ts")
            if (isinstance(rid, str) and outcome in _ROUTING_DEFINITIVE
                    and isinstance(ts, (int, float))):
                prev = latest_verdict.get(rid)
                if prev is None or ts >= prev[0]:
                    latest_verdict[rid] = (ts, outcome)
    held, broke, open_ = [], [], []
    for rid, rec in routings.items():
        v = latest_verdict.get(rid)
        if v is None:
            open_.append(rec)
        elif v[1] == "held":
            held.append(rec)
        else:
            broke.append(rec)
    n_def = len(held) + len(broke)
    return {
        "n_total": len(routings), "n_held": len(held), "n_broke": len(broke),
        "n_open": len(open_), "ratio": (len(held) / n_def) if n_def else None,
        "held": held, "broke": broke, "open": open_,
    }


def rederive_routing(routing_events: list, eval_records: list, now_ts: float,
                     *, eval_gate: float = DEFAULT_EVAL_GATE) -> list:
    """Return NEW routing_verdict events for open, eval-groundable routings.

    Only a routing that ASSERTED tier-L competence (`recommended_tier == local`
    AND `evidence.l_trusted is True`) can flip: a LATER eval that still passes
    that kind → `held`; one that now fails → `broke` (we routed local to a tier
    that proved unfit). Every other routing (higher tier, no positive competence
    claim, no later evidence) stays `open` — an honest terminal state. Nothing is
    manufactured from absence (honest_failure_modes #2)."""
    state = fold_routing(routing_events)
    new: list = []
    for rec in state["open"]:
        if rec.get("recommended_tier") != "local":
            continue
        ev = rec.get("evidence") or {}
        if ev.get("l_trusted") is not True:
            continue  # the routing made no positive competence assertion to re-check
        eval_kind = ev.get("eval_kind")
        if not eval_kind:
            continue
        pr, n = competence_after(eval_records, eval_kind, rec.get("ts") or 0)
        if pr is None:
            continue  # no independent later evidence — leave open
        if pr >= eval_gate:
            new.append({"kind": ROUTING_VERDICT_KIND, "routing_id": rec.get("id"),
                        "ts": now_ts, "outcome": "held",
                        "detail": f"tier-L still passes {eval_kind} "
                                  f"({pr}≥{eval_gate}, n={n}) on later eval"})
        else:
            new.append({"kind": ROUTING_VERDICT_KIND, "routing_id": rec.get("id"),
                        "ts": now_ts, "outcome": "broke",
                        "detail": f"tier-L now FAILS {eval_kind} "
                                  f"({pr}<{eval_gate}) — routed local to an unfit tier"})
    return new


def rederive_routing_and_persist(path: Optional[str] = None,
                                 eval_records: Optional[list] = None,
                                 now_ts: Optional[float] = None,
                                 *, eval_gate: float = DEFAULT_EVAL_GATE) -> dict:
    """Load the routing ledger, re-derive open routings against the eval ledger,
    persist any definitive verdicts, and return the folded state AFTER. An append
    failure is logged (honest_failure_modes #9), never raised — the warm-brief
    consumer must never break on it."""
    now_ts = time.time() if now_ts is None else now_ts
    path = path or routing_ledger_path()
    events = load_routing_events(path)
    if eval_records is None:
        home = resolve_home()
        from .local_brain_eval import EVAL_RESULTS_BASENAME
        eval_records = load_eval_records(os.path.join(home, EVAL_RESULTS_BASENAME))
    new = rederive_routing(events, eval_records, now_ts, eval_gate=eval_gate)
    if new:
        err = append_jsonl(path, new, _ROUTING_LEDGER_MAX_BYTES)
        if err:
            log_warning(f"model_router: could not persist {len(new)} routing "
                        f"verdict(s): {err}")
        events = events + new
    return fold_routing(events)


def format_routing_track_record(state: dict) -> str:
    """Render the routing self-score as a compact warm-brief line, or "" when
    nothing is tracked. BROKE routings surface loudly — those are the "I routed
    work to a tier that couldn't do it" cases the operator should see."""
    n = state.get("n_total", 0)
    if not n:
        return ""
    held, broke, open_ = state["n_held"], state["n_broke"], state["n_open"]
    n_def = held + broke
    if n_def:
        pct = round(100 * state["ratio"])
        icon = "🟢" if broke == 0 else "⚠️"
        lines = [f"- {icon} routing self-score: {n} recorded · re-derived {held} "
                 f"held / {broke} broke ({pct}%) · {open_} open"]
    else:
        lines = [f"- 🔵 routing self-score: {n} recorded · none re-derived yet · "
                 f"{open_} open"]
    for rec in state.get("broke", [])[:2]:
        tk = rec.get("task_kind", "?")
        ek = (rec.get("evidence") or {}).get("eval_kind", "?")
        lines.append(f"  - ⚠️ routed {tk}→local but tier-L later FAILED {ek} — "
                     f"the recommendation did not hold")
    return "\n".join(lines)


# ── haiku-watcher promotion: has api_small EARNED a cadence rung? ─────────────

def _latest_eval_for_tier(eval_records: list, tier: str) -> Optional[dict]:
    """The newest eval summary produced by `tier` (brain_tier; absent→local), or
    None. The two calibration histories never blend — api_small and local are
    read separately (haiku_watcher_eval.md invariant 4)."""
    best = None
    for rec in eval_records or []:
        if not isinstance(rec, dict):
            continue
        if (rec.get("brain_tier") or LOCAL_BRAIN_TIER) != tier:
            continue
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if best is None or ts > (best.get("ts") or 0):
            best = rec
    return best


def haiku_promotion_recommendation(
        eval_records: list, *, earn_margin: float = HAIKU_EARN_MARGIN,
        reject_margin: float = HAIKU_REJECT_MARGIN,
        gate: float = DEFAULT_EVAL_GATE) -> dict:
    """Re-derivable adopt/reject/operator-call for promoting api_small (Haiku via
    API) into a cadence rung above tier-L — haiku_watcher_eval.md's decision gate
    made a MEASURED call rather than a human declaration.

    Returns {status, promote, why, evidence}. Honest about absence: with no
    api_small eval data yet the status is `no_candidate_data` and promote is None
    — never a forged verdict (honest_failure_modes #2). ADVISORY: it recommends;
    wiring stays a deliberate canary-first step."""
    cand = _latest_eval_for_tier(eval_records, HAIKU_TIER)
    base = _latest_eval_for_tier(eval_records, LOCAL_BRAIN_TIER)
    if cand is None:
        return {"status": "no_candidate_data", "promote": None, "evidence": {},
                "why": "no api_small eval records yet — run the haiku head-to-head "
                       "(local_brain_eval --backend anthropic) before the router "
                       "can judge promotion"}
    cpr = cand.get("pass_rate")
    bpr = base.get("pass_rate") if base else None
    ev = {"api_small_pass_rate": cpr, "local_pass_rate": bpr, "gate": gate}
    if not isinstance(cpr, (int, float)):
        return {"status": "no_candidate_data", "promote": None, "evidence": ev,
                "why": "api_small eval record carries no pass_rate"}
    if cpr < gate:
        return {"status": "rejected", "promote": False, "evidence": ev,
                "why": f"api_small {cpr} < gate {gate} — not trustworthy at all"}
    if not isinstance(bpr, (int, float)):
        return {"status": "operator_call", "promote": None, "evidence": ev,
                "why": f"api_small passes the gate ({cpr}≥{gate}) but there is no "
                       f"local baseline to compare — operator call"}
    delta = round(cpr - bpr, 3)
    ev["delta"] = delta
    if delta >= earn_margin:
        return {"status": "earned", "promote": True, "evidence": ev,
                "why": f"api_small {cpr} ≥ local {bpr}+{earn_margin} (Δ{delta}) — "
                       f"earned a cadence rung; queue canary-first wiring"}
    if delta <= reject_margin:
        return {"status": "rejected", "promote": False, "evidence": ev,
                "why": f"api_small {cpr} ≤ local {bpr}+{reject_margin} (Δ{delta}) — "
                       f"local tier is sufficient; reject the rung"}
    return {"status": "operator_call", "promote": None, "evidence": ev,
            "why": f"api_small {cpr} vs local {bpr} (Δ{delta}) between the "
                   f"reject/earn margins — operator call"}


def format_haiku_promotion(rec: dict) -> str:
    """Render the haiku promotion recommendation as a warm-brief line, or "" when
    there is no candidate data (don't inject noise before the head-to-head runs)."""
    if not rec or rec.get("status") == "no_candidate_data":
        return ""
    icon = {"earned": "⬆️", "rejected": "⛔", "operator_call": "⚖️"}.get(
        rec.get("status"), "•")
    return f"- {icon} haiku-watcher promotion: {rec.get('status')} — {rec.get('why', '')}"


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
    ap.add_argument("--task-kind", choices=sorted(TASK_TIER),
                    help="the kind of work to route (required unless --rederive/"
                         "--haiku-check).")
    ap.add_argument("--env", default=None, choices=sorted(ENVS),
                    help="capability-gradient env (default: auto-detect).")
    ap.add_argument("--running-tier", default=None, choices=sorted(TIERS),
                    help="the tier of the model running now (for upshift/downshift).")
    ap.add_argument("--running-model", default=None,
                    help="the model id running now (surfaces its calibration ratio).")
    ap.add_argument("--record", action="store_true",
                    help="append the recommendation to the routing ledger.")
    ap.add_argument("--l-trusted-gate", action="store_true",
                    help="shell gate for a local-tier step: exit 0 if tier-L is "
                         "TRUSTED for this task_kind (eval passes), 2 if NOT "
                         "trusted, 3 if UNKNOWN / not a local kind. Only an "
                         "explicit 2 should make a caller skip local work "
                         "(uncertainty != untrusted).")
    ap.add_argument("--rederive", action="store_true",
                    help="re-derive open routing recommendations against the eval "
                         "ledger (persist held/broke verdicts) and print the "
                         "self-score. No task-kind needed.")
    ap.add_argument("--haiku-check", action="store_true",
                    help="print the measured api_small (Haiku) promotion "
                         "recommendation from the eval ledger. No task-kind needed.")
    ap.add_argument("--json", action="store_true", help="machine-readable output.")
    args = ap.parse_args(argv)

    env = detect_env(args.env, role=_detect_role())
    eval_records, reliability = _load_default_evidence()

    if args.rederive:
        state = rederive_routing_and_persist(eval_records=eval_records)
        if args.json:
            print(json.dumps({k: state[k] for k in
                              ("n_total", "n_held", "n_broke", "n_open", "ratio")}))
        else:
            track = format_routing_track_record(state)
            print(track or "model-router: no routing recommendations recorded yet.")
        return 0

    if args.haiku_check:
        rec = haiku_promotion_recommendation(eval_records)
        if args.json:
            print(json.dumps(rec, default=str))
        else:
            print(f"model-router: haiku promotion → {rec['status']}\n  {rec['why']}")
        return 0

    if not args.task_kind:
        ap.error("--task-kind is required unless --rederive or --haiku-check")
    rec = route(args.task_kind, env, eval_records=eval_records,
                model_reliability=reliability, running_tier=args.running_tier,
                running_model=args.running_model)
    if args.record:
        err = record_routing(rec)
        if err:
            print(f"model-router: routing ledger write FAILED: {err}",
                  file=__import__("sys").stderr)
    if args.l_trusted_gate:
        lt = rec.evidence.get("l_trusted")
        return 0 if lt is True else (2 if lt is False else 3)
    if args.json:
        print(json.dumps(rec.to_row(), default=str))
    else:
        print(f"model-router: {args.task_kind} @ {env} -> {rec.recommended_tier} "
              f"({rec.disposition})")
        print(f"  why: {rec.why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
