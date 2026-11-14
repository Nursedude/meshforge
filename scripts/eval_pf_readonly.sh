#!/bin/bash
# eval_pf_readonly.sh — the ONLY thing an EVAL-PF eval key can run on a lab box.
#
# Installed ONLY by the one grant writer (box_truth_grant.py add --kind eval-pf),
# as the forced command
#   /bin/bash /opt/meshforge/scripts/eval_pf_readonly.sh --grant SHA256:<fp> </dev/null
# so sshd puts the session's requested command in $SSH_ORIGINAL_COMMAND and
# runs THIS instead (plans/eval_pf_grilling_2026_11_13.md Q14/Q18).
#
# Contract: allow a fixed set of READ commands, refuse everything else, log
# every request with its verdict. Nothing is ever passed through a shell: the
# request is split on spaces into words, each word must use a safe character
# set, and the allowed program is exec'd with that word array.
#
# Deliberately NOT allowed: `cat` under ~/.config/meshforge (configs can hold
# MQTT passwords / API keys — list only), anything that writes, restarts, or
# opens a radio/TCP client.
#
# Exit: the program's own exit code when allowed; 126 when refused.
#
# Gates (tests/test_eval_pf_readonly.py): the program arms are a CLOSED list;
# every arm ends in run_allowed/refuse; every other program name on the box is
# refused with no output; a recorder of every executable shows only the asked
# program + helpers run. NOT gated by any test (Fable 11-14 round 8): a future
# edit that reads a file with a bash BUILTIN inside an allowed arm ($(<f),
# mapfile <f, read <f, source). Any change to this file needs a NON-AUTHOR
# review by running before it reaches a box holding the eval key.

set -u
# The client's LANG/LC_* reach us through sshd; under a UTF-8 locale bash's
# [A-Za-z] ranges match letters like 'é'. Every check below assumes bytes.
export LC_ALL=C

# HOME is RESOLVED ONCE, here, and every path below derives from RHOME — the
# HOME *string* is never used again (Fable 11-14 round 10: HOME=<link>/../B
# made realpath -s name a different eval_pf than the real one, skipping its
# audit). A HOME that is unset, empty, relative or `/` gives RHOME
# "/nonexistent-home": the log cannot be written there, so every request
# refuses (rc 126) — unless EVAL_PF_LOG is set (tests), when the root checks
# below refuse every path read.
case "${HOME-}" in
    /?*) RHOME="$(realpath -m -- "$HOME" 2>/dev/null)" ;;
    *) RHOME="" ;;
esac
case "$RHOME" in /?*) ;; *) RHOME="/nonexistent-home" ;; esac
LOG="${EVAL_PF_LOG:-$RHOME/.local/state/meshforge/eval_pf_readonly.log}"
GRANT="-"
SHARE="$RHOME/.local/share/meshforge"
CONF="$RHOME/.config/meshforge"

log() {  # verdict, request, [exec'd argv] — an unwritable log REFUSES (no unaudited access)
    mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
    # Only printable ASCII reaches the log (C0, DEL, C1, bidi → '?'). An
    # ALLOWED request is ≤1024 bytes and single-spaced (checked below), so it
    # is logged whole — the cap only ever cuts a REFUSED one.
    local v="${1//[^ -~]/?}" r="${2//[^ -~]/?}" x="${3:--}"
    x="${x//[^ -~]/?}"
    # 2>/dev/null BEFORE >> so bash's own open error is silenced too.
    if ! printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "${v:0:120}" "${r:0:4000}" "$GRANT" "$x" 2>/dev/null >>"$LOG"; then
        echo "eval_pf_readonly: refused (audit log $LOG is not writable)" >&2
        exit 126
    fi
}

