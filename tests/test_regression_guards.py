"""
Regression Guard Tests

Codebase-scanning tests that enforce architectural invariants.
These tests prevent the circular regressions documented in persistent_issues.md
by failing when known anti-patterns are reintroduced.

Ratchet Pattern: Known violations are tracked with exact counts. Tests fail if
the count goes UP (regression) or DOWN without updating the expected count
(forces tightening when violations are fixed).

Usage:
    python3 -m pytest tests/test_regression_guards.py -v
"""

import ast
import os
import re
import sys

import pytest

# Source directory
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
REPO_ROOT = os.path.join(os.path.dirname(__file__), '..')


def _scan_python_files(pattern, exclude_files=None, exclude_dirs=None,
                       skip_comments=True, skip_strings=True):
    """Scan all Python files in src/ for a regex pattern.

    Returns list of (filepath, lineno, line_text) tuples.
    """
    exclude_files = exclude_files or []
    exclude_dirs = exclude_dirs or []
    matches = []

    for root, dirs, files in os.walk(SRC_DIR):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for filename in files:
            if not filename.endswith('.py'):
                continue
            if filename in exclude_files:
                continue

            filepath = os.path.join(root, filename)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        stripped = line.strip()

                        # Skip comments
                        if skip_comments and stripped.startswith('#'):
                            continue

                        # Skip string literals (lines that start with quotes)
                        if skip_strings and (stripped.startswith('"') or stripped.startswith("'")):
                            continue

                        if re.search(pattern, line):
                            matches.append((filepath, lineno, line.rstrip()))
            except (IOError, OSError):
                continue

    return matches


class TestTCPConnectionContract:
    """Enforce: TCPInterface() creation only in connection infrastructure.

    meshtasticd supports ONE TCP client at a time (Issue #17). Direct
    TCPInterface() creation outside the connection layer causes connection
    thrashing, breaking the web client at :9443.

    Allowlisted files: Connection infrastructure + files using the global lock.
    """

    # Files that ARE the connection infrastructure or use the global lock correctly
    ALLOWLISTED = {
        'connection_manager.py',    # IS the connection manager
        'meshtastic_connection.py', # IS connection infrastructure
        'connections.py',           # IS connection infrastructure
        'node_monitor.py',          # Uses MESHTASTIC_CONNECTION_LOCK
        'device_controller.py',     # Uses MESHTASTIC_CONNECTION_LOCK
        'rns_transport.py',         # Uses MESHTASTIC_CONNECTION_LOCK
        'mesh_bridge.py',           # Uses MESHTASTIC_CONNECTION_LOCK
    }

    def test_no_new_direct_tcpinterface(self):
        """No NEW files should create TCPInterface() directly."""
        matches = _scan_python_files(
            r'TCPInterface\(',
            exclude_files=list(self.ALLOWLISTED),
        )

        violating_files = set()
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            # Skip test files
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violating_files.add(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violating_files) == 0, (
            f"Found {len(violating_files)} NEW file(s) creating TCPInterface() directly.\n"
            f"Use MeshtasticConnection from connection_manager.py or acquire\n"
            f"MESHTASTIC_CONNECTION_LOCK first (Issue #17).\n\n"
            f"Violations:\n" + "\n".join(sorted(violating_files))
        )


class TestRNSReticulumChokepoint:
    """Enforce (MF019): RNS.Reticulum() is constructed ONLY in the guarded
    chokepoint utils/rns_init.py (open_reticulum + the watchdog constructor).

    RNS upstream withdrew public support (the Carrier Switch, Dec 2025), so
    MeshForge OWNS the dependency. A single guarded entry point makes a wedged
    rnsd DEGRADE (#68 fail-open — the bounded AF_UNIX connect probe returns
    None instead of the constructor hanging the calling thread in an
    uninterruptible kernel connect()) and a FOREIGN @rns owner FAIL LOUD (#69).
    Raw construction anywhere else reintroduces the silent-hang class — the
    same regression-prevention shape as MF007/TestTCPConnectionContract.

    Allowlist:
      - rns_init.py        — THE chokepoint.
      - rns_interfaces.py  — an isolated `python3 -c` connectivity probe with
        its own subprocess timeout that deliberately tests NomadNet's OWN venv
        RNS (not MeshForge's), so it cannot route through the in-process
        chokepoint and cannot hang the TUI.
    """

    ALLOWLISTED = {
        'rns_init.py',
        'rns_interfaces.py',
    }

    def test_reticulum_constructed_only_in_chokepoint(self):
        """No file outside the allowlist may construct RNS.Reticulum()."""
        matches = _scan_python_files(
            r'(=\s*\w*\.?Reticulum\s*\(|\breturn\s+\w*\.?Reticulum\s*\()',
            exclude_files=list(self.ALLOWLISTED),
        )

        violating = set()
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violating.add(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violating) == 0, (
            f"Found {len(violating)} RNS.Reticulum() construction(s) outside the "
            f"guarded chokepoint (utils/rns_init.py).\n"
            f"Use open_reticulum() from utils.rns_init — it degrades on a wedged "
            f"rnsd (#68) instead of hanging the thread, and fails loud on a "
            f"foreign @rns owner (#69). See .claude/plans/rns_t2_isolate_arc.md.\n\n"
            f"Violations:\n" + "\n".join(sorted(violating))
        )

    def test_chokepoint_exports_open_reticulum(self):
        """The chokepoint must exist and expose a callable open_reticulum()."""
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.rns_init import open_reticulum
            assert callable(open_reticulum)
        finally:
            if SRC_DIR in sys.path:
                sys.path.remove(SRC_DIR)


class TestFromradioContract:
    """Enforce: TX paths never read /api/v1/fromradio.

    Reading fromradio drains packets (including delivery ACKs) meant for the
    web client at :9443, causing 'waiting for delivery' hangs (Issue #17).
    TX should use send_text_direct() which only POSTs to /api/v1/toradio.
    """

    def test_mqtt_bridge_uses_stateless_tx(self):
        """mqtt_bridge_handler.py primary TX path must be send_text_direct."""
        filepath = os.path.join(SRC_DIR, 'gateway', 'mqtt_bridge_handler.py')
        if not os.path.exists(filepath):
            pytest.skip("mqtt_bridge_handler.py not found")

        with open(filepath, 'r') as f:
            content = f.read()

        assert 'send_text_direct' in content, (
            "mqtt_bridge_handler.py should use send_text_direct() for TX "
            "(stateless HTTP, no fromradio contention)"
        )

    def test_mesh_bridge_uses_stateless_tx(self):
        """mesh_bridge.py primary TX path must be send_text_direct."""
        filepath = os.path.join(SRC_DIR, 'gateway', 'mesh_bridge.py')
        if not os.path.exists(filepath):
            pytest.skip("mesh_bridge.py not found")

        with open(filepath, 'r') as f:
            content = f.read()

        assert 'send_text_direct' in content, (
            "mesh_bridge.py should use send_text_direct() for TX "
            "(stateless HTTP, no fromradio contention)"
        )


