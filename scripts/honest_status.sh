#!/usr/bin/env bash
# honest_status.sh — operator-owned verification gate.
#
# Re-checks the dev + fleet state from EXTERNAL ground truth so you never
# have to trust an AI summary. Born 2026-06-15 from the "AI is convincing me
# things are good and that's not true" concern: the AI asserts green; THIS
# re-derives it from systems the AI (and the local harness) can't fabricate —
# GitHub CI, git SHAs over ssh, the live HTTP API, real test/lint exit codes.
#
# Every line is PASS / FAIL / UNKNOWN with the raw evidence. The cardinal
# rule (honest_failure_modes #2): UNKNOWN (box unreachable, gh not
# authenticated, endpoint absent) is NEVER counted as PASS — unobservable is
# not healthy. So:
#   exit 0  = every check PASSED and nothing was UNKNOWN  (fully verified green)
#   exit 1  = at least one check FAILED                    (proven not-green)
#   exit 2  = no failures but something couldn't be verified (NOT green)
#
# Usage:
#   bash scripts/honest_status.sh           # full (runs the local suite, ~3 min)
#   bash scripts/honest_status.sh --quick   # skip the local suite (UNKNOWN for it)
#   HONEST_BOXES="moc moc1" bash scripts/honest_status.sh   # override fleet list
set -u

REPO="${MESHFORGE_REPO:-/opt/meshforge}"
BOXES="${HONEST_BOXES:-moc moc1 moc2 moc3 moc5}"
RUN_TESTS=1
[ "${1:-}" = "--quick" ] && RUN_TESTS=0

