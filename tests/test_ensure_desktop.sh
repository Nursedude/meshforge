#!/usr/bin/env bash
# Behavior test for scripts/ensure_desktop.sh (2026-10-25).
#
# The desktop launcher was installed only by update.sh; the fleet deploy
# (fleet_sync + fleet_pull) never runs update.sh, so a box provisioned that
# way never got it (the lab, found 10-25: no .desktop, no meshforge-terminal).
# The helper converges two things on every deploy:
#   launcher  — on a CONSOLE box only (default target graphical.target, the
#               headless_unless_console declaration); never on a headless one.
#   autostart — the box's own deployment.json `desktop_autostart.fleet_url`
#               opts it in; only files carrying our marker are ever removed.
# The properties that matter: an unreadable fact is an ERROR, never "headless"
# or "undeclared" (either would act on a value nobody observed), and a hand-
# made autostart file is never touched.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/ensure_desktop.sh"
REPO="$(cd "$HERE/.." && pwd)"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
APPS="$TMP/apps"; BIN="$TMP/bin"; SHARE="$TMP/share"; ETC="$TMP/etc"; H="$TMP/home"; CALLS="$TMP/installer.calls"
AUTO="$H/.config/autostart"; DEP="$H/.config/meshforge/deployment.json"

# Fake systemctl: `get-default` prints $TARGET, or fails when TARGET=FAIL.
cat > "$TMP/systemctl" <<'EOF'
#!/usr/bin/env bash
[ "$1" = "get-default" ] || exit 2
[ "${TARGET:-}" = "FAIL" ] && { echo "Failed to connect to bus" >&2; exit 1; }
echo "$TARGET"
EOF
# Fake sudo: `sudo -n <cmd...>` runs cmd; SUDO_FAIL=1 simulates a refusal.
cat > "$TMP/sudo" <<'EOF'
#!/usr/bin/env bash
[ "${SUDO_FAIL:-0}" = 1 ] && { echo "sudo: a password is required" >&2; exit 1; }
[ "$1" = "-n" ] && shift
exec "$@"
EOF
# Fake installer: records the call, installs what the real one would (from
# THE shared list, scripts/lib/desktop.sh), or fails when INSTALL_FAIL=1.
cat > "$TMP/install-desktop" <<EOF
#!/usr/bin/env bash
echo called >> "$CALLS"
[ "\${INSTALL_FAIL:-0}" = 1 ] && { echo "boom" >&2; exit 3; }
[ "\${INSTALL_NOOP:-0}" = 1 ] && exit 0
. "$REPO/scripts/lib/desktop.sh"
U="$H/.local/share/applications/org.meshforge.app.desktop"
own=0; desktop_user_copy_is_ours "$APPS/org.meshforge.app.desktop" "\$U" && own=1
desktop_copies "$REPO" | while IFS="\$(printf '\t')" read -r s d; do
    mkdir -p "\$(dirname "\$d")" && cp "\$s" "\$d"
done
if [ "\$own" = 1 ]; then mkdir -p "\$(dirname "\$U")"; cp "$REPO/org.meshforge.app.desktop" "\$U"; fi
mkdir -p "$BIN"
ln -sfn "$REPO/scripts/meshforge-terminal.sh" "$BIN/meshforge-terminal"
ln -sfn "$REPO/scripts/meshforge-launcher.sh" "$BIN/meshforge"
EOF
chmod +x "$TMP/systemctl" "$TMP/sudo" "$TMP/install-desktop"

run() {
    SYSTEMCTL_BIN="$TMP/systemctl" SUDO_BIN="$TMP/sudo" \
    INSTALL_DESKTOP_BIN="$TMP/install-desktop" \
    DESKTOP_APPS_DIR="$APPS" DESKTOP_BIN_DIR="$BIN" HOME="$H" \
    DESKTOP_SHARE_DIR="$SHARE" DESKTOP_ETC_DIR="$ETC" \
    DESKTOP_INSTALL_DIR="${DESKTOP_INSTALL_DIR:-$REPO}" \
    bash "$SCRIPT" "$@" 2>&1
}
check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }
calls() { [ -f "$CALLS" ] && wc -l < "$CALLS" | tr -d ' ' || echo 0; }
# The optional install targets (polkit, profile.d) EXIST in the fake tree, so
# the shared list expects them — the shape of a Pi desktop box.
reset() {
    rm -rf "$APPS" "$BIN" "$H" "$CALLS" "$SHARE" "$ETC"
    mkdir -p "$H/.config/meshforge" "$SHARE/polkit-1/actions" "$ETC/profile.d"
}
declare_url() { printf '{"role": "lab", "desktop_autostart": {"fleet_url": "%s"}}\n' "$1" > "$DEP"; }

