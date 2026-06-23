# scripts/lib/install_common.sh — hardened install primitives for shell scripts.
#
# Source this file; it defines functions, no side effects on source (the only
# side effect, the whole-run transcript, happens only when you CALL
# mf_log_init). Bash 4+.
#
# Born from a recurring failure class (feedback_version_env_rigor,
# feedback_install_method_fragility): a fresh user's app+env "did not install
# properly" and they hand-installed pip, because the shell installers assumed
# pip existed, swallowed apt/pip output into /dev/null (or `| tail`, which even
# discards the exit code), and printed an unconditional `✓` regardless. This
# library is the shell twin of src/utils/pip_install.py — ONE place that:
#   * bootstraps pip if missing (mf_ensure_pip — the fresh-user fix),
#   * invokes pip/apt with the return code CHECKED, never masked,
#   * decides PEP 668 in ONE place (mf_pep668_active / mf_pip_args),
#   * verifies a package actually imports (mf_verify_import),
#   * confirms a service really came up (mf_systemctl_confirm),
#   * and leaves a full transcript (mf_log_init).
#
# Functions use an `mf_` prefix so they never collide with a sourcing script's
# own `step`/`warn`/`die`. All return their command's real exit code; CALLERS
# decide UI (echo ✓ only after a checked success). Consumers:
#   install.sh, dev_setup.sh, scripts/install_noc.sh,
#   scripts/configure_gateway.sh, scripts/fix_packaging_conflict.sh

# --------------------------------------------------------------------------
# Transcript — every install run leaves something the operator can paste.
# --------------------------------------------------------------------------
# root  → /var/log/meshforge/install-<UTC-ts>.log (+ install-latest.log)
# user  → ~/.cache/meshforge/logs/install-<UTC-ts>.log
# else  → /tmp/install-<UTC-ts>.log + WARN (never silently drop the log)
mf_log_init() {
    [[ -n "${MF_LOG_INITED:-}" ]] && return 0
    local ts dir
    ts="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || echo now)"
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        dir="/var/log/meshforge"
    else
        dir="${HOME:-/tmp}/.cache/meshforge/logs"
    fi
    if ! mkdir -p "$dir" 2>/dev/null; then
        echo "WARN: could not create log dir $dir; falling back to /tmp" >&2
        dir="/tmp"
    fi
    MF_INSTALL_LOG="$dir/install-$ts.log"
    if : > "$MF_INSTALL_LOG" 2>/dev/null; then
        # Whole-run transcript via process substitution — NOT a trailing
        # `| tee`, so the script keeps its own exit status and `set -e` still
        # trips on real failures.
        exec > >(tee -a "$MF_INSTALL_LOG") 2>&1
        ln -sf "$MF_INSTALL_LOG" "$dir/install-latest.log" 2>/dev/null || true
        export MF_INSTALL_LOG
        echo "MeshForge install transcript: $MF_INSTALL_LOG"
    else
        echo "WARN: log file not writable ($MF_INSTALL_LOG); continuing without transcript" >&2
        MF_INSTALL_LOG=""
    fi
    MF_LOG_INITED=1
}

# Run a command, echoing a timestamped marker around it. Output already tees to
# the transcript via mf_log_init's redirect; this adds a greppable RUN/EXIT
# frame. Returns the command's real exit code.
mf_run_logged() {
    printf '[%s] RUN : %s\n' "$(date -u +%H:%M:%S 2>/dev/null)" "$*"
    "$@"
    local rc=$?
    printf '[%s] EXIT %d : %s\n' "$(date -u +%H:%M:%S 2>/dev/null)" "$rc" "$1"
    return $rc
}

