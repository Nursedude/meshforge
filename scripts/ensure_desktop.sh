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
# A checkout that is not the install dir (a worktree, a second clone) never
# acts: --apply refuses (error), a dry run reports would_refuse. The installer
# would re-point the install dir at it, and an autostart entry would point
# into a directory that may be deleted (reviews of ddf617b8 F9, 5aec69f1).
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
BIN_DIR="${DESKTOP_BIN_DIR:-/usr/local/bin}"
# The checkout install-desktop.sh is allowed to install from. It symlinks
# /opt/meshforge to its own PROJECT_DIR (and now refuses to displace a real
# directory there), so a converge leg run from any other checkout is refused
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
USER_COPY="$HOME/.local/share/applications/org.meshforge.app.desktop"
MARK="X-MeshForge-Managed=true"
rc=0

# THE list of what install-desktop.sh copies — one list, both sides use it
# (DESKTOP_APPS_DIR / DESKTOP_SHARE_DIR / DESKTOP_ETC_DIR are read there).
if ! . "$REPO/scripts/lib/desktop.sh"; then
    echo "launcher: error — cannot load $REPO/scripts/lib/desktop.sh; nothing checked"
    echo "autostart: error — cannot load $REPO/scripts/lib/desktop.sh; nothing checked"
    exit 1
fi
foreign=""
if [ "$(readlink -f "$REPO")" != "$(readlink -f "$INSTALL_DIR" 2>/dev/null)" ]; then
    foreign="this checkout ($REPO) is not $INSTALL_DIR"
fi

