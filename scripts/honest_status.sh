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

# Fleet box list — DERIVED from the same `fleet_hosts` SSOT that fleet_pull.sh
# and fleet_dup_collector read, never a second hardcode.
#
# It WAS a second hardcode ("moc moc1 moc2 moc3 moc5") and it drifted exactly
# as honest_failure_modes #5 predicts (two consumers of one artifact, two
# independent constants): fleet_hosts grew to 8 boxes while this list stayed at
# 5, so the gate printed "fleet SHA drift PASS 5/5" — which READS as whole-fleet
# coverage — while moc4, kiai and meshanchor-server were never checked at all,
# and the manager box checked no box but its own repo (2026-07-28).
#
# Self is appended: the manager runs a watchdog and a map too, and fleet_hosts
# deliberately omits it (there is no ssh to self), so it was unrepresented in
# every fleet leg. Provenance is PRINTED below — a narrowed list must never be
# able to masquerade as the whole fleet again.
SELF="$(hostname 2>/dev/null || echo localhost)"
_hs_hosts_file() {
  for f in "${MESHFORGE_FLEET_HOSTS:-}" \
           "${HOME:-/root}/.config/meshforge/fleet_hosts" \
           /etc/meshforge/fleet_hosts; do
    [ -n "$f" ] && [ -f "$f" ] && { printf '%s' "$f"; return 0; }
  done
  return 1
}
if [ -n "${HONEST_BOXES:-}" ]; then
  BOXES="$HONEST_BOXES"; BOXES_SRC="HONEST_BOXES override"
elif _hf="$(_hs_hosts_file)"; then
  BOXES="$(sed 's/#.*//' "$_hf" | tr '\n' ' ' | tr -s ' ')"
  BOXES="$(printf '%s %s' "$BOXES" "$SELF" | tr -s ' ' | sed 's/^ *//; s/ *$//')"
  BOXES_SRC="$_hf + self"
else
  # No SSOT reachable. Check what we CAN (self) and say so — inventing a fleet
  # list here is how the 5-box lie happened. Narrow is fine; narrow that reads
  # as complete is not.
  BOXES="$SELF"; BOXES_SRC="SELF ONLY — no fleet_hosts found, fleet legs cover 1 box"
fi

# Run a command on a box: locally when it IS this box (there is no ssh to
# self), over ssh otherwise. Callers pass ONE command string, as before.
run_on() {
  _rb="$1"; shift
  if [ "$_rb" = "$SELF" ]; then bash -c "$*" 2>/dev/null
  else $SSH "$_rb" "$*" 2>/dev/null; fi
}
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

# Per-run scratch dir. These were FIXED names (/tmp/.hs_pytest, .hs_lint,
# .hs_ci.json) — honest_failure_modes #8, "fixed tmp names are a collision,
# not a convention". Two honest_status runs overlapping (a cron run and a
# manual one, or the suite leg running a test that itself drives this script)
# interleaved their writes into one file, and the torn result made the suite
# leg report FAIL on an exit-0 run: a false NOT-GREEN from the gate whose
# whole job is to not lie about green. Observed 2026-07-28.
HS_TMP="$(mktemp -d -t honest_status.XXXXXX)"
trap 'rm -rf "$HS_TMP"' EXIT INT TERM

