#!/usr/bin/env bash
# exit_code_mask_guard — PreToolUse(Bash) guard for the exit-code MASK idiom in
# the AGENT-PROCESS lane, where lint cannot reach: lint sees files in the repo,
# it never sees the command I am about to run in a tool call.
#
# The class (defect 3 of .claude/plans/exit_code_gate_and_harness_audit.md):
#   python3 -m pytest ... | tail -3
#   echo "rc=$?"            <-- that is TAIL's exit code, not pytest's
# It recurred on 2026-09-07 in a session that had just named it, and produced a
# reported-green suite that had actually failed two tests.
#
# ── WARN-ONLY, DELIBERATELY ───────────────────────────────────────────────
# A PreToolUse hook's blast radius is EVERY Bash call, including the ones the
# session needs to fix a mistake this hook makes
# ([[feedback_never_arm_a_guard_that_can_kill_the_session]]). So:
#   * it NEVER emits a deny decision and ALWAYS exits 0 — HOME unset, malformed
#     JSON, oversize payload, python missing/crashing/timing out included;
#   * the warning rides the channel the model actually SEES: PreToolUse stdout
#     JSON `hookSpecificOutput.additionalContext` ("shown to Claude without
#     blocking"; with permissionDecision omitted "the tool call continues
#     through the normal permission flow" — code.claude.com/docs/en/hooks).
#     The first version printed to stderr, which on exit 0 "goes to the debug
#     log only, never the transcript, and Claude never sees it" — the
#     corrective half was a no-op for two days (review pass 4, finding 1);
#   * every path past the pre-filter leaves a WITNESS row with a distinct state
#     (honest_failure_modes #9): MASKED-EXIT-CODE / near-miss, or DARK-<reason>
#     when the guard could not judge. A DARK row is a finding, not weather.
#
# ── ONE IMPLEMENTATION OF THE RULE ────────────────────────────────────────
# The verdict is lint's `mf022_exit_code_mask_findings` (scripts/lint.py) —
# derive, don't duplicate (honest_failure_modes #5). Two earlier bash
# re-implementations drifted from it within days. `tests/test_exit_code_mask_guard.sh`
# pins hook and lint to identical verdicts on one corpus and plants every
# degraded path.
#
# lint is imported ONLY from the repo this hook was pinned against: the
# candidate ($CLAUDE_PROJECT_DIR, or the repo copy's own ../..) must carry a
# BYTE-IDENTICAL copy of this hook, the pin, and a scripts/lint.py that bears
# the MF022 marker. Anything else (a foreign repo's lint.py, the CWD's, a
# ~/scripts/lint.py) is never executed; python runs with -I (no CWD / user
# site on sys.path). The residual: a repo that deliberately ships this exact
# hook + pin is trusted to ship the matching lint. Otherwise, or when import
# raises ANYTHING (BaseException — a decoy's sys.exit(3) used to take the guard
# dark with no witness), the verdict falls back to a coarse inline rule and
# the witness row says `fallback:<reason>` so it can never pass as lint's.
#
# ── LATENCY ───────────────────────────────────────────────────────────────
# Measured on VolcanoAI (Pi 5): bare python3 ~20 ms, +import lint ~70 ms. So no
# interpreter runs on the fast path: two greps on the RAW payload decide
# whether a pipe into head/tail AND some consumption shape ($?, &&, ||,
# if/while/then) are even present. The greps accept the JSON escapes `\n`
# `\t` between the pipe and head/tail (a pipe at end-of-line used to slip
# through, finding 10). They are deliberately over-inclusive; lint decides.
#
# ── WITNESS FORMAT ────────────────────────────────────────────────────────
#   <utc ts> \t <state> \t <offending line, secrets redacted> \t <source>
# state:  MASKED-EXIT-CODE | near-miss | skip-nonbash | DARK-badjson |
#         DARK-oversize | DARK-nocmd | DARK-pycrash | DARK-timeout
# source: lint | lint-legacy (lint without the shared predicate: an older
#         checkout still runs the quote-blind per-line rule) | fallback:<why>
# The line logged is the one carrying the `| head/tail`, not the first 200
# chars of a multi-line command (which omitted the offending line). Values
# after Authorization:/Bearer/token=/password=/psk= and known key shapes are
# redacted before the row is written; the file is 0600 and appended under
# flock (parallel Bash calls run this hook concurrently and the old
# read-then-mv rotation lost rows).
set -u
HOME_DIR="${HOME:-}"
if [ -z "$HOME_DIR" ]; then
  HOME_DIR="$(getent passwd "$(id -u 2>/dev/null)" 2>/dev/null | cut -d: -f6)" || HOME_DIR=""
fi
WITNESS="${EXIT_CODE_MASK_WITNESS:-}"
if [ -z "$WITNESS" ] && [ -n "$HOME_DIR" ]; then
  WITNESS="$HOME_DIR/.claude/hooks/exit_code_mask_witness.log"
