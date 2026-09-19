#!/usr/bin/env bash
# Behavior test for fleet_sync.sh sync_repo when the pull applies NOTHING.
#
# WHY (2026-09-18): the "no commits to apply" branch skipped the restart
# unconditionally, asserting "any running service is on the same code the
# restart would bring up". That is only true if sync itself is the only thing
# that pulls -- and scripts/fleet_pull.sh pulls BY DESIGN without restarting.
# After a real fleet_pull, 46 units across 10 boxes were behind the newest CODE
# commit and this branch would have reported every one of them `unchanged`
# while exiting 0. Sync was blind to skew it did not itself cause.
#
# Drives the REAL payload (extracted exactly as fleet_sync binds it) with a
# stub systemctl, so the assertion is about behavior, not about source text.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SYNC="$HERE/../scripts/fleet_sync.sh"
LIB="$HERE/../scripts/lib/code_paths.sh"
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- extract ONLY the sync_repo function ----------------------------------
# NOT the whole REMOTE_SCRIPT body: that payload ends in top-level sync_repo /
# sync_local_* invocations against /opt/meshforge, so sourcing it RUNS A REAL
# DEPLOY on the machine running the tests. Learned the hard way while writing
# this file (no harm: the system paths died early on a missing git_heal stub).
# A test must not be able to deploy.
PAYLOAD="$TMP/sync_repo.sh"
awk '/^sync_repo\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$PAYLOAD"
if [ ! -s "$PAYLOAD" ]; then echo "FAIL could not extract sync_repo"; exit 1; fi
# The USER-bus sibling has the SAME branch and the same blindness -- fixing only
# the one the incident arrived through is the 2026-08-09 trap. Extract it too.
UPAYLOAD="$TMP/sync_user_unit.sh"
awk '/^sync_user_unit\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$UPAYLOAD"
if [ ! -s "$UPAYLOAD" ]; then echo "FAIL could not extract sync_user_unit"; exit 1; fi
# NOT an early exit: an early exit here short-circuits the BEHAVIOURAL cases,
# so a regression would be proven only by a grep. Counted as an assertion at
# the end instead, after the cases have actually run.
# sync_repo calls git_heal_ownership (prepended separately in the real script).
# Source the REAL lib rather than stub it: a stub that returned the wrong token
# made sync_repo bail early, which silently satisfied the "must not restart"
# case for the wrong reason.
GIT_HEAL_LIB="$HERE/../scripts/lib/git_heal.sh"
if [ -r "$GIT_HEAL_LIB" ]; then . "$GIT_HEAL_LIB"
else echo "FAIL missing $GIT_HEAL_LIB"; exit 1; fi

# --- stub systemctl: MainPID is OUR pid, so /proc/<pid> is real -----------
SB="$TMP/bin"; mkdir -p "$SB"
cat > "$SB/systemctl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"-p MainPID"*)      echo "$STUB_PID" ;;
  *list-unit-files*)   echo "meshforge-test.service enabled" ;;
  *restart*)           touch "$STUB_MARKER"; exit 0 ;;
  *is-active*)         echo active; exit 0 ;;
  *)                   exit 0 ;;
esac
STUB
chmod +x "$SB/systemctl"
# sync_repo restarts via `sudo -n systemctl`, and sudo resets PATH (secure_path),
# so the stub above would be bypassed and the REAL systemctl asked to restart a
# unit that does not exist. Stub sudo to drop its flags and exec in place.
cat > "$SB/sudo" <<'STUB'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -n|-A|-H|-E|-S) shift ;; *) break ;; esac; done
exec "$@"
STUB
chmod +x "$SB/sudo"
export PATH="$SB:$PATH"

# --- a repo that is genuinely AT HEAD, with a datable code commit ---------
mk_repo() {   # $1 = committer date for the src/ commit
    rm -rf "$TMP/origin" "$TMP/repo"
    git init -q --bare "$TMP/origin"
    git init -q "$TMP/repo"
    cd "$TMP/repo" || return 1
    git config user.email t@t; git config user.name t
    git symbolic-ref HEAD refs/heads/main
    mkdir -p src; echo "x=1" > src/x.py; git add src/x.py
    GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "code"
    git remote add origin "$TMP/origin"
    git push -q origin main 2>/dev/null
    git branch --set-upstream-to=origin/main main >/dev/null 2>&1
    cd - >/dev/null || return 1
}

