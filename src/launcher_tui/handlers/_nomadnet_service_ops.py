"""NomadNet systemd-user service control for the TUI.

Issue #38 consolidated NomadNet onto a tmux-wrapped systemd user unit
(``~/.config/systemd/user/nomadnet.service`` → template at
``templates/systemd/nomadnet-user.service``). This mixin makes that
service first-class in the TUI:

* ``_nomadnet_service_state()`` — single source of truth for "is the
  service installed / active / enabled", ``tmux has-session``, PID, and
  restart counter. Every guard in ``nomadnet.py`` consults this before
  deciding whether to pkill / spawn a raw process.
* ``_service_control_menu()`` — start / stop / restart / enable /
  install_unit, routed through the user-scope systemctl.
* ``_attach_tmux_session()`` — drops the operator into the running
  TUI via ``tmux attach -t nomadnet``.
* ``_install_user_unit()`` — copies the template, runs
  ``daemon-reload`` + ``enable --now``, and enables linger so the
  service survives logout.

Privilege model: MeshForge typically runs via ``sudo python3 …`` and
must therefore execute ``systemctl --user`` AS the real user. We shell
out via ``sudo -u <user> -H env XDG_RUNTIME_DIR=/run/user/<uid>
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus systemctl --user
<verb> nomadnet`` which is the documented incantation for root-to-user
systemctl bridging and works on every fleet box (Issue #45).
"""

import logging
import os
import pwd
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from backend import clear_screen
from utils.paths import get_real_user_home, get_real_username

logger = logging.getLogger(__name__)

# Path to the systemd unit template shipped in the repo. Resolved relative
# to the package root so this works regardless of cwd.
_UNIT_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "templates" / "systemd" / "nomadnet-user.service"
)

# The unit name we install to under the user's config dir.
_UNIT_FILENAME = "nomadnet.service"


