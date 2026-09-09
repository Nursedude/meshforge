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
    # DRY-RUN: bootstrapping pip is a real mutation and the venv it targets was
    # only PREVIEWED, so the interpreter may not exist at all. Report success so
    # the caller keeps walking the script — the point of a preview is to reach
    # the END, and aborting here would hide every later step from the reader.
    if [[ "$MF_DRY_RUN" == "true" ]]; then
        mf_dry_note "bootstrap pip for $py"; return 0
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
    # DRY-RUN gate BEFORE mf_ensure_pip: pip is reached here through an
    # interpreter PATH ($VENV_DIR/bin/python), never the bare `pip` command, so
    # mf_dry_run_enable's shadow cannot see it. Found by the 2026-09-09 dry-run
    # aborting on "Python dependency install failed" — the venv had only been
    # previewed, so there was no pip to call. A primitive is the right layer:
    # one gate covers all six call sites.
    if [[ "$MF_DRY_RUN" == "true" ]]; then
        mf_dry_note "$py -m pip install $*"; return 0
    fi
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
    # DRY-RUN: nothing was installed, so an honest import check MUST fail — and
    # a failing verify would abort the preview. Report the check as skipped
    # rather than faking a pass: the reader is told it was not performed.
    if [[ "$MF_DRY_RUN" == "true" ]]; then
        mf_dry_note "verify import '$mod' with $py (SKIPPED — nothing installed)"
        return 0
    fi
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

# ==========================================================================
# DRY-RUN (2026-09-09, first-hour audit)
# ==========================================================================
# WHY: install_noc.sh is 2,088 lines the README tells a newcomer to run with
# sudo, and it had no way to preview what it would do. Their only options were
# run-it-blind-as-root or read 2,088 lines of bash.
#
# THE DESIGN CONSTRAINT, stated first because it drove everything: a
# half-working dry-run that mutates anyway is FAR more dangerous than no
# dry-run. It would be an instrument claiming a safety it does not have —
# the exact defect class the 2026-09-09 session spent the day on. So this is
# built to FAIL CLOSED on three independent layers, not to be exhaustive:
#
#  1. SHADOWING. The mutating commands become shell functions that print
#     instead of executing. This catches every call site regardless of where
#     it sits in the script — the 41 raw `systemctl` and 15 raw `apt` calls
#     are not routed through any primitive, so per-call-site gating would have
#     been the fragile approach.
#     ⚠️ Shadows pass READ invocations straight through (`systemctl is-active`,
#     `sed` without -i, `git rev-parse`, `dpkg -l`): a shadow that swallowed
#     reads would make the script take different BRANCHES under dry-run, and
#     then the preview would be of a program nobody runs.
#
#  2. UNPRIVILEGED. Dry-run deliberately does NOT require root. This is the
#     load-bearing layer: anything shadowing MISSES cannot mutate the system,
#     because the kernel refuses it. Redirections (`cat > /etc/...`) and
#     heredocs are not commands and CANNOT be shadowed in bash — the
#     permission system is what covers them. That is a witness this code did
#     not write, which is exactly why it is trusted over the shadow list
#     (calibrated_claims: a witness you did not author outranks one you did).
#
#  3. LOUD ABORT. install_noc.sh runs `set -e`. An ungated mutation therefore
#     ABORTS the dry-run instead of silently proceeding. An abort is a
#     FINDING — it names a path this preview does not yet cover — never a
#     reason to relax layers 1 or 2.
#
# What this does NOT claim: completeness. It claims that a miss fails loudly
# rather than mutating. Do not "improve" it by running dry-run as root.
# --------------------------------------------------------------------------
MF_DRY_RUN="${MF_DRY_RUN:-false}"
MF_DRY_RUN_COUNT=0

mf_dry_note() {
    MF_DRY_RUN_COUNT=$((MF_DRY_RUN_COUNT + 1))
    printf '  [dry-run] would: %s\n' "$*"
}

