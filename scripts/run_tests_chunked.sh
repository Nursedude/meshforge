#!/usr/bin/env bash
# Chunked test runner — splits the 4,692-test suite into topic buckets so
# each pytest invocation is bounded and the OS reclaims memory between
# chunks. A crash isolates to one chunk and you know exactly where to look.
#
# Usage:
#   scripts/run_tests_chunked.sh                  # run every chunk
#   scripts/run_tests_chunked.sh gateway mqtt     # named chunks only
#   scripts/run_tests_chunked.sh --list           # show chunks + file counts
#   scripts/run_tests_chunked.sh --help
#
# Env:
#   PYTEST_ARGS  extra pytest args (default: "-x --tb=line -q")
#   PYTEST       pytest invocation       (default: "python3 -m pytest")
#
# Per-chunk default flags stop on first failure with one-line tracebacks —
# the suite usually finishes a chunk in well under a minute when green and
# bails fast when red.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Chunk order matters: earlier chunks claim matching files first, later
# chunks pick up what remains. "remainder" is a sentinel that sweeps any
# unclaimed top-level test_*.py so nothing is silently dropped.
CHUNK_NAMES=(
    gateway
    mqtt
    rns
    radio_rf
    db
    fleet_map
    tui
    lab
    core
    e2e
    remainder
)

chunk_patterns() {
    case "$1" in
        gateway)
            cat <<'PATTERNS'
test_rns_bridge.py
test_bridge_*.py
test_canonical_message.py
test_composable_bridges.py
test_chunker.py
test_message_*.py
test_meshtastic_broadcast_bridge.py
test_meshtastic_connection.py
test_meshtastic_protobuf.py
test_meshtastic_http.py
test_meshtastic_handler.py
test_meshcore_handler.py
test_meshcore_channel_metrics.py
test_connection_manager.py
test_contact_mapping.py
test_channel_resolver.py
test_gateway_*.py
test_circuit_breaker.py
test_bounded_rpc.py
test_wedge_events.py
test_tribridge_integration.py
test_reconnect.py
test_socket_cleanup.py
test_ack_synthesis_e2e_issue66.py
test_mqtt_bridge_handler.py
test_delivery_counters.py
PATTERNS
            ;;
        mqtt)
            cat <<'PATTERNS'
test_mqtt_*.py
test_broker_profiles.py
test_mesh_bridge_*.py
PATTERNS
            ;;
        rns)
            cat <<'PATTERNS'
test_rns_*.py
test_zombie_shared_instance.py
test_nomadnet_handler.py
PATTERNS
            ;;
        radio_rf)
            cat <<'PATTERNS'
test_rf.py
test_aredn_parser.py
test_compliance.py
test_x1_codec.py
test_qr_transport.py
test_pcap_export.py
test_unit_compare.py
test_radio_*.py
test_failover_*.py
test_hardware_detection.py
test_hat_overlay_sanitizer.py
test_usb_template_matching.py
PATTERNS
            ;;
        db)
            cat <<'PATTERNS'
test_db_*.py
test_*_db.py
test_node_history.py
test_node_state.py
test_node_tracker.py
test_offline_sync_db.py
test_migrate_map_service.py
test_install_map_service.py
PATTERNS
            ;;
        fleet_map)
            cat <<'PATTERNS'
test_fleet_*.py
test_map_*.py
test_tactical_*.py
test_cascade_*.py
test_mesh_alert_engine.py
PATTERNS
            ;;
        tui)
            cat <<'PATTERNS'
test_handlers_*.py
test_all_handlers_protocol.py
test_handler_registry.py
test_*_handler.py
test_status_bar.py
test_status_consistency.py
test_phase1_handlers.py
test_tui_runtime_stability.py
test_commands.py
test_device_config_store.py
test_startup_health.py
test_meshchatx_handler.py
test_install_meshchatx.py
PATTERNS
            ;;
        lab)
            cat <<'PATTERNS'
test_lab_*.py
test_orchestrator.py
test_tracer_fires.py
PATTERNS
            ;;
        core)
            cat <<'PATTERNS'
test_common.py
test_paths.py
test_safe_import.py
test_event_bus.py
test_security.py
test_service_check.py
test_timeouts.py
test_validate_claude_settings.py
test_global_config.py
test_deployment_profiles.py
test_config_*.py
test_daemon.py
test_sandbox_check.py
test_lint_mf017.py
test_regression_guards.py
test_auto_traceroute.py
test_boundary_timing.py
test_preflight_template.py
PATTERNS
            ;;
        e2e)
            cat <<'PATTERNS'
