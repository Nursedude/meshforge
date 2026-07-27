#!/usr/bin/env python3
"""review_provenance_check.py — the upshift-witness pre-push guard.

Compiles `model_advisor.md`'s upshift path — *"review/design-shaped work on a
non-frontier session → QUEUE a review_provenance row instead of faking the
pass"* — into deterministic checks, so it survives a model swap. The harness is
the portability layer, not the model's disposition (the cross-model
agentic-health arc); a behavior that lives only in what a session "should" do is
a house of cards the next model swap collapses.

Design: `.claude/plans/upshift_witness_design.md`. Three legs:

  Leg 1 (HARD gate): a commit in the push range whose message CLAIMS a review
      happened must leave a witness row in `.claude/audits/review_provenance.md`
      (same push). Model-independent — even a Fable session that claims a review
      must stamp the row; now it can't forget.

  Leg 2 (HARD gate): a NEW completed-table row whose Mechanism column claims a
      frontier-tier pass, stamped while the session's newest calibration-ledger
      model_id is non-frontier → block ("queue it as a worklist row instead").
      You may always QUEUE; you may not CLAIM the rationed frontier pass on a
      smaller model. Unknown/absent ledger model → warn, never block.

  Leg 3 (advisory nudge): src lines changed since the last CLOSING provenance
      boundary, on a non-frontier session, exceeding a threshold → one advisory
      block. Never affects the exit code. The boundary is the newest commit
      whose diff ADDS a completed-table row (first cell a date) — a commit that
      merely QUEUES a worklist row does not advance it. Found 2026-07-26 (F3,
      `.claude/plans/audit_gap_2026_07_26.md`): keying the boundary on "last
      commit touching the file" meant recording 12k lines of unreviewed debt
      reset the nudge to zero — writing down that work is unreviewed persuaded
      the guard it was reviewed. Accepted limits (advisory, both over- not
      under-fire relative to a coverage claim): a SCOPED closing row still
      advances the boundary, and an edit to an old completed row does too.

Stdlib only. Fail-CLOSED on the two hard legs where an obligation exists (an
absent/empty provenance file must NOT read as "no obligations" —
honest_failure_modes #1), but fail-OPEN (warn, exit 0) on anything genuinely
unobservable: a missing ledger model_id (#2, unobservable ≠ violation) or an
unresolvable git base. Every leg that fires appends one witness line to
``~/upshift_witness.log`` (#9 — the "did the guard ever run" question).

Honest limit (documented, not hidden): the session model is inferred from the
ledger's newest ``claim`` event, the only deterministic signal available in the
hook environment (no transcript, no live model id). It lags by one claim — a
session that stamps a frontier row BEFORE logging any claim is judged by the
prior session's model. Direction matters (live-caught 2026-07-16, the gate's
first real firing): a stale-FRONTIER read is permissive (never false-blocks),
but a stale-NON-frontier read FALSE-BLOCKS the first genuine frontier session
after an interregnum — exactly the session most likely to stamp a frontier
row. The sanctioned unblock is NOT --no-verify: record the session's real
VERIFIED claim to the calibration ledger first (``record_claim(...,
model_id=<this session's frontier id>, source="manual")`` — calibrated_claims
rule 6, quoted evidence required), then re-push; the gate re-reads honestly.
The leg-2 block message names this path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Repo root (this file lives in <repo>/scripts/).
REPO = Path(__file__).resolve().parent.parent

PROVENANCE_REL = ".claude/audits/review_provenance.md"
WITNESS_LOG_DEFAULT = "~/upshift_witness.log"

# --- The ONE place these vocabularies live (honest_failure_modes #5 — shared, ---
# --- not hardcoded twice; the tests import these, no second copy). ------------

#: Frontier-class model-id prefixes — the rationed tier the upshift path exists
#: to protect. Fable is today's frontier pass (cross-model arc); add future ids
#: here, never inline a second copy.
FRONTIER_MODEL_PREFIXES = ("claude-fable-",)

#: A commit message that ASSERTS a review happened → it must leave a provenance
#: witness row. Deliberately the high-signal phrasings this repo's convention
#: uses; bare "reviewed" is excluded (too common in incidental use — a false
#: block costs a real push, so bias to precision, honest about the limit).
#:
#: The numeric patterns carry a `(?<![-\d/.])` guard so an ISO DATE cannot pose
#: as a count: in "2026-07-25 findings" the day number is preceded by a hyphen,
#: and every shorter start position is preceded by a digit. Without it an
#: ordinary dated summary line blocked a push (measured 3× on 2026-07-25).
#: `\s+` is deliberately kept (it spans newlines) so a genuine claim wrapped
#: across a line break still counts — the date, not the newline, was the bug.
REVIEW_CLAIM_PATTERNS = (
    r"self[-\s]?review",
    r"adversarial",
    r"\bcode[-\s]?review\b",
    r"/code-review",
    r"\breview pass\b",
    r"(?<![-\d/.])\b\d{1,3}\s+finding",       # "3 findings", "11 findings"
    r"(?<![-\d/.])\b\d{1,3}[-\s]?finder\b",   # "6-finder", "10 finder"
    r"(?<![-\d/.])\b\d{1,3}[-\s]?angle\b",    # "6-angle adversarial"
    # A finding attributed to a review — "the Pri-1 finding from the
    # 2026-07-23 gateway review". The numeric guard above deliberately stops
    # reading a hyphenated LABEL ("pri-1", "thread-3") as a count, and this
    # keeps the genuinely review-attributed ones (6afe194c) claiming without
    # resurrecting the label-only false positives (7867f9f5, which references
    # a scope-doc thread and no review at all). Same-clause only.
    r"\bfinding[s]?\b[^.\n]{0,40}\breview\b",
)

#: Words that flip a claim into a DENIAL. Without these the gate had no
#: negation awareness: "no review pass was run here" matched as strongly as an
#: assertion, so the honest disclaimer was itself unpublishable.
NEGATION_TERMS = (
    r"\b(?:no|not|never|without|nor|non|neither|"
    r"isn't|wasn't|weren't|didn't|doesn't|won't|can't|cannot)\b"
)

#: How far back to look for a negator. Short on purpose — a negator in an
#: earlier clause must not immunise a later real claim.
NEGATION_WINDOW_CHARS = 30

#: A negator before one of these does NOT reach across it. Keeps
#: "not a quick patch; a full code-review found bugs" a real claim.
NEGATION_STOP_CHARS = (".", ";", ":", "\n", "—", "|")

#: Phrases in a COMPLETED-row Mechanism column that ASSERT the rationed frontier
#: pass ran. A non-frontier session may not stamp these — it queues instead.
FRONTIER_TIER_MARKERS = (
    r"/code-review\s+ultra",
    r"\bultrareview\b",
    r"\bfrontier\b",
    r"\bfable\b",
)

#: Leg-3 nudge threshold — src lines (added+deleted) since the last provenance
#: boundary. A NUDGE, not a judgment: it cannot know whether the change needed
#: review, only that a lot has accumulated unreviewed. Tuned high to stay quiet
#: on routine work.
UNREVIEWED_SRC_LINES_NUDGE = 800


# --------------------------------------------------------------------------- #
# Pure decision core (unit-tested against synthetic inputs).                   #
# --------------------------------------------------------------------------- #

def is_frontier_model(model_id):
    """True/False for a known model id, None when unknown/absent (the caller
    must NOT collapse None into either — unobservable ≠ violation)."""
    if not isinstance(model_id, str) or not model_id:
        return None
    return any(model_id.startswith(p) for p in FRONTIER_MODEL_PREFIXES)


def newest_ledger_model_id(events):
    """model_id of the newest ``claim`` event (max ts). None if no claim carries
    one — recorded absence, never a fabricated default."""
    best_ts = None
    best_model = None
    for ev in events:
        if not isinstance(ev, dict) or ev.get("kind") != "claim":
            continue
        ts = ev.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if best_ts is None or ts >= best_ts:
            best_ts = ts
            best_model = ev.get("model_id")
    return best_model


def _is_negated(low, start):
    """True if the claim beginning at ``start`` is DENIED by a nearby negator.

    Looks back a short window, then discards anything at or before the last
    clause boundary in it — so "not a quick patch; a full code-review found
    bugs" stays a real claim while "no review pass was run here" does not.
    """
    window = low[max(0, start - NEGATION_WINDOW_CHARS):start]
    for sep in NEGATION_STOP_CHARS:
        idx = window.rfind(sep)
        if idx != -1:
            window = window[idx + 1:]
    return re.search(NEGATION_TERMS, window) is not None


def commit_claims_review(message):
    """True if a commit message ASSERTS a review happened.

    A denial is not an assertion: every match is checked against a nearby
    negator, so a message that says a pass did NOT happen does not demand a
    provenance row (which would be a false witness). Precision matters here —
    a false block costs a real push.
    """
    if not message:
        return False
    low = message.lower()
    for pat in REVIEW_CLAIM_PATTERNS:
        for m in re.finditer(pat, low):
            if not _is_negated(low, m.start()):
                return True
    return False


def provenance_mentions_sha(prov_text, full_sha):
    """True if the provenance file names ``full_sha`` — via any hex token
    (short or full) that is a prefix of it (the file lists short SHAs and
    ranges; a short SHA is a prefix of the full one)."""
    if not full_sha or not prov_text:
        return False
    for tok in re.findall(r"\b[0-9a-f]{7,40}\b", prov_text):
        if full_sha.startswith(tok) or tok.startswith(full_sha):
            return True
    return False


def parse_completed_rows(text):
    """Completed-review table rows as (date, mechanism, raw_line).

    Discriminator: the first cell is a ``YYYY-MM-DD`` date. That cleanly
    separates completed rows from the 3-column queued worklist (first cell is a
    Pri number or ``—``), the header row, and ``|---|`` separators."""
    rows = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if not re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        rows.append((cells[0], cells[2], s))
    return rows


def row_claims_frontier_tier(mechanism):
    """True if a Mechanism cell asserts the rationed frontier pass ran."""
    if not mechanism:
        return False
    low = mechanism.lower()
    return any(re.search(p, low) for p in FRONTIER_TIER_MARKERS)


def new_completed_rows(head_text, base_text):
    """Completed rows present at HEAD but not in the base version (row identity
    by raw line — an edit that injects a frontier claim also counts as new)."""
    base_lines = {r[2] for r in parse_completed_rows(base_text)}
    return [r for r in parse_completed_rows(head_text) if r[2] not in base_lines]


def evaluate_leg2(head_text, base_text, session_is_frontier):
    """Pure leg-2 decision.

    Returns (violating_rows, warn_code). ``session_is_frontier`` is True /
    False / None (unknown). A frontier-tier NEW row:
      * unknown session model  → warn ("unknown-model"), never block
      * frontier session       → allowed (may stamp the pass it ran)
      * non-frontier session   → blocked (queue it instead)
    """
    new_rows = new_completed_rows(head_text, base_text)
    frontier_rows = [r for r in new_rows if row_claims_frontier_tier(r[1])]
    if not frontier_rows:
        return [], None
    if session_is_frontier is None:
        return [], "unknown-model"
    if session_is_frontier:
        return [], None
    return frontier_rows, None


# --------------------------------------------------------------------------- #
# I/O helpers — every git failure returns None (never raises).                 #
# --------------------------------------------------------------------------- #

def _git(repo, args, timeout=10):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _load_ledger_events(path):
    """Parse a calibration ledger JSONL into event dicts (torn-tail safe, never
    raises — a missing file is [])."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def commits_in_range(repo, rev_range):
    """[(full_sha, raw_message)] for the range, or None if git can't resolve
    it (e.g. base ref not fetched — unobservable, the caller warns+skips)."""
    out = _git(repo, ["log", "--format=%H%x1f%B%x1e", rev_range])
    if out is None:
        return None
    commits = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        sha, _, body = chunk.partition("\x1f")
        commits.append((sha.strip(), body))
    return commits


