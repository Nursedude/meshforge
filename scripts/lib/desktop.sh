# scripts/lib/desktop.sh — the desktop-integration facts more than one script
# needs. Source it; it defines functions only, no side effects.
#
# Consumers (keep this list current — it is the grep target when this changes):
#   - scripts/install-desktop.sh   copies desktop_copies; desktop_user_copy_is_ours
#   - scripts/ensure_desktop.sh    present check reads the SAME desktop_copies
#   - scripts/update.sh            desktop_out_healthy on the helper's output
#   - scripts/fleet_sync.sh        self leg, desktop_out_healthy (parity)
#   - tests/test_ensure_desktop.sh
#
# WHY (review of 5aec69f1, 2026-10-25): the installer copied 21 files and the
# present check compared 1 of them, so a lost polkit policy or login message
# never re-converged; and update.sh and fleet_sync judged the helper's output
# with two different tests. Two hand-kept lists drift (honest_failure_modes #5).
#
# Paths default to the real system dirs. DESKTOP_APPS_DIR / DESKTOP_SHARE_DIR /
# DESKTOP_ETC_DIR exist so a test can point both sides at a temp tree; the
# installer runs under sudo, which resets the environment.

# desktop_copies <project_dir> — one "src<TAB>dest" line per file the
# installer COPIES (the two commands are symlinks, not copies, and stay
# literal lines in install-desktop.sh where the symlink guard reads them).
desktop_copies() {
    local p="$1" share="${DESKTOP_SHARE_DIR:-/usr/share}" etc="${DESKTOP_ETC_DIR:-/etc}"
    local apps="${DESKTOP_APPS_DIR:-${DESKTOP_SHARE_DIR:-/usr/share}/applications}"
    local svg="$p/assets/meshforge-icon.svg" d n
    printf '%s\t%s\n' "$p/org.meshforge.app.desktop" "$apps/org.meshforge.app.desktop"
    if [ -f "$svg" ]; then
        for d in 16x16 24x24 32x32 48x48 64x64 128x128 256x256 scalable; do
            for n in meshforge org.meshforge.app; do
                printf '%s\t%s\n' "$svg" "$share/icons/hicolor/$d/apps/$n.svg"
            done
        done
        for n in meshforge org.meshforge.app; do
            printf '%s\t%s\n' "$svg" "$share/pixmaps/$n.svg"
        done
    fi
    # Installed only where the system has the directory (no polkit, no
    # profile.d = nothing to install AND nothing to expect).
    if [ -d "$share/polkit-1/actions" ]; then
        printf '%s\t%s\n' "$p/assets/org.meshforge.policy" "$share/polkit-1/actions/org.meshforge.policy"
    fi
    if [ -d "$etc/profile.d" ]; then
        printf '%s\t%s\n' "$p/scripts/meshforge-profile.sh" "$etc/profile.d/meshforge.sh"
    fi
    return 0
}

# desktop_user_copy_is_ours <installed_system_entry> <user_copy>
# The per-user menu copy SHADOWS the system entry, and it is the file a menu
# editor saves the user's edits to. It is ours to refresh only while absent or
# byte-identical to the system entry we installed LAST (i.e. our own copy of
# it); anything else is the user's edit — kept and reported, never overwritten.
# Call it BEFORE the system entry is replaced.
desktop_user_copy_is_ours() {
    [ ! -e "$2" ] || cmp -s "$1" "$2"
}

# desktop_out_healthy <ensure_desktop.sh output> — 0 only when BOTH status
# lines are there and neither reads error. A helper killed by its timeout, or
# one that crashed before printing, prints neither — that is not a pass.
desktop_out_healthy() {
    printf '%s\n' "$1" | grep -qE '^launcher: ' || return 1
    printf '%s\n' "$1" | grep -qE '^autostart: ' || return 1
    ! printf '%s\n' "$1" | grep -qE '^(launcher|autostart): error'
}
