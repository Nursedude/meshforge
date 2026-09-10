#!/usr/bin/env bash
# Behavior test for honest_status.sh's FLEET BOX LIST derivation (2026-07-28).
#
# WHY: the list was a second hardcode ("moc moc1 moc2 moc3 moc5"), independent
# of the fleet_hosts SSOT that fleet_pull.sh and fleet_dup_collector read. It
# drifted exactly as honest_failure_modes #5 predicts — the file grew to 8
# boxes, this stayed at 5 — and the gate printed "fleet SHA drift PASS 5/5",
# which READS as whole-fleet coverage while 3 boxes were never checked.
#
# Drives the REAL script with a stub ssh so each box's answer is scriptable,
# and asserts the derivation + the dispositions that make widening safe.
set -u
# Pin ambient state (2026-09-07): a caller that exported an override — a full
# honest_status run under HONEST_BOXES drives this very suite — made the
# SSOT cases here fail, and an exported marker path was overwritten mid-suite.
unset HONEST_BOXES MESHFORGE_FLEET_HOSTS HONEST_VERDICT_PATH HONEST_WD_PATH HONEST_WD_STALE_S
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"

# Fake repo (see test_honest_status_preserve.sh — the real repo's venv python
# is an absolute path that routes around the PATH stub).
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then echo "1 passed in 0.10s"; exit 0; fi
done
exec "$REAL_PYTHON3" "$@"
EOF

# Stub ssh: the box name selects a canned personality. The command string is
# the last arg; we only need to know which leg is asking.
# The WDSEP answer shape is: <epoch> <ActiveState> <LoadState> ---WDSEP--- [json].
# LoadState is what splits "no unit installed" (not-found → excluded organ)
# from "unit installed but dead" (loaded + not active → FAULT); see the
# box-wdfail personality and section 11.
cat > "$SB/ssh" <<'EOF'
#!/usr/bin/env bash
box=""; for a in "$@"; do case "$a" in -*|*=*) ;; *) box="$a"; break;; esac; done
cmd="${!#}"
# The conf_rate leg carries a liveness token since 2026-09-07 (echo HSUP; curl).
# Every UP personality answers it; box-down stays unreachable for it too.
case "$box" in box-down) exit 255 ;; esac
case "$cmd" in *curl*) echo "HSUP"; [ -n "${FAKE_CURL_JSON:-}" ] && printf '%s' "$FAKE_CURL_JSON"; exit 0 ;; esac
case "$box" in
  box-down) exit 255 ;;                       # unreachable
  box-norepo)                                  # up; no repo, no watchdog UNIT
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "HSNOREPO" ;;
      *WDSEP*) echo "1700000000"; echo "inactive"; echo "not-found"; echo "---WDSEP---" ;;
    esac ;;
  box-giterr)                                  # repo PRESENT, git itself errors
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "HSGITERR" ;;
      *WDSEP*) echo "1700000000"; echo "inactive"; echo "not-found"; echo "---WDSEP---" ;;
    esac ;;
  box-good)
    case "$cmd" in
      # The SHA leg asks per REPO: the twin leg's command names the sister
      # repo, and this box answers with THAT repo's head (finding 13 test).
      *rev-parse*) echo "HSUP"
                   if [ -n "${FAKE_TWIN_REPO:-}" ] && [[ "$cmd" == *"$FAKE_TWIN_REPO"* ]]; then echo "$FAKE_TWIN_HEAD"; else echo "$FAKE_HEAD"; fi ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(date +%s), \"signals\": []}" ;;
    esac ;;
  box-degraded)                                # ONE live degraded signal, nothing held
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(date +%s), \"signals\": [{\"class\": \"service_inactive\", \"severity\": \"degraded\", \"extra\": {}}]}" ;;
    esac ;;
  box-noisyclock)                              # a .bashrc echo pollutes the `date +%s` line; snapshot is DAYS old
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "welcome to the box"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(( $(date +%s) - 200000 )), \"signals\": []}" ;;
    esac ;;
  box-future)                                  # snapshot stamped a day in the box's OWN future
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(( $(date +%s) + 86400 )), \"signals\": []}" ;;
    esac ;;
  box-mute)                                    # watchdog ACTIVE but no state
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---" ;;
    esac ;;
  box-wdfail)                                  # unit INSTALLED but not running
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "failed"; echo "loaded"; echo "---WDSEP---" ;;
    esac ;;
  box-heldonly)                                # ONLY a held (observer-blind) signal
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(date +%s), \"signals\": [{\"class\": \"kernel_reboot_pending\", \"severity\": \"degraded\", \"extra\": {\"unobserved_hold\": true}}]}" ;;
    esac ;;
  box-mixhold)                                 # one LIVE degraded + one held
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(date +%s), \"signals\": [{\"class\": \"service_inactive\", \"severity\": \"degraded\", \"extra\": {}}, {\"class\": \"kernel_reboot_pending\", \"severity\": \"degraded\", \"extra\": {\"unobserved_hold\": true}}]}" ;;
    esac ;;
