# scripts/lib/pycache_prefix.sh — keep the PRIVILEGED interpreter's bytecode
# out of the repo. Source it; it defines one variable, no side effects.
#
# Consumers (keep this list current — it is the grep target when this changes):
#   - scripts/meshforge-launcher.sh   launch_tui + launch_prometheus
#   - scripts/meshforge-terminal.sh   TUI_CMD (the desktop launcher)
#   - tests/test_regression_guards.py TestPrivilegedPycachePrefix
#
# WHY THIS EXISTS (measured fleet-wide 2026-09-20). The documented primary
# launch is `sudo python3 src/launcher_tui/main.py`. Python caches bytecode
# for every module it IMPORTS next to the source, so each privileged run left
# root-owned `__pycache__` inside /opt/meshforge. SEVEN of the ten fleet
# boxes were carrying it — the worst two at 4,059 and 1,910 root-owned
# files, the rest between 3 and 166 — and the tell was WHERE:
# `src/launcher_tui/handlers/__pycache__`. Those dirs were then root-owned
# and NOT writable by the service user, so the *unprivileged* TUI silently
# stopped caching bytecode — a permanent slowdown nothing reported. Three
# boxes had root-owned VENVS from the same habit while their units declare
# a non-root `User=`, which would fail any dependency install. This is NOT
# an upgrade artifact; it regenerates on documented, everyday use, which is
# why chowning it kept "coming back".
#
# THE CURE IS NOT A CHOWN CRON. Mopping a puddle leaves the tap running, and
# a recurring privileged `chown -R` is a large blast radius for a cosmetic
# symptom (feedback_never_arm_a_guard_that_can_kill_the_session). Redirect
# only the ROOT interpreter's bytecode out of the tree instead.
#
# The unprivileged path is deliberately NOT redirected: it writes
# user-owned cache in-repo, which is correct, fast, and has never been the
# problem. Changing it would trade a fixed bug for a slower TUI.
#
# CPython creates this tree itself on first use — verified 2026-09-20 with a
# paired control (no prefix → `__pycache__` appears beside the source; with a
# prefix naming a NON-EXISTENT dir → source dir stays clean and the tree is
# created). So there is no install step and no directory to provision.
# PYTHONPYCACHEPREFIX requires Python >= 3.8; the fleet floor is 3.9.
#
# ⚠️ `sudo` resets the environment, so exporting this before `sudo` does NOT
# reach the child. It must be passed as `sudo PYTHONPYCACHEPREFIX=... python3`,
# which is what every consumer above does and what the guard test pins.

MF_ROOT_PYCACHE="${MF_ROOT_PYCACHE:-/var/cache/meshforge/pycache-root}"
