"""RNS Repair Wizard — repair shared instance, validate service file.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
Functions take handler or ctx for TUI interaction.
"""

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

from utils.paths import get_real_user_home, ReticulumPaths
from utils.service_check import (
    check_rns_shared_instance, get_rns_shared_instance_info,
    get_udp_port_owner, start_service, stop_service,
    _sudo_write, daemon_reload, enable_service,
)
from utils.config_drift import detect_rnsd_config_drift
from ._rns_interface_mgr import find_blocking_interfaces, disable_interfaces_in_config

logger = logging.getLogger(__name__)


def restart_rnsd() -> bool:
    """Restart rnsd via service_check (stop + start + wait).

    Thin helper so callers outside _rns_repair.py don't need to
    import stop_service/start_service directly.

    Returns True if rnsd restarted and shared instance became available.
    """
    stop_service('rnsd')
    time.sleep(1)
    success, msg = start_service('rnsd')
    if not success:
        logger.warning("rnsd start failed: %s", msg)
        return False
    from ._service_ops_common import wait_for_condition
    if wait_for_condition(check_rns_shared_instance, 5):
        return True
    logger.warning("rnsd restarted but shared instance not available after 5s")
    return False


def validate_rnsd_service_file() -> bool:
    """Validate and fix the rnsd systemd service file.

    Detects and fixes:
    - StartLimitIntervalSec in [Service] instead of [Unit]
    - ExecStart pointing to system rnsd instead of venv rnsd
      (venv has all dependencies like meshtastic)
    - Missing After=meshtasticd.service when MeshtasticInterface is
      configured (rnsd crashes if meshtasticd isn't ready yet)

    Returns True if the service file was fixed (daemon-reload needed).
    """
    service_path = Path('/etc/systemd/system/rnsd.service')
    if not service_path.exists():
        return False

    try:
        content = service_path.read_text()
    except (OSError, PermissionError):
        return False

    # Check for StartLimitIntervalSec in [Service] section (should be in [Unit])
    misplaced_directives = False
    current_section = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            current_section = stripped
        elif current_section == '[Service]' and (
            'StartLimitIntervalSec' in stripped
            or 'StartLimitBurst' in stripped
        ):
            misplaced_directives = True
            break

    # Check ExecStart — two problems to detect:
    # 1. ExecStart points to a binary that doesn't exist on disk (critical)
    # 2. ExecStart uses system rnsd when venv rnsd is available (venv has deps)
    wrong_rnsd_path = False
    current_rnsd_binary = None
    exec_match = re.search(r'ExecStart\s*=\s*(.+)', content)
    if exec_match:
        current_rnsd = exec_match.group(1).strip()
        current_rnsd_binary = current_rnsd.split()[0]

    venv_rnsd = Path('/opt/meshforge/venv/bin/rnsd')

    if current_rnsd_binary:
        if not Path(current_rnsd_binary).exists():
            wrong_rnsd_path = True
        elif venv_rnsd.exists() and current_rnsd_binary != str(venv_rnsd):
            wrong_rnsd_path = True

    # Check for missing meshtasticd ordering dependency
    missing_ordering = False
    if 'meshtasticd.service' not in content:
        try:
            rns_config = ReticulumPaths.get_config_file()
            if rns_config.exists():
                rns_content = rns_config.read_text()
                if re.search(r'^\s*\[\[.*Meshtastic', rns_content, re.MULTILINE):
                    missing_ordering = True
        except Exception as e:
            print(f"  Warning: Could not check RNS config: {e}")

    if not misplaced_directives and not wrong_rnsd_path and not missing_ordering:
        return False

    # Report what we're fixing
    if misplaced_directives:
        print("  Found: StartLimitIntervalSec in [Service] (should be [Unit])")
    if wrong_rnsd_path and current_rnsd_binary:
        if not Path(current_rnsd_binary).exists():
            print(f"  Found: ExecStart binary missing: {current_rnsd_binary}")
        elif venv_rnsd.exists():
            print(f"  Found: ExecStart uses {current_rnsd_binary}")
            print(f"         Should use venv: {venv_rnsd}")
    if missing_ordering:
        print("  Found: Missing After=meshtasticd.service")
        print("         rnsd can crash if meshtasticd isn't ready")
    print("  Regenerating rnsd.service...")

    # Prefer venv rnsd — it has all dependencies
    rnsd_path = str(venv_rnsd) if venv_rnsd.exists() else (
        shutil.which('rnsd') or '/usr/local/bin/rnsd'
    )

    if not Path(rnsd_path).exists():
        print(f"  ERROR: No rnsd binary found on this system.")
        print(f"  Checked: /opt/meshforge/venv/bin/rnsd, PATH, /usr/local/bin/rnsd")
        print(f"  Reinstall MeshForge to restore RNS (Settings > Updates).")
        return False

    service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network-online.target meshtasticd.service
