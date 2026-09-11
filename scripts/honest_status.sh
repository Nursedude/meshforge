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
# Resolution comes from the ONE sourceable resolver that fleet_pull.sh also
# uses — including the per-repo `fleet_hosts.<repo-basename>` tier. This WAS a
# hand-copy of fleet_pull.sh's chain held identical by a comment, and the two
# copies already disagreed (HOME defaulting, comment parsing) — the exact
# two-consumers-two-constants drift the box list exists to end, one tier up
# (honest_failure_modes #5; 2026-07-28 review). A missing lib falls through to
# the SELF-ONLY branch below, which is loud and never eligible for fleet PASS.
_HS_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib/fleet_hosts.sh"
_hs_resolved=0
if [ -f "$_HS_LIB" ]; then
  . "$_HS_LIB"
  fleet_hosts_resolve "$REPO" && _hs_resolved=1
fi
if [ -n "${HONEST_BOXES:-}" ]; then
  BOXES="$HONEST_BOXES"; BOXES_SRC="HONEST_BOXES override"; FLEET_SSOT=1
elif [ "$_hs_resolved" = 1 ]; then
  _hf="$FLEET_HOSTS_FILE"
  _hs_listed="$(printf '%s\n' "$FLEET_HOSTS_LIST" | tr '\n' ' ' | tr -s ' ' | sed 's/^ *//; s/ *$//')"
  BOXES="$(printf '%s %s' "$_hs_listed" "$SELF" | tr -s ' ' | sed 's/^ *//; s/ *$//')"
  if [ -z "$_hs_listed" ]; then
    # The file EXISTS but lists nobody (empty, or every line commented out).
    # Treating that as a found SSOT left BOXES = self while the fleet legs
    # stayed eligible for PASS — the same "one box reported as whole-fleet
    # coverage" defect as the SELF-ONLY path, entered through a different
    # door. fleet_pull.sh:66 already refuses this case out loud ("refusing
    # the silent no-op"); the gate that VERIFIES a deploy must not be laxer
    # than the tool that PERFORMS it (2026-07-28 review residual).
    BOXES_SRC="$_hf has no hosts listed — fleet legs cover 1 box (self)"
    FLEET_SSOT=0
  else
    BOXES_SRC="$_hf + self"; FLEET_SSOT=1
  fi
else
  # No SSOT reachable. Check what we CAN (self) and say so — inventing a fleet
  # list here is how the 5-box lie happened. Narrow is fine; narrow that reads
  # as complete is not.
  #
  # FLEET_SSOT=0 is what makes that last sentence ENFORCED rather than merely
  # printed: the fleet legs below refuse to emit PASS in this state. Printing
  # the provenance told the truth in a line the exit code ignored — and every
  # box that is not the manager lands here, because fleet_hosts is authored on
  # the manager and mirrored nowhere (verified 2026-07-28: moc/moc1/moc3 have
  # no such file). The gate was self-certifying on 8 of 9 boxes.
  BOXES="$SELF"; BOXES_SRC="SELF ONLY — no fleet_hosts found, fleet legs cover 1 box"
  FLEET_SSOT=0
fi

# Dedup, order-preserving. Self is appended unconditionally above, so a
# fleet_hosts that already lists this box counted it TWICE — inflating btotal
# and checking the same box twice in the conf_rate leg (drilled 2026-07-28:
# "<self>:0.000 <self>:0.000", "3/3" for two boxes). The manager's file
# omits self by convention, but that is a comment header, not a guarantee, and
# it describes the MANAGER's file only. Applied to every source, so a host
# listed twice in the SSOT is also one box: a count the gate prints as coverage
# must never be inflatable by a duplicate line.
BOXES="$(printf '%s\n' $BOXES | awk 'NF && !seen[$0]++' | tr '\n' ' ' | sed 's/ *$//')"
# Declared posture (2026-09-01, DORMANT arc): a box the operator switched OFF
# on purpose is neither drifted nor unreachable — it is reported as
# `<box>:dormant`, taken out of the fleet legs' denominators, and never lets
# a leg read PASS about it. Absent/broken declaration = every box checked.
_HP_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib/fleet_posture.sh"
if [ -f "$_HP_LIB" ]; then . "$_HP_LIB"; fleet_posture_read "$REPO"; fi
if ! command -v fleet_posture_is_silent >/dev/null 2>&1; then
  # The lib did not source (absent, unreadable, broken). Every call site wraps
  # the predicate in `2>/dev/null`, which turned rc 127 into "not dormant" for
  # every box SILENTLY (finding 24, 2026-09-09). Same safe default — watch
  # everything — but declared once, out loud, in the posture note below.
  fleet_posture_is_silent() { return 1; }
  FLEET_POSTURE_STATUS="posture-lib-missing"
fi
# ONE reader for the note (scripts/lib/fleet_posture.sh) so this script and any
# other consumer cannot disagree about what a posture "in effect" means -- and
# so the empty-but-declared case is testable without running this whole script.
if command -v fleet_posture_summary >/dev/null 2>&1; then
  _hp_note="$(fleet_posture_summary)"
else
  _hp_note="; POSTURE FILE NOT USABLE (${FLEET_POSTURE_STATUS:-reader-missing}) — every box checked"
fi

# Peers = every box that is NOT this one. The SHA-drift leg must use this, not
# BOXES: it compares a box's `rev-parse HEAD` against $HEADFULL, which this
# script derived from THIS box's repo, so for self the comparison is a
# tautology that cannot fail. Counting it padded both sides of the ratio with a
# guaranteed match ("9/9" where 8 boxes were real evidence), and on the
# SELF-ONLY path it WAS the whole leg — "fleet SHA drift PASS 1/1", a green
# verdict from zero external evidence (2026-07-28 review).
#
# Self stays in BOXES for the watchdog and conf_rate legs: those read real
# local state that no other box reports, and the manager was genuinely
# unrepresented there before. Vacuous is not the same as redundant.
PEERS="$(printf '%s\n' $BOXES | grep -vxF "$SELF" | tr '\n' ' ' | sed 's/ *$//')"

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
# EXIT cleans up. INT/TERM clean up AND EXIT — never resume.
#
# A handler that only cleans lets bash RESUME the script with the scratch dir
# already deleted, so every later leg reads its log as missing and reports
# FAIL. Measured 2026-09-07: an interrupted run printed
#   full suite  UNKNOWN  log .../pytest.log unreadable — cannot classify
#   lint        FAIL     exit 1 —          (a direct lint run was exit 0)
# and closed with "proven not-green" on a green tree. That is the SECOND route
# into the false-NOT-GREEN above: 07-28 came through fixed tmp NAMES, this one
# through the interrupt handler. An interrupted check knows nothing, so the
# only honest answer is UNKNOWN (2) — never a FAIL it did not observe.
trap 'rm -rf "$HS_TMP"' EXIT
trap 'rm -rf "$HS_TMP"; printf "\nUNKNOWN: interrupted — scratch state removed, refusing to classify\n" >&2; exit 2' INT TERM

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
# DISCLOSURE — a fact worth seeing that is NOT a verdict. Touches no counter and
# cannot move the exit code, on purpose: in a lab that exists to test/break/build,
# running-behind is the NORMAL state, so alarming on it would cry wolf every
# deploy and get tuned out. The failure mode is not HAVING drift, it is not being
# able to SEE it. Same treatment `accepted_blind_spots` gets in fleet_truth:
# surfaced as its own line, never averaged into a healthy-looking summary
# (honest_failure_modes #5).
disc()  { printf '  %-22s \033[36mNOTE\033[0m    %s\n' "$1" "$2"; }

