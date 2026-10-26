#!/usr/bin/env bash
# ensure_desktop.sh — converge the desktop launcher + opt-in autostart.
#
# WHY (2026-10-25): the launcher (org.meshforge.app.desktop, meshforge-terminal)
# was installed only by update.sh. The fleet deploy is fleet_sync + fleet_pull,
# which never runs update.sh, so a box provisioned that way never got it — the
# lab had no .desktop and no meshforge-terminal while its desktop autologged in.
# Called by fleet_sync.sh (both legs) so the next console box cannot be missed.
#
# Two concerns, one status line each:
#   launcher  — installed only on a CONSOLE box. The declaration is the default
#               target: headless_unless_console (docs/fleet_roles.yaml) says a
#               box boots graphical.target only when it has a monitor+keyboard,
#               so that IS the operator's console declaration — no second flag.
#   autostart — opt-in per box in its own deployment.json:
#                   "desktop_autostart": {"fleet_url": "http://<manager>:5000/fleet"}
#               writes two login entries (TUI terminal + the fleet page). Only
#               files carrying X-MeshForge-Managed=true are ever replaced or
#               removed; a hand-made file of the same name is reported, kept.
#
# An unreadable fact is an ERROR, never a default: a failed get-default is not
# "headless" and an unparseable deployment.json is not "undeclared" — either
# would act (skip an install, delete entries) on a value nobody observed.
#
# Run AS the operator (fleet_sync legs do); the launcher install escalates with
# sudo -n and says how to run it by hand when refused. Dry-run by default.
# Exit 0 unless a line reads error.
#
# Usage: scripts/ensure_desktop.sh [--apply]
set -u

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
SYSTEMCTL="${SYSTEMCTL_BIN:-systemctl}"
SUDO="${SUDO_BIN:-sudo}"
PYTHON="${PYTHON_BIN:-python3}"
APPS_DIR="${DESKTOP_APPS_DIR:-/usr/share/applications}"
BIN_DIR="${DESKTOP_BIN_DIR:-/usr/local/bin}"
# The checkout install-desktop.sh is allowed to install from. It symlinks
# /opt/meshforge to its own PROJECT_DIR and moves a real directory there
# aside first, so a converge leg run from any other checkout is refused
# (review of ddf617b8, F9).
INSTALL_DIR="${DESKTOP_INSTALL_DIR:-/opt/meshforge}"
if [ -n "${INSTALL_DESKTOP_BIN:-}" ]; then
    INSTALLER=("$INSTALL_DESKTOP_BIN")
else
    INSTALLER=(bash "$REPO/scripts/install-desktop.sh")
fi
AUTO_DIR="$HOME/.config/autostart"
DEPLOYMENT="$HOME/.config/meshforge/deployment.json"
STATE="$HOME/.local/state/meshforge"
MARK="X-MeshForge-Managed=true"
rc=0

# ── launcher ─────────────────────────────────────────────────────────────
console=unknown
target="$("$SYSTEMCTL" get-default 2>/dev/null)"
if [ -z "$target" ]; then
    echo "launcher: error — systemctl get-default unanswered; console state UNKNOWN, nothing installed"
    rc=1
elif [ "$target" != "graphical.target" ]; then
    console=no
    echo "launcher: headless — default target $target (headless_unless_console), no launcher"