def _commit_touches_provenance(repo, full_sha):
    """True if the commit modifies the provenance file — a commit that records
    provenance is participating in the convention, not forgetting it, so leg 1
    exempts it (avoids false-blocking the very commit that adds the row)."""
    out = _git(repo, ["diff-tree", "--no-commit-id", "--name-only", "-r", full_sha])
    if not out:
        return False
    return PROVENANCE_REL in out.split()


def _commit_in_any_range(repo, full_sha, prov_text):
    """Best-effort: True if any ``A..B`` range named in the provenance file
    contains this commit (so a commit inside a reviewed range needn't be listed
    individually). Any git failure → treated as not-covered."""
    for a, b in re.findall(r"\b([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})\b", prov_text or ""):
        out = _git(repo, ["rev-list", f"{a}..{b}"])
        if out and full_sha in out.split():
            return True
    return False


def _read_provenance_at(repo, ref):
    """The provenance file as of ``ref`` (HEAD-of-push, or the base). None if
    the file does not exist at that ref."""
    return _git(repo, ["show", f"{ref}:{PROVENANCE_REL}"])


#: An added completed-table row in a provenance diff — the date-cell
#: discriminator `parse_completed_rows` uses, seen through `git log -p` (the
#: leading '+'). This is what makes a commit a CLOSING commit for leg 3.
_CLOSING_ROW_RE = re.compile(r"^\+\s*\|\s*\d{4}-\d{2}-\d{2}\s*\|", re.M)