# ── headless box: no launcher, installer never called ────────────────────
reset
out="$(TARGET=multi-user.target run --apply)"; rc=$?
check "headless box reports launcher: headless" "$(echo "$out" | grep -q '^launcher: headless' && echo ok)"
check "headless box never calls the installer" "$([ "$(calls)" = 0 ] && echo ok)"
check "headless box exits 0" "$([ "$rc" = 0 ] && echo ok)"

# ── an unreadable default target is an ERROR, never 'headless' ───────────
reset
out="$(TARGET=FAIL run --apply)"; rc=$?
check "unreadable target reports launcher: error" "$(echo "$out" | grep -q '^launcher: error' && echo ok)"
check "unreadable target exits 1" "$([ "$rc" = 1 ] && echo ok)"
check "unreadable target never calls the installer" "$([ "$(calls)" = 0 ] && echo ok)"

# ── console box, launcher missing: dry-run writes nothing ────────────────
reset
out="$(TARGET=graphical.target run)"; rc=$?
check "dry-run on a console box reports would_install" "$(echo "$out" | grep -q '^launcher: would_install' && echo ok)"
check "dry-run never calls the installer" "$([ "$(calls)" = 0 ] && echo ok)"
check "dry-run exits 0" "$([ "$rc" = 0 ] && echo ok)"

# ── console box, launcher missing: --apply installs it ───────────────────
reset
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "apply on a console box reports installed" "$(echo "$out" | grep -q '^launcher: installed' && echo ok)"
check "apply calls the installer once" "$([ "$(calls)" = 1 ] && echo ok)"
check "apply exits 0" "$([ "$rc" = 0 ] && echo ok)"

# ── second run: present, installer NOT called again (idempotent) ─────────
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "second run reports launcher: present" "$(echo "$out" | grep -q '^launcher: present' && echo ok)"
check "second run does not re-run the installer" "$([ "$(calls)" = 1 ] && echo ok)"

# ── a stale installed .desktop (differs from the repo) is re-installed ───
echo "# drift" >> "$APPS/org.meshforge.app.desktop"
out="$(TARGET=graphical.target run --apply)"
check "stale launcher is re-installed" "$(echo "$out" | grep -q '^launcher: installed' && [ "$(calls)" = 2 ] && echo ok)"

# ── missing meshforge-terminal link alone counts as not installed ────────
rm -f "$BIN/meshforge-terminal"
out="$(TARGET=graphical.target run)"
check "missing terminal link reads would_install" "$(echo "$out" | grep -q '^launcher: would_install' && echo ok)"

# ── installer failure / sudo refusal are errors, exit 1 ──────────────────
reset
out="$(TARGET=graphical.target INSTALL_FAIL=1 run --apply)"; rc=$?
check "installer failure reports launcher: error" "$(echo "$out" | grep -q '^launcher: error' && echo ok)"
check "installer failure exits 1" "$([ "$rc" = 1 ] && echo ok)"
reset
out="$(TARGET=graphical.target SUDO_FAIL=1 run --apply)"; rc=$?
check "sudo refusal reports launcher: error naming the manual command" \
    "$(echo "$out" | grep '^launcher: error' | grep -q 'install-desktop.sh' && echo ok)"
check "sudo refusal exits 1" "$([ "$rc" = 1 ] && echo ok)"

# ── autostart: no deployment.json at all = undeclared (absent by design) ─
reset; rm -f "$DEP"
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "no deployment.json reads autostart: undeclared" "$(echo "$out" | grep -q '^autostart: undeclared' && echo ok)"
check "no deployment.json writes no autostart file" "$([ ! -e "$AUTO" ] || [ -z "$(ls -A "$AUTO")" ] && echo ok)"

