#!/bin/bash
# box_config_capture.sh — monthly manager organ: snapshot every fleet box's
# operating config (and this box's own) into ~/fleet-configs/<box>/snapshot/,
# where the fleet-vault bundle carries it off-site, age-encrypted.
#
# WHY (2026-10-04): the vault held a ONE-SHOT capture of 7 Pi boxes from
# 08-11 (two months stale: moc3's RNode move, the txpower experiments and the
# rnsd ipv6 drop-in all post-date it) and NOTHING for the manager, lehua or
# meshanchor-server — while LAB-ZERO.md claimed the manager was captured. The
# 08-11 capture tool itself was never kept. A box rebuild after SD death would
# have restored code and memory but not the box's config: fleet roster, posture,
# RNS radio settings, the drop-ins no deploy script installs.
#
# What is captured (per box; paths resolved ON that box):
#   ~/.config/meshforge, ~/.config/meshanchor   files <=256 KB, maxdepth 1
#   ~/.config/systemd/user                       unit / timer / path / drop-in
#   ~/.ssh/config                                aliases + tunnel ProxyCommands
#   user + root crontab                          -> _meta/crontab-*.txt
#   /etc/reticulum/{config,interfaces,storage/transport_identity}
#   /etc/meshtasticd (whole tree incl. ssl/, minus vendor available.d)
#   /etc/meshforge (noc.yaml & co, files <=256 KB) — added 2026-10-07
#   {/root,/var/lib/meshtasticd}/.portduino/**/*.proto  radio prefs (channels,
#     owner, LoRa) — the user-meshtasticd boxes keep them under /var/lib
#   /etc/systemd/system units + drop-ins for rnsd / meshtasticd / meshforge* /
#     meshanchor* / mosquitto / lxmd / nomadnet
#   /usr/local/bin/meshforge* wrappers; sha256 of meshtasticd binaries (never
#     the binary)
#   /etc/NetworkManager/system-connections, /etc/hosts
# NEVER captured: ssh private keys (operator-held by design, LAB-ZERO.md).
# The snapshot carries secrets (identities, wifi PSKs, API keys): it lands
# ONLY in fleet-configs (no remote) and leaves the box only as age ciphertext.
#
# Each box's snapshot is REPLACED only after a successful, non-trivial fetch;
# an unreachable box keeps its last good snapshot and is a named CONCERN leg.
# Each run COMMITS what it captured (fleet-vault refuses a dirty tree — the
# 2026-10-04 aredn_config_capture lesson). A capture that cannot be committed
# is a CONCERN, never OK.
#
# Hosts: this box (by `hostname`) + every host in the fleet_hosts list
# (MESHFORGE_FLEET_HOSTS, else ~/.config/meshforge/fleet_hosts).
#
# Crontab idiom (manager, monthly — the 5th, after aredn_config_capture, so the
# 6th's fleet_vault_refresh carries both):
#   47 7 5 * * /opt/meshforge/scripts/box_config_capture.sh \
#     >> ~/.local/state/meshforge/box_config_capture.log 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh box_config_capture FAIL wrapper_crashed

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERDICT="${BOX_CAPTURE_VERDICT:-$HERE/cron_verdict.sh}"
NAME=box_config_capture
HOSTS_FILE="${MESHFORGE_FLEET_HOSTS:-$HOME/.config/meshforge/fleet_hosts}"
DEST_ROOT="${BOX_CAPTURE_ROOT:-$HOME/fleet-configs}"
SELF="${BOX_CAPTURE_SELF:-$(hostname)}"
MIN_FILES=3   # fewer than this = the fetch did not really happen

say() { "$VERDICT" "$NAME" "$1" "$2"; }

# The collector runs ON the box (locally for self, via `ssh <host> bash -s` for
# the rest) and writes a gzip tar to stdout. Single-quoted: nothing expands
# here; everything resolves on the box being captured.
read -r -d '' COLLECTOR <<'EOF'
set -u
S=""; sudo -n true 2>/dev/null && S="sudo -n"
T=$(mktemp -d); L=$(mktemp)
for d in "$HOME/.config/meshforge" "$HOME/.config/meshanchor"; do
  [ -d "$d" ] && find "$d" -maxdepth 1 -type f -size -257k
done >>"$L"
[ -d "$HOME/.config/systemd/user" ] && find "$HOME/.config/systemd/user" -maxdepth 2 -type f \
  \( -name '*.service' -o -name '*.timer' -o -name '*.path' -o -name '*.conf' \) >>"$L"
[ -f "$HOME/.ssh/config" ] && echo "$HOME/.ssh/config" >>"$L"
crontab -l >"$T/crontab-user.txt" 2>/dev/null || true
[ -n "$S" ] && $S crontab -l >"$T/crontab-root.txt" 2>/dev/null || true
for p in /etc/reticulum/config /etc/reticulum/interfaces /etc/reticulum/storage/transport_identity \
         /etc/NetworkManager/system-connections /etc/hosts; do
  $S test -e "$p" 2>/dev/null && echo "$p"
