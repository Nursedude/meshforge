#!/usr/bin/env bash
# Behavior test for update.sh's self-re-exec. Port of MeshAnchor 83208d96
# (MeshAnchor #151 re-test, KN4YDL 2026-10-19).
#
# update.sh `git pull`s ITSELF. Bash keeps reading the file it opened and git
# replaces the file, so every step after the pull runs the PRE-update script.
# On MeshAnchor that installed unrendered unit placeholders; here the same
# hole means a fix to any later step of update.sh lands one update late.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$HERE/.."
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

check() { # name, condition-exit-code
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails+1)); fi
}

# ── 1. the real mechanism: a script that git-replaces itself ─────────────────
# v1 pulls v2 over itself, then prints its step-4 line. Without the re-exec
# the OLD text runs ("step4 v1"); with it, the NEW one does.
R="$TMP/repo"; mkdir -p "$R/scripts/lib"
cp "$REPO/scripts/lib/update_reexec.sh" "$R/scripts/lib/" 2>/dev/null
git -C "$R" init -q && git -C "$R" config user.email t@t && git -C "$R" config user.name t
mk_update() { # version
  cat > "$R/scripts/update.sh" <<EOF
#!/usr/bin/env bash
set -e
cd "\$(dirname "\$0")/.."
. scripts/lib/update_reexec.sh
FROM=\$(git rev-parse --short HEAD)
[ -n "\${MESHFORGE_UPDATE_FROM:-}" ] && FROM="\$MESHFORGE_UPDATE_FROM"
git checkout -q main 2>/dev/null || true
mf_reexec_if_self_changed "\$FROM" "\$(git rev-parse --short HEAD)" "\$PWD" scripts/update.sh "\$@"
echo "step4 $1 from=\$FROM args=\$*"
EOF
}
git -C "$R" checkout -q -b main
mk_update v1; git -C "$R" add -A; git -C "$R" commit -qm v1; A="$(git -C "$R" rev-parse --short HEAD)"
mk_update v2; git -C "$R" commit -qam v2
git -C "$R" checkout -q -b old "$A"
out="$(bash "$R/scripts/update.sh" --skip-deps 2>&1)"
echo "$out" | grep -q "^step4 v2 " ; check "new script's later steps run after a self-update" $?
echo "$out" | grep -q "^step4 v1 " ; [ $? -ne 0 ]; check "old script's later steps do NOT run" $?
echo "$out" | grep -q "from=$A " ; check "change range still counted from the ORIGINAL commit" $?
echo "$out" | grep -q "args=--skip-deps$" ; check "arguments survive the re-exec" $?

# 1-control: the same self-update WITHOUT the re-exec runs the stale text.
# If this ever stops reproducing, test 1 proves nothing — say so, don't pass.
git -C "$R" checkout -q old
sed -i '/mf_reexec_if_self_changed/d' "$R/scripts/update.sh"
out="$(bash "$R/scripts/update.sh" 2>&1)"
echo "$out" | grep -q "^step4 v1 "; check "control: without the re-exec the OLD text runs (the bug)" $?
git -C "$R" checkout -q -- scripts/update.sh; git -C "$R" checkout -q main

# 1b. unchanged script → no re-exec (one run, and it is this one)
out="$(bash "$R/scripts/update.sh" 2>&1)"
[ "$(echo "$out" | grep -c '^step4')" = 1 ]; check "no re-exec when update.sh did not change" $?

# 1c. loop guard: already re-executed → never again
. "$REPO/scripts/lib/update_reexec.sh" 2>/dev/null
out="$( MESHFORGE_UPDATE_REEXEC=1 mf_reexec_if_self_changed "$A" main "$R" scripts/update.sh; echo STAYED )"
[ "$out" = STAYED ]; check "re-exec happens at most once" $?

# ── 2. update.sh wiring: re-exec after the pull, before step 2
pull_ln="$(grep -n 'git pull origin' "$REPO/scripts/update.sh" | head -1 | cut -d: -f1)"
reexec_ln="$(grep -n 'mf_reexec_if_self_changed ' "$REPO/scripts/update.sh" | head -1 | cut -d: -f1)"
step2_ln="$(grep -n '\[2/5\]' "$REPO/scripts/update.sh" | head -1 | cut -d: -f1)"
[ -n "$reexec_ln" ] && [ "$reexec_ln" -gt "$pull_ln" ] && [ "$reexec_ln" -lt "$step2_ln" ]
check "update.sh re-execs after the pull, before step 2" $?

echo
[ "$fails" -eq 0 ] && echo "ALL PASS" || echo "$fails FAILED"
exit "$fails"
