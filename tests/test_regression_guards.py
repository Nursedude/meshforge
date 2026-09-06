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

# Source directory.
#
# ⚠️ These MUST be absolute/normalised. Un-normalised, they read
# ``<repo>/tests/../src``, so every path produced by ``_scan_python_files``
# literally contained ``/tests/`` — and NINE guards below skip a match with
# ``if 'test_' in basename or '/tests/' in filepath``. That skip therefore
# discarded EVERY match, and those nine guards could not fail no matter what
# landed in src/. Proven by live drill 2026-08-05: a file added to src/ with
# a bare ``shell=True`` AND a bare ``Path.home()`` passed both
# ``TestNoShellTrue`` and ``TestPathHomeContract``. Layer 1 (scripts/lint.py)
# still caught it, so the fleet was not unprotected — but every Layer-2
# contract with no lint twin was inert.
#
# ``TestGuardsAreNotInert`` at the bottom of this file now pins that the
# scanner can actually see src/, so this cannot silently regress.
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _strip_trailing_comment(line):
    """Return the code portion of ``line``, dropping any trailing ``#`` comment.

    Quote-aware, so a ``#`` inside a string literal is not mistaken for the
    start of a comment. Needed because ``skip_comments`` only ever skipped
    lines that BEGIN with ``#``, leaving trailing comments to be scanned as
    though they were code — e.g. the trailing note in auto_review.py's own
    MF001 rule text, which spells the banned call out in prose,
    inside auto_review.py's own rule text was reported as an MF001 violation.
    Prose about a banned pattern is not a use of it.
    """
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == '#':
            return line[:i]
        i += 1
    return line


def _is_test_path(filepath):
    """True if ``filepath`` lives under a directory literally named ``tests``.

    Structural, not a substring match. The guards used to each carry

        if 'test_' in basename or '/tests/' in filepath: continue

    and both halves were defective:

    * ``'/tests/' in filepath`` compared a substring of an ABSOLUTE path. With
      ``SRC_DIR`` un-normalised as ``<repo>/tests/../src`` it matched every
      file in the tree and silently disabled nine contracts for 5.5 months.
      The job is real — ``src/moc_analysis_tool/tests/`` exists — but it must
      be answered from path STRUCTURE relative to SRC_DIR, where no parent
      directory outside src/ can influence the result.
    * ``'test_' in basename`` is a substring too, and it exempted PRODUCTION
      files: ``utils/fleet_test_runner.py`` and, worse, the TUI handler
      ``launcher_tui/handlers/test_gateway_rx.py`` — a handler excused from
      every contract, including the handler-scoped ones, purely because of
      how it is named. Removed outright: a file that ships in src/ obeys the
      contracts, and a genuine exception belongs in a guard's named
      ALLOWLIST where it is visible and justified.
    """
    rel = os.path.relpath(filepath, SRC_DIR)
    return 'tests' in os.path.dirname(rel).split(os.sep)


