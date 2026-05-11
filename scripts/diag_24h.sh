#!/usr/bin/env bash
# diag_24h.sh — 24-hour RF + MQTT + node-role diagnostic capture
#
# Captures a 24-hour observation window for a regional LongFast Meshtastic
# network. Designed to run on multiple boxes simultaneously (any 3+ fleet hosts)
# so cross-box analysis can detect flood-algorithm failures (packet seen on
# box A but never propagates to box C) and correlate RF receptions with MQTT
# uplinks.
#
# Subcommands:
#   start    Plant T0 marker, begin journalctl + mosquitto tail captures,
#            register the 5-minute snapshot cron entry. Idempotent: re-running
#            on an already-running diag returns nonzero with a hint.
#   stop     Kill captures and snapshot cron entry. Output is preserved.
#   status   Show T0, elapsed time, output sizes, and capture-process health.
#   analyze  Run diag_24h_analyze.py on the captured data, producing
#            report.json + report.md alongside the captures.
#
# Output goes under: /home/<owner>/meshforge_diag_24h/<host>/
#   meshtasticd.log     continuous journal capture
#   mosquitto.log       continuous broker log capture (if readable)
#   snapshots/*.json    point-in-time state every 5 min
#   T0.txt              capture start timestamp (UTC ISO-8601)
#   report.json|md      produced by 'analyze'

set -euo pipefail

HOST="$(hostname -s)"
OWNER="${SUDO_USER:-${USER:-$(whoami)}}"
OUTDIR="/home/${OWNER}/meshforge_diag_24h/${HOST}"
SESSION="meshforge-diag-${HOST}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT_SCRIPT="${SCRIPT_DIR}/diag_24h_snapshot.py"
ANALYZE_SCRIPT="${SCRIPT_DIR}/diag_24h_analyze.py"

mkdir -p "${OUTDIR}/snapshots"

cmd_start() {
    if [ -f "${OUTDIR}/T0.txt" ]; then
        # Liveness check — T0.txt persists across capture deaths (silent
        # nohup death, OOM kill, SIGTERM during reboot). If the tmux
        # session is gone, the T0 marker is stale and `start` should
        # cleanly recover instead of refusing. Caught live 2026-05-09:
        # capture died on May 3, T0 stayed put, operator hit "Already
        # running" 6 days later. See `feedback_no_silent_failure`.
        local stale=0
        if command -v tmux >/dev/null 2>&1; then
            if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
                stale=1
            fi
        elif [ -f "${OUTDIR}/meshtasticd_tail.pid" ]; then
            if ! kill -0 "$(cat "${OUTDIR}/meshtasticd_tail.pid")" 2>/dev/null; then
                stale=1
            fi
        fi
        if [ "${stale}" = "1" ]; then
            echo "Stale T0 marker (capture process is gone). Cleaning up and starting fresh." >&2
            cmd_stop >/dev/null 2>&1 || true
        else
            echo "Already running (T0=$(cat "${OUTDIR}/T0.txt")). Run 'stop' first to restart." >&2
            return 1
        fi
    fi
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "${OUTDIR}/T0.txt"
    logger -t meshforge-diag "diag_24h start host=${HOST} t0=$(cat "${OUTDIR}/T0.txt")"

    # Continuous meshtasticd journal capture (background)
    if command -v tmux >/dev/null 2>&1; then
        tmux new-session -d -s "${SESSION}" \
            "journalctl -u meshtasticd --since now -f -o short-iso >> '${OUTDIR}/meshtasticd.log' 2>&1"
    else
        nohup journalctl -u meshtasticd --since now -f -o short-iso \
            >> "${OUTDIR}/meshtasticd.log" 2>&1 &
        echo "$!" > "${OUTDIR}/meshtasticd_tail.pid"
    fi

    # Continuous mosquitto log capture (background) if readable
    if [ -r /var/log/mosquitto/mosquitto.log ]; then
        nohup tail -F /var/log/mosquitto/mosquitto.log \
            >> "${OUTDIR}/mosquitto.log" 2>&1 &
        echo "$!" > "${OUTDIR}/mosquitto_tail.pid"
    fi

    # 5-minute snapshot cron entry (idempotent)
    if [ -x "${SNAPSHOT_SCRIPT}" ]; then
        TMP_CRON="$(mktemp)"
        crontab -l 2>/dev/null | grep -v "diag_24h_snapshot" > "${TMP_CRON}" || true
        echo "*/5 * * * * ${SNAPSHOT_SCRIPT} >> ${OUTDIR}/snapshot.log 2>&1" >> "${TMP_CRON}"
        crontab "${TMP_CRON}"
        rm "${TMP_CRON}"
    else
        echo "warning: snapshot script not found or not executable at ${SNAPSHOT_SCRIPT}" >&2
        echo "warning: rate-based snapshot data will be missing from the report" >&2
    fi

    echo "diag_24h started on ${HOST}"
    echo "  T0:     $(cat "${OUTDIR}/T0.txt")"
    echo "  Outdir: ${OUTDIR}"
    echo "  Run 'bash $0 status' to check progress; 'analyze' at T+24h to produce the report."
}

