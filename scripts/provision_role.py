#!/usr/bin/env python3
"""Role-aware fleet provisioner (v1) — converge THIS box to its declared role.

Reads the declarative role catalog (`docs/fleet_roles.yaml`) and the box's role
(`~/.config/meshforge/deployment.json` → `role`), then brings local systemd unit
state into line with the role's declaration. Idempotent, dry-run by default,
fail-loud, and reuses the `utils.service_check` SSOT for every systemd
operation (no raw systemctl here — MF008).

  v1 scope (see .claude/plans/provisioner_scope.md):
    - unit states: enabled | disabled | absent
    - masking invariant: rival RNS host masked on a box that owns rnsd (Issue #69)
    - config deltas (bbox/cap/caches): ASSERTED against real state — code<->yaml
      node_cap cross-check, response-cache presence, bbox geo-anchor. The map
      defaults are code-baked / deployment-specific, NOT operator-settable, so
      this converges by assertion not mutation (see config_delta_actions). The
      one force-settable delta (meshtasticd mqtt.root #77) is a separate slice.
    - external roles (provisioned_by:*) and singletons: reported, not enforced

Usage:
    python3 scripts/provision_role.py                 # dry-run: print the diff
    sudo python3 scripts/provision_role.py --apply     # converge
    python3 scripts/provision_role.py --role full-gateway   # override role
    python3 scripts/provision_role.py --set-role primary    # write role, exit
    python3 scripts/provision_role.py --enroll-user-timers  # install+enable the
                                                            # role's user timers
                                                            # (as the operator)

Exit codes: 0 = converged/clean, 1 = drift (dry-run) or apply failure, 2 = config error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

from utils.paths import get_real_user_home  # noqa: E402
from utils.user_units import resolve_operator_home, user_timer_enrolled  # noqa: E402
from utils.service_check import (  # noqa: E402
    check_systemd_service,
    is_service_unit_installed,
    is_service_masked,
    enable_service,
    disable_service,
    stop_service,
    mask_service,
)

DEFAULT_ROLES_FILE = _SCRIPT_DIR.parent / "docs" / "fleet_roles.yaml"
DEPLOYMENT_JSON = get_real_user_home() / ".config" / "meshforge" / "deployment.json"
# fleet_hosts location resolves via utils.fleet_hosts (THE resolver — env
# override, per-repo tier, /etc fallback) at the --fleet-check call site; a
# hardcoded user-home path here was one of ~13 chain copies (2026-07-29).
# Remote role-gathering shells out to ssh; the command is operator-configurable
# via $MESHFORGE_SSH (no key/host hardcoded here — MF014). The operator's ssh
# config/agent normally provides auth, so the default bare "ssh" works.
SSH_CMD = os.environ.get("MESHFORGE_SSH", "ssh")

# RNS hosts that must NEVER own the listener on a box that runs rnsd (one rnsd
# per box — Issue #69). Narrow, explicit list; mask each if present + unmasked.
KNOWN_RNS_RIVALS = ("meshanchor-daemon",)

VALID_UNIT_STATES = {"enabled", "disabled", "absent"}

# Where the shipped `systemd --user` unit bodies live. A declared user timer
# `X.timer` is enrolled from `templates/systemd/X-user.timer` + the matching
# `-user.service` (the templates' own install recipes, made executable —
# 2026-09-09, finding 5: the roles file declared these enabled and NOTHING in
# the product could put them there).
USER_UNIT_TEMPLATE_DIR = _SCRIPT_DIR.parent / "templates" / "systemd"

# The in-product remediation for a declared-but-never-enrolled user timer.
# Named in the advisory so the operator is not sent to a wiki (MF018 spirit).
ENROLL_CMD = "python3 scripts/provision_role.py --enroll-user-timers"


@dataclass
class Action:
    """One convergence step (planned, possibly applied)."""
    item: str
    current: str
    desired: str
    verb: str          # noop | enable | disable | mask | warn
    required: bool = True
    detail: str = ""
    result: str = ""   # filled on apply


# The state-changing verbs plan() can emit — THE shared constant for every
# consumer that filters a plan into a change set (this script's main(),
# the TUI fleet_provision core, probe_role_drift's test-pin). Three
# independent hardcodes of this tuple diverged once already
# (honest_failure_modes #5). foundation_actions() adds 'foundation'
# separately — it is not a plan() verb.
PLAN_CHANGE_VERBS = ("enable", "disable", "mask")


# --------------------------------------------------------------------------
# Role resolution (pure)
# --------------------------------------------------------------------------

def load_roles(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "roles" not in data:
        raise ValueError(f"{path}: missing top-level 'roles'")
    return data


def resolve_role(catalog: dict, role: str) -> dict:
    """Flatten a role to its effective definition, applying `inherits`.

    Returns the role dict with `services` AND `user_timers` merged
    parent→child. Raises KeyError for an unknown role.

    `user_timers` inherits by the same rule as `services` deliberately: a key
    that merged for one and silently did not for the other would be a trap for
    whoever adds the next role — they would declare a timer on a parent and
    quietly get no coverage on its children. An inheriting role that does not
    run a parent's timer says `absent` explicitly (see fleet_roles.yaml).
    """
    roles = catalog["roles"]
    if role not in roles:
        raise KeyError(role)
    node = roles[role]
    services: Dict[str, str] = {}
    user_timers: Dict[str, str] = {}
    parent = node.get("inherits")
    if parent:
        resolved_parent = resolve_role(catalog, parent)
        services.update(resolved_parent.get("services", {}))
        user_timers.update(resolved_parent.get("user_timers", {}))
    services.update(node.get("services", {}) or {})
    user_timers.update(node.get("user_timers", {}) or {})
    merged = dict(node)
    merged["services"] = services
    merged["user_timers"] = user_timers
    return merged


# --------------------------------------------------------------------------
# Observe + diff (pure given the observe callbacks)
# --------------------------------------------------------------------------

def _unit_current(name: str) -> str:
    """Human-readable current state of a unit, via the SSOT."""
    if is_service_masked(name):
        return "masked"
    running, enabled = check_systemd_service(name)
    if not is_service_unit_installed(name) and not running and not enabled:
        return "absent"
    return f"{'active' if running else 'inactive'}/{'enabled' if enabled else 'disabled'}"


def _waiver_enablement_met(cur: str, waived: str) -> bool:
    """Is a waiver's DECLARED state satisfied by the live state?

    Deliberately PERMISSIVE: it answers only "is the box MORE ON than the
    operator declared?", because that is the sole case that is unambiguously
    hidden drift. Everything else is honored.

    ``VALID_UNIT_STATES`` is {enabled, disabled, absent} — a vocabulary about
    ENABLEMENT, never about running — so only the enablement half of
    ``_unit_current``'s ``active|inactive/enabled|disabled`` form is compared.

    Why permissive (2026-09-09): the first cut required an exact match and
    immediately failed an existing pinned test — a waiver declaring ``disabled``
    on a unit that is ``absent`` (not installed at all). That waiver IS honored:
    absent is off and then some, exactly as ``masked`` is. The guard caught the
    over-strict version before it shipped; a stricter rule here would have
    turned a satisfied declaration into a blocking warning on the RF-sparse
    boxes.

    ⚠️ An out-of-vocabulary ``waived`` never reaches here any more. The first
    cut returned ``True`` for it, on the reasoning that "the caller's own branch
    already reports unknown desired states" — which was FALSE: that branch
    (``plan()``'s ``desired not in VALID_UNIT_STATES``) judges the ROLE's value
    and is skipped by the override path's ``continue``. So a waiver with a
    missing or misspelled ``state`` key read as an honored exception forever —
    honest_failure_modes #3, a validator absorbing what the author cannot have
    meant. ``_honored_override_state()`` now rejects those BEFORE this is
    called; the defensive ``False`` below keeps an unchecked value from ever
    reading as satisfied again if a future caller forgets.
    """
    if waived not in VALID_UNIT_STATES:
        return False
    _, _, enablement = cur.partition("/") if "/" in cur else ("", "", cur)
    if waived == "enabled":
        # Declared ON: absent/masked/disabled all fail to deliver it.
        return enablement == "enabled"
    # Declared OFF (disabled or absent): only an ENABLED unit contradicts it.
    return enablement != "enabled"


def _honored_override_state(ov) -> Optional[str]:
    """The state a ``service_overrides`` entry EFFECTIVELY declares, or ``None``
    when the override is not honored.

    An override is honored only when it is a dict, carries a non-empty
    ``reason`` (an unexplained exception is hidden drift), AND names a state
    inside ``VALID_UNIT_STATES``. A missing or misspelled ``state`` key is a
    VALIDATION ERROR, not a permissive default: it cannot be checked against
    anything, so honoring it silently retires the unit's drift check
    (honest_failure_modes #3).

    THE consumer that must not skip this (2026-09-09, finding 4): the rnsd
    masking invariant. It used to read the ROLE's ``services['rnsd']`` and so
    masked ``meshanchor-daemon`` on a box whose reasoned waiver named
    meshanchor-daemon as the RNS owner — Issue #69 in reverse, executed by
    ``--apply``. Ownership is a property of the EFFECTIVE declaration.
    """
    if not isinstance(ov, dict):
        return None
    if not (ov.get("reason") or "").strip():
        return None
    state = ov.get("state")
    return state if state in VALID_UNIT_STATES else None


def _override_action(unit: str, ov) -> Action:
    """Render one ``service_overrides`` entry as its single plan Action."""
    reason = (ov.get("reason") or "").strip() if isinstance(ov, dict) else ""
    waived = ov.get("state", "?") if isinstance(ov, dict) else str(ov)
    cur = _unit_current(unit)

    if not reason:
        return Action(unit, cur, f"waived:{waived}", "warn", required=True,
                      detail="service_override missing required 'reason' "
                             "— NOT honored (an unexplained waiver is "
                             "hidden drift)")

    if _honored_override_state(ov) is None:
        # Reason present, state unusable. Report LOUDLY rather than absorb:
        # the whole point of a waiver is that something checks it, and a state
        # nothing can compare against is a declaration that checks nothing.
        missing = not isinstance(ov, dict) or "state" not in ov
        what = ("has no 'state' key" if missing
                else f"declares an unknown state '{waived}'")
        return Action(unit, cur, f"waived:{waived}", "warn", required=True,
                      detail=(f"service_override {what} — NOT honored. Valid: "
                              f"{'|'.join(sorted(VALID_UNIT_STATES))}. A waiver "
                              f"whose state cannot be checked against the live "
                              f"unit is not an exception, it is an unverifiable "
                              f"claim ({reason})"))

    # 2026-09-09 (aim audit finding, NARROWED after re-derivation).
    # This branch used to emit "honored" without ever comparing
    # `cur` to `waived`, so a waiver could be VIOLATED and still
    # read as an honored exception — and role_drift, which fires
    # only on blocking warnings, read `clean` over it.
    #
    # Two distinct cases, and conflating them was the defect:
    #
    #  1. The waiver is not MET (enablement differs from the
    #     declared state). That is hidden drift in exactly the way
    #     a reason-less waiver is, so it blocks. A declaration
    #     nothing checks is decoration.
    #  2. The waiver IS met on enablement, but the unit is RUNNING
    #     while declared `disabled`/`absent`. This is NOT drift and must not
    #     block: VALID_UNIT_STATES is {enabled,disabled,absent} —
    #     a vocabulary about ENABLEMENT ONLY. It cannot express
    #     "not running", so an override whose reason is about
    #     runtime ("runs RADIO-OFF") is unverifiable by
    #     construction. Measured on the manager box 2026-09-09:
    #     is-enabled=disabled (declaration MET), is-active=active,
    #     the radio serving ~225 pkt/hr — and the operator knows,
    #     having said so on 09-07. Paging here would be paging
    #     about a human decision (feedback_never_restore_a_
    #     deliberate_stop). So DISCLOSE it instead: silence is what
    #     let it read as a clean "honored" for two days.
    if not _waiver_enablement_met(cur, waived):
        return Action(unit, cur, f"waived:{waived}", "warn", required=True,
                      detail=(f"service_override NOT MET: declares "
                              f"'{waived}' but the unit is '{cur}' — a "
                              f"waiver the box does not honor is hidden "
                              f"drift, not an exception ({reason})"))
    if waived in ("disabled", "absent") and cur.startswith("active/"):
        # `absent` lands here too (2026-09-09, finding 6): a unit that is
        # installed AND RUNNING while the waiver says `absent` used to get the
        # plain "honored" line — the same silence, one vocabulary word over.
        return Action(unit, cur, f"waived:{waived}", "warn", required=False,
                      detail=(f"intentional per-node exception: {reason} "
                              f"— ⚠️ declaration MET on enablement but the "
                              f"unit is RUNNING ({cur}). '{waived}' cannot "
                              f"express 'not running', so this override's "
                              f"runtime intent is UNVERIFIED here; judge it "
                              f"by hand"))
    return Action(unit, cur, f"waived:{waived}", "warn", required=False,
                  detail=f"intentional per-node exception: {reason}")


def _user_timer_unit_installed(unit: str) -> Optional[bool]:
    """Is the user unit BODY present in the operator's user-unit dir?

    Distinguishes the two states the enable-symlink read cannot: *never
    enrolled here* (no unit file — the fresh-provision case, which nothing in
    the product used to be able to fix) from *enrolled, then switched off*
    (unit file present, symlink gone — real drift, and the case the 2026-08-09
    declaration was added to catch). ``None`` when the operator is
    unresolvable — unobservable, never "absent".
    """
    home = resolve_operator_home()
    if home is None:
        return None
    return os.path.exists(os.path.join(home, ".config", "systemd", "user", unit))


def _user_timer_actions(declared: Dict[str, str]) -> List[Action]:
    """Observe-only actions for declared ``systemd --user`` timers.

    Emits ONLY ``noop`` and ``warn`` — never ``enable``/``disable`` — so
    ``--apply`` cannot start or stop a user unit. That restriction is the whole
    safety argument: converge is a sweep, and a sweep that can start units is
    how the 2026-07-24 incident happened. A human decides whether the box or
    the declaration is wrong; this only makes the disagreement visible.

    A required ``warn`` is what ``probe_role_drift`` counts as drift, so
    "declared enabled, actually disabled" now pages the same way a system-unit
    divergence does — the case that was previously indistinguishable from
    "this box never ran it".

    ⚠️ THREE states, not two (2026-09-09, finding 5). Treating "declared
    enabled, not enrolled" as one thing made every FRESH full-gateway /
    gateway-only provision exit 1 in both dry-run and ``--apply``, with a
    remediation nothing could execute: no installer copies the soak user
    units, and ``--apply`` deliberately never touches user scope. The Gateway
    Wizard chains ``--set-role`` + ``--apply`` and failed a newcomer's first
    provision on it, while ``probe_role_drift`` paged "converge with
    provision_role --apply" — advice that could not work. So:

      * unit body ABSENT  → never enrolled here. ADVISORY (not drift), naming
        the enrollment command that now exists (``--enroll-user-timers``).
        The declared state is ACHIEVABLE, which is what makes the advisory
        honest rather than a silenced warning.
      * unit body PRESENT, not enabled → enrolled once, then switched off.
        REQUIRED — this is the drift the declaration was written for.

    Unobservable enrollment (no resolvable operator, or a wants dir that
    exists but cannot be read) is a NON-required advisory: unknown is not
    drift, and must never read as "not enabled" (honest_failure_modes #1).
    """
    out: List[Action] = []
    for unit, desired in sorted(declared.items()):
        if desired not in VALID_UNIT_STATES:
            out.append(Action(unit, "?", str(desired), "warn", required=False,
                              detail=f"unknown desired state '{desired}'"))
            continue
        enrolled = user_timer_enrolled(unit)
        if enrolled is None:
            out.append(Action(unit, "unobservable", str(desired), "warn",
                              required=False,
                              detail="user-timer enrollment unreadable "
                                     "(no operator resolved, or wants dir "
                                     "unreadable) — not judged"))
            continue
        cur = "enabled" if enrolled else "not-enabled"
        # 'absent' and 'disabled' are both satisfied by "not enrolled": this
        # layer reads the ENABLE symlink, which cannot distinguish an
        # uninstalled unit from an installed-but-disabled one. Declaring the
        # difference is still worth it — `absent` documents intent for the
        # next reader even where the check cannot separate them.
        want_enrolled = (desired == "enabled")
        if enrolled == want_enrolled:
            out.append(Action(unit, cur, str(desired), "noop"))
            continue
        if want_enrolled:
            body = _user_timer_unit_installed(unit)
            if body is False:
                out.append(Action(unit, "not-installed", str(desired), "warn",
                                  required=False,
                                  detail="declared, but this box has never "
                                         "enrolled it (no unit file in "
                                         "~/.config/systemd/user) — no "
                                         "installer copies user units. "
                                         f"Enroll: {ENROLL_CMD} "
                                         "(as the operator, no sudo)"))
                continue
            if body is None:
                out.append(Action(unit, cur, str(desired), "warn",
                                  required=False,
                                  detail="declared enabled but the operator's "
                                         "user-unit dir is unreadable — not "
                                         "judged (unknown is not drift)"))
                continue
            out.append(Action(unit, cur, str(desired), "warn", required=True,
                              detail="systemd --user timer is INSTALLED here "
                                     "but not enabled — an exerciser that was "
                                     "enrolled and then switched off is drift "
                                     f"(--apply never touches user units). "
                                     f"Re-enable: systemctl --user enable "
                                     f"--now {unit}"))
            continue
        out.append(Action(unit, cur, str(desired), "warn", required=True,
                          detail="systemd --user timer is enabled here but the "
                                 "role declares it "
                                 f"'{desired}' — unlisted load is drift too "
                                 f"(--apply never touches user units). "
                                 f"Disable: systemctl --user disable --now "
                                 f"{unit}"))
    return out


# --------------------------------------------------------------------------
# User-timer enrollment (explicit, operator-run — never part of --apply)
# --------------------------------------------------------------------------

def _user_systemctl(argv: List[str], timeout: int = 30) -> "tuple[bool, str]":
    """Run one ``systemctl --user`` verb as the CURRENT (operator) user.

    ⚠️ Deliberately NOT routed through ``utils.service_check`` (MF008's SSOT):
    that module speaks to the SYSTEM manager, and user units are structurally
    outside its scope — ``systemctl --user`` from root addresses root's own,
    usually absent, session bus (Issue #82, and the whole reason
    ``utils.user_units`` reads the filesystem instead of asking a bus). The
    caller refuses to run as root, so this always speaks to the operator's own
    manager.
    """
    import subprocess
    cmd = ["systemctl", "--user"] + list(argv)
    env = dict(os.environ)
    # An operator shell normally has this; a non-login context (cron, ssh
    # command) may not, and without it the user manager is unreachable.
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{' '.join(cmd)}: {e}"
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()


def user_timer_template_pair(unit: str) -> "tuple[Path, Path]":
    """The shipped ``-user.timer`` / ``-user.service`` bodies for ``unit``."""
    stem = unit[:-len(".timer")] if unit.endswith(".timer") else unit
    return (USER_UNIT_TEMPLATE_DIR / f"{stem}-user.timer",
            USER_UNIT_TEMPLATE_DIR / f"{stem}-user.service")


def enroll_user_timers(declared: Dict[str, str],
                       home: Optional[str] = None,
                       runner=None) -> "tuple[bool, List[str]]":
    """Install + enable the user timers a role declares ``enabled``.

    Returns ``(ok, report_lines)``. Copies the shipped template bodies into
    the operator's ``~/.config/systemd/user`` and enables each timer on the
    operator's own bus.

    Scope, deliberately narrow (the 2026-07-24 lesson — a sweep that starts
    units is how a map came up on a box that had it off by design):

      * enables ONLY units this role declares ``enabled``;
      * never disables, stops, or removes anything — a timer declared
        ``absent`` but enrolled stays a REQUIRED warn for a human;
      * is NOT reachable from ``--apply``. Convergence stays user-unit-free;
        this is an explicit, operator-typed command.

    ``runner`` is injectable so the copy path can be drilled for real against
    a scratch home without touching any bus.
    """
    import shutil
    run = runner or _user_systemctl
    lines: List[str] = []
    ok = True
    if home is None:
        home = resolve_operator_home()
    if home is None:
        return False, ["ERROR: no operator user resolved — cannot locate the "
                       "user-unit directory (see utils.user_units)"]

    dest_dir = Path(home) / ".config" / "systemd" / "user"
    wanted = [u for u, s in sorted(declared.items()) if s == "enabled"]
    if not wanted:
        return True, ["# no user timers declared enabled for this role — nothing to enroll"]

    to_enable: List[str] = []
    for unit in wanted:
        if user_timer_enrolled(unit, home):
            lines.append(f"[PASS       ] {unit}: already enrolled")
            continue
        timer_tmpl, svc_tmpl = user_timer_template_pair(unit)
        missing = [str(p) for p in (timer_tmpl, svc_tmpl) if not p.is_file()]
        if missing:
            ok = False
            lines.append(f"[FAIL       ] {unit}: no shipped template body "
                         f"({', '.join(missing)}) — this role declares a timer "
                         f"the repo does not carry; fix the declaration or add "
                         f"the template")
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(timer_tmpl, dest_dir / unit)
            shutil.copyfile(svc_tmpl, dest_dir / svc_tmpl.name.replace(
                "-user.service", ".service"))
        except OSError as e:
            ok = False
            lines.append(f"[FAIL       ] {unit}: copy failed: {e}")
            continue
        lines.append(f"[CHANGE     ] {unit}: unit body installed in {dest_dir}")
        to_enable.append(unit)

    if not to_enable:
        return ok, lines

    ok_reload, msg = run(["daemon-reload"])
    if not ok_reload:
        # The bodies ARE installed; only the bus step failed. Say exactly that
        # — "copied but not enabled" is a different state from "did nothing".
        lines.append(f"[FAIL       ] daemon-reload: {msg}")
        lines.append(f"[FAIL       ] unit bodies are installed but NOT enabled "
                     f"— finish with: systemctl --user daemon-reload && "
                     f"systemctl --user enable --now {' '.join(to_enable)}")
        return False, lines
    for unit in to_enable:
        ok_en, msg = run(["enable", "--now", unit])
        if ok_en:
            lines.append(f"[CHANGE     ] {unit}: enabled --now")
        else:
            ok = False
            lines.append(f"[FAIL       ] {unit}: enable failed: {msg}")
    return ok, lines


def plan(role_def: dict, overrides: Optional[Dict[str, dict]] = None) -> List[Action]:
    """Build the ordered action list to converge to `role_def`. Pure w.r.t.
    the SSOT observe functions (which read the live system).

    `overrides` is the box's instance-local `service_overrides` (from
    deployment.json): per-unit intentional exceptions to the role's service
    map. A waived unit is reported as a NON-blocking advisory carrying the
    reason — visible and auditable, never silently dropped — and is skipped by
    convergence (left as the operator set it). A waiver WITHOUT a `reason`, or
    with a missing/misspelled `state`, is NOT honored (it stays a blocking
    warning) — an unexplained exception is just hidden drift, and one nothing
    can check against the live unit is an unverifiable claim. See
    `_override_action` / `_honored_override_state`; the rnsd masking invariant
    below reads the same EFFECTIVE state rather than the role's line.
    """
    actions: List[Action] = []
    services: Dict[str, str] = role_def.get("services", {})
    overrides = overrides or {}

    for unit, desired in services.items():
        ov = overrides.get(unit)
        if ov is not None:
            actions.append(_override_action(unit, ov))
            continue
        if desired not in VALID_UNIT_STATES:
            actions.append(Action(unit, "?", str(desired), "warn", required=False,
                                  detail=f"unknown desired state '{desired}'"))
            continue
        running, enabled = check_systemd_service(unit)
        installed = is_service_unit_installed(unit) or is_service_masked(unit)
        cur = _unit_current(unit)

        if desired == "enabled":
            if not installed:
                actions.append(Action(unit, "absent", "enabled", "warn",
                                      detail=f"required unit not installed — run install_noc.sh"))
            elif running and enabled:
                actions.append(Action(unit, cur, "enabled", "noop"))
            else:
                actions.append(Action(unit, cur, "enabled", "enable"))
        elif desired == "disabled":
            if not installed:
                actions.append(Action(unit, "absent", "disabled", "noop"))
            elif running or enabled:
                actions.append(Action(unit, cur, "disabled", "disable"))
            else:
                actions.append(Action(unit, cur, "disabled", "noop"))
        elif desired == "absent":
            if installed:
                actions.append(Action(unit, cur, "absent", "warn", required=False,
                                      detail="present but role declares absent (not auto-removed)"))
            else:
                actions.append(Action(unit, "absent", "absent", "noop"))

    actions.extend(_user_timer_actions(role_def.get("user_timers", {})))

    # Masking invariant: this box owns rnsd → mask any installed rival RNS host.
    #
    # ⚠️ The predicate is "does THIS BOX own rnsd", and that is the role's
    # declaration AS MODIFIED by an honored deployment.json override — not the
    # role's line read alone (2026-09-09, finding 4). Reading `services` here
    # meant a reasoned `rnsd: {state: disabled, reason: "meshanchor-daemon owns
    # @rns here"}` still planned mask:meshanchor-daemon, and `--apply` (CLI and
    # the TUI core step) then masked the very RNS host the waiver named as
    # owner — Issue #69 in reverse, executed by the converge. In dry-run the
    # same line paged role_drift forever over an honored exception.
    rnsd_ov = overrides.get("rnsd")
    if rnsd_ov is None:
        rnsd_effective, dispute = services.get("rnsd"), ""
    else:
        rnsd_effective = _honored_override_state(rnsd_ov)
        dispute = "" if rnsd_effective else (
            "a service_override for rnsd that is NOT honored (no reason, or a "
            "missing/misspelled state)")

    if rnsd_effective == "enabled":
        for rival in KNOWN_RNS_RIVALS:
            if is_service_masked(rival):
                actions.append(Action(f"mask:{rival}", "masked", "masked", "noop"))
            elif is_service_unit_installed(rival):
                actions.append(Action(f"mask:{rival}", "present", "masked", "mask",
                                      detail="rival RNS host on an rnsd box — Issue #69 invariant"))
    elif services.get("rnsd") == "enabled":
        # The role says we own rnsd, the box says otherwise. Masking is
        # destructive and irreversible-by-sweep, so the ambiguous case does
        # NOTHING and says why — never "resolve it by taking out the listener".
        why = dispute or (f"a reasoned service_override declares rnsd "
                          f"'{rnsd_effective}' on this box")
        for rival in KNOWN_RNS_RIVALS:
            if is_service_unit_installed(rival) and not is_service_masked(rival):
                actions.append(Action(
                    f"mask:{rival}", "present", "left as-is", "warn",
                    required=False,
                    detail=(f"masking SKIPPED — the role declares rnsd enabled "
                            f"but {why}. This box may not own the RNS "
                            f"listener, and masking {rival} would take out the "
                            f"#69 owner. Resolve the override, then re-run")))

    # Config deltas (bbox/cap/caches) are asserted against real state in
    # config_delta_actions() (appended in main, parallel to foundation_actions) —
    # they are cross-cutting map `defaults`, not per-role unit state.
    if role_def.get("singleton"):
        actions.append(Action("invariant:singleton", "?", "unique-in-fleet", "warn",
                              required=False,
                              detail="this role must be unique across the fleet — verify no other box claims it"))
    return actions


def foundation_actions() -> List[Action]:
    """Cross-cutting permission-foundation converge step (D1/D3, mf.4/#73).

    Role-independent: EVERY MeshForge box runs its services as the non-root
    operator user and must own the data it writes plus its RNS config tree, so
    this is appended to every converge regardless of role. Drift → one
    `foundation` action that, on --apply, runs the shared
    `fleet_foundation.apply_foundation` (data-root chowns + the rns_tree_perms
    RNS-tree apply-path — the single apply-path). Clean → noop. The recurrence
    this continuously catches: a re-provision that recreates /etc/reticulum
    root:root while rnsd runs non-root (the moc1/moc2 mf.4 trigger, 2026-06-01).
    """
    try:
        from utils.fleet_foundation import audit_foundation
        drift = audit_foundation()
    except Exception as e:  # never let the foundation probe sink the converge
        return [Action("foundation:perms", "?", "operator-owned", "warn",
                       required=False, detail=f"foundation audit skipped: {e}")]
    if not drift:
        return [Action("foundation:perms", "operator-owned", "operator-owned", "noop")]
    detail = "; ".join(drift)
    if len(detail) > 300:
        detail = detail[:297] + "..."
    return [Action("foundation:perms", f"{len(drift)} drift item(s)",
                   "operator-owned", "foundation", required=True, detail=detail)]


def _has_geo_anchor() -> "tuple[bool, str]":
    """Does this box have a geographic anchor for the directory bbox filter?

    The filter is effective only with EITHER an explicit
    ``map_settings.external_bulk_bbox`` OR an ``operator_position.json`` (the bbox
    auto-derives from the latter; no anchor => the directory serves the unfiltered
    firehose). Read straight from the real user's config (MF001) — no
    SettingsManager so a sudo invocation can't read root's home by mistake.
    """
    cfg = get_real_user_home() / ".config" / "meshforge"
    if (cfg / "operator_position.json").exists():
        return True, "operator_position.json"
    ms = cfg / "map_settings.json"
    try:
        if ms.exists() and json.loads(ms.read_text()).get("external_bulk_bbox"):
            return True, "external_bulk_bbox"
    except (json.JSONDecodeError, OSError):
        pass
    return False, "none"


def config_delta_actions(role_def: dict, defaults: dict) -> List[Action]:
    """Assert the cross-cutting map `defaults` (fleet_roles.yaml) against real
    state — MAP-running roles only. Parallel to foundation_actions().

    GROUND-TRUTH (2026-06-08): the declared map defaults are NOT operator-settable
    config; they are baked into code (the "software fits the box" invariant) or
    deployment-specific:
      - response_caches  : instantiated unconditionally in MapDataCollector (#70/#71)
      - node_cap         : the DEFAULT_DIRECTORY_MAX_ROWS code constant (#49/#50)
      - bbox_filter      : effective only with a geo-anchor (operator position) — deployment-specific
    So this converges by ASSERTION not mutation: read the real values, flag genuine
    drift (a code constant that no longer matches the declared default — a
    reproducibility hazard) or a missing deployment input (no anchor => bbox off =>
    unfiltered directory). The one force-settable delta (meshtasticd mqtt.root #77)
    is a radio-touching enforcement deferred to its own slice.
    """
    actions: List[Action] = []
    if role_def.get("services", {}).get("meshforge-map") != "enabled":
        return actions
    nd = (defaults or {}).get("node_directory", {})

    # node_cap: code constant vs declared default — catches code<->yaml drift.
    declared_cap = nd.get("node_cap")
    if declared_cap is not None:
        try:
            from utils.node_history import DEFAULT_DIRECTORY_MAX_ROWS as code_cap
        except Exception as e:
            actions.append(Action("delta:node-cap", "?", str(declared_cap), "warn",
                                  required=False, detail=f"node_history read failed: {e}"))
        else:
            verb = "noop" if code_cap == declared_cap else "warn"
            detail = "" if verb == "noop" else (
                "code DEFAULT_DIRECTORY_MAX_ROWS drifted from the declared node_cap — "
                "reconcile node_history.py and fleet_roles.yaml")
            actions.append(Action("delta:node-cap", str(code_cap), str(declared_cap),
                                  verb, required=False, detail=detail))

    # response_caches: code-always-on; assert the layer is importable.
    if (defaults or {}).get("response_caches"):
        try:
            from utils._response_byte_cache import ResponseByteCache  # noqa: F401
            actions.append(Action("delta:response-caches", "code-always-on",
                                  "directory+geojson+topology", "noop",
                                  detail="unconditional in MapDataCollector (#70/#71)"))
        except Exception as e:
            actions.append(Action("delta:response-caches", "?", "present", "warn",
                                  required=False, detail=f"ResponseByteCache import failed: {e}"))

    # bbox_filter effectiveness: needs a geo-anchor (deployment-specific).
    if nd.get("bbox_filter") and nd.get("operator_position_required"):
        anchored, how = _has_geo_anchor()
        if anchored:
            actions.append(Action("delta:bbox-anchor", how, "anchored", "noop"))
        else:
            actions.append(Action("delta:bbox-anchor", "none", "anchored", "warn",
                                  required=False,
                                  detail="no operator_position.json and no map_settings."
                                         "external_bulk_bbox — bbox_filter is OFF, the "
                                         "directory serves the unfiltered firehose"))
    return actions


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def apply_action(a: Action) -> bool:
    """Execute one action via the SSOT. Returns success. 'warn'/'noop' never act."""
    if a.verb in ("noop", "warn"):
        a.result = "skipped" if a.verb == "warn" else "ok"
        return True
    if a.verb == "enable":
        ok, msg = enable_service(a.item, start=True)
    elif a.verb == "disable":
        ok1, m1 = stop_service(a.item)
        ok2, m2 = disable_service(a.item)
        ok, msg = (ok1 and ok2), f"{m1}; {m2}"
    elif a.verb == "mask":
        ok, msg = mask_service(a.item.split("mask:", 1)[1])
    elif a.verb == "foundation":
        try:
            from utils.fleet_foundation import apply_foundation
            executed = apply_foundation()
            ok, msg = True, f"applied {len(executed)} foundation step(s)"
        except Exception as e:
            ok, msg = False, f"foundation apply failed: {e}"
    else:
        ok, msg = False, f"unknown verb {a.verb}"
    a.result = msg
    return ok


# --------------------------------------------------------------------------
# deployment.json role
# --------------------------------------------------------------------------

def read_role() -> Optional[str]:
    if not DEPLOYMENT_JSON.exists():
        return None
    try:
        return json.loads(DEPLOYMENT_JSON.read_text()).get("role")
    except (json.JSONDecodeError, OSError):
        return None


def read_overrides() -> Dict[str, dict]:
    """Instance-local per-unit exceptions from deployment.json `service_overrides`.

    Shape: ``{"<unit>": {"state": "disabled"|"absent"|..., "reason": "<why>"}}``.
    Instance specifics (which box, why) live HERE, never in the committed roles
    file (MF014/MF015). Honored by ``plan()``; a waiver without a ``reason`` is
    rejected there.
    """
    if not DEPLOYMENT_JSON.exists():
        return {}
    try:
        ov = json.loads(DEPLOYMENT_JSON.read_text()).get("service_overrides") or {}
    except (json.JSONDecodeError, OSError):
        return {}
    return ov if isinstance(ov, dict) else {}


def write_role(role: str) -> None:
    """Merge the role into deployment.json — never clobber other keys.

    An existing-but-unreadable file is a refuse-loud error: silently
    resetting it would destroy ``service_overrides`` and the deployment
    profile (a torn file after a power event is this fleet's known
    class, and the old non-atomic write here was itself a torn-file
    source). Atomic write + ownership fixed back to the operator when
    invoked under sudo (the TUI apply path is root; a root-created
    deployment.json breaks every later user-mode writer, MF001 class).
    """
    from utils.paths import atomic_write_text
    DEPLOYMENT_JSON.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if DEPLOYMENT_JSON.exists():
        try:
            data = json.loads(DEPLOYMENT_JSON.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(
                f"{DEPLOYMENT_JSON} exists but is unreadable ({e}) — "
                f"refusing to overwrite it: that would silently destroy "
                f"service_overrides and the deployment profile. Inspect "
                f"or remove the file, then retry.") from e
        if not isinstance(data, dict):
            raise RuntimeError(
                f"{DEPLOYMENT_JSON} is not a JSON object "
                f"({type(data).__name__}) — refusing to overwrite; "
                f"inspect the file.")
    data["role"] = role
    atomic_write_text(DEPLOYMENT_JSON, json.dumps(data, indent=2))
    from utils.paths import chown_to_operator
    chown_to_operator(DEPLOYMENT_JSON.parent, DEPLOYMENT_JSON)


# --------------------------------------------------------------------------
# Fleet-aware: singleton enforcement across fleet_hosts (v2)
# --------------------------------------------------------------------------

def parse_fleet_hosts(path: Path) -> List[str]:
    """Host list from a fleet_hosts file — parsing delegates to
    ``utils.fleet_hosts`` (THE resolver; this was one of ~13 independent
    chain copies, converged 2026-07-29). [] on a missing/unreadable file."""
    from utils.fleet_hosts import parse_fleet_hosts_text
    try:
        return parse_fleet_hosts_text(path.read_text(encoding="utf-8",
                                                     errors="replace"))
    except OSError:
        return []


def validate_fleet(catalog: dict, role_map: Dict[str, Optional[str]]) -> List[str]:
    """Pure check of a {host: role} assignment against the catalog.

    Flags: a `singleton: true` role claimed by more than one host; a role name
    not in the catalog. Hosts with no role (None) are reported by the caller,
    not treated as a violation here. Returns a list of human-readable violations
    (empty == valid).
    """
    roles = catalog.get("roles", {})
    violations: List[str] = []

    # Unknown role names
    for host, role in role_map.items():
        if role and role not in roles:
            violations.append(f"{host}: unknown role '{role}' (not in catalog)")

    # Singleton uniqueness
    for rname, rdef in roles.items():
        if not rdef.get("singleton"):
            continue
        claimants = [h for h, r in role_map.items() if r == rname]
        if len(claimants) > 1:
            violations.append(
                f"singleton role '{rname}' claimed by {len(claimants)} hosts: "
                f"{', '.join(sorted(claimants))} (must be exactly one)"
            )
    return violations


def gather_fleet_roles(
    hosts: List[str], self_role: Optional[str], ssh_cmd: str = SSH_CMD
) -> Dict[str, Optional[str]]:
    """Collect {host: role} for the fleet. Self comes from the local role; each
    peer is queried with ``<ssh_cmd> <host> python3 <repo>/scripts/provision_role.py
    --print-role``. Unreachable/role-less peers map to None.
    """
    import subprocess
    role_map: Dict[str, Optional[str]] = {"(self)": self_role}
    remote = "python3 /opt/meshforge/scripts/provision_role.py --print-role"
    for host in hosts:
        argv = ssh_cmd.split() + [host, remote]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            role_map[host] = (r.stdout.strip() or None) if r.returncode == 0 else None
        except (subprocess.SubprocessError, OSError):
            role_map[host] = None
    return role_map


# --------------------------------------------------------------------------
# Render + main
# --------------------------------------------------------------------------

_SYM = {"noop": "PASS", "enable": "CHANGE", "disable": "CHANGE", "mask": "CHANGE",
        "foundation": "CHANGE", "warn": "WARN"}


def render(actions: List[Action], apply: bool) -> None:
    for a in actions:
        tag = _SYM.get(a.verb, a.verb.upper())
        if not apply and a.verb not in ("noop", "warn"):
            tag = "WOULD-" + tag
        line = f"[{tag:11}] {a.item}: {a.current} -> {a.desired}"
        if a.detail:
            line += f"  ({a.detail})"
        if apply and a.result and a.verb not in ("noop", "warn"):
            line += f"  => {a.result}"
        print(line)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Converge this box to its fleet role.")
    p.add_argument("--apply", action="store_true", help="execute changes (default: dry-run)")
    p.add_argument("--role", help="override role (else read from deployment.json)")
    p.add_argument("--roles-file", type=Path, default=DEFAULT_ROLES_FILE)
    p.add_argument("--set-role", help="write role into deployment.json and exit")
    p.add_argument("--print-role", action="store_true",
                   help="print this box's assigned role and exit (machine-readable)")
    p.add_argument("--print-unit-state", metavar="UNIT",
                   help="print the effective desired state of one unit for this "
                        "box's role (enabled|disabled|absent|unspecified, or "
                        "waived:<state> for a reasoned deployment.json override) "
                        "and exit (machine-readable; combine with --role)")
    p.add_argument("--enroll-user-timers", action="store_true",
                   help="install + enable the `systemd --user` timers this "
                        "box's role declares enabled (run AS THE OPERATOR, no "
                        "sudo). Never part of --apply, and never disables "
                        "anything.")
    p.add_argument("--fleet-check", action="store_true",
                   help="gather roles across fleet_hosts and validate singleton invariants")
    args = p.parse_args(argv)

    if args.print_role:
        print(read_role() or "")
        return 0

    if args.set_role:
        # write_role now REFUSES (raises) on a torn/unreadable deployment.json
        # rather than clobbering it — surface that as the codebase's clean
        # "could not proceed" exit 2, not an uncaught traceback.
        try:
            write_role(args.set_role)
        except (RuntimeError, OSError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(f"role set to '{args.set_role}' in {DEPLOYMENT_JSON}")
        return 0

    try:
        catalog = load_roles(args.roles_file)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"ERROR loading {args.roles_file}: {e}", file=sys.stderr)
        return 2

    if args.fleet_check:
        from utils.fleet_hosts import resolve_fleet_hosts_file
        _hosts_file = resolve_fleet_hosts_file()
        hosts = parse_fleet_hosts(_hosts_file) if _hosts_file else []
        role_map = gather_fleet_roles(hosts, read_role())
        print("# fleet role assignment")
        for host, role in role_map.items():
            print(f"  {host:24} {role or '(unset/unreachable)'}")
        violations = validate_fleet(catalog, role_map)
        if violations:
            print("# VIOLATIONS:")
            for v in violations:
                print(f"  ! {v}")
            return 1
        print("# fleet invariants OK (singletons unique, roles known)")
        return 0

    role = args.role or read_role()
    if not role:
        print("ERROR: no role. Set one with --set-role <name> or pass --role <name>.",
              file=sys.stderr)
        print(f"  available: {', '.join(catalog['roles'])}", file=sys.stderr)
        return 2

    try:
        role_def = resolve_role(catalog, role)
    except KeyError:
        print(f"ERROR: unknown role '{role}'. available: {', '.join(catalog['roles'])}",
              file=sys.stderr)
        return 2

    if args.print_unit_state:
        # Machine-readable single token for other tooling (e.g. the Gateway
        # Wizard gates its service-install step on this so it can't install a
        # unit the box's role declares disabled/absent). Effective state =
        # role's service map after inheritance, with a REASONED
        # deployment.json override surfaced as waived:<state> (an unexplained
        # override is not honored, matching plan()).
        #
        # It must agree with plan() or it is worse than nothing (2026-09-09,
        # finding 14): it read `services` ONLY, so every unit the role declares
        # under `user_timers` printed `unspecified` — "no declaration" — for a
        # unit the same role's plan renders as declared. Tooling that gates a
        # timer install on this token was told there was nothing to enroll.
        # `service_overrides` are SERVICE overrides and plan() does not apply
        # them to user timers, so neither does this; and an override that
        # plan() refuses to honor (no reason, or an invalid state) must not
        # print as `waived:` here either.
        unit = args.print_unit_state
        services_decl = role_def.get("services", {})
        timers_decl = role_def.get("user_timers", {})
        honored = _honored_override_state(read_overrides().get(unit))
        if honored and unit not in timers_decl:
            print(f"waived:{honored}")
        else:
            print(services_decl.get(unit, timers_decl.get(unit, "unspecified")))
        return 0

    if args.enroll_user_timers:
        # Explicit, operator-typed, and NOT reachable from --apply: converge
        # stays user-unit-free (the 2026-07-24 lesson) while the declared
        # state stops being unachievable (finding 5).
        if os.geteuid() == 0:
            print("ERROR: --enroll-user-timers must run AS THE OPERATOR, not "
                  "under sudo — `systemctl --user` from root addresses root's "
                  "own (absent) session bus, not the operator's (#82). Re-run "
                  "without sudo.", file=sys.stderr)
            return 2
        ok, lines = enroll_user_timers(role_def.get("user_timers", {}))
        for line in lines:
            print(line)
        return 0 if ok else 1

    if role_def.get("provisioned_by"):
        print(f"role '{role}' is EXTERNAL (provisioned_by: {role_def['provisioned_by']}) "
              f"— the MeshForge provisioner does not converge it.", file=sys.stderr)
        return 2

    overrides = read_overrides()
    print(f"# role: {role}  (mode: {'APPLY' if args.apply else 'dry-run'})")
    if overrides:
        print(f"# service_overrides active: {', '.join(sorted(overrides))}")
    actions = plan(role_def, overrides)
    # Cross-cutting permission foundation (D1/D3) — appended to every converge,
    # role-independent. Continuously enforces the born-correct perms (mf.4/#73).
    actions += foundation_actions()
    # Cross-cutting map `defaults` (bbox/cap/caches) — asserted against real
    # state for MAP-running roles (code-baked / deployment-specific, not mutated).
    actions += config_delta_actions(role_def, catalog.get("defaults", {}))
    render(actions, args.apply)

    changes = [a for a in actions
               if a.verb in PLAN_CHANGE_VERBS + ("foundation",)]
    fail_warns = [a for a in actions if a.verb == "warn" and a.required]

    if args.apply:
        failed = []
        for a in changes:
            if not apply_action(a):
                failed.append(a)
        # re-render results
        if changes:
            print("# --- results ---")
            render(changes, apply=True)
        n_fail = len(failed) + len(fail_warns)
        print(f"# summary: {len(changes)} change(s), {len(failed)} failed, "
              f"{len(fail_warns)} blocking warning(s)")
        return 1 if n_fail else 0

    # dry-run
    print(f"# summary: {len(changes)} would-change, {len(fail_warns)} blocking warning(s), "
          f"{sum(1 for a in actions if a.verb=='warn' and not a.required)} advisory")
    return 1 if (changes or fail_warns) else 0


if __name__ == "__main__":
    sys.exit(main())
