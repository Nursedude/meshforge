#!/usr/bin/env python3
"""
MeshForge Linter - Check for common issues and coding standards.

Checks:
- MF001: Path.home() violations (must use get_real_user_home for sudo compatibility)
- MF002: shell=True in subprocess calls (security risk)
- MF003: Bare except: clauses (should use except Exception:)
- MF004: Missing timeout in subprocess calls
- MF005: (removed — was GLib.idle_add check, GTK4 removed in v0.5.x)
- MF006: safe_import for first-party modules (must use direct imports)
- MF007: Direct TCPInterface creation (must use connection manager, Issue #17)
- MF008: Raw systemctl for service state decisions (must use service_check, Issue #20)
- MF009: RNS.Reticulum() without configdir (causes EADDRINUSE, Issue #12)
- MF010: time.sleep() in daemon loops (must use _stop_event.wait(), H1)
- MF011: Repair logic in _nomadnet_rns_checks.py (must be in _rns_repair.py/diagnostics)
- MF012: Context-loaded doc size (persistent_issues.md must stay under 40k chars)
- MF013: Bare sqlite3.connect() outside db_helpers.py (must use connect_tuned)
- MF014: Operator-specific values (hostnames, personal email, /home/<user>/) — break repo portability
- MF016: @patch('src.utils.paths.…') in tests — production imports via bare 'utils.paths', divergent class objects
- MF017: hardened systemd unit (ProtectHome=read-only) ReadWritePaths drift vs the three meshforge buckets (Issue #58)
- MF018: TUI shell-escapes (editor spawns, "run/install manually", "run with sudo") — the In-Domain Principle ratchet (foundations/in_domain_principle.md)
- MF019: RNS.Reticulum() constructed outside the guarded chokepoint (must use open_reticulum() from utils.rns_init; #68/#69, RNS T2-isolate arc)
- MF020: apply_config_and_restart() return (bool, msg) discarded in TUI handlers (hardcoded-success-after-unchecked-action, honest-signal Issues #74-#77)
- MF021: subprocess/systemctl/os.system/Popen/shell=True in the mini-dudeai engine/sources/actions (observation-only invariant — mini observes, never executes)
- MF022: bare/exit-code-masked pip & swallowed apt in shell installers (must route through scripts/lib/install_common.sh — pip-presence + PEP 668 + checked rc; install-hardening arc)
- MF023: blocking meshtastic interface creation (_create_interface — the nodedb sync) in the map collector outside the bounded helper _collect_interface_bounded (serving must never block on collection; 2026-06-23 moc1 spin)
- MF024: version SSOT (src/__version__.py) vs pyproject/README badge+heading drift (the 4-way-drift guard; delegates to scripts/version_consistency_check.py)
- MF025: file-size ratchet — src/ python files over 1,500 lines (frozen 2026-07-13 baseline for the 5 known offenders, which may only shrink; split the file, never raise the cap)

Usage:
    python3 scripts/lint.py [files...]
    python3 scripts/lint.py --all
    python3 scripts/lint.py --staged
"""

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class LintIssue:
    file: str
    line: int
    severity: Severity
    code: str
    message: str

    def __str__(self):
        icon = {"error": "E", "warning": "W", "info": "I"}[self.severity.value]
        return f"{self.file}:{self.line}: [{icon}] {self.code}: {self.message}"