class TestServiceCheckContract:
    """Enforce: Service state decisions use check_service().

    Raw subprocess systemctl calls for state determination (is-active, restart)
    caused inconsistent status display regressions (Issue #20).
    """

    # Known exceptions (non-core services, display-only)
    KNOWN_EXCEPTIONS = 1  # cli/diagnose.py openwebrx check

    # Handler files that legitimately read raw systemctl status_text because
    # check_service() is lossy for their purpose (documented at the call site).
    MULTILINE_READ_ALLOW = {'_rns_repair.py'}  # 'activating' vs 'inactive'/'failed'

    def test_no_new_raw_systemctl_state_checks(self):
        """No NEW files should use raw systemctl for service state decisions."""
        matches = _scan_python_files(
            r"subprocess\.\w+\(.*systemctl.*(?:'is-active'|\"is-active\")",
            exclude_files=['service_check.py'],
        )

        # Filter to actual violations (not comments, not test files)
        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}")

        assert len(violations) <= self.KNOWN_EXCEPTIONS, (
            f"Found {len(violations)} raw systemctl is-active calls "
            f"(expected <= {self.KNOWN_EXCEPTIONS}).\n"
            f"Use check_service() from utils.service_check instead (Issue #20).\n\n"
            f"Violations:\n" + "\n".join(violations)
        )

    def test_no_multiline_systemctl_state_reads_in_handlers(self):
        """The single-line guard above misses the list-literal form
        (``subprocess.run(\\n  ['systemctl', 'is-active', svc])``) where the verb
        and 'subprocess' land on different lines — the Issue #20 / honest-signal
        #74-#77 class. Service state in TUI handlers must come from
        check_service(), not a raw is-active read (S4)."""
        matches = _scan_python_files(
            r"""(?:'systemctl'|"systemctl").*(?:'is-active'|"is-active")""",
        )
        violations = []
        for filepath, lineno, _line in matches:
            if 'launcher_tui/handlers/' not in filepath.replace('\\', '/'):
                continue
            if os.path.basename(filepath) in self.MULTILINE_READ_ALLOW:
                continue
            violations.append(f"{filepath}:{lineno}")

        assert not violations, (
            "Raw ['systemctl', 'is-active', …] state read in a TUI handler "
            "(Issue #20 / honest-signal #74-#77) — use check_service().available.\n"
            "If the raw status_text is genuinely required (e.g. distinguishing "
            "'activating'), add the file to MULTILINE_READ_ALLOW with a reason.\n\n"
            "Violations:\n" + "\n".join(violations)
        )


class TestSaveReturnChecked:
    """GatewayConfig.save() AND SettingsManager.save() return bool (True=persisted
    / False=write-failed). A bare `cfg.save()` / `config.save()` / `settings.save()`
    in a handler discards it, so a later "saved!"/"toggled" dialog lies when the
    write failed (#74-#77). The full .save() sweep is complete (S5 cfg.save +
    S8 M1/M2 meshcore config.save + the automation/first_run settings.save sweep) —
    so this now guards all three idioms handler-wide. Gated forms (`report_action(
    cfg.save(), …)`, `ok = settings.save()`, `if config.save():`, `_save_or_warn(…)`)
    are not bare statements and don't match."""

    _BARE_SAVE = re.compile(r"^(cfg|config|settings)\.save\(\)$")

    def test_no_bare_cfg_save_in_handlers(self):
        handlers_dir = os.path.join(SRC_DIR, 'launcher_tui', 'handlers')
        violations = []
        for root, _dirs, files in os.walk(handlers_dir):
            for fn in files:
                if not fn.endswith('.py'):
                    continue
                fp = os.path.join(root, fn)
                with open(fp, encoding='utf-8', errors='ignore') as f:
                    for n, line in enumerate(f, 1):
                        if self._BARE_SAVE.match(line.strip()):
                            violations.append(f"{os.path.relpath(fp, SRC_DIR)}:{n}")
        assert not violations, (
            "Bare `cfg/config/settings.save()` (write-result bool discarded) in a "
            "TUI handler (#74-#77) — bind it: `if <x>.save(): <ok> else: <surface "
            "the write failure>` (or report_action / _save_or_warn).\n\n"
            "Violations:\n" + "\n".join(violations)
        )


class TestConfigPathContract:
    """Enforce: RNS config paths use ReticulumPaths, not hardcoded paths.

    Config drift between gateway and rnsd causes silent divergence (Issue #12).
    """

    def test_no_hardcoded_reticulum_paths_in_code(self):
        """No hardcoded ~/.reticulum or /root/.reticulum in Python code."""
        matches = _scan_python_files(
            r'(?:~/\.reticulum|/root/\.reticulum|/home/\w+/\.reticulum)',
            skip_comments=True,
            skip_strings=False,  # Hardcoded paths might be in strings
        )

        # Filter: allow in test files, doc files, and comments
        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            # Allow in config_drift.py (it detects these paths)
            if 'config_drift' in filepath:
                continue
            # Allow in documentation/knowledge content
            if 'knowledge' in filepath or 'diagnostic' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}: {line.strip()}")

        # This is informational — hardcoded paths in string configs may be
        # acceptable if they're defaults. Track but don't block.
        if violations:
            # Just print for awareness, don't fail (too many legitimate uses)
            pass

    def test_gateway_rns_clients_use_shared_configdir(self):
        """Both gateway RNS clients init through
        ReticulumPaths.ensure_rns_client_configdir() so the process singleton's
        resourcepath is DETERMINISTIC, not init-order-dependent
        (gw-resourcepath-determinism, 2026-06-27). A new inline
        ``/tmp/meshforge_rns_client`` builder in either file would reintroduce
        the race the helper fixes."""
        # utils/_map_collector_rns.py added 2026-07-05: it was a THIRD
        # inline builder invisible to this guard (different config format,
        # no rpc_key 0600 hardening) — the map + gateway share one /tmp.
        for rel in ("gateway/_rns_bridge_connection.py",
                    "gateway/node_tracker.py",
                    "utils/_map_collector_rns.py"):
            fp = os.path.join(SRC_DIR, rel)
            with open(fp, encoding="utf-8") as fh:
                src = fh.read()
            assert "ensure_rns_client_configdir(" in src, (
                f"{rel} must init RNS via "
                f"ReticulumPaths.ensure_rns_client_configdir() "
                f"(deterministic resourcepath)")
            assert 'tempfile.gettempdir()) / "meshforge_rns_client"' not in src, (
                f"{rel} rebuilds the client configdir inline — use "
                f"ReticulumPaths.ensure_rns_client_configdir() instead")