esac
exit 0
EOF
printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/gh"
# curl: silent by default (no map served), scriptable via FAKE_CURL_JSON so the
# live-conf_rate leg can be driven to a real verdict instead of "nothing to check".
cat > "$SB/curl" <<'EOF'
#!/usr/bin/env bash
[ -n "${FAKE_CURL_JSON:-}" ] && { printf '%s' "$FAKE_CURL_JSON"; exit 0; }
exit 1
EOF
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME/.config/meshforge"

run() {  # env: HONEST_BOXES / MESHFORGE_FLEET_HOSTS as needed
  # The twin repo is pinned to a nonexistent path unless a case sets it — the
  # box running this suite may carry a real /opt/meshanchor, and ambient
  # state must not reach a verdict (feedback_tests_must_pin_ambient_state).
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" \
    MESHFORGE_REPO="$FAKE_REPO" FAKE_HEAD="$FAKE_HEAD" \
    FAKE_CURL_JSON="${FAKE_CURL_JSON:-}" HONEST_WD_PATH="${HONEST_WD_PATH:-}" \
    HONEST_TWIN_REPO="${HONEST_TWIN_REPO:-$TMP/no-twin}" \
    FAKE_TWIN_REPO="${FAKE_TWIN_REPO:-}" FAKE_TWIN_HEAD="${FAKE_TWIN_HEAD:-}" \
    bash "$SCRIPT" --quick "$@" 2>&1
}

# The fake repo is a REAL git repo with one commit, so `git rev-parse HEAD`
# answers a real sha both for the gate's own HEAD and for the SELF leg. That
# matters: with a repo-less fake repo self fell out through the "no-repo" path,
# which HID the fact that self is compared against itself (2026-07-28).
git -C "$FAKE_REPO" init -q 2>/dev/null
# The fixture files are COMMITTED (2026-09-09): the marker's dirty_tree flag
# now comes from the shared git-status predicate, and an empty commit beside
# untracked fixtures would read every run as dirty — the plant below could
# then never distinguish clean from dirty.
printf '__pycache__/\n.pytest_cache/\n' > "$FAKE_REPO/.gitignore"
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q -m init 2>/dev/null
FAKE_HEAD="$(git -C "$FAKE_REPO" rev-parse HEAD 2>/dev/null)"; export FAKE_HEAD

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── 1. the list comes from the fleet_hosts SSOT, not a hardcode ──────────
hosts="$TMP/fleet_hosts"
printf '# comment\nbox-good\n\nbox-norepo\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" run)"
check "provenance line names the SSOT file" \
  "$(echo "$out" | grep -q "source: $hosts" && echo ok)"
check "list = file hosts + self (3 boxes, not a hardcoded 5)" \
  "$(echo "$out" | grep -q 'fleet legs cover 3 box(es)' && echo ok)"
check "no box from the retired hardcode appears" \
  "$(echo "$out" | grep -q 'moc1' && echo '' || echo ok)"

# ── 2. missing SSOT narrows LOUDLY, it does not invent a fleet ───────────
out="$(MESHFORGE_FLEET_HOSTS="$TMP/nope" run)"
check "absent fleet_hosts says SELF ONLY out loud" \
  "$(echo "$out" | grep -q 'SELF ONLY' && echo ok)"

