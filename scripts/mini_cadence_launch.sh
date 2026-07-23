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
# The cron-verdict job name for this cadence. The crontab wiring MUST use it
# (`...mini_cadence_launch.sh; cron_verdict.sh $CRON_VERDICT_NAME $?` style),
# and claw_telemetry.CADENCE_VERDICT_NAME reads the SAME name off /fleet/slo
# as the F-tier evidence key — rename one side alone and the claw glyph
# silently loses tier F forever while the frontier is healthy. The two are
# test-pinned together (tests/test_claw_telemetry.py); this declaration is
# the in-repo write-side anchor (honest_failure_modes #5).
CRON_VERDICT_NAME="mini_cadence"
# Pin the cadence model explicitly. Ratification is high-judgment work (verify
# live truth before authoring durable memory), so we never ride on whatever the
# `claude` CLI happens to default to — that can silently drift. Most-capable Opus
# for the judgment; overridable via env so an operator can change it without
# editing the script.
#
# MODEL-SWAP CONTRACT (verified 2026-06-19, self-audit-qa-arc §0): a retired or
# renamed model FAILS LOUD, never a silent fallback. `claude --model <bad> -p`
# prints "It may not exist or you may not have access to it" and exits 1; that rc
# propagates through the `|| rc=$?` / `exit "$rc"` below into
# `cron_verdict.sh mini_cadence`, recording FAIL. The FAIL is STICKY: a failed
# session cannot ratify the proposed deltas, so they stay "proposed" and
# re-trigger a session every run — so it fails on EVERY cadence with deltas, not
# just once, and Issue #78 `cron_verdict_stale` reliably escalates to ntfy.
# To repoint after a swap: set MINI_DUDEAI_CADENCE_MODEL (no script edit).
MODEL="${MINI_DUDEAI_CADENCE_MODEL:-claude-opus-4-8}"
# Bound the session so a wedged run can't pin a fleet box (cf. the rnsd-RPC
# fragility class — everything mini-adjacent carries a timeout).
TIMEOUT_S="${MINI_CADENCE_TIMEOUT_S:-900}"

# Timeout-backoff (added 2026-06-26). A session that hits TIMEOUT_S exits 124 and
# CANNOT ratify its deltas, so they stay "proposed" and the NEXT hourly run
# re-fires the same doomed session — on 2026-06-24 this burned 4 consecutive 900s
# sessions on an already-slow box. After a 124 we record (ts + a hash of the
# proposed-delta set) and SKIP re-firing for TIMEOUT_BACKOFF_S *as long as the
# proposed set is unchanged*; a genuinely NEW delta set (different hash) bypasses
# the backoff and fires fresh. The skip still exits NON-ZERO (EX_TEMPFAIL, 75) so
# cron_verdict keeps the stuck state VISIBLE to Issue #78 cron_verdict_stale — we
# drop the COST (tokens + box load), never the SIGNAL. Distinct from the
# model-swap FAIL above (exit 1), which is sticky-by-design and NOT backed off.
TIMEOUT_BACKOFF_S="${MINI_CADENCE_TIMEOUT_COOLDOWN_S:-10800}"   # 3h
STATE_FILE="${MINI_CADENCE_STATE_FILE:-$HOME/.config/meshforge/mini_cadence_timeout_state}"

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

# Content-key the proposed set so the backoff below distinguishes "the SAME stuck
# deltas a prior session timed out on" from "genuinely new work". Hash failure
# (no sha256sum) -> empty hash -> backoff still applies on time alone (safe: we
# err toward sparing the box, and a missing tool is itself anomalous).
cur_hash=""
if command -v sha256sum >/dev/null 2>&1; then
  cur_hash="$(grep -E '"status"[[:space:]]*:[[:space:]]*"proposed"' "$DELTAS" 2>/dev/null \
              | sha256sum | cut -d' ' -f1)"
fi

# Backoff gate: if the previous session TIMED OUT (124) on this same proposed set
# within TIMEOUT_BACKOFF_S, skip the (expensive, already-failing) session. Exit
# 75 keeps the FAIL verdict visible without spending a session or pinning the box.
last_ts=0; last_hash=""
if [ -f "$STATE_FILE" ]; then
  # `|| true`: a torn/partial state file (missing a key) must fall through to
  # firing, never abort the launcher under `set -e`/pipefail.
  last_ts="$(grep -E '^last_timeout_ts=' "$STATE_FILE" 2>/dev/null | head -1 | cut -d= -f2 || true)"
  last_hash="$(grep -E '^last_timeout_hash=' "$STATE_FILE" 2>/dev/null | head -1 | cut -d= -f2 || true)"