def _scan_python_files(pattern, exclude_files=None, exclude_dirs=None,
                       skip_comments=True, skip_strings=True):
    """Scan all Python files in src/ for a regex pattern.

    Test-support trees under src/ are excluded HERE, once, so no caller has
    to re-implement that filter — nine hand-rolled copies is how the
    2026-08-05 inertness went unnoticed.

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
            if _is_test_path(filepath):
                continue
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

                        haystack = (_strip_trailing_comment(line)
                                    if skip_comments else line)
                        if re.search(pattern, haystack):
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
    # ⚠️ Every entry must name a file that EXISTS. 'connections.py' and
    # 'device_controller.py' sat here from this file's first commit
    # (c378be1a, 2026-02-23) naming files that were never in the tree —
    # inert while absent, but a standing exemption for any future file that
    # happens to take one of those names. An allowlist is a list of granted
    # exceptions, so an entry that grants nothing is a trap, not a no-op.
    # Removed 2026-08-05 by the guard audit.
    ALLOWLISTED = {
        'connection_manager.py',    # IS the connection manager
        'meshtastic_connection.py', # IS connection infrastructure
        'node_monitor.py',          # Uses MESHTASTIC_CONNECTION_LOCK
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
            violating.add(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violating) == 0, (
            f"Found {len(violating)} RNS.Reticulum() construction(s) outside the "
            f"guarded chokepoint (utils/rns_init.py).\n"
            f"Use open_reticulum() from utils.rns_init — it degrades on a wedged "
            f"rnsd (#68) instead of hanging the thread, and fails loud on a "
            f"foreign @rns owner (#69). See .claude/plans/rns_t2_isolate_arc.md.\n\n"
            f"Violations:\n" + "\n".join(sorted(violating))
        )

    def test_reticulum_not_smuggled_as_a_callable_argument(self):
        """The call-syntax regex above is evadable by passing the CLASS as a
        callable — ``call_boundary("rnsd.attach", RNS.Reticulum, ...)``
        constructs a raw Reticulum with no #68 probe, no #69 preflight, no
        pytest attach backstop, and neither MF019 nor the regex sees it
        because the construction site has no ``Reticulum(``. Found live in
        MeshAnchor's node_tracker (Pri-2 leg-c audit, 2026-08-10) — this leg
        closes the evasion in BOTH repos. A bare ``RNS.Reticulum`` followed
        by ``,`` or ``)`` is a class smuggled into someone else's call;
        attribute access (``RNS.Reticulum.get_instance``) does not match.
        """
        matches = _scan_python_files(
            r'\bRNS\.Reticulum\s*[,)]',
            exclude_files=list(self.ALLOWLISTED),
        )
        violating = sorted(
            f"{filepath}:{lineno}: {line.strip()}"
            for filepath, lineno, line in matches
        )
        assert not violating, (
            "RNS.Reticulum passed as a bare callable — an indirect "
            "construction the chokepoint guards cannot see. Route through "
            "open_reticulum() from utils.rns_init.\n\nViolations:\n"
            + "\n".join(violating)
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

    # Files allowed a raw is-active read, BY NAME and with a reason.
    #
    # Was `KNOWN_EXCEPTIONS = 1`, a numeric budget — which is a free pass for
    # ANY one violation, not for the specific one it was reserved for. Once
    # the scanner started working (2026-08-05) the tree matched this pattern
    # ZERO times, so the whole budget was unused slack: a drill planting a
    # brand-new raw `subprocess.run([... 'is-active' ...])` in src/ PASSED.
    # A named allowlist cannot drift that way — it grants exactly the
    # exception it documents.
    SINGLE_LINE_ALLOW = {
        # Standalone diagnostic: uses check_service() when importable and
        # falls back to raw systemctl only when SERVICE_CHECK_AVAILABLE is
        # False, so it still works on a box where the import is broken —
        # which is exactly when someone runs it.
        'diagnose.py',
    }

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
            if basename in self.SINGLE_LINE_ALLOW:
                continue
            violations.append(f"{filepath}:{lineno}")

        assert not violations, (
            f"Found {len(violations)} raw systemctl is-active call(s).\n"
            f"Use check_service() from utils.service_check instead (Issue #20).\n"
            f"If a raw read is genuinely required, add the file to "
            f"SINGLE_LINE_ALLOW with a reason.\n\n"
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

    ⚠️ ``test_no_hardcoded_reticulum_paths_in_code`` was REMOVED 2026-08-05.
    It collected violations and then did nothing with them::

        if violations:
            # Just print for awareness, don't fail (too many legitimate uses)
            pass

    — no assert, and despite the comment, no print either. It could not fail
    and did not inform, but it counted as a guard and read as coverage. A
    drill planting ``Path('/home/pi/.reticulum/config')`` in src/ passed, and
    always would have; unlike the rest of this file that was never the
    SRC_DIR bug, it was written that way.

    Its premise doesn't survive contact with the tree: the pattern matches 47
    places, nearly all user-facing strings and docstrings ("Edit
    ~/.reticulum/config and ensure 'share_instance = Yes'"). Those are correct
    — the path belongs in prose telling an operator what to edit. There is no
    threshold that separates them from a real hardcode with a line scanner, so
    the honest move is to stop pretending this is a gate.

    What actually enforces this contract: lint MF009 (``RNS.Reticulum()``
    without ``configdir=``), lint MF019 (construction outside the chokepoint),
    ``ReticulumPaths`` as the path SSOT, and the sibling test below — all of
    which have teeth.
    """

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
            violations.append(f"{os.path.relpath(filepath, REPO_ROOT)}:{lineno}: {line.strip()}")
        assert not violations, (
            f"Found {len(violations)} raw pip-install construction(s) outside "
            f"utils/pip_install.py.\nRoute through utils.pip_install.pip_install "
            f"(ensure_pip + PEP 668 + checked rc).\n\n" + "\n".join(violations)
        )