run_case() {  # $1=label $2=date $3=expect_restart(yes|no) $4=pid (default self)
    mk_repo "$2" || { echo "FAIL $1 repo setup"; fails=$((fails+1)); return; }
    export STUB_MARKER="$TMP/restarted.$1"; rm -f "$STUB_MARKER"
    export STUB_PID="${4:-$$}"
    local head out
    head="$(git -C "$TMP/repo" rev-parse HEAD)"
    out="$(cd "$TMP/repo" && . "$LIB" && . "$PAYLOAD" && \
           sync_repo test "$TMP/repo" meshforge-test "$head" 2>&1)"
    if [ "$3" = "yes" ]; then
        if [ -f "$STUB_MARKER" ]; then echo "ok   $1 restarted despite no commits"
        else echo "FAIL $1 expected a restart, got: $out"; fails=$((fails+1)); fi
    else
        if [ -f "$STUB_MARKER" ]; then
            echo "FAIL $1 restarted a CURRENT process: $out"; fails=$((fails+1))
        elif ! printf %s "$out" | grep -q unchanged; then
            # "no marker" is ALSO true when sync_repo died before deciding, so
            # require the decision itself to appear. Without this the case
            # passes for the wrong reason (it did, mid-authoring).
            echo "FAIL $1 never reached the decision: $out"; fails=$((fails+1))
        else echo "ok   $1 no restart, decision reached (process is current)"; fi
    fi
}

# Process start time is ~now (our own /proc entry).
# Code commit an hour OLD  -> process is current    -> must NOT restart.
run_case current "$(date -d '-1 hour' 2>/dev/null || date -v-1H)" no
# Process predates the code -> MUST restart, even though the pull applied
# nothing. This is the regression this file exists for.
#
# ⚠️ STAGED WITH AN OLD PROCESS (pid 1, started at boot), NOT with a
# future-dated commit. Until 2026-09-18 this case dated the commit an hour
# AHEAD, which is cheaper but stages a state the real world cannot produce: a
# commit cannot be made later than now. That fiction is indistinguishable from
# a BOX WHOSE CLOCK IS BEHIND -- which is a real state on this fleet and the
# opposite verdict -- so the test would have pinned the clock-skew bug in place
# the moment anyone tried to fix it. Stage the process as old; leave the clock
# telling the truth.
run_case behind  "$(date)" yes 1

run_user_case() {  # $1=label $2=date $3=expect_restart(yes|no) $4=pid (default self)
    mk_repo "$2" || { echo "FAIL $1 repo setup"; fails=$((fails+1)); return; }
    export STUB_MARKER="$TMP/restarted.$1"; rm -f "$STUB_MARKER"
    export STUB_PID="${4:-$$}"
    local head out
    head="$(git -C "$TMP/repo" rev-parse HEAD)"
    # pre_head == HEAD is the "pull applied nothing" case, same as sync_repo.
    out="$(cd "$TMP/repo" && . "$LIB" && . "$UPAYLOAD" && \
           sync_user_unit test "$TMP/repo" meshforge-test "$head" 2>&1)"
    if [ "$3" = "yes" ]; then
        if [ -f "$STUB_MARKER" ]; then echo "ok   $1 user unit restarted despite no commits"
        else echo "FAIL $1 expected a user restart, got: $out"; fails=$((fails+1)); fi
    else
        if [ -f "$STUB_MARKER" ]; then
            echo "FAIL $1 restarted a CURRENT user process: $out"; fails=$((fails+1))
        elif ! printf %s "$out" | grep -q unchanged; then
            echo "FAIL $1 never reached the decision: $out"; fails=$((fails+1))
        else echo "ok   $1 no restart, decision reached (user process is current)"; fi
    fi
}

run_user_case user-current "$(date -d '-1 hour' 2>/dev/null || date -v-1H)" no
# The one that would have shipped half-fixed: a USER unit older than its code,
# with nothing pulled. It must restart -- and must NOT be swallowed by the
# docs_only test that sits after this branch and diffs a sha against itself.
run_user_case user-behind  "$(date)" yes 1

# ===========================================================================
# CLOCK SKEW (2026-09-18). Both sides of the decision above are wall-clock
# stamps from DIFFERENT clocks: the commit date was written by whichever box
# authored it, the process stamp by THIS box at fork. This fleet forges clocks
# -- no RTC, fake-hwclock restores a stale time at boot, moc4 ran ~8 days
# behind -- and on such a box `code_ct > started` is permanently true, so sync
# restarts the unit, the replacement gets another stale stamp, and the next
# sync restarts it again: a deploy tool in a restart loop, reporting PASS.
# The honest answer is UNKNOWN. These cases pin that it IS unknown, and the
# two `behind` cases above pin that a healthy clock still restarts.

