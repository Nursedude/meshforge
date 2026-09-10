#!/usr/bin/env bash
# Falsification test for scripts/harness_audit.sh — every leg the §3 drill
# (2026-09-07) found SILENT gets its condition planted here, and the REAL
# script must read FAIL or UNKNOWN, never PASS. Discovered and run by
# tests/test_honest_status_shell.py (every tests/test_*.sh is a harness).
#
# The sandbox: a fake HOME holding the state files the audit reads, a
# throwaway git repo carrying .claude/settings.json, .claude/hooks and
# .githooks, and stub crontab/gh/ssh/hostname binaries on PATH that only cat
# files inside the sandbox. The audit's seed-coverage leg runs pytest against
# a tests/ dir that does not exist here — that leg reads FAIL here by
# construction (it is drilled by guard_drill.py) and is excluded from the
# closing control; every other assertion names its own leg.
#
# Rule of this file (feedback_a_guard_that_never_failed_is_not_evidence): the
# CONTROL run must read PASS on every leg asserted below, so a typo in a plant
# cannot masquerade as a firing guard.
set -u
unset HOME_OVERRIDE MESHFORGE_REPO CRON_VERDICT_LOG MANAGER_HEARTBEAT_PEER CALIBRATION_LEDGER_PATH MINI_DUDEAI_HOME SUDO_USER
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_REPO="$HERE/.."
SCRIPT="$REAL_REPO/scripts/harness_audit.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fails=0
check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

SB="$TMP/bin"; H="$TMP/home"; R="$TMP/repo"
mkdir -p "$SB" "$H/.claude/hooks" "$H/.claude/projects/-opt-meshforge/memory" "$H/.claude/plans" "$R/.claude/hooks" "$R/scripts/lib"
NOW=$(date +%s)
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

# ── fake home ─────────────────────────────────────────────────────────
printf '{"last_tick_ts": %s, "rules": 1}\n' "$NOW" > "$H/mini_dudeai_state.json"
printf '{"kind": "claim", "ts": %s}\n{"kind": "verdict", "ts": %s}\n' "$NOW" "$NOW" > "$H/calibration_ledger.jsonl"
printf '%s calibration_reverify OK\n%s cron_freshness OK 0 stale\n' "$(iso $((NOW-600)))" "$(iso $((NOW-600)))" > "$H/cron_verdicts.log"
printf '# index\n- one line\n' > "$H/.claude/projects/-opt-meshforge/memory/MEMORY.md"
git -C "$H/.claude/projects/-opt-meshforge/memory" init -q
git -C "$H/.claude/projects/-opt-meshforge/memory" remote add origin git@github.com:example/mem.git
printf '# notes\n' > "$H/.claude/plans/gateway-session-notes-sbhost.md"
printf '#!/usr/bin/env bash\nexit 0\n' > "$H/.claude/hooks/guard_a.sh"; chmod +x "$H/.claude/hooks/guard_a.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$H/.claude/hooks/guard_b.sh"; chmod 644 "$H/.claude/hooks/guard_b.sh"   # 644: bash <file> runs it; readable is the bar
cat > "$H/.claude/settings.json" <<'EOF'
{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash \"$HOME/.claude/hooks/guard_a.sh\""}, {"type": "command", "command": "bash ~/.claude/hooks/guard_b.sh"}]}]}}
EOF

# ── fake repo ─────────────────────────────────────────────────────────
git -C "$R" init -q
cp "$REAL_REPO/scripts/lib/pytest_checked.sh" "$R/scripts/lib/"
cp "$REAL_REPO/scripts/pytest_verdict.sh" "$R/scripts/"
printf '#!/usr/bin/env python3\nraise SystemExit(0)\n' > "$R/scripts/claim_gate.py"   # the Stop hook's target must EXIST for the repo hook-files leg
mkdir -p "$R/.githooks"
for hk in pre-commit pre-push; do printf '#!/bin/sh\nexit 0\n' > "$R/.githooks/$hk"; chmod +x "$R/.githooks/$hk"; done
git -C "$R" config core.hooksPath .githooks
cp "$H/.claude/hooks/guard_a.sh" "$R/.claude/hooks/guard_a.sh"
cat > "$R/.claude/settings.json" <<'EOF'
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "command": "python3 -m mini_dudeai.warmstart --hook || true"}]}],
  "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard_a.sh\""}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/scripts/claim_gate.py\""}]}]
}}
EOF
cp "$R/.claude/settings.json" "$TMP/settings.good.json"