class TestBackupNeverOverwrites:
    """Enforce: backup destinations are reserved, never named-and-written.

    A backup step that can overwrite is not a backup step. On 2026-08-05 a
    session reusing the fleet's documented generic
    ``~/delivery_db_backup_<date>/`` path destroyed the same day's MeshForge
    ``delivery_counters.db`` backup — ``sqlite3.Connection.backup()`` opened
    the existing file and overwrote it, and no other copy existed. The safety
    net underneath an authorised deletion is exactly what a backup is for.

    The same class was already shipped in three writers whose collision
    window happened to be one second (``commands/rns.py``,
    ``commands/device_backup.py``, ``commands/fleet_backup.py``). All three
    now reserve through ``utils.backup_paths``. This guard exists so the
    fourth one cannot land as a plain path join.

    ``honest_failure_modes`` #8: fixed shared names are a collision, not a
    convention.
    """

    ALLOWLISTED = {
        'backup_paths.py',  # IS the chokepoint
    }

    def test_backup_writers_reserve_their_destination(self):
        """Any module naming a backup file must import the reserver."""
        matches = _scan_python_files(
            r"""backup_dir\s*/\s*f?['"]|backup_path\s*=\s*\w*\s*/\s*f?['"]""",
            exclude_files=list(self.ALLOWLISTED),
        )
        violations = []
        for filepath, lineno, line in matches:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    source = f.read()
            except OSError:
                continue
            # Importing the reserver is the contract. A module that has it
            # resolves its destinations there; one that does not is naming a
            # backup path by hand.
            if 'reserve_backup_path' in source or 'reserve_backup_dir' in source:
                continue
            violations.append(
                f"{os.path.relpath(filepath, REPO_ROOT)}:{lineno}: {line.strip()}")
        assert not violations, (
            f"Found {len(violations)} hand-named backup destination(s).\n"
            f"Route through utils.backup_paths.reserve_backup_path "
            f"(atomic O_EXCL reservation; refuses or steps aside, never "
            f"overwrites).\n\n" + "\n".join(violations)
        )

    def test_chokepoint_exports_the_reservers(self):
        """The guard above is meaningless if the chokepoint moved."""
        from utils.backup_paths import (  # noqa: F401
            BackupDestinationExists,
            reserve_backup_dir,
            reserve_backup_path,
        )

    def test_reserver_refuses_rather_than_overwrites_by_default(self):
        """Pin the DEFAULT. A future edit flipping it to overwrite-by-default
        would satisfy every other test in this class while re-opening the
        exact defect."""
        import inspect
        from utils.backup_paths import reserve_backup_path
        sig = inspect.signature(reserve_backup_path)
        assert sig.parameters['on_exists'].default == 'refuse'


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


class TestCIPytestVerdictContract:
    """CI's test step must route through the pytest classifier (2026-07-31).

    The interpreter's raw exit code was measured to flap 0 on a failed
    full-suite run (~50%, shutdown race — see scripts/pytest_verdict.sh
    header). CI's conclusion for HEAD is quoted downstream as VERIFIED-tier
    evidence by honest_status and the calibration ledger, so CI trusting the
    bare rc reintroduces the false-green at the highest-authority gate.
    """

    CI_YML = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")

    def _ci_text(self):
        with open(self.CI_YML) as fh:
            return fh.read()

    def test_ci_pytest_step_is_classified(self):
        text = self._ci_text()
        assert "pytest_verdict.sh" in text, (
            "ci.yml no longer calls scripts/pytest_verdict.sh — the test step "
            "is back to trusting pytest's bare exit code, the signal measured "
            "to flap 0 on failed runs (2026-07-28)"
        )

    def test_ci_captures_rc_instead_of_dying_on_it(self):
        """Under GitHub's `bash -e`, the pipeline must not be the step's last
        word: rc has to be captured (`|| rc=$?`) so the classifier — not the
        raw code — decides the step outcome."""
        text = self._ci_text()
        assert re.search(r"tee /tmp/pytest\.log \|\| rc=\$\?", text), (
            "ci.yml pytest pipeline no longer captures rc for the classifier; "
            "under `bash -e` the bare rc would decide the step again"
        )


