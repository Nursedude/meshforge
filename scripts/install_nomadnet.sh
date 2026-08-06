#!/usr/bin/env bash
# MeshForge — canonical NomadNet installer (idempotent, pipx-first)
# =================================================================
#
# Single source of truth for the NomadNet install layout. Run after any
# "major fix" in MeshForge to converge the local install + wrapper +
# user unit + RNS alignment, regardless of where the box drifted from.
#
# Modes:
#   ./install_nomadnet.sh              # install if missing, refresh
#                                      # wrapper + unit if drifted
#   ./install_nomadnet.sh --check      # read-only audit, exit 0/1
#   ./install_nomadnet.sh --refresh    # force-rewrite wrapper + unit
#   ./install_nomadnet.sh --reinstall  # pipx uninstall + reinstall
#                                      # (preserves identity)
#   ./install_nomadnet.sh --reinstall --wipe-identity
#                                      # also clears ~/.nomadnetwork/
#
# Sister to scripts/rns_alignment.py — that one normalizes rnsd config,
# this one normalizes the nomadnet client install. Both must succeed
# for a working tmux-wrapped nomadnet.service.
#
# See .claude/foundations/persistent_issues*.md Issue #46 for context
# (resolved-issue index moved to the archive 2026-08-05, MF012).

set -euo pipefail

# --------------------------------------------------------------------
# Resolve real user (this script may be invoked under sudo)
# --------------------------------------------------------------------
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    REAL_USER="${SUDO_USER}"
else
    REAL_USER="$(id -un)"
fi

if [[ "${REAL_USER}" == "root" ]]; then
    echo "ERROR: refuses to install NomadNet for root." >&2
    echo "  Run as a normal user, or via 'sudo -u <user> $0'." >&2
    exit 2
fi

REAL_HOME="$(getent passwd "${REAL_USER}" | cut -d: -f6)"
if [[ -z "${REAL_HOME}" || ! -d "${REAL_HOME}" ]]; then
    echo "ERROR: cannot resolve home directory for ${REAL_USER}" >&2
    exit 2
fi
REAL_UID="$(id -u "${REAL_USER}")"

# Run a command AS the real user. When invoked under sudo we drop down;
# when invoked directly we just exec.
run_as_user() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        "$@"
    else
        sudo -u "${REAL_USER}" -H "$@"
    fi
}

# Same, but inheriting the user's $PATH (for pipx, which lives in
# ~/.local/bin and may not be on root's PATH under sudo).
run_as_user_with_path() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        "$@"
    else
        sudo -u "${REAL_USER}" -H \
            env PATH="${REAL_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin" \
            "$@"
    fi
}

# User-scope systemctl that bridges sudo→user (Issue #45 documented incantation).
run_user_systemctl() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        systemctl --user "$@"
    else
        sudo -u "${REAL_USER}" -H \
            env "XDG_RUNTIME_DIR=/run/user/${REAL_UID}" \
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${REAL_UID}/bus" \
            systemctl --user "$@"
    fi
}

# --------------------------------------------------------------------
# Repo root + canonical paths
# --------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

WRAPPER_TEMPLATE="${REPO_ROOT}/templates/python/nomadnet_wrapper.py"
UNIT_TEMPLATE="${REPO_ROOT}/templates/systemd/nomadnet-user.service"
RNS_ALIGN_CLI="${REPO_ROOT}/scripts/rns_alignment.py"

WRAPPER_DEST="${REAL_HOME}/.config/meshforge/nomadnet_wrapper.py"
UNIT_DEST="${REAL_HOME}/.config/systemd/user/nomadnet.service"
NN_BIN="${REAL_HOME}/.local/bin/nomadnet"
IDENTITY_DIR="${REAL_HOME}/.nomadnetwork"

# --------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------
MODE="default"   # default | check | refresh | reinstall
WIPE_IDENTITY="no"

while (( "$#" )); do
    case "$1" in
        --check)        MODE="check" ;;
        --refresh)      MODE="refresh" ;;
        --reinstall)    MODE="reinstall" ;;
        --wipe-identity) WIPE_IDENTITY="yes" ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "  See: $0 --help" >&2
            exit 2
            ;;
    esac
    shift