class MeshForgeLinter:
    """Linter for MeshForge-specific coding standards."""

    def __init__(self):
        self.issues: List[LintIssue] = []

    def lint_file(self, filepath: str) -> List[LintIssue]:
        """Lint a single file and return issues found."""
        issues = []

        if not filepath.endswith('.py'):
            return issues

        # Self-skip: the linter source legitimately contains every pattern
        # it detects (in detection regexes, docstrings, allowlist comments).
        # Per-line rules don't have rule-by-rule allowlists for this file,
        # so skip the whole file. MF014 still scans via its own pass.
        if os.path.basename(filepath) == 'lint.py' and 'scripts' in filepath.split(os.sep):
            return issues

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except (IOError, OSError) as e:
            return [LintIssue(filepath, 0, Severity.ERROR, "MF000", f"Cannot read file: {e}")]

        content = ''.join(lines)

        # Check each line. Track the running char offset of each line so the
        # lookahead/lookback rules (MF001/MF004/MF009/MF010) use THIS line's
        # position, not content.find(line) — which returns the FIRST textual
        # occurrence. Identical line text is common (`time.sleep(1)`,
        # `result = subprocess.run(`), so recomputing by text made a real
        # violation on a later duplicate line resolve its context from an
        # earlier twin — a silent gate miss (2026-07-09 frontier review of
        # the gates themselves).
        offset = 0
        for i, line in enumerate(lines, 1):
            issues.extend(self._check_line(filepath, i, line, content, offset))
            offset += len(line)

        return issues

    def _check_line(self, filepath: str, lineno: int, line: str, content: str,
                    line_offset: int = -1) -> List[LintIssue]:
        """Check a single line for issues."""
        issues = []
        stripped = line.strip()
        # Backward-compatible fallback: callers that don't pass the true
        # offset get the legacy (first-occurrence) behaviour rather than a
        # crash. lint_file always passes the exact offset.
        if line_offset < 0:
            line_offset = content.find(line)

        # Skip comments
        if stripped.startswith('#'):
            return issues

        # MF001: Path.home() violation
        # Skip the paths.py utility file that defines get_real_user_home()
        if 'Path.home()' in line and 'paths.py' not in filepath:
            # Skip string literals (changelog entries, documentation).
            # Recognize f-string prefixes (f", f', rf", rf', fr", fr') as
            # string-literal starts — without this, an f-string assertion
            # message that mentions Path.home() in its display text trips
            # MF001 as a false positive.
            is_string_literal = (
                stripped.startswith('"')
                or stripped.startswith("'")
                or stripped.startswith('f"')
                or stripped.startswith("f'")
                or stripped.startswith('rf"')
                or stripped.startswith("rf'")
                or stripped.startswith('fr"')
                or stripped.startswith("fr'")
            )
            # Acceptable fallback patterns:
            # 1. return Path.home() in a fallback function
            # 2. else Path.home() in a ternary after SUDO_USER check
            # 3. Inside an except ImportError block with SUDO_USER handling nearby
            is_fallback_pattern = (
                'return Path.home()' in line or
                'else Path.home()' in line or
                ('def get_real_user_home' in content and 'Path.home()' in line)
            )
            # Also check if this is in an except block after trying to import paths
            context_start = max(0, line_offset - 500)
            nearby_context = content[context_start:line_offset + len(line)]
            has_import_fallback = (
                'from utils.paths import' in nearby_context and
                'except ImportError' in nearby_context
            )
            if not is_string_literal and not is_fallback_pattern and not has_import_fallback:
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF001",
                    "Use get_real_user_home() instead of Path.home() for sudo compatibility"
                ))

        # MF002: shell=True security risk
        # Only flag actual subprocess calls, not comments/docstrings/patterns
        if 'shell=True' in line and 'subprocess' in content:
            # Must look like actual code: subprocess.run(..., shell=True, ...)
            # Skip if: in docstring, comment, string literal, or pattern definition
            is_actual_call = (
                re.search(r'subprocess\.\w+\s*\([^)]*shell\s*=\s*True', line) or
                (stripped.startswith('subprocess.') and 'shell=True' in line) or
                ('shell=True' in line and '(' in line and ')' in line and 'subprocess' in line)
            )
            # Exclude comments and docstring-like content
            is_doc_or_comment = (
                stripped.startswith('#') or
                stripped.startswith('"""') or
                stripped.startswith("'''") or
                'Security:' in line or  # Common docstring pattern
                'NEVER' in line or      # Documentation
                'pattern' in line.lower() or
                line.strip().startswith('"') or
                line.strip().startswith("'")
            )
            if is_actual_call and not is_doc_or_comment:
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF002",
                    "Avoid shell=True in subprocess calls - use list args instead"
                ))

        # MF003: Bare except clause
        if re.match(r'^\s*except\s*:\s*(#.*)?$', line):
            issues.append(LintIssue(
                filepath, lineno, Severity.WARNING, "MF003",
                "Bare except: clause - use 'except Exception:' at minimum"
            ))

        # MF004: subprocess.run/call/Popen without timeout
        subprocess_pattern = r'subprocess\.(run|call|Popen)\s*\('
        if re.search(subprocess_pattern, line):
            # Skip if marked as interactive or intentionally no timeout
            if '# Interactive' in line or '# no timeout' in line.lower():
                pass  # Skip interactive commands
            # Skip if it's inside a string (changelog, pattern definition)
            elif (stripped.startswith('"') or stripped.startswith("'") or
                  'SECURITY:' in line or 'IMPROVED:' in line or 'pattern' in line.lower()):
                pass  # Skip changelog/documentation/pattern strings
            else:
                # Look ahead for timeout in the same statement
                start_idx = line_offset
                if start_idx != -1:
                    # Get the call text (matching parens)
                    context = content[start_idx:start_idx + 500]
                    paren_count = 0
                    call_text = ""
                    for char in context:
                        call_text += char
                        if char == '(':
                            paren_count += 1
                        elif char == ')':
                            paren_count -= 1
                            if paren_count == 0:
                                break

                    # Check for timeout in call or kwargs unpacking nearby
                    has_timeout = 'timeout' in call_text
                    # Check for **kwargs pattern - look back for kwargs dict with timeout
                    if '**' in call_text:
                        kwargs_match = re.search(r'\*\*(\w+)', call_text)
                        if kwargs_match:
                            kwargs_name = kwargs_match.group(1)
                            # Look back in content for this dict definition with timeout
                            lookback = content[max(0, start_idx - 1000):start_idx]
                            if f"'{kwargs_name}'" in lookback or f'"{kwargs_name}"' in lookback:
                                pass  # Skip - complex case
                            elif f'{kwargs_name}' in lookback and 'timeout' in lookback:
                                has_timeout = True

                    if not has_timeout and 'Popen' not in line:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.WARNING, "MF004",
                            "subprocess call without timeout parameter"
                        ))

        # MF006: safe_import for first-party modules
        # First-party modules must use direct imports, not safe_import
        if 'safe_import(' in line and 'safe_import.py' not in filepath:
            first_party_prefixes = (
                "'utils.", "'commands.", "'gateway.", "'core.",
                "'launcher_tui.", "'config.", "'monitoring.", "'plugins.",
                "'cli.", "'agent.", "'amateur.", "'diagnostics.", "'updates.",
            )
            if any(prefix in line for prefix in first_party_prefixes):
                # Skip docstrings/comments/examples
                if not stripped.startswith('#') and not stripped.startswith('"') and not stripped.startswith("'"):
                    issues.append(LintIssue(
                        filepath, lineno, Severity.ERROR, "MF006",
                        "safe_import used for first-party module - use direct import instead"
                    ))

        # MF005: Removed — was GLib.idle_add check for GTK4 thread safety.
        # GTK4 was removed in v0.5.x; TUI (whiptail/dialog) is the only interface.

        # MF007: Direct TCPInterface creation (bypasses connection manager)
        # meshtasticd supports ONE TCP client — direct creation causes thrashing (Issue #17)
        if 'TCPInterface(' in line:
            # Allowlist: files that ARE the connection infrastructure
            conn_infrastructure = (
                'connection_manager.py', 'meshtastic_connection.py', 'connections.py',
            )
            # Files that use the global lock correctly (tracked, not violations)
            lock_aware_files = (
                'node_monitor.py', 'device_controller.py',
                'rns_transport.py', 'mesh_bridge.py',
            )
            basename = os.path.basename(filepath)
            is_infra = any(f in filepath for f in conn_infrastructure)
            is_lock_aware = any(f in filepath for f in lock_aware_files)
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            is_test = '/tests/' in filepath or 'test_' in basename
            if not is_infra and not is_lock_aware and not is_string and not is_comment and not is_test:
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF007",
                    "Direct TCPInterface() creation — use MeshtasticConnection from "
                    "connection_manager.py or acquire MESHTASTIC_CONNECTION_LOCK first (Issue #17)"
                ))

        # MF008: Raw systemctl for service state decisions (bypasses service_check)
        if 'systemctl' in line and 'subprocess' in line:
            basename = os.path.basename(filepath)
            # Only flag state-determining calls, not display-only (status --no-pager)
            is_state_check = (
                "'is-active'" in line or '"is-active"' in line or
                "'restart'" in line or '"restart"' in line or
                "'start'" in line or '"start"' in line or
                "'stop'" in line or '"stop"' in line or
                "'enable'" in line or '"enable"' in line
            )
            is_display_only = '--no-pager' in line or "'status'" in line or '"status"' in line
            is_service_check = 'service_check.py' in filepath
            is_string = stripped.startswith('"') or stripped.startswith("'")
            if is_state_check and not is_display_only and not is_service_check and not is_string:
                issues.append(LintIssue(
                    filepath, lineno, Severity.WARNING, "MF008",
                    "Raw systemctl call — use helpers from utils.service_check instead (Issue #20)"
                ))

        # MF009: RNS.Reticulum() without configdir
        # Without configdir, RNS reads user config with interfaces → EADDRINUSE (Issue #12)
        if 'Reticulum(' in line and 'configdir' not in line:
            basename = os.path.basename(filepath)
            is_test = '/tests/' in filepath or 'test_' in basename
            is_comment = stripped.startswith('#')
            is_string = stripped.startswith('"') or stripped.startswith("'")
            # Only flag actual code calls — pattern: assignment or standalone call
            # e.g. "self._reticulum = RNS.Reticulum(" or "reticulum = RNS.Reticulum("
            is_actual_call = bool(re.search(
                r'=\s*\w*\.?Reticulum\s*\(', line
            ))
            if not is_test and not is_comment and not is_string and is_actual_call:
                # Check if configdir is on the next few lines (multi-line call)
                line_idx = line_offset
                if line_idx != -1:
                    following = content[line_idx:line_idx + 300]
                    if 'configdir' not in following.split(')')[0]:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.ERROR, "MF009",
                            "RNS.Reticulum() without configdir= — will cause EADDRINUSE "
                            "when rnsd is running (Issue #12)"
                        ))

        # MF019: RNS.Reticulum() constructed outside the guarded chokepoint.
        # The RNS T2-isolate arc (sub-arc B+C, 2026-05-29) routes ALL in-process
        # RNS init through utils/rns_init.py::open_reticulum so a wedged rnsd
        # degrades (#68 fail-open) instead of hanging the calling thread, and a
        # foreign @rns owner fails loud (#69). Raw construction elsewhere
        # reintroduces the silent-hang class. Mirror of MF007/TestTCPConnection.
        if 'Reticulum(' in line:
            basename = os.path.basename(filepath)
            is_test = '/tests/' in filepath or 'test_' in basename
            is_comment = stripped.startswith('#')
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_actual_call = bool(
                re.search(r'=\s*\w*\.?Reticulum\s*\(', line)
                or re.search(r'\breturn\s+\w*\.?Reticulum\s*\(', line)
            )
            # Allowlisted homes for an actual RNS.Reticulum() construction:
            #   - utils/rns_init.py — THE chokepoint (open_reticulum + the
            #     watchdog-guarded constructor).
            #   - launcher_tui/handlers/rns_interfaces.py — a `python3 -c`
            #     connectivity probe that runs in an ISOLATED subprocess with
            #     its own subprocess timeout, and deliberately tests NomadNet's
            #     OWN venv RNS (not MeshForge's), so it cannot route through the
            #     in-process chokepoint and cannot hang the TUI.
            chokepoint_files = (
                'utils/rns_init.py',
                'launcher_tui/handlers/rns_interfaces.py',
            )
            is_allowed = any(f in filepath for f in chokepoint_files)
            if (is_actual_call and not is_test and not is_comment
                    and not is_string and not is_allowed):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF019",
                    "RNS.Reticulum() constructed outside the guarded chokepoint "
                    "— use open_reticulum() from utils.rns_init (degrades on a "
                    "wedged rnsd instead of hanging the thread; #68/#69). If the "
                    "call is genuinely isolated, add it to the chokepoint "
                    "allowlist in lint.py + TestRNSReticulumChokepoint."
                ))

        # MF020: apply_config_and_restart() return value discarded in a TUI handler.
        # The function returns (success, msg) precisely so callers surface a
        # failed daemon restart; a bare-statement call drops it and feeds the
        # #74-#77 "hardcoded success after an unchecked action" defect class (the
        # 2026-06-08 TUI audit found 8 such sites). Honest pattern:
        #   ok, msg = apply_config_and_restart('meshtasticd')
        #   self.ctx.report_action(ok, "Applied", ..., "Restart Failed", msg)
        norm_path = filepath.replace(os.sep, '/')
        if 'launcher_tui/handlers/' in norm_path:
            if re.match(r'^_?apply_config_and_restart\s*\(', stripped):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF020",
                    "apply_config_and_restart() return (bool, msg) discarded — "
                    "bind 'ok, msg = ...' and surface restart failure via "
                    "ctx.report_action (honest-signal class, Issues #74-#77)"
                ))

        # MF011: _nomadnet_rns_checks.py must not contain repair/service logic
        if '_nomadnet_rns_checks.py' in filepath:
            repair_patterns = ['start_service(', 'stop_service(', 'enable_service(', 'chmod(']
            # subprocess is only flagged for service management commands
            subprocess_forbidden = ['systemctl', 'pkill', 'rnstatus', 'rnsd']
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            is_import = 'import' in line or 'safe_import' in line
            if not is_string and not is_comment and not is_import:
                for pattern in repair_patterns:
                    if pattern in line:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.ERROR, "MF011",
                            f"Repair logic in _nomadnet_rns_checks.py — move to "
                            f"_rns_repair.py or diagnostics handler"
                        ))
                        break
                if 'subprocess' in line:
                    for cmd in subprocess_forbidden:
                        if f"'{cmd}'" in line or f'"{cmd}"' in line:
                            issues.append(LintIssue(
                                filepath, lineno, Severity.ERROR, "MF011",
                                f"Service management subprocess in _nomadnet_rns_checks.py — "
                                f"move to _rns_repair.py or diagnostics handler"
                            ))
                            break

        # MF013: bare sqlite3.connect() must go through utils.db_helpers.connect_tuned
        # — closes the 2026-04-26 fleet wedge class (1.95 GB rollback-journal
        # DB stalled the service 16+ minutes in jbd2_log_wait_commit). The helper
        # itself uses sqlite3.connect (allowed); test fixtures may also.
        if 'sqlite3.connect(' in line:
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            basename = os.path.basename(filepath)
            allowlisted_files = {'db_helpers.py'}
            in_tests = '/tests/' in filepath or basename.startswith('test_')
            if (not is_string and not is_comment and basename not in allowlisted_files
                    and not in_tests):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF013",
                    "Bare sqlite3.connect() — use utils.db_helpers.connect_tuned "
                    "(WAL + sync=NORMAL + 64MB journal cap)"
                ))

        # MF016: @patch('src.utils.paths.…') silently no-ops because production
        # code imports via `from utils.paths import …` and conftest puts only
        # `src/` on sys.path — `src.utils.paths` and `utils.paths` resolve to
        # different module objects with different ReticulumPaths class objects.
        # The patch lands on src.utils.paths.ReticulumPaths; the consumer uses
        # utils.paths.ReticulumPaths; the mock never fires. The test then
        # passes-by-coincidence on a fleet box where the real method returns
        # the expected value (see project_ci_red_2026_05_03_cascade.md).
        # Cure: patch at the consumer's namespace OR use bare 'utils.paths.…'.
        basename_lc = os.path.basename(filepath)
        if (basename_lc.startswith('test_') or '/tests/' in filepath) and '@patch' in line:
            if re.search(r"@patch\(\s*['\"]src\.utils\.paths\.", line):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF016",
                    "@patch('src.utils.paths.…') silently no-ops — production "
                    "imports via 'from utils.paths import …' (different module "
                    "object). Use 'utils.paths.…' or patch at the consumer's "
                    "namespace (Issue: 2026-05-03 CI cascade)"
                ))

        # MF010: time.sleep() in daemon loops (should use _stop_event.wait())
        if 'time.sleep(' in line:
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            if not is_string and not is_comment:
                # Check if we're inside a daemon loop method
                func_match = content.rfind('def ', 0, line_offset)
                if func_match != -1:
                    func_sig = content[func_match:func_match + 200].split('\n')[0]
                    daemon_patterns = ('_loop', '_run', 'run_forever', '_poll', '_monitor')
                    if any(p in func_sig for p in daemon_patterns):
                        issues.append(LintIssue(
                            filepath, lineno, Severity.WARNING, "MF010",
                            "time.sleep() in daemon loop — use _stop_event.wait() for clean shutdown"
                        ))

        return issues

    def lint_files(self, files: List[str]) -> List[LintIssue]:
        """Lint multiple files."""
        all_issues = []
        for f in files:
            if os.path.isfile(f):
                all_issues.extend(self.lint_file(f))
        return all_issues