# A commit dated in this box's FUTURE cannot have happened; it means this box's
# clock is behind the one that wrote it. Process is genuinely old (pid 1), so
# the OLD code would have restarted -- and would go on restarting forever.
run_clock_case() {  # $1=label $2=runner(sys|user)
    local future out
    future="$(date -d '+1 hour' 2>/dev/null || date -v+1H)"
    mk_repo "$future" || { echo "FAIL $1 repo setup"; fails=$((fails+1)); return; }
    export STUB_MARKER="$TMP/restarted.$1"; rm -f "$STUB_MARKER"
    export STUB_PID=1
    local head; head="$(git -C "$TMP/repo" rev-parse HEAD)"
    if [ "$2" = user ]; then
        out="$(cd "$TMP/repo" && . "$LIB" && . "$UPAYLOAD" && \
               sync_user_unit test "$TMP/repo" meshforge-test "$head" 2>&1)"
    else
        out="$(cd "$TMP/repo" && . "$LIB" && . "$PAYLOAD" && \
               sync_repo test "$TMP/repo" meshforge-test "$head" 2>&1)"
    fi
    if [ -f "$STUB_MARKER" ]; then
        echo "FAIL $1 restarted on a clock it cannot trust (the loop): $out"
        fails=$((fails+1))
    elif ! printf %s "$out" | grep -q clock_behind; then
        # No marker is ALSO true when the function died before deciding, so
        # require the VERDICT, not just the absence of a restart.
        echo "FAIL $1 declined for some other reason, not clock doubt: $out"
        fails=$((fails+1))
    elif ! printf %s "$out" | grep -q '^WARN '; then
        # UNKNOWN must be loud. A silent decline re-opens the blindness this
        # file was written for, one box at a time (honest_failure_modes #2).
        echo "FAIL $1 named the clock but did not WARN: $out"
        fails=$((fails+1))
    else
        echo "ok   $1 declined + WARNed instead of looping"
    fi
}
run_clock_case clock-behind      sys
run_clock_case user-clock-behind user

# The backward-step tell cannot be staged through /proc (we cannot forge a
# process that started in the future), so it is asserted on the helper direct.
clock_unit_check() {
    mk_repo "$(date)" >/dev/null 2>&1 || { echo "FAIL clock-helper repo setup"; fails=$((fails+1)); return; }
    local now v
    now="$(date +%s)"
    . "$LIB"
    # healthy: repo dated now, process started now -> no doubt at all.
    v="$(mf_clock_trust "$TMP/repo" "$now")"
    if [ -n "$v" ]; then
        echo "FAIL mf_clock_trust doubts a healthy clock: $v"; fails=$((fails+1))
    else
        echo "ok   mf_clock_trust is silent on a healthy clock"
    fi
    # clock stepped BACKWARD since the process started (hfm #6).
    v="$(mf_clock_trust "$TMP/repo" "$(( now + 86400 ))")"
    case "$v" in
        clock_stepped_back*) echo "ok   mf_clock_trust catches a backward clock step" ;;
        *) echo "FAIL a process started in the future read as: ${v:-<trusted>}"; fails=$((fails+1)) ;;
    esac
    # tolerance is not a hair trigger: ordinary inter-box NTP jitter must pass,
    # or every sync on a healthy fleet turns into a WARN and gets ignored.
    v="$(mf_clock_trust "$TMP/repo" "$(( now + 5 ))")"
    if [ -n "$v" ]; then
        echo "FAIL 5s of jitter tripped the clock gate: $v"; fails=$((fails+1))
    else
        echo "ok   mf_clock_trust tolerates ordinary NTP jitter"
    fi
}
clock_unit_check

# ---------------------------------------------------------------------------
# The SELF leg (this box). Same two-clock comparison, and unlike the remote leg
# it has NO content-based path -- it is timestamps all the way down -- so the
# loop would be worse here: sync_local_unit restarts meshforge-map, a 6-10 min
# cold start on the heavy-DB boxes. Fixing only the branch the incident arrived
# through is the documented 2026-08-09 trap; these cases are that grep, run.
SPAYLOAD="$TMP/sync_local_unit.sh"
awk '/^sync_local_unit\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$SPAYLOAD"
SUPAYLOAD="$TMP/sync_local_user_unit.sh"
awk '/^sync_local_user_unit\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$SUPAYLOAD"
SKIPFN="$TMP/self_skip.sh"
awk '/^self_skip\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$SKIPFN"
for f in "$SPAYLOAD" "$SUPAYLOAD" "$SKIPFN"; do
    [ -s "$f" ] || { echo "FAIL could not extract $(basename "$f" .sh)"; fails=$((fails+1)); }