# ── 3. up-but-no-repo is its own state, not 'unreachable' ────────────────
printf 'box-good\nbox-norepo\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_BOXES="box-good box-norepo" run)"
check "no-repo box reported as no-repo, not unreach" \
  "$(echo "$out" | grep -q 'box-norepo:no-repo' && echo ok)"
check "no-repo box excluded from the drift denominator (1/1, not 1/2)" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q '1/1' && echo ok)"

# ── 4. a DOWN box is UNKNOWN — absence of evidence is not convergence ────
out="$(HONEST_BOXES="box-good box-down" run)"
check "down box keeps the drift leg UNKNOWN" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN\|reachable' && echo ok)"

# ── 5. watchdog: absent organ vs ACTIVE-but-silent are DIFFERENT ─────────
out="$(HONEST_BOXES="box-good box-norepo" run)"
check "box running no watchdog is excluded, not counted blind" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q '1/1 clean' && echo ok)"
check "and it is still named in the detail" \
  "$(echo "$out" | grep -q 'box-norepo:no-watchdog' && echo ok)"

# ── 5b. absent-by-DESIGN vs absent-by-ACCIDENT (2026-09-10) ──────────────
# Observed absence is only half the fact. Until this split, a box that had LOST
# its watchdog was excluded from the denominator exactly like lehua, whose role
# DECLARES `meshforge-watchdog: absent` with a written rationale — so losing a
# watchdog made the fleet look CLEANER (wdtotal shrank, clean/wdtotal stayed
# green). honest_failure_modes #1, and the reason `inert` and `broken` may
# never share a rendering.
DECL="$TMP/wd_decl"

printf 'box-good\tenabled\nbox-norepo\tabsent\n' > "$DECL"
out="$(HONEST_BOXES="box-good box-norepo" HONEST_WD_DECL="$DECL" run)"
check "declared-absent watchdog stays excluded and the leg is clean" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q '1/1 clean' && echo ok)"
check "and it is labelled by-design, not left to be misread as a fault" \
  "$(echo "$out" | grep -q 'box-norepo:no-watchdog(declared-absent,by-design)' && echo ok)"

# The branch that did not exist before: the role says this box RUNS one.
printf 'box-good\tenabled\nbox-norepo\tenabled\n' > "$DECL"
out="$(HONEST_BOXES="box-good box-norepo" HONEST_WD_DECL="$DECL" run)"
check "role declares watchdog ENABLED but none installed → FAIL, not excluded" \
  "$(echo "$out" | grep -E 'watchdog' | grep -q 'FAIL' && echo ok)"
check "and it names the box as provisioning drift" \
  "$(echo "$out" | grep -q 'box-norepo:WATCHDOG-MISSING' && echo ok)"

# Unresolvable declaration must NOT go red: role stamps are a re-derived cache,
# and a new/unstamped box turning the whole gate UNKNOWN would cry wolf over
# something no operator can act on. Excluded as before — but LABELLED.
printf 'box-good\tenabled\n' > "$DECL"
out="$(HONEST_BOXES="box-good box-norepo" HONEST_WD_DECL="$DECL" run)"
check "unknown declaration is excluded as before, never a new alarm" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q '1/1 clean' && echo ok)"
check "and the blind spot is visible in the label" \
  "$(echo "$out" | grep -q 'box-norepo:no-watchdog(.*declaration-unknown)' && echo ok)"

out="$(HONEST_BOXES="box-good box-mute" run)"
check "watchdog ACTIVE but no state is UNKNOWN-loud, never excused" \
  "$(echo "$out" | grep -q 'ACTIVE-but-no-state' && echo ok)"

# ── 6. self is NOT evidence in the SHA-drift leg ─────────────────────────
#
# The drift leg asks each box for `git -C $REPO rev-parse HEAD` and compares it
# to $HEADFULL — which the gate derived from that same local repo. For SELF
# that comparison is a tautology: it cannot fail, ever. Counting it inflates
# BOTH sides of the ratio, so the number that is supposed to express external
# convergence is padded with one guaranteed match (2026-07-28 review).
#
# Self stays in the OTHER fleet legs (watchdog, conf_rate) — those read real
# local state and were genuinely unrepresented before. Only this leg is vacuous.
printf 'box-good\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" run)"
check "self still covered by the fleet legs (2 boxes)" \
  "$(echo "$out" | grep -q 'fleet legs cover 2 box(es)' && echo ok)"