class NomadNetServiceOpsMixin:
    """Service-state + control for the tmux-wrapped NomadNet user unit."""

    # ------------------------------------------------------------------
    # Low-level: user-scope systemctl invocation
    # ------------------------------------------------------------------

    def _user_systemctl_argv(self, verbs: List[str]) -> List[str]:
        """Build argv for ``systemctl --user <verbs>`` honoring sudo.

        When MeshForge was launched via ``sudo`` we must run as the real
        user with their XDG_RUNTIME_DIR/DBUS env set — otherwise the user
        manager is unreachable and every call fails with ``Failed to
        connect to bus: No such file or directory``.
        """
        sudo_user = os.environ.get('SUDO_USER')
        if not sudo_user or sudo_user == 'root':
            return ['systemctl', '--user'] + verbs

        try:
            uid = pwd.getpwnam(sudo_user).pw_uid
        except KeyError:
            logger.warning(
                "SUDO_USER=%s not found in passwd; falling back to "
                "plain systemctl --user", sudo_user,
            )
            return ['systemctl', '--user'] + verbs

        runtime_dir = f"/run/user/{uid}"
        dbus = f"unix:path={runtime_dir}/bus"
        return [
            'sudo', '-u', sudo_user, '-H',
            'env',
            f'XDG_RUNTIME_DIR={runtime_dir}',
            f'DBUS_SESSION_BUS_ADDRESS={dbus}',
            'systemctl', '--user',
        ] + verbs

    # Verbs that operate on the whole user manager, not on a specific
    # unit. ``daemon-reload`` / ``daemon-reexec`` / ``reset-failed``
    # (no arg) refuse "Too many arguments" when given a unit name.
    _MANAGER_VERBS = frozenset({"daemon-reload", "daemon-reexec"})

    def _systemctl_user(
        self, verb: str, timeout: int = 15,
    ) -> Tuple[bool, str]:
        """Run a single-verb systemctl --user call against nomadnet.

        For unit-scoped verbs (start/stop/restart/enable/disable/…),
        appends ``nomadnet`` as the target. For manager-scoped verbs
        (``daemon-reload``/``daemon-reexec``) the unit name is omitted
        — systemd rejects those with "Too many arguments" otherwise.

        Returns ``(success, combined_output)`` where success is ``True``
        iff the command exited 0. Output has stderr appended to stdout
        so dialog rendering gets the whole story.
        """
        verbs = [verb] if verb in self._MANAGER_VERBS else [verb, 'nomadnet']
        argv = self._user_systemctl_argv(verbs)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode == 0, out.strip()
        except subprocess.TimeoutExpired:
            return False, f"systemctl --user {verb} timed out"
        except FileNotFoundError:
            return False, "systemctl not found — not a systemd system?"
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"systemctl --user {verb} failed: {e}"

    def _user_systemctl_text(
        self, verbs: List[str], timeout: int = 10,
    ) -> Tuple[int, str]:
        """Same as _systemctl_user but for arbitrary verbs (is-active,
        is-enabled, show, list-unit-files). Returns ``(returncode,
        stdout)`` with stderr discarded — callers parse stdout only."""
        argv = self._user_systemctl_argv(verbs)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
            return proc.returncode, (proc.stdout or "").strip()
        except (subprocess.TimeoutExpired, subprocess.SubprocessError,
                FileNotFoundError, OSError) as e:
            logger.debug("systemctl --user %s failed: %s", verbs, e)
            return -1, ""

    # ------------------------------------------------------------------
    # Single source of truth: service state
    # ------------------------------------------------------------------

    def _nomadnet_service_state(self) -> dict:
        """Return the authoritative state of the NomadNet user unit.

        Keys:
            unit_installed  — bool: ~/.config/systemd/user/nomadnet.service exists
            active          — bool: systemctl --user is-active returns 0
            enabled         — bool: systemctl --user is-enabled returns 0
            sub_state       — str:  "running" / "exited" / "failed" / ...
            main_pid        — int:  MainPID (0 if no active process)
            n_restarts      — int:  NRestarts (0 if unit healthy)
            tmux_session    — bool: `tmux has-session -t nomadnet`
            error           — Optional[str]: transient reason state is UNKNOWN

        This is the SSOT — every NomadNet handler that previously asked
        "is nomadnet running" should consult this dict first. Falling back
        to ``find_competing_clients()`` is only valid when
        ``unit_installed`` is False.
        """
        unit_path = (
            get_real_user_home() / ".config" / "systemd" / "user"
            / _UNIT_FILENAME
        )
        state = {
            "unit_installed": unit_path.exists(),
            "active": False,
            "enabled": False,
            "sub_state": "",
            "main_pid": 0,
            "n_restarts": 0,
            "tmux_session": False,
            "error": None,
        }

        # is-active
        rc, out = self._user_systemctl_text(['is-active', 'nomadnet'])
        if rc == 0 and out == "active":
            state["active"] = True
        elif rc == -1:
            state["error"] = "systemctl --user unreachable"
            return state

        # is-enabled
        rc, out = self._user_systemctl_text(['is-enabled', 'nomadnet'])
        if rc == 0 and out in ("enabled", "enabled-runtime", "static",
                               "alias"):
            state["enabled"] = True

        # show (SubState / MainPID / NRestarts)
        rc, out = self._user_systemctl_text([
            'show', 'nomadnet',
            '-p', 'SubState', '-p', 'MainPID', '-p', 'NRestarts',
        ])
        if rc == 0:
            for line in out.splitlines():
                if '=' not in line:
                    continue
                k, v = line.split('=', 1)
                v = v.strip()
                if k == "SubState":
                    state["sub_state"] = v
                elif k == "MainPID":
                    try:
                        state["main_pid"] = int(v)
                    except ValueError:
                        pass
                elif k == "NRestarts":
                    try:
                        state["n_restarts"] = int(v)
                    except ValueError:
                        pass

        # tmux has-session — must also run as the real user because the
        # session is owned by whoever launched it.
        state["tmux_session"] = self._tmux_has_session("nomadnet")

        return state

    def _tmux_has_session(self, name: str) -> bool:
        """Return True if the named tmux session exists (real user ctx)."""
        tmux_bin = shutil.which("tmux")
        if not tmux_bin:
            return False

        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            try:
                uid = pwd.getpwnam(sudo_user).pw_uid
            except KeyError:
                return False
            argv = [
                'sudo', '-u', sudo_user, '-H',
                'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
                tmux_bin, 'has-session', '-t', name,
            ]
        else:
            argv = [tmux_bin, 'has-session', '-t', name]

        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=5,
            )
            return proc.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError,
                OSError):
            return False

    def _warn_if_service_active(self, title: str, body: str) -> bool:
        """Return True when the caller may proceed with a raw-launch path.

        If the tmux-wrapped user unit is active, show ``body`` as a
        yesno and only return True when the operator explicitly opts in.
        Otherwise return True (proceed) without prompting.
        """
        state = self._nomadnet_service_state()
        if not state["active"]:
            return True
        return bool(self.ctx.dialog.yesno(
            title,
            body + "\n\nProceed anyway?",
        ))

    def _print_service_state_block(self) -> None:
        """Print the --- Service State --- block of the NomadNet status screen.

        Called from ``_nomadnet_status()`` inside ``nomadnet.py`` — kept
        here so the main file stays under the 1,500-line cap (CLAUDE.md
        Issue #6) and the rendering logic sits next to the state SSOT.
        """
        svc = self._nomadnet_service_state()
        if not svc["unit_installed"]:
            print("  Unit:      NOT INSTALLED "
                  "(~/.config/systemd/user/nomadnet.service)")
            print("  Install:   NomadNet > Service Control > "
                  "Install user unit")
            return
        active_word = "ACTIVE" if svc["active"] else "inactive"
        enabled_word = "enabled" if svc["enabled"] else "disabled"
        line = f"  Unit:      {active_word} / {enabled_word}"
        if svc["sub_state"]:
            line += f" ({svc['sub_state']})"
        print(line)
        if svc["main_pid"]:
            print(f"  MainPID:   {svc['main_pid']}")
        if svc["tmux_session"]:
            print("  tmux:      session 'nomadnet' up "
                  "(attach: tmux attach -t nomadnet)")
        else:
            if svc["active"]:
                print("  tmux:      \033[0;33mmissing — service active"
                      " but no session\033[0m")
            else:
                print("  tmux:      (no session)")
        if svc["n_restarts"] and svc["n_restarts"] > 0:
            print(f"  Restarts:  \033[0;33m{svc['n_restarts']}"
                  f" — view NomadNet logs from this menu\033[0m")
            logger.warning(
                "NomadNet user unit has restarted %d times",
                svc["n_restarts"],
            )
        if svc["error"]:
            print(f"  Note:      {svc['error']}")

    def _service_state_line(self, state: Optional[dict] = None) -> str:
        """Render a one-line summary of the service state for menu subtitles."""
        s = state if state is not None else self._nomadnet_service_state()
        if not s["unit_installed"]:
            return "Service: not installed"
        if s["error"]:
            return f"Service: unknown ({s['error']})"
        if s["active"]:
            parts = [f"active ({s['sub_state'] or 'running'})"]
            if s["main_pid"]:
                parts.append(f"PID {s['main_pid']}")
            if s["tmux_session"]:
                parts.append("tmux up")
            if s["n_restarts"] > 0:
                parts.append(f"{s['n_restarts']} restarts")
            return "Service: " + ", ".join(parts)
        # Not active — distinguish failed vs inactive for the operator
        if s["sub_state"] == "failed":
            return "Service: failed (see journal)"
        state_word = "inactive"
        if s["enabled"]:
            state_word += ", enabled"
        return f"Service: {state_word}"

    # ------------------------------------------------------------------
    # Control actions
    # ------------------------------------------------------------------

    def _service_control_menu(self):
        """Operator-facing submenu for the tmux-wrapped NomadNet service."""
        while True:
            state = self._nomadnet_service_state()
            subtitle = self._service_state_line(state)

            choices: List[Tuple[str, str]] = []
            if not state["unit_installed"]:
                choices.append(("install_unit", "Install systemd user unit"))
            else:
                if state["tmux_session"]:
                    choices.append(("attach", "Attach tmux session"))
                if state["active"]:
                    choices.append(("restart", "Restart service"))
                    choices.append(("stop", "Stop service"))
                else:
                    choices.append(("start", "Start service"))
                if state["enabled"]:
                    choices.append(("disable", "Disable at login (keeps running)"))
                else:
                    choices.append(("enable", "Enable at login"))
                choices.append(("linger", "Enable linger (survives logout)"))
                choices.append(("reinstall_unit",
                               "Reinstall unit from template"))
            choices.append(("repair_align",
                            "Repair RNS alignment   audit + sudo normalize"))
            choices.append(("reinstall_nomadnet",
                            "Reinstall NomadNet (idempotent)"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "NomadNet Service Control",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "install_unit": ("Install user unit", self._install_user_unit),
                "reinstall_unit": ("Reinstall user unit",
                                   lambda: self._install_user_unit(force=True)),
                "attach": ("Attach tmux", self._attach_tmux_session),
                "start": ("Start service",
                          lambda: self._run_systemctl_and_report("start")),
                "stop": ("Stop service",
                         lambda: self._run_systemctl_and_report("stop")),
                "restart": ("Restart service",
                            lambda: self._run_systemctl_and_report("restart")),
                "enable": ("Enable service",
                           lambda: self._run_systemctl_and_report("enable")),
                "disable": ("Disable service",
                            lambda: self._run_systemctl_and_report("disable")),
                "linger": ("Enable linger", self._enable_linger),
                "repair_align": ("Repair RNS alignment",
                                 self._repair_rns_alignment),
                "reinstall_nomadnet": ("Reinstall NomadNet",
                                       self._reinstall_nomadnet),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _run_systemctl_and_report(self, verb: str) -> None:
        """Execute a verb, show the combined output as an msgbox."""
        ok, out = self._systemctl_user(verb)
        title = f"{verb.capitalize()}: " + ("OK" if ok else "FAILED")
        body = out if out else (
            f"systemctl --user {verb} nomadnet completed."
        )
        # Append confirmation from is-active for start/stop/restart
        if verb in ("start", "stop", "restart"):
            _, active = self._user_systemctl_text(['is-active', 'nomadnet'])
            body += f"\n\nis-active: {active or '(empty)'}"
        self.ctx.dialog.msgbox(title, body)

    def _repair_rns_alignment(self) -> None:
        """Run the RNS alignment audit; if drift, offer to normalize.

        Wraps ``scripts/rns_alignment.py`` so operators don't need shell
        access to apply the canonical layout (rnsd reads /etc/reticulum,
        rpc_key pinned, NomadNet --rnsconfig matches, file ownership
        repaired). Issue #46.
        """
        from utils.rns_alignment import (
            analyze_drift, plan_normalize, probe_local,
        )

        # Phase 1: read-only probe
        try:
            state = probe_local()
        except Exception as e:  # pragma: no cover — defensive
            self.ctx.dialog.msgbox(
                "Probe failed",
                f"Could not probe RNS alignment state:\n{e}",
            )
            return
        state.drift_reasons = analyze_drift(state)

        if state.aligned:
            self.ctx.dialog.msgbox(
                "RNS Alignment: ALIGNED",
                f"Host {state.hostname} matches the canonical layout.\n\n"
                f"  rnsd configdir:  {state.rnsd_configdir}\n"
                f"  /etc/reticulum/config: rpc_key pinned, instance_name="
                f"{state.etc_config.instance_name or '(default)'}\n"
                f"  NomadNet --rnsconfig: "
                f"{state.nomadnet_unit_rnsconfig or '(unit not installed)'}",
            )
            return

        # Phase 2: present the drift + planned actions
        plan = plan_normalize(state)
        plan_lines = "\n".join(f"  {i+1}. {a.description}"
                                for i, a in enumerate(plan))
        drift_lines = "\n".join(f"  - {r}" for r in state.drift_reasons)
        body = (
            f"Host: {state.hostname}\n\n"
            f"Drift reasons ({len(state.drift_reasons)}):\n{drift_lines}\n\n"
            f"Planned actions ({len(plan)}):\n{plan_lines}\n\n"
            f"This will modify /etc/reticulum/config, install an rnsd\n"
            f"systemd drop-in, and restart rnsd. The change preserves\n"
            f"rnsd's existing identity (storage is copied, not regenerated).\n\n"
            f"Apply now? (sudo password may be required)"
        )
        if not self.ctx.dialog.yesno("Repair RNS alignment", body):
            return

        # Phase 3: invoke the CLI with sudo so the script writes /etc/reticulum
        repo_root = Path(__file__).resolve().parents[3]
        cli = repo_root / 'scripts' / 'rns_alignment.py'
        if not cli.is_file():
            self.ctx.dialog.msgbox(
                "Script missing",
                f"{cli} not found. Update MeshForge and try again.",
            )
            return
        proc = subprocess.run(
            ['sudo', 'python3', str(cli), 'normalize', '--yes'],
            capture_output=True, text=True, timeout=120,
        )
        out = (proc.stdout or '') + (
            f"\n[stderr]\n{proc.stderr}" if proc.stderr else ''
        )
        title = (
            "Repair OK" if proc.returncode == 0 else
            f"Repair returned {proc.returncode}"
        )
        # Truncate for the dialog if it's huge
        body_out = out[-2400:] if len(out) > 2400 else out
        self.ctx.dialog.msgbox(title, body_out or "(no output)")

    def _reinstall_nomadnet(self) -> None:
        """Run the canonical pipx-first installer (idempotent).

        Companion to ``_repair_rns_alignment`` for the install-method
        side of the equation (Issue #46 sister concern). Refuses to run
        if RNS alignment is drifted — the installer's precondition.
        """
        from utils.rns_alignment import analyze_drift, probe_local

        # Precondition: alignment must be OK
        try:
            state = probe_local()
        except Exception as e:  # pragma: no cover — defensive
            self.ctx.dialog.msgbox(
                "Probe failed",
                f"Could not probe RNS alignment state:\n{e}",
            )
            return
        state.drift_reasons = analyze_drift(state)
        if not state.aligned:
            self.ctx.dialog.msgbox(
                "Repair RNS alignment first",
                "RNS alignment is drifted. The NomadNet installer\n"
                "cannot proceed until rnsd is normalized:\n\n"
                + "\n".join(f"  - {r}" for r in state.drift_reasons[:5])
                + "\n\nFix via:\n"
                "  Service Control > Repair RNS alignment\n"
                "Then return here.",
            )
            return

        # Confirm
        if not self.ctx.dialog.yesno(
            "Reinstall NomadNet (idempotent)",
            "Run the canonical installer:\n\n"
            "  bash /opt/meshforge/scripts/install_nomadnet.sh\n\n"
            "This refreshes the pipx install (if needed), the wrapper,\n"
            "and the systemd user unit. Identity at ~/.nomadnetwork/\n"
            "is preserved.\n\n"
            "Run a no-op when the install is already canonical.\n\n"
            "Proceed?",
        ):
            return

        # Locate + run the installer. The script handles the sudo→user
        # bridging itself, so we shell it directly.
        repo_root = Path(__file__).resolve().parents[3]
        installer = repo_root / 'scripts' / 'install_nomadnet.sh'
        if not installer.is_file():
            self.ctx.dialog.msgbox(
                "Installer missing",
                f"{installer} not found. Update MeshForge and try again.",
            )
            return

        proc = subprocess.run(
            ['bash', str(installer)],
            capture_output=True, text=True, timeout=600,
        )
        out = (proc.stdout or '') + (
            f"\n[stderr]\n{proc.stderr}" if proc.stderr else ''
        )
        title = (
            "NomadNet reinstall: OK"
            if proc.returncode == 0
            else f"NomadNet reinstall returned {proc.returncode}"
        )
        body_out = out[-2400:] if len(out) > 2400 else out
        self.ctx.dialog.msgbox(title, body_out or "(no output)")

        # On success, surface a follow-up offer to restart so the new
        # unit definition replaces any prior tmux session cleanly.
        if proc.returncode == 0:
            if self.ctx.dialog.yesno(
                "Restart service?",
                "Installer succeeded. Restart the nomadnet user service\n"
                "now to pick up the refreshed unit?",
            ):
                self._run_systemctl_and_report("restart")

    def _attach_tmux_session(self) -> None:
        """Drop the operator into ``tmux attach -t nomadnet``.

        This blocks until the operator detaches (Ctrl-b d) or NomadNet
        exits. The TUI is torn down around the call so tmux owns the
        terminal cleanly.
        """
        if not shutil.which("tmux"):
            self.ctx.dialog.msgbox(
                "tmux not installed",
                "tmux is required to attach to NomadNet's detached session.\n\n"
                "Service Control > Reinstall NomadNet installs tmux for you.",
            )
            return

        if not self._tmux_has_session("nomadnet"):
            self.ctx.dialog.msgbox(
                "No tmux session",
                "No tmux session named 'nomadnet' exists.\n\n"
                "Start the service first (Service Control > Start), then\n"
                "wait a second for the session to come up.",
            )
            return

        clear_screen()
        print("=== Attaching to NomadNet tmux session ===")
        print("Detach with Ctrl-b d to return to MeshForge.\n")

        sudo_user = os.environ.get('SUDO_USER')
        tmux_bin = shutil.which("tmux") or "tmux"
        if sudo_user and sudo_user != 'root':
            try:
                uid = pwd.getpwnam(sudo_user).pw_uid
            except KeyError:
                self.ctx.dialog.msgbox(
                    "User Lookup Failed",
                    f"Cannot resolve SUDO_USER={sudo_user} in passwd.",
                )
                return
            argv = [
                'sudo', '-u', sudo_user, '-H',
                'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
                tmux_bin, 'attach', '-t', 'nomadnet',
            ]
        else:
            argv = [tmux_bin, 'attach', '-t', 'nomadnet']

        try:
            # timeout=None: the operator decides when to detach.
            subprocess.run(argv, timeout=None)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\ntmux attach failed: {e}")
            self.ctx.wait_for_enter()
            return
        except KeyboardInterrupt:
            pass
        clear_screen()

    def _apt_install(
        self, package: str, timeout: int = 180,
    ) -> Tuple[bool, str]:
        """sudo apt-get install -y <package>; returns (ok, combined_output).

        Mirrors the apt-install pattern used by ``scripts/install_noc.sh``.
        When MeshForge is already root (`sudo python3 …`), we skip the
        ``sudo`` prefix — that keeps the call working cleanly under the
        Admin Mode privilege separation documented in CLAUDE.md.
        """
        argv = (
            ['apt-get', 'install', '-y', package]
            if os.geteuid() == 0
            else ['sudo', 'apt-get', 'install', '-y', package]
        )
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or '') + (proc.stderr or '')
            return proc.returncode == 0, out.strip()
        except subprocess.TimeoutExpired:
            return False, f"apt-get install -y {package} timed out"
        except (subprocess.SubprocessError, OSError,
                FileNotFoundError) as e:
            return False, f"apt-get install failed: {e}"

    def _install_user_unit(self, force: bool = False) -> None:
        """Render the unit template for THIS box's install layout and activate it.

        Steps (must all succeed for a fresh MeshForge install to produce
        a working service):
          1. Ensure ``tmux`` is available — apt-install via yesno when missing.
          2. Ensure ``nomadnet`` binary is discoverable (via
             ``_find_nomadnet_binary`` — covers pipx venv AND pip --user).
          3. Ensure the RPC-degradation wrapper exists
             (``_create_nomadnet_wrapper`` — idempotent).
          4. Compute the per-box ExecStart argv via
             ``_get_wrapper_command`` and substitute it into
             ``__NOMADNET_EXEC__`` in the template. The template's outer
             `tmux new-session ... '<exec>'` single-quoting is preserved
             by ``shlex.join`` on the inner argv.
          5. Write the rendered unit (confirm overwrite when present
             and not ``force``); chown to the real user.
          6. ``daemon-reload`` + ``enable --now`` (user scope); best-effort
             ``loginctl enable-linger`` so the service survives logout.

        Fails closed at each prerequisite so we never produce a unit
        that references a path that doesn't exist (the Issue #45 +
        follow-up regression seen on a fleet box).
        """
        if not _UNIT_TEMPLATE.exists():
            self.ctx.dialog.msgbox(
                "Template Missing",
                f"Cannot find unit template:\n\n  {_UNIT_TEMPLATE}\n\n"
                f"Is this a full MeshForge checkout?",
            )
            return

        # 1. tmux — required by the unit's ExecStart
        if not shutil.which('tmux'):
            if not self.ctx.dialog.yesno(
                "Install tmux?",
                "tmux is required by the NomadNet user unit but is not\n"
                "installed on this box.\n\n"
                "Run: sudo apt-get install -y tmux ?",
            ):
                return
            tmux_ok, tmux_out = self._apt_install('tmux')
            if not tmux_ok or not shutil.which('tmux'):
                self.ctx.dialog.msgbox(
                    "tmux install failed",
                    f"Could not install tmux:\n\n{tmux_out}\n\n"
                    f"Check the output above, then re-run Install systemd user unit.",
                )
                return

        # 2. nomadnet binary — must be discoverable
        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            # _find_nomadnet_binary already surfaced its own msgbox
            return

        # 3. wrapper — idempotent; covers transient rnsd unavailability
        wrapper_path = None
        try:
            wrapper_path = self._create_nomadnet_wrapper()
        except (OSError, PermissionError) as e:
            logger.warning("nomadnet wrapper creation failed: %s", e)

        # 4. compute argv + substitute into template
        argv = self._get_wrapper_command(
            nn_path, ['--rnsconfig', '/etc/reticulum'],
        )
        if argv is None:
            # Issue #46: refuse to install a unit pointing at a non-canonical
            # install. The canonical installer is the repair path.
            self.ctx.dialog.msgbox(
                "Unit install refused",
                "Cannot derive a canonical ExecStart — pipx venv python\n"
                "is missing next to ~/.local/bin/nomadnet.\n\n"
                "Repair with:\n"
                "  bash /opt/meshforge/scripts/install_nomadnet.sh\n\n"
                "That installer writes the unit on its own, so re-running\n"
                "this menu after it succeeds is not strictly required.",
            )
            return
        exec_str = shlex.join(argv)

        user_home = get_real_user_home()
        unit_dir = user_home / ".config" / "systemd" / "user"
        unit_path = unit_dir / _UNIT_FILENAME

        if unit_path.exists() and not force:
            if not self.ctx.dialog.yesno(
                "Unit already installed",
                f"Unit file already exists:\n\n  {unit_path}\n\n"
                f"Overwrite from template "
                f"(path substitution will be refreshed)?",
            ):
                return

        template_text = _UNIT_TEMPLATE.read_text()
        if '__NOMADNET_EXEC__' not in template_text:
            self.ctx.dialog.msgbox(
                "Template Broken",
                f"Template is missing the __NOMADNET_EXEC__ placeholder:\n\n"
                f"  {_UNIT_TEMPLATE}\n\n"
                f"Refusing to install a unit that would ignore per-box paths.",
            )
            return
        unit_text = template_text.replace('__NOMADNET_EXEC__', exec_str)

        # 5. write unit
        try:
            unit_dir.mkdir(parents=True, exist_ok=True)
            unit_path.write_text(unit_text)
            self._chown_real_user(unit_dir)
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox(
                "Install failed",
                f"Could not write unit file:\n\n  {unit_path}\n\n{e}",
            )
            return

        # 5b. Remove the legacy boot-race drop-in (#69 regression cleanup).
        # Commit 121ac59a shipped the boot-race gate as a drop-in
        # (nomadnet.service.d/10-wait-rnsd.conf) hardcoding @rns/default,
        # which crash-looped the unit on every non-default-instance box.
        # The corrected gate now lives INLINE in the unit above, so a
        # lingering drop-in would only re-shadow it. Remove it (and the
        # dir if it's left empty) so the inline gate is authoritative.
        legacy_dropin = unit_dir / "nomadnet.service.d" / "10-wait-rnsd.conf"
        try:
            if legacy_dropin.exists():
                legacy_dropin.unlink()
                logger.info("Removed legacy boot-race drop-in: %s", legacy_dropin)
                try:
                    legacy_dropin.parent.rmdir()  # only succeeds if now empty
                except OSError:
                    pass  # other drop-ins present — leave the dir
        except (OSError, PermissionError) as e:
            logger.warning(
                "Could not remove legacy drop-in %s: %s", legacy_dropin, e,
            )

        # 6. reload + enable --now
        reload_ok, reload_out = self._systemctl_user("daemon-reload")
        enable_ok, enable_out = self._systemctl_user("enable")
        start_ok, start_out = self._systemctl_user("start")

        # linger (so the service survives logout on headless Pis)
        linger_note = ""
        sudo_user = os.environ.get('SUDO_USER') or get_real_username()
        if sudo_user and sudo_user != 'root':
            try:
                proc = subprocess.run(
                    ['loginctl', 'enable-linger', sudo_user]
                    if os.geteuid() == 0
                    else ['sudo', 'loginctl', 'enable-linger', sudo_user],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    linger_note = f"linger enabled for {sudo_user}"
                else:
                    linger_note = (
                        f"linger FAILED: {proc.stderr.strip() or proc.stdout.strip()}"
                    )
            except (subprocess.SubprocessError, OSError) as e:
                linger_note = f"linger FAILED: {e}"

        wrapper_status = "OK" if wrapper_path else "skipped"
        summary_lines = [
            f"Unit:          {unit_path}",
            f"tmux:          {'OK' if shutil.which('tmux') else 'MISSING'}",
            f"nomadnet:      {nn_path}",
            f"wrapper:       {wrapper_status}",
            f"ExecStart:     {exec_str}",
            f"daemon-reload: {'OK' if reload_ok else 'FAILED'}",
            f"enable:        {'OK' if enable_ok else 'FAILED'}",
            f"start:         {'OK' if start_ok else 'FAILED'}",
            linger_note,
        ]
        if not reload_ok:
            summary_lines.append(f"  {reload_out}")
        if not enable_ok:
            summary_lines.append(f"  {enable_out}")
        if not start_ok:
            summary_lines.append(f"  {start_out}")

        self.ctx.dialog.msgbox(
            "NomadNet user unit installed"
            if (reload_ok and enable_ok and start_ok)
            else "NomadNet user unit install had issues",
            "\n".join(summary_lines) + (
                "\n\nAttach with: Service Control > Attach tmux session"
            ),
        )

    def _enable_linger(self) -> None:
        """Enable linger for the real user via ``loginctl``."""
        sudo_user = os.environ.get('SUDO_USER') or get_real_username()
        if not sudo_user or sudo_user == 'root':
            self.ctx.dialog.msgbox(
                "Linger not applicable",
                "Linger is only meaningful for non-root users.\n"
                "Run MeshForge via 'sudo' from your normal account.",
            )
            return
        argv = (
            ['loginctl', 'enable-linger', sudo_user]
            if os.geteuid() == 0
            else ['sudo', 'loginctl', 'enable-linger', sudo_user]
        )
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=10,
            )
            ok = proc.returncode == 0
            self.ctx.dialog.msgbox(
                "Linger: " + ("OK" if ok else "FAILED"),
                (proc.stdout + proc.stderr).strip() or
                f"loginctl enable-linger {sudo_user} completed.",
            )
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox("Linger FAILED", str(e))

    def _chown_real_user(self, path: Path) -> None:
        """Best-effort ``chown -R $SUDO_USER:$SUDO_USER <path>``."""
        sudo_user = os.environ.get('SUDO_USER')
        if not sudo_user or sudo_user == 'root':
            return
        try:
            subprocess.run(
                ['chown', '-R', f'{sudo_user}:{sudo_user}', str(path)],
                capture_output=True, timeout=15,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("chown %s for %s failed: %s", sudo_user, path, e)
