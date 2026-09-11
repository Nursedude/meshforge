# fleet_posture.sh — the declared-posture reader for SHELL consumers. Source
# it; never copy it (honest_failure_modes #5 — the fleet_hosts.sh lesson).
#
# ONE implementation of the rules lives in src/utils/fleet_posture.py
# (mandatory capped `until`, expiry-as-default, clock HOLD, loud
# unreadable/invalid). This file only asks it and exports the answer, so a
# shell organ and a Python organ can never disagree about whether a box is
# declared off.
#
# Usage:
#   . "<repo>/scripts/lib/fleet_posture.sh"
#   fleet_posture_read "<repo>"       # never fails the caller (rc 0 always)
#   echo "$FLEET_POSTURE_STATUS"      # declared | undeclared | unreadable | invalid | reader-error
#   echo "$FLEET_POSTURE_SILENT"      # "name state note" per line for dormant/detached boxes
#   fleet_posture_is_silent moc4 && echo off   # rc 0 when declared dormant/detached (in effect)
#   fleet_posture_note moc4           # the declaration sentence for the witness line
#
# Honest failure modes: a reader error or a broken file exports
# FLEET_POSTURE_STATUS != declared and an EMPTY silent list — every consumer
# then treats every box as ACTIVE (watch everything; paging/deploying is the
# safe default) and is expected to print the status when it is not
# declared/undeclared, so a broken declaration is found, not absorbed.

# The SSOT module lives beside THIS file (<repo>/src/utils/fleet_posture.py),
# so the Python path is derived from where the library was sourced from —
# never from the caller's notion of the repo (honest_status's test harness
# points MESHFORGE_REPO at a fake repo with no src/, which made the reader
# silently return reader-error → every box active → the dormant case failed
# on first run, 2026-09-01).
_FP_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../src" 2>/dev/null && pwd)"

fleet_posture_read() {
  FLEET_POSTURE_STATUS="reader-error"
  FLEET_POSTURE_SILENT=""
  local out
  out=$(PYTHONPATH="${_FP_SRC:-/opt/meshforge/src}" python3 - <<'PYPOSTURE' 2>/dev/null
try:
    from utils import fleet_posture as fp
    p = fp.read_posture()
    print(p.status + ((" " + p.detail) if p.detail else ""))
    for name, b in sorted(p.boxes.items()):
        if b.silent:
            print("%s %s %s" % (name, b.state, b.note.replace("\n", " ")))
except Exception as exc:
    print("reader-error %s: %s" % (type(exc).__name__, exc))
PYPOSTURE
) || { FLEET_POSTURE_STATUS="reader-error rc=$?"; return 0; }
  FLEET_POSTURE_STATUS="$(printf '%s\n' "$out" | sed -n '1p')"
  case "$FLEET_POSTURE_STATUS" in
    declared*) FLEET_POSTURE_SILENT="$(printf '%s\n' "$out" | sed '1d')" ;;
    *) FLEET_POSTURE_SILENT="" ;;
  esac
  return 0
}

fleet_posture_summary() {  # -> the one-clause note a consumer appends to its line
  # WHY this is not just "declared -> in effect": a posture FILE that parses is
  # `declared` even with ZERO boxes in it, which is the normal resting state
  # after the last box is cleared (fleet_power.py resume). Saying "declared
  # posture in effect" there is an affirmative claim from an EMPTY state --
  # nothing is silenced, no leg's denominator moved, and a reader chasing the
  # note finds nothing. Empty != absent != in effect (2026-09-10).
  local n=0
  if [ -n "${FLEET_POSTURE_SILENT:-}" ]; then
    n=$(printf '%s\n' "$FLEET_POSTURE_SILENT" | grep -c '[^[:space:]]' || true)
  fi
  case "${FLEET_POSTURE_STATUS:-undeclared}" in
    declared*)
      if [ "${n:-0}" -gt 0 ]; then
        printf '; declared posture in effect (%s box(es) silenced)' "$n"
      else
        printf '; posture file present, no box declared — nothing silenced'
      fi
      ;;
    undeclared) : ;;
    *) printf '; POSTURE FILE NOT USABLE (%s) — every box checked' "${FLEET_POSTURE_STATUS}" ;;
  esac
}

fleet_posture_is_silent() {  # name -> rc 0 if declared dormant/detached in effect
  [ -n "${FLEET_POSTURE_SILENT:-}" ] || return 1
  printf '%s\n' "$FLEET_POSTURE_SILENT" | awk -v n="$1" '$1 == n {found=1} END {exit !found}'
}

fleet_posture_state() {  # name -> "dormant" | "detached" | ""
  [ -n "${FLEET_POSTURE_SILENT:-}" ] || return 0
  printf '%s\n' "$FLEET_POSTURE_SILENT" | awk -v n="$1" '$1 == n {print $2; exit}'
}

fleet_posture_note() {  # name -> the declaration sentence ("" if not silent)
  [ -n "${FLEET_POSTURE_SILENT:-}" ] || return 0
  printf '%s\n' "$FLEET_POSTURE_SILENT" | awk -v n="$1" '$1 == n {$1=""; $2=""; sub(/^  /, ""); print; exit}'
}