check "drift leg counts PEERS only — self cannot drift from itself (1/1, not 2/2)" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q '1/1' && echo ok)"

# ── 7. SELF-ONLY: fleet legs must be UNKNOWN, never PASS ─────────────────
#
# With no fleet_hosts anywhere the script checks only itself. Reporting that as
# "fleet SHA drift PASS 1/1" is a green verdict built from zero external
# evidence — the exact "narrowed list masquerading as the whole fleet" this
# file exists to prevent, and reachable on every box that is not the manager
# (none of them carries a fleet_hosts). Unobservable is never a pass.
WD_FIX="$TMP/wd.json"; printf '{"ts": %s, "signals": []}\n' "$(date +%s)" > "$WD_FIX"
out="$(MESHFORGE_FLEET_HOSTS="$TMP/nope" HONEST_WD_PATH="$WD_FIX" \
       FAKE_CURL_JSON='{"confirmation_rate": 0.5}' run)"
check "SELF-ONLY drift leg is UNKNOWN, not a self-confirmed PASS" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN' && echo ok)"
check "SELF-ONLY drift leg says WHY (no peer to compare against)" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -qi 'no peer' && echo ok)"
check "SELF-ONLY watchdog leg is UNKNOWN — one box is not a fleet" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q 'UNKNOWN' && echo ok)"
check "SELF-ONLY conf_rate leg is UNKNOWN — assertion unverified fleet-wide" \
  "$(echo "$out" | grep -E 'conf_rate' | grep -q 'UNKNOWN' && echo ok)"
# (No "gate cannot exit 0" assertion here: run() always passes --quick, so the
# suite leg alone forces non-green and such a check could never fail — the very
# artifact test_honest_status_shell.py was created to stop shipping.)

# ── 8. the box list is DEDUPED ───────────────────────────────────────────
#
# Self is appended unconditionally, so a fleet_hosts that already lists this
# box counted it TWICE — inflating btotal and checking the same box twice in
# the conf_rate leg ("<self>:0.000 <self>:0.000", drilled 2026-07-28).
# The manager's file omits self by convention, but a convention in a comment
# header is not a guarantee, and it is a property of the MANAGER's file only.
# Dedup is general: a host listed twice in the SSOT is also one box.
SELF_NAME="$(hostname 2>/dev/null || echo localhost)"
printf 'box-good\n%s\n' "$SELF_NAME" > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" FAKE_CURL_JSON='{"confirmation_rate": 0.5}' run)"
check "self listed in fleet_hosts is not counted twice (2 boxes, not 3)" \
  "$(echo "$out" | grep -q 'fleet legs cover 2 box(es)' && echo ok)"
check "and the conf_rate leg checks it once, not twice" \
  "$(test "$(echo "$out" | grep 'conf_rate' | grep -o "$SELF_NAME:" | wc -l)" = 1 && echo ok)"

printf 'box-good\nbox-good\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" run)"
check "a host listed twice in the SSOT is one box (2, not 3)" \
  "$(echo "$out" | grep -q 'fleet legs cover 2 box(es)' && echo ok)"

# ── 9. per-repo host list wins, exactly as fleet_pull.sh resolves it ─────
#
# fleet_pull.sh:54-58 resolves fleet_hosts.<repo-basename> BEFORE the generic
# file, and the mechanism is live (fleet_hosts.meshanchor and
# fleet_hosts.meshforge-maps both exist). This script skipped that tier, so the
# "same SSOT as fleet_pull" claim held only by accident: create the per-repo
# file and fleet_pull deploys to list A while the gate verifies list B — and
# prints a provenance line naming the generic file as authoritative. That is
# the two-consumers-two-constants drift this box list exists to end, one tier up.
REPO_BASE="$(basename "$FAKE_REPO")"
mkdir -p "$FAKE_HOME/.config/meshforge"
printf 'box-norepo\n' > "$FAKE_HOME/.config/meshforge/fleet_hosts"
printf 'box-good\n'   > "$FAKE_HOME/.config/meshforge/fleet_hosts.$REPO_BASE"
out="$(run)"
check "per-repo fleet_hosts.<repo> wins over the generic file" \
  "$(echo "$out" | grep -q "source: .*fleet_hosts\.$REPO_BASE" && echo ok)"
