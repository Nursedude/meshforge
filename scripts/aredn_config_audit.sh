#!/usr/bin/env bash
# Read-only AREDN config audit for the MeshForge fleet (Phase B of the
# map-domain audit arc).
#
# Reports, per host, which AREDN auto-discovery key is set in:
#   - meshforge core:  ~/.config/meshforge/map_settings.json
#                      (key: aredn_node_ips — canonical)
#   - meshforge-maps:  ~/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json
#                      (key: aredn_node_ips — canonical, OR
#                       key: aredn_node_targets — DEPRECATED)
#
# When the maps plugin still uses `aredn_node_targets`, a one-cycle
# compat layer in /opt/meshforge-maps/src/utils/config.py copies the
# value into `aredn_node_ips` at load time and emits a one-shot warning.
# This script surfaces which boxes still need the rename so operators
# can clean up before the legacy key is removed.
#
# Reads the same host list as fleet_sync.sh / fleet_status.sh:
#   $MESHFORGE_FLEET_HOSTS (if set)
#   $HOME/.config/meshforge/fleet_hosts
#   /etc/meshforge/fleet_hosts
#
# Exit code is the number of hosts where the legacy key was the ONLY
# AREDN key set — those are the ones that benefit from the rename.

set -uo pipefail

SELF="$(basename "$0")"

FLEET_FILE="${MESHFORGE_FLEET_HOSTS:-}"
if [[ -z "$FLEET_FILE" ]]; then
    if [[ -r "$HOME/.config/meshforge/fleet_hosts" ]]; then
        FLEET_FILE="$HOME/.config/meshforge/fleet_hosts"
    elif [[ -r "/etc/meshforge/fleet_hosts" ]]; then
        FLEET_FILE="/etc/meshforge/fleet_hosts"
    fi
fi

if [[ -z "$FLEET_FILE" || ! -r "$FLEET_FILE" ]]; then
    cat >&2 <<EOF
$SELF: no fleet host list found.

Create one at \$HOME/.config/meshforge/fleet_hosts (one host per line, '#'
comments allowed). Example:

    cp contrib/fleet/fleet_hosts.example ~/.config/meshforge/fleet_hosts
    \$EDITOR ~/.config/meshforge/fleet_hosts

Or set \$MESHFORGE_FLEET_HOSTS to a different path.
EOF
    exit 2
fi

# Remote recipe. Pure read — no writes, no service touch. Emits a single
# RESULT line per host with four space-separated fields:
#   core_state  maps_state  core_path  maps_path
# core_state / maps_state are one of:
#   ips      = aredn_node_ips set (canonical)
#   targets  = aredn_node_targets set (legacy — needs rename for maps,
#              not used at all by core)
#   both     = both keys set in the same file (canonical wins, rename
#              the legacy out to clean up)
#   unset    = file exists but neither key present (uses default)
#   missing  = file doesn't exist (uses default)
REMOTE_SCRIPT='
set -u
core_path="$HOME/.config/meshforge/map_settings.json"
maps_path="$HOME/.config/meshforge/plugins/org.meshforge.extension.maps/settings.json"

inspect() {
    local p="$1"
    if [ ! -f "$p" ]; then
        echo "missing"
        return
    fi
    has_ips=0
    has_targets=0
    grep -q "\"aredn_node_ips\"[[:space:]]*:" "$p" 2>/dev/null && has_ips=1
    grep -q "\"aredn_node_targets\"[[:space:]]*:" "$p" 2>/dev/null && has_targets=1
    if [ $has_ips -eq 1 ] && [ $has_targets -eq 1 ]; then
        echo "both"
    elif [ $has_ips -eq 1 ]; then
        echo "ips"
    elif [ $has_targets -eq 1 ]; then
        echo "targets"
    else
        echo "unset"
    fi
}

core_state=$(inspect "$core_path")
maps_state=$(inspect "$maps_path")
echo "RESULT $core_state $maps_state $core_path $maps_path"
'

# Parse a RESULT line into named fields.
parse_result() {
    local line="$1"
    f_core="?"; f_maps="?"; f_corepath="?"; f_mapspath="?"
    # RESULT <core> <maps> <corepath> <mapspath>
    read -r _ f_core f_maps f_corepath f_mapspath <<< "$line"
}

printf '%-22s %-9s %-9s %s\n' "HOST" "CORE" "MAPS" "NOTES"
printf '%-22s %-9s %-9s %s\n' "----" "----" "----" "-----"

ok_count=0
err_count=0
unreach_count=0
needs_rename=0

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    host="$(echo "$raw_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$host" || "${host:0:1}" == "#" ]] && continue

    result="$(ssh -o BatchMode=yes -o ConnectTimeout=10 \
                  -o StrictHostKeyChecking=accept-new \
                  "$host" "bash -s" <<< "$REMOTE_SCRIPT" 2>&1)"
    rc=$?

    status_line="$(echo "$result" | grep -E '^RESULT ' | tail -1)"

    if [[ -n "$status_line" ]]; then
        parse_result "$status_line"
        notes=""
        # Drift cases worth surfacing
        if [[ "$f_maps" == "targets" ]]; then
            notes="rename maps key → aredn_node_ips"
            needs_rename=$((needs_rename + 1))
        elif [[ "$f_maps" == "both" ]]; then
            notes="legacy key residual — safe to delete from maps file"
        elif [[ "$f_core" == "targets" ]]; then
            # core never read this key historically, but flag in case an
            # operator copy-pasted the wrong key into the wrong file
            notes="core has no aredn_node_targets reader — likely typo"
        fi
        printf '%-22s %-9s %-9s %s\n' \
            "$host" "$f_core" "$f_maps" "$notes"
        ok_count=$((ok_count + 1))
    else
        msg="$(echo "$result" | tail -1 | tr -d '\r' | head -c 60)"
        printf '%-22s %-9s %-9s %s\n' \
            "$host" "-" "-" "unreachable (rc=$rc) ${msg}"
        unreach_count=$((unreach_count + 1))
    fi
done < "$FLEET_FILE"

echo
printf 'Summary: %d ok, %d unreachable. %d host(s) need maps-side rename.\n' \
    "$ok_count" "$unreach_count" "$needs_rename"

if [[ "$needs_rename" -gt 0 ]]; then
    cat <<EOF

To rename the legacy key on a single host (run on the host as the
operator who owns the file — NOT root):

  python3 - <<PY
  import json, pathlib
  p = pathlib.Path.home() / ".config/meshforge/plugins/org.meshforge.extension.maps/settings.json"
  s = json.loads(p.read_text())
  if "aredn_node_targets" in s and "aredn_node_ips" not in s:
      s["aredn_node_ips"] = s.pop("aredn_node_targets")
      p.write_text(json.dumps(s, indent=2))
      print("renamed")
  else:
      print("no-op (already aligned)")
  PY

After rename, restart \`meshforge-map\` (the :8808 service in
meshforge-maps) so the new key is read.
EOF
fi

exit "$needs_rename"