class TestGuardsAreNotInert:
    """The guards must be able to SEE src/ — a scanner that matches nothing
    passes every contract in this file while enforcing none of them.

    Found 2026-08-05 by live drill. ``SRC_DIR`` was ``<repo>/tests/../src``,
    un-normalised, so every path ``_scan_python_files`` produced literally
    contained ``/tests/`` — and NINE guards skip a match with
    ``if 'test_' in basename or '/tests/' in filepath``. That skip discarded
    EVERY match. A file added to src/ carrying a bare ``shell=True`` and a
    bare home-directory call (MF001) passed both ``TestNoShellTrue`` and
    ``TestPathHomeContract``; Layer 1 (scripts/lint.py) still caught it, so
    the fleet was not unprotected, but every Layer-2 contract without a lint
    twin enforced nothing.

    This is the file's own instance of the defect it exists to prevent:
    a degraded internal state (an empty match set) mapped to a valid-looking
    value (no violations) and read downstream as a real-world claim
    ("the contract holds"). honest_failure_modes #1 — empty is not the same
    as error, and here it was not even the same as clean.
    """

    def test_src_dir_is_normalised(self):
        """The root cause, pinned directly."""
        assert '..' not in SRC_DIR, (
            f"SRC_DIR is un-normalised ({SRC_DIR}); every scanned path will "
            f"contain '/tests/' and the nine guards that skip on '/tests/' "
            f"will discard every match"
        )
        assert os.path.isdir(SRC_DIR)

    def test_scanner_finds_known_source(self):
        """A pattern that certainly exists in src/ must actually be found."""
        matches = _scan_python_files(r'^import \w+|^from \w+ import')
        assert len(matches) > 100, (
            f"scanner found only {len(matches)} import lines in src/ — it is "
            f"not seeing the tree, so every scan-based guard is inert"
        )

    def test_test_path_exclusion_is_structural_not_substring(self):
        """Exclusion must come from path STRUCTURE, never a substring.

        Both halves of the old per-guard filter were substring tests and both
        were wrong: `'/tests/' in filepath` matched the whole tree through an
        un-normalised SRC_DIR, and `'test_' in basename` exempted PRODUCTION
        files. `_is_test_path` answers from directory components relative to
        SRC_DIR, so nothing above src/ can influence it.
        """
        base = os.path.join(SRC_DIR, 'utils')
        # A real tests/ package under src/ is excluded...
        assert _is_test_path(os.path.join(SRC_DIR, 'moc_analysis_tool',
                                          'tests', 'test_thing.py'))
        # ...but a production file merely NAMED like a test is not.
        assert not _is_test_path(os.path.join(base, 'fleet_test_runner.py'))
        assert not _is_test_path(
            os.path.join(SRC_DIR, 'launcher_tui', 'handlers',
                         'test_gateway_rx.py'))
        assert not _is_test_path(os.path.join(base, 'service_check.py'))
        # And the repo's own tests/ dir, one level ABOVE src/, must not make
        # every path look like a test path — the 5.5-month failure exactly.
        assert not _is_test_path(os.path.join(SRC_DIR, 'utils', 'paths.py'))

    def test_production_files_named_like_tests_are_scanned(self):
        """The two files the old substring filter silently excused."""
        scanned = {fp for fp, _, _ in _scan_python_files(r'^import |^from ')}
        for rel in ('utils/fleet_test_runner.py',
                    'launcher_tui/handlers/test_gateway_rx.py'):
            expected = os.path.join(SRC_DIR, *rel.split('/'))
            if os.path.exists(expected):
                assert expected in scanned, (
                    f"{rel} ships in src/ but the scanner skips it — it is "
                    f"exempt from every contract in this file because of how "
                    f"it is NAMED"
                )

    def test_a_planted_violation_is_actually_caught(self):
        """End-to-end: the drill, as a test.

        Writes a file into src/ carrying a banned pattern, asserts the
        scanner reports it, and removes it again. Without this, a future
        refactor can quietly return the guards to matching nothing and every
        other test here would still be green.
        """
        drill = os.path.join(SRC_DIR, 'utils', '_guard_selftest_tmp.py')
        try:
            with open(drill, 'w', encoding='utf-8') as fh:
                fh.write(
                    '"""Transient guard self-test file."""\n'
                    'import subprocess\n\n\n'
                    'def planted(cmd):\n'
                    '    return subprocess.run(cmd, shell=True, timeout=5)\n'
                )
            matches = _scan_python_files(r'shell\s*=\s*True')
            hits = [fp for fp, _, _ in matches if fp.endswith('_guard_selftest_tmp.py')]
            assert hits, (
                "a file planted in src/ with a bare `shell=True` was NOT seen "
                "by the scanner — the guards in this file are inert"
            )
        finally:
            if os.path.exists(drill):
                os.remove(drill)


# ─────────────────────────────────────────────────────────────────────
# TestTriStateHelperContract — the class-sweep guard (2026-08-07)
# ─────────────────────────────────────────────────────────────────────


def _core_flat_status_pairs():
    """Flat helpers in watchdog_probe_core that HAVE a tri-state sibling.

    Derived from the source, never hardcoded: any ``def X_status(`` whose
    ``def X(`` also exists is a pair, so a future tri-state split is covered
    the day it lands without anyone remembering to update this list
    (honest_failure_modes #5 — one constant, derived).
    """
    core = os.path.join(SRC_DIR, 'utils', 'watchdog_probe_core.py')
    with open(core, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)}
    return {name[:-len('_status')] for name in defined
            if name.endswith('_status') and name[:-len('_status')] in defined}


def _names_referenced(path):
    """Every bare name a module imports or references, via AST.

    Deliberately NOT a substring scan: a ``grep`` answers "does this byte
    sequence appear", which is a different question and the exact trap this
    arc kept falling into (2026-08-07). Docstrings and comments mentioning a
    helper by name must NOT count as using it — several modules discuss the
    flat form in prose explaining why they moved off it.
    """
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _names_called(path):
    """Names actually CALLED in a module — ``f(...)``, not merely imported.

    Distinct from :func:`_names_referenced` on purpose. For the contract
    below, an unused *import* of a flat helper is still a violation (it is
    the footgun sitting on the module's surface, and two such dead imports
    were removed on 2026-08-07). But for judging whether an ALLOWLIST entry
    is stale, an import is not enough — a lingering dead import would keep a
    dead entry looking alive forever, which is the very "absence read as
    presence" shape this file guards. Proven by drill: deleting the
    allowlisted CALL left the stale-entry check green until this split.
    """
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
    return called


