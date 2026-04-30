#!/usr/bin/env bash
# memory_secrets_check.sh — pre-commit secrets gate for Claude memory git repos.
#
# Scans staged files for high-confidence secret patterns. Exits 1 on any match,
# listing file:line and the pattern that fired so the operator can decide:
# clean it up, or `git commit --no-verify` to override (last resort — prefer
# extending the pattern table or scrubbing the secret).
#
# Patterns are deliberately HIGH-confidence (low false-positive rate) rather
# than entropy-based, because memory routinely contains LXMF / RNS hashes,
# commit shas, and other legitimately random-looking hex strings. The signal
# is *contextual*: a key-ish variable name adjacent to a key-ish value.
#
# Usage:
#   memory_secrets_check.sh           # scan staged files (pre-commit mode)
#   memory_secrets_check.sh --test    # run against fixtures (CI / sanity)
#   memory_secrets_check.sh PATH...   # scan explicit file paths

set -uo pipefail

# tag|extended-regex|description
PATTERNS=(
    'PEM-PRIVATE-KEY|-----BEGIN [A-Z ]*PRIVATE KEY-----|PEM-encoded private key block'
    'AWS-ACCESS-KEY|AKIA[0-9A-Z]{16}|AWS access key ID'
    'GITHUB-TOKEN|gh[pousr]_[A-Za-z0-9_]{36,}|GitHub personal access / fine-grained token'
    'SLACK-TOKEN|xox[baprs]-[A-Za-z0-9-]{10,}|Slack OAuth token'
    'BCRYPT-HASH|\$2[ayb]\$[0-9]{2}\$[A-Za-z0-9./]{53}|bcrypt password hash'
    'RPC-KEY-ASSIGN|rpc_key[[:space:]]*[=:][[:space:]]*[0-9a-fA-F]{32,}|rpc_key assigned to 32+ hex chars (Reticulum pinned key)'
    'GENERIC-KEY-ASSIGN|(private_key|secret_key|api_key|password)[[:space:]]*[=:][[:space:]]*["'\'']?[A-Za-z0-9+/=_-]{16,}|generic private/secret/api/password assignment'
    'SSH-PRIVATE-KEY|-----BEGIN OPENSSH PRIVATE KEY-----|OpenSSH private key block'
)

# Files matching these globs are skipped (no scan). Useful for fixtures that
# legitimately contain example bad strings.
SKIP_PATTERNS=(
    '*memory_secrets_check*'   # this script's own self-test fixtures
)

should_skip() {
    local f="$1"
    for p in "${SKIP_PATTERNS[@]}"; do
        # shellcheck disable=SC2053
        [[ "$f" == $p ]] && return 0
    done
    return 1
}

scan_files() {
    local hits=0
    local f tag rest pattern description matches

    for f in "$@"; do
        [[ -f "$f" ]] || continue
        if should_skip "$f"; then
            continue
        fi

        for entry in "${PATTERNS[@]}"; do
            tag="${entry%%|*}"
            rest="${entry#*|}"
            pattern="${rest%%|*}"
            description="${rest##*|}"

            matches="$(grep -nE -- "$pattern" "$f" 2>/dev/null || true)"
            if [[ -n "$matches" ]]; then
                while IFS= read -r m; do
                    echo "BLOCK [$tag] $f:$m"
                    hits=$((hits + 1))
                done <<< "$matches"
                echo "       reason: $description"
            fi
        done
    done

    [[ $hits -eq 0 ]]
}

run_self_test() {
    local tmpdir
    tmpdir="$(mktemp -d -t mem_secrets_test_XXXXXX)"
    trap "rm -rf '$tmpdir'" EXIT

    # Each "should-block" fixture contains exactly one violation.
    cat > "$tmpdir/has_pem.md" <<'EOF'
Some prose, then a private key block:
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
EOF
    cat > "$tmpdir/has_rpc_key.md" <<'EOF'
On moc3: rpc_key = abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789
in /etc/reticulum/config.
EOF
    cat > "$tmpdir/has_aws.md" <<'EOF'
Sandbox: AKIAIOSFODNN7EXAMPLE — rotated weekly.
EOF
    cat > "$tmpdir/has_github.md" <<'EOF'
Old leaked token: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789
EOF
    cat > "$tmpdir/has_openssh.md" <<'EOF'
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXk...
EOF

    # Should NOT block — these are legitimate memory content.
    cat > "$tmpdir/clean_hashes.md" <<'EOF'
moc3 LXMF gateway hash f68c2f56789abcdef012345678901234 — public.
SHA-256 of the config: 1a2b3c4d5e6f78901a2b3c4d5e6f78901a2b3c4d5e6f78901a2b3c4d5e6f7890
Commit hash 95015d9 introduced memory mirror.
The rpc_key is a 64-char hex string; generate with `openssl rand -hex 32`.
The grep pattern is `^  rpc_key` — block when a literal hex value follows.
EOF

    local rc=0

    echo "=== should BLOCK ==="
    for fixture in has_pem has_rpc_key has_aws has_github has_openssh; do
        if scan_files "$tmpdir/$fixture.md" >/dev/null 2>&1; then
            echo "FAIL: $fixture.md should have triggered" >&2
            rc=1
        else
            echo "OK:   $fixture.md blocked"
        fi
    done

    echo
    echo "=== should ALLOW ==="
    if scan_files "$tmpdir/clean_hashes.md" >/dev/null 2>&1; then
        echo "OK:   clean_hashes.md passed"
    else
        echo "FAIL: clean_hashes.md was blocked (false positive)" >&2
        scan_files "$tmpdir/clean_hashes.md"
        rc=1
    fi

    if [[ $rc -eq 0 ]]; then
        echo
        echo "All self-tests passed."
    fi
    return $rc
}

main() {
    if [[ "${1:-}" == "--test" ]]; then
        run_self_test
        exit $?
    fi

    local files
    if [[ $# -gt 0 ]]; then
        files=("$@")
    else
        # Pre-commit mode: scan staged files.
        mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null)
    fi

    if [[ ${#files[@]} -eq 0 ]]; then
        exit 0
    fi

    if scan_files "${files[@]}"; then
        exit 0
    fi

    cat >&2 <<EOF

Pre-commit secrets check failed — see BLOCK lines above.

If a hit is a genuine false positive, override with:
    git commit --no-verify

If you want to permanently allow a pattern, edit:
    /opt/meshforge/scripts/memory_secrets_check.sh
EOF
    exit 1
}

main "$@"