# ── autostart declared: both managed files, URL in the fleet entry ───────
reset; declare_url "http://manager.example:5000/fleet"
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "declared autostart reports written" "$(echo "$out" | grep -q '^autostart: written' && echo ok)"
check "TUI autostart entry exists and is marked" \
    "$(grep -q '^X-MeshForge-Managed=true' "$AUTO/org.meshforge.app.desktop" 2>/dev/null && echo ok)"
check "TUI autostart entry runs the terminal launcher" \
    "$(grep -q '^Exec=.*meshforge-terminal.sh' "$AUTO/org.meshforge.app.desktop" 2>/dev/null && echo ok)"
check "fleet autostart entry opens the declared URL" \
    "$(grep -q '^Exec=.*http://manager.example:5000/fleet' "$AUTO/org.meshforge.fleet.desktop" 2>/dev/null && echo ok)"
check "fleet autostart entry is marked" \
    "$(grep -q '^X-MeshForge-Managed=true' "$AUTO/org.meshforge.fleet.desktop" 2>/dev/null && echo ok)"
check "declared autostart exits 0" "$([ "$rc" = 0 ] && echo ok)"
out="$(TARGET=graphical.target run --apply)"
check "second run reports autostart: present" "$(echo "$out" | grep -q '^autostart: present' && echo ok)"

# ── dry-run with a declaration writes nothing ────────────────────────────
reset; declare_url "http://manager.example:5000/fleet"
out="$(TARGET=graphical.target run)"
check "dry-run autostart reports would_write" "$(echo "$out" | grep -q '^autostart: would_write' && echo ok)"
check "dry-run autostart writes no file" "$([ ! -e "$AUTO/org.meshforge.fleet.desktop" ] && echo ok)"

# ── a malformed URL is an error and writes nothing ───────────────────────
for bad in 'http://x:5000/fleet; rm -rf ~' 'file:///etc/passwd' 'http://x/a b' 'javascript:alert(1)'; do
    reset; declare_url "$bad"
    out="$(TARGET=graphical.target run --apply)"; rc=$?
    check "malformed URL [$bad] reports autostart: error" "$(echo "$out" | grep -q '^autostart: error' && echo ok)"
    check "malformed URL [$bad] writes no fleet entry" "$([ ! -e "$AUTO/org.meshforge.fleet.desktop" ] && echo ok)"
    check "malformed URL [$bad] exits 1" "$([ "$rc" = 1 ] && echo ok)"
done

# ── an unreadable deployment.json is an ERROR and removes nothing ────────
reset; declare_url "http://manager.example:5000/fleet"
TARGET=graphical.target run --apply >/dev/null
echo '{not json' > "$DEP"
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "invalid deployment.json reports autostart: error" "$(echo "$out" | grep -q '^autostart: error' && echo ok)"
check "invalid deployment.json leaves existing entries alone" "$([ -f "$AUTO/org.meshforge.fleet.desktop" ] && echo ok)"
check "invalid deployment.json exits 1" "$([ "$rc" = 1 ] && echo ok)"

# ── declaration withdrawn: managed entries removed ───────────────────────
echo '{"role": "lab"}' > "$DEP"
out="$(TARGET=graphical.target run --apply)"
check "withdrawn declaration reports autostart: removed" "$(echo "$out" | grep -q '^autostart: removed' && echo ok)"
check "withdrawn declaration removes the managed entries" \
    "$([ ! -e "$AUTO/org.meshforge.fleet.desktop" ] && [ ! -e "$AUTO/org.meshforge.app.desktop" ] && echo ok)"

# ── a HAND-MADE file of the same name (no marker) is never touched ───────
reset; mkdir -p "$AUTO"; echo '{"role": "lab"}' > "$DEP"
printf '[Desktop Entry]\nType=Application\nExec=/usr/bin/true\n' > "$AUTO/org.meshforge.app.desktop"
out="$(TARGET=graphical.target run --apply)"
check "unmarked autostart file is kept when undeclared" "$([ -f "$AUTO/org.meshforge.app.desktop" ] && echo ok)"
declare_url "http://manager.example:5000/fleet"
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "unmarked autostart file is not overwritten when declared" \
    "$(grep -q '^Exec=/usr/bin/true' "$AUTO/org.meshforge.app.desktop" && echo ok)"