refuse() {  # reason
    log "REFUSED:$1" "$REQ"
    echo "eval_pf_readonly: refused ($1). Allowed: systemctl status|show|list-units|list-timers|list-unit-files|is-active|is-enabled|is-failed|cat|--failed [--user]; journalctl [--user] [-u|--user-unit UNIT] [-n N] [-p LEVEL] [-b] [--since=VALUE]; ls/cat under ~/.local/share/meshforge; ls under ~/.config/meshforge; ps aux|-ef; ss -ltn|-lun|-ltnp|-lunp; df -h; free -m|-h; uptime" >&2
    exit 126
}

REQ="${SSH_ORIGINAL_COMMAND-}"
# The grant binds its fingerprint in as `--grant SHA256:<43>`; anything else
# in our own argv means we were not invoked by the grant line — refuse.
if [ "$#" -gt 0 ]; then
    if [ "$#" -eq 2 ] && [ "$1" = "--grant" ] && [[ "$2" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]]; then
        GRANT="$2"
    else
        refuse "wrapper invoked with unexpected arguments"
    fi
fi
[ -n "$REQ" ] || refuse "empty request (interactive shells are not allowed)"
# The log must name everything that ran (Fable 11-14 round 3: space-padding
# pushed an allowed read's argument past the log cap). So: bounded, and
# exactly single-spaced — the words exec'd are the request logged.
[ "${#REQ}" -le 1024 ] || refuse "request longer than 1024 bytes"
case "$REQ" in " "* | *" " | *"  "*) refuse "words must be separated by single spaces" ;; esac

# Whole-request charset: letters, digits and @ . _ : / = , + ~ * - and single
# spaces. This alone rejects ; & | $ ` ( ) < > \ ' " ! # ? [ ] { } and any
# tab or newline.
if [[ ! "$REQ" =~ ^[A-Za-z0-9@._:/=,+~*\ -]+$ ]]; then
    refuse "character outside the allowed set"
fi
case "$REQ" in *..*) refuse "'..' is not allowed" ;; esac

read -r -a W <<<"$REQ"
[ "${#W[@]}" -gt 0 ] || refuse "empty request"
PROG="${W[0]}"
ARGS=("${W[@]:1}")

# THE ONLY WAY A PROGRAM RUNS (Fable 11-14 rounds 3+4: the log must name
# everything that ran). The 5th log field is the exact argv exec'd — e.g. the
# RESOLVED path a symlink led to — and a record that would be cut refuses.
# 3000 keeps the longest line (ts + verdict 120 + request 4000 + grant + exec)
# under 8192 bytes = ONE write(), so concurrent requests never interleave
# (Fable 11-14 round 9: an 8442-byte line went out as 8192 + rest).
EXEC_CAP=3000
run_allowed() {
    local x="$*"
    [ "${#x}" -le "$EXEC_CAP" ] || refuse "exec record over $EXEC_CAP bytes would be cut"
    log ALLOWED "$REQ" "$x"
    exec "$@"
}

unit_ok() { [[ "$1" =~ ^[A-Za-z0-9@._:*][A-Za-z0-9@._:*-]*$ ]]; }

# Expand a leading ~/ to $HOME/ (sshd passes '~' literally); any other '~'
# is refused.
expand_path() {
    local p="$1"
    case "$p" in
        "~/"*) p="${p#\~/}"; case "$p" in *"~"*) return 1 ;; esac; p="$RHOME/$p" ;;
        *"~"*) return 1 ;;
    esac
    printf '%s' "$p"
}

