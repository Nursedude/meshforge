#!/usr/bin/env bash
# rotate_session_notes.sh — trim an oversized session-notes handoff doc by
# moving OLD sections into the half-year archive, newest sections stay put.
#
# WHY: harness_audit.sh fails its "session notes" leg above 80KB
# (harness_audit.sh:156). Until 2026-08-31 that leg DETECTED the overflow and
# nothing cured it — the 08-31 05:35 HST cron failed at 88028B and a human
# rotated by hand 33 minutes later. A detector with no cure makes the same
# class cost twice. This is the cure, deliberately HUMAN-INVOKED: the file's
# top section is "START HERE", and blind size-triggered content movement on a
# handoff doc loses the very context the doc exists to carry. Run it at
# session close, next to /memory-health. DO NOT wire it to cron.
#
# Safety properties (each one earned):
#   - DRY-RUN BY DEFAULT. --apply is required to move a single byte.
#   - POSITION IS NOT STALENESS. Sections whose heading matches STICKY_RE are
#     never rotated regardless of where they sit. The live "🔭 QUEUED" section
#     is at the BOTTOM of the file; a naive tail-drop would have eaten it.
#   - The archive is the NEWEST EXISTING one, not a date-derived name. On
#     2026-08-31 the live archive was still ...-archive-2026H1.md; deriving
#     "2026H2" would have split one body of content across two homes.
#   - Backups reserve their path atomically (set -C == O_CREAT|O_EXCL) and are
#     namespaced by the notes basename, which carries the hostname. Never
#     overwrite a backup (feedback_backup_destinations_must_be_namespaced).
#   - Writes are atomic (temp + mv), and --apply RE-DERIVES the result from
#     the files afterward instead of trusting that it did what it meant to.
#
# Usage:
#   scripts/rotate_session_notes.sh                 # dry-run, keep newest 4
#   scripts/rotate_session_notes.sh --keep 6        # dry-run, keep newest 6
#   scripts/rotate_session_notes.sh --apply         # actually rotate
#
# Exit: 0 = nothing to do, or rotation planned/applied cleanly.
#       1 = refused (bad input, unsafe state) or post-apply verification FAILED.
set -euo pipefail
export LC_ALL=C   # length() must count BYTES, not UTF-8 characters

KEEP=4
APPLY=0
# must track harness_audit.sh:156. Overridable as a TEST hook only —
# the gate-FAIL branch is otherwise undrillable while the real file is
# under 80KB, and an unexercised verification branch is not evidence
# (precedent: CRON_VERDICT_TS in cron_verdict.sh).
GATE_BYTES="${SESSION_NOTES_GATE_BYTES:-81920}"
NOTES_DEFAULT="${HOME}/.claude/plans/gateway-session-notes-$(hostname | tr '[:upper:]' '[:lower:]').md"
NOTES="${SESSION_NOTES:-$NOTES_DEFAULT}"
ARCHIVE=""
# A heading matching this is LIVE work and never rotates, wherever it sits.
STICKY_RE="${SESSION_NOTES_STICKY_RE:-QUEUED|OPEN|LIVE|NEXT SESSION|DO NOT ROTATE|Rotated to the archive}"

die() { printf 'refused: %s\n' "$1" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --apply)   APPLY=1; shift ;;
        --keep)    KEEP="${2:?--keep needs a number}"; shift 2 ;;
        --notes)   NOTES="${2:?--notes needs a path}"; shift 2 ;;
        --archive) ARCHIVE="${2:?--archive needs a path}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *)         die "unknown argument '$1'" ;;
    esac
done

case "$KEEP" in ''|*[!0-9]*) die "--keep must be a non-negative integer, got '$KEEP'" ;; esac
[ -r "$NOTES" ] || die "notes not readable: $NOTES"
[ -w "$NOTES" ] || die "notes not writable: $NOTES"
# A symlink would be REPLACED by the atomic mv, silently converting the link
# into a regular file and orphaning whatever it pointed at. Refuse rather than
# guess which side the operator meant.
if [ -L "$NOTES" ]; then
    die "notes is a symlink: $NOTES -> $(readlink "$NOTES") — the atomic replace would turn the LINK into a regular file; re-run with --notes pointing at the resolved path"
fi