Wants=network-online.target

# Stop crash-looping after 5 failures in 60 seconds
# (e.g., NomadNet holding port 37428)
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={rnsd_path} --service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
    write_ok, write_msg = _sudo_write(str(service_path), service_content)
    if write_ok:
        print("  Fixed: rnsd.service regenerated")
        ok, msg = daemon_reload()
        if ok:
            print("  Reloaded: systemd daemon-reload complete")
        else:
            print(f"  Warning: daemon-reload failed: {msg}")
        ok, msg = enable_service('rnsd')
        if ok:
            print("  Enabled: rnsd will start on boot")
        else:
            print(f"  Warning: could not enable rnsd: {msg}")
        return True
    else:
        print(f"  Warning: Could not write service file: {write_msg}")
        return False


def repair_rns_shared_instance(handler) -> bool:
    """Repair RNS shared instance — explicit user action only.

    This is a repair wizard method, NOT an error handler auto-fix.
    Must only be called from explicit user actions (RNS Diagnostics,
    Repair menu, etc.) — never from error handlers in _run_rns_tool().

    Args:
        handler: RNSDiagnosticsHandler instance (for ctx, dependencies, conflict checks)

    Steps:
    1. Ensures /etc/reticulum/ directories exist with correct permissions,
       deploys template ONLY if no config exists anywhere (never overwrites)
    2. Validates rnsd.service file (fixes ExecStart path & misplaced directives)
    3. Checks rnsd Python dependencies for enabled interface plugins
    4. Clears stale auth tokens, checks blocking interfaces, restarts rnsd
    5. Verifies shared instance is now available (UDP port 37428)

    Returns True if fix was successful.
    """
    ctx = handler.ctx
    rpc_key_generated = False

    print("\n" + "=" * 50)
    print("RNS REPAIR: Shared Instance")
    print("=" * 50)

    # Step 1: Fix directories and deploy config ONLY if none exists
    target_dir = Path('/etc/reticulum')
    target = target_dir / 'config'

    print(f"\n[1/5] Checking RNS config and directories...")

    try:
        if ReticulumPaths.ensure_system_dirs():
            print(f"  Ensured: {ReticulumPaths.ETC_STORAGE}")
            print(f"  Ensured: {ReticulumPaths.ETC_INTERFACES}")
        else:
            print("  ERROR: Could not create /etc/reticulum/ directories")
            print("  (Run MeshForge with sudo)")  # in-domain-ok: privilege separation — creating /etc/reticulum needs root (in_domain_principle.md)
            return False

        existing_config = ReticulumPaths.get_config_file()
        if existing_config.exists():
            print(f"  Existing config preserved: {existing_config}")
        else:
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
            if template.exists():
                shutil.copy2(str(template), str(target))
                target.chmod(0o644)
                print(f"  No config found — deployed template to: {target}")
            else:
                print("  WARNING: No config found and template missing")
                print("  Run: rnsd --exampleconfig > /etc/reticulum/config")
    except (OSError, PermissionError) as e:
        print(f"  ERROR: {e}")
        print("  (Run MeshForge with sudo)")  # in-domain-ok: privilege separation — creating /etc/reticulum needs root (in_domain_principle.md)
        return False

    # Step 1b: ensure rnsd carries a resolvable rpc_key (Issue #41/#37).
    print(f"\n  Ensuring rnsd rpc_key (RPC client authentication)...")
    rpc_key_generated = _ensure_rnsd_rpc_key()

    # Step 2: Validate rnsd.service file
    print(f"\n[2/5] Validating rnsd systemd service file...")
    service_path = Path('/etc/systemd/system/rnsd.service')
    if service_path.exists():
        service_fixed = validate_rnsd_service_file()
        if not service_fixed:
            print("  Service file: OK")
    else:
        print("  Service file: not found (rnsd may not be installed as service)")

    # Step 3: Check rnsd Python dependencies
    print(f"\n[3/5] Checking rnsd Python dependencies...")
    handler._ensure_rnsd_dependencies()

    # Step 4: Stop rnsd, clear stale auth tokens, start rnsd
    print(f"\n[4/5] Restarting rnsd service...")

    print("  Stopping rnsd...")
    success, msg = stop_service('rnsd')
    if not success:
        print(f"  Warning stopping rnsd: {msg}")
    time.sleep(1)

    # Clear stale shared_instance_* files
    print("  Clearing stale shared instance authentication files...")
    user_home = get_real_user_home()
    storage_dirs = [
        Path('/etc/reticulum/storage'),
        Path('/root/.reticulum/storage'),
        user_home / '.reticulum' / 'storage',
        user_home / '.config' / 'reticulum' / 'storage',
    ]
    files_cleared = 0
    for storage_dir in storage_dirs:
        if storage_dir.exists():
            for auth_file in storage_dir.glob('shared_instance_*'):
                try:
                    auth_file.unlink()
                    files_cleared += 1
                    print(f"    Removed: {auth_file}")
                except (OSError, PermissionError) as e:
                    print(f"    Warning: Could not remove {auth_file}: {e}")
    if files_cleared == 0:
        print("    No stale auth files found")

    # Pre-flight 4a: Validate share_instance = Yes
    _preflight_share_instance(ctx)

    # Pre-flight 4b: Config drift
    try:
        drift = detect_rnsd_config_drift()
        if drift.drifted:
            print(f"\n  WARNING: Config drift detected!")
            print(f"    Gateway reads: {drift.gateway_config_dir}")
            print(f"    rnsd reads:    {drift.rnsd_config_dir}")
            print(f"    Fix: {drift.fix_hint}")
            print("    The checks above may have validated the wrong config.")
    except Exception as e:
        logger.debug("Pre-flight config drift check failed: %s", e)

    # Pre-flight 4c: NomadNet conflict
    try:
        if handler._check_nomadnet_conflict():
            print("\n  WARNING: NomadNet is running!")
            print("  NomadNet may hold the RNS shared instance,")
            print("  preventing rnsd from becoming the shared instance.")
            owner = get_udp_port_owner(37428)
            if owner:
                print(f"  Port 37428 held by: {owner[0]} (PID {owner[1]})")
            if ctx.dialog.yesno(
                "Stop NomadNet?",
                "NomadNet is running and may hold the\n"
                "RNS shared instance port.\n\n"
                "Stop NomadNet before starting rnsd?\n"
                "(NomadNet can be restarted afterward as a client)",
            ):
                try:
                    subprocess.run(
                        ['pkill', '-f', 'nomadnet'],
                        capture_output=True, timeout=5
                    )
                    time.sleep(1)
                    print("  NomadNet stopped")
                except (subprocess.SubprocessError, OSError) as e:
                    print(f"  Could not stop NomadNet: {e}")
            else:
                print("  Proceeding with NomadNet running (rnsd may fail)...")
    except Exception as e:
        logger.debug("Pre-flight NomadNet check failed: %s", e)

    # Pre-flight: check blocking interfaces
    user_declined_disable = False
    blocking = find_blocking_interfaces()
    if blocking:
        print("\n  WARNING: Enabled interfaces have missing dependencies:")
        for iface_name, reason, fix in blocking:
            print(f"    [{iface_name}] {reason}")
            print(f"    Fix: {fix}")
        print()
        print("  rnsd will hang if these interfaces can't connect.")

        iface_names = [b[0] for b in blocking]
        names_str = ", ".join(iface_names)
        confirmed, disabled = _confirm_disable_interfaces(
            ctx, iface_names,
            f"These interfaces will prevent rnsd from starting:\n"
            f"  {names_str}\n\n"
            f"Temporarily disable them in the RNS config?\n"
            f"(You can re-enable them later from the RNS menu)\n\n"
            f"If you choose No, rnsd may hang on startup.",
        )
        if not confirmed:
            user_declined_disable = True
            print("  Proceeding without disabling (rnsd may hang)...\n")
        elif disabled:
            print(f"  Disabled {len(disabled)} blocking interface(s):")
            for name in disabled:
                print(f"    [{name}] set enabled = no")
        # confirmed-but-failed already showed its own dialog witness

    # Clear systemd start limit
    try:
        subprocess.run(
            ['systemctl', 'reset-failed', 'rnsd'],
            capture_output=True, timeout=5
        )
    except (subprocess.SubprocessError, OSError):
        pass

    # Start rnsd
    print("  Starting rnsd...")
    try:
        success, msg = start_service('rnsd')
        if success:
            print("  rnsd started successfully")
        else:
            print(f"  Warning: {msg}")
    except Exception as e:
        print(f"  Warning: {e}")

    # Fix permissions on files rnsd just created (auth tokens, caches).
    # This must run AFTER rnsd start so newly-created shared_instance_*
    # files and identity are covered.  Without this, NomadNet (running as
    # a non-root user) cannot read the auth tokens → auth mismatch.
    try:
        time.sleep(1)  # brief delay for rnsd to create files
        ReticulumPaths._fix_storage_file_permissions()
        print("  Fixed file permissions for shared access")
    except Exception as e:
        logger.debug("Post-restart permission fix: %s", e)

    # Step 5: Verify shared instance
    print(f"\n[5/5] Verifying shared instance...")
    print("  Waiting for rnsd shared instance...")

    instance_ok = False
    rnsd_crashed = False
    for i in range(30):
        instance_ok = check_rns_shared_instance()
        if instance_ok:
            break
        try:
            # Intentional raw is-active (not check_service): this loop must
            # distinguish 'activating' (mid-start — keep waiting) from
            # 'failed'/'inactive' (crashed — stop). check_service collapses
            # 'activating' into NOT_RUNNING, which would falsely declare a crash
            # mid-start. Allowlisted in TestServiceCheckContract.
            r = subprocess.run(
                ['systemctl', 'is-active', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            state = r.stdout.strip()
            if state in ('failed', 'inactive'):
                rnsd_crashed = True
                break
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(1)

    if instance_ok:
        info = get_rns_shared_instance_info()
        print(f"  SUCCESS: RNS shared instance is available")
        print(f"  Method: {info['detail']}")
        print("\n" + "=" * 50)
        print("RNS shared instance is now available!")
        print("=" * 50 + "\n")
        if rpc_key_generated:
            _offer_rns_client_restarts(ctx)
        return True

    if rnsd_crashed:
        return _handle_rnsd_crash(ctx)

    # Shared instance not available after 30s but rnsd didn't crash
    return _diagnose_timeout(handler, user_declined_disable)


def _ensure_rnsd_rpc_key() -> bool:
    """Pin a fresh rnsd rpc_key ONLY when none is resolvable. Returns True if a
    key was newly generated/pinned.

    ``get_shared_rpc_key()`` is DERIVATION-AWARE (explicit pin OR rnsd-identity-
    derived); we act only when it returns None — which includes a freshly-
    deployed template's literal ``__RPC_KEY__`` placeholder. Pinning on a box
    that already resolves a (derived) key would break its clients until they
    re-read it, so we never do (#41/#37). The clients rebuild their own client
    config at startup, so a restart is how the new key propagates — see
    ``_offer_rns_client_restarts``.
    """
    cfg_path = ReticulumPaths.get_config_file()
    if ReticulumPaths.get_shared_rpc_key() is not None:
        print("  rpc_key: OK (resolvable)")
        return False
    if not cfg_path.exists():
        print("  rpc_key: skipped (no config file yet)")
        return False
    try:
        from utils.rns_config_setup import ensure_rpc_key
        _key, generated = ensure_rpc_key(cfg_path)
        if generated:
            print("  Pinned a fresh rpc_key — RNS clients read it on (re)start.")
        else:
            print("  rpc_key still unresolved (existing value left as-is).")
        return generated
    except OSError as e:
        print(f"  Could not write rpc_key: {e}")
        print("  (Relaunch in Admin mode to write /etc/reticulum/config.)")  # in-domain-ok: privilege separation — writing /etc/reticulum needs root (in_domain_principle.md)
        return False
    except Exception as e:
        logger.debug("rpc_key ensure failed: %s", e)
        return False


def _offer_rns_client_restarts(ctx):
    """After a fresh rpc_key is pinned, offer to restart the RNS client
    services in-app.

    The clients (gateway, map) rebuild their own RNS client config at startup,
    reading ``ReticulumPaths.get_shared_rpc_key()`` fresh — so restarting them
    is how the new key propagates (no separate copy step). ``offer_service_fix``
    is profile-gated, so only services this box actually runs are offered.
    """
    print("\n  A fresh rpc_key was pinned. RNS client services must restart")
    print("  to pick it up (they rebuild their client config on start).")
    try:
        from service_remediation import offer_service_fix
        for svc in ("meshforge-gateway", "meshforge-map"):
            offer_service_fix(ctx, svc, running=True)
    except Exception as e:
        logger.debug("RNS client restart offer failed: %s", e)


def _preflight_share_instance(ctx):
    """Validate share_instance = Yes before restart."""
    try:
        from commands.rns import _parse_share_instance
        config_path = ReticulumPaths.get_config_file()
        if config_path.exists():
            config_content = config_path.read_text()
            share_ok = _parse_share_instance(config_content)
            if share_ok:
                print("  share_instance: Yes (OK)")
            else:
                print("  share_instance: DISABLED")
                print("  Without share_instance = Yes, rnsd won't accept")
                print("  connections from gateway, rnstatus, or other tools.")
                if ctx.dialog.yesno(
                    "Fix share_instance",
                    "share_instance is disabled in the RNS config.\n\n"
                    "Without it, rnsd won't expose the shared instance\n"
                    "and no client apps (gateway, rnstatus) can connect.\n\n"
                    f"Config: {config_path}\n\n"
                    "Set share_instance = Yes?"
                ):
                    if re.search(r'^\s*share_instance\s*=',
                                 config_content, re.MULTILINE):
                        fixed = re.sub(
                            r'^(\s*share_instance\s*=\s*).*$',
                            r'\1Yes',
                            config_content,
                            count=1,
                            flags=re.MULTILINE
                        )
                    elif '[reticulum]' in config_content.lower():
                        fixed = config_content.replace(
                            '[reticulum]',
                            '[reticulum]\n  share_instance = Yes',
                            1
                        )
                    else:
                        fixed = ('[reticulum]\n  share_instance = Yes\n\n'
                                 + config_content)
                    ok, msg = _sudo_write(str(config_path), fixed)
                    if ok:
                        verify = config_path.read_text()
                        if _parse_share_instance(verify):
                            print("  Fixed: share_instance = Yes")
                        else:
                            print("  WARNING: Config write did not take effect")
                    else:
                        print(f"  Could not write config: {msg}")
        else:
            print(f"  Config not found at {config_path}")
    except Exception as e:
        logger.debug("Pre-flight share_instance check failed: %s", e)


def _handle_rnsd_crash(ctx) -> bool:
    """Handle rnsd crash during repair — diagnose and offer fixes."""
    print("  FAILED: rnsd crashed on startup")
    print()

    venv_rnsd = Path('/opt/meshforge/venv/bin/rnsd')
    rnsd_path = str(venv_rnsd) if venv_rnsd.exists() else (
        shutil.which('rnsd') or '/usr/local/bin/rnsd'
    )
    print("  Running rnsd directly to capture error...")
    output = ""
    try:
        r = subprocess.run(
            [rnsd_path],
            capture_output=True, text=True, timeout=10
        )
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        if output:
            lower_output = output.lower()
            if 'address already in use' in lower_output:
                print("  Cause: Port conflict (another process holds the port)")
            elif 'permission' in lower_output and 'denied' in lower_output:
                print("  Cause: Permission denied")
            elif 'connection refused' in lower_output:
                print("  Cause: meshtasticd not running (Connection refused)")
                print("  Fix: start meshtasticd from Service Control.")
            else:
                for line in output.splitlines()[-5:]:
                    print(f"  {line}")
            print(f"  View full rnsd logs in-app (RNS > Diagnostics).")
        else:
            print("  (no output captured)")
    except subprocess.TimeoutExpired:
        print("  rnsd hung (no crash within 10s — likely a blocking interface)")
    except (OSError, FileNotFoundError) as e:
        print(f"  Could not run rnsd: {e}")

    # Detect missing meshtastic module and offer install
    if 'meshtastic' in output.lower():
        print()
        print("  Cause: Meshtastic_Interface.py plugin needs the meshtastic module")
        venv_pip = Path('/opt/meshforge/venv/bin/pip')
        if venv_pip.exists() and ctx.dialog.yesno(
            "Install meshtastic Module",
            "The Meshtastic_Interface.py plugin requires the\n"
            "meshtastic Python module, which is not installed.\n\n"
            "Install it now?\n\n"
            "  pip install meshtastic (into MeshForge venv)",
        ):
            print("  Installing meshtastic module...")
            # Route through the hardened helper into the venv's interpreter
            # (ensure_pip + return-code check + conflict-retry are all internal).
            from utils.pip_install import pip_install
            venv_python = venv_pip.parent / 'python'
            pip_r = pip_install(['meshtastic'], python=str(venv_python), timeout=120)
            if pip_r.ok:
                print("  meshtastic installed. Restarting rnsd...")
                subprocess.run(
                    ['systemctl', 'reset-failed', 'rnsd'],
                    capture_output=True, timeout=5
                )
                start_service('rnsd')
                time.sleep(3)
                if check_rns_shared_instance():
                    print("  SUCCESS: RNS shared instance is available")
                    print("\n" + "=" * 50)
                    print("RNS shared instance is now available!")
                    print("=" * 50 + "\n")
                    return True
                print("  rnsd restarted — check with RNS > Diagnostics")
            else:
                print(f"  pip install failed: {pip_r.detail[:200]}")

    return False


def _confirm_disable_interfaces(ctx, iface_names, prompt_text):
    """yesno-confirmed interface disable with a DIALOG witness on failure.

    Shared by the pre-flight and second-chance paths (review F2: the C1 fix
    covered only the second-chance branch; the pre-flight sibling ran the
    same confirmed destructive write with only print() as witness — which
    the next dialog's clear_screen erases).

    Returns (user_confirmed, disabled_names). A confirmed-but-failed write
    returns (True, []) after showing an honest "nothing was changed" dialog.
    """
    if not ctx.dialog.yesno("Disable Blocking Interfaces?", prompt_text):
        return False, []
    try:
        disabled = disable_interfaces_in_config(iface_names)
    except Exception as e:
        logger.error("disable_interfaces_in_config failed: %s", e)
        ctx.dialog.msgbox(
            "Disable FAILED — nothing was changed",
            f"Could not modify the Reticulum config:\n\n"
            f"  {type(e).__name__}: {e}\n\n"
            f"Your interfaces are untouched."
        )
        return True, []
    if not disabled:
        ctx.dialog.msgbox(
            "Disable FAILED — nothing was changed",
            "No interfaces could be disabled (none matched in the config).\n"
            "Your Reticulum config is untouched."
        )
        return True, []
    return True, disabled


def _offer_disable_blocking(handler, post_blocking) -> bool:
    """Offer to disable blocking interfaces and restart rnsd — with a witness.

    This is a USER-CONFIRMED DESTRUCTIVE flow (it rewrites the Reticulum
    config), so every failure must reach the operator's screen, and the
    failure dialog must say which side of the write it happened on:
    before the config write, nothing was changed; after it, the config is
    already modified and pretending otherwise would be its own lie.

    Returns True only when the shared instance came back up.
    """
    ctx = handler.ctx
    iface_names = [b[0] for b in post_blocking]
    names_str = ", ".join(iface_names)
    # Pre-write phase (yesno + config write) is shared with the pre-flight
    # path; its failure dialogs say "nothing was changed".
    confirmed, disabled = _confirm_disable_interfaces(
        ctx, iface_names,
        f"Blocking interfaces are preventing rnsd\n"
        f"from initializing:\n"
        f"  {names_str}\n\n"
        f"Disable them and restart rnsd?",
    )
    if not confirmed or not disabled:
        return False

    # --- post-write phase: the config IS modified from here on ----------
    try:
        print(f"  Disabled {len(disabled)} interface(s)")
        stop_service('rnsd')
        time.sleep(1)
        start_service('rnsd')
        from ._service_ops_common import wait_for_condition
        if wait_for_condition(check_rns_shared_instance, 15,
                              label="Waiting for shared instance"):
            si = get_rns_shared_instance_info()
            return ctx.report_action(
                True,
                "RNS Shared Instance Restored",
                f"Disabled: {', '.join(disabled)}\n\n{si['detail']}",
            )
        ctx.report_action(
            False,
            "", "",
            fail_title="Interfaces disabled — rnsd still not up",
            fail_body=(
                f"The config change WAS applied "
                f"(disabled: {', '.join(disabled)}), but the shared "
                f"instance did not come up within 15s.\n\n"
                f"Check RNS > Diagnostics; re-enable the interfaces from "
                f"RNS > Interfaces if this was not the cause."
            ),
        )
        return False
    except Exception as e:
        logger.error("post-write repair step failed: %s", e)
        ctx.dialog.msgbox(
            "Repair FAILED after config change",
            f"Interfaces WERE disabled ({', '.join(disabled)}) but the "
            f"rnsd restart did not complete cleanly:\n\n"
            f"  {type(e).__name__}: {e}\n\n"
            f"Your config is in the modified state. Check RNS > "
            f"Diagnostics, or re-enable the interfaces from RNS > "
            f"Interfaces."
        )
        return False


def _diagnose_timeout(handler, user_declined_disable: bool) -> bool:
    """Diagnose why shared instance isn't available after 30s."""
    ctx = handler.ctx
    print("  WARNING: Shared instance not available after 30s")
    print()
    print("  --- Diagnosing root cause ---")

    info = get_rns_shared_instance_info()
    print(f"  Shared instance: {info['detail']}")

    try:
        owner = get_udp_port_owner(37428)
        if owner:
            print(f"  Port 37428 owner: {owner[0]} (PID {owner[1]})")
            if owner[0] in ('nomadnet', 'python', 'python3'):
                print("  Likely cause: NomadNet is holding the port")
    except Exception as e:
        logger.debug("diagnosis step skipped (port owner): %s", e)

    try:
        from commands.rns import _parse_share_instance
        config_path = ReticulumPaths.get_config_file()
        if config_path.exists():
            config_content = config_path.read_text()
            if not _parse_share_instance(config_content):
                print("  Cause: share_instance not enabled in config")
                print("  (pre-flight fix may not have applied due to config drift)")
    except Exception as e:
        logger.debug("diagnosis step skipped (share_instance): %s", e)

    try:
        drift = detect_rnsd_config_drift()
        if drift.drifted:
            print(f"  Config drift: gateway reads {drift.gateway_config_dir}")
            print(f"                rnsd reads    {drift.rnsd_config_dir}")
            print(f"  Fix: {drift.fix_hint}")
    except Exception as e:
        logger.debug("diagnosis step skipped (config drift): %s", e)

    try:
        if handler._check_nomadnet_conflict():
            print("  NomadNet is running (may hold the shared instance)")
    except Exception as e:
        logger.debug("diagnosis step skipped (nomadnet conflict): %s", e)

    # Blocking interfaces — offer second chance. Only the DETECTION is
    # allowed to fail quietly; the confirmed destructive flow inside
    # _offer_disable_blocking reports its own failures to the user
    # (2026-08-14 audit C1: an except-pass around the whole block meant a
    # user could confirm "disable my interfaces", have the write or the
    # restart throw, and see nothing at all).
    try:
        post_blocking = find_blocking_interfaces()
    except Exception as e:
        logger.debug("diagnosis step skipped (blocking interfaces): %s", e)
        post_blocking = None
    if post_blocking:
        print("\n  Blocking interfaces detected:")
        for iface_name, reason, fix in post_blocking:
            print(f"    [{iface_name}] {reason}")
        if user_declined_disable:
            print("\n  These are likely why rnsd is stuck.")
            if _offer_disable_blocking(handler, post_blocking):
                return True

    # Journal output
    print()
    try:
        from ._service_ops_common import journal_tail_text
        text = journal_tail_text('rnsd', lines=15, quiet=True,
                                 no_hostname=True, empty_text="")
        if text:
            print("  Recent rnsd log:")
            for line in text.splitlines()[-10:]:
                print(f"    {line.strip()[:100]}")
        else:
            print("  No journal entries for rnsd")
    except (subprocess.SubprocessError, OSError):
        print("  View rnsd logs in-app (RNS > Diagnostics).")

    print("\n  Run RNS > Diagnostics for a full health check.")
    return False


def fix_rns_storage_permissions(ctx) -> bool:
    """Fix /etc/reticulum/storage permissions for non-root users.

    When rnsd runs as root, it creates /etc/reticulum/storage owned by root.
    NomadNet (running as user) needs write access. This sets 0o777 on the
    storage directory and fixes file permissions within.

    Moved from _nomadnet_rns_checks.py Tier 1 chmod logic.

    Args:
        ctx: TUIContext with dialog backend.

    Returns:
        True if permissions fixed or already OK, False on failure.
    """
    import os
    import stat

    etc_rns = Path('/etc/reticulum')
    if not etc_rns.exists():
        return True

    storage_dir = etc_rns / 'storage'
    sudo_user = os.environ.get('SUDO_USER')

    # Check if storage is writable by the real user
    can_write = False
    try:
        if storage_dir.exists():
            if sudo_user and sudo_user != 'root':
                mode = storage_dir.stat().st_mode
                can_write = bool(mode & stat.S_IWOTH)
            else:
                test_file = storage_dir / '.write_test'
                try:
                    test_file.touch()
                    test_file.unlink()
                    can_write = True
                except (OSError, PermissionError):
                    pass
        else:
            try:
                storage_dir.mkdir(parents=True, exist_ok=True)
                can_write = True
            except (OSError, PermissionError):
                pass
    except (OSError, ValueError) as e:
        logger.debug("RNS storage dir check failed: %s", e)

    if can_write:
        return True

    # Fix permissions
    target_user = sudo_user if sudo_user and sudo_user != 'root' else 'current user'
    logger.info(
        "/etc/reticulum/storage not writable by %s, fixing permissions to 0o777",
        target_user,
    )
    try:
        old_umask = os.umask(0)
        try:
            storage_dir.chmod(0o777)
            ReticulumPaths._fix_storage_file_permissions()
        finally:
            os.umask(old_umask)
        ctx.dialog.msgbox(
            "Storage Permissions Fixed",
            "/etc/reticulum/storage/ permissions have been fixed.\n\n"
            "NomadNet will use the system config (same as rnsd).",
        )
        return True
    except (OSError, PermissionError) as e:
        ctx.dialog.msgbox(
            "Permission Fix Failed",
            f"Could not fix /etc/reticulum/storage permissions:\n"
            f"  {e}\n\n"
            f"Relaunch MeshForge in Admin mode (sudo) to fix permissions."
        )
        return False