def get_staged_files() -> List[str]:
    """Get list of staged Python files."""
    try:
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True,
            text=True,
            timeout=10
        )
        files = [f for f in result.stdout.strip().split('\n') if f.endswith('.py')]
        return files
    except Exception:
        return []


def get_staged_files_all_types() -> List[str]:
    """Get list of staged text-like files (any extension that MF014 scans)."""
    try:
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True,
            text=True,
            timeout=10
        )
        files = []
        for f in result.stdout.strip().split('\n'):
            if not f:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in MF014_SCAN_EXTENSIONS:
                files.append(f)
        return files
    except Exception:
        return []


# MF014: Operator-specific value blocklist.
# Catches hardcoded fleet-specific values that break repo portability for new
# users. Drove the 2026-04-26 source scrub (commit 155a74d) and Path B history
# rewrite. These values must NEVER land in source, templates, scripts, or root
# docs. Allowed in: this file (defines them), the regression test that asserts
# them, and the .claude/ subtree (operator-private context, intentionally
# non-portable per project_repo_portability_scrub.md).
MF014_PATTERNS = [
    (re.compile(r'shawnmfarley@gmail\.com'),
     "personal email — use noreply form 177804819+Nursedude@users.noreply.github.com"),
    (re.compile(r'wh6gxz\s+nurse\s+dude', re.IGNORECASE),
     "old git author 'wh6gxz nurse dude' — use Nursedude"),
    (re.compile(r'\bnursedude@meshforge\b', re.IGNORECASE),
     "placeholder email 'nursedude@meshforge' — use noreply form"),
    (re.compile(r'\bvolcanoai\b', re.IGNORECASE),
     "fleet hostname 'volcanoai' — use placeholder or read from config"),
    (re.compile(r'\bmeshforge-moc[0-9]?\b', re.IGNORECASE),
     "fleet hostname 'meshforge-moc*' — use placeholder or read from config"),
    (re.compile(r'\bhawaiinet\b', re.IGNORECASE),
     "regional name 'hawaiinet' — use 'regional' placeholder"),
    (re.compile(r'\bf68c2f56cb61527b6c9ad603b9a5009a\b'),
     "specific LXMF gateway hash — use placeholder"),
    (re.compile(r'/home/wh6gxz/'),
     "user-specific home path — use /home/<user>/ or get_real_user_home()"),
]

