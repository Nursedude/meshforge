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
for b in $PEERS; do
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
  #
  # "No repo" is proven by the .git path, NOT by empty git output (2026-07-28
  # review): a box that carries the repo but whose git errors (dubious
  # ownership over ssh, git not installed, corrupt .git) also prints nothing,
  # and counting it norepo silently dropped it from the denominator — a PASS
  # that never verified that box, the same conflation class one door over.
  # A repo-present git failure stays in the denominator as unverified.
  raw=$(run_on "$b" "echo HSUP; if [ -e $REPO/.git ]; then git -C $REPO rev-parse HEAD 2>/dev/null || echo HSGITERR; else echo HSNOREPO; fi")
  up=$(printf '%s\n' "$raw" | sed -n '1p')
  s=$(printf '%s\n' "$raw" | sed -n '2p')
  if [ "$up" != "HSUP" ]; then desc="$desc $b:unreach"; continue; fi
  case "$s" in
    HSNOREPO) norepo=$((norepo+1)); desc="$desc $b:no-repo"; continue ;;
    HSGITERR|"") desc="$desc $b:git-error(repo present)"; continue ;;
  esac
  reached=$((reached+1))
  if [ "$s" = "$HEADFULL" ]; then matched=$((matched+1)); else desc="$desc $b:${s:0:7}"; fi
done
drifted=$((reached - matched))
expect=$((total - norepo))
if [ -z "$PEERS" ]; then
  # Nothing external to compare against. Self is excluded by construction, so
  # there is no evidence here at all — not "converged", UNKNOWN.
  unk "fleet SHA drift" "no peer box to compare against — self cannot drift from itself ($BOXES_SRC)"