e2e/
PATTERNS
            ;;
        remainder)
            echo "*"
            ;;
        *)
            return 1
            ;;
    esac
}

# Globals mutated by expand_chunk so dedup state persists across calls.
# Process substitution would put mutations in a subshell, so we instead
# write to CHUNK_FILES directly and read it back in the caller.
declare -A CLAIMED
declare -a CHUNK_FILES

expand_chunk() {
    local chunk="$1"
    CHUNK_FILES=()
    local pat path base patterns
    patterns="$(chunk_patterns "$chunk")"
    while IFS= read -r pat; do
        [[ -z "$pat" ]] && continue
        if [[ "$pat" == "e2e/" ]]; then
            if [[ -z "${CLAIMED["e2e/"]:-}" && -d tests/e2e ]]; then
                CHUNK_FILES+=("tests/e2e/")
                CLAIMED["e2e/"]=1
            fi
            continue
        fi
        if [[ "$pat" == "*" ]]; then
            for path in tests/test_*.py; do
                [[ -e "$path" ]] || continue
                base="${path#tests/}"
                if [[ -z "${CLAIMED[$base]:-}" ]]; then
                    CHUNK_FILES+=("$path")
                    CLAIMED[$base]=1
                fi
            done
            continue
        fi
        for path in tests/$pat; do
            [[ -e "$path" ]] || continue
            base="${path#tests/}"
            if [[ -z "${CLAIMED[$base]:-}" ]]; then
                CHUNK_FILES+=("$path")
                CLAIMED[$base]=1
            fi
        done
    done <<<"$patterns"
}

LIST_MODE=0
REQUESTED=()
for arg in "$@"; do
    case "$arg" in
        --list|-l) LIST_MODE=1 ;;
        --help|-h)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        -*) echo "unknown flag: $arg" >&2; exit 2 ;;
        *) REQUESTED+=("$arg") ;;
    esac
done

if [[ ${#REQUESTED[@]} -eq 0 ]]; then
    REQUESTED=("${CHUNK_NAMES[@]}")
fi

for name in "${REQUESTED[@]}"; do
    found=0
    for c in "${CHUNK_NAMES[@]}"; do
        [[ "$c" == "$name" ]] && found=1
    done
    if [[ $found -eq 0 ]]; then
        echo "unknown chunk: $name" >&2
        echo "available: ${CHUNK_NAMES[*]}" >&2
        exit 2
    fi
done

# Re-order requested chunks to canonical order so dedup is deterministic.
ORDERED=()
for c in "${CHUNK_NAMES[@]}"; do
    for r in "${REQUESTED[@]}"; do
        if [[ "$c" == "$r" ]]; then
            ORDERED+=("$c")
            break
        fi
    done
done

if [[ $LIST_MODE -eq 1 ]]; then
    printf '%-12s  %s\n' "chunk" "files"
    printf '%-12s  %s\n' "------" "-----"
    for c in "${ORDERED[@]}"; do
        expand_chunk "$c"
        printf '%-12s  %d\n' "$c" "${#CHUNK_FILES[@]}"
    done
    exit 0
fi

PYTEST="${PYTEST:-python3 -m pytest}"
PYTEST_ARGS="${PYTEST_ARGS:--x --tb=line -q}"
failed_chunks=()
start_total=$SECONDS

for c in "${ORDERED[@]}"; do
    expand_chunk "$c"
    if [[ ${#CHUNK_FILES[@]} -eq 0 ]]; then
        echo "── chunk $c: (no files, skipping)"
        continue
    fi
    echo
    echo "══════════════════════════════════════════════════════════════"
    echo "── chunk $c (${#CHUNK_FILES[@]} files)"
    echo "══════════════════════════════════════════════════════════════"
    chunk_start=$SECONDS
    # shellcheck disable=SC2086  # PYTEST_ARGS is intentionally word-split
    if ! $PYTEST $PYTEST_ARGS "${CHUNK_FILES[@]}"; then
        failed_chunks+=("$c")
        echo "── chunk $c FAILED in $((SECONDS - chunk_start))s"
    else
        echo "── chunk $c OK in $((SECONDS - chunk_start))s"
    fi
done

echo
echo "══════════════════════════════════════════════════════════════"
total=$((SECONDS - start_total))
if [[ ${#failed_chunks[@]} -eq 0 ]]; then
    echo "── all chunks passed in ${total}s"
    exit 0
else
    echo "── ${#failed_chunks[@]} failed chunk(s): ${failed_chunks[*]} (total ${total}s)"
    exit 1
fi