HEAD=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "?")
HEADFULL=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "?")
echo "honest_status — $REPO @ $HEAD  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo "  fleet legs cover $(echo $BOXES | wc -w) box(es) — source: $BOXES_SRC"
echo

# 1. CI conclusion for the EXACT current HEAD (external, harness-immune).
#
# TWO sources, on purpose. `gh run list` is cheap, but it is a WINDOWED view of
# the Actions runs API, and that view transiently LAGS /commits/<sha>/check-runs
# in the minute after a run finishes — observed twice on 2026-09-03, on two
# different SHAs whose CI was complete and green both times. This leg used to
# read "absent from the listing" as "no CI run exists", so the check of record
# reported UNKNOWN over green work. That is the detector-defect class from its
# other side: a gate that cries UNKNOWN on healthy work gets tuned out, and then
# a REAL unknown has nowhere to stand out.
#
# So absence in the listing is never a verdict. It ESCALATES to the
# authoritative per-SHA endpoint, and only "absent from BOTH" is no-run-found.
#
# The states stay separate (honest_failure_modes #1 — a degraded value must not
# overlap the healthy domain): query-failed, unparseable, absent-from-both,
# still-running, and a real conclusion each say their own thing. "The query
# broke" must NEVER render as "you haven't pushed yet" — that reading sends you
# to look at your git remote instead of your credentials.
_ci_nwo() {   # owner/repo from the origin remote; ssh and https forms both
  local u
  u=$(git -C "$REPO" config --get remote.origin.url 2>/dev/null) || return 1
  [ -n "$u" ] || return 1
  u=${u%.git}
  printf '%s\n' "$u" | awk -F'[/:]' 'NF>1 {print $(NF-1)"/"$NF}'
}

# The authoritative per-SHA view. Echoes exactly one state:
#   HIT success <n> | HIT failure <names> | PENDING <names> | NONE | FAIL <why>
_ci_check_runs() {
  local nwo
  nwo=$(_ci_nwo) || { echo "FAIL cannot-resolve-owner/repo-from-origin"; return 0; }
  if ! gh api "repos/$nwo/commits/$HEADFULL/check-runs" \
        >"$HS_TMP/ci_cr.json" 2>"$HS_TMP/ci_cr.err"; then
    echo "FAIL $(head -c 120 "$HS_TMP/ci_cr.err" | tr '\n' ' ')"; return 0
  fi
  HS_CR="$HS_TMP/ci_cr.json" python3 - <<'PY'
import json, os
try:
    d = json.load(open(os.environ["HS_CR"]))
except Exception as e:
    print(f"FAIL unparseable-check-runs:{type(e).__name__}"); raise SystemExit
runs = d.get("check_runs") or []
if not runs:
    print("NONE"); raise SystemExit
pending = [r.get("name", "?") for r in runs if r.get("status") != "completed"]
if pending:
    print("PENDING " + ",".join(pending[:4])); raise SystemExit
# neutral/skipped are not failures — a skipped optional check must not turn a
# green head red (the inverse of the bug this whole block fixes).
bad = [f"{r.get('name','?')}={r.get('conclusion')}" for r in runs
       if r.get("conclusion") not in ("success", "neutral", "skipped")]
print("HIT failure " + ",".join(bad[:4]) if bad else f"HIT success {len(runs)}")
PY
}

if ! command -v gh >/dev/null 2>&1; then
  unk "CI($HEAD)" "gh not installed — cannot verify CI externally"
elif ! gh run list --branch main --limit 30 \
        --json headSha,conclusion,status,databaseId \
        >"$HS_TMP/ci.json" 2>"$HS_TMP/ci.err"; then
  unk "CI($HEAD)" "'gh run list' FAILED (auth/network — NOT evidence of no run): $(head -c 120 "$HS_TMP/ci.err" | tr '\n' ' ')"
else
  read -r TAG ST CC RID < <(HEADFULL="$HEADFULL" HS_CI_JSON="$HS_TMP/ci.json" python3 - <<'PY'
import json, os
sha = os.environ["HEADFULL"]
try:
    runs = json.load(open(os.environ["HS_CI_JSON"]))
except Exception:
    print("PARSEFAIL - - -"); raise SystemExit
for r in runs:
    if r.get("headSha") == sha:
        print("HIT", r.get("status") or "-", r.get("conclusion") or "-",
              r.get("databaseId") or "-")
        break
else:
    print("NOMATCH - - -")
PY
)
  case "${TAG:-PARSEFAIL}" in
    HIT)
      if [ "$ST" != "completed" ]; then unk "CI($HEAD)" "run $RID still $ST"
      elif [ "$CC" = "success" ]; then ok "CI($HEAD)" "run $RID success"
      else bad "CI($HEAD)" "run $RID conclusion=$CC"; fi ;;
    PARSEFAIL)
      unk "CI($HEAD)" "'gh run list' returned unparseable JSON — the QUERY broke; this is not evidence about the run" ;;
    *)
      # Absent from the windowed listing. Ask the authoritative endpoint before
      # calling it absent at all (the 2026-09-03 lag).
      CR=$(_ci_check_runs)
      case "$CR" in
        "HIT success"*) ok  "CI($HEAD)" "check-runs ${CR#HIT success } check(s) success (runs listing lagged)" ;;
        "HIT failure"*) bad "CI($HEAD)" "check-runs failing: ${CR#HIT failure }" ;;
        PENDING*)       unk "CI($HEAD)" "check-runs still running: ${CR#PENDING }" ;;
        NONE)           unk "CI($HEAD)" "absent from BOTH the runs listing and check-runs for this SHA (pushed yet?)" ;;
        FAIL*)          unk "CI($HEAD)" "not in runs listing; check-runs fallback FAILED (query, not verdict): ${CR#FAIL }" ;;
        *)              unk "CI($HEAD)" "not in runs listing; check-runs fallback returned an unrecognised state: $CR" ;;
      esac ;;
  esac
fi

