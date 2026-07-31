#!/bin/bash
# MeshForge healthcheck — would CI be green right now?
#
# Runs the same lint + tests CI runs. With --clean-venv, builds a fresh
# venv with ONLY the dep set CI installs, so optional-dep imports that
# pass on the box (where everything is installed) but break in CI
# surface here first.
#
# Driven by today's incident (project_ci_red_2026_05_03_cascade.md):
# CI was red for ~12 hours because local tests passed (real fleet config
# satisfied the assertion accidentally) while CI's clean container
# exposed the bug. This script closes that gap.
#
# Exit codes:
#   0  — all checks pass (CI would be green)
#   1  — lint failed
#   2  — tests failed
#   3  — venv setup failed (--clean-venv only)
#
# Usage:
#   scripts/healthcheck.sh                # quick: lint + tests with current env
#   scripts/healthcheck.sh --clean-venv   # slow but accurate: fresh CI-parity venv
#   scripts/healthcheck.sh --tests-only   # skip lint
#   scripts/healthcheck.sh --lint-only    # skip tests

set -u
set -o pipefail

CLEAN_VENV=0
RUN_LINT=1
RUN_TESTS=1

for arg in "$@"; do
    case "$arg" in
        --clean-venv)  CLEAN_VENV=1 ;;
        --tests-only)  RUN_LINT=0 ;;
        --lint-only)   RUN_TESTS=0 ;;
        --help|-h)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown arg: $arg" >&2
            exit 64
            ;;
    esac
done

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
# Shared pytest-invocation wrapper: pairs "run pytest" with "classify
# it honestly" so no caller here can drift back to a bare rc or a
# tail-piped conditional (see scripts/lib/pytest_checked.sh).
# shellcheck source=lib/pytest_checked.sh
. "$REPO_ROOT/scripts/lib/pytest_checked.sh"

# Match CI's dep set in .github/workflows/ci.yml. Optional deps that CI
# uses `|| echo` to soft-fail are listed separately so --clean-venv mode
# can mirror that behavior precisely.
CI_DEPS_REQUIRED=(pytest pytest-cov pytest-timeout rich textual flask pyyaml requests psutil distro)
CI_DEPS_OPTIONAL=(meshtastic folium)

# CI's actual pytest invocation (matches ci.yml — no -x since 13d54e9).
CI_PYTEST_ARGS=(-v --tb=short --timeout=30 --timeout-method=thread
                --ignore=tests/test_bridge_integration.py
                -o log_cli_level=WARNING)

print_step() { printf "\n\033[1;36m=== %s ===\033[0m\n" "$1"; }
print_ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
print_fail() { printf "\033[1;31m✗\033[0m %s\n" "$1"; }

run_lint() {
    print_step "Lint (MF001-MF016)"
    if python3 scripts/lint.py --all; then
        print_ok "Lint passed"
        return 0
    else
        print_fail "Lint failed"
        return 1
    fi
}

run_tests_local() {
    print_step "Tests (current env)"
    # This was `if python3 -m pytest ... | tail -50; then`. That LOOKS like the
    # banned tail-pipe (exit status of `tail`, always 0) and was first flagged
    # as such — wrongly: `set -o pipefail` at the top of this file propagates
    # pytest's non-zero through the pipe, so the gate did work. What it could
    # not survive is the measured exit-code flap: on the full suite pytest
    # exits 0 while having computed TESTS_FAILED:1 (~50% of runs, 2026-07-28),
    # and pipefail faithfully propagates a zero that was already wrong. Route
    # through the shared classifier, which cross-checks the summary line.
    mf_pytest_checked tests/ "${CI_PYTEST_ARGS[@]}" -q
    local rc=$?
    tail -50 "$MF_PYTEST_LOG"; rm -f "$MF_PYTEST_LOG"
    if [ "$rc" -eq 0 ]; then
        print_ok "Tests passed"
        return 0
    fi
    print_fail "Tests $MF_PYTEST_VERDICT — $MF_PYTEST_WHY"
    return 1
}

run_tests_clean_venv() {
    print_step "Tests (clean venv, CI-parity deps)"
    local venv="${REPO_ROOT}/.healthcheck-venv"
    if [ ! -d "$venv" ]; then
        echo "Creating fresh venv at $venv"
        if ! python3 -m venv "$venv"; then
            print_fail "Could not create venv (try: apt install python3-venv)"
            return 3
        fi
    fi
    # shellcheck disable=SC1091
    source "$venv/bin/activate"
    # Route through the hardened helper (ensure_pip + checked rc) against the
    # venv's own interpreter — no more silent `2>/dev/null` self-upgrade.
    # shellcheck source=lib/install_common.sh
    source "${REPO_ROOT}/scripts/lib/install_common.sh"
    local vpy="$venv/bin/python"
    mf_pip_install "$vpy" --quiet --upgrade pip || echo "  (pip self-upgrade skipped)"
    echo "Installing required CI deps: ${CI_DEPS_REQUIRED[*]}"
    mf_pip_install "$vpy" --quiet "${CI_DEPS_REQUIRED[@]}" \
        || { print_fail "required deps failed"; deactivate; return 3; }
    echo "Installing optional CI deps (soft-fail): ${CI_DEPS_OPTIONAL[*]}"
    for dep in "${CI_DEPS_OPTIONAL[@]}"; do
        mf_pip_install "$vpy" --quiet "$dep" || echo "  (skipped: $dep)"
    done
    # ${PIPESTATUS[0]} correctly captured PYTEST's status rather than tail's —
    # this leg was never the tail-pipe bug. It still trusted that status, which
    # is the signal that flaps. `python3` here is the ACTIVATED venv's
    # interpreter, and mf_pytest_checked defaults to python3, so the classifier
    # judges the clean-venv run and not the host one.
    local rc=0
    CI=true MESHFORGE_CI=true mf_pytest_checked tests/ "${CI_PYTEST_ARGS[@]}" -q
    rc=$?
    tail -50 "$MF_PYTEST_LOG"; rm -f "$MF_PYTEST_LOG"
    deactivate
    if [ "$rc" -eq 0 ]; then
        print_ok "Tests passed in clean venv"
    else
        print_fail "Tests in clean venv: $MF_PYTEST_VERDICT — $MF_PYTEST_WHY"
    fi
    return $rc
}

# ----------------------------------------------------------------------
RC=0

if [ "$RUN_LINT" -eq 1 ]; then
    run_lint || RC=1
fi

if [ "$RUN_TESTS" -eq 1 ] && [ "$RC" -eq 0 ]; then
    if [ "$CLEAN_VENV" -eq 1 ]; then
        run_tests_clean_venv || RC=2
    else
        run_tests_local || RC=2
    fi
fi

print_step "Summary"
if [ "$RC" -eq 0 ]; then
    print_ok "All checks passed — CI should be green on push"
else
    print_fail "Failures detected — fix before pushing (saves a CI round trip)"
fi
exit "$RC"