fi
MAX_WITNESS_LINES=500
PY="${EXIT_CODE_MASK_PYTHON:-python3}"
PY_TIMEOUT="${EXIT_CODE_MASK_TIMEOUT:-6}"
MAX_PAYLOAD=120000   # one env var must stay under the kernel's 128 KiB arg limit

payload="$(cat 2>/dev/null)" || exit 0
[ -n "$payload" ] || exit 0

# ── witness writer: flock'd append + rotate, 0600, never fails the hook ───
witness() {  # $1=state $2=line $3=source
  [ -n "$WITNESS" ] || return 0
  [ -d "$(dirname "$WITNESS")" ] || return 0
  (
    umask 077
    if command -v flock >/dev/null 2>&1; then
      exec 9>>"$WITNESS.lock" && flock -w 2 9 || true
    fi
    printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" >> "$WITNESS"
    chmod 600 "$WITNESS" 2>/dev/null || true
    lines="$(wc -l < "$WITNESS" 2>/dev/null || echo 0)"
    if [ "${lines:-0}" -gt "$MAX_WITNESS_LINES" ] 2>/dev/null; then
      tmp="$(mktemp "$WITNESS.XXXXXX" 2>/dev/null)" || exit 0
      if tail -n "$MAX_WITNESS_LINES" "$WITNESS" > "$tmp" 2>/dev/null; then
        mv -f "$tmp" "$WITNESS" 2>/dev/null || rm -f "$tmp" 2>/dev/null
      else
        rm -f "$tmp" 2>/dev/null
      fi
    fi
  ) 2>/dev/null || true
}

# ── fast path: two string greps on the raw JSON, no interpreter ───────────
# EXIT_CODE_MASK_TRACE is a TEST seam: the pin sets it so a pre-filter exit
# leaves a `prefiltered` row and an EMPTY witness can only mean dark. Unset in
# production, so the fast path never touches the file.
prefiltered() { [ -n "${EXIT_CODE_MASK_TRACE:-}" ] && witness "prefiltered" "-" "-"; exit 0; }
printf '%s' "$payload" | grep -Eq '\|([[:space:]]|\\[nrt])*(tail|head)\b' || prefiltered
printf '%s' "$payload" \
  | grep -Eq '[$][{]?\?|&&|\|\||\b(if|elif|while|until|then|do)\b' || prefiltered

if [ "${#payload}" -ge "$MAX_PAYLOAD" ]; then
  witness "DARK-oversize" "payload ${#payload} chars" "-"
  exit 0
fi

# ── which lint may judge: only the repo this exact hook was pinned against ─
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)" || SELF="${BASH_SOURCE[0]}"
cand="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$cand" ]; then
  case "$SELF" in
    "${HOME_DIR:-/nonexistent}/.claude/hooks/"*) cand="" ;;   # installed copy: ../.. is $HOME, not a repo
    *) cand="$(cd "$(dirname "$SELF")/../.." 2>/dev/null && pwd)" || cand="" ;;
  esac
fi
LINT_DIR=""; reason="norepo"
if [ -n "$cand" ] && [ ! -d "$cand" ]; then
  cand=""
fi
if [ -n "$cand" ]; then
  if ! cmp -s "$cand/.claude/hooks/exit_code_mask_guard.sh" "$SELF" 2>/dev/null; then
    reason="hook-mismatch"
  elif [ ! -f "$cand/tests/test_exit_code_mask_guard.sh" ]; then
    reason="no-pin"
  elif [ ! -f "$cand/scripts/lint.py" ]; then
    reason="no-lint"
  else
    LINT_DIR="$cand/scripts"; reason="-"
  fi
fi

# ── slow path (rare): the payload rides an ENV VAR, not stdin — the heredoc
# below owns stdin (a `printf | python3 - <<PY` pipeline feeds the SCRIPT to
# the script; measured, the first draft went dark on every case).
verdict="$(EXIT_CODE_MASK_PAYLOAD="$payload" EXIT_CODE_MASK_LINT_DIR="$LINT_DIR" \
  EXIT_CODE_MASK_REASON="$reason" timeout "$PY_TIMEOUT" "$PY" -I - <<'PY' 2>/dev/null
import json, os, re, sys, tempfile

SENT = "ECMG\t"
FALLBACK_CMDS = ('pytest', 'python3', 'python', 'gh', 'git', 'systemctl',
                 'curl', 'ssh', 'lint.py', 'honest_status.sh')
FALLBACK_RX = re.compile(r'\b(' + '|'.join(re.escape(c) for c in FALLBACK_CMDS)
                         + r')\b.*\|\s*(tail|head)\b')