# ── resolve the archive: newest EXISTING sibling wins over a derived name ────
stem="${NOTES%.md}"
if [ -z "$ARCHIVE" ]; then
    # shellcheck disable=SC2012  # -t ordering is the point; names are ours
    ARCHIVE="$(ls -1t "${stem}"-archive-*.md 2>/dev/null | head -1 || true)"
    if [ -z "$ARCHIVE" ]; then
        half=1; [ "$(date +%m)" -gt 6 ] && half=2
        ARCHIVE="${stem}-archive-$(date +%Y)H${half}.md"
        archive_note="(none existed — would CREATE)"
    else
        archive_note="(newest existing)"
    fi
else
    archive_note="(explicit --archive)"
fi
if [ -L "$ARCHIVE" ]; then
    die "archive is a symlink: $ARCHIVE -> $(readlink "$ARCHIVE") — the atomic replace would turn the LINK into a regular file; re-run with --archive pointing at the resolved path"
fi

notes_size="$(wc -c < "$NOTES")"
arch_size=0; [ -f "$ARCHIVE" ] && arch_size="$(wc -c < "$ARCHIVE")"

# ── index the sections ───────────────────────────────────────────────────────
# TSV: idx <TAB> startline <TAB> endline <TAB> bytes <TAB> sticky <TAB> heading
# A "## " line INSIDE a ``` fence is sample markdown, not a section boundary —
# these notes are full of pasted snippets. Splitting there would cut a section
# in half and move the wrong bytes (found in review 2026-08-31, latent: 0
# occurrences in the live files at the time, but code blocks are everywhere).
index="$(awk -v sticky="$STICKY_RE" '
    /^[ \t]*```/ { fence = !fence; if (fence) fence_line = NR; else fence_line = 0 }
    /^## / && !fence { sec++; start[sec]=NR; head[sec]=$0; if ($0 ~ sticky) st[sec]=1 }
    { if (sec > 0) { bytes[sec] += length($0)+1; last[sec]=NR } else pre = NR }
    END {
        print "FENCE\t" (fence ? fence_line : 0)
        print "PRE\t" pre+0
        for (i = 1; i <= sec; i++)
            printf "%d\t%d\t%d\t%d\t%d\t%s\n", i, start[i], last[i], bytes[i], st[i]+0, head[i]
    }' "$NOTES")"

fence_open="$(printf '%s\n' "$index" | awk -F'\t' '$1=="FENCE"{print $2}')"
# Unbalanced fence => every heading after it reads as "inside a fence" and the
# split silently moves the wrong bytes. Refuse loudly rather than absorb it.
[ "${fence_open:-0}" -eq 0 ] || die "unbalanced code fence opened at line ${fence_open} in $NOTES — close it and re-run (a heading inside an unclosed fence would split the file at the wrong place)"

pre_lines="$(printf '%s\n' "$index" | awk -F'\t' '$1=="PRE"{print $2}')"
sections="$(printf '%s\n' "$index" | awk -F'\t' '$1!="PRE" && $1!="FENCE"')"
n_sections="$(printf '%s' "$sections" | grep -c . || true)"

[ "$n_sections" -gt 0 ] || die "no '## ' sections found in $NOTES — nothing this tool understands"

# ── decide: keep the newest KEEP by position, plus every sticky section ──────
plan="$(printf '%s\n' "$sections" | awk -F'\t' -v keep="$KEEP" '
    { idx=$1; sticky=$5
      if (idx <= keep)      { verdict="KEEP";        reason="newest " keep }
      else if (sticky == 1) { verdict="KEEP-STICKY"; reason="live marker" }
      else                  { verdict="ROTATE";      reason="older than newest " keep }
      printf "%s\t%s\t%s\t%s\t%s\t%s\n", $1, verdict, $4, reason, $2 "-" $3, $6 }')"

rot_bytes="$(printf '%s\n' "$plan" | awk -F'\t' '$2=="ROTATE"{s+=$3} END{print s+0}')"
n_rotate="$(printf '%s\n' "$plan" | awk -F'\t' '$2=="ROTATE"' | grep -c . || true)"
after_size=$(( notes_size - rot_bytes ))

# ── report ───────────────────────────────────────────────────────────────────
mode="DRY-RUN (no files touched)"; [ "$APPLY" -eq 1 ] && mode="APPLY"
printf 'rotate_session_notes — %s\n\n' "$mode"
printf '  notes    %s  (%s B)\n' "$NOTES" "$notes_size"
printf '  archive  %s  (%s B) %s\n' "$ARCHIVE" "$arch_size" "$archive_note"
printf '  preamble %s line(s) kept · %s section(s) · keep newest %s + sticky\n\n' \
    "$pre_lines" "$n_sections" "$KEEP"

printf '  %-4s %-12s %9s  %s\n' '#' 'VERDICT' 'BYTES' 'HEADING'
printf '%s\n' "$plan" | awk -F'\t' '{
    h=$6; if (length(h) > 62) h = substr(h,1,59) "..."
    printf "  %-4s %-12s %9s  %s\n", $1, $2, $3, h }'

printf '\n  rotating %s section(s), %s B\n' "$n_rotate" "$rot_bytes"
printf '  notes: %s B -> %s B  (gate is %s B)\n' "$notes_size" "$after_size" "$GATE_BYTES"
if [ "$after_size" -gt "$GATE_BYTES" ]; then
    printf '  ⚠️  STILL OVER THE GATE — lower --keep and re-run the dry-run.\n'
else
    printf '  ✅ clears the harness_audit gate with %s B of headroom.\n' "$((GATE_BYTES - after_size))"
fi

if [ "$n_rotate" -eq 0 ]; then
    printf '\nnothing to rotate.\n'; exit 0
fi

if [ "$APPLY" -eq 0 ]; then
    printf '\nDry run only. Re-run with --apply to move these sections.\n'
    exit 0
fi

# ── apply ────────────────────────────────────────────────────────────────────
# Two --apply runs against one notes file would interleave: both read the same
# section index, both append to the archive, and the second mv wins — losing
# whatever the first rotated. Exclude, loudly, never wait (honest_failure_modes
# #8). Dry-run stays lock-free: it writes nothing. Missing flock fails CLOSED —
# an unprotected writer is exactly what this guard exists to prevent.
command -v flock >/dev/null 2>&1 || die "flock not found — refusing to --apply without single-writer exclusion"
lockdir="${HOME}/.local/state/meshforge"
mkdir -p "$lockdir"
LOCKFILE="${lockdir}/rotate_session_notes.$(basename "$NOTES" .md).lock"
exec 9>"$LOCKFILE"
flock -n 9 || die "another rotate_session_notes --apply holds $LOCKFILE — refusing to interleave writers"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
bkdir="${HOME}/.local/state/meshforge/session_notes_backup"
mkdir -p "$bkdir"
base="$(basename "$NOTES" .md)"
bk_notes="${bkdir}/${base}.${ts}.md.bak"
bk_arch="${bkdir}/$(basename "$ARCHIVE" .md).${ts}.md.bak"

# noclobber == O_CREAT|O_EXCL: reserve the path, never check-then-write.
( set -C; : > "$bk_notes" ) 2>/dev/null || die "backup path already exists: $bk_notes"
cat "$NOTES" > "$bk_notes"
if [ -f "$ARCHIVE" ]; then
    ( set -C; : > "$bk_arch" ) 2>/dev/null || die "backup path already exists: $bk_arch"
    cat "$ARCHIVE" > "$bk_arch"
fi
printf '\n  backed up -> %s\n' "$bk_notes"

tmp_notes="$(mktemp "${NOTES}.rotate.XXXXXX")"
tmp_arch="$(mktemp "${ARCHIVE}.rotate.XXXXXX")"
tmp_add="$(mktemp "${ARCHIVE}.add.XXXXXX")"
pre_counts="$(mktemp)"
trap 'rm -f "$tmp_notes" "$tmp_arch" "$tmp_add" "$pre_counts"' EXIT

# Occurrence counts BEFORE the append. A bare "is this heading in the archive?"
# check passes vacuously when the heading already appears there — the archive
# already carries duplicate generic headings ("## What happened"). Assert the
# count grew by exactly one instead (review 2026-08-31: self-confirming check).
while IFS=$'\t' read -r _i verdict _b _r _rg heading; do
    [ "$verdict" = "ROTATE" ] || continue
    c=0; [ -f "$ARCHIVE" ] && c="$(grep -cxF "$heading" "$ARCHIVE" || true)"
    printf '%s\t%s\n' "$c" "$heading" >> "$pre_counts"
done <<< "$plan"

# new notes = preamble + kept sections, ORIGINAL order preserved
[ "$pre_lines" -gt 0 ] && sed -n "1,${pre_lines}p" "$NOTES" > "$tmp_notes"
printf '%s\n' "$plan" | awk -F'\t' '$2!="ROTATE"{print $5}' | while IFS='-' read -r a b; do
    sed -n "${a},${b}p" "$NOTES"
done >> "$tmp_notes"

# archive = existing + banner + rotated sections, appended in original order.
# Built into its own file first so the exact appended byte count is KNOWN and
# can be asserted afterward, rather than inferred.
{
    printf '\n<!-- rotated %s by rotate_session_notes.sh from %s -->\n\n' "$ts" "$(basename "$NOTES")"
    printf '%s\n' "$plan" | awk -F'\t' '$2=="ROTATE"{print $5}' | while IFS='-' read -r a b; do
        sed -n "${a},${b}p" "$NOTES"
    done
} > "$tmp_add"
add_bytes="$(wc -c < "$tmp_add")"
[ -f "$ARCHIVE" ] && cat "$ARCHIVE" > "$tmp_arch"
cat "$tmp_add" >> "$tmp_arch"
expected_notes=$(( notes_size - rot_bytes ))
expected_arch=$(( arch_size + add_bytes ))

mv "$tmp_arch" "$ARCHIVE"
mv "$tmp_notes" "$NOTES"
trap - EXIT

# ── verify from the FILES, not from what we intended ─────────────────────────
new_notes="$(wc -c < "$NOTES")"
new_arch="$(wc -c < "$ARCHIVE")"
vfail=0
printf '\n  verification (re-derived from disk):\n'
printf '    notes   %s B -> %s B\n' "$notes_size" "$new_notes"
printf '    archive %s B -> %s B\n' "$arch_size" "$new_arch"

if [ "$new_notes" -le "$GATE_BYTES" ]; then
    printf '    ✅ notes under the %s B gate\n' "$GATE_BYTES"
else
    printf '    ❌ notes STILL over the gate\n'; vfail=1
fi

# Byte-exact conservation: proves EXACTLY rot_bytes left the notes and EXACTLY
# add_bytes arrived in the archive. Independent of heading text, so it cannot
# pass vacuously the way a presence check can.
if [ "$new_notes" -eq "$expected_notes" ]; then
    printf '    ✅ notes lost exactly %s B (the rotated sections)\n' "$rot_bytes"
else
    printf '    ❌ notes size %s B, expected %s B\n' "$new_notes" "$expected_notes"; vfail=1
fi
if [ "$new_arch" -eq "$expected_arch" ]; then
    printf '    ✅ archive gained exactly %s B (%s B content + %s B banner)\n' \
        "$add_bytes" "$rot_bytes" "$((add_bytes - rot_bytes))"
else
    printf '    ❌ archive size %s B, expected %s B\n' "$new_arch" "$expected_arch"; vfail=1
fi

while IFS=$'\t' read -r before heading; do
    after="$(grep -cxF "$heading" "$ARCHIVE" || true)"
    if [ "$after" -ne "$((before + 1))" ]; then
        printf '    ❌ archive count for %s: %s -> %s (expected %s)\n' \
            "$heading" "$before" "$after" "$((before + 1))"; vfail=1
    fi
    grep -qxF "$heading" "$NOTES" && { printf '    ❌ still in notes: %s\n' "$heading"; vfail=1; }
done < "$pre_counts"

# every KEPT heading must have survived — the half nobody checks
while IFS=$'\t' read -r _idx verdict _b _r _range heading; do
    [ "$verdict" = "ROTATE" ] && continue
    grep -qxF "$heading" "$NOTES" || { printf '    ❌ KEPT section vanished: %s\n' "$heading"; vfail=1; }
done <<< "$plan"

if [ "$vfail" -eq 0 ]; then
    printf '    ✅ all %s rotated section(s) appended once and gone from notes\n' "$n_rotate"
    printf '\n  Reminder: the "## Rotated to the archive" pointer section is hand-written —\n'
    printf '  update it yourself if these sections deserve a breadcrumb.\n'
    exit 0
fi
printf '\n  ❌ VERIFICATION FAILED — restore with:\n     cp %s %s\n' "$bk_notes" "$NOTES"
exit 1