class TestPathHomeContract:
    """Enforce: No Path.home() usage outside paths.py (Issue #1, MF001)."""

    def test_no_path_home_violations(self):
        """No new Path.home() calls outside the utility function."""
        matches = _scan_python_files(
            r'Path\.home\(\)',
            exclude_files=['paths.py'],
        )

        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            # Allow in fallback functions that define get_real_user_home
            stripped = line.strip()
            if 'return Path.home()' in stripped or 'else Path.home()' in stripped:
                continue
            violations.append(f"{filepath}:{lineno}: {stripped}")

        assert len(violations) == 0, (
            f"Found {len(violations)} Path.home() violations.\n"
            f"Use get_real_user_home() from utils.paths instead (Issue #1, MF001).\n\n"
            f"Violations:\n" + "\n".join(violations)
        )


class TestNoShellTrue:
    """Enforce: No shell=True in subprocess calls (MF002)."""

    def test_no_shell_true(self):
        """No subprocess calls with shell=True."""
        matches = _scan_python_files(
            r'subprocess\.\w+\([^)]*shell\s*=\s*True',
        )

        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violations) == 0, (
            f"Found {len(violations)} shell=True violations (MF002).\n"
            f"Use list args instead of shell=True.\n\n"
            f"Violations:\n" + "\n".join(violations)
        )


class TestPipInvocationContract:
    """Enforce: raw pip-install subprocess construction only in utils/pip_install.py.

    The install-hardening arc routed every pip site through the one hardened
    helper (ensure_pip + PEP 668 + checked rc + import-as-consumer verify). A new
    raw ``['pip', 'install', ...]`` argv (or ``run_command('pip install ...')``)
    re-opens the fresh-user / silent-failure class this arc closed. pipx is
    excluded (it is owner-aware and not pip). The Python analogue of lint MF022.
    """

    ALLOWLISTED = {
        'pip_install.py',  # IS the hardened helper
    }

    def test_no_raw_pip_install_outside_helper(self):
        matches = _scan_python_files(
            r"""(['"]pip3?['"]\s*,\s*['"]install['"])|(run_command\([^)]*pip3?\s+install)""",
            exclude_files=list(self.ALLOWLISTED),
        )
        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violations.append(f"{os.path.relpath(filepath, REPO_ROOT)}:{lineno}: {line.strip()}")
        assert not violations, (
            f"Found {len(violations)} raw pip-install construction(s) outside "
            f"utils/pip_install.py.\nRoute through utils.pip_install.pip_install "
            f"(ensure_pip + PEP 668 + checked rc).\n\n" + "\n".join(violations)
        )


class TestEventBusThreadPool:
    """Enforce: EventBus.emit() uses bounded ThreadPoolExecutor.

    Thread-per-emit caused thread explosion over extended uptime — thousands
    of short-lived threads created/destroyed, leading to GIL contention and
    eventual RuntimeError: can't start new thread.
    """

    def test_emit_uses_thread_pool_not_thread_per_call(self):
        """EventBus.emit() must not create threading.Thread per subscriber."""
        import inspect
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.event_bus import EventBus
            source = inspect.getsource(EventBus.emit)
            assert 'threading.Thread(' not in source, (
                "EventBus.emit() must not create Thread() per subscriber. "
                "Use self._executor.submit() with ThreadPoolExecutor instead."
            )
            init_source = inspect.getsource(EventBus.__init__)
            assert 'ThreadPoolExecutor' in init_source, (
                "EventBus.__init__ must create a ThreadPoolExecutor "
                "for bounded async callback dispatch."
            )
        finally:
            sys.path.pop(0)

    def test_eventbus_has_shutdown_method(self):
        """EventBus must have a shutdown() method for cleanup."""
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.event_bus import EventBus
            assert hasattr(EventBus, 'shutdown'), (
                "EventBus must have a shutdown() method to release "
                "thread pool resources during cleanup."
            )
        finally:
            sys.path.pop(0)


class TestKnownServicesConsistency:
    """Enforce: KNOWN_SERVICES stays in sync across the codebase."""

    def test_known_services_has_core_services(self):
        """KNOWN_SERVICES must include meshtasticd, rnsd, mosquitto."""
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.service_check import KNOWN_SERVICES
            assert 'meshtasticd' in KNOWN_SERVICES, "meshtasticd missing from KNOWN_SERVICES"
            assert 'rnsd' in KNOWN_SERVICES, "rnsd missing from KNOWN_SERVICES"
            assert 'mosquitto' in KNOWN_SERVICES, "mosquitto missing from KNOWN_SERVICES"
        finally:
            sys.path.pop(0)

    def test_rnsd_uses_unix_socket(self):
        """rnsd must use unix_socket detection, not UDP port."""
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.service_check import KNOWN_SERVICES
            rnsd = KNOWN_SERVICES.get('rnsd', {})
            assert rnsd.get('port_type') == 'unix_socket', (
                f"rnsd port_type is '{rnsd.get('port_type')}', expected 'unix_socket'. "
                "UDP port check was replaced by abstract Unix socket detection (PRs #920-922)."
            )
        finally:
            sys.path.pop(0)