check "unmarked collision is reported, not silent" "$(echo "$out" | grep '^autostart:' | grep -q 'hand-made' && echo ok)"

# ── headless box with a declaration: autostart is not written ────────────
reset; declare_url "http://manager.example:5000/fleet"
out="$(TARGET=multi-user.target run --apply)"
check "headless box never writes autostart even when declared" "$([ ! -e "$AUTO/org.meshforge.fleet.desktop" ] && echo ok)"
check "headless box says why autostart was skipped" "$(echo "$out" | grep '^autostart:' | grep -q 'headless' && echo ok)"

# ── review 2026-10-25 (non-author, ddf617b8) ─────────────────────────────
# F1: a JSON fleet_url with an embedded newline must not smuggle a 2nd line
reset; printf '{"desktop_autostart": {"fleet_url": "http://m:5000/fleet\\nExec=sh -c evil"}}\n' > "$DEP"
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "F1 newline in fleet_url reports autostart: error" "$(echo "$out" | grep -q '^autostart: error' && echo ok)"
check "F1 newline in fleet_url writes no fleet entry" "$([ ! -e "$AUTO/org.meshforge.fleet.desktop" ] && echo ok)"
check "F1 newline in fleet_url exits 1" "$([ "$rc" = 1 ] && echo ok)"
check "F1 no smuggled Exec line anywhere in autostart" "$( ! grep -rqs 'evil' "$AUTO" && echo ok)"

# F3: a frozen /usr/local/bin/meshforge is not 'present'. (Its per-user menu
# leg was REVERSED by the 5aec69f1 review, R7 below: a differing per-user copy
# is the user's menu edit — reported as shadowed, never reinstalled over.)
reset; TARGET=graphical.target run --apply >/dev/null
rm -f "$BIN/meshforge"; cp "$REPO/scripts/meshforge-launcher.sh" "$BIN/meshforge"
out="$(TARGET=graphical.target run)"
check "F3 frozen meshforge COPY (not a link) reads would_install" "$(echo "$out" | grep -q '^launcher: would_install' && echo ok)"

# F5: python3 unavailable is its own error, never a 'bad URL'
reset; declare_url "http://manager.example:5000/fleet"
out="$(TARGET=graphical.target PYTHON_BIN="$TMP/no-such-python" run --apply)"; rc=$?
check "F5 missing python reports autostart: error" "$(echo "$out" | grep -q '^autostart: error' && echo ok)"
check "F5 missing python is not blamed on the URL" "$( ! echo "$out" | grep -q 'not a plain http' && echo ok)"
check "F5 missing python exits 1" "$([ "$rc" = 1 ] && echo ok)"

# F6: an installer that exits 0 but installs nothing is an error, not 'installed'
reset
out="$(TARGET=graphical.target INSTALL_NOOP=1 run --apply)"; rc=$?
check "F6 no-op installer reports launcher: error" "$(echo "$out" | grep -q '^launcher: error' && echo ok)"
check "F6 no-op installer never reads installed" "$( ! echo "$out" | grep -q '^launcher: installed' && echo ok)"
check "F6 no-op installer exits 1" "$([ "$rc" = 1 ] && echo ok)"

# F9: a checkout that is not the install dir is refused (install-desktop.sh
# would move a real install dir aside to symlink this one in)
reset
out="$(TARGET=graphical.target DESKTOP_INSTALL_DIR="$TMP/elsewhere" run --apply)"; rc=$?
check "F9 foreign checkout reports launcher: error" "$(echo "$out" | grep -q '^launcher: error' && echo ok)"
check "F9 foreign checkout never calls the installer" "$([ "$(calls)" = 0 ] && echo ok)"
check "F9 foreign checkout exits 1" "$([ "$rc" = 1 ] && echo ok)"

# ── review 2026-10-25 (non-author, 5aec69f1) ─────────────────────────────
# R1: a foreign checkout must not write autostart entries pointing INTO it
reset; declare_url "http://manager.example:5000/fleet"
out="$(TARGET=graphical.target DESKTOP_INSTALL_DIR="$TMP/elsewhere" run --apply)"; rc=$?
check "R1 foreign checkout writes no TUI autostart entry" "$([ ! -e "$AUTO/org.meshforge.app.desktop" ] && echo ok)"
check "R1 foreign checkout reports autostart: error" "$(echo "$out" | grep -q '^autostart: error' && echo ok)"
check "R1 foreign checkout exits 1 on --apply" "$([ "$rc" = 1 ] && echo ok)"