# 2. Fleet SHA drift — each box's HEAD vs this repo's HEAD (external).
#
# ONE measurement for BOTH repos' fleets (finding 13, 2026-09-09): the twin
# leg below was a hand-copy of this loop that lacked the declared-posture
# exclusion, the no-repo denominator subtraction and the HSUP liveness token,
# so a dormant sister-repo box read `unreach` → UNKNOWN for the whole declared
# window and the check of record could not exit 0. Two consumers of one
# mechanism share it or they drift (honest_failure_modes #5).
#
# Compare FULL 40-char SHAs — abbreviation length varies per box (a 7-char
# local abbrev vs an 8-char remote one is the SAME commit, not drift).
#
# The liveness token separates two states an empty answer used to conflate
# (2026-07-28, widening the list to the whole fleet): a box that is DOWN
# (UNKNOWN — cannot confirm it converged) and a box that is UP but has no
# repo at $repo (a MeshAnchor-only box, say). The latter cannot drift, so
# counting it against the denominator would make the gate permanently
# UNKNOWN; it is reported and excluded, never silently dropped.
#
# "No repo" is proven by the .git path, NOT by empty git output (2026-07-28
# review): a box that carries the repo but whose git errors (dubious
# ownership over ssh, git not installed, corrupt .git) also prints nothing,
# and counting it norepo silently dropped it from the denominator — a PASS
# that never verified that box, the same conflation class one door over.
# A repo-present git failure stays in the denominator as unverified.
#
# Posture is asked BEFORE the round-trip (finding 24): a box the operator
# switched off is not sshed at all — it used to pay the ConnectTimeout and
# then be discarded.
sha_drift_measure() {  # $1=repo $2=headfull $3..=hosts → SD_* variables
  local repo="$1" headfull="$2" b raw up s; shift 2
  SD_matched=0; SD_reached=0; SD_total=0; SD_norepo=0; SD_dormant=0; SD_desc=""
  for b in "$@"; do
    SD_total=$((SD_total+1))
    if fleet_posture_is_silent "$b" 2>/dev/null; then SD_dormant=$((SD_dormant+1)); SD_desc="$SD_desc $b:dormant"; continue; fi
    raw=$(run_on "$b" "echo HSUP; if [ -e $repo/.git ]; then git -C $repo rev-parse HEAD 2>/dev/null || echo HSGITERR; else echo HSNOREPO; fi")
    up=$(printf '%s\n' "$raw" | sed -n '1p')
    s=$(printf '%s\n' "$raw" | sed -n '2p')
    if [ "$up" != "HSUP" ]; then SD_desc="$SD_desc $b:unreach"; continue; fi
    case "$s" in
      HSNOREPO) SD_norepo=$((SD_norepo+1)); SD_desc="$SD_desc $b:no-repo"; continue ;;
      HSGITERR|"") SD_desc="$SD_desc $b:git-error(repo present)"; continue ;;
    esac
    SD_reached=$((SD_reached+1))
    if [ "$s" = "$headfull" ]; then SD_matched=$((SD_matched+1)); else SD_desc="$SD_desc $b:${s:0:7}"; fi
  done
  SD_drifted=$((SD_reached - SD_matched))
  SD_expect=$((SD_total - SD_norepo - SD_dormant))
}
sha_drift_verdict() {  # $1=label $2=head-display $3=repo $4=fail-hint (may be "")
  if [ "$SD_drifted" -gt 0 ]; then bad "$1" "$SD_matched/$SD_expect @ $2;$SD_desc$4"
  elif [ "$SD_reached" -lt "$SD_expect" ]; then unk "$1" "$SD_matched/$SD_reached reachable of $SD_expect @ $2;$SD_desc"
  elif [ "$SD_reached" = 0 ]; then unk "$1" "no box carried $3;$SD_desc"
  else ok "$1" "$SD_matched/$SD_expect @ $2${SD_desc:+;$SD_desc}"; fi
}
sha_drift_measure "$REPO" "$HEADFULL" $PEERS
SD_desc="$_hp_note$SD_desc"
if [ -z "$PEERS" ]; then
  # Nothing external to compare against. Self is excluded by construction, so
  # there is no evidence here at all — not "converged", UNKNOWN.
  unk "fleet SHA drift" "no peer box to compare against — self cannot drift from itself ($BOXES_SRC)"
else
  sha_drift_verdict "fleet SHA drift" "$HEAD" "$REPO" ""
fi

# 2a. TWIN fleet SHA drift — the SISTER repo's own boxes (2026-09-09).
#
# WHY THIS LEG EXISTS. The leg above is scoped to $REPO (MeshForge), so
# MeshAnchor's fleet was outside the check of record entirely. On 2026-09-09
# FOUR MeshAnchor pushes landed with the deploy never run — meshanchor-server sat
# four commits behind for hours while this gate printed "fleet SHA drift PASS
# 9/9". That 9/9 was TRUE and about the wrong repo, which is this file's own
# recurring defect class read back at it.
#
# It surfaced only by ACCIDENT: parity_drift on that box went indeterminate
# because a byte-locked test file was absent there. Had that file not been
# byte-locked hours earlier, the drift could have sat indefinitely with every
# leg green. The tooling was never the gap — `fleet_pull.sh /opt/meshanchor` and
# fleet_hosts.meshanchor have both existed since 2026-07-16. Nothing CALLED
# them, and nothing could SEE that.
#
# Documentation was not the fix and had already been tried: the instruction
# ("push the sister repo from the dev box, PULL on its own fleet box") was in the operator's
# memory, injected at session start, read, and missed four times that day.
#
# ⚠️ THE HOST LIST MUST BE THE PER-REPO ONE. fleet_hosts_resolve falls back to
# the GENERIC fleet_hosts, and for this repo that means comparing 9 boxes when
# the sister repo lives on 1 — exactly the "every MA deploy reads 6 NOT
# converged" wolf-cry fixed on 2026-07-16. If the per-repo list did not win the
# resolution, this leg reports UNKNOWN rather than judging against the wrong
# denominator. Wrong-denominator is not a verdict.
#
# ABSENT is not a verdict either: on 8 of 10 boxes the sister repo is absent BY
# DESIGN, so a run from there emits a NOTE that touches no counter — never
# UNKNOWN, which would make the gate un-greenable where the twin does not live.
TWIN_REPO="${HONEST_TWIN_REPO:-/opt/meshanchor}"
if [ ! -e "$TWIN_REPO/.git" ]; then
  disc "twin fleet SHA" "$TWIN_REPO not on this box — sister repo absent by design here"
elif ! TWIN_HEADFULL=$(git -C "$TWIN_REPO" rev-parse HEAD 2>/dev/null); then
  unk "twin fleet SHA" "$TWIN_REPO present but git could not read HEAD — unobservable, not converged"
else
  # Subshell so the resolver cannot leak FLEET_HOSTS_* into later legs.
  _twin_out=$(
    if [ -r "$_HS_LIB" ]; then
      . "$_HS_LIB"
      unset MESHFORGE_FLEET_HOSTS FLEET_HOSTS
      if fleet_hosts_resolve "$TWIN_REPO"; then
        printf '%s\n' "FILE=$FLEET_HOSTS_FILE"
        printf '%s\n' "$FLEET_HOSTS_LIST" | sed 's/^/HOST=/'
      fi
    fi
  )
  _twin_file=$(printf '%s\n' "$_twin_out" | sed -n 's/^FILE=//p')
  _twin_rb=$(basename "$TWIN_REPO")
  if [ -z "$_twin_file" ]; then
    unk "twin fleet SHA" "no host list resolved for $TWIN_REPO — cannot tell where the sister repo is deployed"
  elif [ "$(basename "$_twin_file")" != "fleet_hosts.$_twin_rb" ]; then
    unk "twin fleet SHA" "resolved the GENERIC $(basename "$_twin_file") for $_twin_rb — refusing to judge the sister repo against the wrong denominator (create fleet_hosts.$_twin_rb)"
  else
    # The SAME measurement as leg 2 — posture exclusion, liveness token,
    # no-repo denominator — never a second loop (finding 13).
    _twin_hosts=$(printf '%s\n' "$_twin_out" | sed -n 's/^HOST=//p' | tr '\n' ' ')
    sha_drift_measure "$TWIN_REPO" "$TWIN_HEADFULL" $_twin_hosts
    if [ "$SD_total" -eq 0 ]; then
      unk "twin fleet SHA" "$(basename "$_twin_file") yielded zero hosts — a list that names nobody is not 'all converged'"
    else
      sha_drift_verdict "twin fleet SHA" "${TWIN_HEADFULL:0:8} ($_twin_rb)" "$TWIN_REPO" " — deploy: scripts/fleet_pull.sh $TWIN_REPO"
    fi
  fi
fi

