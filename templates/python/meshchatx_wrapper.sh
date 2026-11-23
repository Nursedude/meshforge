#!/usr/bin/env bash
# MeshForge MeshChatX wrapper — refuses-loud on rpc_key mismatch.
#
# Version: 4
#
# Pairs with the canonical NomadNet wrapper (Python). MeshChatX is a
# web daemon, not a TUI that imports RNS in-process, so the analogous
# refuses-loud guard happens at startup as a precondition check rather
# than a runtime monkeypatch:
#
#   - If the config rnsd loads (rnsd's own order: /etc/reticulum,
#     ~/.config/reticulum, ~/.reticulum) carries no 64-hex rpc_key rnsd
#     USES — asked of ${MESHFORGE_REPO:-/opt/meshforge}/scripts/
#     rns_rpc_key_pin.py --check (rnsd's own parser; v2, r13.2 — v1's grep
#     parked MeshChatX over a quoted key rnsd reads; v3, r13.5 — the
#     checker resolves the config, and a key it cannot JUDGE is said
#     UNKNOWN, not MISMATCH; v4, r13.6 — the home is passed EXPLICITLY,
#     --home "$HOME", the home rnsd itself expands, not inferred from
#     SUDO_USER/LOGNAME) — exit 87. The systemd unit's StartLimitBurst then
#     parks the service in failed state after 5 retries so the journal
#     carries one clear diagnostic line instead of 392 silent
#     restarts. (Issue #41 / Issue #46.)
#
# Storage dir, host, port, and HTTPS settings are pinned here so the
# unit's ExecStart is independent of MeshChatX's CLI defaults — those
# may shift between releases. To change a flag, edit this wrapper and
# re-run:  sudo bash /opt/meshforge/scripts/install_meshchatx.sh --refresh
#
# ``scripts/install_meshchatx.sh`` (step 5) is the ONLY writer that
# copies this file into ``~/.config/meshforge/meshchatx_wrapper.sh``
# (the TUI handler was retired 2026-09-06). Bumping the ``Version:``
# line above makes ``--refresh`` rewrite it.

set -eu

# --------------------------------------------------------------------
# Refuse-loud sentinel (matches NomadNet wrapper for fleet consistency)
# --------------------------------------------------------------------
EXIT_AUTH_MISMATCH=87

err() {
    printf '\n[meshforge meshchatx_wrapper] %s\n' "$*" >&2
}

# --------------------------------------------------------------------
# 1. rpc_key precondition (Issue #41)
# --------------------------------------------------------------------
# rc vocabulary: scripts/rns_rpc_key_pin.py UNKNOWN_RCS / NOT_PINNED_RCS.
checker="${MESHFORGE_REPO:-/opt/meshforge}/scripts/rns_rpc_key_pin.py"
if [[ ! -f "${checker}" ]]; then
    rpc_rc=1
    err "cannot verify rpc_key: ${checker} not found (set MESHFORGE_REPO)"
else
    rpc_rc=0
    python3 "${checker}" --check --home "${HOME}" >&2 || rpc_rc=$?
fi

case "${rpc_rc}" in
    0) ;;
    3|4)
        err "RNS rpc_key MISMATCH detected (or rpc_key not pinned)."
        err "  rnsd and MeshChatX would use different identities → AuthenticationError."
        err "  FIX:"
        err "    sudo python3 /opt/meshforge/scripts/rns_alignment.py normalize"
        err "    then: sudo bash /opt/meshforge/scripts/install_meshchatx.sh --refresh"
        exit "${EXIT_AUTH_MISMATCH}" ;;
    *)
        err "RNS rpc_key UNKNOWN — could not verify it (rc=${rpc_rc}; reason above)."
        err "  Not starting: an unverified key is how AuthenticationError churns."
        err "  rc=5: install RNS for this user's python3 so it reads rnsd's config"
        err "  rc=1/2: fix what the reason above names, then restart meshchatx"
        exit "${EXIT_AUTH_MISMATCH}" ;;
esac

# --------------------------------------------------------------------
# 2. Resolve the meshchatx binary (pipx-installed under ~/.local/bin)
# --------------------------------------------------------------------
MCX_BIN="${HOME}/.local/bin/meshchatx"
if [[ ! -x "${MCX_BIN}" ]]; then
    if command -v meshchatx >/dev/null 2>&1; then
        MCX_BIN="$(command -v meshchatx)"
    else
        err "meshchatx binary not found in ~/.local/bin or on PATH."
        err "  Run: sudo bash /opt/meshforge/scripts/install_meshchatx.sh --reinstall"
        exit 2
    fi
fi

# --------------------------------------------------------------------
# 3. Storage dir (canonical, survives cwd changes)
# --------------------------------------------------------------------
STORAGE_DIR="${HOME}/.local/share/meshchatx"
mkdir -p "${STORAGE_DIR}"

# --------------------------------------------------------------------
# 4. Exec MeshChatX with canonical flags
# --------------------------------------------------------------------
# --headless        : no Electron/browser auto-launch
# --host 127.0.0.1  : localhost-only by default; SSH-tunnel for remote
# --port 8000       : free in MeshForge (see project_map_state for the
#                     fleet's port map)
# --no-https        : self-signed cert friction is a UX cost we pay
#                     only when exposing beyond localhost
# --storage-dir     : pin to ~/.local/share/meshchatx so the service
#                     doesn't depend on cwd
exec "${MCX_BIN}" \
    --headless \
    --host 127.0.0.1 \
    --port 8000 \
    --no-https \
    --storage-dir "${STORAGE_DIR}"
