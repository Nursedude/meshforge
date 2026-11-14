#!/bin/bash
# eval_pf_ssh.sh — the ONE way an EVAL-PF session reaches the lab box.
#
#   scripts/eval_pf_ssh.sh systemctl --user --failed
#
# Always connects with the dedicated eval key and its own ssh config
# (plans/eval_pf_grilling_2026_11_13.md, step 3):
#   * -F <eval config>  → ~/.ssh/config is never read, so no other Host
#     block's IdentityFile can be offered;
#   * the config sets IdentitiesOnly yes + IdentityAgent none, and the agent
#     socket is unset here too → a key loaded in ssh-agent (the operator's
#     full-shell key) is never offered;
#   * `--` comes BEFORE the host, so no word the session types can become an
#     ssh option (-i, -o, -F, -L …): everything is the remote request.
# The box side then runs only scripts/eval_pf_readonly.sh (forced command).
#
# Exit: the remote exit code (126 = refused by the wrapper); 2 = not set up.

set -u

CFG="${EVAL_PF_SSH_CONFIG:-$HOME/.config/meshforge/eval_pf/ssh_config}"

if [ ! -r "$CFG" ]; then
    echo "eval_pf_ssh: no eval ssh config at $CFG — run scripts/eval_pf_key_install.py --apply" >&2
    exit 2
fi
if [ "$#" -eq 0 ]; then
    echo "eval_pf_ssh: give the read command to run on the lab box, e.g. 'systemctl --user --failed'" >&2
    exit 2
fi

exec env -u SSH_AUTH_SOCK ssh -F "$CFG" -- eval-lab "$*"
