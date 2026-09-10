"""Calibration ledger — mini-dudeai's observe→fire pattern, turned on ME.

Born 2026-06-15 with `.claude/rules/calibrated_claims.md` and the reflective
claim-gate (`scripts/claim_gate.py`). Those two make me *say* calibrated things;
this makes the calibration **measurable**. The operator's concern was the math:
*"when you say 100%% and we do it N more times, the math is wrong."* A ledger
that records every VERIFIED claim and later re-derives whether it HELD turns that
from an assertion into a number we can both watch — and shrink.

Append-only event log, exactly like `history.jsonl` (reusing its shared
`append_jsonl` posture: rotate-if-over-cap, repair-torn-tail, swallow-OSError).
Two event kinds:

  * ``claim``   — one per VERIFIED completion claim of consequence:
                  {kind, id, ts, session_id, model_id, claim_class, claim_text,
                   evidence, head_full, status:"open"}.  ``model_id`` is recorded
                  so a reliability shift after a model swap (the Fable 5 → Opus
                  lurch) is detectable, not invisible.
  * ``verdict`` — a later re-derivation of a claim's truth:
                  {kind, claim_id, ts, outcome:"held"|"broke", detail}.

Re-derivation is deliberately CONSERVATIVE (honest_failure_modes #2: unobservable
≠ resolved). A claim flips to ``held``/``broke`` ONLY when an external
honest_status verdict definitively covers the head it was claimed on. Everything
else stays ``open`` and is surfaced as *unverified*, never silently "recovered".
The heavyweight re-run-the-suite re-derivation is a daily cron's job; this module
provides the substrate and the cheap warm-start pass.

Stdlib only. Pure folding/decision functions are unit-tested (RED + GREEN); the
thin I/O uses the shared mini helpers.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time

from ._util import (APP_VERDICT_SUBDIR, atomic_write_json, log_warning,
                    resolve_home)
from .history import append_jsonl

#: 2 MB cap — matches the #79 ledger-rotation bound. Plenty of forensic history
#: at a handful of claims per session, but bounded on an unattended box.
DEFAULT_LEDGER_MAX_BYTES = 2_000_000

_DEFINITIVE = ("held", "broke")

#: Basename of the verdict marker honest_status.sh writes and the claim-gate +
#: warm-start read. ONE constant — the path was typed in three places
#: (claim_gate, warmstart, honest_status) with three different HOME fallbacks
#: (review 2026-09-09, finding 18), so writer and reader diverged under an
#: unset HOME (cron/daemon context).
VERDICT_MARKER_BASENAME = "honest_verdict.json"


def ledger_path(home: str | None = None) -> str:
    """The one ledger location: $CALIBRATION_LEDGER_PATH → <mini home>/…jsonl.

    Lives in the mini home next to ``mini_dudeai_brief.md``/``…_state.json`` so
    the warm-start emitter reads it with no extra path config."""
    env = os.environ.get("CALIBRATION_LEDGER_PATH")
    if env:
        return env
    home = home or resolve_home()
    return os.path.join(home, "calibration_ledger.jsonl")


def verdict_marker_path() -> str:
    """THE honest_status verdict-marker path: $HONEST_VERDICT_PATH, else
    ``<home>/<APP_VERDICT_SUBDIR>/honest_verdict.json`` where home is $HOME
    or, when HOME is unset, the pw-database home (``expanduser``). Writer
    (honest_status.sh) and both readers (claim_gate, warmstart) resolve
    through this one function."""
    env = os.environ.get("HONEST_VERDICT_PATH")
    if env:
        return env
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, APP_VERDICT_SUBDIR, VERDICT_MARKER_BASENAME)


def repo_head(repo: str) -> str | None:
    """Full HEAD sha of ``repo``, or None on ANY git failure.

    ``-c safe.directory=<repo>`` is load-bearing: under git's dubious-ownership
    refusal (root/service-account host, a worktree owned by another user) a
    plain ``rev-parse`` fails and None means "no HEAD" downstream — the gate
    then blocks every strong claim beside a fresh green marker, and the ledger
    silently stops re-deriving. Shared by warmstart and claim_gate so the two
    readers of one repo cannot disagree about what HEAD is (finding 18)."""
    try:
        out = subprocess.run(["git", "-c", f"safe.directory={repo}",
                              "-C", repo, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def tree_is_dirty(repo: str) -> bool:
    """True when the working tree carries uncommitted OR untracked changes —
    or when git cannot tell. Unknown → dirty is the REFUSING direction: a
    marker stamped ``dirty_tree`` backs no fleet-strength claim and mints no
    verdict, so a git failure can only make the ledger quieter, never a
    fabricated ``held`` for code other than HEAD (review 2026-09-07/09-09).
    ONE predicate for honest_status.sh and calibration_reverify.sh; the
    reverify marker carried no such flag at all (finding 11, second leg)."""
    try:
        out = subprocess.run(["git", "-c", f"safe.directory={repo}",
                              "-C", repo, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return True
    if out.returncode != 0:
        return True
    return bool(out.stdout.strip())


def marker_refusal(marker) -> str | None:
    """Why a verdict marker cannot back a fleet-strength claim, or None if it
    can. THE one predicate both readers apply (claim_gate.marker_satisfies and
    rederive_open carried the rule verbatim, twice — finding 11):

      * not a dict → "no marker"
      * ``ran_full_suite`` falsy → a --quick run verified no suite
      * ``scope_narrowed`` → HONEST_BOXES override / no fleet SSOT: the run
        verified something other than "this HEAD, fleet-wide"
      * ``dirty_tree`` → uncommitted/untracked edits: the run verified a tree
        other than HEAD

    A marker without the two flags (older writer, test fixture) is judged on
    the fields it has. The string NAMES the field, so a reader can render the
    refusal instead of the self-contradiction "no fresh verdict covers HEAD"
    beside "latest verdict: N/N PASS on HEAD" (finding 15)."""
    if not isinstance(marker, dict):
        return "no marker"
    if not marker.get("ran_full_suite"):
        return "ran_full_suite is false — a --quick run verified no suite"
    if marker.get("scope_narrowed"):
        return ("scope_narrowed — the run's box list was narrowed (HONEST_BOXES "
                "override or no fleet_hosts SSOT on this box), so it verified "
                "something other than this HEAD fleet-wide")
    if marker.get("dirty_tree"):
        return ("dirty_tree — the tree carried uncommitted or untracked files, "
                "so the run verified code other than HEAD (commit or stash, "
                "then re-run)")
    return None


def build_marker(head_full: str, exit_code: int, *, instrument: str,
                 summary: str, ran_full_suite: bool, scope_narrowed: bool,
                 dirty_tree: bool, ts: float | None = None, **extra) -> dict:
    """The ONE marker shape every producer writes (honest_status.sh,
    calibration_reverify.sh). Both used to hand-type the dict, and the
    reverify copy lacked ``scope_narrowed``/``dirty_tree`` entirely, so a
    reverify on a dirty tree sailed past ``marker_refusal`` (None is falsy)
    and minted ``held`` for code other than HEAD (finding 11)."""
    ts = time.time() if ts is None else ts
    m = {
        "head_full": head_full,
        "exit_code": int(exit_code),
        "ts": ts,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        "summary": summary,
        # Producer name, separate from the message: rederive_open attributes
        # the verdict to `instrument`; `summary` is display text (2026-07-31).
        "instrument": instrument,
        "ran_full_suite": bool(ran_full_suite),
        "scope_narrowed": bool(scope_narrowed),
        "dirty_tree": bool(dirty_tree),
    }
    m.update(extra)
    return m


def write_marker(path: str, marker: dict) -> None:
    """Atomic (unique tmp + fsync + replace) marker write; creates the parent
    dir. Raises OSError — the caller (honest_status.sh) owns the witness."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    atomic_write_json(path, marker)


