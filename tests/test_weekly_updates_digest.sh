#!/usr/bin/env bash
# Behavior tests for scripts/weekly_updates_digest.sh — pins the two
# honest-failure-modes fixes from the 2026-07-11 code-review audit:
#   1. a MISSING fleet hosts file surfaces loudly + exits non-zero (2) instead
#      of silently producing a one-box digest that reads OK.
#   2. a failed `git fetch` marks the origin/main baseline UNKNOWN ('?') instead
#      of computing a false "BEHIND" against a stale ref.
# All externals (git/curl/ssh/apt/dpkg) are stubbed on PATH — no network, no ssh.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/weekly_updates_digest.sh"
fails=0

make_stubs() {  # $1 = stub dir, $2 = "fetchok" | "fetchfail"
    local sb="$1" fetch="$2" c
    mkdir -p "$sb"
    cat >"$sb/curl" <<'EOF'
#!/bin/bash
prev=""; for a in "$@"; do [[ "$prev" == "-d" ]] && printf '%s' "$a" > "$CURL_BODY_OUT"; prev="$a"; done
exit 0
EOF
    cat >"$sb/git" <<EOF
#!/bin/bash
case "\$*" in
  *"fetch origin main"*) [[ "$fetch" == fetchok ]] && exit 0 || exit 1 ;;
  *"rev-parse --short=8 origin/main"*) echo aaaaaaaa; exit 0 ;;
  *"rev-parse --short=8 HEAD"*) echo bbbbbbbb; exit 0 ;;
  *"rev-list --count"*) echo 0; exit 0 ;;
esac
exit 0
EOF
    for c in apt-cache dpkg-query apt-mark ssh timeout pgrep; do
        printf '#!/bin/bash\nexit 0\n' > "$sb/$c"
    done
    printf '#!/bin/bash\necho testbox\n' > "$sb/hostname"
    chmod +x "$sb"/*
}

run_case() {  # $1 = label; sets globals: rc, body
    local sb="$1" fetch="$2" hosts_env="$3"
    make_stubs "$sb" "$fetch"
    export CURL_BODY_OUT="$sb/body.txt"; : > "$CURL_BODY_OUT"
    PATH="$sb:$PATH" MESHFORGE_NTFY_TOPIC=faketopic \
        MESHFORGE_FLEET_HOSTS="$hosts_env" \
        bash "$SCRIPT" >/dev/null 2>&1
    rc=$?
    body="$(cat "$sb/body.txt")"
}

check() {  # $1 = description; $2 = condition (already evaluated: "ok"/"")
    if [[ -n "$2" ]]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=$((fails+1)); fi
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── Scenario 1: missing hosts file → loud + exit 2 ──
run_case "$TMP/s1" fetchok /nonexistent/hosts_file
check "missing hosts file exits 2" "$([[ $rc -eq 2 ]] && echo ok)"
check "missing hosts file warns in body" "$([[ "$body" == *"NO fleet hosts file found"* ]] && echo ok)"

# ── Scenario 2: hosts file present, fetch fails → baseline UNKNOWN, no BEHIND ──
hf="$TMP/hosts"; printf 'moc1\nmoc2\n' > "$hf"
run_case "$TMP/s2" fetchfail "$hf"
check "fetch failure exits 0 (digest still sent)" "$([[ $rc -eq 0 ]] && echo ok)"
check "fetch failure surfaces baseline-unavailable" "$([[ "$body" == *"baseline unavailable"* ]] && echo ok)"
check "fetch failure marks git '?' not BEHIND" "$([[ "$body" != *"BEHIND"* && "$body" == *"origin/main unknown"* ]] && echo ok)"

# ── Scenario 3: hosts present + fetch ok → clean pass, no false warnings ──
run_case "$TMP/s3" fetchok "$hf"
check "healthy path exits 0" "$([[ $rc -eq 0 ]] && echo ok)"
check "healthy path has no hosts/baseline warnings" "$([[ "$body" != *"NO fleet hosts file"* && "$body" != *"baseline unavailable"* ]] && echo ok)"

echo "---"
if [[ $fails -eq 0 ]]; then echo "ALL PASS"; exit 0; else echo "$fails FAILED"; exit 1; fi