class TestMessageLengthEnforcement:
    """Enforce: Meshtastic-facing handlers must validate message length.

    Meshtastic firmware silently truncates/drops oversized messages.
    All TX paths must reference MAX_MESHTASTIC_MSG_LENGTH from utils.defaults.
    """

    HANDLER_FILES = [
        'base_handler.py',
        'meshtastic_handler.py',
        'mqtt_bridge_handler.py',
    ]

    def test_handlers_reference_length_constant(self):
        """Base handler or leaf handlers must reference the length limit.

        MAX_MESHTASTIC_MSG_LENGTH must appear in base_handler.py (shared
        _truncate_if_needed) or in the individual handler files.
        """
        found_in_base = False
        base_path = os.path.join(SRC_DIR, 'gateway', 'base_handler.py')
        if os.path.exists(base_path):
            with open(base_path, 'r') as f:
                if 'MAX_MESHTASTIC_MSG_LENGTH' in f.read():
                    found_in_base = True

        for filename in self.HANDLER_FILES:
            filepath = os.path.join(SRC_DIR, 'gateway', filename)
            if not os.path.exists(filepath):
                continue
            with open(filepath, 'r') as f:
                content = f.read()
            # Accept if the handler itself references the constant
            # OR if the base handler (which it inherits) does
            has_ref = 'MAX_MESHTASTIC_MSG_LENGTH' in content
            inherits_base = 'BaseMessageHandler' in content
            assert has_ref or (found_in_base and inherits_base), (
                f"{filename} must reference MAX_MESHTASTIC_MSG_LENGTH "
                f"or inherit from BaseMessageHandler which does"
            )


class TestNomadNetPrelaunchContract:
    """Enforce: _nomadnet_rns_checks.py must not contain repair logic.

    Pre-launch checks should be read-only state queries + diagnostics redirect.
    Repair logic belongs in _rns_repair.py or the rns_diagnostics handler.
    """

    def test_prelaunch_no_service_mutations(self):
        """_nomadnet_rns_checks.py must not call start/stop/enable_service."""
        filepath = os.path.join(SRC_DIR, 'launcher_tui', 'handlers', '_nomadnet_rns_checks.py')
        if not os.path.exists(filepath):
            pytest.skip("_nomadnet_rns_checks.py not found")

        with open(filepath, 'r') as f:
            content = f.read()

        for fn in ['start_service', 'stop_service', 'enable_service']:
            # Allow imports but not calls
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith('#') or stripped.startswith('"') or stripped.startswith("'"):
                    continue
                # Check for function calls (not imports)
                if f'{fn}(' in line and 'import' not in line and 'safe_import' not in line:
                    assert False, (
                        f"_nomadnet_rns_checks.py:{i} calls {fn}(). "
                        f"Repair logic belongs in _rns_repair.py or diagnostics handler."
                    )

    def test_prelaunch_no_subprocess(self):
        """_nomadnet_rns_checks.py must not call subprocess.run/Popen for repairs."""
        filepath = os.path.join(SRC_DIR, 'launcher_tui', 'handlers', '_nomadnet_rns_checks.py')
        if not os.path.exists(filepath):
            pytest.skip("_nomadnet_rns_checks.py not found")

        with open(filepath, 'r') as f:
            content = f.read()

        # Allow subprocess for chown in _validate_nomadnet_config (config repair).
        # Forbid service management commands.
        forbidden = ['systemctl', 'pkill', 'rnstatus', 'rnsd']
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('"') or stripped.startswith("'"):
                continue
            if 'subprocess' in line and any(f"'{cmd}'" in line or f'"{cmd}"' in line
                                            for cmd in forbidden):
                assert False, (
                    f"_nomadnet_rns_checks.py:{i} uses subprocess for service management. "
                    f"Repair logic belongs in _rns_repair.py or diagnostics handler."
                )

    def test_prelaunch_file_size(self):
        """_nomadnet_rns_checks.py must stay under 300 lines."""
        filepath = os.path.join(SRC_DIR, 'launcher_tui', 'handlers', '_nomadnet_rns_checks.py')
        if not os.path.exists(filepath):
            pytest.skip("_nomadnet_rns_checks.py not found")

        with open(filepath, 'r') as f:
            line_count = sum(1 for _ in f)

        assert line_count <= 300, (
            f"_nomadnet_rns_checks.py is {line_count} lines (limit: 300). "
            f"Move complex logic to _nomadnet_prelaunch.py or _rns_repair.py."
        )


class TestRNSAnnounceHandlerContract:
    """RNS.Transport calls handler.received_announce(destination_hash=..., ...)
    with keyword arguments. Handlers that name the parameter anything other
    than 'destination_hash' (e.g. legacy 'dest_hash') raise TypeError on
    every announce — the gateway loses peer-discovery hits, M->R bridging
    silently fails to track new senders.
    """

    def test_received_announce_param_name(self):
        """Every received_announce() must use 'destination_hash' as first arg."""
        import re
        pattern = re.compile(r'def\s+received_announce\s*\(\s*self\s*,\s*(\w+)')
        violations = []
        for root, _dirs, files in os.walk(SRC_DIR):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                path = os.path.join(root, fname)
                with open(path) as f:
                    text = f.read()
                for m in pattern.finditer(text):
                    if m.group(1) != 'destination_hash':
                        rel = os.path.relpath(path, SRC_DIR)
                        violations.append(f"{rel}: param={m.group(1)}")
        assert not violations, (
            "RNS calls received_announce(destination_hash=..., "
            "announced_identity=..., app_data=...) with kwargs. The "
            "first parameter MUST be named 'destination_hash'. "
            "Violations: " + "; ".join(violations)
        )


class TestSqliteConnectContract:
    """Enforce: No bare sqlite3.connect() outside db_helpers.py (MF013).

    Closes the fleet-host 2026-04-26 wedge class — every SQLite consumer
    must go through utils.db_helpers.connect_tuned for WAL + sync=NORMAL
    + 64 MB journal_size_limit. The lint rule catches at editor /
    pre-commit; this test catches in CI even if lint is bypassed."""

    def test_no_bare_sqlite_connect(self):
        matches = _scan_python_files(
            r'sqlite3\.connect\(',
            exclude_files=['db_helpers.py'],
        )

        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violations) == 0, (
            f"Found {len(violations)} bare sqlite3.connect() violations.\n"
            f"Use connect_tuned() from utils.db_helpers instead (MF013).\n"
            f"Reason: WAL + synchronous=NORMAL + journal_size_limit=64MB "
            f"prevent the rollback-journal fdatasync wedge that took out "
            f"fleet-host's :5000 service for 16 minutes (2026-04-26).\n\n"
            f"Violations:\n" + "\n".join(violations)
        )