# Consumer-of-record interpreter (calibrated_claims rule 7; ported from the
# MA twin 2026-07-19, where bare python3 had no pytest at all and the suite
# leg read a false shape): the services' ExecStart prefers $REPO/venv/bin/
# python when present, so the suite/lint legs must test THAT interpreter —
# its dependency set is the one the fleet actually runs. Falls back to
# system python3 (venv-less boxes). Gate-local JSON parsing stays python3.
PY="python3"
[ -x "$REPO/venv/bin/python" ] && PY="$REPO/venv/bin/python"
pass=0; fail=0; unknown=0; warns=0
ok()    { printf '  %-22s \033[32mPASS\033[0m    %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()   { printf '  %-22s \033[31mFAIL\033[0m    %s\n' "$1" "$2"; fail=$((fail+1)); }
unk()   { printf '  %-22s \033[33mUNKNOWN\033[0m %s\n' "$1" "$2"; unknown=$((unknown+1)); }
warnf() { printf '  %-22s \033[33mWARN\033[0m    %s\n' "$1" "$2"; warns=$((warns+1)); }

HEAD=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "?")
HEADFULL=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "?")
echo "honest_status — $REPO @ $HEAD  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo "  fleet legs cover $(echo $BOXES | wc -w) box(es) — source: $BOXES_SRC"
echo

# 1. CI conclusion for the EXACT current HEAD (external, harness-immune).
if command -v gh >/dev/null 2>&1; then
  if gh run list --branch main --limit 30 \
       --json headSha,conclusion,status,databaseId >$HS_TMP/ci.json 2>/dev/null; then
    read -r ST CC RID < <(HEADFULL="$HEADFULL" HS_CI_JSON="$HS_TMP/ci.json" python3 - <<'PY' 2>/dev/null
import json, os, sys
sha = os.environ["HEADFULL"]
try:
    runs = json.load(open(os.environ["HS_CI_JSON"]))
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
matched=0; reached=0; total=0; norepo=0; desc=""
for b in $BOXES; do
  total=$((total+1))
  # Compare FULL 40-char SHAs — abbreviation length varies per box (a 7-char
  # local abbrev vs an 8-char remote one is the SAME commit, not drift).
  #
  # The liveness token separates two states an empty answer used to conflate
  # (2026-07-28, widening the list to the whole fleet): a box that is DOWN
  # (UNKNOWN — cannot confirm it converged) and a box that is UP but has no
  # repo at $REPO (a MeshAnchor-only box, say). The latter cannot drift, so
  # counting it against the denominator would make the gate permanently
  # UNKNOWN; it is reported and excluded, never silently dropped.
  raw=$(run_on "$b" "echo HSUP; git -C $REPO rev-parse HEAD 2>/dev/null")
  up=$(printf '%s\n' "$raw" | sed -n '1p')
  s=$(printf '%s\n' "$raw" | sed -n '2p')
  if [ "$up" != "HSUP" ]; then desc="$desc $b:unreach"; continue; fi
  if [ -z "$s" ]; then norepo=$((norepo+1)); desc="$desc $b:no-repo"; continue; fi
  reached=$((reached+1))
  if [ "$s" = "$HEADFULL" ]; then matched=$((matched+1)); else desc="$desc $b:${s:0:7}"; fi
done
drifted=$((reached - matched))
expect=$((total - norepo))
if [ "$drifted" -gt 0 ]; then bad "fleet SHA drift" "$matched/$expect @ $HEAD;$desc"
elif [ "$reached" -lt "$expect" ]; then unk "fleet SHA drift" "$matched/$reached reachable of $expect @ $HEAD;$desc"
elif [ "$reached" = 0 ]; then unk "fleet SHA drift" "no box carried $REPO;$desc"
else ok "fleet SHA drift" "$matched/$expect @ $HEAD${desc:+;$desc}"; fi

# 3. Full local suite — file-routed, never a streamed tail.
#
# PASS needs THREE independent signals to agree (2026-07-28): the exit code,
# the absence of FAILED/ERROR/INTERNALERROR lines, AND a summary line that
# affirmatively reports passes with no failures or errors. Any disagreement
# resolves to not-PASS, and an ABSENT or non-committal summary is UNKNOWN —
# never PASS.
#
# WHY: pytest's process exit status is not trustworthy on this fleet. Measured
# here 2026-07-28 — on the full suite the interpreter exits 0 while pytest's
# own pytest_sessionfinish hook reports `ExitCode.TESTS_FAILED: 1` with
# testsfailed=1. Byte-identical output, ~50% of runs, and it VANISHES when a
# probe adds work at shutdown, so it is a race in interpreter shutdown (the
# suite leaks ~25 non-daemon ThreadPoolExecutor workers that get joined
# there). pytest computed 1; the kernel reported 0.
#
# The old gate happened to survive that because it also required nfail==0 and
# the run printed FAILED lines. It would NOT have survived the same lost exit
# code next to an INTERNALERROR (which starts "INTERNALERROR>", matching
# neither ^FAILED nor ^ERROR) or a crash that printed no summary at all: rc=0
# + nfail=0 read as PASS. Two signals that agree until the day they don't —
# the same defect class as everything else in this file.
_hs_preserve() {  # keep the log of any non-green run (NOT /tmp: RTC-less Pis
                  # clear it on reboot). Overwrite on non-green ONLY, so a
                  # later green run cannot clobber the evidence.
  _hs_fdir="${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/hs_failures"
  mkdir -p "$_hs_fdir" 2>/dev/null \
    && cp $HS_TMP/pytest.log "$_hs_fdir/last_failure.log" 2>/dev/null \
    && printf ' — saved %s/last_failure.log' "$_hs_fdir"
}
if [ "$RUN_TESTS" = 1 ]; then
  "$PY" -m pytest "$REPO/tests/" -q -p no:cacheprovider >$HS_TMP/pytest.log 2>&1; rc=$?
  summ=$(grep -E "[0-9]+ (passed|failed|error)|no tests ran" $HS_TMP/pytest.log | tail -1)
  nfail=$(grep -cE "^FAILED|^ERROR" $HS_TMP/pytest.log)
  ninternal=$(grep -c "INTERNALERROR" $HS_TMP/pytest.log)
  # Does the summary affirmatively say "passes, and nothing failed"?
  nsumbad=$(printf '%s' "$summ" | grep -cE "[0-9]+ (failed|errors?)")
  nsumok=$(printf '%s' "$summ" | grep -cE "[0-9]+ passed")
  names=$(grep -E "^FAILED|^ERROR" $HS_TMP/pytest.log | sed -E 's/^(FAILED|ERROR) //; s/ -.*//' | head -3 | paste -sd' ' -)
  if [ -z "$summ" ]; then
    # pytest died before summarising (crash, OOM, killed). Unobservable is
    # never a pass, and it is not proven-bad either.
    unk "full suite" "no pytest summary line — suite did not report (exit $rc)$(_hs_preserve)"
  elif [ "$nfail" != 0 ] || [ "$ninternal" != 0 ] || [ "$nsumbad" != 0 ]; then
    bad "full suite" "exit $rc, $nfail FAILED/ERROR${ninternal:+, $ninternal INTERNALERROR}${names:+ ($names)}$(_hs_preserve) — $summ"
  elif [ "$rc" != 0 ]; then
    # Clean-looking output but a non-zero code: trust the WORSE signal.
    bad "full suite" "exit $rc with no FAILED/ERROR lines — exit code and output disagree$(_hs_preserve) — $summ"
  elif [ "$nsumok" = 0 ]; then
    # e.g. "no tests ran" — a broken invocation, not a green suite.
    unk "full suite" "summary reports no passing tests — nothing was verified$(_hs_preserve) — $summ"
  else
    ok "full suite" "$summ (exit 0)"
  fi
else
  unk "full suite" "skipped (--quick) — not verified"
fi

# 4. Lint — real exit code.
"$PY" "$REPO/scripts/lint.py" --all >$HS_TMP/lint.log 2>&1; rc=$?
if [ "$rc" = 0 ]; then ok "lint" "exit 0"
else bad "lint" "exit $rc — $(grep -E '\[E\]' $HS_TMP/lint.log | tail -1)"; fi

# 5. Live honesty assert — no displayed confirmation_rate may exceed 1.0
#    (the exact #74 false-green: a rate that read 1.64 = ">164% confirmed").
viol=""; checked=0; det=""
for b in $BOXES; do
  j=$(run_on "$b" "curl -s --max-time 8 http://localhost:5000/api/gateway/delivery 2>/dev/null")
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

wedge_t=0; deg_t=0; held_t=0; clean=0; unreach=0; nowd=0; sigdesc=""
btotal=$(echo $BOXES | wc -w)
for b in $BOXES; do
  # Fetch the box's OWN clock alongside its watchdog.json in ONE round-trip, so
  # the freshness age is computed same-clock — never this box's clock vs that
  # box's ts (cross-machine wall-clock is forgeable: honest_failure #6). The
  # unit state rides the SAME round-trip (2026-07-28) to split what an empty
  # answer used to conflate — see below.
  raw=$(run_on "$b" "date +%s 2>/dev/null; systemctl is-active meshforge-watchdog.service 2>/dev/null || echo absent; echo '---WDSEP---'; cat $WD_PATH 2>/dev/null")
  rnow=$(printf '%s\n' "$raw" | sed -n '1p')
  wunit=$(printf '%s\n' "$raw" | sed -n '2p')
  w=$(printf '%s\n' "$raw" | awk 'f{print} /^---WDSEP---$/{f=1}')
  # THREE states, not one (widening the list to the whole fleet exposed the
  # conflation): the box is DOWN; the box is up and runs NO watchdog (a
  # MeshAnchor-only box — a legitimately-absent organ, excluded from the
  # denominator, never counted as blindness); or the watchdog is ACTIVE yet
  # wrote no state, which is a real fault and must stay UNKNOWN-loud rather
  # than being excused as "not installed" (honest_failure_modes #2).
  if [ -z "$rnow" ]; then unreach=$((unreach+1)); sigdesc="$sigdesc $b:unreach"; continue; fi
  if [ -z "$w" ] && [ "$wunit" != "active" ]; then
    nowd=$((nowd+1)); sigdesc="$sigdesc $b:no-watchdog($wunit)"; continue
  fi
  if [ -z "$w" ]; then
    unreach=$((unreach+1)); sigdesc="$sigdesc $b:ACTIVE-but-no-state"; continue
  fi
  p=$(printf '%s' "$w" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: print("PARSE"); sys.exit()
s=d.get("signals",[])
# A signal carrying extra.unobserved_hold is LAST-KNOWN, not observed this
# tick (watchdog_tracker re-emits it so silence cannot read as recovery).
# Reporting it as "degraded" alongside live observations is the exact
# conflation this gate exists to prevent -- unobservable is UNKNOWN, not
# proven-bad. moc5 2026-07-27 held kernel_reboot_pending for 347 ticks
# after the condition was cured, because its probe was masked by the
# units OWN ProtectKernelModules=yes and could never see again.
# NOTE: no apostrophes below -- this whole block lives inside python3 -c
# single quotes, and one contraction ends the shell string (caught by
# bash -n 2026-07-27).
def _held(x):
    e=x.get("extra") or {}
    return bool(isinstance(e,dict) and e.get("unobserved_hold"))
live=[x for x in s if not _held(x)]
held=[x for x in s if _held(x)]
wg=sum(1 for x in live if x.get("severity")=="wedge")
dg=len(live)-wg
hd=len(held)
ts=d.get("ts")
tsf=("%.3f"%ts) if isinstance(ts,(int,float)) else "NOTS"
def _fmt(xs,suf=""):
    return ",".join("%s(%s%s)"%(x.get("class","?"),x.get("severity","?"),suf) for x in xs)
cl=",".join(z for z in (_fmt(live), _fmt(held,",held-blind")) if z)
print("%d %d %d %s %s"%(wg,dg,hd,tsf,cl))' 2>/dev/null)
  # Unreadable/garbage watchdog.json is UNKNOWN, never "clean" — a file I
  # can't parse must not read as healthy (the exact false-green this tool
  # exists to prevent; caught by the classifier drill 2026-06-15).
  if [ -z "$p" ] || [ "$p" = "PARSE" ]; then
    unreach=$((unreach+1)); sigdesc="$sigdesc $b:unparseable"; continue
  fi
  wg=$(printf '%s' "$p" | awk "{print \$1}"); dg=$(printf '%s' "$p" | awk "{print \$2}")
  hd=$(printf '%s' "$p" | awk "{print \$3}")
  ts=$(printf '%s' "$p" | awk "{print \$4}"); cl=$(printf '%s' "$p" | cut -d" " -f5-)
  # Freshness gate: a valid-but-stale snapshot (wedged loop) is NOT clean.
  # Same UNKNOWN tier as unparseable — old signals are not current truth.
  if [ "$WD_STALE_S" -gt 0 ] && [ "$ts" != "NOTS" ] && [ -n "$rnow" ]; then
    age=$(awk "BEGIN{printf \"%d\", $rnow - $ts}" 2>/dev/null)
    if [ -n "$age" ] && [ "$age" -gt "$WD_STALE_S" ] 2>/dev/null; then
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:stale(${age}s)"; continue
    fi
  fi
  held_t=$((held_t+${hd:-0}))
  if [ "${wg:-0}" = 0 ] && [ "${dg:-0}" = 0 ] && [ "${hd:-0}" = 0 ]; then clean=$((clean+1))
  else sigdesc="$sigdesc $b:[$cl]"; wedge_t=$((wedge_t+wg)); deg_t=$((deg_t+dg)); fi
done
# Order is deliberate: proven-bad outranks unobservable outranks held-blind.
# A held signal NEVER reaches the WARN/FAIL tiers on its own — it is
# last-known evidence from a blind observer, which is UNKNOWN by this
# project's tiering, and UNKNOWN is never a pass either.
wdtotal=$((btotal - nowd))   # boxes that actually run a watchdog
if [ "$wedge_t" -gt 0 ]; then bad "watchdog (wedge)" "$wedge_t WEDGE + $deg_t degraded across fleet:$sigdesc"
elif [ "$unreach" -gt 0 ]; then unk "watchdog signals" "$clean/$wdtotal clean, $unreach unreachable/stale:$sigdesc"
elif [ "$deg_t" -gt 0 ]; then warnf "watchdog (degraded)" "$deg_t degraded, 0 wedge:$sigdesc"
elif [ "$held_t" -gt 0 ]; then unk "watchdog signals" "$held_t held-blind (last-known, observer cannot see), 0 observed:$sigdesc"
elif [ "$wdtotal" = 0 ]; then unk "watchdog signals" "no box ran a watchdog:$sigdesc"
else ok "watchdog signals" "$clean/$wdtotal clean, 0 signals${sigdesc:+;$sigdesc}"; fi

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