class TestTriStateHelperContract:
    """A probe module must not use a FLAT helper when its tri-state sibling
    exists — the ``inert`` vs ``indeterminate`` collapse, guarded.

    Why this exists (2026-08-07). The same defect was fixed three times in
    three days, each fix correct and each stopping at the instance it was
    reported for:

      08-05  ``_journal_newest_match_status`` written, ``mqtt_root_drift``
             converted — and ``channel_feed_dark``, in the same file family,
             left on the flat shim.
      08-07  ``channel_feed_dark`` converted (4 boxes had read blind for
             weeks) — and ``_read_deployment_declaration`` still collapsed
             absent-vs-unreadable, leaving meshanchor-server permanently
             indeterminate on two classes.
      08-07  that fixed too, found only because the operator said "grep for
             other callers of the flat shim".

    The rule "a fix applied to one instance is not applied to the class" was
    WRITTEN DOWN before the third instance and still needed a human to
    trigger it. So it stops being a rule and becomes a gate: prose does not
    survive a model swap, this does.

    Collapsing empty into error (or absent into unreadable) is the same
    honest_failure_modes #1 defect this whole file exists to prevent — here
    in helper-selection form.
    """

    #: (module basename, flat symbol) -> why the flat form is CORRECT there.
    #: Never add an entry to silence a finding; add it only when the caller
    #: provably makes no disposition/health claim from the collapsed value.
    ALLOWED_FLAT = {
        ('watchdog_probes_drift.py', '_read_deployment_declaration'):
            'role-action planner: returns None on a falsy role and notes NO '
            'disposition, so it makes no health claim either way — nothing '
            'downstream can read absent-vs-unreadable as a verdict',
    }

    def _probe_modules(self):
        """Probe bodies only — the hub re-exports flat names by design, and
        core DEFINES both halves (its shim must call the status form)."""
        import glob
        skip = {'watchdog_probes.py', 'watchdog_probe_core.py'}
        paths = [p for p in sorted(glob.glob(
            os.path.join(SRC_DIR, 'utils', 'watchdog_probes*.py')))
            if os.path.basename(p) not in skip]
        assert len(paths) >= 4, (
            f'expected the split watchdog_probes* modules, found {paths} — '
            f'path moved and this guard would vacuously pass')
        return paths

    def test_pairs_are_discovered(self):
        """Non-vacuity: if no pairs are found the whole contract is inert."""
        pairs = _core_flat_status_pairs()
        assert pairs, (
            'no flat/tri-state helper pairs discovered in '
            'watchdog_probe_core.py — the deriver broke, so this contract '
            'enforces nothing')
        assert '_journal_newest_match' in pairs
        assert '_read_deployment_declaration' in pairs

    def test_scanner_sees_real_uses(self):
        """Non-vacuity, the harder half: the AST walker must actually detect
        the one use we KNOW is there (the allowlisted planner call). A
        scanner that sees nothing would pass the contract below silently —
        the 2026-08-05 inert-guard shape."""
        drift = os.path.join(SRC_DIR, 'utils', 'watchdog_probes_drift.py')
        assert '_read_deployment_declaration' in _names_referenced(drift), (
            'the AST scan cannot see a reference that is provably in '
            'watchdog_probes_drift.py — the walker is broken and this '
            'contract is inert')
        assert '_read_deployment_declaration' in _names_called(drift), (
            'the call-walker cannot see the allowlisted CALL in '
            'watchdog_probes_drift.py — the stale-entry check is inert')

    def test_status_resolver_callers_also_call_the_presence_gate(self):
        """A module that CALLS ``_resolve_main_pid_status`` must also call
        ``note_unit_presence_gate`` — the ONE implementation of the
        absent→inert / unknown→indeterminate policy (2026-08-12 review).

        Why: the first conversion applied that policy as a hand-copied
        ``if/else`` at ten call sites. The contract above only forbids the
        FLAT helper, so a new probe could destructure the status tuple,
        branch on the pid alone, and re-create the inert/indeterminate
        collapse with every gate green. Requiring the gate call makes the
        policy's consumer mechanised, not remembered. A future probe with a
        genuinely different policy (where ``absent`` IS the finding) should
        not resolve a MainPID at all (``probe_service_inactive`` doesn't) —
        or must earn an allowlist entry here stating why."""
        ALLOWED_WITHOUT_GATE: dict = {}
        violations = []
        for path in self._probe_modules():
            base = os.path.basename(path)
            if base in ALLOWED_WITHOUT_GATE:
                continue
            called = _names_called(path)
            if ('_resolve_main_pid_status' in called
                    and 'note_unit_presence_gate' not in called):
                violations.append(base)
        assert not violations, (
            'probe module(s) resolving a unit MainPID status without the '
            'shared presence gate:\n  ' + '\n  '.join(violations) + '\n\n'
            'Branching on the pid alone re-collapses absent into '
            'indeterminate (or worse, into inert for the unknown case). '
            'Call note_unit_presence_gate(cls, pid_status, ...) — the '
            'per-site judgement (which family, which reasons) stays yours; '
            'the mechanism must not be a tenth hand copy.')

    def test_presence_gate_contract_is_not_vacuous(self):
        """Non-vacuity for the check above: the walker must SEE at least one
        real caller pair, or the contract enforces nothing."""
        seen = 0
        for path in self._probe_modules():
            called = _names_called(path)
            if ('_resolve_main_pid_status' in called
                    and 'note_unit_presence_gate' in called):
                seen += 1
        assert seen >= 4, (
            f'only {seen} probe module(s) call both _resolve_main_pid_status '
            f'and note_unit_presence_gate — the 2026-08-12 conversion touched '
            f'six modules, so the walker (or the conversion) broke')

    def test_no_probe_module_uses_a_flat_helper(self):
        pairs = _core_flat_status_pairs()
        violations = []
        for path in self._probe_modules():
            base = os.path.basename(path)
            names = _names_referenced(path)
            for flat in sorted(pairs & names):
                if (base, flat) in self.ALLOWED_FLAT:
                    continue
                violations.append(f'{base} uses {flat}() '
                                  f'(tri-state {flat}_status exists)')
        assert not violations, (
            'probe module(s) using a FLAT helper whose tri-state sibling '
            'exists:\n  ' + '\n  '.join(violations) + '\n\n'
            'The flat form collapses "observed nothing" into "could not '
            'look", so an organ absent BY DESIGN gets reported as an '
            'observation that FAILED — that is how four boxes read blind '
            'for weeks (channel_feed_dark) and meshanchor-server sat '
            'permanently indeterminate (role_drift/rules_seed_drift). Use '
            f'the *_status form and split inert from indeterminate, or add '
            'an ALLOWED_FLAT entry stating why this caller makes no health '
            'claim from the collapsed value.')

    def test_allowlist_has_no_stale_entries(self):
        """An allowlist that outlives its callers is dead weight that reads
        as sanctioned — every entry must still correspond to a real CALL.

        Uses :func:`_names_called`, not ``_names_referenced``: the first
        version of this test accepted a bare import as proof of use, so
        deleting the allowlisted call left the entry green (drill,
        2026-08-07). A dead import keeping a dead exemption alive is the
        same absence-read-as-presence defect the contract above exists for.
        """
        stale = []
        for (base, flat), _why in self.ALLOWED_FLAT.items():
            path = os.path.join(SRC_DIR, 'utils', base)
            if not os.path.exists(path) or flat not in _names_called(path):
                stale.append(f'{base}:{flat}')
        assert not stale, (
            f'ALLOWED_FLAT entries no longer correspond to any use: {stale} '
            f'— delete them so the allowlist keeps meaning what it says')