check "and the generic file is NOT the one used" \
  "$(echo "$out" | grep -E 'source:' | grep -qE "fleet_hosts +\+" && echo '' || echo ok)"

rm -f "$FAKE_HOME/.config/meshforge/fleet_hosts.$REPO_BASE"
out="$(run)"
check "generic fleet_hosts still used when no per-repo file exists" \
  "$(echo "$out" | grep -q "source: $FAKE_HOME/.config/meshforge/fleet_hosts +" && echo ok)"
rm -f "$FAKE_HOME/.config/meshforge/fleet_hosts"

# ── 10. an EMPTY fleet_hosts is not a fleet ──────────────────────────────
#
# A file that exists but lists no hosts was treated as a FOUND SSOT, so BOXES
# collapsed to self while the fleet legs stayed eligible for PASS — the same
# "one box reported as whole-fleet coverage" defect as the SELF-ONLY path,
# entered through a different door. fleet_pull.sh:66 already refuses this case
# out loud ("refusing the silent no-op"); the gate that VERIFIES the deploy
# must not be laxer than the tool that PERFORMS it.
#
# The comments-only variant is the subtle one: stripping `#` comments leaves
# blank lines, so the file is non-empty on disk and yields nothing.
WD_FIX="$TMP/wd.json"; printf '{"ts": %s, "signals": []}\n' "$(date +%s)" > "$WD_FIX"
for variant in empty comments-only; do
  case "$variant" in
    empty)         : > "$TMP/hostlist_none" ;;
    comments-only) printf '# all boxes retired\n\n#moc1\n' > "$TMP/hostlist_none" ;;
  esac
  out="$(MESHFORGE_FLEET_HOSTS="$TMP/hostlist_none" HONEST_WD_PATH="$WD_FIX" \
         FAKE_CURL_JSON='{"confirmation_rate": 0.5}' run)"
  check "[$variant] provenance says the list has NO HOSTS, not that a fleet was found" \
    "$(echo "$out" | grep -q 'no hosts listed' && echo ok)"
  check "[$variant] drift leg is UNKNOWN, not a self-confirmed PASS" \
    "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN' && echo ok)"
  check "[$variant] watchdog leg is UNKNOWN — one box is not a fleet" \
    "$(echo "$out" | grep -E 'watchdog signals' | grep -q 'UNKNOWN' && echo ok)"
  check "[$variant] conf_rate leg is UNKNOWN — assertion unverified fleet-wide" \
    "$(echo "$out" | grep -E 'conf_rate' | grep -q 'UNKNOWN' && echo ok)"
done

# ── 11. a DEAD watchdog unit is a FAULT, never an absent organ ───────────
#
# The tri-state read "anything not 'active' = no watchdog here" and shrank
# the denominator, so a failed/crashlooping meshforge-watchdog (the #82
# class — NRestarts=7842, undetected 10 days) read GREEN in the check of
# record. LoadState is what separates the two: not-found = legitimately
# absent, loaded+not-active = broken (2026-07-28 review).
out="$(HONEST_BOXES="box-good box-wdfail" run)"
check "installed-but-dead watchdog unit FAILS the leg" \
  "$(echo "$out" | grep -E 'watchdog' | grep -q 'FAIL' && echo ok)"
check "and names the box + unit state" \
  "$(echo "$out" | grep -q 'box-wdfail:WATCHDOG-UNIT-failed' && echo ok)"
out="$(HONEST_BOXES="box-good box-norepo" run)"
check "unit not-found still reads as absent organ (no false FAIL)" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q '1/1 clean' && echo ok)"

