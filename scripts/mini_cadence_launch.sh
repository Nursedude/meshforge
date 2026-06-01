#!/usr/bin/env bash
# mini-dudeai cadence launcher — self-contained, event-gated.
#
# Each run: (1) refresh proposals via mini's deterministic --dream pass, then
# (2) fire a real (token-costing) `claude -p` cadence session ONLY if proposed
# memory-deltas now exist. No proposals → exit cheap. Intended as a cron on the
# box that runs mini + the digest daemon (the federator box).
#
# Install (operator opt-in — this spends tokens):
#   crontab -e  →  37 * * * * /opt/meshforge/scripts/mini_cadence_launch.sh >> ~/mini_cadence.log 2>&1
#
# DO NOT run during a controlled mini experiment (e.g. the honest-mini soak):
# --dream would synthesize patterns from synthetic stress signals and the
# cadence session could author memory about fake events. Activate after the
# experiment concludes.
#
# The cadence session's instructions live in .claude/prompts/mini_cadence.md
# (versioned), which encodes the anti-theater standard: verify before authoring,
# never write verified=True without a live check.
set -euo pipefail

# cron runs with a minimal PATH (/usr/bin:/bin) that omits ~/.local/bin, where the
# `claude` CLI installs (symlink -> ~/.local/share/claude/versions/*). Without this
# the launcher fired post-hold but bailed at the `command -v claude` gate five times
# (2026-05-31) — the second-brain cadence never actually ran. Augment PATH so claude
# (and any user-local python3/git) resolve the same as an interactive shell.
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"

DELTAS="${MINI_DELTAS_PATH:-$HOME/mini_dudeai_memory_deltas.jsonl}"
REPO="${MESHFORGE_REPO:-/opt/meshforge}"
RUNBOOK="$REPO/.claude/prompts/mini_cadence.md"
ENV_FILE="${MINI_ENV_FILE:-$HOME/.config/meshforge/mini_dudeai.env}"
PRESET="${MINI_PRESET:-meshforge_fleet}"
# Bound the session so a wedged run can't pin a fleet box (cf. the rnsd-RPC
# fragility class — everything mini-adjacent carries a timeout).
TIMEOUT_S="${MINI_CADENCE_TIMEOUT_S:-900}"

# Hold guard — pause ALL cadence activity (no --dream, no session) until a
# given epoch. Generic: use it to keep cadence out of any controlled mini
# experiment (e.g. a soak) without touching the crontab. Source is an epoch in
# $MINI_CADENCE_HOLD_UNTIL, else the first integer in the hold file. Once the
# epoch passes the launcher proceeds normally — no manual re-enable needed.
HOLD_FILE="${MINI_CADENCE_HOLD_FILE:-$HOME/.config/meshforge/mini_cadence_hold_until}"
HOLD_UNTIL="${MINI_CADENCE_HOLD_UNTIL:-}"
if [ -z "$HOLD_UNTIL" ] && [ -f "$HOLD_FILE" ]; then
  HOLD_UNTIL="$(tr -cd '0-9' < "$HOLD_FILE")"
fi
if [ -n "$HOLD_UNTIL" ] && [ "$(date +%s)" -lt "$HOLD_UNTIL" ]; then
  echo "mini-cadence: held until @$HOLD_UNTIL ($(date -d "@$HOLD_UNTIL" 2>/dev/null)) — skipping."
  exit 0
fi

# Refresh proposals FIRST. mini's --dream pass is deterministic (no LLM, cheap)
# and is the ONLY thing that proposes memory-deltas — without it the gate below
# can never open. Set MINI_SKIP_DREAM=1 to gate on existing deltas only (e.g. if
# a separate --dream cron owns synthesis). The preset's build_engine() needs the
# ntfy topic even for --dream, so load the env file the systemd unit uses.
if [ "${MINI_SKIP_DREAM:-0}" != "1" ]; then
  if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
  if ! PYTHONPATH="$REPO/src" python3 -m mini_dudeai --preset "$PRESET" --dream >/dev/null 2>&1; then
    echo "mini-cadence: --dream refresh failed; gating on existing deltas only." >&2
  fi
fi

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