# 2b. Running code vs DEPLOYED code — DISCLOSURE, never a verdict.
#
# The SHA-drift leg above compares each box's git HEAD ON DISK. That is not the
# code any long-lived process is EXECUTING: `fleet_pull` is restart-free by
# design, so a box can read "converged" while its map/watchdog still run code
# from days ago. On 2026-08-09 the gate printed `fleet SHA drift PASS 8/8`
# while three unit classes fleet-wide were behind — true, and not the question
# anyone was actually asking.
#
# `server_class_skew` (fleet_truth) is the #79 detector for this class, but it
# is a PROXY: it infers "my code is older" from a peer reporting a signal class
# this server does not know, so it only fires when the newer code ADDS a class
# name. Most deploys change behavior without adding one. This leg measures the
# thing directly instead — unit start time vs the deployed HEAD's commit time.
#
# Units are ENUMERATED from what is installed and ACTIVE, never a hardcoded
# list: a unit added tomorrow is covered the day it ships (closed-enum hazard,
# honest_failure_modes #7), and a unit that is INACTIVE BY DESIGN is never
# judged at all — moc3 runs no map on purpose, and calling that "stale" would
# be the same lie as alarming on an accepted blind spot.
#
# 2026-08-09 review corrections, all four measured live:
#   * meshanchor-* units are judged against the MESHANCHOR repo's HEAD
#     (/opt/meshanchor), never MeshForge's — the old single-HT compare read
#     any MA unit restarted before the latest MF commit as falsely 'behind'.
#
# 2026-08-11: the SAME defect, in the THIRD repo the 08-09 pass did not look
# for. `meshforge-maps` runs /opt/meshforge-maps/venv/bin/python -m src.main —
# its own repo, which fleet_pull.sh does not even touch. But the dispatch knew
# only two repos and matched on the unit-NAME prefix, so `meshforge-maps*` fell
# through `*)` to MeshForge's HEAD and was judged against a repo it does not
# run. MeshForge commits constantly, so those units read "behind" forever no
# matter what their own repo did: on 2026-08-11 it reported maps(4d) for three
# units whose repo had had ZERO commits since they started — the units began
# FOUR MINUTES after their HEAD. A fix applied to one instance is not applied
# to the class (the same lesson the mini rollup sibling taught the same day):
# when a dispatch grows a special case, grep for every OTHER value that needs
# one. Display prefix is mm: so the operator can see WHICH repo is judging it.
#   * BOTH systemd scopes are enumerated. User-scope units (the #82 nomadnet
#     class) were invisible while the clean text claimed "every ACTIVE unit";
#     a box whose user manager is unreachable now DISCLOSES that blindness
#     instead of folding it into clean (honest_failure_modes #2).
#   * A non-numeric ActiveEnterTimestamp used to error inside a suppressed
#     `[ -lt ]` and silently count as CURRENT; it is now unknown.
#   * The display keeps a repo prefix (mf:/ma:) — stripping it meant the
#     operator could not tell which repo's unit was being judged.
#
# Three outcomes are kept DISTINCT, because collapsing them is this project's
# signature defect: `behind` (measured), `unknown` (active but no start time /
# no git — NOT "current"), and units simply absent (not counted).
#
# 2026-08-12: behind-on-CODE is separated from behind-on-PROSE. The compare was
# unit-start vs HEAD's commit time, unconditionally — so every documentation
# commit marked every active unit "behind" until it was restarted. Measured
# that day: three doc-only commits (.claude/, evals/, scripts/) put all nine
# watchdogs on the list, they were restarted to clear it, and the next docs:
# commit would have refilled it. A note that is near-permanently non-empty
# carries no signal, which is this file's own standing-noise defect wearing a
# NOTE's clothing.
#
# ⚠️ It LABELS, it does not FILTER. Nothing is dropped from the count, because
# no path class is provably inert for every unit: mini-dudeai's offline_oracle
# indexes `.claude/foundations|rules|research/*.md` AND `docs/*.md` as its
# corpus (default_roots()), so a "docs-only" commit can genuinely make a
# resident mini stale. Filtering those out would have been a real blindness
# sold as noise reduction. Both buckets print; only the HEADLINE changes.
#
# CODE = src/ + requirements/ + requirements.txt + templates/ + scripts/ —
# what a resident unit can load (modules, the dep floor, unit files, and
# script-hosted daemons). Two corrections from the same-day review
# (2026-08-12), both in the QUIETER direction the fail-safe below exists to
# forbid:
#   * scripts/ was excluded on the premise "exec'd fresh per invocation, no
#     resident unit can be stale on one" — false on both repos:
#     nomadnet-silence-watch-user.service is a Type=simple resident daemon
#     whose ExecStart IS scripts/nomadnet_silence_watch.py, and MeshAnchor
#     keeps its systemd unit files under scripts/ (meshanchor-daemon.service
#     et al). A code-stale resident watcher read "behind on prose only".
#   * git pathspec `requirements` matches only the DIRECTORY;
#     meshforge-maps pins its deps in a top-level requirements.txt (it has
#     no requirements/ dir), so its dep-floor bumps never moved the
#     code-head. `requirements.txt` is listed explicitly.
# The prose bucket is therefore docs/, .claude/, evals/ — the corpora only
# mini's oracle loads, never a resident interpreter.
#
# FAIL-SAFE, and this is the load-bearing line: if the code-head cannot be
# resolved for a repo (git error, path never touched, unreadable), it falls
# back to HEAD — i.e. exactly the pre-2026-08-12 behaviour, everything counted
# as code-behind. An unresolvable code-head must NEVER quietly move units into
# the benign bucket (honest_failure_modes #1: the degraded value must not
# overlap the healthy domain).
skew_desc=""; skew_behind=0; skew_unknown=0; skew_boxes=0; skew_udark=0
skew_prose=0; skew_prose_desc=""
skew_noattr=0; skew_noattr_desc=""
# The attribution program is a real file so it can be unit-tested directly
# (tests/test_honest_status_skew_attr.sh); ship it base64 so no quoting of an
# awk program has to survive this remote command string.
HS_HERE="$(cd "$(dirname "$0")" && pwd)"
HS_ATTR_F="$HS_HERE/hs_skew_attr.awk"
[ -r "$HS_ATTR_F" ] || { echo "honest_status: missing $HS_ATTR_F" >&2; exit 3; }
HS_ATTR_B64=$(base64 -w0 < "$HS_ATTR_F" 2>/dev/null || base64 < "$HS_ATTR_F" | tr -d "\n")
for b in $BOXES; do
  # A declared-dormant box is not asked (finding 24): this leg had no posture
  # check at all, so it paid a ConnectTimeout per switched-off box.
  fleet_posture_is_silent "$b" 2>/dev/null && continue
  raw=$(run_on "$b" "echo HSUP
# Newest commit touching CODE the repo's resident units load. Empty (git
# error / paths never touched) FALLS BACK to that repo's HEAD below, so an
# unresolvable code-head can never demote a unit into the prose bucket.
hs_codehead() { git -C \"\$1\" log -1 --format=%ct -- src requirements requirements.txt templates scripts 2>/dev/null; }
if [ -e $REPO/.git ]; then
  HTMF=\$(git -C $REPO show -s --format=%ct HEAD 2>/dev/null); [ -n \"\$HTMF\" ] || HTMF=SKIP
  HTMA=\$(git -C /opt/meshanchor show -s --format=%ct HEAD 2>/dev/null); [ -n \"\$HTMA\" ] || HTMA=SKIP
  HTMM=\$(git -C /opt/meshforge-maps show -s --format=%ct HEAD 2>/dev/null); [ -n \"\$HTMM\" ] || HTMM=SKIP
  HCMF=\$(hs_codehead $REPO); [ -n \"\$HCMF\" ] || HCMF=\$HTMF
  HCMA=\$(hs_codehead /opt/meshanchor); [ -n \"\$HCMA\" ] || HCMA=\$HTMA
  HCMM=\$(hs_codehead /opt/meshforge-maps); [ -n \"\$HCMM\" ] || HCMM=\$HTMM
  AWKF=\$(mktemp); printf %s '$HS_ATTR_B64' | base64 -d > \"\$AWKF\"
  HSV=\"-v HTMF=\$HTMF -v HCMF=\$HCMF -v HTMA=\$HTMA -v HCMA=\$HCMA -v HTMM=\$HTMM -v HCMM=\$HCMM\"
  # ATTRIBUTION BY LOADED CODE, NEVER BY UNIT NAME. Enumerate EVERY active
  # service in both scopes; hs_skew_attr.awk drops the ones whose process
  # loads no repo code and judges the rest against the repo they actually
  # load. See that file's header for the audit this replaced.
  XRD=/run/user/\$(id -u)
  if XDG_RUNTIME_DIR=\$XRD systemctl --user list-units --no-pager >/dev/null 2>&1; then USOK=1; else USOK=0; echo USCOPEDARK; fi
  SU=\$(systemctl list-units --type=service --state=active --no-legend --no-pager 2>/dev/null | awk '{print \$1}')
  [ -n \"\$SU\" ] && systemctl show -p Id -p ExecStart -p Environment -p WorkingDirectory -p ActiveEnterTimestamp --timestamp=unix \$SU 2>/dev/null | awk \$HSV -f \"\$AWKF\"
  if [ \"\$USOK\" = 1 ]; then
    UU=\$(XDG_RUNTIME_DIR=\$XRD systemctl --user list-units --type=service --state=active --no-legend --no-pager 2>/dev/null | awk '{print \$1}')
    [ -n \"\$UU\" ] && XDG_RUNTIME_DIR=\$XRD systemctl --user show -p Id -p ExecStart -p Environment -p WorkingDirectory -p ActiveEnterTimestamp --timestamp=unix \$UU 2>/dev/null | awk \$HSV -f \"\$AWKF\"
  fi
  rm -f \"\$AWKF\"
else echo HSNOREPO; fi")
  [ "$(printf '%s\n' "$raw" | sed -n '1p')" = "HSUP" ] || continue
  body=$(printf '%s\n' "$raw" | sed -n '2,$p')
  printf '%s\n' "$body" | grep -q HSNOREPO && continue
  skew_boxes=$((skew_boxes+1))
  printf '%s\n' "$body" | grep -q '^USCOPEDARK' && skew_udark=$((skew_udark+1))
  nb=$(printf '%s\n' "$body" | grep -c '^B ' || true)
  nu=$(printf '%s\n' "$body" | grep -c '^U ' || true)
  np=$(printf '%s\n' "$body" | grep -c '^P ' || true)
  nn=$(printf '%s\n' "$body" | grep -c '^N ' || true)
  skew_noattr=$((skew_noattr+nn))
  skew_behind=$((skew_behind+nb)); skew_unknown=$((skew_unknown+nu))
  skew_prose=$((skew_prose+np))
  # ONE formatter for both buckets — two awk copies would drift the display
  # the first time a fourth repo prefix lands (honest_failure_modes #5).
  # The repo tag now comes from field 2 (the repo the unit's process ACTUALLY
  # LOADS, resolved by hs_skew_attr.awk) instead of the unit's name prefix —
  # so meshanchor.service reads ma: and nomadnet-silence-watch reads mf:,
  # neither of which the name-prefix display could express (2026-09-02).
  _skew_units() {  # $1 = marker letter
    printf '%s\n' "$body" | awk -v m="$1" '$1==m{sub(/\.service$/,"",$3); if ($2=="/opt/meshforge-maps") { t="mm:"; sub(/^meshforge-maps/,"maps",$3) } else if ($2=="/opt/meshanchor") { t="ma:"; sub(/^meshanchor-/,"",$3) } else { t="mf:"; sub(/^meshforge-/,"",$3) } printf "%s%s(%sd),", t, $3, $4}' | sed 's/,$//'
  }
  [ "$nb" -gt 0 ] && skew_desc="$skew_desc $b:$(_skew_units B)"
  [ "$np" -gt 0 ] && skew_prose_desc="$skew_prose_desc $b:$(_skew_units P)"
  # N records are shaped "N <unit> <interp>", not "<marker> <repo> <unit> <days>"
  # — there IS no repo, which is the whole point — so they need their own
  # formatter rather than a fourth branch inside _skew_units.
  _noattr_units() {
    printf '%s\n' "$body" | awk '$1=="N"{sub(/\.service$/,"",$2); printf "%s,", $2}' | sed 's/,$//'
  }
  [ "$nn" -gt 0 ] && skew_noattr_desc="$skew_noattr_desc $b:$(_noattr_units)"
done
udark_note=""
[ "$skew_udark" -gt 0 ] && udark_note=" ; user scope unobservable on $skew_udark box(es) — those units are NOT covered"
# The prose bucket rides on EVERY outcome line below, including the clean one:
# "no unit is behind on code" while six are behind on a corpus mini indexes is
# a true sentence that must not be printed alone.
prose_note=""
[ "$skew_prose" -gt 0 ] && prose_note=" ; $skew_prose behind on NON-code only (docs/.claude/evals — still real for mini's oracle corpus)${skew_prose_desc}"
# Coverage disclosure, and it rides every outcome line for the same reason the
# prose bucket does: "every active unit is current" is only true of the units
# the leg can SEE. A venv interpreter that resolved to no repo may have that
# repo editable-installed into it, in which case the unit loads repo code the
# leg cannot attribute. Expected to be 0 on this fleet — it exists so the class
# announces itself rather than silently shrinking coverage.
noattr_note=""
[ "$skew_noattr" -gt 0 ] && noattr_note=" ; $skew_noattr unit(s) run a venv interpreter resolving to NO repo — an editable install there would be INVISIBLE to this leg${skew_noattr_desc}"
if [ "$skew_boxes" = 0 ]; then
  disc "running-code skew" "no box answered with a repo — not measured"
elif [ "$skew_behind" = 0 ] && [ "$skew_unknown" = 0 ]; then
  disc "running-code skew" "$skew_boxes box(es): every ACTIVE mf/ma unit (system+user scope) started at/after its own repo's newest CODE commit${prose_note}${noattr_note}${udark_note}"
else
  # ${var:+...} expands whenever the var is NON-EMPTY, and "0" is non-empty —
  # so the naive form printed "; 0 unknown(no start time)" on every clean run.
  # Caught by drilling the branches with synthetic counts, not by reading.
  unk_note=""
  [ "$skew_unknown" -gt 0 ] && unk_note=" ; $skew_unknown unknown(no start time / no repo for the unit — NOT 'current')"
  disc "running-code skew" "$skew_behind unit(s) behind their repo's newest CODE commit across $skew_boxes box(es)${skew_desc}${unk_note}${prose_note}${noattr_note}${udark_note} — disclosure, not a fault; they load it at next restart"
fi

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
  # The classification above used to live inline here. It now lives in
  # scripts/pytest_verdict.sh, because this gate was not its only consumer and
  # the cure had reached only this one: calibration_reverify.sh ran the SAME
  # full-suite invocation, trusted a bare `pyrc=$?`, and minted ledger verdicts
  # from it (2026-07-31 audit — 21 of 34 `held` verdicts came through that
  # path). Two consumers of one phenomenon share one classifier or they drift
  # (honest_failure_modes #5); this is the "derive" arm of that rule rather
  # than the weaker "test-pin" arm.
  #
  # Resolved as a SIBLING OF THIS SCRIPT, deliberately not "$REPO/scripts/...":
  # $REPO is overridable and the suite drives this gate against a FAKE repo
  # (tests/test_honest_status_suite_leg.sh), so a $REPO-relative path would
  # resolve to a tree that has no classifier in it. The classifier is part of
  # the harness, not of the tree under test.
  # Overridable ONLY so the degrade path is testable — pointing it at a missing
  # file is how the suite proves a vanished classifier reads UNKNOWN and not
  # PASS. A guard whose failure branch no test can reach is the fail-dark shape
  # this gate exists to refuse (MF027).
  _hs_verdict=$("${HS_PYTEST_VERDICT:-$(dirname "$0")/pytest_verdict.sh}" \
                  --log "$HS_TMP/pytest.log" --rc "$rc" 2>/dev/null)
  _hs_class=$(printf '%s' "$_hs_verdict" | cut -f1)
  _hs_why=$(printf '%s' "$_hs_verdict" | cut -f2-)
  if [ -z "$_hs_class" ]; then
    # The classifier itself did not run (missing, not executable, crashed).
    # Every swallow leaves a witness (#9), and an unobservable suite is UNKNOWN
    # — never a pass — no matter how clean pytest's own exit code looked.
    unk "full suite" "scripts/pytest_verdict.sh did not run — suite unclassified (pytest exit $rc)$(_hs_preserve)"
  else
    case "$_hs_class" in
      PASS) ok  "full suite" "$_hs_why" ;;
      FAIL) bad "full suite" "$_hs_why$(_hs_preserve)" ;;
      *)    unk "full suite" "$_hs_why$(_hs_preserve)" ;;
    esac
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
viol=""; checked=0; det=""; cr_unreach=0
for b in $BOXES; do
  # Liveness token, as the SHA leg has had since 2026-07-28: an empty answer
  # used to mean "no map served here" AND "the box did not answer", and the
  # leg printed PASS over a fleet with an unreachable box silently dropped
  # (§3 drill 2026-09-07). Down is UNKNOWN; up-with-no-map is nothing to check.
  if fleet_posture_is_silent "$b" 2>/dev/null; then det="$det $b:dormant"; continue; fi
  raw=$(run_on "$b" "echo HSUP; curl -s --max-time 8 http://localhost:5000/api/gateway/delivery 2>/dev/null")
  [ "$(printf '%s\n' "$raw" | sed -n '1p')" = "HSUP" ] || { cr_unreach=$((cr_unreach+1)); det="$det $b:unreach"; continue; }
  j=$(printf '%s\n' "$raw" | sed -n '2,$p')
  [ -z "$j" ] && continue   # up, no map served here — not a failure, just nothing to check
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
elif [ "$cr_unreach" -gt 0 ]; then unk "live conf_rate<=1.0" "$checked checked, $cr_unreach box(es) unreachable — not a fleet-wide assertion;$det"
elif [ "$checked" = 0 ]; then unk "live conf_rate<=1.0" "no box served /api/gateway/delivery"
elif [ "$FLEET_SSOT" = 0 ]; then
  # A violation found here is still real (the bad branch above stands), but
  # "no violation" over one box is not the fleet-wide assertion this leg names.
  unk "live conf_rate<=1.0" "$checked checked, THIS box only — no fleet_hosts SSOT, fleet unverified;$det"
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

wedge_t=0; deg_t=0; held_t=0; clean=0; unreach=0; nowd=0; wdfault=0; wddormant=0; sigdesc=""
wdmissing=0; nowdunk=0
btotal=$(echo $BOXES | wc -w)
# DECLARED watchdog state per box, resolved ONCE (one python call, not one per
# box). Until 2026-09-10 this leg inferred an absent watchdog from
# LoadState=not-found alone and dropped the box from the denominator — so
# lehua, whose role DECLARES `meshforge-watchdog: absent` with a written
# rationale, and a box that had simply LOST its watchdog read identically, and
# the benign reading won. A box losing its watchdog made the fleet look
# CLEANER: wdtotal shrank while clean/wdtotal stayed green.
# Unresolvable stays `unknown` and is NEVER collapsed into absent.
# HONEST_WD_DECL overrides the resolved table with a fixture — ONLY so this
# gate's own tests can drive the three branches below. Without it the
# `declared enabled but missing` branch could never be exercised, and a branch
# that has never fired is not evidence that it works.
if [ -n "${HONEST_WD_DECL:-}" ]; then
  WD_DECL="$HONEST_WD_DECL"
else
  WD_DECL=$(mktemp 2>/dev/null || echo /tmp/hs_wd_decl.$$)
  timeout 60 python3 "$REPO/scripts/role_declared_services.py" \
    --service meshforge-watchdog $BOXES >"$WD_DECL" 2>/dev/null || : >"$WD_DECL"
fi
wd_declared() {  # $1=box -> enabled|disabled|absent|unknown
  awk -v b="$1" -F'\t' '$1==b{print $2; f=1; exit} END{if(!f) print "unknown"}' "$WD_DECL"
}
for b in $BOXES; do
  # Fetch the box's OWN clock alongside its watchdog.json in ONE round-trip, so
  # the freshness age is computed same-clock — never this box's clock vs that
  # box's ts (cross-machine wall-clock is forgeable: honest_failure #6). The
  # unit ACTIVE state and LOAD state ride the SAME round-trip (2026-07-28) to
  # split what an empty answer used to conflate — see below. Each field is
  # forced to exactly one line so the positional parse cannot shear.
  # Posture first — a declared-dormant box is not sshed (finding 24).
  if fleet_posture_is_silent "$b" 2>/dev/null; then wddormant=$((wddormant+1)); sigdesc="$sigdesc $b:dormant"; continue; fi
  raw=$(run_on "$b" "date +%s 2>/dev/null; { systemctl is-active meshforge-watchdog.service 2>/dev/null || echo absent; } | head -1; { systemctl show meshforge-watchdog.service -p LoadState --value 2>/dev/null || echo unknown; } | head -1; echo '---WDSEP---'; cat $WD_PATH 2>/dev/null")
  rnow=$(printf '%s\n' "$raw" | sed -n '1p')
  wunit=$(printf '%s\n' "$raw" | sed -n '2p')
  wload=$(printf '%s\n' "$raw" | sed -n '3p')
  w=$(printf '%s\n' "$raw" | awk 'f{print} /^---WDSEP---$/{f=1}')
  # FOUR states, not one (widening the list to the whole fleet exposed the
  # conflation; the fourth split off 2026-07-28 review): the box is DOWN; the
  # box is up with NO watchdog unit installed (LoadState=not-found — a
  # MeshAnchor-only box, a legitimately-absent organ, excluded from the
  # denominator, never counted as blindness); the unit is INSTALLED but not
  # running (failed/crashlooping/stopped — a FAULT: the #82 class,
  # NRestarts=7842 undetected 10 days, read GREEN here because anything
  # non-"active" was excused as absent); or the watchdog is ACTIVE yet wrote
  # no state, which stays UNKNOWN-loud (honest_failure_modes #2). Only
  # LoadState distinguishes absent from broken — is-active prints "inactive"
  # for both a missing unit and a dead one.
  if [ -z "$rnow" ]; then unreach=$((unreach+1)); sigdesc="$sigdesc $b:unreach"; continue; fi
  if [ "$wload" = "loaded" ] && [ "$wunit" != "active" ]; then
    wdfault=$((wdfault+1)); sigdesc="$sigdesc $b:WATCHDOG-UNIT-$wunit"; continue
  fi
  if [ -z "$w" ] && [ "$wunit" != "active" ]; then
    # THREE outcomes, not one. Observed-absent is only half the fact; the other
    # half is whether the role DECLARED it absent. Collapsing them is how a
    # lost watchdog hid inside a legitimate exclusion.
    case "$(wd_declared "$b")" in
      absent|disabled)
        # Absent BY DESIGN. Excluded from the denominator, and labelled so the
        # next reader does not mistake it for a fault the way one did on
        # 2026-09-10 — the label is the fix as much as the branch is.
        nowd=$((nowd+1)); sigdesc="$sigdesc $b:no-watchdog(declared-absent,by-design)"; continue ;;
      enabled)
        # The role says this box RUNS a watchdog and no unit is installed.
        # That is provisioning drift — a fault, not an absent organ.
        wdmissing=$((wdmissing+1)); sigdesc="$sigdesc $b:WATCHDOG-MISSING(role declares enabled)"; continue ;;
      *)
        # No resolvable declaration. Treated exactly as before — excluded from
        # the denominator, the leg claims NOTHING about this box — but LABELLED
        # so the blind spot is visible instead of implied.
        #
        # Deliberately NOT escalated to a gate-level UNKNOWN. Role stamps are a
        # re-derived CACHE, and utils.fleet_naming's tri-state contract says the
        # safe reading of unknown is "keep watching", not "go red": a new or
        # unstamped box would otherwise turn the whole gate UNKNOWN over
        # something no operator can act on. The actionable half of this class —
        # declared ENABLED but missing — is caught by the branch above.
        nowdunk=$((nowdunk+1)); sigdesc="$sigdesc $b:no-watchdog($wunit,declaration-unknown)"; continue ;;
    esac
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
  # A snapshot with NO usable ts cannot prove its age either: it read
  # "1/1 clean" under the stale gate (§3 drill 2026-09-07) — the absent value
  # landing in the healthy domain (honest_failure_modes #1).
  if [ "$WD_STALE_S" -gt 0 ]; then
    if [ "$ts" = "NOTS" ]; then
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:no-ts(age unobservable)"; continue
    fi
    # The age was `awk "BEGIN{printf ..., $rnow - $ts}"` and an EMPTY result
    # silently SKIPPED the stale gate (finding 14a): a .bashrc echo landing in
    # the box's `date +%s` line made a days-old snapshot read clean. Both
    # operands are validated as integers; anything else is its own UNKNOWN
    # state, never a fresh-looking number.
    case "$rnow" in ''|*[!0-9]*)
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:clock-unreadable('${rnow:0:24}')"; continue ;;
    esac
    tsi=${ts%.*}
    case "$tsi" in ''|*[!0-9]*)
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:ts-unreadable('${ts:0:24}')"; continue ;;
    esac
    age=$((rnow - tsi))
    # A snapshot stamped in the box's own FUTURE is a stepped clock (RTC-less
    # Pi; honest_failure_modes #6) and cannot prove its age. 60s of grace: the
    # `date` runs BEFORE the `cat` in the same round-trip, so a tick landing
    # between them legitimately reads a second or two "young".
    if [ "$age" -lt -60 ]; then
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:future-stamped(${age#-}s — clock stepped)"; continue
    fi
    if [ "$age" -gt "$WD_STALE_S" ]; then
      unreach=$((unreach+1)); sigdesc="$sigdesc $b:stale(${age}s)"; continue
    fi
  fi
  held_t=$((held_t+${hd:-0}))
  if [ "${wg:-0}" = 0 ] && [ "${dg:-0}" = 0 ] && [ "${hd:-0}" = 0 ]; then clean=$((clean+1))
  else sigdesc="$sigdesc $b:[$cl]"; wedge_t=$((wedge_t+wg)); deg_t=$((deg_t+dg)); fi
done
# Order is deliberate: proven-bad outranks unobservable outranks WARN. A held
# signal is last-known evidence from a BLIND observer — UNKNOWN by this
# project's tiering — and UNKNOWN outranks WARN (finding 12, 2026-09-09):
# `deg_t` used to be judged before `held_t`, so a held-blind box beside any
# other box's live degraded signal yielded WARN / exit 0, while the held
# signal ALONE yielded UNKNOWN / exit 2. Adding a fault made the gate greener.
# A held signal never reaches WARN/FAIL on its own; a live degraded signal
# rides along in the UNKNOWN line so it is not hidden either.
# Declared-absent and declaration-unknown both leave the denominator: neither
# can be judged clean. A box whose role declares the watchdog ENABLED stays IN
# (wdmissing), because a missing-but-required watchdog is a fault to report,
# not a box to quietly stop counting.
wdtotal=$((btotal - nowd - nowdunk - wddormant))
if [ "$wedge_t" -gt 0 ]; then bad "watchdog (wedge)" "$wedge_t WEDGE + $deg_t degraded across fleet:$sigdesc"
elif [ "$wdfault" -gt 0 ]; then bad "watchdog (unit down)" "$wdfault box(es) with the watchdog unit installed but not running — a dead watchdog is a fault, not an absent organ:$sigdesc"
elif [ "$wdmissing" -gt 0 ]; then bad "watchdog (missing)" "$wdmissing box(es) whose ROLE declares meshforge-watchdog enabled but no unit is installed — provisioning drift, not an absent organ:$sigdesc"
elif [ "$unreach" -gt 0 ]; then unk "watchdog signals" "$clean/$wdtotal clean, $unreach unreachable/stale:$sigdesc"
elif [ "$held_t" -gt 0 ]; then unk "watchdog signals" "$held_t held-blind (last-known, observer cannot see) beside $deg_t live degraded, 0 wedge:$sigdesc"
elif [ "$deg_t" -gt 0 ]; then warnf "watchdog (degraded)" "$deg_t degraded, 0 wedge:$sigdesc"
elif [ "$wdtotal" = 0 ]; then unk "watchdog signals" "no box ran a watchdog:$sigdesc"
elif [ "$FLEET_SSOT" = 0 ]; then
  # Clean on the one box we could enumerate. A real signal here still outranks
  # this (the branches above run first) — but silence across a fleet we cannot
  # even list is unobservable, and unobservable is never a pass.
  unk "watchdog signals" "$clean/$wdtotal clean on THIS box only — no fleet_hosts SSOT, fleet not observed${sigdesc:+;$sigdesc}"
else ok "watchdog signals" "$clean/$wdtotal clean, 0 signals${sigdesc:+;$sigdesc}"; fi

# --- dependency findings -----------------------------------------------------
# Reads the two dependency checks' artifacts. Added 2026-09-05 because both were
# WRITERS WITH NO READER: dep_advisory_check.py shipped 09-04 and dep_range_check.py
# 09-05, each dutifully writing a finding file that nothing consumed — no mini
# rule, no probe, no gate leg. A finding only a human who remembers to `cat` it
# will ever see is the half-wired detector shape (honest_failure_modes #4), and
# it is how the cryptography pin stayed invisible for six months in the first
# place. One leg, both artifacts.
#
# The two answer DIFFERENT halves and neither substitutes for the other:
#   ~/.meshforge-dep-ADVISORY      — what the fleet has INSTALLED (the stock)
#   ~/.meshforge-dep-RANGE-FINDING — what the manifests DECLARE (the inflow)
#
# Findings are a WARN, not a FAIL, for the same reason the watchdog leg is: they
# are a real fleet condition, not evidence this repo's code is broken. --strict
# promotes them. But a MISSING or STALE status file is UNKNOWN, never a pass —
# "no finding file" and "the check has not run in days" look identical on disk,
# and collapsing them would let a dead timer read as safety. Both timers are
# daily, so 48h is two missed windows.
dep_stale_h=48
dep_note=""; dep_state="ok"
for pair in "advisories:.meshforge-dep-advisories:.meshforge-dep-ADVISORY:installed" \
            "ranges:.meshforge-dep-ranges:.meshforge-dep-RANGE-FINDING:declared"; do
  IFS=: read -r dlabel dstatus dfind dwhat <<<"$pair"
  spath="$HOME/$dstatus"; fpath="$HOME/$dfind"
  if [ ! -f "$spath" ]; then
    dep_note="$dep_note ${dwhat}:never-ran"; dep_state="unk"; continue
  fi
  # `stat … || echo 0` was the epoch-0 sentinel persistent_issues names
  # (finding 14b): an unreadable status file read as 56 years STALE — a true
  # UNKNOWN wearing a stale number. Tri-state: unreadable is its own state,
  # a future mtime is a stepped clock (honest_failure_modes #6), never fresh.
  if ! _dep_mt=$(stat -c %Y "$spath" 2>/dev/null) || [ -z "$_dep_mt" ]; then
    dep_note="$dep_note ${dwhat}:mtime-unreadable"; dep_state="unk"; continue
  fi
  age_h=$(( ( $(date +%s) - _dep_mt ) / 3600 ))
  if [ "$age_h" -lt 0 ]; then
    dep_note="$dep_note ${dwhat}:FUTURE-STAMPED(${age_h#-}h — clock stepped)"; dep_state="unk"; continue
  fi
  if [ "$age_h" -gt "$dep_stale_h" ]; then
    dep_note="$dep_note ${dwhat}:STALE(${age_h}h)"; dep_state="unk"; continue
  fi
  # The installed-stock record must SAY it is a fleet run. 2026-09-06: a
  # `--host X` spot-check overwrote the ten-box file twice in one session and
  # this leg reported 4, then 7 findings while the fleet number was 22. The
  # script now writes narrow runs to `.scoped` siblings and stamps the
  # canonical pair `# scope: fleet`; a file without the stamp is a narrow view,
  # an older script, or a hand copy — none of which is the fleet's answer.
  if [ "$dwhat" = installed ] && ! grep -q '^# scope: fleet' "$spath" 2>/dev/null; then
    dep_note="$dep_note ${dwhat}:NOT-A-FLEET-RUN(no scope stamp)"; dep_state="unk"; continue
  fi
  # A status file whose body opens with UNKNOWN (gh unauthenticated, host list
  # unreadable) is a run that never looked; the finding file beside it is a
  # leftover from the last run that did, not this one's verdict.
  if grep -v '^#' "$spath" 2>/dev/null | head -1 | grep -q '^UNKNOWN'; then
    dep_note="$dep_note ${dwhat}:$(grep -v '^#' "$spath" | head -1 | cut -c1-60)"; dep_state="unk"; continue
  fi
  if [ -f "$fpath" ]; then
    # `grep -c … || echo 0` yielded the TWO-line value $'0\n0' on a file with
    # no findings (grep -c prints its 0 AND exits 1), and any finding file at
    # all flipped the leg to WARN (finding 14c). Count non-blank,
    # non-comment lines; an unreadable file is UNKNOWN; zero findings is OK.
    n=$(grep -cvE '^[[:space:]]*(#|$)' "$fpath" 2>/dev/null); _grc=$?
    if [ "$_grc" -gt 1 ] || [ -z "$n" ]; then
      dep_note="$dep_note ${dwhat}:finding-file-unreadable"; dep_state="unk"; continue
    fi
    if [ "$n" -gt 0 ]; then
      dep_note="$dep_note ${dwhat}:${n}_finding(s)"
      [ "$dep_state" != "unk" ] && dep_state="warn"
    else
      dep_note="$dep_note ${dwhat}:clean(${age_h}h, finding file empty)"
    fi
  else
    dep_note="$dep_note ${dwhat}:clean(${age_h}h)"
  fi
done
case "$dep_state" in
  unk)  unk   "dependency findings" "could not confirm both halves —$dep_note (a check that stopped running is not a clean bill)" ;;
  warn) warnf "dependency findings" "$dep_note — see ~/.meshforge-dep-ADVISORY / ~/.meshforge-dep-RANGE-FINDING" ;;
  *)    ok    "dependency findings" "$dep_note" ;;
esac

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
#
# Path, shape and tree predicate come from mini_dudeai.calibration_ledger —
# the ONE contract the claim-gate and warm-start read (finding 11/18,
# 2026-09-09). The path was typed here as ${HOME:-/tmp}/… while both readers
# used expanduser('~'): under an unset HOME the writer and its readers
# diverged. The marker dict was hand-typed here AND in calibration_reverify.sh,
# and the reverify copy lacked the flags this one had. The package is resolved
# beside THIS script (like fleet_posture.sh), never via $REPO: the suite drives
# this gate against a FAKE repo with no src/.
#
# Scope fingerprint (§3 drill 2026-09-07): a run narrowed by HONEST_BOXES (or
# with no fleet SSOT) wrote a marker indistinguishable from a fleet run.
# claim_gate refuses a marker carrying scope_narrowed or dirty_tree; the tree
# flag is computed by the shared predicate (untracked counts; git failure =
# dirty, the refusing direction).
HV_NARROW=0; { [ -n "${HONEST_BOXES:-}" ] || [ "$FLEET_SSOT" = 0 ]; } && HV_NARROW=1
HS_SRC="$(cd "$HS_HERE/../src" 2>/dev/null && pwd)"
VERDICT_PATH="$(PYTHONPATH="$HS_SRC" python3 - <<'PY' 2>/dev/null
from mini_dudeai.calibration_ledger import verdict_marker_path
print(verdict_marker_path())
PY
)"
if [ -z "$VERDICT_PATH" ]; then
  echo "honest_status: WARN — could not resolve the verdict marker path (mini_dudeai" \
       "unimportable from $HS_SRC); no marker written, claim-gate will treat this HEAD as unverified" >&2
elif ! HV_RC="$verdict_rc" HV_MSG="$verdict_msg" HV_HEAD="$HEADFULL" \
     HV_FULL="$RUN_TESTS" HV_STRICT="$STRICT" HV_PATH="$VERDICT_PATH" \
     HV_NARROW="$HV_NARROW" HV_BOXES="$BOXES" HV_REPO="$REPO" \
     PYTHONPATH="$HS_SRC" python3 - <<'PY' 2>/dev/null
import os
from mini_dudeai import calibration_ledger as cl
env = os.environ
m = cl.build_marker(
    env.get("HV_HEAD", ""), int(env.get("HV_RC", "2") or 2),
    instrument="honest_status", summary=env.get("HV_MSG", ""),
    ran_full_suite=env.get("HV_FULL") == "1",
    scope_narrowed=env.get("HV_NARROW") == "1",
    dirty_tree=cl.tree_is_dirty(env["HV_REPO"]),
    strict=env.get("HV_STRICT") == "1", boxes=env.get("HV_BOXES", ""))
cl.write_marker(env["HV_PATH"], m)
PY
then
  echo "honest_status: WARN — could not write verdict marker $VERDICT_PATH" \
       "(claim-gate will treat this HEAD as unverified)" >&2
fi

exit "$verdict_rc"