# Echo the first SUBCOMMAND in an argv, skipping leading option flags (and the
# value of flags that take one). Without this, `git -C <dir> rev-parse` looks
# like subcommand "-C" and `systemctl --user is-active` like "--user" — both
# READS that would then be shadowed as mutations. Not cosmetic: a shadowed read
# returns 0 without producing output, so the script takes a DIFFERENT BRANCH
# under dry-run and the preview is of a program nobody runs — exactly what the
# shadow contract promises not to do. Found 2026-09-09 when
# `git -C ... rev-parse HEAD` showed up in the preview as a would-be mutation.
_mf_subcmd() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -C|--directory|--git-dir|--work-tree|-H|--host)
                shift 2 || return 0 ;;
            --) shift ;;
            -*) shift ;;
            *) printf '%s' "$1"; return 0 ;;
        esac
    done
}

# Is this argv a READ for the named tool? Reads pass through untouched so the
# script's branching is identical to a real run.
_mf_is_read() {
    local tool="$1"; shift
    case "$tool" in
        systemctl)
            case "$(_mf_subcmd "$@")" in
                is-active|is-enabled|is-failed|show|status|cat|list-units|list-timers|list-unit-files|get-default) return 0 ;;
            esac ;;
        apt-get|apt)
            case "$(_mf_subcmd "$@")" in list|show|policy|search) return 0 ;; esac ;;
        dpkg)
            case "$*" in *--print-architecture*|-l*|*--list*|-s*|*--status*) return 0 ;; esac ;;
        sed)   [[ "$*" != *-i* ]] && return 0 ;;
        git)
            case "$(_mf_subcmd "$@")" in rev-parse|status|log|show|diff|config|describe|ls-remote|remote) return 0 ;; esac ;;
        pip|pip3)
            case "$(_mf_subcmd "$@")" in show|list|freeze) return 0 ;; esac
            case "$*" in *--version*) return 0 ;; esac ;;
        curl|wget)
            # Only a write-to-file invocation mutates.
            case "$*" in *-o\ *|*-O*|*--output*) return 1 ;; esac
            return 0 ;;
    esac
    return 1
}

mf_dry_run_enable() {
    MF_DRY_RUN=true
    local tool
    # ALWAYS-mutating tools: no read form worth passing through.
    for tool in mkdir cp mv rm ln chmod chown install tee useradd usermod \
                groupadd update-rc-d a2enmod; do
        eval "${tool}() { mf_dry_note \"${tool} \$*\"; return 0; }"
    done
    # MIXED tools: reads pass through, writes are previewed.
    for tool in systemctl apt-get apt dpkg sed git pip pip3 curl wget; do
        eval "${tool}() {
            if _mf_is_read ${tool} \"\$@\"; then command ${tool} \"\$@\"; return \$?; fi
            mf_dry_note \"${tool} \$*\"; return 0
        }"
    done
    printf '\n'
    printf '  ══ DRY RUN — nothing will be changed ══\n'
    printf '  Running UNPRIVILEGED on purpose: anything this preview does not\n'
    printf '  cover cannot mutate the system, because the kernel refuses it.\n'
    printf '  If this run ABORTS, that is a finding (an uncovered path), not a\n'
    printf '  failure of your machine. Re-run without --dry-run, as root, to\n'
    printf '  actually install.\n\n'
}

mf_dry_run_summary() {
    [[ "$MF_DRY_RUN" == "true" ]] || return 0
    printf '\n  ══ DRY RUN COMPLETE — %d operation(s) previewed, 0 performed ══\n' \
        "$MF_DRY_RUN_COUNT"
    printf '  This preview covers shadowed commands. Shell REDIRECTIONS and\n'
    printf '  heredocs (cat > file) cannot be shadowed in bash; running\n'
    printf '  unprivileged is what stops those, so a system path they touch\n'
    printf '  would have aborted this run rather than appearing above.\n'
    printf '  To install for real:  sudo bash scripts/install_noc.sh\n\n'
}

# Redirection-safe writers. Shell redirections (`echo >> f`, `cat > f`) are NOT
# commands, so mf_dry_run_enable cannot shadow them — the 2026-09-09 dry-run
# aborted on exactly that at install_noc.sh:677. Route a redirection through one
# of these and it becomes previewable; leave it raw and the unprivileged layer
# stops it loudly instead. Both are honest outcomes; this is the nicer one.
mf_append_line() {   # mf_append_line <file> <line>
    if [[ "$MF_DRY_RUN" == "true" ]]; then
        mf_dry_note "append to $1: $2"; return 0
    fi
    printf '%s\n' "$2" >> "$1"
}

