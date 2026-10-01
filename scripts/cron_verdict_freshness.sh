#!/bin/bash
# cron_verdict_freshness.sh — flags any cron whose last verdict (or data file,
# for self-logging organs) is older than expected. The other half of the
# verdict convention: a MISSING verdict surfaces instead of looking like
# health. Hourly on the manager box; alerts -> ~/fleet_alerts.log + ntfy
# (re-alert per item at most every REALERT_S via ~/.cron_freshness_state).
#
# Moved into the repo 2026-09-30 from an untracked ~/cron_verdict_freshness.sh:
# it wrote the log that src/monitoring/meshforge_digest.py reads, yet nothing
# versioned it. Operator values (which crons, which boxes, what limits) live in
# a CONFIG, never here (MF014):
#
#   ~/.config/meshforge/cron_freshness.conf   (KEY=value lines; read, never sourced)
#     LOCAL_LABEL=<name for this box in alerts>        default: short hostname
#     VERDICT_MANIFEST=<name:max_min name:max_min ...> crons judged from ~/cron_verdicts.log
#     POWER_LOCAL_MAX_MIN=<min>                        ~/power_history.log age here (blank = skip)
#     POWER_REMOTE_BOXES=<ssh-name ...>                same file on these boxes, via ssh
#     POWER_REMOTE_MAX_MIN=<min>                       default 10
#
# No config where cron runs this = a misconfiguration: verdict CONCERN
# "nothing checked", never OK (an instrument that measured nothing must not
# report health).
#
# ⚠️ OVERLAP, measured 2026-09-30, NOT resolved: the watchdog's
# `cron_verdict_stale` (src/utils/watchdog_probes_cron.py) judges EVERY
# cron_verdict-wired cron from the crontab (3x cadence, 2h floor) and read
# `clean` live. This script's manifest is a hand-kept subset with TIGHTER
# limits and a direct ntfy page. Whether it is redundant is UNKNOWN (mini's
# history starts 09-04; the verdict log is trimmed) — see deferred ledger
# `cron-freshness-overlap`. The power_history leg is unique to this script.
#
# Test seams (env): CRON_FRESHNESS_HOME, CRON_FRESHNESS_CONF, CRON_FRESHNESS_NTFY,
# CRON_FRESHNESS_VERDICT, CRON_FRESHNESS_SSH, CRON_FRESHNESS_NOW.
export PATH=/usr/local/bin:/usr/bin:/bin
# readlink -f: a symlink to this script must still find ITS repo, or the
# verdict and the page both point at files that do not exist (readers).
REPO_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
H="${CRON_FRESHNESS_HOME:-$HOME}"
CONF="${CRON_FRESHNESS_CONF:-$H/.config/meshforge/cron_freshness.conf}"
NTFY="${CRON_FRESHNESS_NTFY:-$REPO_DIR/scripts/fleet_ntfy_push.sh}"
VERDICT="${CRON_FRESHNESS_VERDICT:-$REPO_DIR/scripts/cron_verdict.sh}"
SSH="${CRON_FRESHNESS_SSH:-ssh}"
ALERTS="$H/fleet_alerts.log"
STATE="$H/.cron_freshness_state"
NOW="${CRON_FRESHNESS_NOW:-$(date +%s)}"
case "$NOW" in ''|*[!0-9]*) NOW=$(date +%s) ;; esac
TS=$(date -u -d "@$NOW" +%Y-%m-%dT%H:%M:%SZ)
NL=$'\n'
# 6h. meshforge_digest.py's CRON_FRESHNESS_WINDOW_S = this + 2h (the hourly
# cadence plus one late/skipped run of slack); tests pin the two together.
REALERT_S=21600

concern() { "$VERDICT" cron_freshness CONCERN "$1"; exit 0; }

# --- config: known keys only, never `source`d ------------------------------
LOCAL_LABEL=""; VERDICT_MANIFEST=""; POWER_LOCAL_MAX_MIN=""
POWER_REMOTE_BOXES=""; POWER_REMOTE_MAX_MIN="10"
[ -r "$CONF" ] || concern "no config at $CONF — nothing checked"
while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"                                   # CRLF-edited file
    case "$line" in ''|[[:space:]]*'#'*|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) [ -z "${line//[[:space:]]/}" ] && continue
                              concern "line without '=' in $CONF: '$line'" ;; esac
    key="${line%%=*}"; val="${line#*=}"
    key="${key//[[:space:]]/}"
    val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"   # trim
    case "$val" in \"*\"|\'*\') val="${val:1:${#val}-2}" ;; esac                # unquote
    case "$key" in
        LOCAL_LABEL) LOCAL_LABEL="$val" ;;
        VERDICT_MANIFEST) VERDICT_MANIFEST="$val" ;;
        POWER_LOCAL_MAX_MIN) POWER_LOCAL_MAX_MIN="$val" ;;
        POWER_REMOTE_BOXES) POWER_REMOTE_BOXES="$val" ;;
        POWER_REMOTE_MAX_MIN) POWER_REMOTE_MAX_MIN="$val" ;;
        *) concern "unknown key '$key' in $CONF — refusing to guess" ;;
    esac