MF014_ALLOWED_FILES = {
    'scripts/lint.py',
    'tests/test_regression_guards.py',
    # The Prometheus scrape config legitimately points at real fleet
    # hosts — that's what a scrape config IS. Future templating
    # refactor (placeholders + install-time substitution) would let
    # this come back under MF014; until then, allowlist.
    'templates/prometheus/prometheus.yml',
}

MF014_ALLOWED_DIRS = {
    '.claude',  # operator-private context, non-portable by design
}

# Path prefixes (multi-segment) that are exempt from MF014. Substack posts
# are dated narrative artifacts where the operator's box names are the
# protagonists — sanitizing them damages the published story. MF015 still
# governs LAN-IP leaks in the same tree.
MF014_ALLOWED_PREFIXES = (
    'docs/substack' + os.sep,
)

MF014_SCAN_EXTENSIONS = {
    '.py', '.sh', '.bash', '.yaml', '.yml', '.json', '.toml', '.ini', '.cfg',
    '.conf', '.service', '.md', '.txt', '.rst', '.html', '.js', '.css', '.tmpl',
    '.example', '.j2',
}

MF014_SCAN_EXCLUDE_DIRS = {
    '.git', 'venv', '.venv', '__pycache__', 'node_modules', '.pytest_cache',
    '.tox', '.cache', '.mypy_cache', '.ruff_cache', 'dist', 'build', '.eggs',
}


def _check_operator_values_in_file(filepath: str, rel_path: str) -> List[LintIssue]:
    issues: List[LintIssue] = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for lineno, line in enumerate(f, 1):
                for pattern, message in MF014_PATTERNS:
                    if pattern.search(line):
                        issues.append(LintIssue(
                            rel_path, lineno, Severity.ERROR, "MF014", message,
                        ))
                        break  # one violation per line is enough
    except (IOError, OSError):
        pass
    return issues


def check_operator_values_in_files(files: List[str], repo_root: str = '.') -> List[LintIssue]:
    """MF014: scan a specific set of files (e.g. staged) for operator values."""
    issues: List[LintIssue] = []
    for f in files:
        rel_path = os.path.relpath(f, repo_root) if os.path.isabs(f) else f
        first_seg = rel_path.split(os.sep)[0]
        if first_seg in MF014_ALLOWED_DIRS:
            continue
        if any(rel_path.startswith(p) for p in MF014_ALLOWED_PREFIXES):
            continue
        if rel_path in MF014_ALLOWED_FILES:
            continue
        ext = os.path.splitext(rel_path)[1].lower()
        if ext not in MF014_SCAN_EXTENSIONS:
            continue
        if not os.path.isfile(f):
            continue
        issues.extend(_check_operator_values_in_file(f, rel_path))
    return issues


def check_operator_values_full_tree(repo_root: str = '.') -> List[LintIssue]:
    """MF014: scan the whole repo tree for operator values."""
    issues: List[LintIssue] = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in MF014_SCAN_EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, repo_root)
        first_seg = rel_root.split(os.sep)[0] if rel_root != '.' else ''
        if first_seg in MF014_ALLOWED_DIRS:
            dirs[:] = []
            continue
        for filename in files:
            rel_path = os.path.normpath(os.path.join(rel_root, filename)) if rel_root != '.' else filename
            if any(rel_path.startswith(p) for p in MF014_ALLOWED_PREFIXES):
                continue
            if rel_path in MF014_ALLOWED_FILES:
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in MF014_SCAN_EXTENSIONS:
                continue
            filepath = os.path.join(root, filename)
            issues.extend(_check_operator_values_in_file(filepath, rel_path))
    return issues


def get_all_python_files(directory: str = 'src') -> List[str]:
    """Get all Python files in directory."""
    files = []
    for root, _, filenames in os.walk(directory):
        for f in filenames:
            if f.endswith('.py'):
                files.append(os.path.join(root, f))
    return files


# MF012: Context-loaded docs must stay small so per-conversation overhead is bounded.
# When a doc trips this cap, move the oldest fully-resolved issues to the companion
# archive file and leave a one-row summary in the in-file archived-issues table.
# DO NOT raise the limit to make a tripped check pass.
CONTEXT_DOC_LIMITS = {
    '.claude/foundations/persistent_issues.md': 40_000,
}


def check_context_doc_sizes(repo_root: str = '.') -> List[LintIssue]:
    """MF012: enforce char-size caps on docs routinely loaded into model context."""
    issues: List[LintIssue] = []
    for rel_path, limit in CONTEXT_DOC_LIMITS.items():
        full = os.path.join(repo_root, rel_path)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size > limit:
            issues.append(LintIssue(
                rel_path, 0, Severity.ERROR, "MF012",
                f"File is {size:,} chars (limit {limit:,}). "
                f"Move oldest resolved issues to the archive; do not raise the limit.",
            ))
    return issues


