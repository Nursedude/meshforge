#!/usr/bin/env bash
# Fleet alert email — the SECOND escalation channel (Phase 1 of the ntfy
# receipt-heartbeat arc, .claude/plans/ntfy_receipt_heartbeat_2026_06_17.md).
# Sends a plain-text alert via Gmail SMTP using curl, so a box can still reach
# the operator when the ntfy path is dark — the 2026-06-14→17 "send ≠ receipt"
# incident, where every ntfy publish returned HTTP 200 while the phone got
# nothing for four days. Sibling of fleet_ntfy_push.sh: SAME 4-arg signature so
# a caller can page both channels symmetrically.
#
# Usage:  fleet_alert_email.sh <title> <priority> <tags> <message>
#   Mirrors fleet_ntfy_push.sh. priority/tags fold into the subject + body.
#
# Creds:  ~/.config/fleet_email_creds  (chmod 600 — NEVER commit it; live-secret
#         rule, feedback_never_live_secrets_as_test_vectors). KEY=VALUE lines:
#           FLEET_EMAIL_FROM          dedicated Gmail address (the SMTP auth user)
#           FLEET_EMAIL_APP_PASSWORD  Gmail app password (spaces are ignored)
#           FLEET_EMAIL_TO            destination(s), comma-separated
#         Env overrides win:  $FLEET_EMAIL_FROM / _APP_PASSWORD / _TO / _CREDS.
#
# Contract (honest_failure_modes #9 — every swallow leaves a witness):
#   * No creds configured   -> intentional, SILENT no-op, exit 0 (parity with
#                              ntfy when no topic is set: a box without creds
#                              simply doesn't email).
#   * Configured but broken  -> LOUD: stderr + exit 1 (curl missing, no valid
#                              recipient, or the send fails). A backbone channel
#                              that fails silently is the exact bug this whole
#                              arc exists to kill — so it must NOT be hidden.
set -uo pipefail

TITLE="${1:-}"
PRIORITY="${2:-default}"
TAGS="${3:-}"
MESSAGE="${4:-}"

CREDS="${FLEET_EMAIL_CREDS:-$HOME/.config/fleet_email_creds}"

# Read a KEY=VALUE line WITHOUT sourcing the file — never execute operator data.
# Tolerant of `export `, spaces around `=`, and surrounding quotes/whitespace.
_cred() {
    local v
    v="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" "$CREDS" 2>/dev/null \
         | head -n1 | sed -E 's/^[^=]*=//')"
    v="${v#"${v%%[![:space:]]*}"}"   # strip leading whitespace
    v="${v%"${v##*[![:space:]]}"}"   # strip trailing whitespace
    v="${v%\"}"; v="${v#\"}"          # strip surrounding double quotes
    v="${v%\'}"; v="${v#\'}"          # strip surrounding single quotes
    printf '%s' "$v"
}

FROM="${FLEET_EMAIL_FROM:-$(_cred FLEET_EMAIL_FROM)}"
PASS="${FLEET_EMAIL_APP_PASSWORD:-$(_cred FLEET_EMAIL_APP_PASSWORD)}"
TO="${FLEET_EMAIL_TO:-$(_cred FLEET_EMAIL_TO)}"

# Gmail shows app passwords as 4x4 space-separated groups; SMTP AUTH wants them
# unspaced — be forgiving about a copy-pasted "abcd efgh ijkl mnop".
PASS="${PASS// /}"

# No creds configured -> intentional, silent no-op (parity with ntfy/no-topic).
if [ -z "$FROM" ] || [ -z "$PASS" ] || [ -z "$TO" ]; then
    exit 0
fi

# Configured but the tool is missing -> LOUD witness, never silent.
if ! command -v curl >/dev/null 2>&1; then
    echo "fleet_alert_email: curl not found but email creds ARE configured — channel dark" >&2
    exit 1
fi

HOST="$(hostname 2>/dev/null || echo unknown)"

# Header-injection guard: no CR/LF allowed in any header field.
_clean() { printf '%s' "$1" | tr -d '\r\n'; }
TITLE="$(_clean "$TITLE")"
PRIORITY="$(_clean "$PRIORITY")"
TAGS="$(_clean "$TAGS")"

# Subject carries host + priority for at-a-glance inbox triage.
if [ -n "$PRIORITY" ] && [ "$PRIORITY" != "default" ]; then
    SUBJECT="[$HOST][$PRIORITY] ${TITLE:-fleet alert}"
else
    SUBJECT="[$HOST] ${TITLE:-fleet alert}"
fi

DATE="$(date -R 2>/dev/null || date)"

# Comma-separated recipients -> one --mail-rcpt each + a joined To: header.
RCPT_ARGS=()
TO_HEADER=""
IFS=',' read -ra _addrs <<< "$TO"
for a in "${_addrs[@]}"; do
    a="${a#"${a%%[![:space:]]*}"}"; a="${a%"${a##*[![:space:]]}"}"
    [ -n "$a" ] || continue
    RCPT_ARGS+=( --mail-rcpt "$a" )
    TO_HEADER="${TO_HEADER:+$TO_HEADER, }$a"
done
if [ ${#RCPT_ARGS[@]} -eq 0 ]; then
    echo "fleet_alert_email: no valid recipient in FLEET_EMAIL_TO -> channel dark" >&2
    exit 1
fi

# RFC 5322 message, CRLF line endings (SMTP wants them; Gmail is lenient anyway).
_build_message() {
    printf 'From: %s\r\n' "$FROM"
    printf 'To: %s\r\n' "$TO_HEADER"
    printf 'Subject: %s\r\n' "$SUBJECT"
    printf 'Date: %s\r\n' "$DATE"
    printf 'Content-Type: text/plain; charset=UTF-8\r\n'
    printf '\r\n'
    printf 'host:      %s\r\n' "$HOST"
    printf 'priority:  %s\r\n' "$PRIORITY"
    [ -n "$TAGS" ] && printf 'tags:      %s\r\n' "$TAGS"
    printf 'sent:      %s\r\n' "$DATE"
    printf '\r\n'
    printf '%s\r\n' "$MESSAGE"
}

_err="$(mktemp "${TMPDIR:-/tmp}/.fleet_alert_email.XXXXXX")"
if _build_message | curl -s --ssl-reqd --max-time 30 \
        --url 'smtps://smtp.gmail.com:465' \
        --user "$FROM:$PASS" \
        --mail-from "$FROM" \
        "${RCPT_ARGS[@]}" \
        --upload-file - 2>"$_err"; then
    rm -f "$_err"
    exit 0
else
    rc=$?
    echo "fleet_alert_email: send FAILED (curl exit $rc) -> $TO_HEADER" >&2
    sed 's/^/fleet_alert_email:   /' "$_err" >&2 2>/dev/null || true
    rm -f "$_err"
    exit 1
fi