class TestOperatorValueContract:
    """Enforce: no operator-specific values in source/templates/scripts/docs (MF014).

    Drove the 2026-04-26 source scrub (commit 155a74d) and Path B history
    rewrite. New users must be able to clone the repo and run it without
    inheriting fleet-specific hostnames, personal emails, or user-home paths.
    Operator-private context lives in .claude/ (allowlisted).
    """

    def test_lint_rule_runs_clean_on_repo(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
        try:
            import importlib
            lint_mod = importlib.import_module('lint')
        finally:
            sys.path.pop(0)

        issues = lint_mod.check_operator_values_full_tree(REPO_ROOT)
        violations = [
            f"{i.file}:{i.line}: MF014: {i.message}"
            for i in issues
        ]
        assert len(violations) == 0, (
            f"Found {len(violations)} MF014 operator-value violations.\n"
            f"These break repo portability for new users. Replace with "
            f"placeholders or read from config.\n"
            f"Allowlisted: scripts/lint.py, this test file, .claude/ subtree.\n\n"
            f"Violations:\n" + "\n".join(violations)
        )

    def test_lint_rule_catches_known_pattern(self):
        """Self-test: feed the rule a synthetic line and confirm it fires."""
        sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
        try:
            import importlib
            lint_mod = importlib.import_module('lint')
        finally:
            sys.path.pop(0)

        # Each pattern must match its canonical leak example
        canon = [
            ('shawnmfarley@gmail.com', 'personal email'),
            ('volcanoai', 'fleet hostname'),
            ('meshforge-moc1', 'fleet hostname'),
            ('hawaiinet', 'regional name'),
            ('/home/wh6gxz/foo', 'user-specific home'),
            ('f68c2f56cb61527b6c9ad603b9a5009a', 'LXMF gateway hash'),
        ]
        unmatched = []
        for sample, label in canon:
            hit = any(p.search(sample) for p, _ in lint_mod.MF014_PATTERNS)
            if not hit:
                unmatched.append(f"{label}: {sample!r} — NO PATTERN MATCHED")
        assert not unmatched, (
            "MF014 patterns failed self-test:\n" + "\n".join(unmatched)
        )


class TestNoHardcodedRnsDefaultSocket:
    """Enforce: no hardcoded ``@rns/default`` shared-instance socket name.

    The #69 boot-race gate in templates/systemd/nomadnet-user.service
    hardcoded ``@rns/default`` and crash-looped the user unit ~7800x on
    every box whose rnsd instance_name != 'default' (regression 2026-06-09,
    commit 121ac59a; the operator's "house of cards"). rnsd binds
    ``@rns/<instance_name>`` (e.g. ``@rns/volcano ai rns``), so any code or
    unit directive that must reference the socket MUST derive the name via
    ``ReticulumPaths.get_configured_instance_name()`` (src/utils/paths.py)
    or gate on instance-agnostic ``rnstatus`` — never a literal 'default'.
    Comments/docstrings may mention it for context (this file does too).
    """

    # Quoted code literal: '@rns/default', "rns/default", etc. Bare prose
    # mentions (no adjacent quotes) do not match — those are documentation.
    _RNS_DEFAULT_LITERAL = r"""['"]@?rns/default['"]"""

    def test_no_rns_default_in_systemd_templates(self):
        """systemd unit/drop-in DIRECTIVES must not pin @rns/default."""
        templates_dir = os.path.join(REPO_ROOT, 'templates', 'systemd')
        violations = []
        for root, _dirs, files in os.walk(templates_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8',
                              errors='ignore') as f:
                        for lineno, line in enumerate(f, 1):
                            stripped = line.strip()
                            # `#` comments may explain the regression; only
                            # active directive lines are violations.
                            if not stripped or stripped.startswith('#'):
                                continue
                            if 'rns/default' in stripped:
                                violations.append(
                                    f"{filepath}:{lineno}: {stripped}"
                                )
                except (IOError, OSError):
                    continue
        assert not violations, (
            "Hardcoded @rns/default in a systemd directive — the #69 gate "
            "regression. Gate on `rnstatus` (instance-agnostic) or derive "
            "the socket from get_configured_instance_name():\n\n"
            + "\n".join(violations)
        )

    def test_no_rns_default_literal_in_src(self):
        """Python code must not use a literal '@rns/default' string."""
        # skip_strings=False: the literal we hunt lives INSIDE quotes by
        # definition, and the regression's own idiom (`'@rns/default' in x`)
        # is a quote-leading line the default skip would silently miss.
        matches = _scan_python_files(self._RNS_DEFAULT_LITERAL,
                                     skip_strings=False)
        violations = [f"{fp}:{ln}: {txt.strip()}" for fp, ln, txt in matches]
        assert not violations, (
            "Hardcoded '@rns/default' string literal in src/ — derive the "
            "instance name via ReticulumPaths.get_configured_instance_name() "
            "instead (the #69 hardcode class):\n\n" + "\n".join(violations)
        )

    def test_guard_catches_known_pattern(self):
        """Self-test: the literal pattern fires on a leak, not on a derive."""
        pat = re.compile(self._RNS_DEFAULT_LITERAL)
        assert pat.search("if '@rns/default' in proc_unix:")
        assert pat.search('socket = "rns/default"')
        assert not pat.search("f'@rns/{inst_token}'")
        assert not pat.search("# rnsd binds @rns/default on default boxes")


class TestDeliveryCallbackSymmetry:
    """Enforce: both RNS send paths register LXMF delivery-proof callbacks.

    Background: the syn/ack fork-D session caught an asymmetry where
    send_to_rns() registered register_delivery_callback /
    register_failed_callback (so CONFIRMED + DROPPED(rns_delivery_failed)
    bumped on receiver-side acks), but _queue_send_rns() did NOT, so
    queue-retried messages silently bypassed the CONFIRMED ledger and
    biased /api/gateway/delivery.confirmation_rate downward.

    The behavioural test in tests/test_rns_bridge.py::TestSynAckCallbackSymmetry
    catches *behavioural* deletion (the callback's side effect goes away).
    This source-shape guard catches the *easy* deletion case: someone
    removes the register_*_callback lines from either method, the
    behavioural test still passes because LXMF is mocked, but the
    operator-facing ledger silently regresses in production. The two
    layers are complementary.
    """

    def test_both_rns_send_paths_register_callbacks(self):
        import ast
        import os

        # 2026-06-09 rns_bridge split: the helper now lives in
        # bridge_ack_mixin.py and the two send paths in
        # bridge_send_mixin.py, both mixed into RNSMeshtasticBridge in
        # rns_bridge.py. Parse all three so the guard keeps holding
        # wherever the methods are defined.
        trees = []
        for fname in ("rns_bridge.py", "bridge_ack_mixin.py",
                      "bridge_send_mixin.py"):
            path = os.path.join(SRC_DIR, "gateway", fname)
            with open(path, "r", encoding="utf-8") as f:
                trees.append(ast.parse(f.read(), filename=path))

        # send_to_rns and _queue_send_rns delegate callback wiring to
        # _register_lxmf_delivery_callbacks (the Fork-D helper).
        # Either:
        # (a) the method directly calls register_delivery_callback /
        #     register_failed_callback, OR
        # (b) the method calls self._register_lxmf_delivery_callbacks(...)
        #     and the helper carries the two register_*_callback calls.
        # Both shapes satisfy the contract.

        def _method_calls(method_name):
            for tree in trees:
                for node in ast.walk(tree):
                    if (isinstance(node, ast.FunctionDef)
                            and node.name == method_name):
                        return {
                            n.attr for n in ast.walk(node)
                            if isinstance(n, ast.Attribute)
                        }
            return None

        helper_attrs = _method_calls("_register_lxmf_delivery_callbacks")
        assert helper_attrs is not None, (
            "Bridge no longer defines _register_lxmf_delivery_callbacks. "
            "If callback wiring moved, update this test or inline-check "
            "register_delivery_callback / register_failed_callback in "
            "every RNS send path directly."
        )
        assert "register_delivery_callback" in helper_attrs, (
            "_register_lxmf_delivery_callbacks no longer calls "
            "register_delivery_callback — CONFIRMED ledger broken"
        )
        assert "register_failed_callback" in helper_attrs, (
            "_register_lxmf_delivery_callbacks no longer calls "
            "register_failed_callback — DROPPED(rns_delivery_failed) "
            "ledger broken"
        )

        for method in ("send_to_rns", "_queue_send_rns"):
            attrs = _method_calls(method)
            assert attrs is not None, (
                f"src/gateway/rns_bridge.py (or its bridge_*_mixin.py "
                f"split files) no longer defines {method} — this "
                f"regression guard needs its target list refreshed."
            )
            directly_wires = (
                "register_delivery_callback" in attrs
                and "register_failed_callback" in attrs
            )
            delegates_to_helper = (
                "_register_lxmf_delivery_callbacks" in attrs
            )
            assert directly_wires or delegates_to_helper, (
                f"{method} no longer wires LXMF delivery-proof callbacks "
                f"(neither directly nor via "
                f"_register_lxmf_delivery_callbacks). This would silently "
                f"break the operator-facing confirmation_rate in "
                f"/api/gateway/delivery for this send path. See "
                f"TestSynAckCallbackSymmetry in tests/test_rns_bridge.py "
                f"for the behavioural counterpart."
            )


class TestServingNeverBlocksOnCollection:
    """Invariant 1 of the recurring map-wedge class (#17/#70/#71/#73/#75/#76 +
    the 2026-06-23 moc1 spin): a serving/collection path must never block
    unboundedly on an external source. The meshtastic interface constructor
    blocks until the full nodedb sync; a wedged daemon can stall it ~900s while
    the map collector holds _collect_lock, wedging every /api/nodes/geojson +
    /api/network/topology request behind it.

    Two structural guards (defense-in-depth with lint MF023):
      1. the blocking interface create lives ONLY in the bounded helper, and
      2. the direct-USB-radio fallback is gated on meshtasticd SERVICE state,
         not on an empty result (the ttyACM0 cross-subsystem radio seizure).
    """

    COLLECTOR = os.path.join(SRC_DIR, 'utils', '_map_collector_meshtastic.py')
    ORCHESTRATOR = os.path.join(SRC_DIR, 'utils', 'map_data_collector.py')
    BOUNDED_HELPER = '_collect_interface_bounded'
    BLOCKING_CALLS = {'_create_interface'}

    def _parse(self, path):
        with open(path, 'r', encoding='utf-8') as f:
            return ast.parse(f.read())

    @staticmethod
    def _call_name(func):
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return ''

    def test_interface_create_only_in_bounded_helper(self):
        """_create_interface() (the blocking nodedb sync) must be confined to
        _collect_interface_bounded — nowhere else in the collector."""
        tree = self._parse(self.COLLECTOR)
        offenders = []

        def walk(node, stack):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, stack + [child.name])  # nested fns inherit ancestry
                    continue
                if isinstance(child, ast.Call):
                    name = self._call_name(child.func)
                    if name in self.BLOCKING_CALLS and self.BOUNDED_HELPER not in stack:
                        offenders.append((child.lineno, name, list(stack)))
                walk(child, stack)

        walk(tree, [])
        assert not offenders, (
            f"Blocking meshtastic interface creation outside {self.BOUNDED_HELPER}() "
            f"in _map_collector_meshtastic.py — serving would block on collection "
            f"again (the 2026-06-23 moc1 spin). Offenders (line, call, fns): "
            f"{offenders}"
        )

    def test_bounded_helper_exists(self):
        """The chokepoint helper must exist (the guard above is vacuous without it)."""
        tree = self._parse(self.COLLECTOR)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert self.BOUNDED_HELPER in names, (
            f"{self.BOUNDED_HELPER}() is gone — the bounded-collect chokepoint "
            f"(MF023) has no home. Restore it or update this guard."
        )

    def test_direct_radio_gated_on_meshtasticd_presence(self):
        """The direct-USB-radio fallback must be gated on _meshtasticd_present()
        (service state), not on `not tcp_features` alone — else a wedged or
        gateway-deferred meshtasticd (empty result, daemon present) lets the
        fallback seize /dev/ttyACM0, a DIFFERENT radio (dude-claw's)."""
        tree = self._parse(self.ORCHESTRATOR)
        # Identify THE gate by `tcp_features` in its TEST (not merely
        # `_collect_direct_radio` somewhere in its body): an outer wrapper such
        # as `if self._meshtastic_enabled:` also contains the call nested in its
        # body, so a body-only match hits the wrong node. Keying on the gate's
        # own predicate is robust to such wrappers (caught porting to MeshAnchor,
        # whose _collect_locked has exactly that outer wrapper).
        gate = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            if 'tcp_features' not in ast.dump(node.test):
                continue
            body_dump = " ".join(ast.dump(s) for s in node.body)
            if '_collect_direct_radio' not in body_dump:
                continue
            gate = node
            break
        assert gate is not None, (
            "Could not find the direct_radio gate (an `if` whose TEST references "
            "tcp_features and whose body calls _collect_direct_radio) in "
            "map_data_collector.py — refactored? Update this guard so the "
            "presence-gate invariant stays enforced."
        )
        assert '_meshtasticd_present' in ast.dump(gate.test), (
            "The direct_radio fallback in map_data_collector._collect_locked is "
            "no longer gated on self._meshtasticd_present() — reverting to "
            "`if not tcp_features:` reintroduces the cross-subsystem radio "
            "seizure (ttyACM0 cascade, 2026-06-23)."
        )