# R2: install-desktop.sh itself refuses to displace a REAL install directory.
# Runs the real installer NON-root only: the guard sits before the root check,
# so a missing guard ends at "Please run as root" and can touch nothing.
if [ "$(id -u)" != 0 ]; then
    mkdir -p "$TMP/optmf"; touch "$TMP/optmf/keep"
    out="$(MESHFORGE_INSTALL_LINK="$TMP/optmf" bash "$REPO/scripts/install-desktop.sh" 2>&1)"; rc=$?
    check "R2 installer refuses a foreign checkout over a real install dir" "$(echo "$out" | grep -q '^Refused' && echo ok)"
    check "R2 refusal says what to do" "$(echo "$out" | grep -qF "$TMP/optmf/scripts/install-desktop.sh" && echo ok)"
    check "R2 refusal exits 1" "$([ "$rc" = 1 ] && echo ok)"
    check "R2 the real install dir is untouched" "$([ -d "$TMP/optmf" ] && [ ! -L "$TMP/optmf" ] && [ -e "$TMP/optmf/keep" ] && echo ok)"
    ln -sfn "$REPO" "$TMP/optlink"
    out="$(MESHFORGE_INSTALL_LINK="$TMP/optlink" bash "$REPO/scripts/install-desktop.sh" 2>&1)"
    check "R2 a SYMLINKED install link is not refused" "$( ! echo "$out" | grep -q '^Refused' && echo ok)"
else
    echo "SKIP: R2 (running as root — never run the real installer here)"
fi

# R3: every COPIED artifact is part of 'present', from the installer's own list
reset; TARGET=graphical.target run --apply >/dev/null
rm -f "$ETC/profile.d/meshforge.sh"
out="$(TARGET=graphical.target run)"
check "R3 missing /etc/profile.d login message reads would_install" "$(echo "$out" | grep -q '^launcher: would_install' && echo ok)"
reset; TARGET=graphical.target run --apply >/dev/null
echo "<!-- drift -->" >> "$SHARE/polkit-1/actions/org.meshforge.policy"
out="$(TARGET=graphical.target run)"
check "R3 stale polkit policy reads would_install" "$(echo "$out" | grep -q '^launcher: would_install' && echo ok)"
reset; TARGET=graphical.target run --apply >/dev/null
rm -f "$SHARE/icons/hicolor/scalable/apps/org.meshforge.app.svg"
out="$(TARGET=graphical.target run)"
check "R3 missing taskbar icon reads would_install" "$(echo "$out" | grep -q '^launcher: would_install' && echo ok)"

# R4: update.sh and fleet_sync judge the helper's output with ONE function,
# and output missing a status line is not healthy
(
    . "$REPO/scripts/lib/desktop.sh"
    desktop_out_healthy "$(printf 'launcher: present\nautostart: undeclared\n')" && echo "R4OK1"
    desktop_out_healthy "launcher: present" || echo "R4OK2"
    desktop_out_healthy "" || echo "R4OK3"
    desktop_out_healthy "$(printf 'launcher: present\nautostart: error — x\n')" || echo "R4OK4"
) > "$TMP/r4" 2>&1
check "R4 both lines, no error = healthy" "$(grep -q R4OK1 "$TMP/r4" && echo ok)"
check "R4 no autostart line = NOT healthy" "$(grep -q R4OK2 "$TMP/r4" && echo ok)"
check "R4 empty output = NOT healthy" "$(grep -q R4OK3 "$TMP/r4" && echo ok)"
check "R4 an error line = NOT healthy" "$(grep -q R4OK4 "$TMP/r4" && echo ok)"
check "R4 update.sh judges with desktop_out_healthy" "$(grep -qF 'desktop_out_healthy "$desk_out"' "$REPO/scripts/update.sh" && echo ok)"
check "R4 fleet_sync self leg judges with desktop_out_healthy" "$(grep -qF 'desktop_out_healthy "$desk_out"' "$REPO/scripts/fleet_sync.sh" && echo ok)"