#: How many provenance-touching commits leg 3 walks looking for a closing row.
#: Closing rows land every few days; 60 covers months of history.
_CLOSING_WALK_LIMIT = 60


def closing_boundary_from_log(log_text):
    """Newest sha in a ``git log --format=%x1e%H -p`` walk whose provenance
    diff ADDS a completed-table row; falls back to the OLDEST walked sha when
    none does (the advisory then over-fires rather than going quiet — the F3
    failure was the boundary advancing on a commit that merely queued debt).
    None when the log is empty/unreadable."""
    if not log_text:
        return None
    oldest = None
    for chunk in log_text.split("\x1e"):
        chunk = chunk.strip("\n ")
        if not chunk:
            continue
        sha, _, diff = chunk.partition("\n")
        sha = sha.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        oldest = sha
        if _CLOSING_ROW_RE.search(diff):
            return sha
    return oldest


def _last_closing_provenance_commit(repo):
    out = _git(repo, ["log", "-n", str(_CLOSING_WALK_LIMIT),
                      "--format=%x1e%H", "-p", "--", PROVENANCE_REL],
               timeout=30)
    if out is None:
        return None
    return closing_boundary_from_log(out)


def _src_lines_changed(repo, since_sha):
    out = _git(repo, ["diff", "--numstat", f"{since_sha}..HEAD", "--", "src/"])
    if out is None:
        return None
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            for n in parts[:2]:
                if n.isdigit():
                    total += int(n)
    return total