done < "$CONF"
[ -n "$LOCAL_LABEL" ] || LOCAL_LABEL="${HOSTNAME%%.*}"
# State keys are the FIRST field of a "<key> <epoch>" line: a label with
# whitespace would never match its own key — every run re-paged and the file
# grew a copy per run (reader T, reproduced). Refuse it.
case "$LOCAL_LABEL" in *[[:space:]]*) concern "LOCAL_LABEL '$LOCAL_LABEL' in $CONF contains whitespace" ;; esac
# Each limit is validated as a WHOLE string: a blank or "5 9" once
# word-split to nothing / two numbers, passed, and made `-gt` error →
# the else branch read every file FRESH (readers, reproduced).
case "$POWER_REMOTE_MAX_MIN" in ''|*[!0-9]*) concern "POWER_REMOTE_MAX_MIN '$POWER_REMOTE_MAX_MIN' in $CONF is not a whole number" ;; esac
if [ -n "$POWER_LOCAL_MAX_MIN" ]; then
    case "$POWER_LOCAL_MAX_MIN" in *[!0-9]*) concern "POWER_LOCAL_MAX_MIN '$POWER_LOCAL_MAX_MIN' in $CONF is not a whole number" ;; esac
fi
[ -n "$VERDICT_MANIFEST$POWER_LOCAL_MAX_MIN$POWER_REMOTE_BOXES" ] || \
    concern "$CONF configures nothing to check"

# --- one run at a time: overlapping runs would interleave the state file ----
exec 9>>"$STATE.lock"
flock -n 9 || { echo "cron_verdict_freshness: previous run still holds $STATE.lock" >&2; exit 0; }
touch "$STATE"

STALE=""      # the re-alert-GATED notification list
OBSERVED=""   # every stale item seen THIS run, ungated — the verdict stamps from this
note_stale() {  # item detail
    OBSERVED="$OBSERVED$NL$1: $2"
    # LAST value for the key, digits only: a duplicated key (power loss, hand
    # edit) once made `last` two lines → arithmetic error → never paged again.
    last=$(awk -v k="$1" '$1==k {v=$2} END {print v}' "$STATE" 2>/dev/null)
    case "$last" in *[!0-9]*) last="" ;; esac
    if [ -z "$last" ] || [ $((NOW - last)) -ge $REALERT_S ]; then
        STALE="$STALE$NL$1: $2"
        local tmp; tmp=$(mktemp "$STATE.XXXXXX") || return
        awk -v k="$1" '$1!=k' "$STATE" > "$tmp" && echo "$1 $NOW" >> "$tmp" && mv "$tmp" "$STATE" \
            || rm -f "$tmp"
    fi
}

clear_state() {  # item recovered -> next staleness alerts immediately
    # Exact key match (awk, not a grep regex — '.' in a name matched anything),
    # and write ONLY on success: the old `grep -v … && mv` never recorded a
    # recovery when the item was the ONLY line (grep exit 1), and an
    # unconditional mv would truncate the file on a read ERROR.
    local tmp; tmp=$(mktemp "$STATE.XXXXXX") || return
    if awk -v k="$1" '$1!=k' "$STATE" > "$tmp"; then mv "$tmp" "$STATE"; else rm -f "$tmp"; fi
}

check_verdicts() {  # host verdict_log_content "name:max_min name:max_min ..."
    local host="$1" content="$2" manifest="$3"
    for spec in $manifest; do
        name="${spec%%:*}"; max_min="${spec##*:}"
        case "$max_min" in ''|*[!0-9]*)
            note_stale "$host/$name" "bad manifest entry '$spec' (want name:minutes)"; continue ;;
        esac
        last_ts=$(printf '%s\n' "$content" | awk -v n="$name" '$2==n {ts=$1} END {print ts}')
        if [ -z "$last_ts" ]; then
            note_stale "$host/$name" "no verdict ever recorded"
            continue
        fi
        last_s=$(date -d "$last_ts" +%s 2>/dev/null || echo 0)
        age_min=$(( (NOW - last_s) / 60 ))
        if [ "$age_min" -lt -5 ]; then
            # honest_failure_modes #6: after a clock step back (RTC-less Pi,
            # fake-hwclock) a NEGATIVE age is never -gt max, so every cron read
            # fresh until real time caught up. Its age is unknowable — say so.
            note_stale "$host/$name" "last verdict dated $(( -age_min ))m in the FUTURE — clock stepped? age unknowable"
        elif [ "$age_min" -gt "$max_min" ]; then
            note_stale "$host/$name" "last verdict ${age_min}m ago (max ${max_min}m)"
        else
            clear_state "$host/$name"
        fi
        # FAIL in the latest verdict is also worth surfacing once…
        last_status=$(printf '%s\n' "$content" | awk -v n="$name" '$2==n {s=$3} END {print s}')
        # …and a verdict that is no longer FAIL clears it, so the NEXT failure
        # pages at once (it used to stay gated: /FAIL items were never cleared).
        case "$last_status" in
            FAIL*) note_stale "$host/$name/FAIL" "latest verdict: $last_status" ;;
            *)     clear_state "$host/$name/FAIL" ;;
        esac
    done
}