# --------------------------------------------------------------------------
# PEP 668 — ONE detector (replaces the three divergent copies in install.sh,
# dev_setup.sh, install_noc.sh). Keyed to the resolved interpreter; a venv is
# NEVER externally managed. Returns 0 when --break-system-packages is needed.
# --------------------------------------------------------------------------
mf_pep668_active() {
    local py="${1:-python3}" out
    out="$("$py" -c 'import sys,sysconfig,os
v = sys.prefix != sys.base_prefix
p = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
print("0" if v else ("1" if os.path.exists(p) else "0"))' 2>/dev/null)" || out=""
    if [[ "$out" == "1" ]]; then
        return 0
    fi
    # Fallback when the interpreter could not be probed: a system marker present.
    if [[ -z "$out" ]] && ls /usr/lib/python3*/EXTERNALLY-MANAGED >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# Echo the pip flags for a target interpreter (single source for PIP_ARGS).
mf_pip_args() {
    if mf_pep668_active "${1:-python3}"; then
        echo "--break-system-packages --timeout 60"
    else
        echo "--timeout 60"
    fi
}

# --------------------------------------------------------------------------
# mf_ensure_pip [python] — guarantee `<python> -m pip` works, or fail LOUD.
# The fresh-user fix on the bootstrap path. Ladder: pip --version → ensurepip
# → apt python3-pip (root only) → actionable error. Returns 0 on success.
# --------------------------------------------------------------------------
mf_ensure_pip() {
    local py="${1:-python3}"
    if "$py" -m pip --version >/dev/null 2>&1; then
        return 0
    fi
    echo "  pip not available for $py — bootstrapping it..."
    "$py" -m ensurepip --upgrade >/dev/null 2>&1 || true
    if "$py" -m pip --version >/dev/null 2>&1; then
        echo "  pip bootstrapped via ensurepip"
        return 0
    fi
    if [[ "${EUID:-$(id -u)}" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
        echo "  installing python3-pip via apt..."
        mf_run_logged apt-get install -y python3-pip || true
        if "$py" -m pip --version >/dev/null 2>&1; then
            echo "  pip installed via apt"
            return 0
        fi
    fi
    local sp=""
    [[ "${EUID:-$(id -u)}" -ne 0 ]] && sp="sudo "
    echo "ERR: pip is not available for $py and could not be bootstrapped." >&2
    echo "     Install it with: ${sp}apt install -y python3-pip" >&2
    echo "     (You should not have to do this by hand — please report this.)" >&2
    return 1
}

# --------------------------------------------------------------------------
# mf_pip_install <python> <pip-args...> — the ONE checked pip invoker.
# Ensures pip first, then runs `<python> -m pip install <args>`. NEVER pipes the
# output (which would mask pip's exit code — the configure_gateway `| tail`
# bug). Returns pip's real exit code; the CALLER checks it.
# --------------------------------------------------------------------------
mf_pip_install() {
    local py="$1"; shift
    mf_ensure_pip "$py" || return 1
    mf_run_logged "$py" -m pip install "$@"
}

# mf_apt_install <pkgs...> — apt install with output visible+logged (no
# &>/dev/null swallow) and the exit code returned. -q (level 1) trims progress
# noise but keeps errors.
mf_apt_install() {
    mf_run_logged apt-get install -y -q "$@"
}

# --------------------------------------------------------------------------
# mf_verify_import <python> <module> [sudo_user] — import-as-consumer check.
# "Installed" is not "importable" (Issue #24). Optionally as another principal
# (the gateway user, root for rnsd). Returns 0 when the import succeeds.
# --------------------------------------------------------------------------
mf_verify_import() {
    local py="$1" mod="$2" user="${3:-}"
    if [[ -n "$user" ]]; then
        sudo -u "$user" -H "$py" -c "import $mod" >/dev/null 2>&1
    else
        "$py" -c "import $mod" >/dev/null 2>&1
    fi
}

# --------------------------------------------------------------------------
# mf_git_sync <repo_url> <install_dir> — clone or update a checkout, honoring an
# optional MESHFORGE_REF pin (tag/branch/sha). An unresolvable pin HARD-fails
# (returns nonzero): a pinned install silently landing on main is worse than
# stopping. Echoes the resolved HEAD for provenance (the soak fleet needs to
# know which SHA actually shipped). A pull failure on an existing tree is loud
# but non-fatal — we record the SHA we kept rather than running pip against a
# half-updated tree under the illusion of success.
# --------------------------------------------------------------------------
mf_git_sync() {
    local url="$1" dir="$2" ref="${MESHFORGE_REF:-}"
    if [[ -d "$dir/.git" ]]; then
        git config --global --add safe.directory "$dir" 2>/dev/null || true
        git -C "$dir" pull -q || echo "  WARN: git pull failed; continuing with the existing checkout" >&2
    else
        if [[ -n "$ref" ]]; then
            git clone -q --branch "$ref" "$url" "$dir" 2>/dev/null \
                || git clone -q "$url" "$dir" || { echo "ERR: git clone failed" >&2; return 1; }
        else
            git clone -q "$url" "$dir" || { echo "ERR: git clone failed" >&2; return 1; }
        fi
        git config --global --add safe.directory "$dir" 2>/dev/null || true
    fi
    if [[ -n "$ref" ]]; then
        git -C "$dir" fetch -q --tags origin 2>/dev/null || true
        git -C "$dir" checkout -q "$ref" \
            || { echo "ERR: MESHFORGE_REF '$ref' could not be resolved" >&2; return 1; }
    fi
    local sha
    sha="$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "  source HEAD: $sha"
    return 0
}

# --------------------------------------------------------------------------
# mf_systemctl_confirm <unit> — confirm a unit actually came up after
# enable/start, instead of echoing "started" unconditionally (the rnsd gap).
# Returns 0 when active.
# --------------------------------------------------------------------------
mf_systemctl_confirm() {
    local unit="$1"
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        echo "  ✓ $unit is active"
        return 0
    fi
    echo "  ✗ $unit is NOT active — inspect: journalctl -u $unit -n 50 --no-pager" >&2
    return 1
}