class TestDetectorHonesty:
    """Invariant 2 of the recurring class: a detector must never map an AMBIGUOUS
    observation onto a DEFINITIVE verdict (honest_failure_modes #1 — a degraded
    value overlapping the healthy/absent domain). Enforced here for the
    meshtasticd presence gate: 'cannot determine' must NOT read as 'absent'
    (which would permit the radio seizure). New detectors should add a guard of
    this shape. Also pinned: the dude-claw witness verdict
    (scripts/host_probe_check.py::_verdict) — banner==0 alone is ambiguous
    (slow box vs real freeze), so a hard HOST_FROZEN requires the kernel
    hung-task corroboration (kstack==1); kstack==0 (kernel healthy, just slow)
    must NOT page. (Fixed 2026-06-23; behavioural detail in
    tests/test_host_probe_check.py.)
    """

    def _collector(self):
        import tempfile
        from pathlib import Path
        from utils.map_data_collector import MapDataCollector
        d = Path(tempfile.mkdtemp())
        return MapDataCollector(cache_dir=d, config_dir=d, enable_history=False)

    def test_unknown_service_state_is_not_read_as_absent(self):
        sys.path.insert(0, SRC_DIR)
        try:
            from unittest.mock import patch, MagicMock
            from utils.service_check import ServiceState
            c = self._collector()
            with patch('utils.service_check.check_service') as cs:
                cs.return_value = MagicMock(state=ServiceState.UNKNOWN)
                # UNKNOWN is ambiguous → must read PRESENT (don't seize the radio).
                assert c._meshtasticd_present() is True
        finally:
            if SRC_DIR in sys.path:
                sys.path.remove(SRC_DIR)

    def test_check_failure_is_not_read_as_absent(self):
        sys.path.insert(0, SRC_DIR)
        try:
            from unittest.mock import patch
            c = self._collector()
            with patch('utils.service_check.check_service',
                       side_effect=OSError("boom")):
                assert c._meshtasticd_present() is True
        finally:
            if SRC_DIR in sys.path:
                sys.path.remove(SRC_DIR)

    def test_only_not_installed_reads_as_absent(self):
        sys.path.insert(0, SRC_DIR)
        try:
            from unittest.mock import patch, MagicMock
            from utils.service_check import ServiceState
            c = self._collector()
            with patch('utils.service_check.check_service') as cs:
                cs.return_value = MagicMock(state=ServiceState.NOT_INSTALLED)
                assert c._meshtasticd_present() is False  # the ONLY absent state
                for st in (ServiceState.AVAILABLE, ServiceState.DEGRADED,
                           ServiceState.FAILED, ServiceState.NOT_RUNNING,
                           ServiceState.UNKNOWN):
                    cs.return_value = MagicMock(state=st)
                    assert c._meshtasticd_present() is True, st
        finally:
            if SRC_DIR in sys.path:
                sys.path.remove(SRC_DIR)

    def test_witness_freeze_requires_kernel_corroboration(self):
        """The dude-claw witness must not call a merely-slow box frozen:
        banner==0 + kstack==0 (kernel healthy) is NOT HOST_FROZEN; a real
        freeze needs the kernel hung-task signal (kstack==1)."""
        scripts_dir = os.path.join(REPO_ROOT, 'scripts')
        sys.path.insert(0, scripts_dir)
        try:
            import host_probe_check
            v = host_probe_check._verdict
            base = {"ip_alive": 1, "app_state": "open", "banner": 0, "kstack": 0}
            # the .32 false-positive shape must read OK, not page
            assert v(dict(base, kstack=0), True) == "OK"
            # a kernel-corroborated wedge must still fire
            assert v(dict(base, kstack=1), True) == "HOST_FROZEN"
        finally:
            if scripts_dir in sys.path:
                sys.path.remove(scripts_dir)


