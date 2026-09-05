#!/usr/bin/env bash
# Fleet ntfy push — the SINGLE source of truth for sending an ntfy notification
# from a shell publisher. Centralizes the topic + (optional) auth token in ONE
# place so a future Pro/auth migration is a one-file change, not N inline curls
# drifting apart (the "two consumers, one constant" trap — honest_failure_modes
# #5). Mirror for Python publishers: mini-dudeai's NtfyAction (src/mini_dudeai/
# actions/ntfy.py) carries the equivalent token support.
#
# Usage:  fleet_ntfy_push.sh <title> <priority> <tags> <message>
#         fleet_ntfy_push.sh --check        # read the witness, see below
#   tags = comma-separated ntfy tags (may be empty).
#
# Topic: $MESHFORGE_NTFY_TOPIC (drills point this at a throwaway topic) else
#        ~/.config/fleet_push_topic. No topic configured -> no-op exit 0.
# Auth:  $MESHFORGE_NTFY_TOKEN else ~/.config/fleet_push_token. OPTIONAL — when
#        absent the request is unauthenticated, byte-for-byte the free-tier
#        behavior today. Set it (and reserve the topic) only when you go Pro.
#
# Best-effort: never hard-fails the caller (paging is advisory). A send exits 0
# ALWAYS, including when the send failed — so the exit code is NOT evidence.
# The witness below is.
#
# ── THE WITNESS (2026-09-05) ────────────────────────────────────────────────
# WHY: until today this script was `curl ... >/dev/null 2>&1 || true; exit 0`.
# A failed page and a successful page left IDENTICAL traces — none. No log, no
# counter, no exit code. That is honest_failure_modes #9 (every swallow gets a
# witness) with nothing to show, and it sat under the whole alerting spine.
#
# It matters because fleet_ntfy_loopback.sh — the 2-hourly heartbeat that
# "proves alerting works" — publishes with its OWN curl and never calls this
# script. So a green loopback vouched for the loopback's path, not this one,
# while bot_deaf_check, watchdog_runner and watchdog_probes_env all page
# THROUGH HERE. If this path broke, every instrument stayed green and the
# operator found out by noticing silence (which is exactly how it came up).
#
# So every attempt now records its outcome to $STATE, and `--check` reads it
# back. Deliberately narrow claims:
#   ok       ntfy.sh ACCEPTED the publish (HTTP 2xx). NOT "the phone got it" —
#            the 2026-06-14→17 dark incident had every publish return 200 while
#            the phone got nothing. Receipt is the loopback's job; the phone is
#            fleet_ntfy_ack.sh's job. This witness claims only what it saw.
#   failed   curl errored, or the server answered non-2xx.
#   no_topic no topic configured — this box does not page at all. INERT, a
#            different claim from "sent fine" and from "failed" (an absent
#            organ must never read as an observation that succeeded).
set -uo pipefail

# State path. An explicit override always wins. Otherwise: a DRILL (which is
# exactly what $MESHFORGE_NTFY_TOPIC signals — see the Topic note above) gets
# its own file, so a successful send to a throwaway topic can never be counted
# as evidence that the REAL topic works. Without this split the witness could
# report green for a channel it never touched.
if [ -n "${MESHFORGE_NTFY_PUSH_STATE:-}" ]; then
    STATE="$MESHFORGE_NTFY_PUSH_STATE"
elif [ -n "${MESHFORGE_NTFY_TOPIC:-}" ]; then
    STATE="$HOME/.local/state/meshforge/ntfy_push_state.drill.json"
else
    STATE="$HOME/.local/state/meshforge/ntfy_push_state.json"
fi
# Consecutive failures before --check calls it FAIL. One failed page is a
# transient (ntfy.sh blips); a run of them is the channel.
FAIL_AFTER="${MESHFORGE_NTFY_PUSH_FAIL_AFTER:-3}"

# ── --check: the READER. A witness with no reader is a writer-with-no-reader,
# the defect class that hid the node-cache service_type drop. Wire it like any
# other cron:
#   */30 * * * * /opt/meshforge/scripts/fleet_ntfy_push.sh --check \
#       >"$HOME/.local/state/meshforge/cron_out/ntfy_push_health.out" 2>&1; \
#       /opt/meshforge/scripts/cron_verdict.sh ntfy_push_health $?
# Exit: 0 healthy/inert/nothing-sent-yet, 1 failing, 2 unobservable.
if [ "${1:-}" = "--check" ]; then
    if [ ! -f "$STATE" ]; then
        # Never sent, or never sent since this witness existed. NOT a failure:
        # a quiet fleet legitimately pages nothing for days. Also not a pass
        # for the channel — say which, rather than implying health.
        echo "ntfy_push: no sends recorded yet (nothing has paged through this path)"
        exit 0
    fi
    if ! read_out=$(cat "$STATE" 2>/dev/null) || [ -z "$read_out" ]; then
        echo "ntfy_push: state UNREADABLE at $STATE — send health unobservable" >&2
        exit 2
    fi
    # python3 parses it; a corrupt/truncated file is unobservable, never green.
    python3 - "$STATE" "$FAIL_AFTER" <<'PY'
