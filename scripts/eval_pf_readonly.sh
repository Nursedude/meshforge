#!/bin/bash
# eval_pf_readonly.sh — the ONLY thing an EVAL-PF eval key can run on a lab box.
#
# Installed as an ssh forced command:
#   restrict,command="/opt/meshforge/scripts/eval_pf_readonly.sh" ssh-ed25519 … eval-pf
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

set -u

LOG="${EVAL_PF_LOG:-$HOME/.local/state/meshforge/eval_pf_readonly.log}"
SHARE="$HOME/.local/share/meshforge"
CONF="$HOME/.config/meshforge"

log() {  # verdict, request
    mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
    # Tabs/newlines in the request are already refused; still strip them so a
    # log line is always one line.
    printf '%s\t%s\t%s\n' "$(date -Is)" "$1" "${2//[$'\t\n']/ }" >>"$LOG" 2>/dev/null || true
}

refuse() {  # reason
    log "REFUSED:$1" "$REQ"
    echo "eval_pf_readonly: refused ($1). Allowed: systemctl status|show|list-units|list-timers|list-unit-files|is-active|is-enabled|is-failed|cat|--failed [--user]; journalctl [--user] [-u|--user-unit UNIT] [-n N] [-p LEVEL] [-b] [--since=VALUE]; ls/cat under ~/.local/share/meshforge; ls under ~/.config/meshforge; ps aux|-ef; ss -ltn|-lun|-ltnp|-lunp; df -h; free -m|-h; uptime" >&2
    exit 126
}

REQ="${SSH_ORIGINAL_COMMAND-}"
[ -n "$REQ" ] || refuse "empty request (interactive shells are not allowed)"

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

unit_ok() { [[ "$1" =~ ^[A-Za-z0-9@._:*-]+$ ]]; }

# Expand a leading ~/ to $HOME/ (sshd passes '~' literally); any other '~'
# is refused.
expand_path() {
    local p="$1"
    case "$p" in
        "~/"*) p="$HOME/${p#\~/}" ;;
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
    # unit (or property) name. With no verb at all, only --failed is allowed
    # (systemctl's default verb is list-units).
    verb=""; failed=0
    for a in "${ARGS[@]}"; do
        case "$a" in
            --failed) failed=1 ;;
            --user | --all | --no-pager | --no-legend | --full | -l | -p) ;;
            --type=* | --state=* | --property=*) ;;
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
    done
    [ -n "$verb" ] || [ "$failed" = 1 ] || refuse "systemctl needs an allowed verb"
    log ALLOWED "$REQ"
    exec systemctl --no-pager "${ARGS[@]}"
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
            --since=* | --until=* | -o=short* | --output=short*) out+=("$a") ;;
            *) refuse "journalctl argument '$a' not allowed" ;;
        esac
        i=$((i + 1))
    done
    log ALLOWED "$REQ"
    exec journalctl --no-pager "${out[@]}"
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
    for p in "${paths[@]}"; do
        if under "$p" "$SHARE"; then :
        elif [ "$PROG" = ls ] && under "$p" "$CONF"; then :
        else refuse "path '$p' is outside the allowed roots"
        fi
    done
    log ALLOWED "$REQ"
    exec "$PROG" "${flags[@]}" -- "${paths[@]}"
    ;;
ps)
    [ "${ARGS[*]-}" = "aux" ] || [ "${ARGS[*]-}" = "-ef" ] || refuse "ps takes 'aux' or '-ef' only"
    log ALLOWED "$REQ"; exec ps "${ARGS[@]}" ;;
ss)
    case "${ARGS[*]-}" in -ltn | -lun | -ltnp | -lunp | -tln | -uln) ;; *) refuse "ss takes -ltn|-lun|-ltnp|-lunp only" ;; esac
    log ALLOWED "$REQ"; exec ss "${ARGS[@]}" ;;
df)
    [ "${ARGS[*]-}" = "-h" ] || refuse "df takes -h only"
    log ALLOWED "$REQ"; exec df -h ;;
free)
    case "${ARGS[*]-}" in -m | -h) ;; *) refuse "free takes -m or -h only" ;; esac
    log ALLOWED "$REQ"; exec free "${ARGS[@]}" ;;
uptime)
    [ "${#ARGS[@]}" -eq 0 ] || refuse "uptime takes no arguments"
    log ALLOWED "$REQ"; exec uptime ;;
*)
    refuse "program '$PROG' is not on the read-only list" ;;
esac