elif [ "$drifted" -gt 0 ]; then bad "fleet SHA drift" "$matched/$expect @ $HEAD;$desc"
elif [ "$reached" -lt "$expect" ]; then unk "fleet SHA drift" "$matched/$reached reachable of $expect @ $HEAD;$desc"
elif [ "$reached" = 0 ]; then unk "fleet SHA drift" "no box carried $REPO;$desc"
else ok "fleet SHA drift" "$matched/$expect @ $HEAD${desc:+;$desc}"; fi

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
for b in $BOXES; do
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
  XRD=/run/user/\$(id -u)
  if XDG_RUNTIME_DIR=\$XRD systemctl --user list-units --no-pager >/dev/null 2>&1; then USOK=1; else USOK=0; echo USCOPEDARK; fi
  { systemctl list-units --type=service --state=active --no-legend --no-pager 2>/dev/null | awk '{print \$1}' | sed 's/^/sys /'
    [ \"\$USOK\" = 1 ] && XDG_RUNTIME_DIR=\$XRD systemctl --user list-units --type=service --state=active --no-legend --no-pager 2>/dev/null | awk '{print \$1}' | sed 's/^/usr /'
  } | grep -E '^(sys|usr) (meshforge|meshanchor)-' | while read -r sc u; do
    case \"\$u\" in
      meshforge-maps.service|meshforge-maps@*) H=\$HTMM; HC=\$HCMM;;
      meshanchor-*) H=\$HTMA; HC=\$HCMA;;
      *) H=\$HTMF; HC=\$HCMF;;
    esac
    # Belt and braces: a code-head that is empty or non-numeric for ANY reason
    # collapses to this repo's HEAD, never to 'no code changes'.
    case \"\$HC\" in ''|*[!0-9]*) HC=\$H;; esac
    if [ \"\$sc\" = usr ]; then T=\$(XDG_RUNTIME_DIR=\$XRD systemctl --user show \"\$u\" -p ActiveEnterTimestamp --value --timestamp=unix 2>/dev/null | tr -d '@')
    else T=\$(systemctl show \"\$u\" -p ActiveEnterTimestamp --value --timestamp=unix 2>/dev/null | tr -d '@'); fi
    case \"\$T\" in ''|*[!0-9]*) echo \"U \$u\"; continue;; esac
    if [ \"\$H\" = SKIP ]; then echo \"U \$u\"
    elif [ \"\$T\" -lt \"\$HC\" ]; then echo \"B \$u \$(( (\$HC - \$T) / 86400 ))\"
    elif [ \"\$T\" -lt \"\$H\" ]; then echo \"P \$u \$(( (\$H - \$T) / 86400 ))\"; fi
  done
else echo HSNOREPO; fi")
  [ "$(printf '%s\n' "$raw" | sed -n '1p')" = "HSUP" ] || continue
  body=$(printf '%s\n' "$raw" | sed -n '2,$p')
  printf '%s\n' "$body" | grep -q HSNOREPO && continue
  skew_boxes=$((skew_boxes+1))
  printf '%s\n' "$body" | grep -q '^USCOPEDARK' && skew_udark=$((skew_udark+1))
  nb=$(printf '%s\n' "$body" | grep -c '^B ' || true)
  nu=$(printf '%s\n' "$body" | grep -c '^U ' || true)
  np=$(printf '%s\n' "$body" | grep -c '^P ' || true)
  skew_behind=$((skew_behind+nb)); skew_unknown=$((skew_unknown+nu))
  skew_prose=$((skew_prose+np))
  # ONE formatter for both buckets — two awk copies would drift the display
  # the first time a fourth repo prefix lands (honest_failure_modes #5).
  _skew_units() {  # $1 = marker letter
    printf '%s\n' "$body" | awk -v m="$1" '$1==m{sub(/\.service$/,"",$2); if ($2 ~ /^meshforge-maps($|@)/) sub(/^meshforge-maps/,"mm:maps",$2); else { sub(/^meshforge-/,"mf:",$2); sub(/^meshanchor-/,"ma:",$2) } printf "%s(%sd),", $2, $3}' | sed 's/,$//'
  }
  [ "$nb" -gt 0 ] && skew_desc="$skew_desc $b:$(_skew_units B)"
  [ "$np" -gt 0 ] && skew_prose_desc="$skew_prose_desc $b:$(_skew_units P)"
done
udark_note=""
[ "$skew_udark" -gt 0 ] && udark_note=" ; user scope unobservable on $skew_udark box(es) — those units are NOT covered"
# The prose bucket rides on EVERY outcome line below, including the clean one:
# "no unit is behind on code" while six are behind on a corpus mini indexes is
# a true sentence that must not be printed alone.
prose_note=""
[ "$skew_prose" -gt 0 ] && prose_note=" ; $skew_prose behind on NON-code only (docs/.claude/evals — still real for mini's oracle corpus)${skew_prose_desc}"
if [ "$skew_boxes" = 0 ]; then
  disc "running-code skew" "no box answered with a repo — not measured"
elif [ "$skew_behind" = 0 ] && [ "$skew_unknown" = 0 ]; then
  disc "running-code skew" "$skew_boxes box(es): every ACTIVE mf/ma unit (system+user scope) started at/after its own repo's newest CODE commit${prose_note}${udark_note}"
else
  # ${var:+...} expands whenever the var is NON-EMPTY, and "0" is non-empty —
  # so the naive form printed "; 0 unknown(no start time)" on every clean run.
  # Caught by drilling the branches with synthetic counts, not by reading.
  unk_note=""
  [ "$skew_unknown" -gt 0 ] && unk_note=" ; $skew_unknown unknown(no start time / no repo for the unit — NOT 'current')"
  disc "running-code skew" "$skew_behind unit(s) behind their repo's newest CODE commit across $skew_boxes box(es)${skew_desc}${unk_note}${prose_note}${udark_note} — disclosure, not a fault; they load it at next restart"
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

wedge_t=0; deg_t=0; held_t=0; clean=0; unreach=0; nowd=0; wdfault=0; sigdesc=""
btotal=$(echo $BOXES | wc -w)
for b in $BOXES; do
  # Fetch the box's OWN clock alongside its watchdog.json in ONE round-trip, so
  # the freshness age is computed same-clock — never this box's clock vs that
  # box's ts (cross-machine wall-clock is forgeable: honest_failure #6). The
  # unit ACTIVE state and LOAD state ride the SAME round-trip (2026-07-28) to
  # split what an empty answer used to conflate — see below. Each field is
  # forced to exactly one line so the positional parse cannot shear.
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
wdtotal=$((btotal - nowd))   # boxes that actually carry a watchdog unit
if [ "$wedge_t" -gt 0 ]; then bad "watchdog (wedge)" "$wedge_t WEDGE + $deg_t degraded across fleet:$sigdesc"
elif [ "$wdfault" -gt 0 ]; then bad "watchdog (unit down)" "$wdfault box(es) with the watchdog unit installed but not running — a dead watchdog is a fault, not an absent organ:$sigdesc"
elif [ "$unreach" -gt 0 ]; then unk "watchdog signals" "$clean/$wdtotal clean, $unreach unreachable/stale:$sigdesc"
elif [ "$deg_t" -gt 0 ]; then warnf "watchdog (degraded)" "$deg_t degraded, 0 wedge:$sigdesc"
elif [ "$held_t" -gt 0 ]; then unk "watchdog signals" "$held_t held-blind (last-known, observer cannot see), 0 observed:$sigdesc"
elif [ "$wdtotal" = 0 ]; then unk "watchdog signals" "no box ran a watchdog:$sigdesc"
elif [ "$FLEET_SSOT" = 0 ]; then
  # Clean on the one box we could enumerate. A real signal here still outranks
  # this (the branches above run first) — but silence across a fleet we cannot
  # even list is unobservable, and unobservable is never a pass.
  unk "watchdog signals" "$clean/$wdtotal clean on THIS box only — no fleet_hosts SSOT, fleet not observed${sigdesc:+;$sigdesc}"
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
    # Producer name, separate from the message: rederive_open attributes the
    # verdict to `instrument`; `summary` is display text (2026-07-31, f8).
    "instrument": "honest_status",
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