import json, sys, time
path, fail_after = sys.argv[1], int(sys.argv[2])
try:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
except Exception as exc:
    print(f"ntfy_push: state unparseable ({type(exc).__name__}) — "
          f"send health unobservable", file=sys.stderr)
    raise SystemExit(2)
status = d.get("last_status")
cf = int(d.get("consecutive_failures", 0) or 0)
ok, fail = d.get("sends_ok", 0), d.get("sends_failed", 0)
last = d.get("last_attempt_ts") or 0
try:
    age = f"{(time.time() - float(last)) / 3600:.1f}h ago"
except Exception:
    age = "unknown"
if status == "no_topic":
    print("ntfy_push: INERT — no topic configured, this box does not page")
    raise SystemExit(0)
if cf >= fail_after:
    print(f"ntfy_push: FAILING — {cf} consecutive publish failures "
          f"(last attempt {age}); last error: {d.get('last_error') or '?'} "
          f"| totals ok={ok} failed={fail}", file=sys.stderr)
    raise SystemExit(1)
if status == "failed":
    print(f"ntfy_push: CONCERN — last publish failed ({cf}/{fail_after} "
          f"consecutively, under the alarm threshold); "
          f"last error: {d.get('last_error') or '?'} | ok={ok} failed={fail}")
    raise SystemExit(0)
print(f"ntfy_push: publish path accepted at {age} "
      f"(HTTP {d.get('last_http_code') or '?'}) | ok={ok} failed={fail}. "
      f"NOTE: server ACCEPTED — delivery to the phone is the loopback's and "
      f"fleet_ntfy_ack's claim, not this one")
PY
    exit $?
fi

TITLE="${1:-}"
PRIORITY="${2:-default}"
TAGS="${3:-}"
MESSAGE="${4:-}"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# record <status> <http_code> <error>  — never fails the caller.
record() {
    local status="$1" code="${2:-}" err="${3:-}"
    local dir; dir="$(dirname "$STATE")"
    mkdir -p "$dir" 2>/dev/null || return 0
    # Serialize: several publishers (watchdog, bot_deaf_check, mini) can fire
    # at once, and a read-modify-write of counters would interleave
    # (honest_failure_modes #8 — exclude or merge, never interleave). flock
    # EXCLUDES; if the lock cannot be taken in 5s we skip the counter update
    # rather than corrupt it. Skipping is the honest option: a witness that
    # lies about its counts is worse than one that occasionally abstains.
    if command -v flock >/dev/null 2>&1; then
        flock -w 5 "$dir/.ntfy_push.lock" \
            python3 "$SELF_DIR/_ntfy_push_record.py" \
            "$STATE" "$status" "$code" "$err" "$TITLE" 2>/dev/null || true
    else
        python3 "$SELF_DIR/_ntfy_push_record.py" \
            "$STATE" "$status" "$code" "$err" "$TITLE" 2>/dev/null || true
    fi
    return 0
}

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
if [ -z "$TOPIC" ]; then
    # No topic is INERT, not success. Recorded so --check can say "this box
    # does not page" instead of implying a healthy channel.
    record no_topic "" "no topic configured"
    exit 0
fi

TOKEN="${MESHFORGE_NTFY_TOKEN:-$(cat "$HOME/.config/fleet_push_token" 2>/dev/null)}"

# Build args in an array so a conditional quoted header can't word-split.
# -w '%{http_code}' + -o /dev/null: curl's EXIT CODE alone is not the outcome —
# it is 0 for a 500 or a 403 too. The server's status is the thing that says
# whether the publish was accepted, so both are checked.
args=( -s -o /dev/null -w '%{http_code}' --max-time 12
       -H "Title: $TITLE" -H "Priority: $PRIORITY" )
[ -n "$TAGS" ]  && args+=( -H "Tags: $TAGS" )
[ -n "$TOKEN" ] && args+=( -H "Authorization: Bearer $TOKEN" )
args+=( --data-raw "$MESSAGE" "https://ntfy.sh/$TOPIC" )

http_code="$(curl "${args[@]}" 2>/dev/null)"; curl_rc=$?

if [ "$curl_rc" -ne 0 ]; then
    record failed "$http_code" "curl exit $curl_rc"
elif [ -z "$http_code" ] || [ "${http_code#2}" = "$http_code" ]; then
    # Empty, or a code not starting with 2 — accepted means 2xx, nothing else.
    record failed "$http_code" "server returned HTTP ${http_code:-none}"
else
    record ok "$http_code" ""
fi

# Paging stays advisory: the caller is never failed by a page that did not
# land. The witness is what carries that fact forward.
exit 0
