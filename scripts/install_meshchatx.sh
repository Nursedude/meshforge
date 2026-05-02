#!/usr/bin/env bash
# MeshForge — canonical MeshChatX installer (idempotent, pipx-first)
# ===================================================================
#
# Single source of truth for the MeshChatX install layout. Run after
# any "major fix" in MeshForge to converge the local install + wrapper
# + user unit, regardless of where the box drifted from.
#
# MeshChatX is a third-party LXMF web chat client (RNS-Things org on
# git.quad4.io). It coexists side-by-side with NomadNet — each has its
# own LXMF identity. The canonical NomadNet installer is the model for
# this script (scripts/install_nomadnet.sh); the structural deltas are:
#
#   - Wheel fetch from gitea releases API (MeshChatX is not on PyPI).
#   - Shell wrapper, not Python wrapper (MeshChatX is a web daemon, not
#     a TUI that imports RNS in-process).
#   - systemd-user unit is Type=simple, no tmux wrapping (the daemon
#     doesn't need an interactive terminal).
#   - "Liveness" is the listening socket on 127.0.0.1:8000, not a tmux
#     session.
#
# Modes:
#   ./install_meshchatx.sh              # install if missing, refresh
#                                       # wrapper + unit if drifted
#   ./install_meshchatx.sh --check      # read-only audit, exit 0/1
#   ./install_meshchatx.sh --refresh    # force-rewrite wrapper + unit
#   ./install_meshchatx.sh --reinstall  # pipx uninstall + reinstall
#                                       # (preserves identity)
#   ./install_meshchatx.sh --reinstall --wipe-identity
#                                       # also clears ~/.local/share/meshchatx/
#
# See the parallel:
#   scripts/install_nomadnet.sh                 (model for this script)
#   .claude/foundations/persistent_issues.md    (Issues #41, #46)

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
    echo "ERROR: refuses to install MeshChatX for root." >&2
    echo "  Run as a normal user, or via 'sudo -u <user> $0'." >&2
    exit 2
fi

REAL_HOME="$(getent passwd "${REAL_USER}" | cut -d: -f6)"
if [[ -z "${REAL_HOME}" || ! -d "${REAL_HOME}" ]]; then
    echo "ERROR: cannot resolve home directory for ${REAL_USER}" >&2
    exit 2
fi
REAL_UID="$(id -u "${REAL_USER}")"

run_as_user() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        "$@"
    else
        sudo -u "${REAL_USER}" -H "$@"
    fi
}

run_as_user_with_path() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        "$@"
    else
        sudo -u "${REAL_USER}" -H \
            env PATH="${REAL_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin" \
            "$@"
    fi
}

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

WRAPPER_TEMPLATE="${REPO_ROOT}/templates/python/meshchatx_wrapper.sh"
UNIT_TEMPLATE="${REPO_ROOT}/templates/systemd/meshchatx-user.service"

WRAPPER_DEST="${REAL_HOME}/.config/meshforge/meshchatx_wrapper.sh"
UNIT_DEST="${REAL_HOME}/.config/systemd/user/meshchatx.service"
MCX_BIN="${REAL_HOME}/.local/bin/meshchatx"
STORAGE_DIR="${REAL_HOME}/.local/share/meshchatx"

# Upstream release source. v4.6+ may move to GitHub per release notes;
# gitea remains canonical until that switchover is field-validated.
RELEASES_API="https://git.quad4.io/api/v1/repos/RNS-Things/MeshChatX/releases/latest"

# --------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------
MODE="default"
WIPE_IDENTITY="no"

