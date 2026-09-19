# scripts/lib/code_paths.sh — THE definition of "code a running daemon loaded".
#
# Source this file; it defines two variables and two functions, no side effects.
# MF_DAEMON_CODE_PATHS/mf_code_head say WHICH code dates a daemon;
# MF_CLOCK_TOL_S/mf_clock_trust say whether this box's clock may date it AT ALL.
#
# Consumers (keep this list current — it is the grep target when this changes):
#   - scripts/fleet_sync.sh    all four restart deciders — sync_repo and
#                              sync_user_unit (remote, shipped into
#                              REMOTE_SCRIPT) + sync_local_unit and
#                              sync_local_user_unit (self). These ACT on the
#                              answer, so they use mf_clock_trust too.
#   - scripts/honest_status.sh hs_codehead (interpolated into the REMOTE probe
#                              script, so the value must survive ssh as text)
#   - scripts/fleet_pull.sh    the meshforge-map "started before HEAD" nag
#
# ⚠️ The last two REPORT rather than act, and as of 2026-09-18 neither consults
# mf_clock_trust: on a clock-stale box honest_status will still bucket daemons
# as `behind` and fleet_pull will still nag. That is a legibility defect, not a
# loop, and it is named here rather than left to be rediscovered — the right
# landing for honest_status is its existing `U` (unknown) bucket, not a new one.
#
# WHY THIS FILE EXISTS (2026-09-15). The same question had THREE different
# answers, and they disagreed in production:
#
#   fleet_sync.sh    src/*  pyproject.toml  requirements*.txt
#   honest_status.sh src  requirements  requirements.txt  templates  scripts
#   fleet_pull.sh    <no pathspec at all — the whole repo's HEAD>
#
# So commit 712ac491, which touched ONLY scripts/fleet_sync.sh, made
# honest_status report 45 daemons "behind their repo's newest CODE commit"
# while fleet_sync correctly restarted none of them, and made fleet_pull nag
# about meshforge-map whose `server_class_skew` was in fact `{}`. The mirror
# case is the one that bites: a pyproject.toml-only change would have
# fleet_sync restarting daemons that honest_status calls current.
# honest_failure_modes #5 — two consumers of one artifact share ONE constant.
# Resolved by adopting the NARROW list (the remote classifier's, which is the
# one with measurements behind it) plus fleet_sync's pyproject.toml, which
# honest_status had been missing. A union was tried first and reverted: see
# WHAT COUNTS below for why, and for the gap that choice leaves open.
#
# WHAT COUNTS — and this list is NARROW on purpose, after the union was tried
# and reverted on 2026-09-15 (operator call). Two prior decisions, each with a
# measurement behind it, say so:
#
#   * 2026-05-11 — scripts/ was IN, and tuning scripts/cloud/push_snapshot.sh
#     "triggered fleet-wide map restarts for no benefit". The map does not
#     import that file. Excluded, and the remote classifier has carried that
#     exclusion ever since.
#   * templates/ cannot take effect through this mechanism AT ALL: fleet_sync
#     uses `try-restart`, never `daemon-reload`, so a changed unit file is not
#     picked up by the restart it would trigger. Including it is pointless by
#     construction, not a judgement call.
#
#   src               the daemon's own importable modules
#   pyproject.toml    packaging + dependency pins the interpreter resolves
#   requirements      the requirements/ directory (e.g. requirements/rns.txt)
#   requirements.txt  the top-level pin file
#
# ✅ THE GAP THIS LIST USED TO LEAVE IS NOW CLOSED — BY MOVING THE CODE,
# NOT BY WIDENING THE LIST (2026-09-15).
#
# Until this date exactly one resident daemon lived in scripts/:
#
#     nomadnet-silence-watch
#       ExecStart=/usr/bin/python3 /opt/meshforge/scripts/nomadnet_silence_watch.py
#
# A commit touching only that file moved no code-head, so the daemon could keep
# running pre-fix code. Not hypothetical: 366b435d (2026-09-02, "a box with no
# NomadNet logfile is INERT, not silent" — the cure for a LATCHING alarm)
# touched only that file and its test, and the code-head there resolved to a
# src/ commit 7.8 HOURS EARLIER.
#
# It now lives at src/monitoring/nomadnet_silence_watch.py and runs
# `-m monitoring.nomadnet_silence_watch`, the same shape as every other
# resident daemon. So this list is correct BY CONSTRUCTION: scripts/ means
# operator-invoked tooling that is current the moment it lands, src/ means code
# a resident process loads. Both dated decisions now agree instead of
# contradicting — 05-11 excluded scripts/ on the premise that no daemon lives
# there, and that premise is TRUE again.
#
# The fleet was SWEPT before choosing this (all 10 boxes, both scopes, 1,715
# units): 310 Type=simple, 12 running a repo path, and exactly ONE out of
# scripts/. Everything else already ran `-m module` or a src/ path.
#
# ⚠️ IF A DAEMON EVER LANDS IN scripts/ AGAIN, MOVE IT — do not widen this
# list and do not add an exception entry for it. Two rejected alternatives,
# both recorded so they are not re-proposed:
#   * PER-UNIT ExecStart attribution — needs `-m` module resolution and
#     `bash -c` parsing for the 11 units that do NOT need it to serve 1 that
#     does, and at file granularity it is blind to TRANSITIVE IMPORTS (this
#     daemon imports utils.fleet_hosts), so it would open a NEW under-restart
#     gap while looking more precise.
#   * An exception pathspec (`scripts/nomadnet_silence_watch.py` appended
#     here) — one line and near-zero risk, but it teaches the tool about a
#     layout violation instead of fixing it, and preserves the exact ambiguity
#     that cost three sessions of argument (05-11, 08-12, 09-15).
# Fix the layout; do not teach the tool about the violation.