# R5: the URL rule lives in ONE place (the Python validator)
check "R5 URL regex appears once in ensure_desktop.sh" \
    "$([ "$(grep -c 'https?://\[A-Za-z0-9' "$SCRIPT")" = 1 ] && echo ok)"

# R6: an unusable state dir is reported as such, never as 'could not read
# deployment.json' — the declaration does not need the state dir at all
reset; TARGET=graphical.target run --apply >/dev/null
mv "$H/.local/state/meshforge" "$TMP/state.moved"; echo x > "$H/.local/state/meshforge"
declare_url "http://manager.example:5000/fleet"
out="$(TARGET=graphical.target run --apply)"
check "R6 state dir unusable: declaration is still read" "$( ! echo "$out" | grep -q 'could not read deployment.json' && echo ok)"
check "R6 state dir unusable: autostart still written" "$(echo "$out" | grep -q '^autostart: written' && echo ok)"
reset; mkdir -p "$H/.local/state"; echo x > "$H/.local/state/meshforge"
out="$(TARGET=graphical.target run --apply)"
check "R6 state dir unusable: launcher error names the state dir" "$(echo "$out" | grep '^launcher: error' | grep -q 'state dir' && echo ok)"
check "R6 state dir unusable: installer not run blind" "$([ "$(calls)" = 0 ] && echo ok)"

# R7: a per-user menu copy that differs is the USER'S edit — reported, kept
reset; TARGET=graphical.target run --apply >/dev/null
echo "# my menu edit" >> "$H/.local/share/applications/org.meshforge.app.desktop"
out="$(TARGET=graphical.target run --apply)"; rc=$?
check "R7 edited per-user copy reports launcher: shadowed" "$(echo "$out" | grep -q '^launcher: shadowed' && echo ok)"
check "R7 edited per-user copy does not re-run the installer" "$([ "$(calls)" = 1 ] && echo ok)"
check "R7 edited per-user copy is kept" "$(grep -q 'my menu edit' "$H/.local/share/applications/org.meshforge.app.desktop" && echo ok)"
check "R7 shadowed exits 0" "$([ "$rc" = 0 ] && echo ok)"
# ...a reinstall for ANOTHER reason still keeps it (the installer's rule)
echo "# drift" >> "$APPS/org.meshforge.app.desktop"
TARGET=graphical.target run --apply >/dev/null
check "R7 reinstall keeps the user's edited copy" "$(grep -q 'my menu edit' "$H/.local/share/applications/org.meshforge.app.desktop" && echo ok)"
# ...while our OWN stale copy (identical to the old system entry) is refreshed
reset; TARGET=graphical.target run --apply >/dev/null
echo "# old release" >> "$APPS/org.meshforge.app.desktop"
echo "# old release" >> "$H/.local/share/applications/org.meshforge.app.desktop"
TARGET=graphical.target run --apply >/dev/null
check "R7 our own stale per-user copy is refreshed" \
    "$(cmp -s "$REPO/org.meshforge.app.desktop" "$H/.local/share/applications/org.meshforge.app.desktop" && echo ok)"

# R8: dry-run on a foreign checkout reports, it does not error
reset
out="$(TARGET=graphical.target DESKTOP_INSTALL_DIR="$TMP/elsewhere" run)"; rc=$?
check "R8 foreign dry-run exits 0" "$([ "$rc" = 0 ] && echo ok)"
check "R8 foreign dry-run says it would refuse" "$(echo "$out" | grep -q '^launcher: would_refuse' && echo ok)"
check "R8 foreign dry-run prints no error line" "$( ! echo "$out" | grep -q ': error' && echo ok)"

# R9: a headless box tells the truth about a launcher update.sh installed
reset; TARGET=graphical.target run --apply >/dev/null
out="$(TARGET=multi-user.target run)"
check "R9 headless line does not deny an installed launcher" "$( ! echo "$out" | grep -q 'no launcher' && echo ok)"
check "R9 headless line reports the launcher present" "$(echo "$out" | grep '^launcher: headless' | grep -q 'present' && echo ok)"
reset
out="$(TARGET=multi-user.target run)"
check "R9 headless line reports an absent launcher as absent" "$(echo "$out" | grep '^launcher: headless' | grep -q 'absent' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
