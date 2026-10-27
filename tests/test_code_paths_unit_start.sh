#!/usr/bin/env bash
# lib/code_paths.sh mf_mono_to_epoch / mf_unit_start_epoch — date a unit's
# start on the MONOTONIC clock, step-immune (2026-10-27).
#
# WHY: `ActiveEnterTimestamp` is the wall clock AT START and systemd never
# rewrites it after NTP steps the clock. kiai (RTC-less) booted 10-26 21:44 on a
# clock still reading 09-27; honest_status called echo + mini-dudeai "29d
# behind" while ps showed them 5 h old on current code. fleet_pull's
# meshforge-map nag read the same stamp the same way. The formula is
#   start = now_epoch - (mono_now_s - ActiveEnterTimestampMonotonic/1e6)
# with mono_now from CLOCK_MONOTONIC — the clock systemd stamps with. Not
# /proc/uptime: that is CLOCK_BOOTTIME and counts suspend, which would push
# every start EARLY by the suspended time (review 2026-10-27).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="${CODE_PATHS_LIB:-$HERE/../scripts/lib/code_paths.sh}"
# shellcheck disable=SC1090
. "$LIB"
fails=0
expect() { if [ "$2" = "$3" ]; then printf '  %-56s ok\n' "$1"; else printf '  %-56s FAIL\n    want: [%s]\n    got : [%s]\n' "$1" "$2" "$3"; fails=$((fails+1)); fi; }

# Pure converter: <mono_usec> <now_epoch> <mono_now_s> <suspended_s>
expect "kiai shape: 999.75 s ago, truncates"   "8999" "$(mf_mono_to_epoch 4000000000 10000 5000.25 0)"
expect "fractional usec truncates, not rounds"  "9000" "$(mf_mono_to_epoch 4000999999 10000 5000.5 0)"
expect "unset (0) -> empty, never epoch 0"       ""     "$(mf_mono_to_epoch 0 10000 5000 0)"
expect "non-numeric mono -> empty"               ""     "$(mf_mono_to_epoch abc 10000 5000 0)"
expect "missing mono_now -> empty"               ""     "$(mf_mono_to_epoch 4000000000 10000 '' 0)"
expect "missing now -> empty"                    ""     "$(mf_mono_to_epoch 4000000000 '' 5000 0)"
# Started a moment AFTER mono_now was read (a restart racing the probe): within
# the slack it is a real, current start — never "incoherent".
expect "start just after mono_now (race) -> dated" "10002" "$(mf_mono_to_epoch 5002000000 10000 5000 0)"
# Far beyond: incoherent input, cannot be dated.
expect "start far beyond mono_now -> empty"     ""     "$(mf_mono_to_epoch 9000000000 10000 5000 0)"
# A computed start at/before the epoch is incoherent (bad mono_now): empty,
# never a number another consumer would compare against.
expect "computed epoch <= 0 -> empty"           ""     "$(mf_mono_to_epoch 1000000 100 5000 0)"
# SUSPEND (review 2026-10-27). CLOCK_MONOTONIC stops while suspended, so after a
# suspend the formula dates a start LATE — the false-GREEN direction. The box
# can MEASURE it: BOOTTIME - MONOTONIC = total time ever suspended. Beyond the
# slack, monotonic dating is refused (empty = cannot tell).
expect "suspended beyond slack -> empty"        ""     "$(mf_mono_to_epoch 4000000000 10000 5000 172800)"
expect "suspended within slack -> dated"        "9000" "$(mf_mono_to_epoch 4000000000 10000 5000 0.4)"
expect "missing suspended value -> empty"       ""     "$(mf_mono_to_epoch 4000000000 10000 5000 '')"
expect "slack constant is defined and numeric"  "yes"  "$(case "${MF_MONO_SLACK_S:-}" in ''|*[!0-9]*) echo no;; *) echo yes;; esac)"
# The slack is interpolated into a REMOTE shell string by honest_status, so a
# non-numeric inherited value must be refused at source time (review #5).
expect "hostile inherited slack is replaced"    "60" \
  "$(MF_MONO_SLACK_S='60; touch /tmp/x' bash -c ". '$LIB'; printf %s \"\$MF_MONO_SLACK_S\"")"