cmd_stop() {
    if command -v tmux >/dev/null 2>&1; then
        tmux kill-session -t "${SESSION}" 2>/dev/null || true
    fi
    if [ -f "${OUTDIR}/meshtasticd_tail.pid" ]; then
        kill "$(cat "${OUTDIR}/meshtasticd_tail.pid")" 2>/dev/null || true
        rm -f "${OUTDIR}/meshtasticd_tail.pid"
    fi
    if [ -f "${OUTDIR}/mosquitto_tail.pid" ]; then
        kill "$(cat "${OUTDIR}/mosquitto_tail.pid")" 2>/dev/null || true
        rm -f "${OUTDIR}/mosquitto_tail.pid"
    fi
    TMP_CRON="$(mktemp)"
    crontab -l 2>/dev/null | grep -v "diag_24h_snapshot" > "${TMP_CRON}" || true
    crontab "${TMP_CRON}"
    rm "${TMP_CRON}"
    # Rename rather than delete so post-stop `analyze` still has T0 (#1161).
    # cmd_status keys "live" off T0.txt presence; T0_archived.txt preserves
    # the window for the analyzer without spoofing the live-run signal.
    mv -f "${OUTDIR}/T0.txt" "${OUTDIR}/T0_archived.txt" 2>/dev/null || true
    echo "diag_24h stopped on ${HOST}. Captures preserved in ${OUTDIR}."
}

cmd_status() {
    if [ ! -f "${OUTDIR}/T0.txt" ]; then
        echo "Not running on ${HOST}. Run 'bash $0 start' to begin."
        return 0
    fi
    T0="$(cat "${OUTDIR}/T0.txt")"
    NOW="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "Host:    ${HOST}"
    echo "T0:      ${T0}"
    echo "Now:     ${NOW}"
    echo "Outdir:  ${OUTDIR}"
    echo "Sizes:"
    if ls "${OUTDIR}"/*.log >/dev/null 2>&1; then
        du -sh "${OUTDIR}"/*.log | sed 's/^/  /'
    else
        echo "  (no logs yet)"
    fi
    SNAP_COUNT="$(find "${OUTDIR}/snapshots" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)"
    echo "Snapshots: ${SNAP_COUNT}"
    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${SESSION}" 2>/dev/null; then
        echo "Capture:   tmux session '${SESSION}' is running"
    elif [ -f "${OUTDIR}/meshtasticd_tail.pid" ] \
        && kill -0 "$(cat "${OUTDIR}/meshtasticd_tail.pid")" 2>/dev/null; then
        echo "Capture:   nohup tail (pid $(cat "${OUTDIR}/meshtasticd_tail.pid")) is running"
    else
        echo "Capture:   NOT running — restart with 'bash $0 stop && bash $0 start'"
    fi
    if crontab -l 2>/dev/null | grep -q diag_24h_snapshot; then
        echo "Snapshot cron: active"
    else
        echo "Snapshot cron: NOT active"
    fi
}

cmd_analyze() {
    if [ ! -x "${ANALYZE_SCRIPT}" ]; then
        echo "Analyzer not found or not executable: ${ANALYZE_SCRIPT}" >&2
        return 1
    fi
    "${ANALYZE_SCRIPT}" "${OUTDIR}"
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    analyze) cmd_analyze ;;
    *)
        echo "Usage: $0 {start|stop|status|analyze}" >&2
        exit 2
        ;;
esac