fi
case "${last_ts:-0}" in ''|*[!0-9]*) last_ts=0 ;; esac
now="$(date +%s)"
if [ "$last_ts" -gt 0 ] && [ "$cur_hash" = "$last_hash" ] \
   && [ "$((now - last_ts))" -lt "$TIMEOUT_BACKOFF_S" ]; then
  ago_m=$(( (now - last_ts) / 60 ))
  left_m=$(( (TIMEOUT_BACKOFF_S - (now - last_ts)) / 60 ))
  echo "mini-cadence: prior session TIMED OUT ${ago_m}m ago on these SAME proposed deltas;" \
       "backing off (retry in ~${left_m}m). Skipping the session to spare tokens + box load;" \
       "the stuck state stays VISIBLE as a FAIL verdict." >&2
  exit 75
fi

if [ ! -f "$RUNBOOK" ]; then
  echo "mini-cadence: runbook missing ($RUNBOOK) — refusing to launch blind." >&2
  exit 1
fi

# Local-tier fallback (W1, dudeclaw_local_brain_2026_07_03 §4). When the
# frontier session cannot run or fails, a bounded local-LLM pass TRIAGES the
# proposed backlog for the returning frontier — it NEVER ratifies (that is
# high-judgment frontier work by charter), and this path NEVER exits 0, so
# the `mini_cadence OK` cron verdict stays frontier-only evidence (the claw
# tier glyph's F cannot be forged by a local run). Best-effort: its own
# failure never masks the frontier rc. nice+timeout bound the client; the
# inference load lives in the ollama SERVER, capped by its own unit drop-in.
run_local_fallback() {
  local frc="${1:-}"
  echo "mini-cadence: attempting LOCAL-tier triage fallback (frontier rc=${frc:-cli-missing})."
  if PYTHONPATH="$REPO/src" nice -n 10 \
       timeout "${MINI_CADENCE_LOCAL_TIMEOUT_S:-600}" \
       python3 -m mini_dudeai.cadence_fallback \
         --deltas "$DELTAS" --mode fallback --frontier-rc "$frc"; then
    echo "mini-cadence: local triage witness written (suggestions only — nothing ratified)."
  else
    echo "mini-cadence: local fallback also failed (rc=$?) — deltas stay pending, verdict stays FAIL." >&2
  fi
}

# Local-tier PRE-SCORE (increment 3, WS-C). BEFORE the frontier session, a
# bounded local-LLM pass triages the proposed backlog and writes the witness
# (mode=pre-score) so the frontier session starts ORIENTED — it prioritises by
# each delta's suggested_disposition (looks-ratifiable / looks-rejectable /
# needs-live-check) instead of reading the queue cold, which shrinks the
# 0/164-ratified tail. The local tier NEVER ratifies (charter) — these are
# suggestions the frontier still verifies. Best-effort + bounded: a failed or
# slow pre-score NEVER blocks or delays the frontier pass and NEVER changes the
# cron verdict (still frontier-only evidence). It runs SEQUENTIALLY before the
# frontier API session (never concurrent), so peak local memory is the same as
# the existing fallback path. Set MINI_CADENCE_PRESCORE=0 to disable on a
# memory-tight box (the local model is the heavier load — the documented
# local-inference memory-freeze class).
run_local_prescore() {
  [ "${MINI_CADENCE_PRESCORE:-1}" = "1" ] || {
    echo "mini-cadence: pre-score disabled (MINI_CADENCE_PRESCORE=0) — frontier runs cold."
    return 0
  }
  # WS-E: the model router decides whether the pre-score is worth running, from
  # MEASURED tier-L triage competence (the eval ledger). If tier-L FAILS the
  # triage eval, a noisy local triage is worse than none — skip it. --record
  # logs the routing decision (self-scoring). Best-effort: ONLY an explicit
  # "not trusted" (rc 2) skips; UNKNOWN (rc 3) or any error proceeds
  # (uncertainty != untrusted), so a box without the router behaves as before.
  # timeout: everything mini-adjacent carries one (the script's own doctrine)
  # — the gate is pure file I/O today, but a wedged home mount must not pin
  # the cron. stderr is KEPT (07-23 audit): the router's "routing ledger
  # write FAILED" line is the only witness a permanently unwritable ledger
  # gets; >/dev/null on it meant recording could fail forever, silently.
  local grc=0
  PYTHONPATH="$REPO/src" timeout 60 python3 -m mini_dudeai.model_router \
    --task-kind cadence_triage --l-trusted-gate --record >/dev/null || grc=$?
  if [ "$grc" -eq 2 ]; then
    echo "mini-cadence: router — tier-L NOT trusted for triage (eval); skipping pre-score (frontier runs cold)."
    return 0
  fi
  echo "mini-cadence: local-tier PRE-SCORE of the backlog (frontier will consume it)."
  if PYTHONPATH="$REPO/src" nice -n 10 \
       timeout "${MINI_CADENCE_LOCAL_TIMEOUT_S:-600}" \
       python3 -m mini_dudeai.cadence_fallback \
         --deltas "$DELTAS" --mode pre-score >/dev/null 2>&1; then
    echo "mini-cadence: pre-score witness written (suggestions only — nothing ratified)."
  else
    echo "mini-cadence: pre-score unavailable (rc=$?); frontier runs cold. Non-fatal." >&2
  fi
  return 0
}