class TestPostCommitRefreshesBothEnumHolders:
    """The post-commit hook must refresh EVERY local process holding
    ``SIGNAL_CLASSES``, not just one of them.

    2026-08-08: the hook restarted ``meshforge-map`` (the CONSUMER that serves
    ``/api/fleet/truth``) but not ``meshforge-watchdog`` (the PRODUCER that
    emits the per-class dispositions). Those go stale in OPPOSITE directions —
    a GROWING enum leaves the map behind, a SHRINKING one leaves the watchdog
    behind — and the enum had only ever grown, so the gap stayed invisible
    until the yield audit removed four classes and the fleet verdict read
    ``dark`` with all 9 boxes healthy.

    This is honest_failure_modes #4 (a mechanism with a producing half and a
    consuming half must wire both or refuse loudly) found *in the harness*, so
    the guard belongs here rather than in a comment.
    """

    HOOK = os.path.join(REPO_ROOT, '.githooks', 'post-commit')

    #: Long-running units that import ``utils.watchdog_probe_core`` at startup
    #: and therefore cache the enum. Derived by grep at the time of writing;
    #: mini-dudeai, the gateway and the monitoring daemons do NOT import it.
    #: If a third process starts importing it, add it here AND to the hook.
    ENUM_HOLDERS = ('meshforge-map.service', 'meshforge-watchdog.service')

    def _hook_text(self):
        assert os.path.exists(self.HOOK), f'missing hook: {self.HOOK}'
        with open(self.HOOK, 'r', encoding='utf-8') as fh:
            return fh.read()

    def test_hook_refreshes_every_enum_holder(self):
        text = self._hook_text()
        missing = [u for u in self.ENUM_HOLDERS if u not in text]
        assert not missing, (
            f'post-commit hook does not refresh {missing} — refreshing only '
            f'SOME holders of SIGNAL_CLASSES manufactures the exact '
            f'server_class_skew the hook exists to prevent (one half reports '
            f'classes the other does not know, and the fleet verdict goes '
            f'dark with every box healthy).')

    def test_hook_uses_try_restart_never_restart(self):
        """``try-restart`` is a no-op on an inactive unit; ``restart`` STARTS
        it. A deploy must never change WHICH services run — that is the
        2026-07-24 incident, where a restart loop started meshforge-map on a
        gateway-only box whose map is disabled by design."""
        text = self._hook_text()
        assert 'try-restart' in text
        bad = [ln.strip() for ln in text.splitlines()
               if 'systemctl' in ln and ' restart ' in ln
               and 'try-restart' not in ln and not ln.strip().startswith('#')
               and 'echo' not in ln]
        assert not bad, (
            f'hook uses a bare `systemctl restart` on a live path: {bad} — '
            f'that can START a unit the operator deliberately stopped')

    def test_hook_gates_on_is_active(self):
        """The committed hook ships to every box; it must no-op where the
        unit is not running rather than assume this box serves the map."""
        assert 'is-active' in self._hook_text()

    def test_partial_refresh_is_reported_loudly(self):
        """If one holder cannot be restarted, the hook must SAY the box is
        left skewed — reporting the successes and going quiet about the rest
        is the swallow this whole class of bug is made of."""
        text = self._hook_text()
        assert 'ENUM HOLDERS LEFT STALE' in text


