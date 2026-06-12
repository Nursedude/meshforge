"""Regression guards for the mini-dudeai DEPLOY path (Defect #1, 2026-06-09).

The mini-dudeai daemon runs as a ``systemctl --user`` unit whose template
names (``meshforge-mini-dudeai.service`` + the ``-dream`` service/timer) do
NOT end in ``-user.service``. Before this fix:

  - ``update.sh`` only deployed ``*-user.service`` templates AND never
    restarted any user unit, so a ``git pull`` left the daemon on OLD code.
  - ``fleet_sync.sh`` sudo-restarted only the 3 SYSTEM units (gateway / map /
    maps) and had zero mini references.
  - ``install_noc.sh`` never enrolled the mini user units, so a fresh box
    never got them.

These script-level contract tests pin the deploy wiring so a future refactor
can't silently re-open the gap. They mirror the shape of
``test_install_meshchatx.py`` — grep the script text, don't run the installer
(that needs sudo + a live user bus, which belong in field validation).

Run: python3 -m pytest tests/test_mini_dudeai_deploy.py -v
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UPDATE_SH = REPO_ROOT / "scripts" / "update.sh"
FLEET_SYNC_SH = REPO_ROOT / "scripts" / "fleet_sync.sh"
INSTALL_NOC_SH = REPO_ROOT / "scripts" / "install_noc.sh"
TEMPLATE_DIR = REPO_ROOT / "templates" / "systemd"

MINI_SERVICE = "meshforge-mini-dudeai.service"
MINI_DREAM_SERVICE = "meshforge-mini-dudeai-dream.service"
MINI_DREAM_TIMER = "meshforge-mini-dudeai-dream.timer"


# ─────────────────────────────────────────────────────────────────
# (a) The templates the deploy scripts copy must exist + parse.
# ─────────────────────────────────────────────────────────────────


class TestMiniTemplatesExist:
    def test_service_template_exists(self):
        assert (TEMPLATE_DIR / MINI_SERVICE).is_file()

    def test_dream_service_template_exists(self):
        assert (TEMPLATE_DIR / MINI_DREAM_SERVICE).is_file()

    def test_dream_timer_template_exists(self):
        assert (TEMPLATE_DIR / MINI_DREAM_TIMER).is_file()

    def test_service_template_not_user_suffixed(self):
        # The whole reason the old loops missed them: these unit names do NOT
        # end in -user.service. If they ever DID, the *-user.service loop
        # would catch them and the explicit enumeration below would be dead.
        # This pins the assumption the fix is built on.
        for name in (MINI_SERVICE, MINI_DREAM_SERVICE):
            assert not name.endswith("-user.service"), (
                f"{name} now ends in -user.service — revisit the explicit "
                f"enumeration in the deploy scripts"
            )


# ─────────────────────────────────────────────────────────────────
# (b) Bash-syntax cleanliness — the scripts must still parse.
# ─────────────────────────────────────────────────────────────────


class TestBashSyntaxClean:
    def _check(self, path):
        proc = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, (
            f"bash -n {path.name} failed:\nstderr:\n{proc.stderr}"
        )

    def test_update_sh_syntax(self):
        self._check(UPDATE_SH)

    def test_fleet_sync_sh_syntax(self):
        self._check(FLEET_SYNC_SH)

    def test_install_noc_sh_syntax(self):
        self._check(INSTALL_NOC_SH)


# ─────────────────────────────────────────────────────────────────
# (c) update.sh: copies mini units (sans -user suffix) AND restarts on the
#     user bus.
# ─────────────────────────────────────────────────────────────────


class TestUpdateShDeploysMini:
    def test_copies_mini_service_explicitly(self):
        text = UPDATE_SH.read_text()
        # The *-user.service loop won't catch it, so the script must name it.
        assert MINI_SERVICE in text, (
            "update.sh must explicitly copy the mini service template "
            "(it lacks the -user.service suffix the existing loop matches)"
        )

    def test_copies_dream_units_explicitly(self):
        text = UPDATE_SH.read_text()
        assert MINI_DREAM_SERVICE in text
        assert MINI_DREAM_TIMER in text

    def test_restarts_mini_on_user_bus(self):
        text = UPDATE_SH.read_text()
        # The defect: update.sh never restarted any user unit. The fix must
        # try-restart the mini daemon on the operator's user bus.
        assert "systemctl --user" in text, (
            "update.sh must restart user units on the user bus"
        )
        assert re.search(
            r"try-restart\s+meshforge-mini-dudeai\.service", text
        ), "update.sh must try-restart the mini daemon after a code pull"

    def test_uses_xdg_runtime_dir_bridge(self):
        # Restarting a user unit from a sudo/root context needs the documented
        # XDG_RUNTIME_DIR bridge (Issue #45), not a bare systemctl --user.
        text = UPDATE_SH.read_text()
        assert "XDG_RUNTIME_DIR=/run/user/" in text


# ─────────────────────────────────────────────────────────────────
# (d) fleet_sync.sh: restarts the mini daemon on BOTH the remote fleet and
#     this box, on the user bus.
# ─────────────────────────────────────────────────────────────────


class TestFleetSyncRestartsMini:
    def test_references_mini_unit(self):
        text = FLEET_SYNC_SH.read_text()
        assert "meshforge-mini-dudeai" in text, (
            "fleet_sync.sh had zero mini references — the deploy gap"
        )

    def test_remote_user_unit_sync_helper(self):
        text = FLEET_SYNC_SH.read_text()
        # Remote (SSH'd) fleet boxes get a user-bus sync helper.
        assert "sync_user_unit" in text
        assert re.search(
            r"sync_user_unit\s+meshforge-mini-dudeai", text
        ), "fleet_sync.sh must run sync_user_unit for the mini daemon (remote)"

    def test_local_user_unit_sync_helper(self):
        text = FLEET_SYNC_SH.read_text()
        # This box (run AS the operator) gets its own user-bus self-sync.
        assert "sync_local_user_unit" in text
        assert re.search(
            r"sync_local_user_unit\s+meshforge-mini-dudeai", text
        ), "fleet_sync.sh must self-sync the mini daemon on this box"

    def test_user_bus_restart_present(self):
        text = FLEET_SYNC_SH.read_text()
        assert "systemctl --user" in text

    def test_honors_disabled_units_via_try_restart(self):
        # The mini remote sync must use try-restart so an operator-disabled
        # daemon stays stopped — same invariant the system-unit path keeps.
        text = FLEET_SYNC_SH.read_text()
        assert "try-restart" in text

    def test_system_unit_restarts_preserved(self):
        # Don't break the existing system-unit restart paths.
        text = FLEET_SYNC_SH.read_text()
        for unit in ("meshforge-gateway", "meshforge-map", "meshforge-maps"):
            assert unit in text, f"{unit} system-unit sync regressed"


# ─────────────────────────────────────────────────────────────────
# (e) install_noc.sh: enrolls the mini user units on a fresh box.
# ─────────────────────────────────────────────────────────────────


class TestInstallNocEnrollsMini:
    def test_copies_all_three_mini_units(self):
        text = INSTALL_NOC_SH.read_text()
        assert MINI_SERVICE in text
        assert MINI_DREAM_SERVICE in text
        assert MINI_DREAM_TIMER in text

    def test_enables_service_and_dream_timer(self):
        text = INSTALL_NOC_SH.read_text()
        # enable --now the daemon and the nightly dream TIMER (not the dream
        # service — the timer pulls it).
        assert re.search(
            r"enable --now meshforge-mini-dudeai\.service", text
        ), "install_noc.sh must enable --now the mini daemon"
        assert re.search(
            r"enable --now meshforge-mini-dudeai-dream\.timer", text
        ), "install_noc.sh must enable --now the nightly dream timer"

    def test_daemon_reload_on_user_bus(self):
        text = INSTALL_NOC_SH.read_text()
        assert "systemctl --user" in text
        assert "daemon-reload" in text

    def test_enables_linger_for_headless_24x7(self):
        # A 24/7 user-bus watcher needs lingering to survive without a login.
        text = INSTALL_NOC_SH.read_text()
        assert "enable-linger" in text

    def test_xdg_runtime_dir_bridge(self):
        text = INSTALL_NOC_SH.read_text()
        assert "XDG_RUNTIME_DIR=/run/user/" in text


# ─────────────────────────────────────────────────────────────────
# (f) REMOTE_SCRIPT must stay single-quote-free — the apostrophe-in-recipe guard.
#
# fleet_sync.sh builds its remote recipe as a single-quoted string
# (``REMOTE_SCRIPT='...'``). The body MUST stay single-quote-free: a stray
# apostrophe (e.g. a comment saying "can't") silently closes the string early,
# turning the assignment into a ``VAR=val command`` prefix so REMOTE_SCRIPT
# never enters script scope — it reads as "unbound variable" at use time and
# the whole remote leg (git pull + restarts) is skipped. `bash -n` does NOT
# catch this (the syntax stays valid), and the 2026-06-09 deploy regression
# shipped exactly this way. A purely STATIC scan is the right guard here: a
# behavioral bind-check would have to execute the script's env-dependent
# top-level (the recipe is woven through ~11 unrelated apostrophe-bearing
# comment lines, so any partial-execution probe is parse-fragile and differs
# CI-vs-box). The invariant — no stray apostrophe in the recipe body — is the
# cause, and checking it directly is robust everywhere.
# ─────────────────────────────────────────────────────────────────


class TestRemoteScriptBinds:
    def test_recipe_body_has_no_stray_apostrophes(self):
        # Apostrophes are allowed ONLY as paired heredoc delimiters (<<'PY').
        text = FLEET_SYNC_SH.read_text()
        lines = text.splitlines()
        start = next(i for i, l in enumerate(lines)
                     if l.startswith("REMOTE_SCRIPT='"))
        close = next(i for i in range(start + 1, len(lines))
                     if lines[i].strip() == "'")
        offenders = []
        for i in range(start + 1, close):
            ln = lines[i]
            if "'" not in ln:
                continue
            # strip the allowed paired heredoc-delimiter form, e.g. <<'PY'
            stripped = re.sub(r"<<'[A-Za-z_]+'", "", ln)
            if "'" in stripped:
                offenders.append((i + 1, ln))
        assert not offenders, (
            "stray apostrophe(s) in the single-quoted REMOTE_SCRIPT recipe "
            "(will unbind REMOTE_SCRIPT): "
            + "; ".join(f"L{n}: {t.strip()}" for n, t in offenders)
        )


# ─────────────────────────────────────────────────────────────────
# (g) The federator's self-sync must restart the watchdog — it carries the
# mini probes. The remote leg (sync_repo) restarts meshforge-watchdog; the self
# leg omitted it, so a watchdog code change (e.g. the #79 mini probes) reached
# all 5 remotes but NOT the federator until a manual restart (caught 2026-06-09).
# ─────────────────────────────────────────────────────────────────


class TestSelfSyncRestartsWatchdog:
    def test_self_sync_includes_watchdog(self):
        text = FLEET_SYNC_SH.read_text()
        assert "sync_local_unit meshforge-watchdog" in text, (
            "fleet_sync self-sync must restart meshforge-watchdog on the "
            "federator — it carries the mini probes; otherwise a watchdog code "
            "change silently never reaches THIS box (the #79 deploy gap)."
        )


# ─────────────────────────────────────────────────────────────────
# (h) Standalone dude-claw unit (Phase A, 2026-06-11): same deploy-gap class
# as (a)-(d) — a template with no copy/restart wiring is a daemon that runs
# OLD code forever. The claw unit also lacks the -user.service suffix, so it
# needs the same explicit enumeration.
# ─────────────────────────────────────────────────────────────────

MINI_CLAW_SERVICE = "meshforge-mini-dudeai-claw.service"


class TestClawDeployEnrollment:
    def test_claw_template_exists(self):
        assert (TEMPLATE_DIR / MINI_CLAW_SERVICE).is_file()

    def test_claw_template_uses_standalone_preset(self):
        text = (TEMPLATE_DIR / MINI_CLAW_SERVICE).read_text()
        assert "--preset standalone" in text
        # claw env file, NOT the fleet daemon's (separate operator values)
        assert "mini_dudeai_claw.env" in text

    def test_claw_template_keeps_cwd_shadowing_guard(self):
        # same guard as the fleet unit: a stray ~/mini_dudeai.py must not
        # shadow the package
        text = (TEMPLATE_DIR / MINI_CLAW_SERVICE).read_text()
        assert "WorkingDirectory=/opt/meshforge" in text

    def test_update_sh_copies_claw_template(self):
        assert MINI_CLAW_SERVICE in UPDATE_SH.read_text()

    def test_update_sh_restarts_claw_on_user_bus(self):
        assert re.search(
            r"try-restart\s+meshforge-mini-dudeai-claw\.service",
            UPDATE_SH.read_text(),
        ), "update.sh must try-restart the claw daemon after a code pull"

    def test_fleet_sync_remote_syncs_claw(self):
        assert re.search(
            r"sync_user_unit\s+meshforge-mini-dudeai-claw", FLEET_SYNC_SH.read_text()
        ), "fleet_sync.sh must run sync_user_unit for the claw daemon (remote)"

    def test_fleet_sync_local_syncs_claw(self):
        assert re.search(
            r"sync_local_user_unit\s+meshforge-mini-dudeai-claw",
            FLEET_SYNC_SH.read_text(),
        ), "fleet_sync.sh must self-sync the claw daemon on this box"

    def test_nats_server_template_exists_and_is_hardened(self):
        # the claw bus: OpenClaw v1 has no auth, the server layer is the
        # trust boundary — the canonical unit ships in the repo (pre-push
        # rule 4) and must not run as root
        tmpl = TEMPLATE_DIR / "nats-server.service"
        assert tmpl.is_file()
        text = tmpl.read_text()
        assert "User=nats" in text
        assert "NoNewPrivileges=true" in text