PIPE_RX = re.compile(r'\|\s*(tail|head)\b')
REDACT = [
    (re.compile(r'((?:authorization|x-api-key|api[-_]?key|cookie)\s*[:=]\s*)(?:bearer\s+|basic\s+|token\s+)?[^\s"\'&;]+',
                re.I), r'\1<redacted>'),
    (re.compile(r'\b(bearer\s+)[^\s"\'&;]+', re.I), r'\1<redacted>'),
    (re.compile(r'\b((?:token|psk|password|passwd|secret|api_?key|access_?key|client_?secret)\s*=\s*)[^\s"\'&;]+',
                re.I), r'\1<redacted>'),
    (re.compile(r'(--?(?:token|password|passwd|psk|secret|api-key)[\s=]+)[^\s"\'&;]+', re.I), r'\1<redacted>'),
    (re.compile(r'\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}'), r'\1_<redacted>'),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}'), 'github_pat_<redacted>'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), 'AKIA<redacted>'),
    (re.compile(r'\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}'), 'sk-<redacted>'),
    (re.compile(r'\bxox[abpors]-[A-Za-z0-9-]{10,}'), 'xox?-<redacted>'),
]


def redact(s):
    for rx, rep in REDACT:
        s = rx.sub(rep, s)
    return s


def out(state, line, src):
    line = re.sub(r'\s+', ' ', line).strip()[:200] or '-'
    sys.stdout.write(SENT + state + "\t" + line + "\t" + (src or '-') + "\n")
    sys.stdout.flush()


def offending_line(cmd):
    for ln in cmd.splitlines():
        if PIPE_RX.search(ln):
            return ln
    return cmd.splitlines()[0] if cmd.splitlines() else cmd


def fallback(cmd):
    """Only when lint may not judge. The coarse old rule minus its `[^|]*`
    bug: a noisy verdict beats a dark one."""
    return bool(FALLBACK_RX.search(cmd)) and ('$?' in cmd or '&&' in cmd or '||' in cmd)


payload = os.environ.get("EXIT_CODE_MASK_PAYLOAD", "")
try:
    d = json.loads(payload)
    if not isinstance(d, dict):
        raise ValueError("not an object")
except Exception:
    out("DARK-badjson", redact(payload[:120]), "-")
    sys.exit(0)
if d.get("tool_name") != "Bash":
    out("skip-nonbash", str(d.get("tool_name"))[:40], "-")
    sys.exit(0)
ti = d.get("tool_input")
cmd = ti.get("command") if isinstance(ti, dict) else None
if not isinstance(cmd, str) or not cmd.strip():
    out("DARK-nocmd", "-", "-")
    sys.exit(0)

line = redact(offending_line(cmd))
lint_dir = os.environ.get("EXIT_CODE_MASK_LINT_DIR", "")
reason = os.environ.get("EXIT_CODE_MASK_REASON", "norepo")
fired = None
src = None
if lint_dir:
    try:
        with open(os.path.join(lint_dir, "lint.py"), encoding="utf-8", errors="ignore") as fh:
            if "MF022_EXITCODE_MASK" not in fh.read(600000):
                raise RuntimeError("no-marker")
        sys.path.insert(0, lint_dir)
        import lint
        if hasattr(lint, "mf022_exit_code_mask_findings"):
            fired = bool(lint.mf022_exit_code_mask_findings(cmd))
            src = "lint"
        else:
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, "agent_cmd.sh")
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(cmd + "\n")
                issues = lint.check_pip_invocations_in_files([p], repo_root=td)
            fired = any(i.code == "MF022" and "not the command's" in i.message
                        for i in issues)
            src = "lint-legacy"
    except BaseException as e:          # SystemExit from a decoy included
        fired = None
        reason = "import-" + type(e).__name__
if fired is None:
    fired = fallback(cmd)
    src = "fallback:" + reason
out("MASKED-EXIT-CODE" if fired else "near-miss", line, src)
PY
)"; py_rc=$?

# Parse by SENTINEL, last one wins — stray import-time stdout cannot displace
# the verdict, and a decoy cannot print one after ours.
row="$(printf '%s\n' "$verdict" | awk -F'\t' '$1=="ECMG"{r=$0} END{print r}')"
state=""; line="-"; src="-"
if [ -n "$row" ]; then
  IFS=$'\t' read -r _ state line src <<<"$row"
fi
if [ -z "${state:-}" ]; then
  if [ "$py_rc" = 124 ] || [ "$py_rc" = 137 ]; then state="DARK-timeout"
  else state="DARK-pycrash"; fi
  line="python rc=$py_rc"; src="-"
fi

if [ "$state" = "MASKED-EXIT-CODE" ]; then
  # The ONLY stdout this hook ever produces. Static text: no command content
  # is interpolated, so the JSON cannot be malformed by the payload.
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"exit-code mask guard (WARN only, nothing blocked): this command pipes a verdict-bearing command into head/tail and then consumes the pipeline'"'"'s exit status ($?, &&, ||, if/while) — that is the PIPE'"'"'s exit code, not the command'"'"'s. Capture it first: `cmd >log 2>&1; rc=$?` then read the log."}}'
fi

witness "$state" "${line:--}" "${src:--}"
exit 0