class TestStreakSaversAreOne:
    """Enforce: the debounce-streak load/save pair is DEFINED once, in
    ``utils/watchdog_probe_core.py`` (``_load_parity_streak`` /
    ``_save_parity_streak``), and every probe module ALIASES it.

    The 2026-09-02 falsifiability audit found EIGHT byte-identical copies of
    this pair across the probe modules. The parity copy had been cured on
    2026-07-26 of the defect that let an unwritable state dir freeze a streak
    at 1 below its debounce forever; the seven copies had not, so seven
    detector classes could never fire on that box shape and nothing said so.
    One mechanism, one implementation (honest_failure_modes #5). A module
    that needs a streak imports the core pair under its own name.
    """

    _DEF = re.compile(r"^\s*def\s+_(load|save)_\w*streak\w*\s*\(", re.M)

    def test_no_streak_pair_defined_outside_probe_core(self):
        violations = []
        utils_dir = os.path.join(SRC_DIR, 'utils')
        for filename in sorted(os.listdir(utils_dir)):
            if not filename.endswith('.py') or filename == 'watchdog_probe_core.py':
                continue
            path = os.path.join(utils_dir, filename)
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
            for m in self._DEF.finditer(text):
                lineno = text.count('\n', 0, m.start()) + 1
                violations.append(f"{filename}:{lineno}: {m.group(0).strip()}")
        assert not violations, (
            "streak load/save pair defined outside watchdog_probe_core.py — "
            "alias _load_parity_streak/_save_parity_streak instead "
            "(2026-09-02 eight-copies finding):\n  " + "\n  ".join(violations))


class TestCryptoPinIsOneConstant:
    """Enforce: the cryptography / pyOpenSSL version constraint is declared
    ONCE, in ``requirements/rns.txt``, and every executable copy agrees.

    2026-09-05: ``requirements/rns.txt`` pinned ``cryptography`` to a range in
    which EVERY version carried published advisories (one medium + three high)
    — the fleet was unpatchable while fully requirements-compliant. Raising the
    pin was not enough: the same constraint was hardcoded in FIVE more places
    per repo (``scripts/install_noc.sh`` ×3-4 and the in-app RNS dependency
    repair in ``launcher_tui/handlers/rns_diagnostics.py``). Left alone, a
    fresh install or an in-app "repair dependencies" would have re-installed
    the vulnerable range and silently undone the fix.

    Two consumers of one artifact share ONE constant (honest_failure_modes #5)
    — and across a bash/python boundary the sanctioned form of "share" is a
    test pin, which is this. Only QUOTED literals count as declarations: prose
    that cites a historical pin (``scripts/dep_advisory_check.py`` quotes the
    old broken one on purpose) is documentation, not a use.
    """

    # A quoted requirement literal, e.g. the pip argument form used by
    # install_noc.sh and the in-app repair path. Backtick-quoted RST prose is
    # deliberately NOT matched.
    _LITERAL = re.compile(
        r"""['"]\s*(cryptography|pyopenssl)\s*([<>=!~,.0-9\s]+?)\s*['"]""",
        re.IGNORECASE)

    _SCAN_DIRS = ('src', 'scripts', 'templates')
    _SCAN_EXTS = ('.py', '.sh')

    @staticmethod
    def _ssot():
        """The declared constraint, read from requirements/rns.txt."""
        path = os.path.join(REPO_ROOT, 'requirements', 'rns.txt')
        specs = {}
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                m = re.match(r'^(cryptography|pyopenssl)\s*(.+)$', line, re.I)
                if m:
                    specs[m.group(1).lower()] = m.group(2).strip()
        return specs

    def _scan(self):
        hits = []
        for rel in self._SCAN_DIRS:
            root = os.path.join(REPO_ROOT, rel)
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in ('.git', '__pycache__')]
                for name in sorted(filenames):
                    if not name.endswith(self._SCAN_EXTS):
                        continue
                    path = os.path.join(dirpath, name)
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        text = f.read()
                    for m in self._LITERAL.finditer(text):
                        lineno = text.count('\n', 0, m.start()) + 1
                        hits.append((os.path.relpath(path, REPO_ROOT), lineno,
                                     m.group(1).lower(), m.group(2).strip()))
        return hits

    def test_ssot_declares_both_packages(self):
        specs = self._ssot()
        assert 'cryptography' in specs and 'pyopenssl' in specs, (
            "requirements/rns.txt must declare both packages explicitly — "
            f"found: {sorted(specs)}")

    def test_guard_is_not_inert(self):
        """The scan must actually see the hardcoded copies it exists to pin.

        Without this, a broken scan path or an over-tight regex would let the
        guard pass vacuously — the failure mode TestGuardsAreNotInert exists
        for, reproduced once already in this file's history.
        """
        hits = self._scan()
        assert len(hits) >= 4, (
            "crypto-pin scan found "
            f"{len(hits)} quoted requirement literal(s) under "
            f"{self._SCAN_DIRS} — expected the installer + in-app repair "
            "copies. A zero/low count means the scan broke, not that the "
            "copies are gone.")

    def test_every_hardcoded_copy_matches_the_ssot(self):
        specs = self._ssot()
        violations = []
        for relpath, lineno, pkg, spec in self._scan():
            expected = specs.get(pkg)
            if expected is not None and spec != expected:
                violations.append(
                    f"{relpath}:{lineno}: {pkg}{spec!r} != requirements/rns.txt "
                    f"{pkg}{expected!r}")
        assert not violations, (
            "hardcoded version constraint disagrees with requirements/rns.txt "
            "— update the copy, or better, install from the requirements file "
            "(2026-09-05 unpatchable-pin finding):\n  " + "\n  ".join(violations))