done

if [[ "${WIPE_IDENTITY}" == "yes" && "${MODE}" != "reinstall" ]]; then
    echo "ERROR: --wipe-identity is only valid with --reinstall" >&2
    exit 2
fi

# --------------------------------------------------------------------
# Step 1: detect existing install method
# --------------------------------------------------------------------
detect_install_method() {
    # Prints one of: pipx | pip-user | apt | unknown | missing
    local nn_resolved
    if [[ ! -e "${NN_BIN}" ]]; then
        # Maybe ~/.local/bin/nomadnet is missing but a system one exists?
        if command -v nomadnet >/dev/null 2>&1; then
            local sys_nn
            sys_nn="$(command -v nomadnet)"
            if [[ "${sys_nn}" == /usr/* ]]; then
                echo "apt"
                return
            fi
            echo "unknown"
            return
        fi
        echo "missing"
        return
    fi

    nn_resolved="$(readlink -f "${NN_BIN}")"
    case "${nn_resolved}" in
        */pipx/venvs/nomadnet/bin/nomadnet)
            echo "pipx"
            ;;
        "${REAL_HOME}/.local/bin/nomadnet")
            # Not a symlink — pip --user dropped a real script
            echo "pip-user"
            ;;
        *)
            # Unknown layout — could still be pipx if the user moved
            # things around. Trust the symlink if there's a python3
            # next to the resolved binary.
            if [[ -x "$(dirname "${nn_resolved}")/python3" ]]; then
                echo "pipx"
            else
                echo "unknown"
            fi
            ;;
    esac
}

# --------------------------------------------------------------------
# Step 2: derive the canonical wrapper command
# Echoes the ExecStart inner command (without tmux), or empty if pipx
# venv python isn't found.
# --------------------------------------------------------------------
canonical_exec_command() {
    if [[ ! -e "${NN_BIN}" ]]; then
        return
    fi
    local nn_resolved venv_python
    nn_resolved="$(readlink -f "${NN_BIN}")"
    venv_python="$(dirname "${nn_resolved}")/python3"
    if [[ ! -x "${venv_python}" ]]; then
        # No pipx venv adjacent → cannot compose the wrapper command.
        return
    fi
    printf '%s %s --rnsconfig /etc/reticulum' \
        "${venv_python}" "${WRAPPER_DEST}"
}

# --------------------------------------------------------------------
# Step 3: ensure pipx itself is available (apt-installs if needed)
# --------------------------------------------------------------------
ensure_pipx() {
    if run_as_user_with_path command -v pipx >/dev/null 2>&1; then
        return
    fi
    echo "pipx not found — installing via apt..."
    if [[ "$(id -un)" == "root" ]]; then
        apt-get update -qq
        apt-get install -y pipx
    else
        sudo apt-get update -qq
        sudo apt-get install -y pipx
    fi
    run_as_user_with_path pipx ensurepath >/dev/null 2>&1 || true
}

# --------------------------------------------------------------------
# Step 4: pipx-install nomadnet (used by default + reinstall)
# --------------------------------------------------------------------
pipx_install_nomadnet() {
    echo "Installing nomadnet via pipx (as ${REAL_USER})..."
    run_as_user_with_path pipx install nomadnet
}

pipx_uninstall_nomadnet() {
    echo "Uninstalling nomadnet via pipx (as ${REAL_USER})..."
    run_as_user_with_path pipx uninstall nomadnet || true
}

# --------------------------------------------------------------------
# Step 5: write canonical wrapper
# --------------------------------------------------------------------
write_wrapper() {
    if [[ ! -f "${WRAPPER_TEMPLATE}" ]]; then
        echo "ERROR: wrapper template missing at ${WRAPPER_TEMPLATE}" >&2
        return 1
    fi
    run_as_user mkdir -p "$(dirname "${WRAPPER_DEST}")"
    # cp doesn't honor sudo target ownership reliably; use install(1)
    install -o "${REAL_USER}" -g "${REAL_USER}" -m 0644 \
        "${WRAPPER_TEMPLATE}" "${WRAPPER_DEST}"
    echo "  wrapper:   ${WRAPPER_DEST}"
}

