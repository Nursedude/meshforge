#!/usr/bin/env bash
# wait_for_ci.sh — block until CI completes for a SHA, then report honestly.
#
# WHY THIS EXISTS (2026-07-20): a hand-written poll loop
#
#     until [ "$(gh run list --commit <sha> --json status --jq '.[0].status')" = "completed" ]
#     do sleep 20; done
#
# ran for **7h 33m** on this box, burning ~1,350 GitHub API calls, for a commit
# whose CI had gone green 7 hours earlier. Root cause: `gh run list --commit
# <sha>` returns an EMPTY list on this gh version, so `.[0].status` is null,
# the comparison is `"" = "completed"`, and the predicate is UNSATISFIABLE BY
# CONSTRUCTION. The loop could never exit no matter what CI did.
#
# That is the house defect class in a shell one-liner: an ABSENT result
# (query matched nothing) silently mapped onto a VALID-LOOKING one ("not
# finished yet") instead of "cannot observe" — honest_failure_modes #1. The
# same filter failure bit a second session hours later on a different SHA, so
# it is compiled into a tool here rather than left to memory (the
# cross-model-agentic-health lesson: put reliability in the harness, not in a
# model's recall).
#
# The query shape below is the one `honest_status.sh` already proved works:
# list by BRANCH, then match the SHA client-side. Never `--commit`.
#
# Usage:
#   scripts/wait_for_ci.sh                      # current HEAD
#   scripts/wait_for_ci.sh <sha>                # explicit SHA (short or full)
#   scripts/wait_for_ci.sh --timeout 600 <sha>
#   scripts/wait_for_ci.sh --branch main --interval 15 --grace 180 <sha>
#
# Exit codes follow the honest_status.sh convention — UNKNOWN is NEVER a pass:
#   0  CI completed with conclusion=success
#   1  CI completed, proven NOT green (failure/cancelled/timed_out/…)
#   2  UNKNOWN — no run found, gh missing/unauthed, or we timed out waiting
set -u

REPO="${REPO:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
BRANCH="main"
TIMEOUT=900          # hard ceiling; NEVER loop forever
INTERVAL=20
GRACE=180            # how long "no run yet" is tolerated after a fresh push
SHA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --branch)   BRANCH="$2"; shift 2 ;;
    --timeout)  TIMEOUT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --grace)    GRACE="$2"; shift 2 ;;
    -h|--help)  sed -n '1,40p' "$0"; exit 0 ;;
    -*)         echo "wait_for_ci: unknown option $1" >&2; exit 2 ;;
    *)          SHA="$1"; shift ;;
  esac
done

# A non-numeric --timeout/--interval/--grace makes every `-ge`/`-lt` test
# below evaluate FALSE with a warning — the ceiling never triggers and the
# loop this script exists to bound becomes unbounded again (2026-07-21
# review). Refuse loudly at parse time instead.
for v in "TIMEOUT=$TIMEOUT" "INTERVAL=$INTERVAL" "GRACE=$GRACE"; do
  case "${v#*=}" in
    ''|*[!0-9]*) echo "wait_for_ci: ${v%%=*} must be a non-negative integer (got '${v#*=}')" >&2
                 exit 2 ;;
  esac
done

if [ -z "$SHA" ]; then
  SHA=$(git -C "$REPO" rev-parse HEAD 2>/dev/null) || {
    echo "UNKNOWN: not a git repo and no SHA given"; exit 2; }
fi
# Accept a short SHA; resolve to full so the client-side match is exact.
FULL=$(git -C "$REPO" rev-parse "$SHA" 2>/dev/null || echo "$SHA")
SHORT="${FULL:0:8}"

if ! command -v gh >/dev/null 2>&1; then
  echo "UNKNOWN($SHORT): gh not installed — cannot verify CI externally"
  exit 2
fi

# Own tmp file per invocation. honest_status.sh uses a fixed /tmp name; two
# concurrent runs would interleave on it (honest_failure_modes #8 — "fixed tmp
# names are a collision, not a convention"), and this script is explicitly
# built to be run from scripts and hooks that may overlap.
TMP=$(mktemp "${TMPDIR:-/tmp}/wait_for_ci.XXXXXX.json") || {
  echo "UNKNOWN($SHORT): could not create temp file"; exit 2; }
trap 'rm -f "$TMP"' EXIT

start=$(date +%s)
last_state="(none)"

while :; do
  now=$(date +%s); elapsed=$((now - start))

  if gh run list --branch "$BRANCH" --limit 60 \
       --json headSha,conclusion,status,databaseId > "$TMP" 2>/dev/null; then
    read -r ST CC RID < <(FULL="$FULL" TMP="$TMP" python3 - <<'PY' 2>/dev/null
import json, os, sys
sha = os.environ["FULL"]
try:
    runs = json.load(open(os.environ["TMP"]))
except Exception:
    sys.exit()
for r in runs:
    if r.get("headSha") == sha:
        print(r.get("status", ""), r.get("conclusion", ""), r.get("databaseId", ""))
        break
PY
)
    if [ -n "${ST:-}" ]; then
      last_state="$ST"
      if [ "$ST" = "completed" ]; then
        if [ "$CC" = "success" ]; then
          echo "PASS($SHORT): run $RID conclusion=success after ${elapsed}s"
          exit 0
        fi
        echo "FAIL($SHORT): run $RID conclusion=$CC after ${elapsed}s"
        exit 1
      fi
    else
      # No run matched. Early = the push may not have registered yet; late =
      # genuinely unobservable. Absence must NOT be retried forever — that is
      # exactly the bug this script exists to retire.
      if [ "$elapsed" -ge "$GRACE" ]; then
        echo "UNKNOWN($SHORT): no CI run found for this SHA after ${elapsed}s" \
             "(pushed? correct branch '$BRANCH'? note: 'gh run list --commit'" \
             "returns empty on this gh version — do not use it)"
        exit 2
      fi
      last_state="no-run-yet"
    fi
  else
    echo "UNKNOWN($SHORT): 'gh run list' failed (auth?) after ${elapsed}s"
    exit 2
  fi

  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "UNKNOWN($SHORT): still '$last_state' after ${elapsed}s" \
         "(timeout ${TIMEOUT}s) — not green, not proven red; check manually"
    exit 2
  fi
  sleep "$INTERVAL"
done