check_file_age() {  # host path max_min mtime_epoch
    local host="$1" path="$2" max_min="$3" mt="$4"
    if [ -z "$mt" ] || [ "$mt" = "0" ]; then
        note_stale "$host/$path" "file missing/unreadable"
        return
    fi
    age_min=$(( (NOW - mt) / 60 ))
    if [ "$age_min" -lt -5 ]; then
        note_stale "$host/$path" "data file dated $(( -age_min ))m in the FUTURE — clock stepped? age unknowable"
    elif [ "$age_min" -gt "$max_min" ]; then
        note_stale "$host/$path" "data file ${age_min}m stale (max ${max_min}m)"
    else
        clear_state "$host/$path"
    fi
}

# ── this box ─────────────────────────────────────────────────────────────
[ -n "$VERDICT_MANIFEST" ] && \
    check_verdicts "$LOCAL_LABEL" "$(cat "$H/cron_verdicts.log" 2>/dev/null)" "$VERDICT_MANIFEST"
[ -n "$POWER_LOCAL_MAX_MIN" ] && \
    check_file_age "$LOCAL_LABEL" "power_history.log" "$POWER_LOCAL_MAX_MIN" \
        "$(stat -c %Y "$H/power_history.log" 2>/dev/null || echo 0)"

# ── remote boxes (power_capture's data file IS its verdict) ─────────────
remote_n=0; unanswered=""
for box in $POWER_REMOTE_BOXES; do
    remote_n=$((remote_n + 1))
    # The box ANSWERS with a number, or MISSING when it has no data file —
    # a box that answered "no file" is a dead power_capture, NOT an
    # unreachable box (reader T: it was filed as unanswered and read OK).
    # Last line only, so a login banner on stdout cannot pose as the answer.
    mt=$(timeout 25 "$SSH" -o ConnectTimeout=8 -o BatchMode=yes "$box" \
        "stat -c %Y ~/power_history.log 2>/dev/null || echo MISSING" 2>/dev/null | tail -n 1)
    case "$mt" in
        MISSING) check_file_age "$box" "power_history.log" "$POWER_REMOTE_MAX_MIN" "0" ;;
        ''|*[!0-9]*)
            # One unreachable box is fleet_offline_check's job — no page from us.
            # But it is never silent: it rides the verdict's evidence, and a
            # misspelled name (it never answers) cannot hide behind it forever.
            unanswered="$unanswered $box" ;;
        *) check_file_age "$box" "power_history.log" "$POWER_REMOTE_MAX_MIN" "$mt" ;;
    esac
done

if [ -n "$STALE" ]; then
    printf '%s CRON-FRESHNESS STALE:%s\n' "$TS" "$STALE" >> "$ALERTS"
    "$NTFY" "fleet cron gone silent" "high" "hourglass" "$(printf '%s' "$STALE" | head -c 800)"
fi
# Status FAIL when anything is stale (2026-09-07): this line stamped OK
# unconditionally for its whole life, so the harness_audit leg that read it had
# one outcome and mini never saw a stale cron through this channel.
# The verdict is derived from OBSERVED (every stale item this run), never from
# $STALE, which is the 6h re-alert-gated NOTIFICATION list: stamping from the
# gated list made a persistently silent cron read FAIL one hour in six and
# OK the other five (review 2026-09-07 — a rate limiter may withhold a
# notification, never the record).
_fs_n=$(printf '%s' "$OBSERVED" | grep -c ':')
_fs_ev="$_fs_n stale"
[ -n "$unanswered" ] && _fs_ev="$_fs_ev; remote unanswered:$unanswered"
n_unanswered=$(printf '%s' "$unanswered" | wc -w)
if [ -n "$OBSERVED" ]; then _fs_st=FAIL
elif [ "$remote_n" -gt 0 ] && [ "$n_unanswered" -eq "$remote_n" ]; then
    _fs_st=CONCERN; _fs_ev="$_fs_ev — NO remote box answered (of $remote_n): the remote leg measured nothing"
else _fs_st=OK; fi
"$VERDICT" cron_freshness "$_fs_st" "$_fs_ev"
