#!/usr/bin/env bash
# mini-dudeai cadence launcher — event-gated.
#
# Each cadence run is a real (token-costing) Claude Code session, so this fires
# `claude -p` ONLY when mini has proposed memory-deltas awaiting ratification.
# No proposals → exit cheap. Intended as a cron on the box that runs mini +
# the digest daemon (the federator box).
#
# Install (operator opt-in — this spends tokens):
#   crontab -e  →  17 * * * * /opt/meshforge/scripts/mini_cadence_launch.sh >> ~/mini_cadence.log 2>&1
#
# The cadence session's instructions live in .claude/prompts/mini_cadence.md
# (versioned), which encodes the anti-theater standard: verify before authoring,
# never write verified=True without a live check.
set -euo pipefail

DELTAS="${MINI_DELTAS_PATH:-$HOME/mini_dudeai_memory_deltas.jsonl}"
REPO="${MESHFORGE_REPO:-/opt/meshforge}"
RUNBOOK="$REPO/.claude/prompts/mini_cadence.md"
# Bound the session so a wedged run can't pin a fleet box (cf. the rnsd-RPC
# fragility class — everything mini-adjacent carries a timeout).
TIMEOUT_S="${MINI_CADENCE_TIMEOUT_S:-900}"

if [ ! -f "$DELTAS" ]; then
  echo "mini-cadence: no deltas file ($DELTAS) — nothing to do."
  exit 0
fi

# Gate on the STATUS value specifically. "proposed" also appears as the key
# "proposed_action"; anchor on `"status": "proposed"` (tolerant of spacing) so a
# delta that merely carries a proposed_action never trips the gate.
if ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"proposed"' "$DELTAS"; then
  echo "mini-cadence: no proposed deltas — exit cheap (no Claude session spent)."
  exit 0
fi

if [ ! -f "$RUNBOOK" ]; then
  echo "mini-cadence: runbook missing ($RUNBOOK) — refusing to launch blind." >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "mini-cadence: 'claude' CLI not on PATH — cannot launch cadence session." >&2
  exit 1
fi

echo "mini-cadence: proposed deltas present — launching cadence session (timeout ${TIMEOUT_S}s)."
# -p runs headless with the runbook as the prompt; the session reads the rest of
# the runbook file itself for the full procedure. Capture the rc without letting
# `set -e` abort before we log it.
rc=0
timeout "$TIMEOUT_S" claude -p "Run the mini-dudeai cadence pass per $RUNBOOK. \
Resolve every proposed memory-delta: verify each against live truth, then ratify \
(authoring a verified canonical memory via mini_dudeai.memory_apply) or reject. \
Never write verified=True without a check you ran. One bounded pass, then stop." || rc=$?
echo "mini-cadence: cadence session finished (exit $rc)."
exit "$rc"