def append_witness(message, path=None):
    """Best-effort witness append (#9). A witness failure never blocks a push."""
    path = path or os.path.expanduser(WITNESS_LOG_DEFAULT)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Leg orchestration.                                                           #
# --------------------------------------------------------------------------- #

def check_leg1(repo, commits, prov_text):
    """[(sha, subject)] for review-claiming commits with no witness row."""
    violations = []
    for full_sha, message in commits:
        if not commit_claims_review(message):
            continue
        if _commit_touches_provenance(repo, full_sha):
            continue
        if provenance_mentions_sha(prov_text, full_sha):
            continue
        if _commit_in_any_range(repo, full_sha, prov_text):
            continue
        subject = message.splitlines()[0].strip() if message else ""
        violations.append((full_sha, subject))
    return violations


def _fmt_leg1_block(violations):
    today = time.strftime("%Y-%m-%d")
    lines = [
        "UPSHIFT-WITNESS (leg 1): these commits CLAIM a review but leave no",
        f"witness row in {PROVENANCE_REL} (same push):",
    ]
    for sha, subject in violations:
        lines.append(f"  {sha[:9]} {subject[:66]}")
    lines += [
        "",
        "Add one row (it rides THIS push — \"provenance rides the fix commit\"),",
        "e.g. append to the completed-review table:",
        "",
        f"| {today} | <range + paths reviewed> | <mechanism, e.g. /code-review "
        "high 8-finder> | <fix SHAs> | <residuals / refuted notes> |",
        "",
        "Then re-push.  (Override, sparingly: git push --no-verify)",
    ]
    return "\n".join(lines)


def _fmt_leg2_block(rows, model_id):
    lines = [
        "UPSHIFT-WITNESS (leg 2): a NEW completed-review row claims a",
        f"frontier-tier pass, but this session's newest ledger model is "
        f"'{model_id}' (non-frontier).",
        "You may QUEUE the range — you may not CLAIM the rationed frontier pass",
        "on a smaller model. Move it to the \"Frontier worklist — queued upshift",
        "ranges\" table instead:",
        "",
        "| <Pri> | <scope range + paths> | <why it's frontier-shaped> |",
        "",
        "Offending row(s):",
    ]
    for _date, mech, raw in rows:
        lines.append(f"  {raw[:100]}")
    lines += [
        "",
        "If this session genuinely IS a frontier pass (the ledger model lags by",
        "one claim — common on the first frontier session after an interregnum):",
        "record the pass's real VERIFIED claim to the calibration ledger first",
        "(record_claim(..., model_id=<this session's frontier id>,",
        "source=\"manual\") — calibrated_claims rule 6, quoted evidence), then",
        "re-push; this gate re-reads the ledger honestly.",
        "",
        "(Override, sparingly: git push --no-verify)",
    ]
    return "\n".join(lines)