done

run_self_case() {  # $1=label $2=fn $3=commit_date $4=pid $5=expect(restart|current|clock)
    mk_repo "$3" || { echo "FAIL $1 repo setup"; fails=$((fails+1)); return; }
    export STUB_MARKER="$TMP/restarted.$1"; rm -f "$STUB_MARKER"
    export STUB_PID="$4"
    local body out
    case "$2" in sync_local_unit) body="$SPAYLOAD" ;; *) body="$SUPAYLOAD" ;; esac
    out="$(. "$LIB"; . "$SKIPFN"; . "$body"; \
           action_pass=0; action_fail=0; action_warn=0; action_skip=0; NO_RESTART=0; \
           "$2" meshforge-test "$TMP/repo" 2>&1)"
    case "$5" in
        restart)
            if [ -f "$STUB_MARKER" ]; then echo "ok   $1 restarted (process behind code)"
            else echo "FAIL $1 expected a restart, got: $out"; fails=$((fails+1)); fi ;;
        current)
            if [ -f "$STUB_MARKER" ]; then
                echo "FAIL $1 restarted a CURRENT process: $out"; fails=$((fails+1))
            elif ! printf %s "$out" | grep -q 'current (no restart needed)'; then
                echo "FAIL $1 never reached the decision: $out"; fails=$((fails+1))
            else echo "ok   $1 no restart, decision reached"; fi ;;
        clock)
            if [ -f "$STUB_MARKER" ]; then
                echo "FAIL $1 restarted on a clock it cannot trust (the loop): $out"
                fails=$((fails+1))
            elif ! printf %s "$out" | grep -q clock_behind; then
                echo "FAIL $1 declined for some other reason: $out"; fails=$((fails+1))
            elif ! printf %s "$out" | grep -q ' WARN '; then
                echo "FAIL $1 named the clock but did not WARN: $out"; fails=$((fails+1))
            else echo "ok   $1 declined + WARNed instead of looping"; fi ;;
    esac
}
FUTURE="$(date -d '+1 hour' 2>/dev/null || date -v+1H)"
HOURAGO="$(date -d '-1 hour' 2>/dev/null || date -v-1H)"
run_self_case self-current      sync_local_unit      "$HOURAGO" $$ current
run_self_case self-behind       sync_local_unit      "$(date)"  1  restart
run_self_case self-clock-behind sync_local_unit      "$FUTURE"  1  clock
run_self_case self-user-current sync_local_user_unit "$HOURAGO" $$ current
run_self_case self-user-behind  sync_local_user_unit "$(date)"  1  restart
run_self_case self-user-clock   sync_local_user_unit "$FUTURE"  1  clock

# ALL FOUR deciders, not just the two the fix arrived through.
# ⚠️ Match the CALL, not the NAME. The first cut of this grepped for the bare
# string `mf_clock_trust`, and when the gates were stripped out to drill it,
# the two self-leg payloads still passed -- their COMMENT names the helper. A
# check a comment can satisfy is satisfied the cheapest way.
missing=""
for f in "$PAYLOAD" "$UPAYLOAD" "$SPAYLOAD" "$SUPAYLOAD"; do
    grep -qE '\$\(mf_clock_trust ' "$f" || missing="$missing $(basename "$f" .sh)"
done
if [ -z "$missing" ]; then
    echo "ok   all four restart deciders consult mf_clock_trust"
else
    echo "FAIL restart decider(s) with no clock gate:$missing"; fails=$((fails+1))
fi
# ===========================================================================

if grep -q 'mf_code_head' "$PAYLOAD" && grep -q 'mf_code_head' "$UPAYLOAD"; then
    echo "ok   both sync_repo and sync_user_unit consult mf_code_head"
else
    echo "FAIL a sync path does not consult mf_code_head"; fails=$((fails+1))
fi

# mf_code_head must be REACHABLE from the payload on the remote box, or the
# branch above dies with command-not-found on all nine at once.
if grep -q 'MF_CODE_PATHS_SRC' "$SYNC" && grep -q '\$MF_CODE_PATHS_SRC' "$SYNC"; then
    echo "ok   lib/code_paths.sh is shipped into the remote payload"
else
    echo "FAIL remote payload does not ship lib/code_paths.sh"; fails=$((fails+1))
fi

# "ALL PASS" is the sentinel tests/test_honest_status_shell.py asserts on --
# exit 0 alone is NOT enough there, deliberately, so a harness cannot pass by
# saying nothing. (This file failed that check on its first run.)
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "SOME FAILED ($fails assertion(s))"
exit 1