# Everything a menu click resolves through: every file the installer copies
# (from its own list) and both commands as LINKS into this checkout — a copy
# is the frozen-launcher class (review F3). The per-user menu copy is NOT part
# of it: it is the file a menu editor saves the user's edits to, so a
# difference is reported (shadowed), never "fixed" by reinstalling over it.
launcher_present() {
    local src dest pair tab
    tab="$(printf '\t')"
    while IFS="$tab" read -r src dest; do
        cmp -s "$src" "$dest" || return 1
    done < <(desktop_copies "$REPO")
    for pair in "meshforge-terminal:meshforge-terminal.sh" "meshforge:meshforge-launcher.sh"; do
        [ -L "$BIN_DIR/${pair%%:*}" ] || return 1
        [ "$(readlink -f "$BIN_DIR/${pair%%:*}")" = "$(readlink -f "$REPO/scripts/${pair#*:}")" ] || return 1
    done
}

# ── launcher ─────────────────────────────────────────────────────────────
console=unknown
target="$("$SYSTEMCTL" get-default 2>/dev/null)"
if [ -z "$target" ]; then
    echo "launcher: error — systemctl get-default unanswered; console state UNKNOWN, nothing installed"
    rc=1
elif [ "$target" != "graphical.target" ]; then
    console=no
    # Not converged here — but update.sh's step 3 installs the same files on
    # every box (the `meshforge` command, polkit and the login message serve
    # headless boxes too), so say what IS there instead of "no launcher".
    if launcher_present; then have=present; else have=absent; fi
    echo "launcher: headless — default target $target (headless_unless_console): not required here, files $have"
else
    console=yes
    if launcher_present; then
        if [ -e "$USER_COPY" ] && ! cmp -s "$REPO/org.meshforge.app.desktop" "$USER_COPY"; then
            echo "launcher: shadowed — $USER_COPY differs from the repo entry and shadows it in the menu (kept: menu edits live there; rm it to use the system entry)"
        else
            echo "launcher: present"
        fi
    elif [ -n "$foreign" ]; then
        if [ "$APPLY" = 0 ]; then
            echo "launcher: would_refuse — $foreign; --apply would not run install-desktop.sh (it re-points $INSTALL_DIR)"
        else
            echo "launcher: error — $foreign; install-desktop.sh would re-point it, refused"
            rc=1
        fi
    elif [ "$APPLY" = 0 ]; then
        echo "launcher: would_install — console box, launcher missing or stale"
    elif ! mkdir -p "$STATE" 2>/dev/null || [ ! -w "$STATE" ]; then
        # The log is the evidence of what the installer did; without it a
        # failure would read as "sudo refused" (review of 5aec69f1).
        echo "launcher: error — state dir $STATE is not a writable directory; installer not run"
        rc=1
    elif ! timeout 120 "$SUDO" -n "${INSTALLER[@]}" >"$STATE/install-desktop.log" 2>&1; then
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
# The URL is validated HERE, on the whole string, and ONLY here: a line-wise
# grep let an embedded newline smuggle a second line into the .desktop file
# (review F1). It is http(s), a host, an optional port, a plain path — nothing
# a .desktop Exec line or a shell could read as more than one argument.
# stderr joins stdout: the read needs no state dir (an unwritable one used to
# surface as "could not read deployment.json"), and a stray line fails the
# one-line check below instead of passing as an answer.
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
' "$DEPLOYMENT" 2>&1)"
prc=$?
# Only the three shapes, on ONE line, count as an answer; a missing or
# crashing interpreter is its own error, never a bad URL (review F5).
if [ "$prc" != 0 ] || [ "$(printf '%s\n' "$decl" | wc -l)" != 1 ] \
   || ! printf '%s' "$decl" | grep -qE '^(undeclared|url |error )'; then
    decl="error could not read deployment.json ($PYTHON rc=$prc: $(printf '%s' "$decl" | head -c 100 | tr '\n' ' '))"
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
# A foreign checkout never writes or removes an entry: the TUI entry's Exec
# would point INTO it — a worktree that gets deleted (review of 5aec69f1).
refuse_autostart() {
    if [ "$APPLY" = 0 ]; then
        echo "autostart: would_refuse — $foreign; --apply would not $1"
        exit $rc
    fi
    echo "autostart: error — $foreign; refused to $1 (nothing changed)"
    exit 1
}

case "$decl" in
    error*)
        echo "autostart: error — ${decl#error } (existing entries left alone)"
        exit 1 ;;
    undeclared)
        removed=0
        for e in "${ENTRIES[@]}"; do
            if ours "$AUTO_DIR/$e"; then
                if [ "$APPLY" = 1 ] && [ -z "$foreign" ]; then rm -f "$AUTO_DIR/$e"; fi
                removed=$((removed + 1))
            fi
        done
        if [ "$removed" = 0 ]; then
            echo "autostart: undeclared"
        elif [ -n "$foreign" ]; then
            refuse_autostart "remove $removed managed entr(ies)"
        elif [ "$APPLY" = 1 ]; then
            echo "autostart: removed — $removed managed entr(ies), desktop_autostart no longer declared"
        else
            echo "autostart: would_remove — $removed managed entr(ies)"
        fi
        exit $rc ;;
esac

url="${decl#url }"

kept=""; changed=0
for e in "${ENTRIES[@]}"; do
    f="$AUTO_DIR/$e"
    if [ -e "$f" ] && ! ours "$f"; then
        kept="$kept $e"; continue
    fi
    if [ "$e" = org.meshforge.app.desktop ]; then want="$(tui_entry)"; else want="$(fleet_entry "$url")"; fi
    if [ -f "$f" ] && [ "$(cat "$f")" = "$want" ]; then continue; fi
    changed=$((changed + 1))
    if [ "$APPLY" = 1 ] && [ -z "$foreign" ]; then
        mkdir -p "$AUTO_DIR" && printf '%s\n' "$want" > "$f.tmp" && mv -f "$f.tmp" "$f" \
            || { echo "autostart: error — cannot write $f"; exit 1; }
    fi
done

note=""
[ -n "$kept" ] && note=" (hand-made file kept, not ours:$kept)"
if [ "$changed" = 0 ]; then
    echo "autostart: present — $url$note"
elif [ -n "$foreign" ]; then
    refuse_autostart "write $changed entr(ies) for $url"
elif [ "$APPLY" = 1 ]; then
    echo "autostart: written — $url$note"
else
    echo "autostart: would_write — $url$note"
fi
exit $rc