# ── 12. a git FAILURE on a repo-carrying box is not 'no repo' ────────────
#
# Empty git output used to be counted norepo and dropped from the
# denominator — a box with a corrupt .git / dubious-ownership refusal /
# missing git silently fell out of "fleet SHA drift PASS N/N". Repo presence
# is proven by the .git path; a repo-present git failure stays in the
# denominator as unverified (2026-07-28 review).
out="$(HONEST_BOXES="box-good box-giterr" run)"
check "git-error box keeps the drift leg UNKNOWN, never PASS" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN' && echo ok)"
check "and is named as git-error with the repo present, not as no-repo" \
  "$(echo "$out" | grep -q 'box-giterr:git-error(repo present)' && echo ok)"

# ── 13. host lines may carry trailing comments — both consumers agree ────
#
# 'moc1  # retired' must parse as host moc1 everywhere. The gate's old copy
# stripped comments while fleet_pull kept the whole line as a garbage
# hostname — the two consumers of one SSOT disagreeing about what it SAYS.
# Both now source scripts/lib/fleet_hosts.sh; this drives the gate's side.
printf 'box-good  # the only real peer\n# a full-line comment\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" run)"
check "trailing comment stripped: 2 boxes (host + self), not a garbage token" \
  "$(echo "$out" | grep -q 'fleet legs cover 2 box(es)' && echo ok)"
check "and the commented host itself resolved (drift 1/1)" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q '1/1' && echo ok)"

# ── 14. a HELD (observer-blind) signal is UNKNOWN, never degraded ────────
#
# Row-92 contract (ratified 2026-08-06): watchdog_tracker re-emits a signal
# whose observer went blind (extra.unobserved_hold) so silence cannot read
# as recovery — but the gate of record must file it as LAST-KNOWN evidence
# (UNKNOWN tier), never as a live "degraded" observation (moc5 held
# kernel_reboot_pending 347 ticks AFTER the condition was cured). Chronic
# blindness escalation belongs to mini's detector_blind_any (24h grace) —
# the tracker and this gate deliberately carry no second debounce.
# Self's REAL watchdog state must not leak into these verdicts (it carried a
# live degraded signal when this section was written) — pin self to the clean
# fixture; the stub boxes still answer over the stubbed ssh.
printf 'box-heldonly\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_WD_PATH="$WD_FIX" run)"
check "held-only box reports held-blind (last-known), not a live signal" \
  "$(echo "$out" | grep 'watchdog' | grep -q 'held-blind' && echo ok)"
check "held-only box produces NO degraded WARN line" \
  "$(echo "$out" | grep -q 'watchdog (degraded)' || echo ok)"
printf 'box-mixhold\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_WD_PATH="$WD_FIX" run)"
# Finding 12 (2026-09-09): the held-blind signal is UNKNOWN and UNKNOWN
# outranks WARN — a live degraded signal beside it must not demote the leg
# to WARN/exit 0. The live signal is still counted and named (1, not 2).
check "mixed box: held-blind keeps the leg UNKNOWN; the live signal is counted beside it (1, not 2)" \
  "$(echo "$out" | grep 'watchdog signals' | grep 'UNKNOWN' | grep -q 'beside 1 live degraded' && echo ok)"
check "mixed box: no WARN line — a fault must never make the gate greener" \
  "$(echo "$out" | grep -q 'watchdog (degraded)' || echo ok)"
check "mixed box: the held signal stays visible in the class list" \
  "$(echo "$out" | grep -q 'held-blind' && echo ok)"

# ── finding 12: held-blind on ONE box + live degraded on ANOTHER ───────
# The exact shape: alone, box-heldonly read UNKNOWN; add box-degraded and the
# old ordering read WARN (exit 0 non-strict). Adding a fault made it greener.
printf 'box-heldonly\nbox-degraded\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_WD_PATH="$WD_FIX" run)"
check "held-blind box + degraded box → UNKNOWN, not WARN" \
  "$(echo "$out" | grep 'watchdog signals' | grep -q 'UNKNOWN' && echo ok)"
check "and no degraded WARN line masks it" \
  "$(echo "$out" | grep -q 'watchdog (degraded)' || echo ok)"
