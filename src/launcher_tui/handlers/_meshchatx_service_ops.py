"""MeshChatX systemd-user service control + state SSOT.

Mirrors ``_nomadnet_service_ops.py`` for the MeshChatX web daemon.
Structural deltas vs NomadNet:

* No tmux. MeshChatX is a long-lived HTTP server, so the analog of
  ``tmux_session`` ("is the wrapped process actually up?") is a
  port-bind check on 127.0.0.1:8000 against the unit's MainPID.
* No "attach" action. The operator opens a browser at
  ``http://127.0.0.1:8000/`` (or via SSH tunnel) instead of attaching
  a terminal.
* Refuse-loud guard is the wrapper's exit-87 path on rpc_key mismatch
  (Issue #41), surfaced via ``StartLimitBurst=5`` exactly as NomadNet
  does.

Privilege model matches NomadNet — ``sudo -u <user> -H env XDG_RUNTIME_DIR
DBUS_SESSION_BUS_ADDRESS systemctl --user`` is the documented incantation
for root-to-user systemctl bridging on every fleet box (Issue #45).
"""

import logging
import os
import pwd
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from utils.paths import get_real_user_home, get_real_username

logger = logging.getLogger(__name__)

_UNIT_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "templates" / "systemd" / "meshchatx-user.service"
)
_WRAPPER_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "templates" / "python" / "meshchatx_wrapper.sh"
)
_INSTALLER = (
    Path(__file__).resolve().parents[3]
    / "scripts" / "install_meshchatx.sh"
)
_UNIT_FILENAME = "meshchatx.service"
_WRAPPER_FILENAME = "meshchatx_wrapper.sh"
_DEFAULT_PORT = 8000
_DEFAULT_HOST = "127.0.0.1"