done >>"$L"
# The whole /etc/meshtasticd tree (ssl/ included — the 08-11 moc5 lesson) minus
# the vendor templates in available.d, and the radio's own prefs: channel keys,
# owner, LoRa settings live in *.proto under /root/.portduino — or under
# /var/lib/meshtasticd/.portduino where meshtasticd runs as its own user.
$S find /etc/meshtasticd -type f -not -path '*/available.d/*' -size -257k 2>/dev/null >>"$L"
# /etc/meshforge (noc.yaml & co) — uncaptured until 2026-10-07, when moc's
# noc.yaml turned out to exist nowhere but the box. The env seam exists only
# for the test shim; over real ssh it is unset and this reads /etc/meshforge.
$S find "${BOX_CAPTURE_ETC_MESHFORGE:-/etc/meshforge}" -type f -size -257k 2>/dev/null >>"$L"
$S find /root/.portduino /var/lib/meshtasticd/.portduino -name '*.proto' -type f -size -257k 2>/dev/null >>"$L"
find /etc/systemd/system -maxdepth 2 -type f \( -path '*rnsd*' -o -path '*meshtasticd*' \
  -o -path '*meshforge*' -o -path '*meshanchor*' -o -path '*mosquitto*' -o -path '*lxmd*' \
  -o -path '*nomadnet*' \) 2>/dev/null >>"$L"
find /usr/local/bin -maxdepth 1 -type f -name 'meshforge*' -size -257k 2>/dev/null >>"$L"
# Launch wrappers are symlinks into the repo since 09-19 — record the layout.
ls -la /usr/local/bin/meshforge* >"$T/wrappers.txt" 2>/dev/null || true
# Patched / installed meshtasticd binaries: fingerprint, never the binary.
for b in /usr/local/sbin/meshtasticd* /usr/bin/meshtasticd /usr/sbin/meshtasticd; do
  [ -f "$b" ] && sha256sum "$b"
done >"$T/binary-fingerprints.txt" 2>/dev/null
{ echo "host=$(hostname)"; echo "captured_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)";
  echo "sudo=$([ -n "$S" ] && echo yes || echo no)"; echo "paths_listed=$(wc -l <"$L")"; } >"$T/capture.meta"
# Belt and braces: a private key must never ride along, whatever the list says.
grep -v -E '/\.ssh/(id_|.*_np$|.*\.pem$)' "$L" >"$L.ok"
$S tar -czf - --ignore-failed-read --transform "s,^${T#/},_meta," -T "$L.ok" "$T" 2>/dev/null
rm -rf "$T" "$L" "$L.ok"
EOF

hosts=("$SELF")
if [ -f "$HOSTS_FILE" ]; then
    while IFS= read -r line; do
        line="${line%%#*}"; line="${line//[[:space:]]/}"
        [ -n "$line" ] && [ "$line" != "$SELF" ] && hosts+=("$line")
    done < "$HOSTS_FILE"
fi

captured=() captured_dirs=() failed=() nosudo=()
for h in "${hosts[@]}"; do
    box="${h##*@}"
    tmp="$(mktemp -d)"
    if [ "$h" = "$SELF" ]; then
        bash -s <<<"$COLLECTOR" > "$tmp/c.tgz" 2>/dev/null
    else
        timeout 180 ssh -o ConnectTimeout=15 -o BatchMode=yes "$h" bash -s \
            <<<"$COLLECTOR" > "$tmp/c.tgz" 2>/dev/null
    fi
    mkdir -p "$tmp/x"
    if tar -xzf "$tmp/c.tgz" -C "$tmp/x" 2>/dev/null \
       && [ "$(find "$tmp/x" -type f | wc -l)" -ge "$MIN_FILES" ]; then
        mkdir -p "$DEST_ROOT/$box"
        rm -rf "$DEST_ROOT/$box/snapshot"
        mv "$tmp/x" "$DEST_ROOT/$box/snapshot"
        n=$(find "$DEST_ROOT/$box/snapshot" -type f | wc -l)
        captured+=("$box(${n}f)")
        captured_dirs+=("$box/snapshot")
        grep -q '^sudo=no' "$DEST_ROOT/$box/snapshot/_meta/capture.meta" 2>/dev/null && nosudo+=("$box")
    else
        failed+=("$box")
    fi
    rm -rf "$tmp"
done

join() { local IFS=,; echo "$*"; }

commit_note=""
if [ ${#captured_dirs[@]} -gt 0 ]; then
    if [ ! -d "$DEST_ROOT/.git" ]; then
        commit_note="NOT COMMITTED: $DEST_ROOT is not a git repo — fleet-vault cannot carry it"
    elif ! git -C "$DEST_ROOT" add -A -- "${captured_dirs[@]}" 2>/dev/null; then
        commit_note="NOT COMMITTED: git add failed in $DEST_ROOT — fleet_vault_refresh will refuse"
    elif ! git -C "$DEST_ROOT" diff --cached --quiet -- "${captured_dirs[@]}"; then
        if ! git -C "$DEST_ROOT" commit -q \
                -m "capture: $NAME $(date -u +%Y-%m-%d) — $(join "${captured[@]}")" \
                -- "${captured_dirs[@]}" >/dev/null 2>&1; then
            commit_note="NOT COMMITTED: git commit failed in $DEST_ROOT — fleet_vault_refresh will refuse"
        fi
    fi
fi

partial=""
[ ${#nosudo[@]} -gt 0 ] && partial=" NO-SUDO(/etc not captured)=$(join "${nosudo[@]}")"
total=$(( ${#captured[@]} + ${#failed[@]} ))
if [ ${#captured[@]} -eq 0 ]; then
    say FAIL "nothing captured from $total host(s): $(join "${failed[@]}") — nothing captured is not everything safe"
elif [ ${#failed[@]} -gt 0 ] || [ -n "$commit_note" ] || [ -n "$partial" ]; then
    say CONCERN "captured=$(join "${captured[@]}")${failed:+ UNCAPTURED=$(join "${failed[@]}") (last good snapshot retained)}${partial}${commit_note:+ $commit_note}"
else
    say OK "captured $total box(es): $(join "${captured[@]}")"
fi
exit 0