wrapper_is_current() {
    # Compare bytes against the template — they are intended to be
    # identical (no per-box substitution in the wrapper body).
    [[ -f "${WRAPPER_DEST}" ]] && \
        cmp -s "${WRAPPER_TEMPLATE}" "${WRAPPER_DEST}"
}

# --------------------------------------------------------------------
# Step 6: render + write systemd user unit
# --------------------------------------------------------------------
write_unit() {
    local exec_cmd
    exec_cmd="$(canonical_exec_command)"
    if [[ -z "${exec_cmd}" ]]; then
        echo "ERROR: cannot derive ExecStart — pipx venv python missing" >&2
        echo "  (~/.local/bin/nomadnet must symlink into a venv with python3)" >&2
        return 1
    fi
    if [[ ! -f "${UNIT_TEMPLATE}" ]]; then
        echo "ERROR: unit template missing at ${UNIT_TEMPLATE}" >&2
        return 1
    fi
    if ! grep -q '__NOMADNET_EXEC__' "${UNIT_TEMPLATE}"; then
        echo "ERROR: unit template lacks __NOMADNET_EXEC__ placeholder" >&2
        return 1
    fi

    run_as_user mkdir -p "$(dirname "${UNIT_DEST}")"
    # Substitute the placeholder. exec_cmd contains no single-quote
    # characters (paths + flags only), so the template's outer
    # `tmux new-session ... '<exec>'` quoting is preserved.
    local rendered
    rendered="$(sed "s|__NOMADNET_EXEC__|${exec_cmd}|" "${UNIT_TEMPLATE}")"
    printf '%s\n' "${rendered}" | install -o "${REAL_USER}" \
        -g "${REAL_USER}" -m 0644 /dev/stdin "${UNIT_DEST}"
    echo "  unit:      ${UNIT_DEST}"
    echo "  ExecStart: ${exec_cmd}"
}

unit_is_current() {
    local exec_cmd rendered
    exec_cmd="$(canonical_exec_command)"
    [[ -z "${exec_cmd}" ]] && return 1
    [[ ! -f "${UNIT_DEST}" ]] && return 1
    rendered="$(sed "s|__NOMADNET_EXEC__|${exec_cmd}|" "${UNIT_TEMPLATE}")"
    diff -q <(printf '%s\n' "${rendered}") "${UNIT_DEST}" >/dev/null 2>&1
}

# --------------------------------------------------------------------
# Step 7: ensure tmux available (required by unit ExecStart)
# --------------------------------------------------------------------
ensure_tmux() {
    if command -v tmux >/dev/null 2>&1; then
        return
    fi
    echo "tmux not found — installing via apt..."
    if [[ "$(id -un)" == "root" ]]; then
        apt-get install -y tmux
    else
        sudo apt-get install -y tmux
    fi
}

# --------------------------------------------------------------------
# Step 8: RNS alignment audit (precondition for a working install)
# --------------------------------------------------------------------
rns_alignment_ok() {
    if [[ ! -f "${RNS_ALIGN_CLI}" ]]; then
        echo "WARN: ${RNS_ALIGN_CLI} not found — skipping alignment check" >&2
        return 0
    fi
    # `audit` exits 0 when aligned, non-zero when drifted.
    python3 "${RNS_ALIGN_CLI}" audit >/dev/null 2>&1
}

# --------------------------------------------------------------------
# Step 9: enable linger + activate unit
# --------------------------------------------------------------------
activate_unit() {
    # Linger lets the user manager (and our nomadnet.service) keep
    # running across logout — required on headless Pis.
    if [[ "$(id -un)" == "root" ]]; then
        loginctl enable-linger "${REAL_USER}" 2>/dev/null || true
    else
        sudo loginctl enable-linger "${REAL_USER}" 2>/dev/null || true
    fi
    run_user_systemctl daemon-reload
    run_user_systemctl enable nomadnet
    run_user_systemctl start nomadnet
}