mf_write_stdin() {   # <file>  — body on stdin
    if [[ "$MF_DRY_RUN" == "true" ]]; then
        # MUST consume stdin. These call sites are heredocs; returning without
        # reading would leave the body queued on the script's own stdin and the
        # shell would then try to EXECUTE it. Found while converting the 17
        # hardcoded system-path writes (2026-09-09).
        local n; n="$(command wc -c 2>/dev/null)" || n="?"
        mf_dry_note "write ${n// /} bytes to $1"; return 0
    fi
    command cat > "$1"
}

# Can we actually READ /dev/tty, not merely see the device node?
# `[[ -c /dev/tty ]]` is a PRESENCE check: the node exists in a pipeline, a cron
# job, or under `</dev/null`, and the guarded `read < /dev/tty` then fails —
# aborting the whole installer under `set -e`. Found 2026-09-09 when the
# dry-run died at install_noc.sh:571 on exactly that. This is the FUNCTION
# check: open it, discard, report.
mf_have_tty() {
    { : < /dev/tty; } 2>/dev/null
}

# ==========================================================================
# PHASE TRACKING + THE FAILURE REPORT (2026-09-09)
# ==========================================================================
# install_noc.sh runs `set -e` across 8 phases of real system mutation. Before
# this, an unhandled failure in phase 5 simply KILLED the script: whatever error
# the failing command happened to print, then nothing. The user was left with a
# partially-configured box and no statement of what had been done, what had not,
# or what to do next — and the log path was only mentioned at the very top of a
# long transcript they had just watched scroll past.
#
# WHAT THIS DOES NOT DO, deliberately: it does not roll back. Undoing an apt
# install, a systemd unit, or a udev rule automatically is both hard and
# genuinely dangerous — a rollback that removes a package the user already
# depended on is worse than a half-install. Claiming transactional safety we do
# not have would be the exact defect class this session was about. So the
# contract is honest instead: name the phase, name the log, and state the
# recovery path.
MF_PHASE="startup"
MF_PHASE_NUM=0

# phase <n/total> <description>
mf_phase() {
    MF_PHASE="$2"
    MF_PHASE_NUM="$1"
    printf '\033[0;36m[%s] %s\033[0m\n' "$1" "$2"
}

mf_install_failed_report() {
    local rc="$1"
    printf '\n\033[0;31m══ INSTALL FAILED (exit %s) ══\033[0m\n' "$rc"
    printf '  Failed during: [%s] %s\n' "${MF_PHASE_NUM:-?}" "${MF_PHASE:-unknown}"
    printf '\n'
    printf '  Your box is PARTIALLY CONFIGURED. Nothing was rolled back — that is\n'
    printf '  deliberate: automatically undoing apt installs and systemd units can\n'
    printf '  remove something you already depended on.\n'
    printf '\n'
    printf '  WHAT TO DO\n'
    printf '    1. Re-run the same command. The steps are written to be repeatable:\n'
    printf '       existing installs are detected, config files are rewritten\n'
    printf '       rather than appended to, and apt/systemctl operations are\n'
    printf '       idempotent. A re-run picks up from where this stopped.\n'
    if [[ -n "${MF_INSTALL_LOG:-}" ]]; then
        printf '    2. If it fails the same way, the full transcript is here:\n'
        printf '         %s\n' "$MF_INSTALL_LOG"
        printf '       The last RUN/EXIT lines in it name the exact command.\n'
    else
        printf '    2. If it fails the same way, re-run and capture the output —\n'
        printf '       no transcript was written (the log dir was not writable).\n'
    fi
    printf '    3. To see what the installer WOULD do without changing anything:\n'
    printf '         bash %s --dry-run\n' "${0:-scripts/install_noc.sh}"
    printf '\n'
    printf '  HONEST LIMIT: re-run-to-recover is the designed path, not a\n'
    printf '  guarantee proven from every one of the 8 phases. If a re-run does\n'
    printf '  not clear it, please report the transcript rather than hand-patching.\n\n'
}
