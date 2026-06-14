#!/usr/bin/env bash
# Fleet ntfy push — the SINGLE source of truth for sending an ntfy notification
# from a shell publisher. Centralizes the topic + (optional) auth token in ONE
# place so a future Pro/auth migration is a one-file change, not N inline curls
# drifting apart (the "two consumers, one constant" trap — honest_failure_modes
# #5). Mirror for Python publishers: mini-dudeai's NtfyAction (src/mini_dudeai/
# actions/ntfy.py) carries the equivalent token support.
#
# Usage:  fleet_ntfy_push.sh <title> <priority> <tags> <message>
#   tags = comma-separated ntfy tags (may be empty).
#
# Topic: $MESHFORGE_NTFY_TOPIC (drills point this at a throwaway topic) else
#        ~/.config/fleet_push_topic. No topic configured -> no-op exit 0.
# Auth:  $MESHFORGE_NTFY_TOKEN else ~/.config/fleet_push_token. OPTIONAL — when
#        absent the request is unauthenticated, byte-for-byte the free-tier
#        behavior today. Set it (and reserve the topic) only when you go Pro.
#
# Best-effort: never hard-fails the caller (paging is advisory). Exit 0 always.
set -uo pipefail

TITLE="${1:-}"
PRIORITY="${2:-default}"
TAGS="${3:-}"
MESSAGE="${4:-}"

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
[ -n "$TOPIC" ] || exit 0   # nothing to send to — same as the old `[ -n "$NTFY_TOPIC" ] ||`

TOKEN="${MESHFORGE_NTFY_TOKEN:-$(cat "$HOME/.config/fleet_push_token" 2>/dev/null)}"

# Build args in an array so a conditional quoted header can't word-split.
args=( -s --max-time 12 -H "Title: $TITLE" -H "Priority: $PRIORITY" )
[ -n "$TAGS" ]  && args+=( -H "Tags: $TAGS" )
[ -n "$TOKEN" ] && args+=( -H "Authorization: Bearer $TOKEN" )
args+=( --data-raw "$MESSAGE" "https://ntfy.sh/$TOPIC" )

curl "${args[@]}" >/dev/null 2>&1 || true
exit 0
