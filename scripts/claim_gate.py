#!/usr/bin/env python3
"""claim_gate.py — the calibrated-claims reflective Stop hook.

Born 2026-06-15 from the operator's concern: *"honesty is not enough when
honesty is a house of cards… when you say 100%% and we do it N more times the
math is wrong."* `.claude/rules/calibrated_claims.md` is the write-time
discipline; THIS is the harness-level enforcement that survives a model swap,
because it lives here and not in the model's disposition.

What it does (and deliberately does NOT do):
  * On a Stop, it reads my last assistant message. If that message makes an
    unqualified completion claim ("all green / 100%% / all tests pass / fully
    verified / deployed successfully …") with NO evidence tag and NO fresh
    `honest_status.sh` verdict covering the current HEAD, it BLOCKS ONCE and
    injects the re-derived truth + a pointer to the rule. One reflective beat.
  * It is NOT a cage. `stop_hook_active` guarantees at most one block per stop
    sequence — after the reflective beat my judgment stands. And a message that
    is already calibrated (tags VERIFIED/BELIEVED/UNKNOWN, quotes a real exit
    code, or hedges honestly) passes untouched. Enforcement = truth-injection,
    not speech-prohibition.

FAIL-OPEN, ALWAYS. A bug in this gate must never wedge a session: every
unexpected error is swallowed into a silent pass (exit 0) with a stderr witness
(honest_failure_modes #9). A gate that wedges sessions gets ripped out and
protects nothing. The calibration ledger is the backstop for what slips through.

Stdlib only. Pure core (`evaluate`) is exercised by tests/test_claim_gate.py
against synthetic inputs (RED + GREEN); the thin __main__ wrapper does I/O.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

# Repo root (this file lives in <repo>/scripts/). Used to resolve HEAD and the
# default verdict-marker path, matching honest_status.sh.
REPO = Path(__file__).resolve().parent.parent

# The marker-acceptance predicate, the marker path and the HEAD resolver are
# the ledger module's (finding 18, 2026-09-09): this file carried its own
# copies — a HEAD resolver WITHOUT the `-c safe.directory` guard warmstart
# has (so under dubious ownership the gate blocked every strong claim beside
# a fresh green marker), a third hand-typed marker path with a different
# HOME fallback from honest_status's, and a verbatim copy of the refusal rule
# rederive_open applies. Import failure is NOT fatal (fail-open contract):
# `_cl` stays None, no marker is honored (the refusing direction — the gate
# then only ever blocks MORE, never less), and the one stderr line says so.
_SRC = str(REPO / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
try:
    from mini_dudeai import calibration_ledger as _cl
except Exception as _cl_err:  # noqa: BLE001 — fail-open, with a witness
    _cl = None
    print(f"claim_gate: WARN — mini_dudeai.calibration_ledger unavailable "
          f"({_cl_err!r}); no verdict marker will be honored, nothing recorded",
          file=sys.stderr)

# A verified marker is only honored if it is this fresh AND covers the current
# HEAD AND ran the full suite. Same HEAD = same committed code; the age cap
# bounds the working-tree-drifted-since-verify window (the rule's "re-derive at
# the end" covers the rest — the gate is a nudge, not a proof system).
MARKER_MAX_AGE_S = 2 * 60 * 60  # 2h

# Unqualified completion claims that have burned the operator. Lowercased
# substring match. Deliberately CONSERVATIVE — bare "done"/"fixed" are excluded
# (too common in honest use); we fire only on certainty-of-completion phrasing.
# A false fire costs one beat and is cleared by tagging, so we bias to precision.
STRONG_CLAIMS = (
    "all green", "everything is green", "all checks pass", "all checks passing",
    "fully verified", "fully tested", "verified green",
    "100%", "all tests pass", "all tests passing", "tests all pass",
    "all passing", "everything works", "everything is working",
    "everything passes", "it works now", "works perfectly",
    "successfully deployed", "deployed successfully", "deploy succeeded",
    "confirmed working", "guaranteed to work", "definitely works",
    "definitely fixed",
)

# If ANY of these appear in the message, it is already calibrated → pass. This
# is the autonomy-preserving escape: honest hedging and quoted evidence always
# pass. Generous by design — the gate fires only on confident completion
# language with ZERO evidence and ZERO hedging.
CALIBRATION_MARKERS = (
    "verified", "believed", "unverified", "untested", "unknown",
    "not verified", "haven't verified", "have not verified", "not yet verified",
    "not tested", "needs verification", "need to verify", "should work",
    "i believe", "i think this", "can't verify", "cannot verify",
    "couldn't verify", "unable to verify", "not been verified",
)

# Quoted real exit codes / results = evidence → calibrated. Regexes so we match
# the shapes honest_status.sh and the test discipline actually emit.
EVIDENCE_PATTERNS = (
    r"exit\s*(?:code\s*)?\d",      # "exit 0", "exit code 1"
    r"\bEXIT=\d",                   # "EXIT=0"
    r"\brc=\d",                     # "rc=0"
    r"return\s*code",               # "return code"
    r"\(exit\s*\d",                 # "(exit 0)"
    r"_EXIT=\d",                    # "LINT_EXIT=0"
    r"exit\s*status\s*\d",
)


def extract_last_assistant_text(transcript_lines) -> str:
    """Concatenated text of the LAST assistant message in a JSONL transcript.

    Defensive: skips unparseable lines, tolerates both the
    ``{"type":"assistant","message":{"content":[…]}}`` shape and a bare
    ``{"role":"assistant","content":[…]}``. Returns "" if none found — the
    caller treats an empty extraction as "nothing to judge" (pass)."""
    last_text = ""
    for line in transcript_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = msg.get("role") or obj.get("type")
        if role != "assistant":
            continue
        content = msg.get("content")
        parts = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(block, str):
                    parts.append(block)
        if parts:
            last_text = "\n".join(parts)
    return last_text


def extract_last_assistant_model(transcript_lines) -> str | None:
    """The ``model`` of the LAST assistant message, if the transcript carries
    it (Claude Code lines do: ``{"message":{"role":"assistant","model":…}}``).
    None when absent — recorded as-is so a model swap stays visible, never
    fabricated."""
    model = None
    for line in transcript_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        if (msg.get("role") or obj.get("type")) != "assistant":
            continue
        m = msg.get("model")
        if isinstance(m, str) and m:
            model = m
    return model


# A strong claim is a claim only when it is not negated and not the tail of a
# longer word: "not fully verified yet", "haven't fully verified", "unverified
# green" are hedges, not overclaims. Leading word boundary; no trailing
# boundary, so "all tests passed" still counts.
#
# NEGATION IS A WINDOW, NOT AN ADJACENCY (finding 10, 2026-09-09). The first
# cut used look-behinds, which can only see the ONE token before the phrase —
# so "I have not yet fully verified this." and "hasn't been fully tested yet"
# BLOCKED (the honest hedge read as an overclaim) while "not fully verified
# yet" passed. A negation word within the preceding NEG_WINDOW tokens of the
# SAME sentence negates the phrase. The window is deliberately small: "did
# not run lint but all tests pass" keeps its claim (the `not` is four tokens
# back, across a `but`), because the gate biases to precision — a false fire
# costs one reflective beat; a false pass costs the operator hours.
NEG_WINDOW = 3
_NEG_WORDS = frozenset({"not", "never", "no", "nothing", "cannot"})
_CLAIM_RES = tuple(re.compile(r"(?<![\w])" + re.escape(c)) for c in STRONG_CLAIMS)
_SENTENCE_END = re.compile(r"[.!?;:\n]")
_TOKEN = re.compile(r"[a-z']+")


def _negated_before(low: str, start: int) -> bool:
    """Is there a negation word within NEG_WINDOW tokens before ``start``,
    without crossing a sentence boundary?"""
    before = low[:start]
    cut = [m.end() for m in _SENTENCE_END.finditer(before)]
    if cut:
        before = before[cut[-1]:]
    toks = _TOKEN.findall(before)[-NEG_WINDOW:]
    return any(t in _NEG_WORDS or t.endswith("n't") for t in toks)


def _claim_spans(low: str):
    return [m.span() for rx in _CLAIM_RES for m in rx.finditer(low)
            if not _negated_before(low, m.start())]


def has_strong_claim(text: str) -> bool:
    return bool(_claim_spans(text.lower()))


def is_calibrated(text: str) -> bool:
    """True if the message already shows evidence or honest hedging — the
    autonomy-preserving exempt path.

    A marker that lies INSIDE a strong claim does not count: two
    STRONG_CLAIMS ("fully verified", "verified green") contain the marker
    "verified", so the phrase that should trip the gate exempted itself for
    the gate's whole life (§3 drill 2026-09-07). The first fix stripped the
    claims from the text before the scan, which also deleted the "verified"
    inside honest hedges that overlap a claim ("not fully verified yet" went
    from pass to BLOCK — caught by the same-day review). Spans, not strips:
    a marker anywhere outside a claim span still calibrates."""
    low = text.lower()
    spans = _claim_spans(low)

    def _inside(s, e):
        return any(a <= s and e <= b for a, b in spans)

    for mk in CALIBRATION_MARKERS:
        for m in re.finditer(re.escape(mk), low):
            if not _inside(*m.span()):
                return True
    return any(re.search(p, text, re.IGNORECASE) for p in EVIDENCE_PATTERNS)


def marker_refusal_reason(marker, head_full, now_ts,
                          max_age_s=MARKER_MAX_AGE_S) -> str | None:
    """Why the marker does NOT back a claim about ``head_full`` — or None when
    it does. The scope/tree/quick rules come from the ledger's ONE predicate
    (``calibration_ledger.marker_refusal``); this adds the gate's own head,
    exit and freshness rules. Every refusal NAMES its cause (finding 15): the
    gate used to print "no fresh full honest_status verdict covers HEAD"
    beside "Latest verdict: N/N PASS (HEAD <same>, exit 0)" — a self-
    contradiction whose advice was to re-run the script just run."""
    if not isinstance(marker, dict):
        return "no marker"
    if not head_full:
        return "current HEAD unknown"
    if _cl is None:
        return "marker predicate unavailable (mini_dudeai import failed)"
    if marker.get("head_full") != head_full:
        return (f"the latest verdict is for HEAD "
                f"{str(marker.get('head_full', ''))[:7] or '?'}, not this one")
    if marker.get("exit_code") != 0:
        return f"the latest verdict on this HEAD was exit {marker.get('exit_code')}"
    why = _cl.marker_refusal(marker)
    if why is not None:
        return why
    ts = marker.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return "the marker carries no usable timestamp"
    age = now_ts - ts
    if age < 0:
        return "the marker is stamped in the future — a clock stepped"
    if age > max_age_s:
        return f"the marker is {int(age // 60)} min old (cap {max_age_s // 60} min)"
    return None


def marker_satisfies(marker, head_full, now_ts, max_age_s=MARKER_MAX_AGE_S) -> bool:
    """A fresh, full, green honest_status verdict covering the current HEAD
    backs a completion claim. Anything less does not (the safe direction)."""
    return marker_refusal_reason(marker, head_full, now_ts, max_age_s) is None


def evaluate(text, head_full, marker, now_ts):
    """Pure decision core. Returns (block: bool, reason: str|None).

    block=True only when the message makes an unqualified strong completion
    claim AND it is neither already-calibrated NOR backed by a fresh verdict
    marker. Everything else passes."""
    if not text or not has_strong_claim(text):
        return False, None
    if is_calibrated(text):
        return False, None
    why = marker_refusal_reason(marker, head_full, now_ts)
    if why is None:
        return False, None

    head_disp = (head_full[:7] if head_full else "unknown")
    if isinstance(marker, dict) and marker.get("summary"):
        marker_line = (f"Latest honest_status verdict: "
                       f"{marker.get('summary')} "
                       f"(HEAD {str(marker.get('head_full',''))[:7]}, "
                       f"exit {marker.get('exit_code')}) — NOT honored: {why}.")
    else:
        marker_line = f"No honest_status verdict on record for any HEAD ({why})."

    reason = (
        "CALIBRATED-CLAIMS CHECK (.claude/rules/calibrated_claims.md): your "
        "closing message makes an unqualified completion claim, but this turn "
        "ran no external check you quoted, and no fresh full honest_status "
        f"verdict covers the current HEAD ({head_disp}).\n"
        f"{marker_line}\n\n"
        "This is one reflective beat, not a cage — reconcile your wording with "
        "the evidence, then your judgment stands:\n"
        "  • If it IS verified: run `bash scripts/honest_status.sh` (exit 0 = "
        "green) or quote the real captured exit code, and tag the claim VERIFIED.\n"
        "  • If it is NOT yet verified: say so — tag it BELIEVED (written, not "
        "run) or UNKNOWN (couldn't check), and name the check that would confirm "
        "it. Unobservable is never 'healthy'; 'worked once' is not 'reliable'.\n"
        "Then finish."
    )
    return True, reason


def should_record(text, head_full, marker, now_ts) -> bool:
    """A VERIFIED claim worth logging = a strong completion claim BACKED by a
    fresh full-green honest_status verdict on the current HEAD. Scope is
    intentionally tight to marker-backed claims so every logged claim is one a
    later re-derivation can actually re-check (no open-forever noise). A bare
    'all green' counts here ONLY because the gate of record backs it — the
    claim's *text* honesty is the gate's job; the ledger's job is the *math*."""
    return bool(text and has_strong_claim(text)
                and marker_satisfies(marker, head_full, now_ts))