def run(repo, rev_range, base_ref, ledger_path, witness_path):
    """Execute all three legs. Returns (exit_code, out_lines, warn_lines)."""
    out = []
    warn = []
    prov_head = _read_provenance_at(repo, "HEAD")
    if prov_head is None:
        # Fall back to the working tree (a clean push has HEAD == worktree).
        try:
            prov_head = (Path(repo) / PROVENANCE_REL).read_text(encoding="utf-8")
        except OSError:
            prov_head = ""  # absent → leg 1 will loudly flag any claim (#1)

    # --- Leg 1 -------------------------------------------------------------
    leg1_violations = []
    commits = commits_in_range(repo, rev_range) if rev_range else []
    if commits is None:
        warn.append(f"leg1: could not resolve push range '{rev_range}' "
                    "(base not fetched?) — skipping review-claim witness check")
    else:
        leg1_violations = check_leg1(repo, commits, prov_head)

    # --- Leg 2 -------------------------------------------------------------
    leg2_violations = []
    events = _load_ledger_events(ledger_path)
    model_id = newest_ledger_model_id(events)
    session_is_frontier = is_frontier_model(model_id)
    base_prov = _read_provenance_at(repo, base_ref)
    if base_prov is None:
        warn.append(f"leg2: could not read {PROVENANCE_REL} at base '{base_ref}' "
                    "— skipping frontier-claim gate (can't tell which rows are new)")
    else:
        leg2_violations, leg2_warn = evaluate_leg2(prov_head, base_prov,
                                                   session_is_frontier)
        if leg2_warn == "unknown-model":
            warn.append("leg2: a NEW completed row claims a frontier-tier pass "
                        "but the ledger carries no model_id — warning, not "
                        "blocking (unobservable ≠ violation)")

    # --- Leg 3 (advisory) --------------------------------------------------
    leg3_msg = None
    if session_is_frontier is False:  # only nudge a KNOWN non-frontier session
        boundary = _last_closing_provenance_commit(repo)
        if boundary:
            changed = _src_lines_changed(repo, boundary)
            if changed is not None and changed > UNREVIEWED_SRC_LINES_NUDGE:
                leg3_msg = (
                    f"UPSHIFT-WITNESS (leg 3, advisory): {changed} src lines "
                    f"changed since the last CLOSING review boundary "
                    f"({boundary[:9]}) > {UNREVIEWED_SRC_LINES_NUDGE}. "
                    "Consider queueing an upshift row (review_provenance "
                    "worklist) for the next frontier pass. This never blocks.")

    # --- Verdict + witnesses ----------------------------------------------
    blocked = bool(leg1_violations or leg2_violations)
    if leg1_violations:
        out.append(_fmt_leg1_block(leg1_violations))
        append_witness(
            "leg1 BLOCK: " + ",".join(s[:9] for s, _ in leg1_violations),
            witness_path)
    if leg2_violations:
        out.append(_fmt_leg2_block(leg2_violations, model_id))
        append_witness(
            f"leg2 BLOCK: {len(leg2_violations)} frontier-claim row(s) under "
            f"model={model_id}", witness_path)
    if leg3_msg:
        out.append(leg3_msg)
        append_witness(f"leg3 advisory under model={model_id}", witness_path)

    return (1 if blocked else 0), out, warn


# --------------------------------------------------------------------------- #
# CLI wrapper.                                                                 #
# --------------------------------------------------------------------------- #

def _default_ledger_path():
    env = os.environ.get("CALIBRATION_LEDGER_PATH")
    if env:
        return env
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, "calibration_ledger.jsonl")


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--repo", default=None,
                   help="Repo root (default: this script's repo).")
    p.add_argument("--range", dest="rev_range", default=None,
                   help="Push range REV..REV (default: <base-ref>..HEAD).")
    p.add_argument("--base-ref", default="origin/main",
                   help="Base ref for the push range + new-row diff.")
    p.add_argument("--ledger", default=None,
                   help="Calibration ledger path (default: $CALIBRATION_LEDGER_"
                        "PATH or ~/calibration_ledger.jsonl).")
    p.add_argument("--witness", default=None,
                   help="Witness log path (default: ~/upshift_witness.log).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    repo = args.repo or str(REPO)
    ledger_path = args.ledger or _default_ledger_path()
    rev_range = args.rev_range or f"{args.base_ref}..HEAD"
    try:
        code, out, warn = run(repo, rev_range, args.base_ref,
                              ledger_path, args.witness)
    except Exception as e:  # noqa: BLE001 — a gate bug must not wedge a push
        print(f"review_provenance_check: WARN — internal error ({e!r}); "
              "passing open", file=sys.stderr)
        return 0
    for line in warn:
        print(f"review_provenance_check: WARN — {line}", file=sys.stderr)
    for block in out:
        print(block)
    return code


if __name__ == "__main__":
    sys.exit(main())