def make_claim_id(ts: float, claim_text: str, head_full: str) -> str:
    """Stable short id from the claim's identity. Deterministic given inputs
    (so tests are not clock/random dependent)."""
    h = hashlib.sha1(
        f"{ts}\x00{claim_text}\x00{head_full}".encode("utf-8", "replace")
    ).hexdigest()
    return h[:12]


#: The ``end`` field (2026-09-09 → removed the same day, finding 16). It was
#: meant to answer the operator's "who is watching who?" — which END (message
#: delivered / truth in-app / operator time / harness) a claim served — but
#: the only automatic writer (claim_gate) has no honest way to derive a USER
#: end from a Stop-hook transcript, every production row folded to "unknown"
#: without a log, and no reader anywhere rendered the tally. A writer with no
#: reader beside a metric with no producer (honest_failure_modes #4), added
#: inside the harness freeze. The measurement it wanted is the commit split
#: harness_restraint.md re-runs on 2026-10-09 (git paths, not claim text);
#: that is the honest instrument for the question. Old rows carrying
#: ``"end": "unknown"`` are tolerated by ``fold`` as any unknown key is.


def record_claim(claim_text: str, claim_class: str, evidence: str,
                 head_full: str, *, model_id: str | None = None,
                 session_id: str | None = None, source: str | None = None,
                 ts: float | None = None, path: str | None = None,
                 max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Append one ``claim`` event and return the record (with its generated id).

    ``source`` is the feed's provenance — ``"claim_gate"`` for the automatic
    Stop-hook feed, ``"manual"`` for a deliberate hand-logged claim (sanctioned
    by calibrated_claims rule 6 when the evidence lives outside the gate's
    marker: CI conclusion, live drill, fleet check). Recorded so held-rates can
    later be split per feed. Pre-2026-07-03 records lack the key — consumers
    must ``.get`` it.

    Best-effort persistence: an append failure leaves a log witness
    (honest_failure_modes #9) but never raises — recording a claim must not be
    able to crash whatever made the claim."""
    ts = time.time() if ts is None else ts
    path = path or ledger_path()
    rec = {
        "kind": "claim",
        "id": make_claim_id(ts, claim_text, head_full),
        "ts": ts,
        "session_id": session_id,
        "model_id": model_id,
        "source": source,
        "claim_class": claim_class,
        "claim_text": claim_text,
        "evidence": evidence,
        "head_full": head_full,
        "status": "open",
    }
    err = append_jsonl(path, [rec], max_bytes)
    if err:
        log_warning(f"calibration_ledger: could not record claim {rec['id']}: {err}")
    return rec


def record_annotation(claim_id: str, topic: str, note: str, *,
                      ts: float | None = None, path: str | None = None,
                      extra: dict | None = None,
                      max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Append one ``annotation`` event — a note ABOUT a claim's verdict.

    An annotation qualifies the EVIDENCE behind a verdict without changing the
    verdict. ``fold`` never moves a claim between held/broke/open on account of
    one; it only counts them, so the ratio is quoted with its caveat attached.

    This is deliberately NOT a verdict. Verdicts are minted only by the
    re-derivation machinery and are never hand-written (calibrated_claims rule
    6) — a self-issued "held" is the patched-tally disease the ledger exists to
    catch. Saying "this verdict's evidence was weaker than it looks" is the
    opposite move: it can only reduce confidence, never manufacture it, which
    is why it is safe to write by hand.
    """
    ts = time.time() if ts is None else ts
    path = path or ledger_path()
    rec = {"kind": "annotation", "claim_id": claim_id, "ts": ts,
           "topic": topic, "note": note}
    if extra:
        # The reserved keys are what MAKE this an annotation: an extra dict
        # carrying {"kind": "verdict", "outcome": "held"} would otherwise
        # override them and mint a hand-written verdict through the one door
        # the docstring promises cannot (ultra review 2026-07-31). The
        # unforgeability lives in code, not caller convention.
        reserved = {"kind", "claim_id", "ts", "topic", "note", "outcome"}
        dropped = sorted(k for k in extra if k in reserved)
        rec.update({k: v for k, v in extra.items() if k not in reserved})
        if dropped:
            log_warning(f"calibration_ledger: annotation extra tried to set "
                        f"reserved key(s) {dropped} — dropped")
    err = append_jsonl(path, [rec], max_bytes)
    if err:
        log_warning(f"calibration_ledger: could not record annotation for "
                    f"{claim_id}: {err}")
    return rec


def record_verdict(claim_id: str, outcome: str, detail: str = "", *,
                   ts: float | None = None, path: str | None = None,
                   max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Append one ``verdict`` event (a later re-derivation of a claim's truth).

    Verdicts are minted ONLY by re-derivation machinery (the reverify cron,
    the warm-start cheap path) — NEVER hand-written by the claimant. Claims
    may enter by hand (see ``record_claim`` ``source``); a self-issued "held"
    is the patched-tally disease the ledger exists to catch."""
    ts = time.time() if ts is None else ts
    path = path or ledger_path()
    rec = {"kind": "verdict", "claim_id": claim_id, "ts": ts,
           "outcome": outcome, "detail": detail}
    err = append_jsonl(path, [rec], max_bytes)
    if err:
        log_warning(f"calibration_ledger: could not record verdict for "
                    f"{claim_id}: {err}")
    return rec


def load_events(path: str | None = None) -> list[dict]:
    """Parse the ledger into a list of event dicts. Skips blank, torn, and
    unparseable lines (torn-tail safe). Never raises — a missing file is []."""
    import json
    path = path or ledger_path()
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return out
    except OSError as e:
        log_warning(f"calibration_ledger: could not read {path}: {e}")
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue  # torn / malformed line — skip, never fuse
        if isinstance(obj, dict):
            out.append(obj)
    return out


def fold(events: list[dict]) -> dict:
    """Reduce the event log to current calibration state.

    For each claim, its LATEST verdict (by ts) decides its bucket: ``held``,
    ``broke``, or — with no definitive verdict — ``open`` (unverified, NOT
    counted as either; unobservable ≠ resolved). The ratio is held / (held +
    broke) over claims that GOT a definitive re-derivation, or None when none
    have — honestly "of the claims we could re-check, X%% held", never a
    fabricated 100%% / 0%% from an empty set.
    """
    claims: dict[str, dict] = {}
    # latest verdict per claim_id: (ts, outcome)
    latest_verdict: dict[str, tuple] = {}
    # claim_id -> [annotation, ...]. Annotations are METADATA about the quality
    # of a verdict's evidence; they never move a claim between buckets. Kept
    # separate from verdicts on purpose: a verdict is the re-derivation
    # machinery's output and is never hand-written (calibrated_claims rule 6),
    # while an annotation is a note ABOUT one and may be.
    annotations: dict[str, list] = {}
    for ev in events:
        kind = ev.get("kind")
        if kind == "claim":
            cid = ev.get("id")
            if isinstance(cid, str):
                claims[cid] = ev  # a later claim record with same id wins
        elif kind == "annotation":
            cid = ev.get("claim_id")
            if isinstance(cid, str):
                annotations.setdefault(cid, []).append(ev)
        elif kind == "verdict":
            cid = ev.get("claim_id")
            outcome = ev.get("outcome")
            ts = ev.get("ts")
            if (isinstance(cid, str) and outcome in _DEFINITIVE
                    and isinstance(ts, (int, float))):
                prev = latest_verdict.get(cid)
                if prev is None or ts >= prev[0]:
                    latest_verdict[cid] = (ts, outcome)

    held, broke, open_ = [], [], []
    for cid, rec in claims.items():
        v = latest_verdict.get(cid)
        if v is None:
            open_.append(rec)
        elif v[1] == "held":
            held.append(rec)
        else:
            broke.append(rec)

    n_def = len(held) + len(broke)
    ratio = (len(held) / n_def) if n_def else None
    # How many of the claims that got a definitive verdict carry an annotation
    # qualifying that verdict's evidence. Surfaced beside the ratio so the
    # headline number is never quoted without its caveat.
    n_annotated = sum(1 for rec in held + broke if annotations.get(rec.get("id")))
    return {
        "n_total": len(claims),
        "n_held": len(held),
        "n_broke": len(broke),
        "n_open": len(open_),
        "ratio": ratio,
        "held": held,
        "broke": broke,
        "open": open_,
        "annotations": annotations,
        "n_annotated": n_annotated,
    }


def rederive_open(events: list[dict], head_full_now: str | None,
                  marker: dict | None, now_ts: float) -> list[dict]:
    """Cheap warm-start re-derivation. Returns NEW verdict events (only the
    DEFINITIVE held/broke ones) for currently-open claims.

    A claim can be cheaply re-derived ONLY when a full honest_status verdict
    covers the SAME head it was claimed on:
      * exit_code 0 on that head → ``held`` (re-confirmed green)
      * exit_code 1 on that head → ``broke`` (proven not-green on the head I
        called green — the exact "you said 100%%" miss made visible)
    Any other state (head moved on, no marker, --quick/exit 2 = couldn't verify)
    yields NO event — the claim stays open and is surfaced as unverified. We
    never manufacture a verdict from absence (honest_failure_modes #2).

    THE MARKER MUST POST-DATE THE CLAIM (finding 11, 2026-09-09). claim_gate
    records only marker-BACKED claims, so the honest_status marker that
    ADMITTED a claim is, on the next warm start, still the freshest marker on
    that head — and this function re-derived the claim as ``held`` from it:
    zero new evidence, a self-confirming verdict. Live ledger that day: 9 of
    47 held verdicts landed within 15 min of their claim. A marker is new
    evidence only if it was PRODUCED AFTER the claim, so ``marker.ts`` must be
    strictly greater than the claim's ``ts``; a marker or claim with no
    numeric ts cannot establish that and mints nothing."""
    state = fold(events)
    # ONE predicate shared with claim_gate.marker_satisfies (review
    # 2026-09-07 wrote the rule twice; finding 11 folded it): a --quick run,
    # a run narrowed by HONEST_BOXES / no fleet SSOT, or a dirty tree verified
    # something other than "this HEAD, fleet-wide" — minting `held` from it
    # would inflate the held-rate with exactly the run class the gate refuses.
    if marker_refusal(marker) is not None:
        return []
    m_head = marker.get("head_full")
    m_exit = marker.get("exit_code")
    m_ts = marker.get("ts")
    if not m_head or m_head != head_full_now:
        return []
    if not isinstance(m_ts, (int, float)) or isinstance(m_ts, bool):
        return []  # undated evidence cannot be shown to be NEW evidence
    # The verdict must name the instrument that actually produced it. This
    # previously hardcoded "honest_status ..." for EVERY marker; the first fix
    # then read `summary` as the producer name — but honest_status writes its
    # verdict MESSAGE there ("12 checks: 12 OK (fully verified green)"), so the
    # majority path still named no instrument and doubled the text (review
    # 2026-07-31, finding 8: attribution only worked for calibration_reverify
    # because its message HAPPENS to start with its own name). Producer and
    # message are separate fields now: `instrument` names the tool, `summary`
    # rides along as the message. A summary-only marker is attributed as
    # exactly what it is — a message from an unnamed producer — never dressed
    # up as a tool name; a bare legacy marker keeps the historical writer.
    inst = marker.get("instrument")
    summ = marker.get("summary")
    summ = str(summ).strip() if summ else ""
    if inst:
        src = str(inst).strip()
        if summ:
            src = "%s (%s)" % (src, summ)
    elif summ:
        src = "unattributed marker (%s)" % summ
    else:
        src = "honest_status"
    if m_exit == 0:
        outcome, detail = "held", f"{src} green on {str(m_head)[:7]}"
    elif m_exit == 1:
        outcome, detail = "broke", (
            f"{src} FAILED (exit 1) on {str(m_head)[:7]} — a head "
            "previously claimed verified")
    else:
        return []  # exit 2 / unknown — could not verify; leave open

    new: list[dict] = []
    for rec in state["open"]:
        if rec.get("head_full") != m_head:
            continue
        c_ts = rec.get("ts")
        if not isinstance(c_ts, (int, float)) or isinstance(c_ts, bool):
            continue  # undated claim — cannot show the marker post-dates it
        if m_ts <= c_ts:
            continue  # the marker that admitted the claim is not a re-check
        new.append({"kind": "verdict", "claim_id": rec.get("id"),
                    "ts": now_ts, "outcome": outcome, "detail": detail})
    return new


def rederive_and_persist(path: str | None, head_full_now: str | None,
                         marker: dict | None, now_ts: float,
                         max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Load, re-derive open claims against the marker, persist any definitive
    verdicts, and return the folded state AFTER the re-derivation. The
    warm-start emitter calls this; an append failure is logged, not raised."""
    path = path or ledger_path()
    events = load_events(path)
    new = rederive_open(events, head_full_now, marker, now_ts)
    if new:
        err = append_jsonl(path, new, max_bytes)
        if err:
            log_warning(f"calibration_ledger: could not persist "
                        f"{len(new)} re-derived verdict(s): {err}")
        events = events + new
    return fold(events)


def format_brief_block(state: dict, max_show: int = 3) -> str:
    """Render the folded state as a warm-brief section. ``""`` when nothing is
    tracked (don't inject noise). Surfaces BROKE claims loudly — those are the
    "you said 100%% and the math was wrong" cases the operator must see first.

    Honest about the denominator: a percentage is shown only when claims have
    actually been re-checked; with none re-checked it says exactly that, never a
    fabricated 100%%."""
    n = state.get("n_total", 0)
    if not n:
        return ""
    held, broke, open_ = state["n_held"], state["n_broke"], state["n_open"]
    n_def = held + broke
    lines = ["## calibration ledger — my own track record"]
    if n_def:
        pct = round(100 * state["ratio"])
        icon = "🟢" if broke == 0 else "⚠️"
        lines.append(
            f"{icon} {n} VERIFIED claim(s) logged · re-checked: {held} held / "
            f"{broke} broke ({pct}% held) · {open_} still unverified")
    else:
        lines.append(
            f"🔵 {n} VERIFIED claim(s) logged · none re-checked yet · "
            f"{open_} still unverified")
    # The ratio's own caveat, printed with it rather than filed away. A held
    # verdict whose evidence could not distinguish green from red still counts
    # as held — re-deriving it is a separate act — but quoting the percentage
    # without saying how much of it rests on qualified evidence would be the
    # averaged-away blind spot calibrated_claims rule 5 forbids.
    n_annot = state.get("n_annotated", 0)
    if n_annot:
        lines.append(
            f"- ⚠️ {n_annot} of those re-checked carry an evidence annotation — "
            "the verdict stands, but its evidence was qualified. See `kind: "
            "annotation` rows in the ledger for what and why.")
    for rec in state.get("broke", [])[:max_show]:
        ct = (rec.get("claim_text") or "")[:80]
        head = str(rec.get("head_full") or "")[:7]
        lines.append(
            f"- ⚠️ BROKE on {head}: \"{ct}\" — was claimed VERIFIED; "
            "re-derivation says not-green. Treat my completion claims with "
            "this in mind this session.")
    return "\n".join(lines) + "\n"