# The reader: "<monotonic_s> <suspended_s>", both numbers, on this box.
m="$(mf_mono_now)"
expect "mf_mono_now yields two numbers" "yes" "$(printf '%s' "$m" | grep -Eq '^[0-9]+(\.[0-9]+)? [0-9]+(\.[0-9]+)?$' && echo yes || echo no)"

# mf_unit_start_epoch through stubs: the REAL helper with the clock readers
# and systemctl pinned, so the result is deterministic (no timing window).
STUBS="$(mktemp -d)"; trap 'rm -r -- "$STUBS"' EXIT
cat > "$STUBS/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
case "$*" in *"-p ActiveEnterTimestampMonotonic --value"*) echo "${STUB_MONO:-0}";; esac
EOF
printf '#!/usr/bin/env bash\necho 10000\n' > "$STUBS/date"
chmod +x "$STUBS/systemctl" "$STUBS/date"
export STUB_LOG="$STUBS/log"
mf_mono_now() { echo "5000.000000 0.250000"; }
out="$(STUB_MONO=4900000000 PATH="$STUBS:$PATH" mf_unit_start_epoch meshforge-map)"
expect "unit started 100 s ago dates 100 s ago" "9900" "$out"
expect "system scope asks without --user" "yes" "$(grep -q -- '^show -p ActiveEnterTimestampMonotonic --value meshforge-map' "$STUB_LOG" && echo yes || echo no)"
: > "$STUB_LOG"
STUB_MONO=1 PATH="$STUBS:$PATH" mf_unit_start_epoch foo --user >/dev/null
expect "--user scope is passed through" "yes" "$(grep -q -- '^--user show -p ActiveEnterTimestampMonotonic --value foo' "$STUB_LOG" && echo yes || echo no)"
out="$(STUB_MONO=0 PATH="$STUBS:$PATH" mf_unit_start_epoch meshforge-map)"
expect "never-active unit (mono 0) -> empty" "" "$out"
mf_mono_now() { echo "5000.000000 90000.000000"; }
out="$(STUB_MONO=4900000000 PATH="$STUBS:$PATH" mf_unit_start_epoch meshforge-map)"
expect "box that suspended -> empty" "" "$out"
mf_mono_now() { :; }
out="$(STUB_MONO=4900000000 PATH="$STUBS:$PATH" mf_unit_start_epoch meshforge-map)"
expect "unreadable monotonic clock -> empty" "" "$out"

# ONE FORMULA, TWO IMPLEMENTATIONS (review #4). honest_status dates units with
# the same formula inside hs_skew_attr.awk (it runs on the remote box). Pin
# that both give the SAME start: a code-head AT the lib's answer reads current,
# one second later reads behind. A fix to one that skips the other fails here.
AWKF="$HERE/../scripts/hs_skew_attr.awk"
for c in "4000000000 10000 5000.25 0" "4000999999 10000 5000.5 0.3" "123456789012 1800000000 200000.75 1.5"; do
  set -- $c
  e="$(mf_mono_to_epoch "$1" "$2" "$3" "$4")"
  for d in 0 1; do
    h=$(( e + d ))
    got=$(printf 'Id=x.service\nExecStart={ argv[]=/opt/meshforge/x ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=@1\nActiveEnterTimestampMonotonic=%s\n\n' "$1" \
      | awk -v HTMF=$h -v HCMF=$h -v HTMA=SKIP -v HCMA=SKIP -v HTMM=SKIP -v HCMM=SKIP \
            -v BOXNOW="$2" -v BOXUP="$3" -v BOXSUSP="$4" -v MONOSLACK="$MF_MONO_SLACK_S" -f "$AWKF")
    want=""; [ "$d" = 1 ] && want="B /opt/meshforge x.service 0"
    expect "awk agrees with lib ($1 @ head+$d)" "$want" "$got"
  done
done

if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $fails assertion(s)"; exit 1; fi