class TestSignalClassBudget:
    """The detector-class count is a BUDGET, not a scoreboard — it may shrink
    freely and may not grow without a deliberate decision.

    2026-08-08's subtraction arc took SIGNAL_CLASSES from 62 to 58 on the
    operator's footprint constraint: this fleet is Pi-class, and machinery to
    watch machinery competes with the services it watches. By 2026-09-05 the
    count was back to 60 — added one probe at a time, each individually
    justified, none of them noticed as a trend by anybody. That is the failure
    mode this pins: every gate in this repo checks CORRECTNESS, and nothing
    checked VOLUME, so the ratchet only ever turned one way.

    honest_failure_modes #10 makes it structural rather than careless: a
    resolved incident is *supposed* to compile down to a probe. There is no
    matching instruction to ever remove one, so the standing gradient of this
    codebase is toward more detectors. A gradient needs a counterweight, not
    vigilance.

    To ADD a class, name the one it replaces and lower the ceiling in the same
    change; or make the case in the commit and raise it deliberately. Either is
    fine — being unable to do it *by accident* is the point.
    """

    #: Frozen ceiling. May only ever be LOWERED (the MF025 baseline idiom).
    #: 62 -> 58 (subtraction arc, 2026-08-08) -> 60 (drift, 2026-09-05).
    SIGNAL_CLASS_BUDGET = 60

    @staticmethod
    def _live_count():
        src = os.path.join(SRC_DIR, 'utils', 'watchdog_probe_core.py')
        with open(src, 'r', encoding='utf-8') as f:
            text = f.read()
        m = re.search(r'^SIGNAL_CLASSES\s*=\s*\((.*?)^\)', text, re.M | re.S)
        assert m, "SIGNAL_CLASSES tuple not found — this guard cannot see its subject"
        return len(re.findall(r'^\s+"', m.group(1), re.M))

    def test_the_scanner_can_see_its_subject(self):
        """Anti-vacuous: a zero count would make the budget trivially satisfied."""
        n = self._live_count()
        assert n > 20, (
            "counted only %d signal class(es) — the parse broke, and a broken "
            "parse would let the budget pass no matter how many were added" % n)

    def test_signal_classes_stay_within_budget(self):
        n = self._live_count()
        assert n <= self.SIGNAL_CLASS_BUDGET, (
            "SIGNAL_CLASSES is %d, budget is %d. Adding a detector class is a "
            "deliberate act: name the class this one REPLACES and remove it, or "
            "raise SIGNAL_CLASS_BUDGET in this file with the reason in the "
            "commit. The 2026-08-08 subtraction arc got this to 58 and it drifted "
            "back to 60 unnoticed — one justified probe at a time."
            % (n, self.SIGNAL_CLASS_BUDGET))

    def test_budget_is_ratcheted_to_reality(self):
        """If the count DROPS, lower the budget in the same change — otherwise
        the headroom silently banks itself and the next drift is invisible.
        Same rule as the MF025 file-length baseline: it only shrinks."""
        n = self._live_count()
        assert n >= self.SIGNAL_CLASS_BUDGET, (
            "SIGNAL_CLASSES is down to %d but the budget still reads %d — lower "
            "SIGNAL_CLASS_BUDGET to %d so the win is banked, not spent."
            % (n, self.SIGNAL_CLASS_BUDGET, n))