printf 'box-degraded\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_WD_PATH="$WD_FIX" run)"
check "control: a degraded box ALONE still reads WARN" \
  "$(echo "$out" | grep 'watchdog (degraded)' | grep -q '1 degraded, 0 wedge' && echo ok)"

# ── finding 14a: a polluted remote clock is UNKNOWN, never a fresh age ──
# `awk "BEGIN{… $rnow - $ts}"` yielded EMPTY on a non-numeric rnow and the
# empty age SKIPPED the stale gate, so a days-old snapshot counted clean.
printf 'box-noisyclock\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_WD_PATH="$WD_FIX" HONEST_WD_STALE_S=300 run)"
check "non-numeric remote clock → UNKNOWN naming the clock, not clean" \
  "$(echo "$out" | grep 'watchdog signals' | grep 'UNKNOWN' | grep -q 'box-noisyclock:clock-unreadable' && echo ok)"
check "and the days-old snapshot is NOT counted clean" \
  "$(echo "$out" | grep 'watchdog signals' | grep -q '2/2 clean' && echo '' || echo ok)"
printf 'box-future\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_WD_PATH="$WD_FIX" HONEST_WD_STALE_S=300 run)"
check "snapshot stamped in the box's own future → UNKNOWN naming the stepped clock" \
  "$(echo "$out" | grep 'watchdog signals' | grep 'UNKNOWN' | grep -q 'box-future:future-stamped' && echo ok)"

# ── finding 13: the TWIN fleet SHA leg shares leg 2's measurement ────────
# A dormant sister-repo box read `unreach` → UNKNOWN for the whole declared
# window: the twin loop was a hand-copy with no posture check.
TWIN="$TMP/twin"; mkdir -p "$TWIN"
git -C "$TWIN" init -q 2>/dev/null
git -C "$TWIN" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init 2>/dev/null
TWIN_HEAD="$(git -C "$TWIN" rev-parse HEAD)"
printf 'box-good\nbox-down\n' > "$FAKE_HOME/.config/meshforge/fleet_hosts.twin"
POSTURE2="$TMP/posture2.json"
"$REAL_PYTHON3" - "$POSTURE2" <<'PYEOF'
import json, sys, time
from datetime import datetime, timezone
ts = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"boxes": {"box-down": {"state": "dormant", "since": ts(time.time()),
                                  "until": ts(time.time() + 3600), "reason": "drill"}}},
          open(sys.argv[1], "w"))
PYEOF
printf 'box-good\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_TWIN_REPO="$TWIN" FAKE_TWIN_REPO="$TWIN" FAKE_TWIN_HEAD="$TWIN_HEAD" \
       MESHFORGE_FLEET_POSTURE="$POSTURE2" run)"
check "twin leg: dormant sister-repo box reads :dormant" \
  "$(echo "$out" | grep 'twin fleet SHA' | grep -q 'box-down:dormant' && echo ok)"
check "twin leg: dormant box is not :unreach" \
  "$(echo "$out" | grep 'twin fleet SHA' | grep -q 'box-down:unreach' && echo '' || echo ok)"
check "twin leg: PASSES 1/1 with the dormant box out of the denominator" \
  "$(echo "$out" | grep 'twin fleet SHA' | grep -q 'PASS' && echo ok)"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_TWIN_REPO="$TWIN" FAKE_TWIN_REPO="$TWIN" FAKE_TWIN_HEAD="$TWIN_HEAD" \
       MESHFORGE_FLEET_POSTURE="$TMP/absent.json" run)"
check "twin leg: without a declaration the down box is unreach → UNKNOWN (control)" \
  "$(echo "$out" | grep 'twin fleet SHA' | grep 'UNKNOWN' | grep -q 'box-down:unreach' && echo ok)"
rm -f "$FAKE_HOME/.config/meshforge/fleet_hosts.twin"