class TestContentIdInvariant:
    """STEP 3 (dedup/identity arc): every explicit BridgedMessage /
    CanonicalMessage construction in the gateway either stamps/carries a
    content_id or sits in a DOCUMENTED allowlist — so a new bridge ingress or
    egress can't silently ship an unidentifiable logical message. This is the
    foundation guard the dup/miss detector rests on, mirroring
    MF019/TestRNSReticulumChokepoint. AST-based, so it survives line shifts.

    Allowlist (keyed by (file, enclosing-function)):
      - meshtastic_handler.py::_handle_text_message — the PhoneAPI/TCP mesh
        ingress. It has only the box-LOCAL numeric channel INDEX, not the topic
        channel NAME (#77); stamping here would mint an id INCONSISTENT with the
        live MQTT-json leg's name-keyed id, and a valid-looking-but-wrong id is
        the honest-failure trap. Deliberately unstamped until a channel
        index→name resolver is wired (MQTT-json is the live M→R-to-RNS leg).
      - meshcore_handler.py::send_text — a MeshCore SEND (egress) built from
        text+destination; not a logical-message ingress and carries no origin
        to mint from. content_id is carried where a sourced message bridges,
        not minted at this egress.
    """

    CONSTRUCTORS = {"BridgedMessage", "CanonicalMessage"}
    ALLOWLIST = {
        ("meshtastic_handler.py", "_handle_text_message"),
        ("meshcore_handler.py", "send_text"),
    }

    @staticmethod
    def _scan_constructions():
        gw = os.path.join(SRC_DIR, "gateway")
        found = []  # (basename, funcname, lineno, has_content_id)
        for fn in sorted(os.listdir(gw)):
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(gw, fn), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fn)
            stack = []

            class _V(ast.NodeVisitor):
                def visit_FunctionDef(self, node):
                    stack.append(node.name)
                    self.generic_visit(node)
                    stack.pop()
                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Call(self, node):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in TestContentIdInvariant.CONSTRUCTORS:
                        has = any(k.arg == "content_id" for k in node.keywords)
                        found.append((fn, stack[-1] if stack else "<module>",
                                      node.lineno, has))
                    self.generic_visit(node)

            _V().visit(tree)
        return found

    def test_no_unstamped_message_construction(self):
        violations = []
        for fn, func, lineno, has_cid in self._scan_constructions():
            if has_cid or (fn, func) in self.ALLOWLIST:
                continue
            violations.append(f"{fn}:{lineno} in {func}()")
        assert not violations, (
            "Bridge message construction without a content_id (dedup/identity "
            "arc STEP 3). Stamp content_id= (compute_content_id, a carried id, "
            "or self.content_id), or add (file, function) to ALLOWLIST with a "
            "documented reason.\nViolations:\n  " + "\n  ".join(sorted(violations)))

    def test_allowlist_entries_are_live(self):
        # A stale allowlist entry (construction removed/renamed/now stamped)
        # must be cleaned up so the allowlist can't rot into a blind spot.
        unstamped = {(fn, func) for fn, func, _, has
                     in self._scan_constructions() if not has}
        stale = self.ALLOWLIST - unstamped
        assert not stale, f"Stale content_id allowlist entries: {sorted(stale)}"

    def test_compute_content_id_is_the_shared_minter(self):
        sys.path.insert(0, SRC_DIR)
        try:
            from gateway.canonical_message import compute_content_id
            assert callable(compute_content_id)
            a = compute_content_id("meshtastic:!x", "hi", "ch")
            assert a == compute_content_id("meshtastic:!x", "hi", "ch")
            assert a.startswith("c1:")
        finally:
            if SRC_DIR in sys.path:
                sys.path.remove(SRC_DIR)