class MeshChatXServiceOpsMixin:
    """Service-state + control for the MeshChatX user daemon."""

    # ------------------------------------------------------------------
    # Low-level: user-scope systemctl invocation (mirrors NomadNet)
    # ------------------------------------------------------------------

    def _user_systemctl_argv(self, verbs: List[str]) -> List[str]:
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
            'sudo', '-u', sudo_user, '-H', 'env',
            f'XDG_RUNTIME_DIR={runtime_dir}',
            f'DBUS_SESSION_BUS_ADDRESS={dbus}',
            'systemctl', '--user',
        ] + verbs

    _MANAGER_VERBS = frozenset({"daemon-reload", "daemon-reexec"})

    def _systemctl_user(
        self, verb: str, timeout: int = 15,
    ) -> Tuple[bool, str]:
        verbs = [verb] if verb in self._MANAGER_VERBS else [verb, 'meshchatx']
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
    # Liveness probe: is the HTTP port bound by the unit's MainPID?
    # ------------------------------------------------------------------

    def _port_bound_by_pid(self, pid: int, port: int = _DEFAULT_PORT) -> bool:
        """Return True if ``pid`` (or a child) holds a LISTEN on ``port``.

        We check ``ss -tnlp`` rather than a generic port scan so we can
        attribute the bind to the meshchatx process and not, e.g., an
        unrelated dev server. Falls back to a simple ``ss`` parse when
        the lsof-style ``users:`` field isn't populated.
        """
        if pid <= 0:
            return False
        ss_bin = shutil.which("ss")
        if not ss_bin:
            return False
        try:
            proc = subprocess.run(
                [ss_bin, '-Htnlp', f'sport = :{port}'],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        if proc.returncode != 0:
            return False
        text = proc.stdout or ""
        if not text.strip():
            return False
        # ss output: "users:((\"meshchatx\",pid=12345,fd=8))"
        marker = f"pid={pid}"
        if marker in text:
            return True
        # Fall back to "any listener on this port" — matches NomadNet's
        # tmux_session shape: a slightly weaker SSOT but still useful
        # when the unit's MainPID is the parent and a child holds the
        # actual socket (Python multi-process patterns).
        return any(line.strip() for line in text.splitlines())

    # ------------------------------------------------------------------
    # Single source of truth: service state
    # ------------------------------------------------------------------

    def _meshchatx_service_state(self) -> dict:
        """Return the authoritative state of the MeshChatX user unit.

        Keys (parity with ``_nomadnet_service_state`` where meaningful):
            unit_installed  — bool: ~/.config/systemd/user/meshchatx.service exists
            wrapper_installed — bool: ~/.config/meshforge/meshchatx_wrapper.sh exists
            active          — bool: systemctl --user is-active
            enabled         — bool: systemctl --user is-enabled
            sub_state       — str:  "running" / "exited" / "failed" / ...
            main_pid        — int:  MainPID
            n_restarts      — int:  NRestarts
            port_bound      — bool: 127.0.0.1:8000 LISTEN attributable to the unit
            port            — int:  the port we expect (default 8000)
            error           — Optional[str]: transient reason state is UNKNOWN
        """
        unit_path = (
            get_real_user_home() / ".config" / "systemd" / "user"
            / _UNIT_FILENAME
        )
        wrapper_path = (
            get_real_user_home() / ".config" / "meshforge" / _WRAPPER_FILENAME
        )
        state = {
            "unit_installed": unit_path.exists(),
            "wrapper_installed": wrapper_path.exists(),
            "active": False,
            "enabled": False,
            "sub_state": "",
            "main_pid": 0,
            "n_restarts": 0,
            "port_bound": False,
            "port": _DEFAULT_PORT,
            "error": None,
        }

        rc, out = self._user_systemctl_text(['is-active', 'meshchatx'])
        if rc == 0 and out == "active":
            state["active"] = True
        elif rc == -1:
            state["error"] = "systemctl --user unreachable"
            return state

        rc, out = self._user_systemctl_text(['is-enabled', 'meshchatx'])
        if rc == 0 and out in ("enabled", "enabled-runtime", "static",
                               "alias"):
            state["enabled"] = True

        rc, out = self._user_systemctl_text([
            'show', 'meshchatx',
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

        state["port_bound"] = self._port_bound_by_pid(
            state["main_pid"], state["port"],
        )
        return state

    def _service_state_line(self, state: Optional[dict] = None) -> str:
        """One-line summary for menu subtitles."""
        s = state if state is not None else self._meshchatx_service_state()
        if not s["unit_installed"]:
            return "Service: not installed"
        if s["error"]:
            return f"Service: unknown ({s['error']})"
        if s["active"]:
            parts = [f"active ({s['sub_state'] or 'running'})"]
            if s["main_pid"]:
                parts.append(f"PID {s['main_pid']}")
            if s["port_bound"]:
                parts.append(f":{s['port']} bound")
            else:
                parts.append(f":{s['port']} NOT bound")
            if s["n_restarts"] > 0:
                parts.append(f"{s['n_restarts']} restarts")
            return "Service: " + ", ".join(parts)
        if s["sub_state"] == "failed":
            return "Service: failed (see journal)"
        word = "inactive"
        if s["enabled"]:
            word += ", enabled"
        return f"Service: {word}"

    def _print_service_state_block(self) -> None:
        """Print the --- Service State --- block of the status screen."""
        svc = self._meshchatx_service_state()
        if not svc["unit_installed"]:
            print("  Unit:      NOT INSTALLED "
                  "(~/.config/systemd/user/meshchatx.service)")
            print("  Install:   sudo bash "
                  "/opt/meshforge/scripts/install_meshchatx.sh")
            return
        active_word = "ACTIVE" if svc["active"] else "inactive"
        enabled_word = "enabled" if svc["enabled"] else "disabled"
        line = f"  Unit:      {active_word} / {enabled_word}"
        if svc["sub_state"]:
            line += f" ({svc['sub_state']})"
        print(line)
        if svc["main_pid"]:
            print(f"  MainPID:   {svc['main_pid']}")
        if svc["active"]:
            if svc["port_bound"]:
                print(f"  Port:      {svc['port']} bound (web UI up)")
                print(f"  Open:      http://127.0.0.1:{svc['port']}/")
            else:
                print(f"  Port:      \033[0;33m{svc['port']} NOT bound — "
                      f"daemon may still be warming up\033[0m")
        if svc["n_restarts"] and svc["n_restarts"] > 0:
            print(f"  Restarts:  \033[0;33m{svc['n_restarts']} — view "
                  f"MeshChatX logs from this menu\033[0m")
            logger.warning(
                "MeshChatX user unit has restarted %d times",
                svc["n_restarts"],
            )
        if svc["error"]:
            print(f"  Note:      {svc['error']}")

    # ------------------------------------------------------------------
    # Operator actions
    # ------------------------------------------------------------------

    def _service_control_menu(self):
        while True:
            state = self._meshchatx_service_state()
            subtitle = self._service_state_line(state)

            choices: List[Tuple[str, str]] = []
            if not state["unit_installed"]:
                choices.append(("install", "Install MeshChatX (canonical installer)"))
            else:
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
                choices.append(("reinstall_canonical",
                               "Reinstall (canonical, idempotent)"))
            choices.append(("repair_align",
                            "Repair RNS alignment   audit + sudo normalize"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "MeshChatX Service Control",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "install": ("Install MeshChatX",
                            self._run_canonical_installer),
                "reinstall_canonical": ("Reinstall MeshChatX",
                                        self._run_canonical_installer),
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
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _run_systemctl_and_report(self, verb: str) -> None:
        ok, out = self._systemctl_user(verb)
        title = f"{verb.capitalize()}: " + ("OK" if ok else "FAILED")
        body = out if out else (
            f"systemctl --user {verb} meshchatx completed."
        )
        if verb in ("start", "stop", "restart"):
            _, active = self._user_systemctl_text(['is-active', 'meshchatx'])
            body += f"\n\nis-active: {active or '(empty)'}"
        self.ctx.dialog.msgbox(title, body)

    def _run_canonical_installer(self) -> None:
        """Shell out to ``scripts/install_meshchatx.sh`` (idempotent).

        The installer handles sudo→user bridging itself — same as
        ``install_nomadnet.sh``. Output is captured and shown as a
        msgbox truncated to the dialog viewport.
        """
        if not _INSTALLER.is_file():
            self.ctx.dialog.msgbox(
                "Installer missing",
                f"{_INSTALLER} not found. Update MeshForge and try again.",
            )
            return
        if not self.ctx.dialog.yesno(
            "Run MeshChatX canonical installer?",
            f"Run:\n\n  bash {_INSTALLER}\n\n"
            "This refreshes the pipx install (downloads the latest\n"
            "wheel from gitea), the wrapper, and the systemd user\n"
            "unit. Identity at ~/.local/share/meshchatx/ is preserved.\n\n"
            "Idempotent — safe to re-run on an already-aligned box.\n\n"
            "Proceed?",
        ):
            return

        proc = subprocess.run(
            ['bash', str(_INSTALLER)],
            capture_output=True, text=True, timeout=600,
        )
        out = (proc.stdout or '') + (
            f"\n[stderr]\n{proc.stderr}" if proc.stderr else ''
        )
        title = (
            "MeshChatX install: OK"
            if proc.returncode == 0
            else f"MeshChatX install returned {proc.returncode}"
        )
        body_out = out[-2400:] if len(out) > 2400 else out
        self.ctx.dialog.msgbox(title, body_out or "(no output)")

    def _repair_rns_alignment(self) -> None:
        """Run the shared RNS alignment audit (same SSOT as NomadNet).

        Issue #46: rnsd alignment is the cross-cutting precondition for
        every shared-instance LXMF client (NomadNet, MeshChatX, Sideband).
        We delegate to the same ``rns_alignment.py`` CLI rather than
        reimplementing the audit logic.
        """
        from utils.rns_alignment import (
            analyze_drift, plan_normalize, probe_local,
        )
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
                f"Host {state.hostname} matches the canonical layout.\n"
                f"MeshChatX rpc_key precondition is met.",
            )
            return

        plan = plan_normalize(state)
        plan_lines = "\n".join(f"  {i+1}. {a.description}"
                                for i, a in enumerate(plan))
        drift_lines = "\n".join(f"  - {r}" for r in state.drift_reasons)
        body = (
            f"Host: {state.hostname}\n\n"
            f"Drift reasons ({len(state.drift_reasons)}):\n{drift_lines}\n\n"
            f"Planned actions ({len(plan)}):\n{plan_lines}\n\n"
            f"This will modify /etc/reticulum/config and restart rnsd.\n"
            f"All LXMF clients on this box (MeshChatX, NomadNet) need\n"
            f"this alignment to authenticate with rnsd's RPC socket\n"
            f"(Issue #41).\n\n"
            f"Apply now? (sudo password may be required)"
        )
        if not self.ctx.dialog.yesno("Repair RNS alignment", body):
            return

        cli = Path(__file__).resolve().parents[3] / 'scripts' / 'rns_alignment.py'
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
        body_out = out[-2400:] if len(out) > 2400 else out
        self.ctx.dialog.msgbox(title, body_out or "(no output)")

    def _enable_linger(self) -> None:
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