# ── finding 11/18: the marker comes from the shared builder + predicate ──
# The tree flag was computed by a bash copy here and NOT AT ALL in
# calibration_reverify.sh; both now call calibration_ledger.tree_is_dirty.
# The path is verdict_marker_path() — under HOME=$FAKE_HOME that is
# $FAKE_HOME/.cache/meshforge/honest_verdict.json for writer AND readers.
MARKER="$FAKE_HOME/.cache/meshforge/honest_verdict.json"
rm -f "$MARKER"
out="$(HONEST_BOXES="box-good" run)"
mflag() { "$REAL_PYTHON3" - "$MARKER" "$1" <<'PYEOF'
import json, sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2]))
except Exception as e: print("ERR", e)
PYEOF
}
check "marker written at the SSOT path with instrument=honest_status" \
  "$(test "$(mflag instrument)" = honest_status && echo ok)"
check "clean fake repo → dirty_tree False" \
  "$(test "$(mflag dirty_tree)" = False && echo ok)"
: > "$FAKE_REPO/untracked_new_test.py"
out="$(HONEST_BOXES="box-good" run)"
check "an untracked file → dirty_tree True (the shared predicate counts untracked)" \
  "$(test "$(mflag dirty_tree)" = True && echo ok)"
rm -f "$FAKE_REPO/untracked_new_test.py"

echo "---"
# ── 12. declared posture (2026-09-01 DORMANT arc): a box switched OFF on
# purpose is `:dormant`, out of the denominators, never `unreach`, and the
# SHA leg still PASSES on the boxes that are up. Absent file = untouched.
printf 'box-good\nbox-down\n' > "$hosts"
POSTURE="$TMP/posture.json"
"$REAL_PYTHON3" - "$POSTURE" <<'PYEOF'
import json, sys, time
from datetime import datetime, timezone
ts = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"boxes": {"box-down": {"state": "dormant", "since": ts(time.time()),
                                  "until": ts(time.time() + 3600), "reason": "drill"}}},
          open(sys.argv[1], "w"))
PYEOF
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_BOXES="box-good box-down" MESHFORGE_FLEET_POSTURE="$POSTURE" run)"
check "dormant box reported as :dormant on the SHA leg" \
  "$(echo "$out" | grep 'fleet SHA drift' | grep -q 'box-down:dormant' && echo ok)"
check "dormant box is not counted unreach on the SHA leg" \
  "$(echo "$out" | grep 'fleet SHA drift' | grep -q 'box-down:unreach' && echo '' || echo ok)"
check "SHA leg PASSES 1/1 with the dormant box out of the denominator" \
  "$(echo "$out" | grep 'fleet SHA drift' | grep -q 'PASS' && echo ok)"
check "watchdog leg reports the dormant box as :dormant, not unreach" \
  "$(echo "$out" | grep 'watchdog signals' | grep -q 'box-down:dormant' && echo ok)"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_BOXES="box-good box-down" MESHFORGE_FLEET_POSTURE="$TMP/absent.json" run)"
check "without a declaration the same box is unreach (today's behaviour)" \
  "$(echo "$out" | grep 'fleet SHA drift' | grep -q 'box-down:unreach' && echo ok)"

# ── §3 drill 2026-09-07: two conflations the fleet legs read as green ──
# (a) an UNREACHABLE box was silently dropped from the conf_rate leg, which
#     then printed PASS over the boxes that answered.
out="$(HONEST_BOXES="box-good box-down" run)"
check "conf_rate leg is UNKNOWN when a box is unreachable, never PASS" \
  "$(echo "$out" | grep -E 'conf_rate' | grep -q 'UNKNOWN' && echo ok)"
check "and it names the unreachable box" \
  "$(echo "$out" | grep -E 'conf_rate' | grep -q 'box-down:unreach' && echo ok)"
# (b) a watchdog snapshot with NO ts skipped the freshness gate and counted
#     clean — an absent age reading as a fresh one.
WD_NOTS="$TMP/wd_nots.json"; printf '{"signals": []}\n' > "$WD_NOTS"
out="$(HONEST_BOXES="$SELF_NAME" HONEST_WD_PATH="$WD_NOTS" HONEST_WD_STALE_S=300 run)"
check "watchdog snapshot without ts is UNKNOWN under the stale gate, not clean" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q 'UNKNOWN' && echo ok)"
check "and says the age is unobservable" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q 'no-ts' && echo ok)"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