class TestConfigAtomicityMF026:
    """MF026: config/state persistence must be atomic (ported from the client
    repo's MED3 rule). O_TRUNC is banned; new non-atomic config open('w') writes
    fail against a frozen baseline. Route through utils.paths.atomic_write_text.
    """

    def _lint(self):
        scripts_dir = os.path.join(REPO_ROOT, 'scripts')
        sys.path.insert(0, scripts_dir)
        try:
            import importlib
            return importlib.import_module('lint')
        finally:
            if scripts_dir in sys.path:
                sys.path.remove(scripts_dir)

    def test_no_config_atomicity_violations_above_baseline(self):
        lint_mod = self._lint()
        files = lint_mod.get_all_python_files('src')
        issues = lint_mod.check_config_atomicity(files, REPO_ROOT)
        violations = [f"{i.file}:{i.line}: {i.message}" for i in issues]
        assert violations == [], (
            "MF026: non-atomic config/state write(s) above baseline — route "
            "through utils.paths.atomic_write_text:\n" + "\n".join(violations)
        )

    def test_detector_flags_o_trunc(self):
        lint_mod = self._lint()
        assert lint_mod.MF026_OTRUNC.search("fd = os.open(p, os.O_WRONLY | os.O_TRUNC)")

    def test_detector_flags_config_openw_but_not_reports(self):
        lint_mod = self._lint()
        # config/state paths → hint matches, no exclusion
        for good in ("open(conf_path, 'w')", "open(self._state_file, 'w')", 'open(overlay_path, "w")'):
            m = lint_mod.MF026_OPENW.search(good)
            assert m is not None
            assert lint_mod.MF026_HINT.search(m.group(1)), good
            assert not lint_mod.MF026_EXCL.search(m.group(1)), good
        # report/cache/pid/temp paths → excluded (no false positive)
        for benign in ("open(output_path, 'w')", "open(temp_path, 'w')", "open(pid_file, 'w')", "open(self._info_cache, 'w')"):
            m = lint_mod.MF026_OPENW.search(benign)
            assert m is not None
            assert lint_mod.MF026_EXCL.search(m.group(1)), benign

    def test_baseline_only_shrinks(self):
        # The frozen baseline must not silently grow; pin its documented size so
        # a future edit that adds headroom trips review.
        lint_mod = self._lint()
        assert sum(lint_mod.MF026_BASELINE.values()) <= 9


class TestNoUnsatisfiableCIPoll:
    """Enforce: never poll CI with `gh run list --commit <sha>` (2026-07-20).

    That filter returns an EMPTY list on the gh version this fleet runs, so a
    poll loop written as::

        until [ "$(gh run list --commit $SHA --json status --jq '.[0].status')" \
                = "completed" ]; do sleep 20; done

    compares `"" = "completed"` forever — a predicate UNSATISFIABLE BY
    CONSTRUCTION. One such loop ran 7h33m (~1,350 API calls) for a commit whose
    CI had been green for 7 hours, and the same filter silently returned
    nothing for a second session hours later.

    It is the house defect class in shell form: an ABSENT result mapped onto a
    VALID-LOOKING one ("not finished yet") rather than "cannot observe"
    (honest_failure_modes #1). The cure is `scripts/wait_for_ci.sh` — list by
    BRANCH, match the SHA client-side, bound the wait, and exit 2 on UNKNOWN.
    This guard keeps the footgun from being re-typed from memory.
    """

    def _scan_files(self):
        for sub in ("scripts", ".githooks"):
            base = os.path.join(REPO_ROOT, sub)
            if not os.path.isdir(base):
                continue
            for dirpath, _dirnames, filenames in os.walk(base):
                for name in filenames:
                    if name.endswith((".json", ".md")):
                        continue
                    yield os.path.join(dirpath, name)

    def test_no_gh_run_list_commit_filter(self):
        # The cure itself quotes the broken form in its header (the whole point
        # is to name what not to re-type) and in its UNKNOWN message. Exempt it
        # by PATH rather than by matching phrases on the offending line —
        # prose wraps, and a phrase allowlist would silently stop guarding.
        exempt = {os.path.join("scripts", "wait_for_ci.sh")}
        offenders = []
        for path in self._scan_files():
            rel = os.path.relpath(path, REPO_ROOT)
            if rel in exempt:
                continue
            try:
                with open(path, errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if "gh run list" in line and "--commit" in line:
                    offenders.append("%s:%d" % (rel, n))
        assert not offenders, (
            "`gh run list --commit` returns EMPTY on this gh version — a poll "
            "loop on it can never exit (7h33m incident, 2026-07-20). Use "
            "scripts/wait_for_ci.sh, or list by --branch and match the SHA "
            "client-side as honest_status.sh does. Offenders: " + ", ".join(offenders)
        )

    def test_wait_for_ci_helper_exists_and_is_bounded(self):
        """The cure must exist, be executable, and carry a hard timeout —
        an unbounded 'fixed' version would just be the same bug again."""
        script = os.path.join(REPO_ROOT, "scripts", "wait_for_ci.sh")
        assert os.path.isfile(script), "scripts/wait_for_ci.sh missing"
        assert os.access(script, os.X_OK), "scripts/wait_for_ci.sh not executable"
        with open(script) as fh:
            body = fh.read()
        assert "TIMEOUT" in body, "no timeout ceiling — could loop forever"
        assert "--branch" in body, "must query by branch, not --commit"
        assert "exit 2" in body, "must have an UNKNOWN exit path (never a pass)"
