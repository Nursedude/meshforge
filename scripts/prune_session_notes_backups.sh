#!/usr/bin/env bash
# prune_session_notes_backups.sh — delete stale hand-made session-notes
# backups, but ONLY ones whose content is provably already preserved.
#
# WHY: sessions hand-copy the handoff doc before editing it
# (`cp notes notes.md.bak-$(date ...)`) and nothing ever pruned those.
# Measured 2026-09-17 on the dev box: 21 files, 2.2 MB, in ~/.claude/plans.
# rotate_session_notes.sh prunes only ITS OWN backups, under a different
# directory and a stricter name shape, and only when a human rotates.
#
# ⚠️ THE SAFETY PROPERTY THIS SCRIPT EXISTS FOR — read before editing.
# An age-based or count-based prune of this pile is DESTRUCTIVE. On
# 2026-09-17 those 21 backups carried **22 sections that were in NEITHER
# the live notes NOR the archive**: hand-edits during the 09-11..09-14
# network incident trimmed sections without ever rotating them, so the
# only surviving copy was the backup. A `find -mtime +N -delete` would
# have destroyed ~6 sessions' handoffs and nothing would have reported it.
#
# So the gate here is COVERAGE, not age: a backup is deletable only when
# EVERY "## " section heading it carries is already present in the live
# notes or in an archive beside them. An uncovered backup is EVIDENCE —
# it is kept, and it is reported LOUDLY (honest_failure_modes #2:
# unobservable/unpreserved must never render as ordinary garbage).
# Age is a secondary gate only, so a session's fresh safety net survives.
#
# Usage:
#   scripts/prune_session_notes_backups.sh              # dry-run
#   scripts/prune_session_notes_backups.sh --apply      # delete covered ones
#   scripts/prune_session_notes_backups.sh --min-age 7  # older than 7d only
#
# Exit: 0 = nothing to do / planned / applied cleanly (also when there is no
#           notes file here — absent by design reads INERT, never an error).
#       1 = refused (bad input, unsafe state).
set -euo pipefail
export LC_ALL=C

MIN_AGE_DAYS="${SESSION_NOTES_BACKUP_MIN_AGE_DAYS:-3}"
APPLY=0
PLANS_DIR="${SESSION_NOTES_DIR:-${HOME}/.claude/plans}"

die() { printf 'refused: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --apply)    APPLY=1; shift ;;
        --min-age)  MIN_AGE_DAYS="${2:?--min-age needs a number of days}"; shift 2 ;;
        --dir)      PLANS_DIR="${2:?--dir needs a path}"; shift 2 ;;
        -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
        *)          die "unknown argument '$1'" ;;
    esac
done
case "$MIN_AGE_DAYS" in ''|*[!0-9]*) die "--min-age must be a non-negative integer, got '$MIN_AGE_DAYS'" ;; esac

[ -d "$PLANS_DIR" ] || { printf 'inert: no notes directory at %s\n' "$PLANS_DIR"; exit 0; }

# The live notes for THIS box. Absent (most fleet boxes) = inert, not a fault.
notes="$PLANS_DIR/gateway-session-notes-$(hostname | tr '[:upper:]' '[:lower:]').md"
[ -r "$notes" ] || { printf 'inert: no session notes at %s\n' "$notes"; exit 0; }

tmp="$(mktemp -d)"; trap 'rm -rf -- "$tmp"' EXIT
covered="$tmp/covered.txt"

# Coverage corpus = the live notes + EVERY archive beside them. Archives are
# append-only, so a heading found in any of them is preserved.
cat "$notes" > "$covered"
shopt -s nullglob
for a in "$PLANS_DIR"/*-archive-*.md; do cat "$a" >> "$covered"; done
grep '^## ' "$covered" | sed 's/[[:space:]]*$//' | sort -u > "$tmp/covered_heads.txt"

printf 'prune_session_notes_backups — %s\n\n' \
    "$([ "$APPLY" -eq 1 ] && echo 'APPLY' || echo 'DRY-RUN (no files deleted)')"
printf '  dir       %s\n' "$PLANS_DIR"
printf '  notes     %s\n' "$(basename "$notes")"
printf '  corpus    %s heading(s) preserved in notes + archive(s)\n' "$(wc -l < "$tmp/covered_heads.txt")"
printf '  min age   %s day(s)\n\n' "$MIN_AGE_DAYS"

deletable=0; kept_young=0; kept_orphan=0; bytes=0; orphan_names=""
for f in "$PLANS_DIR"/*.md.bak*; do
    [ -f "$f" ] || continue
    # Never select the live notes or an archive, whatever they are named.
    [ "$f" = "$notes" ] && continue
    case "$(basename "$f")" in *-archive-*) continue ;; esac

    b="$(basename "$f")"
    sz="$(wc -c < "$f")"

    # Orphan check FIRST: a young backup is kept either way, but an orphan
    # must be reported even while it is young, or the finding waits N days.
    orphans=0
    while IFS= read -r h; do
        [ -n "$h" ] || continue
        grep -Fxq -- "$h" "$tmp/covered_heads.txt" || orphans=$((orphans + 1))
    done < <(grep '^## ' "$f" | sed 's/[[:space:]]*$//' | sort -u)

    if [ "$orphans" -gt 0 ]; then
        kept_orphan=$((kept_orphan + 1))
        orphan_names="${orphan_names}${b} (${orphans} section(s))"$'\n'
        printf '  KEEP-ORPHAN  %8s B  %s  — %s unpreserved section(s)\n' "$sz" "$b" "$orphans"
        continue
    fi
    if [ -n "$(find "$f" -maxdepth 0 -mtime -"$MIN_AGE_DAYS" 2>/dev/null)" ]; then
        kept_young=$((kept_young + 1))
        printf '  KEEP-YOUNG   %8s B  %s\n' "$sz" "$b"
        continue
    fi

    deletable=$((deletable + 1)); bytes=$((bytes + sz))
    if [ "$APPLY" -eq 1 ]; then
        if rm -f -- "$f"; then printf '  DELETED      %8s B  %s\n' "$sz" "$b"
        else printf '  ⚠️  could not delete %s (kept)\n' "$b"; fi
    else
        printf '  WOULD-DELETE %8s B  %s\n' "$sz" "$b"
    fi
done

printf '\n  %s %s backup(s), %s B\n' \
    "$([ "$APPLY" -eq 1 ] && echo 'deleted' || echo 'deletable:')" "$deletable" "$bytes"
[ "$kept_young" -gt 0 ] && printf '  kept %s younger than %sd\n' "$kept_young" "$MIN_AGE_DAYS"

if [ "$kept_orphan" -gt 0 ]; then
    printf '\n  ⚠️  %s backup(s) carry sections preserved NOWHERE ELSE and were KEPT.\n' "$kept_orphan"
    printf '      These are the only surviving copy. Do NOT delete them by hand —\n'
    printf '      salvage into the archive first, then re-run:\n'
    printf '%s' "$orphan_names" | sed 's/^/        /'
fi
[ "$APPLY" -eq 1 ] || printf '\nDry run only. Re-run with --apply to delete.\n'
exit 0