if ! command -v claude >/dev/null 2>&1; then
  echo "mini-cadence: 'claude' CLI not on PATH — cannot launch cadence session." >&2
  run_local_fallback ""
  # 75 (EX_TEMPFAIL, the same convention as the backoff skip): the cadence is
  # DEGRADED and visibly so via the FAIL(75) verdict — never OK.
  exit 75
fi

# Pre-score the backlog on the local tier so the frontier session starts
# oriented (best-effort; never blocks the session — see run_local_prescore).
run_local_prescore || true

echo "mini-cadence: proposed deltas present — launching cadence session (model ${MODEL}, timeout ${TIMEOUT_S}s)."
# -p runs headless with the runbook as the prompt; the session reads the rest of
# the runbook file itself for the full procedure. --model pins the ratification
# model explicitly (see MODEL above). Capture the rc without letting `set -e`
# abort before we log it.
rc=0
timeout "$TIMEOUT_S" claude --model "$MODEL" -p "Run the mini-dudeai cadence pass per $RUNBOOK. \
Resolve every proposed memory-delta: verify each against live truth, then ratify \
(authoring a verified canonical memory via mini_dudeai.memory_apply) or reject. \
A local pre-triage may exist at ~/mini_dudeai_cadence_triage.json — use its per-key \
suggested_disposition to PRIORITISE your pass, but it ran NO checks, so verify before \
you ratify (a suggestion is not verification). Record a --reason on each reject. \
Never write verified=True without a check you ran. One bounded pass, then stop." || rc=$?
echo "mini-cadence: cadence session finished (exit $rc)."

# Record/clear the timeout-backoff state. Only a 124 (the session hit TIMEOUT_S)
# arms the backoff; ANY other outcome — a clean ratify/reject (0) OR a loud
# non-124 failure like the sticky-by-design model-swap FAIL — clears it.
if [ "$rc" -eq 124 ]; then
  mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
  { echo "last_timeout_ts=$(date +%s)"; echo "last_timeout_hash=$cur_hash"; } \
      > "$STATE_FILE" 2>/dev/null || true
else
  rm -f "$STATE_FILE" 2>/dev/null || true
fi

# Frontier session failed (API unreachable, auth, model swap…): make the
# failure productive on the local tier. NOT on 124 — a timeout means the box
# was already grinding for TIMEOUT_S; stacking a local inference on top is
# the freeze class, and the backoff above owns that path. The ORIGINAL rc is
# preserved either way (sticky FAIL(1) and 124 semantics untouched).
if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ]; then
  run_local_fallback "$rc"
elif [ "$rc" -eq 0 ]; then
  # Frontier returned and resolved the backlog: retire the local-tier
  # witness so the warm brief stops flagging a degradation that is over
  # (the run history stays in mini_cadence.log + the verdict trail).
  PYTHONPATH="$REPO/src" python3 -m mini_dudeai.cadence_fallback --clear || true
fi
exit "$rc"