# ── stubs ─────────────────────────────────────────────────────────────
printf '0 * * * * /x/manager_heartbeat.sh\n' > "$TMP/crontab.txt"
printf '#!/bin/sh\ncat "%s/crontab.txt"\n' "$TMP" > "$SB/crontab"
printf 'PRIVATE\n' > "$TMP/gh_vis"; printf '#!/bin/sh\ncat "%s/gh_vis"\n' "$TMP" > "$SB/gh"
printf '1\n' > "$TMP/peer_count"; printf '#!/bin/sh\ncat "%s/peer_count"\n' "$TMP" > "$SB/ssh"
printf '#!/bin/sh\necho sbhost\n' > "$SB/hostname"
chmod +x "$SB"/*

run() {
  HOME="$H" MESHFORGE_REPO="$R" MESHANCHOR_REPO="$TMP/no-such-repo" CRON_VERDICT_LOG="$H/cron_verdicts.log" MANAGER_HEARTBEAT_PEER=peerbox \
    PATH="$SB:$PATH" bash "$SCRIPT" 2>&1
}
leg() { echo "$1" | grep -E "^  $2 " ; }   # $1=out $2=leg label regex

# ── control: every leg asserted below must be PASS here ──────────────
out="$(run)"
for l in 'hooksPath\(repo\)' 'Stop->claim_gate' 'SessionStart->warmstart' 'hook files\(repo\)' 'user hooks' 'mini fresh' \
         'calibration ledger' 'calibration_reverify verdict' 'cron_freshness' 'heartbeat cron \(local\)'; do
  check "control: $l PASS" "$(leg "$out" "$l" | grep -q ' PASS ' && echo ok)"
done

# ── leg 1: hooksPath set but the hooks are gone ───────────────────────
mv "$R/.githooks" "$R/.githooks.off"
out="$(run)"
check "hooksPath: .githooks set but directory absent → FAIL" "$(leg "$out" 'hooksPath\(repo\)' | grep -q ' FAIL ' && echo ok)"
mv "$R/.githooks.off" "$R/.githooks"
chmod -x "$R/.githooks/pre-push"
out="$(run)"
check "hooksPath: pre-push not executable → FAIL naming it" "$(leg "$out" 'hooksPath\(repo\)' | grep ' FAIL ' | grep -q 'pre-push' && echo ok)"
chmod +x "$R/.githooks/pre-push"

# ── leg 2: session hooks — parse, don't grep ──────────────────────────
printf '\n}garbage' >> "$R/.claude/settings.json"
out="$(run)"
check "Stop->claim_gate: unparseable settings → FAIL naming JSON" "$(leg "$out" 'Stop->claim_gate' | grep ' FAIL ' | grep -q 'JSON' && echo ok)"
check "SessionStart->warmstart: unparseable settings → FAIL" "$(leg "$out" 'SessionStart->warmstart' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/settings.good.json" "$R/.claude/settings.json"
python3 - "$R/.claude/settings.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["hooks"].pop("Stop")
d["_note"] = "Stop hook scripts/claim_gate.py parked"; json.dump(d, open(p, "w"))
PY
out="$(run)"
check "Stop->claim_gate: entry deleted, name only in a note → FAIL" "$(leg "$out" 'Stop->claim_gate' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/settings.good.json" "$R/.claude/settings.json"
python3 - "$R/.claude/settings.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["hooks"]["PreCompact"] = d["hooks"].pop("Stop"); json.dump(d, open(p, "w"))
PY
out="$(run)"
check "Stop->claim_gate: wired under another event → FAIL" "$(leg "$out" 'Stop->claim_gate' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/settings.good.json" "$R/.claude/settings.json"

# ── leg 2c: user-level hooks ──────────────────────────────────────────
mv "$H/.claude/hooks/guard_a.sh" "$TMP/guard_a.bak"
out="$(run)"
check "user hooks: wired hook file deleted → FAIL naming it" "$(leg "$out" 'user hooks' | grep ' FAIL ' | grep -q 'guard_a.sh' && echo ok)"
mv "$TMP/guard_a.bak" "$H/.claude/hooks/guard_a.sh"
cp "$H/.claude/settings.json" "$TMP/user.good.json"; printf '\n}x' >> "$H/.claude/settings.json"
out="$(run)"
check "user hooks: unparseable user settings → FAIL" "$(leg "$out" 'user hooks' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/user.good.json" "$H/.claude/settings.json"
mv "$H/.claude/hooks/guard_b.sh" "$TMP/guard_b.bak"
out="$(run)"
check "user hooks: a hook wired with the ~/ spelling and deleted → FAIL naming it" "$(leg "$out" 'user hooks' | grep ' FAIL ' | grep -q 'guard_b.sh' && echo ok)"
mv "$TMP/guard_b.bak" "$H/.claude/hooks/guard_b.sh"
mv "$R/.claude/hooks/guard_a.sh" "$TMP/rguard.bak"
out="$(run)"
check "hook files(repo): a \$CLAUDE_PROJECT_DIR-wired hook deleted → FAIL naming it" "$(leg "$out" 'hook files\(repo\)' | grep ' FAIL ' | grep -q 'guard_a.sh' && echo ok)"
mv "$TMP/rguard.bak" "$R/.claude/hooks/guard_a.sh"
if [ "$(id -u)" != 0 ]; then
  chmod 000 "$R/.claude/settings.json"
  out="$(run)"
  check "Stop->claim_gate: settings file unreadable → UNKNOWN, not 'bad JSON'" "$(leg "$out" 'Stop->claim_gate' | grep ' UNKNOWN ' | grep -q 'could not be read' && echo ok)"
  chmod 644 "$R/.claude/settings.json"
fi
printf '[1, 2]' > "$R/.claude/settings.json"
out="$(run)"
check "Stop->claim_gate: settings parses but is not an object → FAIL" "$(leg "$out" 'Stop->claim_gate' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/settings.good.json" "$R/.claude/settings.json"

# ── leg 3: mini fresh — future tick and absent key say what they are ──
printf '{"last_tick_ts": %s}\n' "$((NOW+600))" > "$H/mini_dudeai_state.json"
out="$(run)"
check "mini fresh: tick in the future → UNKNOWN naming the clock" "$(leg "$out" 'mini fresh' | grep ' UNKNOWN ' | grep -q 'FUTURE' && echo ok)"
printf '{"rules": 1}\n' > "$H/mini_dudeai_state.json"
out="$(run)"
check "mini fresh: no last_tick_ts → UNKNOWN naming the key (never now-0)" "$(leg "$out" 'mini fresh' | grep ' UNKNOWN ' | grep -q 'last_tick_ts' && echo ok)"
printf '{"last_tick_ts": %s}\n' "$((NOW-1000))" > "$H/mini_dudeai_state.json"
out="$(run)"
check "mini fresh: stale tick still FAILS" "$(leg "$out" 'mini fresh' | grep -q ' FAIL ' && echo ok)"
printf '{"last_tick_ts": %s}\n' "$NOW" > "$H/mini_dudeai_state.json"

# ── leg 4: ledger and reverify verdict ────────────────────────────────
cp "$H/calibration_ledger.jsonl" "$TMP/ledger.bak"; head -c 2048 /dev/urandom > "$H/calibration_ledger.jsonl"
out="$(run)"
check "calibration ledger: garbage bytes → FAIL, not 'N events'" "$(leg "$out" 'calibration ledger' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/ledger.bak" "$H/calibration_ledger.jsonl"
cp "$H/cron_verdicts.log" "$TMP/verdicts.bak"
printf '%s calibration_reverify OK\n' "$(iso $((NOW+86400)))" >> "$H/cron_verdicts.log"
out="$(run)"
check "calibration_reverify: verdict stamped in the future → UNKNOWN" "$(leg "$out" 'calibration_reverify verdict' | grep ' UNKNOWN ' | grep -q 'FUTURE' && echo ok)"
cp "$TMP/verdicts.bak" "$H/cron_verdicts.log"
printf '{"kind": "claim", "ts": 1}\n{"kind": "cl' > "$H/calibration_ledger.jsonl"   # one torn tail — the appender's contract
out="$(run)"
check "calibration ledger: ONE torn line beside good events → PASS that discloses it" "$(leg "$out" 'calibration ledger' | grep ' PASS ' | grep -q 'torn' && echo ok)"
cp "$TMP/ledger.bak" "$H/calibration_ledger.jsonl"
printf '{"kind": "claim", "ts": 1}\n' > "$H/calibration_ledger.jsonl"; for i in 1 2 3 4 5 6 7 8 9 10 11 12; do printf 'not json %s\n' $i >> "$H/calibration_ledger.jsonl"; done
out="$(run)"
check "calibration ledger: 12 malformed beside 1 good → FAIL (beyond the torn-tail contract)" "$(leg "$out" 'calibration ledger' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/ledger.bak" "$H/calibration_ledger.jsonl"
printf 'garbage calibration_reverify OK\n' >> "$H/cron_verdicts.log"
out="$(run)"
check "calibration_reverify: unparseable timestamp → UNKNOWN naming it (never now-0 stale)" "$(leg "$out" 'calibration_reverify verdict' | grep ' UNKNOWN ' | grep -q 'unparseable' && echo ok)"
cp "$TMP/verdicts.bak" "$H/cron_verdicts.log"
grep -v ' calibration_reverify ' "$TMP/verdicts.bak" > "$H/cron_verdicts.log"
printf '%s calibration_reverify FAIL 3 crons stale\n' "$(iso $((NOW-2*86400)))" >> "$H/cron_verdicts.log"
out="$(run)"
check "calibration_reverify: a 2-day-old FAIL shows the writer's status, not just 'stale'" "$(leg "$out" 'calibration_reverify verdict' | grep ' FAIL ' | grep -q 'FAIL (3 crons stale)' && echo ok)"
cp "$TMP/verdicts.bak" "$H/cron_verdicts.log"

# ── leg 5: cron_freshness — age window and status both judged ─────────
printf '%s cron_freshness OK 0 stale\n' "$(iso $((NOW-30*86400)))" > "$H/cron_verdicts.log"
printf '%s calibration_reverify OK\n' "$(iso $((NOW-600)))" >> "$H/cron_verdicts.log"
out="$(run)"
check "cron_freshness: last OK 30 days old → FAIL stale" "$(leg "$out" 'cron_freshness' | grep ' FAIL ' | grep -q 'stale' && echo ok)"
cp "$TMP/verdicts.bak" "$H/cron_verdicts.log"
printf '%s cron_freshness FAIL 7 stale\n' "$(iso $NOW)" >> "$H/cron_verdicts.log"
out="$(run)"
check "cron_freshness: writer now stamps FAIL when stale → FAIL" "$(leg "$out" 'cron_freshness' | grep -q ' FAIL ' && echo ok)"
cp "$TMP/verdicts.bak" "$H/cron_verdicts.log"

# ── leg 8: a commented-out crontab line is not a wired cron ───────────
printf '# 0 * * * * /x/manager_heartbeat.sh\n' > "$TMP/crontab.txt"
out="$(run)"
check "heartbeat cron: commented-out line → FAIL" "$(leg "$out" 'heartbeat cron \(local\)' | grep -q ' FAIL ' && echo ok)"
printf '0 * * * * /x/manager_heartbeat.sh\n' > "$TMP/crontab.txt"

# ── finding 18 (2026-09-09): paths come from the producers' SSOT ──────
# (a) the ledger leg read $HOME/calibration_ledger.jsonl by hand while
#     ledger_path() honours CALIBRATION_LEDGER_PATH — the audit certified a
#     file the ledger never writes. Point the SSOT elsewhere; the leg follows.
printf '{"kind": "claim", "ts": 1}\n' > "$TMP/elsewhere.jsonl"
for i in 1 2 3 4 5; do printf 'not json %s\n' $i >> "$TMP/elsewhere.jsonl"; done
out="$(CALIBRATION_LEDGER_PATH="$TMP/elsewhere.jsonl" run)"
check "calibration ledger: reads the path ledger_path() resolves (CALIBRATION_LEDGER_PATH), not \$HOME's file" \
  "$(leg "$out" 'calibration ledger' | grep -q ' FAIL ' && echo ok)"
# (b) the notes leg lowercased the hostname; warmstart tries the exact case
#     first. A note stored under the exact-case name read "absent" here while
#     warmstart lifted it every session.
printf '#!/bin/sh\necho SbHost\n' > "$SB/hostname"
mv "$H/.claude/plans/gateway-session-notes-sbhost.md" "$H/.claude/plans/gateway-session-notes-SbHost.md"
out="$(run)"
check "session notes: exact-case note resolves the way warmstart resolves it → PASS" \
  "$(leg "$out" 'session notes' | grep -q ' PASS ' && echo ok)"
mv "$H/.claude/plans/gateway-session-notes-SbHost.md" "$H/.claude/plans/gateway-session-notes-sbhost.md"
out="$(run)"
check "session notes: lowercase note still resolves under a mixed-case hostname" \
  "$(leg "$out" 'session notes' | grep -q ' PASS ' && echo ok)"
printf '#!/bin/sh\necho sbhost\n' > "$SB/hostname"

# ── finding 23: the deadman peer is config, never a fleet hostname ────
run_nopeer() {
  HOME="$H" MESHFORGE_REPO="$R" MESHANCHOR_REPO="$TMP/no-such-repo" CRON_VERDICT_LOG="$H/cron_verdicts.log" \
    PATH="$SB:$PATH" bash "$SCRIPT" 2>&1
}
out="$(run_nopeer)"
check "deadman cron: heartbeat wired + no peer configured → UNKNOWN naming the config, never a hardcoded host" \
  "$(leg "$out" 'deadman cron' | grep ' UNKNOWN ' | grep -q 'manager_heartbeat_peer' && echo ok)"
check "deadman cron: no hardcoded fleet host is sshed" \
  "$(leg "$out" 'deadman cron' | grep -q 'moc' && echo '' || echo ok)"
mkdir -p "$H/.config/meshforge"; printf 'peerbox  # the deadman box\n' > "$H/.config/meshforge/manager_heartbeat_peer"
out="$(run_nopeer)"
check "deadman cron: peer from ~/.config/meshforge/manager_heartbeat_peer → PASS" \
  "$(leg "$out" 'deadman cron \(peerbox\)' | grep -q ' PASS ' && echo ok)"
rm -f "$H/.config/meshforge/manager_heartbeat_peer"
printf '# 0 * * * * /x/manager_heartbeat.sh\n' > "$TMP/crontab.txt"
out="$(run_nopeer)"
check "deadman cron: no sender and no peer → inert NOTE, not UNKNOWN" \
  "$(leg "$out" 'deadman cron' | grep -q ' NOTE ' && echo ok)"
printf '0 * * * * /x/manager_heartbeat.sh\n' > "$TMP/crontab.txt"

# ── closing control ───────────────────────────────────────────────────
out="$(run)"
check "closing control: no FAIL line (other than the sandbox's seed-coverage leg) after every plant was restored" \
  "$(echo "$out" | grep ' FAIL ' | grep -qv 'seed coverage' || echo ok)"
echo "$out" | grep ' FAIL ' | grep -v 'seed coverage' | sed 's/^/  closing-control FAIL line: /'

if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