def _record_verified_claim(text, head_full, marker, session_id, model_id):
    """Best-effort: log a marker-backed VERIFIED claim to the calibration
    ledger so a later re-derivation can check whether that green head held.
    This is the only AUTOMATIC feed into the ledger; deliberate hand-logged
    claims (source="manual") are sanctioned by calibrated_claims rule 6 when
    the evidence lives outside this gate's marker. Hand-written *verdict*
    events are never sanctioned — verdicts belong to re-derivation machinery
    alone. Fully guarded — recording must NEVER affect the gate's pass/block
    decision or its fail-open contract (a ledger/IO problem is swallowed; the
    claim is just not logged)."""
    try:
        if _cl is None:
            raise RuntimeError("calibration_ledger unavailable")
        summary = (marker or {}).get("summary", "") if isinstance(marker, dict) else ""
        evidence = f"honest_status {summary} (HEAD {str(head_full)[:7]})".strip()
        claim = " ".join(text.split())[:200]
        _cl.record_claim(claim, "completion", evidence, head_full,
                         model_id=model_id, session_id=session_id,
                         source="claim_gate")
    except Exception as e:  # noqa: BLE001 — best-effort, never breaks the gate
        # Every swallow gets a witness (honest_failure_modes #9; finding 17):
        # a claim that silently fails to record is a ledger with a hole the
        # held-rate cannot see.
        print(f"claim_gate: WARN — could not record the verified claim in the "
              f"calibration ledger ({e!r}); the gate's pass stands",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# I/O wrapper — everything here fails OPEN (exit 0) on any error.
# ---------------------------------------------------------------------------


def _current_head():
    """HEAD of the repo this gate lives in, via the ledger's ONE resolver
    (safe.directory-guarded). None when unresolvable — the gate then treats
    every marker as not covering HEAD, the refusing direction."""
    if _cl is None:
        return None
    return _cl.repo_head(str(REPO))


def _marker_path():
    """The ONE marker path contract (writer honest_status.sh, readers this
    gate + warmstart). None when the SSOT is unavailable → no marker read."""
    if _cl is None:
        return None
    return _cl.verdict_marker_path()


def _read_marker():
    p = _marker_path()
    if not p:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    # 1. Parse the Stop-hook stdin envelope. Empty/garbage → pass.
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0  # fail-open
    if not isinstance(data, dict):
        return 0

    # 2. Loop guard — the one-beat guarantee. If we are here because we ALREADY
    #    blocked once, pass unconditionally. My judgment stands after the beat.
    if data.get("stop_hook_active"):
        return 0

    # 3. Read the last assistant message. Unreadable transcript → pass.
    transcript_path = data.get("transcript_path")
    if not transcript_path:
        return 0
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, MemoryError) as e:
        # MemoryError is in the tuple on purpose (finding 17): the transcript
        # grows all session and this hook slurps it whole; a Pi that cannot
        # hold it must pass open with a witness, not traceback.
        print(f"claim_gate: WARN — transcript unreadable ({e!r}); passing open",
              file=sys.stderr)
        return 0

    try:
        text = extract_last_assistant_text(lines)
        head = _current_head()
        marker = _read_marker()
        now = time.time()
        block, reason = evaluate(text, head, marker, now)
    except Exception as e:  # noqa: BLE001 — fail-open is the whole contract
        print(f"claim_gate: WARN — internal error ({e!r}); passing open",
              file=sys.stderr)
        return 0

    if block:
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    # Verified-pass auto-record — the ONLY production feed into the calibration
    # ledger: a strong completion claim backed by a fresh full-green
    # honest_status verdict on HEAD is a VERIFIED claim of consequence, logged
    # so a later re-derivation can check it held. Runs only on a PASS, never a
    # block; never on the reflective continuation (stop_hook_active returned
    # early above), so it can't double-count. Fully guarded — never affects the
    # pass.
    try:
        if should_record(text, head, marker, now):
            _record_verified_claim(text, head, marker,
                                   data.get("session_id"),
                                   extract_last_assistant_model(lines))
    except Exception as e:  # noqa: BLE001 — fail-open, with a witness (#9)
        print(f"claim_gate: WARN — ledger recording step failed ({e!r}); "
              f"the gate's pass stands", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