def check_version_consistency(repo_root: Optional[str] = None) -> List[LintIssue]:
    """MF024: the version SSOT (src/__version__.py) must agree with pyproject +
    README badge/heading + CHANGELOG. Prevents the 4-way version drift the
    2026-07-07 audit found. Delegates to scripts/version_consistency_check.py
    (the standalone, also runnable on its own and mirrored to sister repos).

    repo_root defaults to THIS script's repo (not the cwd — a cwd-relative
    default made `lint.py --all` from any other directory emit a false hard
    ERROR, 2026-07-09 review)."""
    issues: List[LintIssue] = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if repo_root is None:
        repo_root = os.path.dirname(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        import version_consistency_check as vcc
    except ImportError as e:
        # Missing the guard itself is a real gap — unobservable is never a pass.
        return [LintIssue("scripts/version_consistency_check.py", 0, Severity.WARNING,
                          "MF024", f"version guard unavailable: {e}")]
    ssot, problems = vcc.check(repo_root)
    if ssot is None:
        return [LintIssue("src/__version__.py", 0, Severity.ERROR, "MF024", problems[0])]
    for msg in problems:
        issues.append(LintIssue("(version)", 0, Severity.ERROR, "MF024", msg))
    return issues


# MF017: hardened systemd units (ProtectHome=read-only) must whitelist all three
# canonical MeshForge data buckets in ReadWritePaths=. The bucket-class taxonomy
# is the contract documented in utils/sandbox_check.py:meshforge_writable_paths.
# Drift between the code's data path (e.g. _meshforge_data_dir() in
# utils/db_inventory.py) and the unit's ReadWritePaths= is the Issue #58 class:
# the service stays "active (running)" while every write fails in a callback
# exception. moc3 ran in that state for 18h on 2026-05-18 before detection.
#
# Audit rule: every contrib/systemd/*.service.in (and any other hardened
# unit template) with ProtectHome=read-only must have ReadWritePaths= that
# covers all three meshforge buckets. Use an inline comment
# "# audit-skip: <reason>" on the ReadWritePaths line to explicitly opt out
# (e.g. for a service that genuinely only needs .config — but think hard,
# because Issue #58 was exactly the case where "only needs .config + .cache"
# turned out to be wrong six months later).
MF017_REQUIRED_BUCKETS = (".config/meshforge", ".local/share/meshforge", ".cache/meshforge")

# D2 (2026-06-20): a hardened unit that declares itself an RNS client must also
# whitelist the RNS storage path, or it has the exact 2026-06-20 wx-total-loss
# EROFS latent (a shared-instance RNS client writes assembled Resources under
# <configdir>/storage; ProtectSystem=strict makes /etc read-only otherwise).
# The unit DECLARES the profile with the marker below (a self-documenting,
# unit-local statement of what it writes); MF017 then requires the derived
# path. The literal mirrors ReticulumPaths.ETC_STORAGE — the SSOT — and a test
# (test_lint_mf017 / test_sandbox_check) pins the two equal so they can't drift.
# The runtime preflight (sandbox_check.meshforge_writable_paths(rns_client=True))
# is the BACKSTOP for an un-marked unit: it probes the real path at startup,
# keyed to the service code, not this declaration.
MF017_RNS_CLIENT_MARKER = "mf-sandbox-requires: rns-storage"
MF017_RNS_STORAGE_PATH = "/etc/reticulum/storage"  # == ReticulumPaths.ETC_STORAGE


def _audit_one_systemd_unit(
    rel_path: str,
    content: str,
    required_buckets: tuple,
) -> List[LintIssue]:
    """Audit one hardened systemd unit for ReadWritePaths bucket coverage.

    Returns issues for this unit only. Outer caller iterates units and
    accumulates. Factored out so a per-unit `audit-skip:` marker doesn't
    suppress later units in the same run (the pre-2026-05-19 shape used
    `return issues` from inside the iteration, which short-circuited the
    entire MF017 audit — pattern-audit Finding #4).
    """
    issues: List[LintIssue] = []
    # Only audit hardened units. Units without ProtectHome (or with
    # ProtectHome=false) aren't subject to the trap.
    if 'ProtectHome=read-only' not in content and 'ProtectHome=yes' not in content:
        return issues

    # Collect every ReadWritePaths line (systemd allows multiple). Each
    # may be a space-separated list. Audit-skip is a per-line marker that
    # suppresses the check for THIS UNIT ONLY.
    whitelisted = []
    rwp_lineno = 0
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.split('#', 1)[0].strip()
        if stripped.startswith('ReadWritePaths='):
            if '# audit-skip:' in line:
                # Operator explicitly acknowledged the gap on this line.
                # Defer to their judgment for THIS UNIT but keep auditing
                # the rest. Pre-2026-05-19 this returned out of the whole
                # audit function — pattern-audit Finding #4.
                return issues
            if rwp_lineno == 0:
                rwp_lineno = lineno
            rest = stripped[len('ReadWritePaths='):]
            whitelisted.extend(rest.split())

    # If no ReadWritePaths at all, the unit is hardened-without-state —
    # also a smell, but probably intentional (a pure compute service).
    # Don't fire on this case; MF017 is specifically about the trap shape.
    if not whitelisted:
        return issues

    for bucket in required_buckets:
        if not any(bucket in p for p in whitelisted):
            issues.append(LintIssue(
                rel_path, rwp_lineno, Severity.ERROR, "MF017",
                f"hardened systemd unit (ProtectHome=read-only) missing "
                f"'{bucket}' in ReadWritePaths= — Issue #58 class. Add "
                f"'@HOME@/{bucket}' to the ReadWritePaths line, OR mark "
                f"the omission deliberate with an inline "
                f"'# audit-skip: <reason>' comment.",
            ))

    # D2: a unit that DECLARES the rns-client profile must grant the RNS storage
    # path, or it has the 2026-06-20 EROFS latent. Derived requirement, not a
    # per-unit hand-list (the #60 lesson).
    if MF017_RNS_CLIENT_MARKER in content:
        if not any(MF017_RNS_STORAGE_PATH in p for p in whitelisted):
            issues.append(LintIssue(
                rel_path, rwp_lineno, Severity.ERROR, "MF017",
                f"hardened systemd unit declares '{MF017_RNS_CLIENT_MARKER}' "
                f"but ReadWritePaths= is missing '{MF017_RNS_STORAGE_PATH}' — "
                f"the Issue #60 EROFS class. A shared-instance RNS client "
                f"assembles inbound Resources under that path; without it, "
                f"ProtectSystem=strict makes /etc read-only and multi-chunk "
                f"replies drop silently (the 2026-06-20 wx-dark incident). Add "
                f"'{MF017_RNS_STORAGE_PATH}' to the ReadWritePaths line.",
            ))
    return issues


def check_systemd_sandbox_paths(repo_root: str = '.') -> List[LintIssue]:
    """MF017: hardened systemd units must whitelist all three meshforge data buckets."""
    issues: List[LintIssue] = []
    contrib_dir = os.path.join(repo_root, 'contrib', 'systemd')
    if not os.path.isdir(contrib_dir):
        return issues

    for fname in sorted(os.listdir(contrib_dir)):
        if not fname.endswith('.service.in'):
            continue
        full = os.path.join(contrib_dir, fname)
        rel_path = os.path.relpath(full, repo_root)
        try:
            with open(full, 'r', encoding='utf-8') as f:
                content = f.read()
        except OSError:
            continue

        issues.extend(_audit_one_systemd_unit(
            rel_path, content, MF017_REQUIRED_BUCKETS,
        ))
    return issues


# MF018: the In-Domain Principle ratchet. The TUI must let the user do
# everything in the domain — including REPAIR — without quitting to a shell.
# A user-facing "run X manually" / editor-spawn / "run with sudo" string is a
# shell-escape defect (foundations/in_domain_principle.md). We can't fix the
# whole backlog at once, so this is a per-file ratchet: each handler file has a
# frozen baseline count of known escapes; the check ERRORs only when a file
# EXCEEDS its baseline (i.e. new code adds an escape). The baseline can only
# shrink — when an arc closes a gap, drop/decrement its entry here. Same
# regression-prevention shape as MF007's ALLOWLISTED set (Issue #29).
#
# Scope is src/launcher_tui/ — the operator-facing surface the principle
# governs. A legitimate cross-app/protocol case (e.g. an rnid hash to paste
# into Sideband) is exempted with an inline '# in-domain-ok: <reason>' marker.
# The CORRECT way to clear an MF018 failure is an in-app remediation action,
# not the marker.
MF018_SCAN_DIR = os.path.join('src', 'launcher_tui')
MF018_MARKER = '# in-domain-ok:'
MF018_PATTERNS = [
    re.compile(r"(?i)\b(try|run|install|create|copy|start)\b[^\n]{0,40}\bmanually\b"),
    re.compile(r"(?i)\bmanually\b[^\n]{0,30}\b(install|edit|create|copy|restart|run|set)\b"),
    re.compile(r"(?i)run\s+(with sudo|meshforge with sudo|the following|these commands)"),
    re.compile(r"(?i)install[^\n]{0,12}:\s?(sudo )?(apt|pip3?|pipx)\b"),
    re.compile(r"(?i)install (manually )?with[: ]"),
    re.compile(r"subprocess\.(run|call|Popen)\([^\n]*\b(nano|vim?|emacs)\b"),
    re.compile(r"\[\s*editor\b"),
    re.compile(r"(?i)\bpipx reinstall\b"),
    re.compile(r"(?i)(try|find it|run)[: ]\s*(sudo )?(lsof|pkill)\b"),
]

# Frozen 2026-05-29 (the foundation arc), RETIRED 2026-06-16 (the In-Domain arc).
# The backlog only ever shrank; as of the Class 5 install close-out + remainder
# sweep, EVERY src/launcher_tui/ file scans to 0 shell-escapes (real escapes
# closed via in-app actions/pointers; legitimate privilege/cross-app/protocol
# cases carry an inline '# in-domain-ok: <reason>' marker, which the counter
# skips). The baseline is therefore empty: every TUI file has an implicit
# baseline of 0, so ANY new bare shell-escape fails the build. Do NOT re-add
# entries to grant a file headroom — close the escape with an in-app action or
# mark a genuine exception (foundations/in_domain_principle.md).
MF018_BASELINE = {}


def _count_in_domain_escapes(filepath: str) -> tuple:
    """Return (count, [linenos]) of shell-escape patterns, skipping marked lines."""
    n = 0
    hits: List[int] = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for lineno, line in enumerate(f, 1):
                if MF018_MARKER in line:
                    continue
                for pat in MF018_PATTERNS:
                    if pat.search(line):
                        n += 1
                        hits.append(lineno)
                        break
    except (IOError, OSError):
        pass
    return n, hits


def check_in_domain_escapes(files: List[str], repo_root: str = '.') -> List[LintIssue]:
    """MF018: fail when a TUI file exceeds its frozen shell-escape baseline."""
    issues: List[LintIssue] = []
    for f in files:
        rel = os.path.relpath(f, repo_root) if os.path.isabs(f) else f
        rel = rel.replace(os.sep, '/')
        if not rel.startswith('src/launcher_tui/') or not rel.endswith('.py'):
            continue
        if not os.path.isfile(f):
            continue
        count, hits = _count_in_domain_escapes(f)
        baseline = MF018_BASELINE.get(rel, 0)
        if count > baseline:
            issues.append(LintIssue(
                rel, hits[-1] if hits else 0, Severity.ERROR, "MF018",
                f"{count} shell-escape pattern(s) — baseline {baseline}. New TUI "
                f"code must offer an in-app action, not a shell instruction "
                f"(foundations/in_domain_principle.md). Resolve with an in-app "
                f"remediation; for a legitimate cross-app/protocol case add an "
                f"inline '# in-domain-ok: <reason>' marker. The baseline only shrinks.",
            ))
    return issues


# MF025: the file-size ratchet. CLAUDE.md has said "ALWAYS split files
# exceeding 1,500 lines" since the foundation docs — but nothing enforced it,
# and by 2026-07-13 five src/ files had silently drifted past the cap (the
# worst, watchdog_probes_drift.py, to 2,625). Same lesson as Issue #29 and
# the model-agnostic-harness principle: a rule that lives in model memory is
# a house of cards; a rule that lives in an executable gate survives model
# handoffs. Ratchet shape mirrors MF018: known offenders are FROZEN at their
# 2026-07-13 line counts and may only shrink; everything else (and any new
# file) fails above the limit. When an arc splits a baseline file, delete its
# entry. DO NOT add entries to grant new headroom — split the file.
MF025_LINE_LIMIT = 1_500

# Frozen 2026-07-13. Entries may only shrink or be deleted.
MF025_BASELINE = {
    'src/utils/watchdog_probes_drift.py': 2625,
    'src/utils/watchdog_probes_gateway.py': 1638,
    'src/utils/map_data_collector.py': 1566,
    'src/gateway/rns_bridge.py': 1544,
    'src/utils/node_history.py': 1510,
}


def check_file_size_ratchet(files: List[str], repo_root: str = '.') -> List[LintIssue]:
    """MF025: fail when a src/ python file exceeds 1,500 lines (or, for a
    frozen-baseline offender, exceeds its frozen size)."""
    issues: List[LintIssue] = []
    for f in files:
        rel = os.path.relpath(f, repo_root) if os.path.isabs(f) else f
        rel = rel.replace(os.sep, '/')
        if not rel.startswith('src/') or not rel.endswith('.py'):
            continue
        if not os.path.isfile(f):
            continue
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                lines = sum(1 for _ in fh)
        except (IOError, OSError):
            continue
        limit = max(MF025_LINE_LIMIT, MF025_BASELINE.get(rel, 0))
        if lines > limit:
            frozen = rel in MF025_BASELINE
            issues.append(LintIssue(
                rel, lines, Severity.ERROR, "MF025",
                f"{lines:,} lines exceeds the "
                f"{'frozen baseline of ' + format(limit, ',') if frozen else '1,500-line cap'}"
                f" — split the file (CLAUDE.md size rule). The baseline only "
                f"shrinks; do not add or raise entries to grant headroom.",
            ))
    return issues


# MF021: the mini-dudeai observation-only invariant. mini-dudeai is a
# deterministic, dependency-free stdlib rule-loop agent. Its doctrine is that
# the engine and ALL built-in sources/actions OBSERVE the system (read files,
# parse /proc, http GET) but NEVER EXECUTE it — no subprocess, no systemctl, no
# os.system/popen/exec, no Popen, no shell=True. This was true by grep but had
# no automated guard (unlike Issue #29's MF007/008/009/019). MF021 pins it.
#
# Scope is deliberately narrow: engine.py + sources/*.py + actions/*.py. NOT
# rollup.py / dreams.py (cloud-session orchestration tools that legitimately
# ssh-fan the fleet and are NOT engine/sources/actions). The check is on CODE
# lines only — a token in a comment or docstring is fine (e.g. boot_health.py's
# module docstring "mini-dudeai is observation-only (no subprocess)").
MF021_SCAN_ROOTS = (
    os.path.join('src', 'mini_dudeai', 'engine.py'),
    os.path.join('src', 'mini_dudeai', 'sources'),
    os.path.join('src', 'mini_dudeai', 'actions'),
)
# Word-boundary patterns for the forbidden execution surfaces. shell=True is a
# kwarg (allow optional whitespace around the =).
MF021_FORBIDDEN = (
    (re.compile(r'\bsubprocess\b'), 'subprocess'),
    (re.compile(r'\bsystemctl\b'), 'systemctl'),
    (re.compile(r'\bos\.system\b'), 'os.system'),
    (re.compile(r'\bos\.popen\b'), 'os.popen'),
    (re.compile(r'\bos\.exec\w*\b'), 'os.exec*'),
    (re.compile(r'\bPopen\b'), 'Popen'),
    (re.compile(r'\bshell\s*=\s*True\b'), 'shell=True'),
)


def _mf021_code_lines(content: str):
    """Yield (lineno, code_text) for lines outside comments and docstrings.

    Tracks triple-quoted string state so a forbidden token mentioned in a
    docstring is not flagged; strips trailing ``# ...`` inline comments from
    code lines. Conservative: a line that opens/closes a docstring is treated
    as docstring (not code), matching how the other rules skip string-literal
    lines. A one-line triple-quoted docstring on its own line is skipped too.
    """
    in_docstring = False
    doc_delim = ''
    for lineno, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if in_docstring:
            # Look for the closing delimiter on this line.
            if doc_delim in stripped:
                in_docstring = False
            continue
        # A line that starts a docstring/triple-quoted block.
        for delim in ('"""', "'''"):
            if stripped.startswith(delim) or stripped.startswith('r' + delim) \
                    or stripped.startswith('f' + delim):
                body = stripped.split(delim, 1)[1]
                # Closed on the same line? (e.g. a one-line docstring)
                if delim in body:
                    body = ''  # whole thing is a string literal — skip
                else:
                    in_docstring = True
                    doc_delim = delim
                break
        else:
            # Not a docstring opener. Skip pure comment lines.
            if stripped.startswith('#'):
                continue
            # Strip an inline comment tail (best-effort; not quote-aware, but
            # the forbidden tokens are distinctive enough that a token only in
            # a trailing comment is exceedingly rare and erring toward the
            # invariant is the safe direction).
            code = raw.split('#', 1)[0]
            yield lineno, code
            continue
        # If we entered a docstring on this opener line, nothing to yield.
        continue


def _mf021_scan_file(filepath: str, rel_path: str) -> List[LintIssue]:
    issues: List[LintIssue] = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except (IOError, OSError):
        return issues
    for lineno, code in _mf021_code_lines(content):
        for pattern, token in MF021_FORBIDDEN:
            if pattern.search(code):
                issues.append(LintIssue(
                    rel_path, lineno, Severity.ERROR, "MF021",
                    f"'{token}' in mini-dudeai {rel_path} — the engine, sources "
                    f"and actions are OBSERVATION-ONLY (read state, never execute "
                    f"it). No subprocess/systemctl/os.system/popen/exec/Popen/"
                    f"shell=True. If you need to run something, it belongs in a "
                    f"cloud-session tool (rollup.py), not the deterministic loop.",
                ))
    return issues


def check_mini_dudeai_observation_only(repo_root: str = '.') -> List[LintIssue]:
    """MF021: pin the mini-dudeai observation-only invariant (no execution)."""
    issues: List[LintIssue] = []
    for root in MF021_SCAN_ROOTS:
        full = os.path.join(repo_root, root)
        if os.path.isfile(full):
            rel = os.path.relpath(full, repo_root).replace(os.sep, '/')
            issues.extend(_mf021_scan_file(full, rel))
        elif os.path.isdir(full):
            for fname in sorted(os.listdir(full)):
                if not fname.endswith('.py'):
                    continue
                fpath = os.path.join(full, fname)
                rel = os.path.relpath(fpath, repo_root).replace(os.sep, '/')
                issues.extend(_mf021_scan_file(fpath, rel))
    return issues


# ─────────────────────────────────────────────────────────────────────────
# MF022: pip/apt hygiene in shell installers (install-hardening arc).
# Shell scripts must route package installs through scripts/lib/install_common.sh
# so pip-presence (ensure_pip), PEP 668, and the REAL exit code are handled —
# the fresh-user "had to install pip by hand" failure + the configure_gateway
# `pip … | tail` exit-code mask. `lint_file` is .py-only, so (like MF014) MF022
# scans shell files via its own pass.
# ─────────────────────────────────────────────────────────────────────────
MF022_SCAN_EXTENSIONS = {'.sh', '.bash'}

# The lib DEFINES the sanctioned wrappers (it legitimately constructs `pip
# install` / `apt-get install`); lint.py + the rule's own test carry example
# strings. Exempt them.
MF022_ALLOWED_FILES = {
    'scripts/lib/install_common.sh',
    'scripts/lint.py',
    'tests/test_lint_mf022.py',
}

MF022_PIPE_MASK = re.compile(r'\bpip3?\s+install\b.*\|\s*(tail|head)\b')
MF022_BARE_PIP = re.compile(r'\bpip3?\s+install\b')
MF022_APT_SWALLOW = re.compile(r'\bapt(-get)?\s+install\b.*&>\s*/dev/null')


def _match_in_quotes(line: str, pos: int) -> bool:
    """True when the match at `pos` sits inside a quoted string — i.e. it is a
    fix-hint / echo / dry-run preview, not an actual command. An odd count of
    quotes before the match means we are inside one."""
    prefix = line[:pos]
    return (prefix.count('"') % 2 == 1) or (prefix.count("'") % 2 == 1)


def _check_pip_invocations_in_file(filepath: str, rel_path: str) -> List[LintIssue]:
    issues: List[LintIssue] = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for lineno, line in enumerate(f, 1):
                if line.lstrip().startswith('#'):
                    continue
                m = MF022_PIPE_MASK.search(line)
                if m and not _match_in_quotes(line, m.start()):
                    issues.append(LintIssue(
                        rel_path, lineno, Severity.ERROR, "MF022",
                        "pip install piped to tail/head masks pip's exit code — route "
                        "through mf_pip_install (scripts/lib/install_common.sh) and check rc",
                    ))
                    continue
                m = MF022_BARE_PIP.search(line)
                if (m and not _match_in_quotes(line, m.start())
                        and '-m pip' not in line and 'mf_pip_install' not in line):
                    issues.append(LintIssue(
                        rel_path, lineno, Severity.WARNING, "MF022",
                        "bare 'pip install' in a shell script — route through mf_pip_install "
                        "(scripts/lib/install_common.sh) for pip-presence + PEP 668 + checked rc",
                    ))
                    continue
                m = MF022_APT_SWALLOW.search(line)
                if m and not _match_in_quotes(line, m.start()):
                    issues.append(LintIssue(
                        rel_path, lineno, Severity.WARNING, "MF022",
                        "apt-get install with &>/dev/null hides the failure reason — "
                        "use mf_apt_install (scripts/lib/install_common.sh)",
                    ))
    except (IOError, OSError):
        pass
    return issues


def _mf022_exempt(rel_path: str) -> bool:
    if rel_path in MF022_ALLOWED_FILES:
        return True
    ext = os.path.splitext(rel_path)[1].lower()
    return ext not in MF022_SCAN_EXTENSIONS


def check_pip_invocations_in_files(files: List[str], repo_root: str = '.') -> List[LintIssue]:
    """MF022: scan a specific set of files (e.g. staged) for shell pip/apt hygiene."""
    issues: List[LintIssue] = []
    for f in files:
        rel_path = os.path.relpath(f, repo_root) if os.path.isabs(f) else f
        if _mf022_exempt(rel_path) or not os.path.isfile(f):
            continue
        issues.extend(_check_pip_invocations_in_file(f, rel_path))
    return issues


def check_pip_invocations_full_tree(repo_root: str = '.') -> List[LintIssue]:
    """MF022: scan the whole repo tree's shell scripts for pip/apt hygiene."""
    issues: List[LintIssue] = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in MF014_SCAN_EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, repo_root)
        for filename in files:
            rel_path = os.path.normpath(os.path.join(rel_root, filename)) if rel_root != '.' else filename
            if _mf022_exempt(rel_path):
                continue
            issues.extend(_check_pip_invocations_in_file(os.path.join(root, filename), rel_path))
    return issues


