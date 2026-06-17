"""MeshChatX Handler — third-party LXMF web client install + service.

MeshChatX is an actively-maintained fork of Reticulum MeshChat shipped
by RNS-Things on git.quad4.io. It coexists side-by-side with NomadNet:
both attach to the same shared rnsd instance, each owns a separate
LXMF identity (NomadNet under ``~/.nomadnetwork/``, MeshChatX under
``~/.local/share/meshchatx/``).

The structural model is a parity install of NomadNet's canonical
pattern (Issue #45/#46). Three differences:

* MeshChatX is a long-lived HTTP daemon, not a tmux-wrapped TUI.
  ``Type=simple`` unit, no tmux machinery, liveness is a port-bind
  check on 127.0.0.1:8000 (see ``_meshchatx_service_ops.py``).
* "Attach" → "Open Web UI". On boxes with a desktop we ``xdg-open``;
  on headless boxes we print the URL + an SSH-tunnel hint.
* Wheel install via gitea releases API rather than PyPI — MeshChatX
  is not on PyPI, the canonical source is
  ``https://git.quad4.io/RNS-Things/MeshChatX/releases/latest``.

Entry point: ``MeshChatXHandler.execute("meshchatx")`` opens the main
menu. Gated by the ``meshchatx`` feature flag (default off, opt-in
on GATEWAY/FULL profiles).
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from backend import clear_screen
from handler_protocol import BaseHandler

from handlers._meshchatx_service_ops import MeshChatXServiceOpsMixin
from utils.paths import (
    MeshChatXPaths,
    ReticulumPaths,
    get_real_user_home,
)

logger = logging.getLogger(__name__)

_INSTALLER = (
    Path(__file__).resolve().parents[3]
    / "scripts" / "install_meshchatx.sh"
)


class MeshChatXHandler(MeshChatXServiceOpsMixin, BaseHandler):
    """TUI handler for MeshChatX web client management."""

    handler_id = "meshchatx"
    menu_section = "mesh_networks"

    def menu_items(self):
        # Feature-flagged behind ``meshchatx`` (off by default).
        return [
            ("meshchatx", "MeshChatX Web Client    LXMF web UI on :8000",
             "meshchatx"),
        ]

    def execute(self, action):
        if action == "meshchatx":
            self._meshchatx_menu()

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    def _meshchatx_menu(self) -> None:
        while True:
            state = self._meshchatx_service_state()
            subtitle = self._service_state_line(state)

            choices: List[Tuple[str, str]] = [
                ("status", "Status                  install + service + identity"),
                ("open_ui", "Open Web UI             http://127.0.0.1:8000/"),
                ("service", "Service Control         start / stop / install / repair"),
                ("logs", "View Logs               journalctl --user -u meshchatx"),
                ("preflight", "Run install audit       scripts/install_meshchatx.sh --check"),
                ("uninstall", "Uninstall               disable + pipx uninstall"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshChatX Web Client",
                subtitle,
                choices,
            )
            if choice is None or choice == "back":
                return

            dispatch = {
                "status": ("MeshChatX status", self._show_status),
                "open_ui": ("Open MeshChatX UI", self._open_web_ui),
                "service": ("Service control", self._service_control_menu),
                "logs": ("View logs", self._view_logs),
                "preflight": ("Install audit", self._run_preflight_audit),
                "uninstall": ("Uninstall MeshChatX", self._uninstall),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _show_status(self) -> None:
        clear_screen()
        print("=== MeshChatX Status ===\n")

        # Install state
        bin_path = get_real_user_home() / ".local" / "bin" / "meshchatx"
        if bin_path.is_symlink() or bin_path.exists():
            target = (
                str(bin_path.resolve()) if bin_path.is_symlink()
                else str(bin_path)
            )
            print(f"  Binary:    {bin_path}")
            print(f"  Resolved:  {target}")
        else:
            print(f"  Binary:    NOT INSTALLED ({bin_path})")
            print("  Install:   Service Control > Install MeshChatX")

        # Storage / identity
        storage = MeshChatXPaths.get_storage_dir()
        if storage.is_dir():
            try:
                size = sum(
                    p.stat().st_size
                    for p in storage.rglob("*") if p.is_file()
                )
                print(f"  Storage:   {storage}  "
                      f"({size / 1024:.1f} KiB)")
            except OSError:
                print(f"  Storage:   {storage}  (size unavailable)")
        else:
            print(f"  Storage:   not yet created ({storage})")

        # rpc_key precondition (Issue #41)
        rpc_key = ReticulumPaths.get_shared_rpc_key()
        if rpc_key:
            print(f"  rpc_key:   pinned ({rpc_key[:8]}…)")
        else:
            print("  rpc_key:   \033[0;33mNOT PINNED — MeshChatX will "
                  "exit 87 at startup\033[0m")
            print("             (Service Control > Repair RNS alignment)")

        print("\n--- Service State ---")
        self._print_service_state_block()

        # Coexistence note
        nomadnet_dir = get_real_user_home() / ".nomadnetwork"
        if nomadnet_dir.is_dir():
            print("\n--- Coexistence ---")
            print(f"  NomadNet identity present: {nomadnet_dir}")
            print("  MeshChatX and NomadNet share rnsd but use separate")
            print("  LXMF identities. Peers must add both hashes to")
            print("  reach both clients.")

        self.ctx.wait_for_enter()

    def _open_web_ui(self) -> None:
        """Surface the web UI URL.

        On boxes with a graphical session (``$DISPLAY`` set), offer to
        ``xdg-open`` the URL. On headless boxes, print the URL plus an
        SSH-tunnel hint and copy to clipboard if ``xclip``/``wl-copy``
        is available — the operator on the remote workstation pastes
        into their browser.
        """
        state = self._meshchatx_service_state()
        port = state.get("port", 8000)
        url = f"http://127.0.0.1:{port}/"

        if not state["active"]:
            self.ctx.dialog.msgbox(
                "MeshChatX is not running",
                f"The service is not active.\n\n"
                f"Start it via:  Service Control > Start service\n"
                f"Then return here to open the web UI.",
            )
            return
        if not state["port_bound"]:
            self.ctx.dialog.msgbox(
                "MeshChatX still warming up",
                f"The service is active but :{port} is not yet\n"
                f"bound. Web servers usually need a few seconds to\n"
                f"finish startup; try again in 5-10 seconds.\n\n"
                f"If this persists, view the logs from this menu (View Logs).",
            )
            return

        display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        xdg_open = shutil.which("xdg-open")

        if display and xdg_open:
            if self.ctx.dialog.yesno(
                "Open MeshChatX in browser?",
                f"Detected a graphical session — open\n\n"
                f"  {url}\n\n"
                f"in the default browser?",
            ):
                try:
                    subprocess.Popen(
                        [xdg_open, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    self.ctx.dialog.msgbox(
                        "Browser launched",
                        f"xdg-open dispatched.\n\nURL: {url}",
                    )
                    return
                except (OSError, subprocess.SubprocessError) as e:
                    self.ctx.dialog.msgbox(
                        "Browser launch failed",
                        f"xdg-open failed: {e}\n\nURL is still:\n  {url}",
                    )
                    return

        # Headless path: print URL + tunnel hint
        hostname = self._hostname()
        body = (
            f"URL on this box:\n  {url}\n\n"
            f"From a remote workstation, open an SSH tunnel:\n"
            f"  ssh -L {port}:localhost:{port} {self._user()}@{hostname}\n"
            f"Then visit http://localhost:{port}/ in your browser.\n\n"
        )
        copied = self._copy_to_clipboard(url)
        if copied:
            body += f"URL copied to clipboard ({copied})."
        self.ctx.dialog.msgbox("MeshChatX Web UI", body)

    def _view_logs(self) -> None:
        argv = self._user_systemctl_argv([])
        # _user_systemctl_argv ends with ['systemctl', '--user'] — swap
        # in journalctl for the journal viewer.
        argv = [a for a in argv if a not in ('systemctl', '--user')]
        argv += ['journalctl', '--user', '-u', 'meshchatx',
                 '-n', '100', '--no-pager']
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=10,
            )
            out = (proc.stdout or '') + (proc.stderr or '')
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox(
                "Logs unavailable",
                f"Could not read journal: {e}",
            )
            return
        body = out[-3500:] if len(out) > 3500 else (out or "(no output)")
        self.ctx.dialog.msgbox("MeshChatX journal (last 100)", body)

    def _run_preflight_audit(self) -> None:
        """Run the canonical installer in --check mode."""
        if not _INSTALLER.is_file():
            self.ctx.dialog.msgbox(
                "Installer missing",
                f"{_INSTALLER} not found. Update MeshForge.",
            )
            return
        try:
            proc = subprocess.run(
                ['bash', str(_INSTALLER), '--check'],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox("Audit failed", str(e))
            return
        out = (proc.stdout or '') + (proc.stderr or '')
        title = (
            "Install audit: ALIGNED"
            if proc.returncode == 0
            else f"Install audit: DRIFT (exit {proc.returncode})"
        )
        self.ctx.dialog.msgbox(title, out or "(no output)")

    def _uninstall(self) -> None:
        if not self.ctx.dialog.yesno(
            "Uninstall MeshChatX?",
            "This will:\n\n"
            "  - Stop and disable the meshchatx user service\n"
            "  - Remove the systemd unit file\n"
            "  - pipx uninstall reticulum-meshchatx\n\n"
            "It does NOT delete:\n"
            f"  - Storage / identity at {MeshChatXPaths.get_storage_dir()}\n"
            "  - The wrapper at ~/.config/meshforge/meshchatx_wrapper.sh\n\n"
            "To wipe identity too, re-run the canonical installer with\n"
            "--reinstall --wipe-identity.\n\n"
            "Proceed?",
        ):
            return

        self._systemctl_user("stop")
        self._systemctl_user("disable")
        unit_path = (
            get_real_user_home() / ".config" / "systemd" / "user"
            / "meshchatx.service"
        )
        try:
            if unit_path.exists():
                unit_path.unlink()
        except OSError as e:
            logger.warning("Failed to remove %s: %s", unit_path, e)

        self._systemctl_user("daemon-reload")

        # pipx uninstall — reuse the installer's path resolution by
        # shelling out, but as the real user with their PATH.
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            argv = [
                'sudo', '-u', sudo_user, '-H',
                'env', f'PATH={get_real_user_home()}/.local/bin:'
                f'/usr/local/bin:/usr/bin:/bin',
                'pipx', 'uninstall', 'reticulum-meshchatx',
            ]
        else:
            argv = ['pipx', 'uninstall', 'reticulum-meshchatx']
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=60,
            )
            uninstall_out = (proc.stdout or '') + (proc.stderr or '')
        except (subprocess.SubprocessError, OSError) as e:
            uninstall_out = f"pipx uninstall failed: {e}"

        self.ctx.dialog.msgbox(
            "MeshChatX uninstalled",
            f"Service disabled, unit removed.\n\n"
            f"pipx uninstall:\n{uninstall_out.strip() or '(no output)'}\n\n"
            f"Storage preserved at:\n  {MeshChatXPaths.get_storage_dir()}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _hostname(self) -> str:
        try:
            return os.uname().nodename
        except OSError:
            return "<host>"

    def _user(self) -> str:
        return os.environ.get("SUDO_USER") or os.environ.get("USER") or "user"

    def _copy_to_clipboard(self, text: str) -> Optional[str]:
        for tool in ("wl-copy", "xclip", "xsel"):
            path = shutil.which(tool)
            if not path:
                continue
            try:
                if tool == "xclip":
                    argv = [path, "-selection", "clipboard"]
                elif tool == "xsel":
                    argv = [path, "--clipboard", "--input"]
                else:
                    argv = [path]
                proc = subprocess.run(
                    argv,
                    input=text, text=True,
                    capture_output=True, timeout=5,
                )
                if proc.returncode == 0:
                    return tool
            except (subprocess.SubprocessError, OSError):
                continue
        return None
