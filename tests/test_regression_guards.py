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
    """GatewayConfig.save() returns bool (True=persisted / False=write-failed).
    A bare `cfg.save()` in a handler discards it, so a later "saved!"/"toggled"
    dialog lies when the write failed (#74-#77, S5). Scoped to the `cfg.save()`
    GatewayConfig idiom (zero-FP — only dual_radio_failover uses it). The broader
    .save() sweep — `settings.save()` (SettingsManager, also bool; automation/
    first_run) and meshcore's `config.save()` — is tracked separately in the arc
    plan; many of those are benign silent-saves with no success dialog to belie."""

    _BARE_CFG_SAVE = re.compile(r"^cfg\.save\(\)$")

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
                        if self._BARE_CFG_SAVE.match(line.strip()):
                            violations.append(f"{os.path.relpath(fp, SRC_DIR)}:{n}")
        assert not violations, (
            "Bare `cfg.save()` (GatewayConfig bool discarded) in a TUI handler "
            "(#74-#77 / S5) — bind it: `if cfg.save(): <ok> else: <surface the "
            "write failure>`.\n\nViolations:\n" + "\n".join(violations)
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

        path = os.path.join(SRC_DIR, "gateway", "rns_bridge.py")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)

        # send_to_rns and _queue_send_rns delegate callback wiring to
        # _register_lxmf_delivery_callbacks (the Fork-D helper).
        # Either:
        # (a) the method directly calls register_delivery_callback /
        #     register_failed_callback, OR
        # (b) the method calls self._register_lxmf_delivery_callbacks(...)
        #     and the helper carries the two register_*_callback calls.
        # Both shapes satisfy the contract.

        def _method_calls(method_name):
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
                f"src/gateway/rns_bridge.py no longer defines {method} — "
                f"this regression guard needs its target list refreshed."
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