# ─────────────────────────────────────────────────────────────────────────
# MF023: serving never blocks on collection — bounded-collect chokepoint.
#
# Invariant 1 of the recurring map-wedge class (#17/#70/#71/#73/#75/#76 +
# 2026-06-23 spin): the meshtastic interface constructor blocks until the full
# nodedb sync completes, and a wedged daemon can stall it up to its ~900s idle
# cap. If a serving/collection path creates that interface OUTSIDE the bounded
# worker, it can pin the map collector's _collect_lock and wedge every
# /api/nodes/geojson + /api/network/topology request behind it. So the blocking
# create (_create_interface, the connection-manager method that does the sync)
# must live ONLY inside _collect_interface_bounded. Mirror of MF019's chokepoint
# shape (raw TCPInterface/SerialInterface construction is already MF007's job).
# AST-scoped because the rule is "which function is this call in", not a regex.
# ─────────────────────────────────────────────────────────────────────────
MF023_SCAN_FILES = (
    os.path.join('src', 'utils', '_map_collector_meshtastic.py'),
)
MF023_BOUNDED_HELPER = '_collect_interface_bounded'
MF023_BLOCKING_CALLS = {'_create_interface'}


def _mf023_call_name(func) -> str:
    """Last attribute/name of a call target: manager._create_interface -> the
    attr; TCPInterface(...) -> the name. '' for anything else."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ''


def _mf023_scan_file(filepath: str, rel_path: str) -> List[LintIssue]:
    issues: List[LintIssue] = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            src = f.read()
    except (IOError, OSError):
        return issues
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return issues  # py_compile / the parser-based rules surface syntax errors

    def walk(node, func_stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Nested fns (e.g. the bounded helper's _worker) inherit the
                # ancestry, so a call inside _worker still counts as "inside
                # _collect_interface_bounded".
                walk(child, func_stack + [child.name])
                continue
            if isinstance(child, ast.Call):
                name = _mf023_call_name(child.func)
                if name in MF023_BLOCKING_CALLS and MF023_BOUNDED_HELPER not in func_stack:
                    issues.append(LintIssue(
                        rel_path, child.lineno, Severity.ERROR, "MF023",
                        f"'{name}()' (blocking meshtastic nodedb sync) called "
                        f"outside {MF023_BOUNDED_HELPER}() — serving must never "
                        f"block on collection. Route it through the bounded "
                        f"helper so a wedged daemon can't pin _collect_lock (the "
                        f"2026-06-23 moc1 spin). If a new call site is genuinely "
                        f"bounded/isolated, add it to MF023_BLOCKING_CALLS' "
                        f"allowlist in lint.py + TestServingNeverBlocksOnCollection."
                    ))
            walk(child, func_stack)

    walk(tree, [])
    return issues


def check_bounded_collect_chokepoint(repo_root: str = '.') -> List[LintIssue]:
    """MF023: confine the blocking meshtastic nodedb sync to the bounded helper."""
    issues: List[LintIssue] = []
    for rel in MF023_SCAN_FILES:
        full = os.path.join(repo_root, rel)
        if os.path.isfile(full):
            r = os.path.relpath(full, repo_root).replace(os.sep, '/')
            issues.extend(_mf023_scan_file(full, r))
    return issues


def main():
    parser = argparse.ArgumentParser(description='MeshForge Linter')
    parser.add_argument('files', nargs='*', help='Files to lint')
    parser.add_argument('--all', action='store_true', help='Lint all Python files in src/')
    parser.add_argument('--staged', action='store_true', help='Lint staged files only')
    parser.add_argument('--format', choices=['text', 'json', 'github'], default='text',
                       help='Output format')
    parser.add_argument('--severity', choices=['error', 'warning', 'info'], default='info',
                       help='Minimum severity to report')
    args = parser.parse_args()

    # Determine files to lint
    if args.all:
        files = get_all_python_files('src')
    elif args.staged:
        files = get_staged_files()
    elif args.files:
        files = args.files
    else:
        # Default: lint src/
        files = get_all_python_files('src')

    if not files:
        print("No files to lint.")
        return 0

    # Run linter
    linter = MeshForgeLinter()
    issues = linter.lint_files(files)

    # MF012: doc-size cap (skip in --staged mode — only relevant to whole-repo checks)
    if not args.staged:
        issues.extend(check_context_doc_sizes())

    # MF024: version SSOT vs pyproject/README drift (repo-level; the 4-way-drift
    # guard). Whole-repo only — a one-file --staged PR shouldn't pay the audit.
    if not args.staged:
        issues.extend(check_version_consistency())

    # MF014: operator-value blocklist (broader than .py — scans templates/scripts/docs too)
    if args.staged:
        issues.extend(check_operator_values_in_files(get_staged_files_all_types()))
    else:
        issues.extend(check_operator_values_full_tree())

    # MF022: shell-installer pip/apt hygiene (broader than .py — scans .sh/.bash).
    if args.staged:
        issues.extend(check_pip_invocations_in_files(get_staged_files_all_types()))
    else:
        issues.extend(check_pip_invocations_full_tree())

    # MF018: In-Domain Principle ratchet — TUI shell-escapes vs frozen baseline.
    if args.staged:
        issues.extend(check_in_domain_escapes(get_staged_files()))
    else:
        issues.extend(check_in_domain_escapes(get_all_python_files(MF018_SCAN_DIR)))

    # MF017: systemd sandbox writable-path drift (Issue #58 class). Walks
    # the whole contrib/systemd/ tree — drift in a unit not touched by this
    # PR still matters. Skip in --staged mode so a one-file PR doesn't pay
    # the audit cost.
    if not args.staged:
        issues.extend(check_systemd_sandbox_paths())

    # MF025: file-size ratchet (1,500-line cap, frozen offender baseline).
    # Cheap (line counts of the files already selected), so it runs in both
    # whole-tree and --staged modes — a file blowing past the cap must fail
    # in the same commit that grew it.
    issues.extend(check_file_size_ratchet(files))

    # MF021: mini-dudeai observation-only invariant. Scans the fixed engine/
    # sources/actions set (cheap), so run it in both whole-tree and --staged
    # modes — an execution token sneaking into the loop must always fail.
    issues.extend(check_mini_dudeai_observation_only())

    # MF023: bounded-collect chokepoint. Scans one fixed file (cheap), so run
    # it in both whole-tree and --staged modes — an unbounded interface create
    # sneaking into a serving path must always fail.
    issues.extend(check_bounded_collect_chokepoint())

    # Filter by severity
    severity_order = {'error': 0, 'warning': 1, 'info': 2}
    min_severity = severity_order[args.severity]
    issues = [i for i in issues if severity_order[i.severity.value] <= min_severity]

    # Output results
    if args.format == 'json':
        import json
        print(json.dumps([{
            'file': i.file,
            'line': i.line,
            'severity': i.severity.value,
            'code': i.code,
            'message': i.message
        } for i in issues], indent=2))
    elif args.format == 'github':
        for issue in issues:
            level = 'error' if issue.severity == Severity.ERROR else 'warning'
            print(f"::{level} file={issue.file},line={issue.line}::{issue.code}: {issue.message}")
    else:
        for issue in issues:
            print(issue)

    # Summary
    errors = sum(1 for i in issues if i.severity == Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity == Severity.WARNING)

    if issues:
        print(f"\nFound {len(issues)} issues ({errors} errors, {warnings} warnings)")

    # Exit with error if there are errors
    return 1 if errors > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