#: Pathspecs for code a resident daemon has loaded. Word-split on purpose:
#: every consumer passes it unquoted after `--` so each entry is its own
#: pathspec. Plain directory names match recursively; no globs needed.
MF_DAEMON_CODE_PATHS="src pyproject.toml requirements requirements.txt"

# mf_code_head <repo> -> epoch seconds of the newest commit touching that code,
# or EMPTY when git fails or the paths were never touched. Empty is NOT zero:
# a caller must decide what an unknown code-head means for it (fleet_sync
# treats it as "cannot tell", honest_status falls back to the repo HEAD), and
# collapsing it to 0 would silently read as "infinitely old" — the absent-value
# -in-the-measurement-domain trap.
mf_code_head() {
    git -C "$1" log -1 --format=%ct -- $MF_DAEMON_CODE_PATHS 2>/dev/null
}

#: Tolerance (seconds) for wall-clock disagreement, beyond which a
#: commit-vs-process comparison is declared UNTRUSTWORTHY rather than answered.
#: Generous on purpose: inter-box NTP offset on a healthy fleet is sub-second,
#: and a deploy compares commits that are seconds old, so five minutes never
#: trips on a healthy box — while one that came up on fake-hwclock trips hard.
MF_CLOCK_TOL_S="${MF_CLOCK_TOL_S:-300}"

# mf_clock_trust <repo> <started_epoch> -> EMPTY when this box's wall clock can
# honestly date a commit against a process start, or a short reason token when
# it cannot. A token means UNKNOWN: never "current", never "behind".
#
# WHY (2026-09-18). Every consumer of mf_code_head asks one question —
#
#     is this running process older than the newest commit touching code it loads?
#
# — and answers it by comparing two numbers from TWO DIFFERENT CLOCKS. `%ct` is
# a wall-clock stamp written by whichever box authored the commit; the process
# side is `stat -c %Y /proc/<pid>`, written by THIS box's wall clock at fork.
# The comparison means something only while those two clocks agree.
#
# On this fleet they demonstrably do not. The Pis have no RTC, fake-hwclock
# restores a stale time at boot, and an unreachable NTP server cannot step it
# forward: moc4 ran ~8 days behind for days (persistent_issues, "uptime
# disagrees with wtmp/`who -b`"). On such a box the process stamp is written 8
# days in the past while commits pulled from a correct-clock box are not, so
# `code_ct > started` is permanently true — sync restarts the unit, the
# replacement process gets another stale stamp, and the next sync restarts it
# again. A deploy tool in a restart loop, reporting PASS every time.
# honest_failure_modes #6 — wall-clock durations are forgeable on this fleet.
#
# Two tells, both cheap, both from evidence the caller already has in hand:
#
#   1. The process claims to have started in this box's own FUTURE. Only a
#      backward clock step produces that, and it voids `started` outright.
#      (hfm #6: "persisted timestamps need a clock-went-backward branch".)
#   2. A commit in the repo is dated in this box's FUTURE. A commit cannot have
#      been made later than now, so this box's clock is behind the clock that
#      wrote it — precisely the disagreement that makes the comparison a lie.
#      The witness is the newest commit of ANY kind, not mf_code_head's: it is
#      the tightest bound available, and after a pull it is minutes old here.
#
# Tell 2 is the one that catches the damaging case. The verdict only goes wrong
# when the skew exceeds the true commit->start gap, and a skew that large puts
# a freshly pulled commit in the future, where tell 2 sees it.
#
# ⚠️ NOT total, and the residue is named rather than papered over: a repo whose
# newest commit is itself OLDER than the skew leaves the disagreement invisible
# to both tells (quiet repo + long-running process). Closing that needs a
# restart marker — record the code head a unit was actually started ON and
# compare CONTENT, no clocks — which is a bigger change than this loop warrants
# today, and which brings its own unwritable-state-dir failure mode (the
# 2026-09-02 frozen-streak class). If the residue ever bites, that is the fix;
# do not widen the tolerance, which only moves the hair trigger.
mf_clock_trust() {
    local repo="$1" started="${2:-}" now newest delta
    now="$(date +%s 2>/dev/null)"
    case "${now:-}" in
        ""|*[!0-9]*) echo "no_clock"; return 0 ;;
    esac
    case "$started" in
        ""|*[!0-9]*) : ;;
        *)
            if [ "$started" -gt "$(( now + MF_CLOCK_TOL_S ))" ]; then
                delta=$(( started - now ))
                echo "clock_stepped_back process start is ${delta}s in this box's future"
                return 0
            fi ;;
    esac
    newest="$(git -C "$repo" log -1 --format=%ct 2>/dev/null)"
    case "${newest:-}" in
        ""|*[!0-9]*) return 0 ;;
    esac
    if [ "$newest" -gt "$(( now + MF_CLOCK_TOL_S ))" ]; then
        delta=$(( newest - now ))
        echo "clock_behind newest commit is dated ${delta}s in this box's future"
        return 0
    fi
    return 0
}