while (( "$#" )); do
    case "$1" in
        --check)         MODE="check" ;;
        --refresh)       MODE="refresh" ;;
        --reinstall)     MODE="reinstall" ;;
        --wipe-identity) WIPE_IDENTITY="yes" ;;
        -h|--help)
            sed -n '2,32p' "$0"
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
    local mcx_resolved
    if [[ ! -e "${MCX_BIN}" ]]; then
        if command -v meshchatx >/dev/null 2>&1; then
            local sys_mcx
            sys_mcx="$(command -v meshchatx)"
            case "${sys_mcx}" in
                /usr/*) echo "system"; return ;;
                *)      echo "unknown"; return ;;
            esac
        fi
        echo "missing"
        return
    fi

    mcx_resolved="$(readlink -f "${MCX_BIN}")"
    case "${mcx_resolved}" in
        */pipx/venvs/*/bin/meshchatx)
            echo "pipx"
            ;;
        "${REAL_HOME}/.local/bin/meshchatx")
            # Real script (not a symlink) — pip --user install
            echo "pip-user"
            ;;
        *)
            if [[ -x "$(dirname "${mcx_resolved}")/python3" ]]; then
                echo "pipx"
            else
                echo "unknown"
            fi
            ;;
    esac
}

# --------------------------------------------------------------------
# Step 2: derive canonical wrapper invocation for the systemd unit.
# Echoes the inner ExecStart line, or empty when the wrapper template
# is missing.
# --------------------------------------------------------------------
canonical_exec_command() {
    if [[ ! -f "${WRAPPER_DEST}" ]]; then
        return
    fi
    printf '%s' "${WRAPPER_DEST}"
}

# --------------------------------------------------------------------
# Step 3: ensure pipx itself is available
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
# Step 4: fetch latest wheel URL from gitea releases API.
# Echoes "<tag>|<wheel_url>", or empty + non-zero on failure.
# --------------------------------------------------------------------
fetch_latest_wheel() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "ERROR: curl is required to fetch the MeshChatX wheel" >&2
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: python3 is required to parse the gitea API response" >&2
        return 1
    fi

    local json
    if ! json="$(curl -sf -m 30 "${RELEASES_API}")"; then
        echo "ERROR: failed to GET ${RELEASES_API}" >&2
        return 1
    fi

    # Use python3 (already a hard dep of MeshForge) to parse JSON.
    # JSON arrives via stdin (echo | python3 -c '...').
    echo "${json}" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception as exc:
    sys.stderr.write(f"ERROR: failed to parse gitea response: {exc}\n")
    sys.exit(1)
tag = data.get("tag_name") or ""
url = ""
for asset in data.get("assets") or []:
    name = asset.get("name") or ""
    if name.endswith(".whl") and "py3-none-any" in name:
        url = asset.get("browser_download_url") or ""
        break
if not tag or not url:
    sys.stderr.write("ERROR: no wheel asset found in latest release\n")
    sys.exit(1)
print(f"{tag}|{url}")
'
}

pipx_install_meshchatx() {
    echo "Resolving latest MeshChatX wheel from gitea..."
    local meta tag url wheel_path
    meta="$(fetch_latest_wheel)"
    if [[ -z "${meta}" ]]; then
        echo "ERROR: cannot resolve a wheel URL — aborting install." >&2
        return 1
    fi
    tag="${meta%%|*}"
    url="${meta##*|}"
    wheel_path="/tmp/meshchatx-${tag}.whl"
    echo "  release:  ${tag}"
    echo "  wheel:    ${url}"

    if [[ ! -f "${wheel_path}" ]]; then
        echo "  fetching to ${wheel_path}..."
        if ! curl -sfL -m 180 -o "${wheel_path}" "${url}"; then
            echo "ERROR: wheel download failed" >&2
            rm -f "${wheel_path}"
            return 1
        fi
    else
        echo "  cached at ${wheel_path}"
    fi
    chown "${REAL_USER}:${REAL_USER}" "${wheel_path}" 2>/dev/null || true

    echo "Installing meshchatx via pipx (as ${REAL_USER})..."
    run_as_user_with_path pipx install "${wheel_path}"
}

pipx_uninstall_meshchatx() {
    echo "Uninstalling meshchatx via pipx (as ${REAL_USER})..."
    # The pipx package name from the wheel is 'reticulum-meshchatx',
    # not 'meshchatx'. Try both for robustness.
    run_as_user_with_path pipx uninstall reticulum-meshchatx 2>/dev/null \
        || run_as_user_with_path pipx uninstall meshchatx 2>/dev/null \
        || true
}

# --------------------------------------------------------------------
# Step 5: write canonical wrapper (shell)
# --------------------------------------------------------------------
write_wrapper() {
    if [[ ! -f "${WRAPPER_TEMPLATE}" ]]; then
        echo "ERROR: wrapper template missing at ${WRAPPER_TEMPLATE}" >&2
        return 1
    fi
    run_as_user mkdir -p "$(dirname "${WRAPPER_DEST}")"
    install -o "${REAL_USER}" -g "${REAL_USER}" -m 0755 \
        "${WRAPPER_TEMPLATE}" "${WRAPPER_DEST}"
    echo "  wrapper:   ${WRAPPER_DEST}"
}

wrapper_is_current() {
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
        echo "ERROR: cannot derive ExecStart — wrapper not yet written" >&2
        return 1
    fi
    if [[ ! -f "${UNIT_TEMPLATE}" ]]; then
        echo "ERROR: unit template missing at ${UNIT_TEMPLATE}" >&2
        return 1
    fi
    if ! grep -q '__MESHCHATX_EXEC__' "${UNIT_TEMPLATE}"; then
        echo "ERROR: unit template lacks __MESHCHATX_EXEC__ placeholder" >&2
        return 1
    fi

    run_as_user mkdir -p "$(dirname "${UNIT_DEST}")"
    local rendered
    rendered="$(sed "s|__MESHCHATX_EXEC__|${exec_cmd}|" "${UNIT_TEMPLATE}")"
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
    rendered="$(sed "s|__MESHCHATX_EXEC__|${exec_cmd}|" "${UNIT_TEMPLATE}")"
    diff -q <(printf '%s\n' "${rendered}") "${UNIT_DEST}" >/dev/null 2>&1
}

# --------------------------------------------------------------------
# Step 7: ensure storage dir exists with correct ownership
# --------------------------------------------------------------------
ensure_storage_dir() {
    run_as_user mkdir -p "${STORAGE_DIR}"
    chown "${REAL_USER}:${REAL_USER}" "${STORAGE_DIR}" 2>/dev/null || true
}

# --------------------------------------------------------------------
# Step 8: rpc_key precondition (Issue #41).
# MeshChatX uses RNS shared instance and inherits rnsd's rpc_key from
# the active config. If rpc_key is unpinned, the daemon will fail with
# AuthenticationError on every RPC call.
# --------------------------------------------------------------------
rpc_key_pinned() {
    local cfg
    if [[ -r /etc/reticulum/config ]]; then
        cfg=/etc/reticulum/config
    elif [[ -r "${REAL_HOME}/.reticulum/config" ]]; then
        cfg="${REAL_HOME}/.reticulum/config"
    else
        return 1
    fi
    grep -qE '^[[:space:]]*rpc_key[[:space:]]*=[[:space:]]*[0-9a-fA-F]{64}' "${cfg}"
}

# --------------------------------------------------------------------
# Step 9: enable linger + activate unit
# --------------------------------------------------------------------
activate_unit() {
    if [[ "$(id -un)" == "root" ]]; then
        loginctl enable-linger "${REAL_USER}" 2>/dev/null || true
    else
        sudo loginctl enable-linger "${REAL_USER}" 2>/dev/null || true
    fi
    run_user_systemctl daemon-reload
    run_user_systemctl enable meshchatx
    run_user_systemctl start meshchatx
}

# --------------------------------------------------------------------
# Mode: --check (read-only)
# --------------------------------------------------------------------
do_check() {
    local method drift=0
    method="$(detect_install_method)"

    echo "MeshChatX canonical install audit (host: $(hostname), user: ${REAL_USER})"
    echo "  install method:   ${method}"

    if [[ "${method}" != "pipx" ]]; then
        echo "  DRIFT: not pipx-installed (run: $0 --reinstall)"
        drift=$((drift + 1))
    fi

    if [[ -d "${STORAGE_DIR}" ]]; then
        echo "  storage dir:      ${STORAGE_DIR} (OK)"
    else
        echo "  DRIFT: storage dir missing (${STORAGE_DIR})"
        drift=$((drift + 1))
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

    if rpc_key_pinned; then
        echo "  rpc_key:          pinned (Issue #41 OK)"
    else
        echo "  DRIFT: rpc_key not pinned in active RNS config"
        echo "    (run: sudo python3 ${REPO_ROOT}/scripts/rns_alignment.py normalize)"
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
            echo "MeshChatX not installed — installing via pipx..."
            ensure_pipx
            pipx_install_meshchatx
            ;;
        pipx)
            echo "MeshChatX already installed via pipx — refreshing wrapper + unit only."
            ;;
        pip-user|system|unknown)
            cat >&2 <<EOF
WARN: MeshChatX appears to be installed via '${method}', not pipx.
  This script standardizes on pipx for predictable venv layout.
  Migrate with:
      $0 --reinstall
  (preserves ${STORAGE_DIR} identity by default; add
  --wipe-identity to clear that too)
  Continuing with refresh only — the unit will reference the
  existing binary, which may break after future "major fix" runs.
EOF
            ;;
    esac
}

do_reinstall() {
    if [[ "${WIPE_IDENTITY}" == "yes" ]]; then
        if [[ -d "${STORAGE_DIR}" ]]; then
            echo "Wiping ${STORAGE_DIR} (--wipe-identity)..."
            run_as_user rm -rf "${STORAGE_DIR}"
        fi
    else
        echo "Preserving ${STORAGE_DIR}"
    fi

    ensure_pipx
    pipx_uninstall_meshchatx
    pipx_install_meshchatx
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

echo
echo "=== Refreshing storage dir, wrapper, unit ==="
ensure_storage_dir
write_wrapper
write_unit

echo
echo "=== rpc_key precondition (Issue #41) ==="
if rpc_key_pinned; then
    echo "  rpc_key: pinned"
else
    cat >&2 <<EOF

ERROR: rpc_key is not pinned in /etc/reticulum/config (or
~/.reticulum/config). MeshChatX will fail with AuthenticationError
on shared-instance RPC calls until this is fixed.

  Fix first:
    sudo python3 ${REPO_ROOT}/scripts/rns_alignment.py normalize
  Then re-run:
    $0 --refresh

EOF
    exit 3
fi

echo
echo "=== Activating meshchatx.service (user scope) ==="
activate_unit

echo
echo "=== Verifying ==="
sleep 5
if run_user_systemctl is-active meshchatx >/dev/null 2>&1; then
    echo "  meshchatx.service: ACTIVE"
    echo
    echo "Open the web UI:  http://127.0.0.1:8000/"
    echo "  (on a headless box: ssh -L 8000:localhost:8000 ${REAL_USER}@\$(hostname))"
    exit 0
else
    echo "WARN: meshchatx.service did not become active within 5s." >&2
    echo "  Inspect: journalctl --user -u meshchatx -n 50 --no-pager" >&2
    exit 4
fi