under() {  # path root — true if path is root or inside it (no .. — refused above)
    case "$1" in "$2" | "$2"/*) return 0 ;; esac
    return 1
}

case "$PROG" in
systemctl)
    # The FIRST non-option word is what systemctl treats as the verb, so it
    # must be an allowed verb; every later non-option word must look like a
    # unit name. With no verb at all, only --failed is allowed (systemctl's
    # default verb is list-units). `-p` CONSUMES the next word (as systemctl
    # does — Fable 11-14: reading it as a bare flag let `-p status restart X`
    # run `restart`), and that word must be a property list.
    verb=""; failed=0; i=0
    prop_ok() { [[ "$1" =~ ^[A-Za-z][A-Za-z0-9]*(,[A-Za-z][A-Za-z0-9]*)*$ ]]; }
    while [ $i -lt ${#ARGS[@]} ]; do
        a="${ARGS[$i]}"
        case "$a" in
            --failed) failed=1 ;;
            --user | --all | --no-pager | --no-legend | --full | -l) ;;
            -p)
                i=$((i + 1)); v="${ARGS[$i]-}"
                prop_ok "$v" || refuse "-p needs a property list (got '${v}')" ;;
            --property=*) prop_ok "${a#--property=}" || refuse "bad property '${a#--property=}'" ;;
            --type=* | --state=*)
                [[ "${a#*=}" =~ ^[a-z0-9][a-z0-9-]*(,[a-z0-9][a-z0-9-]*)*$ ]] || refuse "bad value in '$a'" ;;
            -*) refuse "systemctl option '$a' not allowed" ;;
            *)
                if [ -z "$verb" ]; then
                    case "$a" in
                        status | show | list-units | list-timers | list-unit-files | is-active | is-enabled | is-failed | cat) verb="$a" ;;
                        *) refuse "systemctl verb '$a' not allowed" ;;
                    esac
                else
                    unit_ok "$a" || refuse "bad unit name '$a'"
                fi ;;
        esac
        i=$((i + 1))
    done
    [ -n "$verb" ] || [ "$failed" = 1 ] || refuse "systemctl needs an allowed verb"
    run_allowed systemctl --no-pager "${ARGS[@]}"
    ;;
journalctl)
    i=0; out=()
    while [ $i -lt ${#ARGS[@]} ]; do
        a="${ARGS[$i]}"
        case "$a" in
            --user | -b | --no-pager | -q | --quiet) out+=("$a") ;;
            -u | --user-unit | -n | -p)
                i=$((i + 1)); v="${ARGS[$i]-}"
                [ -n "$v" ] || refuse "$a needs a value"
                case "$a" in
                    -n) [[ "$v" =~ ^[0-9]{1,5}$ ]] || refuse "-n takes a number up to 99999" ;;
                    -p) [[ "$v" =~ ^[a-z0-9.]+$ ]] || refuse "bad priority '$v'" ;;
                    *) unit_ok "$v" || refuse "bad unit name '$v'" ;;
                esac
                out+=("$a" "$v") ;;
            --since=* | --until=* | --output=short | --output=short-iso | --output=short-precise | --output=short-monotonic | --output=short-full | --output=short-unix) out+=("$a") ;;
            *) refuse "journalctl argument '$a' not allowed" ;;
        esac
        i=$((i + 1))
    done
    run_allowed journalctl --no-pager "${out[@]}"
    ;;
ls | cat)
    paths=(); flags=()
    for a in "${ARGS[@]}"; do
        case "$a" in
            -l | -a | -la | -al | -lh | -lah | -1 | -lt | -ltr)
                [ "$PROG" = ls ] || refuse "cat takes no options"
                flags+=("$a") ;;
            -*) refuse "option '$a' not allowed" ;;
            *) p="$(expand_path "$a")" || refuse "'~' only at the start of a path"
               case "$p" in /*) ;; *) refuse "paths must be absolute or start with ~/" ;; esac
               paths+=("$p") ;;
        esac
    done
    [ "${#paths[@]}" -gt 0 ] || refuse "$PROG needs a path"
    # NO LINK IS FOLLOWED below a root (Fable 11-14 rounds 1, 6, 7: every
    # path escape was an operator-made link around a check). A path is read
    # only if it RESOLVES to exactly where it SAYS (realpath vs realpath -s),
    # under a root (the roots themselves may be links), and never under the
    # eval's own dir by its literal name. The exec'd path is the resolved one.
    lshare="$(realpath -m -s -- "$SHARE")"; lconf="$(realpath -m -s -- "$CONF")"
    rshare="$(realpath -m -- "$SHARE")"; rconf="$(realpath -m -- "$CONF")"
    [ -n "$lshare" ] && [ -n "$lconf" ] && [ -n "$rshare" ] && [ -n "$rconf" ] \
        || refuse "cannot resolve the allowed roots"
    # The roots must resolve to EXACTLY <real HOME>/.local/share/meshforge and
    # <real HOME>/.config/meshforge — HOME itself may be a link, nothing below
    # it may be (Fable 11-14 rounds 8+9: a root aliasing another — share ->
    # config let cat read config secrets — or resolving to / or elsewhere).
    # Fixed, disjoint roots end the root-alias class; any other layout
    # refuses EVERY path read. (Measured 11-14: no root is a link on
    # meshforge-lab or the manager.)
    # SHARE/CONF are built from the RESOLVED home, so lexical == resolved iff
    # no link sits at or below .local / .config.
    [ "$RHOME" != "/nonexistent-home" ] && [ "$lshare" = "$SHARE" ] && [ "$rshare" = "$SHARE" ] \
        && [ "$lconf" = "$CONF" ] && [ "$rconf" = "$CONF" ] \
        || refuse "a root is a link or alias (share/config must be plain dirs under HOME)"
    # eval_pf must be a plain dir of plain, singly-linked files: a link in it
    # (cases -> ../store) moves the key somewhere readable, and a hardlink is
    # invisible by path. Either one refuses EVERY path read (fail closed).
    EV="$lshare/eval_pf"; rEV="$(realpath -m -- "$EV")"
    [ -n "$rEV" ] || refuse "cannot resolve the allowed roots"
    if [ -e "$EV" ] || [ -L "$EV" ]; then
        [ ! -L "$EV" ] && [ -d "$EV" ] || refuse "eval_pf is not a plain directory"
        bad="$(find "$EV" \( -type l -o \( ! -type d -links +1 \) \) -print -quit 2>/dev/null)" \
            || refuse "cannot audit eval_pf"
        [ -z "$bad" ] || refuse "eval_pf holds a link or a hardlinked file"
    fi
    # Any hardlink into eval_pf from outside also shows here as -links +1.
    resolved=()
    for p in "${paths[@]}"; do
        l="$(realpath -m -s -- "$p")" && r="$(realpath -m -- "$p")" || refuse "cannot resolve '$p'"
        if under "$l" "$lshare"; then lroot="$lshare"; rroot="$rshare"
        elif [ "$PROG" = ls ] && under "$l" "$lconf"; then lroot="$lconf"; rroot="$rconf"
        else refuse "path '$p' is outside the allowed roots"
        fi
        [ "$r" = "$rroot${l#"$lroot"}" ] || refuse "path '$p' goes through a link (links are not followed)"
        # Literal AND resolved (round 8: a ROOT may alias into the share —
        # config -> share listed eval_pf through ls).
        if under "$l" "$EV" || under "$r" "$rEV"; then
            refuse "the eval's own cases/results are not readable"
        fi
        resolved+=("$r")
    done
    paths=("${resolved[@]}")
    run_allowed "$PROG" "${flags[@]}" -- "${paths[@]}"
    ;;
ps)
    [ "${ARGS[*]-}" = "aux" ] || [ "${ARGS[*]-}" = "-ef" ] || refuse "ps takes 'aux' or '-ef' only"
    run_allowed ps "${ARGS[@]}" ;;
ss)
    case "${ARGS[*]-}" in -ltn | -lun | -ltnp | -lunp | -tln | -uln) ;; *) refuse "ss takes -ltn|-lun|-ltnp|-lunp only" ;; esac
    run_allowed ss "${ARGS[@]}" ;;
df)
    [ "${ARGS[*]-}" = "-h" ] || refuse "df takes -h only"
    run_allowed df -h ;;
free)
    case "${ARGS[*]-}" in -m | -h) ;; *) refuse "free takes -m or -h only" ;; esac
    run_allowed free "${ARGS[@]}" ;;
uptime)
    [ "${#ARGS[@]}" -eq 0 ] || refuse "uptime takes no arguments"
    run_allowed uptime ;;
*)
    refuse "program '$PROG' is not on the read-only list" ;;
esac
