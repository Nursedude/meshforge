#!/usr/bin/env bash
# honest_status.sh — operator-owned verification gate.
#
# Re-checks the dev + fleet state from EXTERNAL ground truth so you never
# have to trust an AI summary. Born 2026-06-15 from the "AI is convincing me
# things are good and that's not true" concern: the AI asserts green; THIS
# re-derives it from systems the AI (and the local harness) can't fabricate —
# GitHub CI, git SHAs over ssh, the live HTTP API, real test/lint exit codes.
#
# Two tiers, kept distinct so `exit 0` stays meaningful:
#   VERIFICATION — CI, fleet SHA, full suite, lint, live-honesty, watchdog
#     WEDGE. A FAIL here means the code/deploy I claimed is not green.
#   FLEET WARNINGS — watchdog DEGRADED signals. Real conditions, surfaced
#     LOUD, but not necessarily this code's fault (e.g. a remote node
#     flapping); they do not by themselves fail the verification verdict.
#
# Cardinal rule (honest_failure_modes #2): UNKNOWN (box unreachable, gh not
# authenticated, endpoint absent) is NEVER counted as PASS — unobservable is
# not healthy. A WARN is never hidden — it is printed and counted in the
# summary, so `exit 0` never means "nothing is wrong", only "code+deploy
# verified; read the warnings".
#
#   exit 0 = all verification PASSED, nothing UNKNOWN (warnings surfaced)
#   exit 1 = a verification check FAILED (incl. a watchdog WEDGE) — not green
#   exit 2 = no failures but something couldn't be verified — NOT green
#   --strict promotes WARNINGS to failures (exit 1) for "nothing may be wrong"
#
# Usage:
#   bash scripts/honest_status.sh             # full (runs the local suite, ~3 min)
#   bash scripts/honest_status.sh --quick     # skip the local suite (UNKNOWN for it)
#   bash scripts/honest_status.sh --strict    # fleet warnings also fail the gate
#   HONEST_BOXES="moc moc1" bash scripts/honest_status.sh   # override fleet list
set -u

REPO="${MESHFORGE_REPO:-/opt/meshforge}"
BOXES="${HONEST_BOXES:-moc moc1 moc2 moc3 moc5}"
# Watchdog state path — overridable (HONEST_WD_PATH) ONLY so the gate's own
# severity logic can be exercised end-to-end against fixtures; production is
# the default. Never point this at production fixtures.
WD_PATH="${HONEST_WD_PATH:-/var/lib/meshforge/watchdog.json}"
RUN_TESTS=1
STRICT=0
for arg in "$@"; do
  case "$arg" in
    --quick)  RUN_TESTS=0 ;;
    --strict) STRICT=1 ;;
  esac
done

