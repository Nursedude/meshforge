# scripts/lib/unit_compare.sh — installed-unit vs repo-template compare.
#
# Source this file; it defines functions, no side effects on source.
# Bash 4+ (associative arrays not used, but `mapfile`/`[[ ]]` are).
#
# Consumers:
#   - scripts/fleet_diag_bundle.sh     (drift section, read-only)
#   - scripts/fleet_etc_reconcile.sh   (deferred Phase B — cp + restart)
#
# The compare honors two structural rules so it only flags drift that
# actually matters:
#
# 1. **Placeholder substitution** — repo templates carry
#    `__MESHFORGE_USER__` which install_noc.sh rewrites at install
#    time. The installed unit has a real username; the template has
#    the literal placeholder. We substitute the installed unit's
#    `User=` line into the template before comparing.
#
# 2. **Comment-strip + blank-line collapse** — full-line `#` comments
#    and blank lines don't change systemd behavior. Without stripping
#    them, every doc-only template update flags as DRIFT until each
#    box gets a manual cp (the meshforge-map.service comment-only
#    false-positive class observed 2026-05-13). Stripping closes
#    that class.
#
# Mid-line `#` (after a directive's `=`) is preserved — systemd
# accepts it as a value continuation in some cases, and stripping
# would change semantics.

# Template search path. Repo-relative; assumes /opt/meshforge and
# /opt/meshanchor are the canonical checkout locations on fleet boxes.
UNIT_COMPARE_TEMPLATE_DIRS=(
    /opt/meshforge/templates/systemd
    /opt/meshforge/scripts
    /opt/meshanchor/scripts
)

# Echo the template path for an installed unit, or return non-zero.
# First-match wins per the order in UNIT_COMPARE_TEMPLATE_DIRS.
unit_compare_find_template() {
    local name="$1"
    local dir
    for dir in "${UNIT_COMPARE_TEMPLATE_DIRS[@]}"; do
        if [[ -f "$dir/$name" ]]; then
            echo "$dir/$name"
            return 0
        fi
    done
    return 1
}

# Strip full-line `#` comments + blank lines + trailing whitespace.
# Reads stdin, writes to stdout. Mid-line `#` preserved (sed regex
# anchored to start-of-line).
unit_compare_strip_comments() {
    sed -E '/^[[:space:]]*(#|$)/d; s/[[:space:]]+$//'
}

# Compare installed unit against the matching repo template.
# Echoes exactly one line:
#   MATCH
#   DRIFT <installed_md5> <template_md5> <template_path>
#   NO_TEMPLATE
#   MISSING
unit_compare_check() {
    local installed="$1"
    if [[ ! -f "$installed" ]]; then
        echo "MISSING"
        return
    fi

    local name
    name=$(basename "$installed")
    local tpl
    if ! tpl=$(unit_compare_find_template "$name"); then
        echo "NO_TEMPLATE"
        return
    fi

    local user_val
    user_val=$(grep -m1 '^User=' "$installed" 2>/dev/null | cut -d= -f2)

    local installed_norm tpl_norm
    installed_norm=$(mktemp -t unit_cmp_inst.XXXXXX)
    tpl_norm=$(mktemp -t unit_cmp_tpl.XXXXXX)

    unit_compare_strip_comments < "$installed" > "$installed_norm"

    if [[ -n "$user_val" ]] && grep -q '__MESHFORGE_USER__' "$tpl"; then
        sed "s|__MESHFORGE_USER__|$user_val|g" "$tpl" \
            | unit_compare_strip_comments > "$tpl_norm"
    else
        unit_compare_strip_comments < "$tpl" > "$tpl_norm"
    fi

    if cmp -s "$installed_norm" "$tpl_norm"; then
        echo "MATCH"
    else
        local imd5 tmd5
        imd5=$(md5sum "$installed_norm" | cut -d' ' -f1)
        tmd5=$(md5sum "$tpl_norm" | cut -d' ' -f1)
        echo "DRIFT $imd5 $tmd5 $tpl"
    fi

    rm -f "$installed_norm" "$tpl_norm"
}