else
    console=yes
    # Everything a menu click resolves through: the system entry, the per-user
    # copy (it SHADOWS the system one), and both commands as LINKS into this
    # checkout — a copy is the frozen-launcher class (review F3).
    launcher_present() {
        local d="org.meshforge.app.desktop" pair
        cmp -s "$REPO/$d" "$APPS_DIR/$d" || return 1
        cmp -s "$REPO/$d" "$HOME/.local/share/applications/$d" || return 1
        for pair in "meshforge-terminal:meshforge-terminal.sh" "meshforge:meshforge-launcher.sh"; do
            [ -L "$BIN_DIR/${pair%%:*}" ] || return 1
            [ "$(readlink -f "$BIN_DIR/${pair%%:*}")" = "$(readlink -f "$REPO/scripts/${pair#*:}")" ] || return 1
        done
    }
    if launcher_present; then
        echo "launcher: present"
    elif [ "$(readlink -f "$REPO")" != "$(readlink -f "$INSTALL_DIR" 2>/dev/null)" ]; then
        echo "launcher: error — this checkout ($REPO) is not $INSTALL_DIR; install-desktop.sh would re-point it, refused"
        rc=1
    elif [ "$APPLY" = 0 ]; then
        echo "launcher: would_install — console box, launcher missing or stale"
    else
        mkdir -p "$STATE"
        if ! timeout 120 "$SUDO" -n "${INSTALLER[@]}" >"$STATE/install-desktop.log" 2>&1; then
            echo "launcher: error — install failed or sudo refused (log $STATE/install-desktop.log); by hand: sudo bash $REPO/scripts/install-desktop.sh"
            rc=1
        elif ! launcher_present; then
            # Exit 0 is the installer's claim; the files are the evidence (F6).
            echo "launcher: error — installer exited 0 but the launcher is still missing or stale (log $STATE/install-desktop.log)"
            rc=1
        else
            echo "launcher: installed"
        fi
    fi
fi

# ── autostart ────────────────────────────────────────────────────────────
if [ "$console" != yes ]; then
    if [ "$console" = no ]; then
        echo "autostart: skipped — headless box, no desktop session to start in"
    else
        echo "autostart: skipped — console state UNKNOWN"
    fi
    exit $rc
fi

# The declaration, read without guessing: undeclared | url <value> | error <why>.
# The URL is validated HERE, on the whole string: a line-wise grep let an
# embedded newline smuggle a second line into the .desktop file (review F1).
mkdir -p "$STATE" 2>/dev/null
decl="$("$PYTHON" -I -c '
import json, re, sys
p = sys.argv[1]
try:
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
except FileNotFoundError:
    print("undeclared"); sys.exit(0)
except Exception as e:
    print("error deployment.json unreadable: %s" % type(e).__name__); sys.exit(0)
if not isinstance(d, dict):
    print("error deployment.json is not an object"); sys.exit(0)
a = d.get("desktop_autostart")
if a is None:
    print("undeclared"); sys.exit(0)
if not isinstance(a, dict) or not isinstance(a.get("fleet_url"), str):
    print("error desktop_autostart must be {\"fleet_url\": \"http://...\"}"); sys.exit(0)
u = a["fleet_url"]
if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?", u):
    print("error fleet_url not a plain http(s) URL: %s" % ascii(u)[:80]); sys.exit(0)
print("url " + u)
' "$DEPLOYMENT" 2>"$STATE/ensure_desktop.err")"
prc=$?
# Only the three shapes, on ONE line, count as an answer; a missing or
# crashing interpreter is its own error, never a bad URL (review F5).
if [ "$prc" != 0 ] || [ "$(printf '%s\n' "$decl" | wc -l)" != 1 ] \
   || ! printf '%s' "$decl" | grep -qE '^(undeclared|url |error )'; then
    decl="error could not read deployment.json ($PYTHON rc=$prc: $(head -c 100 "$STATE/ensure_desktop.err" 2>/dev/null | tr '\n' ' '))"
fi

# Our two entries, rendered; a file is OURS only when it carries the marker.
tui_entry() {
    printf '[Desktop Entry]\nType=Application\nName=MeshForge TUI\n'
    printf 'Comment=Started at login by ensure_desktop.sh (deployment.json desktop_autostart)\n'
    printf 'Exec=%s/scripts/meshforge-terminal.sh\nIcon=org.meshforge.app\nTerminal=false\n%s\n' "$REPO" "$MARK"
}
fleet_entry() {
    printf '[Desktop Entry]\nType=Application\nName=MeshForge Fleet\n'
    printf 'Comment=Started at login by ensure_desktop.sh (deployment.json desktop_autostart)\n'
    printf 'Exec=xdg-open %s\nIcon=org.meshforge.app\nTerminal=false\n%s\n' "$1" "$MARK"
}
ENTRIES=(org.meshforge.app.desktop org.meshforge.fleet.desktop)
ours() { [ -f "$1" ] && grep -qxF "$MARK" "$1"; }

case "$decl" in
    error*)
        echo "autostart: error — ${decl#error } (existing entries left alone)"
        exit 1 ;;
    undeclared)
        removed=0
        for e in "${ENTRIES[@]}"; do
            if ours "$AUTO_DIR/$e"; then
                if [ "$APPLY" = 1 ]; then rm -f "$AUTO_DIR/$e"; fi
                removed=$((removed + 1))
            fi
        done
        if [ "$removed" = 0 ]; then
            echo "autostart: undeclared"
        elif [ "$APPLY" = 1 ]; then
            echo "autostart: removed — $removed managed entr(ies), desktop_autostart no longer declared"
        else
            echo "autostart: would_remove — $removed managed entr(ies)"
        fi
        exit $rc ;;
esac

url="${decl#url }"
# http(s), a host, an optional port, a plain path: nothing a .desktop Exec
# line or a shell could read as more than one argument.
if ! printf '%s' "$url" | grep -qE '^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$'; then
    echo "autostart: error — fleet_url not a plain http(s) URL: $(printf '%s' "$url" | head -c 80) (nothing written)"
    exit 1
fi

kept=""; changed=0
for e in "${ENTRIES[@]}"; do
    f="$AUTO_DIR/$e"
    if [ "$e" = org.meshforge.app.desktop ]; then want="$(tui_entry)"; else want="$(fleet_entry "$url")"; fi
    if [ -e "$f" ] && ! ours "$f"; then
        kept="$kept $e"; continue
    fi
    if [ -f "$f" ] && [ "$(cat "$f")" = "$want" ]; then continue; fi
    changed=$((changed + 1))
    if [ "$APPLY" = 1 ]; then
        mkdir -p "$AUTO_DIR" && printf '%s\n' "$want" > "$f.tmp" && mv -f "$f.tmp" "$f" \
            || { echo "autostart: error — cannot write $f"; exit 1; }
    fi
done

note=""
[ -n "$kept" ] && note=" (hand-made file kept, not ours:$kept)"
if [ "$changed" = 0 ]; then
    echo "autostart: present — $url$note"
elif [ "$APPLY" = 1 ]; then
    echo "autostart: written — $url$note"
else
    echo "autostart: would_write — $url$note"
fi
exit $rc