# --------------------------------------------------------------------
# Mode: --check (read-only)
# --------------------------------------------------------------------
do_check() {
    local method exec_cmd drift=0
    method="$(detect_install_method)"

    echo "NomadNet canonical install audit (host: $(hostname), user: ${REAL_USER})"
    echo "  install method:   ${method}"

    if [[ "${method}" != "pipx" ]]; then
        echo "  DRIFT: not pipx-installed (run: $0 --reinstall)"
        drift=$((drift + 1))
    fi

    exec_cmd="$(canonical_exec_command)"
    if [[ -z "${exec_cmd}" ]]; then
        echo "  DRIFT: cannot derive canonical ExecStart"
        drift=$((drift + 1))
    else
        echo "  ExecStart:        ${exec_cmd}"
    fi

    if wrapper_is_current; then
        echo "  wrapper:          OK (${WRAPPER_DEST})"
    else
        echo "  DRIFT: wrapper missing or stale (${WRAPPER_DEST})"
        drift=$((drift + 1))
    fi

    if unit_is_current; then
        echo "  unit:             OK (${UNIT_DEST})"
    else
        echo "  DRIFT: unit missing or stale (${UNIT_DEST})"
        drift=$((drift + 1))
    fi

    if rns_alignment_ok; then
        echo "  rns alignment:    OK"
    else
        echo "  DRIFT: rns alignment audit failed"
        echo "    (run: sudo python3 ${RNS_ALIGN_CLI} normalize)"
        drift=$((drift + 1))
    fi

    if (( drift == 0 )); then
        echo "RESULT: aligned"
        return 0
    else
        echo "RESULT: drifted (${drift} issue(s))"
        return 1
    fi
}

# --------------------------------------------------------------------
# Mode: install (default)
# --------------------------------------------------------------------
do_install() {
    local method
    method="$(detect_install_method)"

    case "${method}" in
        missing)
            echo "NomadNet not installed — installing via pipx..."
            ensure_pipx
            pipx_install_nomadnet
            ;;
        pipx)
            echo "NomadNet already installed via pipx — refreshing wrapper + unit only."
            ;;
        pip-user|apt|unknown)
            cat >&2 <<EOF
WARN: NomadNet appears to be installed via '${method}', not pipx.
  This script standardizes on pipx for predictable venv layout.
  Migrate with:
      $0 --reinstall
  (preserves ~/.nomadnetwork/ identity by default; add
  --wipe-identity to clear that too)
  Continuing with refresh only — the unit will reference the
  existing binary, which may break after future "major fix" runs.
EOF
            ;;
    esac
}

do_reinstall() {
    if [[ "${WIPE_IDENTITY}" == "yes" ]]; then
        if [[ -d "${IDENTITY_DIR}" ]]; then
            echo "Wiping ${IDENTITY_DIR} (--wipe-identity)..."
            run_as_user rm -rf "${IDENTITY_DIR}"
        fi
    else
        echo "Preserving ${IDENTITY_DIR}"
    fi

    ensure_pipx
    pipx_uninstall_nomadnet
    pipx_install_nomadnet
}

# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------
case "${MODE}" in
    check)
        do_check
        exit $?
        ;;
    reinstall)
        do_reinstall
        ;;
    refresh|default)
        do_install
        ;;
esac

# After install/reinstall/refresh, wrapper + unit are always written
# (idempotent — no-op when they already match).
echo
echo "=== Refreshing wrapper + unit ==="
ensure_tmux
write_wrapper
write_unit

echo
echo "=== RNS alignment precondition ==="
if rns_alignment_ok; then
    echo "  alignment: OK"
else
    cat >&2 <<EOF

ERROR: RNS alignment audit failed. The nomadnet.service will not
boot cleanly until rnsd is normalized.

  Fix first:
    sudo python3 ${RNS_ALIGN_CLI} normalize
  Then re-run:
    $0 --refresh

EOF
    exit 3
fi

echo
echo "=== Activating nomadnet.service (user scope) ==="
activate_unit

echo
echo "=== Verifying ==="
sleep 5
if run_user_systemctl is-active nomadnet >/dev/null 2>&1; then
    echo "  nomadnet.service: ACTIVE"
    echo
    echo "Attach with:  tmux attach -t nomadnet"
    exit 0
else
    echo "WARN: nomadnet.service did not become active within 5s." >&2
    echo "  Inspect: journalctl --user -u nomadnet -n 50 --no-pager" >&2
    exit 4
fi
