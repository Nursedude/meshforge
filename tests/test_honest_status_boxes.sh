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
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "loaded"; echo "---WDSEP---"
               echo "{\"ts\": $(date +%s), \"signals\": []}" ;;
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
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" \
    MESHFORGE_REPO="$FAKE_REPO" FAKE_HEAD="$FAKE_HEAD" \
    FAKE_CURL_JSON="${FAKE_CURL_JSON:-}" HONEST_WD_PATH="${HONEST_WD_PATH:-}" \
    bash "$SCRIPT" --quick "$@" 2>&1
}

# The fake repo is a REAL git repo with one commit, so `git rev-parse HEAD`
# answers a real sha both for the gate's own HEAD and for the SELF leg. That
# matters: with a repo-less fake repo self fell out through the "no-repo" path,
# which HID the fact that self is compared against itself (2026-07-28).
git -C "$FAKE_REPO" init -q 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init 2>/dev/null
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
check "mixed box: only the LIVE signal counts as degraded (1, not 2)" \
  "$(echo "$out" | grep 'watchdog (degraded)' | grep -q '1 degraded, 0 wedge' && echo ok)"
check "mixed box: the held signal stays visible in the class list" \
  "$(echo "$out" | grep -q 'held-blind' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