SSH="ssh -o ConnectTimeout=8 -o BatchMode=yes"
pass=0; fail=0; unknown=0
ok()  { printf '  %-22s \033[32mPASS\033[0m  %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf '  %-22s \033[31mFAIL\033[0m  %s\n' "$1" "$2"; fail=$((fail+1)); }
unk() { printf '  %-22s \033[33mUNKNOWN\033[0m %s\n' "$1" "$2"; unknown=$((unknown+1)); }

HEAD=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "?")
HEADFULL=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "?")
echo "honest_status — $REPO @ $HEAD  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo

# 1. CI conclusion for the EXACT current HEAD (external, harness-immune).
if command -v gh >/dev/null 2>&1; then
  if gh run list --branch main --limit 30 \
       --json headSha,conclusion,status,databaseId >/tmp/.hs_ci.json 2>/dev/null; then
    read -r ST CC RID < <(HEADFULL="$HEADFULL" python3 - <<'PY' 2>/dev/null
import json, os, sys
sha = os.environ["HEADFULL"]
try:
    runs = json.load(open("/tmp/.hs_ci.json"))
except Exception:
    sys.exit()
for r in runs:
    if r.get("headSha") == sha:
        print(r.get("status",""), r.get("conclusion",""), r.get("databaseId",""))
        break
PY
)
    if [ -z "${ST:-}" ]; then unk "CI($HEAD)" "no CI run found for this SHA (pushed yet?)"
    elif [ "$ST" != "completed" ]; then unk "CI($HEAD)" "run $RID still $ST"
    elif [ "$CC" = "success" ]; then ok "CI($HEAD)" "run $RID success"
    else bad "CI($HEAD)" "run $RID conclusion=$CC"; fi
  else
    unk "CI($HEAD)" "gh present but 'gh run list' failed (auth?)"
  fi
else
  unk "CI($HEAD)" "gh not installed — cannot verify CI externally"
fi

# 2. Fleet SHA drift — each box's HEAD vs this repo's HEAD (external).
matched=0; reached=0; total=0; desc=""
for b in $BOXES; do
  total=$((total+1))
  # Compare FULL 40-char SHAs — abbreviation length varies per box (a 7-char
  # local abbrev vs an 8-char remote one is the SAME commit, not drift).
  s=$($SSH "$b" "git -C $REPO rev-parse HEAD" 2>/dev/null)
  if [ -z "$s" ]; then desc="$desc $b:unreach"; continue; fi
  reached=$((reached+1))
  if [ "$s" = "$HEADFULL" ]; then matched=$((matched+1)); else desc="$desc $b:${s:0:7}"; fi
done
drifted=$((reached - matched))
if [ "$drifted" -gt 0 ]; then bad "fleet SHA drift" "$matched/$total @ $HEAD;$desc"
elif [ "$reached" -lt "$total" ]; then unk "fleet SHA drift" "$matched/$reached reachable @ $HEAD;$desc"
else ok "fleet SHA drift" "$matched/$total @ $HEAD"; fi

# 3. Full local suite — real exit code + count (file-routed, never a streamed tail).
if [ "$RUN_TESTS" = 1 ]; then
  python3 -m pytest "$REPO/tests/" -q -p no:cacheprovider >/tmp/.hs_pytest 2>&1; rc=$?
  summ=$(grep -E "[0-9]+ (passed|failed|error)" /tmp/.hs_pytest | tail -1)
  nfail=$(grep -cE "^FAILED|^ERROR" /tmp/.hs_pytest)
  if [ "$rc" = 0 ] && [ "$nfail" = 0 ]; then ok "full suite" "$summ (exit 0)"
  else bad "full suite" "exit $rc, $nfail FAILED/ERROR — $summ"; fi
else
  unk "full suite" "skipped (--quick) — not verified"
fi

# 4. Lint — real exit code.
python3 "$REPO/scripts/lint.py" --all >/tmp/.hs_lint 2>&1; rc=$?
if [ "$rc" = 0 ]; then ok "lint" "exit 0"
else bad "lint" "exit $rc — $(grep -E '\[E\]' /tmp/.hs_lint | tail -1)"; fi

# 5. Live honesty assert — no displayed confirmation_rate may exceed 1.0
#    (the exact #74 false-green: a rate that read 1.64 = ">164% confirmed").
viol=""; checked=0; det=""
for b in $BOXES; do
  j=$($SSH "$b" "curl -s --max-time 8 http://localhost:5000/api/gateway/delivery 2>/dev/null" 2>/dev/null)
  [ -z "$j" ] && continue   # no map served here — not a failure, just nothing to check
  checked=$((checked+1))
  v=$(printf '%s' "$j" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: print("PARSE"); sys.exit()
r=d.get("confirmation_rate")
if r is None: print("none")
elif isinstance(r,(int,float)) and not isinstance(r,bool) and r>1.0: print("VIOL=%.3f"%r)
else: print("%.3f"%r if isinstance(r,(int,float)) else "shape?")' 2>/dev/null)
  det="$det $b:$v"
  case "$v" in VIOL*) viol="$viol$b:$v ";; esac
done
if [ -n "$viol" ]; then bad "live conf_rate<=1.0" "$viol"
elif [ "$checked" = 0 ]; then unk "live conf_rate<=1.0" "no box served /api/gateway/delivery"
else ok "live conf_rate<=1.0" "$checked checked;$det"; fi

# 6. Watchdog — surface ACTIVE signals (don't hide them behind a green summary).
clean=0; wreach=0; sigdesc=""
for b in $BOXES; do
  w=$($SSH "$b" "cat /var/lib/meshforge/watchdog.json 2>/dev/null" 2>/dev/null)
  [ -z "$w" ] && { sigdesc="$sigdesc $b:?"; continue; }
  wreach=$((wreach+1))
  n=$(printf '%s' "$w" | python3 -c 'import sys,json
try: print(len(json.load(sys.stdin).get("signals",[])))
except Exception: print("?")' 2>/dev/null)
  if [ "$n" = 0 ]; then clean=$((clean+1)); else sigdesc="$sigdesc $b:${n}sig"; fi
done
if [ -n "$sigdesc" ] && printf '%s' "$sigdesc" | grep -q "sig"; then
  bad "watchdog signals" "$clean/$wreach clean; ACTIVE:$sigdesc"
elif [ "$wreach" -lt "$(echo $BOXES | wc -w)" ]; then
  unk "watchdog signals" "$clean/$wreach reachable clean;$sigdesc"
else ok "watchdog signals" "$clean/$wreach clean, 0 active signals"; fi

echo
total_checks=$((pass+fail+unknown))
if [ "$fail" -gt 0 ]; then
  printf -- '--> \033[31m%d/%d PASS, %d FAIL, %d UNKNOWN\033[0m  (proven not-green)\n' "$pass" "$total_checks" "$fail" "$unknown"
  exit 1
elif [ "$unknown" -gt 0 ]; then
  printf -- '--> \033[33m%d/%d PASS, %d UNKNOWN\033[0m  (could not fully verify — NOT green)\n' "$pass" "$total_checks" "$unknown"
  exit 2
else
  printf -- '--> \033[32m%d/%d PASS\033[0m  (fully verified green)\n' "$pass" "$total_checks"
  exit 0
fi