SSH="ssh -o ConnectTimeout=8 -o BatchMode=yes"
pass=0; fail=0; unknown=0; warns=0
ok()    { printf '  %-22s \033[32mPASS\033[0m    %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()   { printf '  %-22s \033[31mFAIL\033[0m    %s\n' "$1" "$2"; fail=$((fail+1)); }
unk()   { printf '  %-22s \033[33mUNKNOWN\033[0m %s\n' "$1" "$2"; unknown=$((unknown+1)); }
warnf() { printf '  %-22s \033[33mWARN\033[0m    %s\n' "$1" "$2"; warns=$((warns+1)); }

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
  else
    # Surface the failing test id(s) (already in the captured file, just not
    # shown before) AND preserve the log to a durable path — NOT /tmp (RTC-less
    # Pis clear it on reboot), overwrite-on-failure-ONLY so a later green run
    # can't clobber it. Every honest_status run samples the suite under
    # concurrent load (CI + ssh probes), so a rare timing flake's traceback
    # finally gets captured instead of discarded.
    names=$(grep -E "^FAILED|^ERROR" /tmp/.hs_pytest | sed -E 's/^(FAILED|ERROR) //; s/ -.*//' | head -3 | paste -sd' ' -)
    fdir="${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/hs_failures"; saved=""
    mkdir -p "$fdir" 2>/dev/null && cp /tmp/.hs_pytest "$fdir/last_failure.log" 2>/dev/null \
      && saved=" — saved $fdir/last_failure.log"
    bad "full suite" "exit $rc, $nfail FAILED/ERROR${names:+ ($names)}$saved — $summ"
  fi
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

# 6. Watchdog — classify by severity. A WEDGE is real breakage (FAIL); a
#    DEGRADED signal is a surfaced concern (WARN), loud but not necessarily
#    this code's fault. Unreachable box = UNKNOWN (can't confirm clean).
#    Verdict = worst present, ordered FAIL(wedge) > UNKNOWN(unreach) >
#    WARN(degraded) > PASS — a signal is never hidden behind a green line.
# Watchdog freshness threshold. The watchdog tick is 30s (DEFAULT_TICK_S); >10
# ticks with no fresh write means the loop wedged and its last (possibly
# 0-signal) snapshot is STALE — which would otherwise read as "clean" here: the
# §3c "active != doing the job" false-green, for the watchdog daemon ITSELF. A
# watchdog self-probe can't catch a wedged loop (a stuck loop never runs the
# probe), so this EXTERNAL gate is the non-circular check. Disabled (0) in
# fixture mode (HONEST_WD_PATH set), where fixtures carry a static ts, unless
# HONEST_WD_STALE_S is set explicitly to exercise the stale path.
if [ -n "${HONEST_WD_STALE_S:-}" ]; then WD_STALE_S="$HONEST_WD_STALE_S"
elif [ -n "${HONEST_WD_PATH:-}" ]; then WD_STALE_S=0
else WD_STALE_S=300; fi

wedge_t=0; deg_t=0; clean=0; unreach=0; sigdesc=""
btotal=$(echo $BOXES | wc -w)
for b in $BOXES; do
  # Fetch the box's OWN clock alongside its watchdog.json in ONE round-trip, so
  # the freshness age is computed same-clock — never this box's clock vs that
  # box's ts (cross-machine wall-clock is forgeable: honest_failure #6).
  raw=$($SSH "$b" "date +%s 2>/dev/null; echo '---WDSEP---'; cat $WD_PATH 2>/dev/null" 2>/dev/null)
  rnow=$(printf '%s\n' "$raw" | sed -n '1p')
  w=$(printf '%s\n' "$raw" | awk 'f{print} /^---WDSEP---$/{f=1}')
  if [ -z "$w" ]; then unreach=$((unreach+1)); sigdesc="$sigdesc $b:unreach"; continue; fi
  p=$(printf '%s' "$w" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: print("PARSE"); sys.exit()
s=d.get("signals",[])
wg=sum(1 for x in s if x.get("severity")=="wedge")
dg=len(s)-wg
ts=d.get("ts")
tsf=("%.3f"%ts) if isinstance(ts,(int,float)) else "NOTS"
cl=",".join("%s(%s)"%(x.get("class","?"),x.get("severity","?")) for x in s)
print("%d %d %s %s"%(wg,dg,tsf,cl))' 2>/dev/null)
  # Unreadable/garbage watchdog.json is UNKNOWN, never "clean" — a file I
  # can't parse must not read as healthy (the exact false-green this tool
  # exists to prevent; caught by the classifier drill 2026-06-15).
  if [ -z "$p" ] || [ "$p" = "PARSE" ]; then
    unreach=$((unreach+1)); sigdesc="$sigdesc $b:unparseable"; continue
  fi
  wg=$(printf '%s' "$p" | awk "{print \$1}"); dg=$(printf '%s' "$p" | awk "{print \$2}")
  ts=$(printf '%s' "$p" | awk "{print \$3}"); cl=$(printf '%s' "$p" | cut -d" " -f4-)
  # Freshness gate: a valid-but-stale snapshot (wedged loop) is NOT clean.
  # Same UNKNOWN tier as unparseable — old signals are not current truth.
  if [ "$WD_STALE_S" -gt 0 ] && [ "$ts" != "NOTS" ] && [ -n "$rnow" ]; then
    age=$(awk "BEGIN{printf \"%d\", $rnow - $ts}" 2>/dev/null)
    if [ -n "$age" ] && [ "$age" -gt "$WD_STALE_S" ] 2>/dev/null; then
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:stale(${age}s)"; continue
    fi
  fi
  if [ "${wg:-0}" = 0 ] && [ "${dg:-0}" = 0 ]; then clean=$((clean+1))
  else sigdesc="$sigdesc $b:[$cl]"; wedge_t=$((wedge_t+wg)); deg_t=$((deg_t+dg)); fi
done
if [ "$wedge_t" -gt 0 ]; then bad "watchdog (wedge)" "$wedge_t WEDGE + $deg_t degraded across fleet:$sigdesc"
elif [ "$unreach" -gt 0 ]; then unk "watchdog signals" "$clean/$btotal clean, $unreach unreachable/stale:$sigdesc"
elif [ "$deg_t" -gt 0 ]; then warnf "watchdog (degraded)" "$deg_t degraded, 0 wedge:$sigdesc"
else ok "watchdog signals" "$clean/$btotal clean, 0 signals"; fi

echo
total_checks=$((pass+fail+unknown+warns))
SUM="$pass/$total_checks PASS"
[ "$warns"   -gt 0 ] && SUM="$SUM, $warns WARN"
[ "$unknown" -gt 0 ] && SUM="$SUM, $unknown UNKNOWN"
[ "$fail"    -gt 0 ] && SUM="$SUM, $fail FAIL"

if [ "$fail" -gt 0 ]; then
  verdict_rc=1; verdict_msg="$SUM  (proven not-green)"
elif [ "$STRICT" = 1 ] && [ "$warns" -gt 0 ]; then
  verdict_rc=1; verdict_msg="$SUM  (--strict: warnings treated as failures — not clean)"
elif [ "$unknown" -gt 0 ]; then
  verdict_rc=2; verdict_msg="$SUM  (could not fully verify — NOT green)"
elif [ "$warns" -gt 0 ]; then
  verdict_rc=0; verdict_msg="$SUM  (code+deploy verified; $warns fleet warning(s) surfaced above — read them)"
else
  verdict_rc=0; verdict_msg="$SUM  (fully verified green)"
fi
echo "--> $verdict_msg"

# Durable verdict marker — the unfabricatable record the calibration claim-gate
# and ledger read: "honest_status ran for THIS HEAD at THIS time with THIS
# verdict." User-writable + env-overridable (tests point HONEST_VERDICT_PATH at a
# tmp path). Best-effort by design: a marker-write failure must NEVER change the
# verdict the operator just saw — but it leaves a stderr witness
# (honest_failure_modes #9), never a silent swallow. A missing/old marker simply
# reads as "this HEAD is unverified" downstream, which is the safe direction.
VERDICT_PATH="${HONEST_VERDICT_PATH:-${HOME:-/tmp}/.cache/meshforge/honest_verdict.json}"
if ! HV_RC="$verdict_rc" HV_MSG="$verdict_msg" HV_HEAD="$HEADFULL" \
     HV_FULL="$RUN_TESTS" HV_STRICT="$STRICT" HV_PATH="$VERDICT_PATH" \
     python3 - <<'PY' 2>/dev/null
import json, os, tempfile, time
p = os.environ["HV_PATH"]
d = os.path.dirname(os.path.abspath(p)) or "."
os.makedirs(d, exist_ok=True)
payload = json.dumps({
    "head_full": os.environ.get("HV_HEAD", ""),
    "exit_code": int(os.environ.get("HV_RC", "2") or 2),
    "ts": time.time(),
    "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "summary": os.environ.get("HV_MSG", ""),
    "ran_full_suite": os.environ.get("HV_FULL") == "1",
    "strict": os.environ.get("HV_STRICT") == "1",
}, indent=2)
fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(p) + ".", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
PY
then
  echo "honest_status: WARN — could not write verdict marker $VERDICT_PATH" \
       "(claim-gate will treat this HEAD as unverified)" >&2
fi

exit "$verdict_rc"
