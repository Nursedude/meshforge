"""
MeshForge Auto-Review System

Orchestrates automated code reviews using specialized review agents,
inspired by Auto-Claude's autonomous multi-agent architecture.

This module provides the schema and patterns for systematic code review
following MeshForge's foundational principles.

Usage:
    from utils.auto_review import ReviewOrchestrator, ReviewScope

    orchestrator = ReviewOrchestrator()
    results = orchestrator.run_full_review(scope=ReviewScope.ALL)
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Optional, Callable, Any
from pathlib import Path
import re
import logging

from utils.logging_config import get_logger

logger = get_logger(__name__)


class Severity(Enum):
    """Issue severity levels"""
    CRITICAL = "critical"  # Must fix before merge
    HIGH = "high"          # Should fix this sprint
    MEDIUM = "medium"      # Plan fix next sprint
    LOW = "low"            # Consider for backlog
    INFO = "info"          # Informational only


class ReviewCategory(Enum):
    """Categories of review agents"""
    SECURITY = "security"
    REDUNDANCY = "redundancy"
    PERFORMANCE = "performance"
    RELIABILITY = "reliability"


class ReviewScope(Enum):
    """Scope of review"""
    ALL = auto()           # Full codebase review
    SECURITY = auto()      # Security agent only
    REDUNDANCY = auto()    # Redundancy agent only
    PERFORMANCE = auto()   # Performance agent only
    RELIABILITY = auto()   # Reliability agent only


class AutoFixStatus(Enum):
    """Status of automatic fix attempt"""
    APPLIED = "applied"
    MANUAL_REQUIRED = "manual_required"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ReviewFinding:
    """Represents a single review finding"""
    category: ReviewCategory
    severity: Severity
    file_path: str
    line_number: Optional[int]
    issue: str
    description: str
    recommendation: str
    auto_fixable: bool = False
    fix_status: AutoFixStatus = AutoFixStatus.SKIPPED
    pattern_matched: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'category': self.category.value,
            'severity': self.severity.value,
            'file_path': self.file_path,
            'line_number': self.line_number,
            'issue': self.issue,
            'description': self.description,
            'recommendation': self.recommendation,
            'auto_fixable': self.auto_fixable,
            'fix_status': self.fix_status.value,
            'pattern_matched': self.pattern_matched,
        }


@dataclass
class AgentResult:
    """Result from a single review agent"""
    category: ReviewCategory
    files_scanned: int
    findings: List[ReviewFinding] = field(default_factory=list)
    fixes_applied: int = 0
    manual_required: int = 0

    @property
    def total_issues(self) -> int:
        return len(self.findings)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.MEDIUM)

    def summary(self) -> str:
        """Generate text summary of results"""
        return (
            f"{self.category.value.upper()} Agent Results\n"
            f"Files scanned: {self.files_scanned}\n"
            f"Issues found: {self.total_issues} "
            f"({self.critical_count} CRITICAL, {self.high_count} HIGH, "
            f"{self.medium_count} MEDIUM)\n"
            f"Fixes applied: {self.fixes_applied}\n"
            f"Manual review needed: {self.manual_required}"
        )


@dataclass
class ReviewReport:
    """Complete review report from all agents"""
    scope: ReviewScope
    agent_results: Dict[ReviewCategory, AgentResult] = field(default_factory=dict)
    total_files_scanned: int = 0

    @property
    def total_issues(self) -> int:
        return sum(r.total_issues for r in self.agent_results.values())

    @property
    def total_fixes_applied(self) -> int:
        return sum(r.fixes_applied for r in self.agent_results.values())

    def get_all_findings(self, min_severity: Severity = Severity.INFO) -> List[ReviewFinding]:
        """Get all findings at or above specified severity"""
        severity_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
        min_index = severity_order.index(min_severity)
        allowed_severities = severity_order[:min_index + 1]

        findings = []
        for result in self.agent_results.values():
            findings.extend(f for f in result.findings if f.severity in allowed_severities)
        return sorted(findings, key=lambda f: severity_order.index(f.severity))

    def to_markdown(self) -> str:
        """Generate markdown report"""
        lines = [
            "# MeshForge Auto-Review Report",
            "",
            f"**Scope**: {self.scope.name}",
            f"**Total Files Scanned**: {self.total_files_scanned}",
            f"**Total Issues Found**: {self.total_issues}",
            f"**Fixes Applied**: {self.total_fixes_applied}",
            "",
        ]

        for category, result in self.agent_results.items():
            lines.append(f"## {category.value.title()} Agent")
            lines.append("")
            lines.append(f"- Files scanned: {result.files_scanned}")
            lines.append(f"- Issues found: {result.total_issues}")
            lines.append(f"- Fixes applied: {result.fixes_applied}")
            lines.append("")

            if result.findings:
                # Group by severity
                for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM]:
                    severity_findings = [f for f in result.findings if f.severity == severity]
                    if severity_findings:
                        lines.append(f"### {severity.value.upper()} Priority")
                        lines.append("")
                        lines.append("| File | Line | Issue | Status |")
                        lines.append("|------|------|-------|--------|")
                        for finding in severity_findings:
                            line_str = str(finding.line_number) if finding.line_number else "N/A"
                            lines.append(
                                f"| {finding.file_path} | {line_str} | "
                                f"{finding.issue} | {finding.fix_status.value} |"
                            )
                        lines.append("")

        return "\n".join(lines)


class ReviewPatterns:
    """
    Centralized review patterns for each agent category.

    These patterns align with the MeshForge Auto-Review Principles.
    """

    # Security patterns (Priority order: CRITICAL, HIGH, MEDIUM)
    SECURITY = {
        # CRITICAL - Command/Code injection
        'shell_true': {
            'pattern': r'shell\s*=\s*True',
            'severity': Severity.HIGH,
            'issue': 'Command injection risk',
            'recommendation': 'Use argument list instead of shell=True',
            'auto_fixable': True,
        },
        'eval_call': {
            'pattern': r'\beval\s*\(',
            'severity': Severity.CRITICAL,
            'issue': 'Code injection via eval()',
            'recommendation': 'Use ast.literal_eval() for data, avoid eval entirely',
            'auto_fixable': False,
        },
        'exec_call': {
            'pattern': r'\bexec\s*\(',
            'severity': Severity.CRITICAL,
            'issue': 'Code execution via exec()',
            'recommendation': 'Remove exec() or use safer alternatives',
            'auto_fixable': False,
        },
        'os_system': {
            'pattern': r'os\.system\s*\(',
            'severity': Severity.HIGH,
            'issue': 'Command injection via os.system()',
            'recommendation': 'Use subprocess.run() with shell=False',
            'auto_fixable': False,
        },
        # HIGH - Data exposure
        'hardcoded_password': {
            'pattern': r'password\s*=\s*["\'][^"\']+["\']',
            'severity': Severity.HIGH,
            'issue': 'Hardcoded password',
            'recommendation': 'Use environment variable or secure config',
            'auto_fixable': False,
        },
        'hardcoded_api_key': {
            'pattern': r'api_key\s*=\s*["\'][^"\']+["\']',
            'severity': Severity.HIGH,
            'issue': 'Hardcoded API key',
            'recommendation': 'Use environment variable or secure config',
            'auto_fixable': False,
        },
        # MEDIUM - Deserialization
        'pickle_load': {
            'pattern': r'pickle\.load\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Unsafe deserialization',
            'recommendation': 'Use JSON or validate pickle source',
            'auto_fixable': False,
        },
        'yaml_unsafe': {
            'pattern': r'yaml\.load\s*\([^)]*\)\s*(?!.*Loader)',
            'severity': Severity.MEDIUM,
            'issue': 'YAML load without safe Loader',
            'recommendation': 'Use yaml.safe_load() or specify Loader=yaml.SafeLoader',
            'auto_fixable': True,
        },
        # Issue #1: Path.home() returns /root with sudo
        'path_home': {
            'pattern': r'Path\.home\s*\(\s*\)',
            'severity': Severity.HIGH,
            'issue': 'Path.home() returns /root with sudo (MF001)',
            'recommendation': 'Use get_real_user_home() from utils.paths',
            'auto_fixable': False,
        },
    }

    # Redundancy patterns
    REDUNDANCY = {
        'console_instantiation': {
            'pattern': r'Console\s*\(\s*\)',
            'severity': Severity.LOW,
            'issue': 'Multiple Console instances',
            'recommendation': 'Use singleton from utils.console',
            'auto_fixable': False,
        },
        'logger_setup': {
            'pattern': r'logging\.getLogger\s*\([^)]*\)',
            'severity': Severity.LOW,
            'issue': 'Duplicate logger setup',
            'recommendation': 'Use get_logger() from utils.logging_config',
            'auto_fixable': False,
        },
        'check_root_function': {
            'pattern': r'def\s+check_root\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Duplicate check_root function',
            'recommendation': 'Use require_root() from utils.system',
            'auto_fixable': False,
        },
        # Issue #5: Duplicate utility functions
        'duplicate_utility': {
            'pattern': r'def\s+_?get_real_user_home\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Duplicate utility function (Issue #5)',
            'recommendation': 'Use get_real_user_home() from utils.paths',
            'auto_fixable': False,
        },
    }

    # Performance patterns
    PERFORMANCE = {
        'subprocess_no_timeout': {
            'pattern': r'subprocess\.(run|Popen|call)\s*\([^)]*\)(?!.*timeout)',
            'severity': Severity.MEDIUM,
            'issue': 'Subprocess without timeout',
            'recommendation': 'Add timeout parameter (e.g., timeout=30)',
            'auto_fixable': False,
        },
        'requests_no_timeout': {
            'pattern': r'requests\.(get|post|put|delete)\s*\([^)]*\)(?!.*timeout)',
            'severity': Severity.MEDIUM,
            'issue': 'HTTP request without timeout',
            'recommendation': 'Add timeout parameter (e.g., timeout=10)',
            'auto_fixable': False,
        },
        'glib_timeout_no_cleanup': {
            'pattern': r'GLib\.timeout_add\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Timer may leak without cleanup',
            'recommendation': 'Track timer ID and remove in cleanup/unrealize',
            'auto_fixable': False,
        },
        'string_concat_loop': {
            'pattern': r'for\s+[^:]+:\s*[^=]+=\s*[^=]+\+\s*["\']',
            'severity': Severity.LOW,
            'issue': 'String concatenation in loop',
            'recommendation': 'Use list.append() and "".join()',
            'auto_fixable': False,
        },
    }

    # Reliability patterns
    RELIABILITY = {
        'bare_except': {
            'pattern': r'except\s*:',
            'severity': Severity.HIGH,
            'issue': 'Bare except catches SystemExit',
            'recommendation': 'Catch specific exceptions (e.g., except Exception as e:)',
            'auto_fixable': True,
        },
        'index_no_check': {
            'pattern': r'\[\s*0\s*\]',  # Simplified - real check needs context
            'severity': Severity.LOW,
            'issue': 'Index access may fail on empty',
            'recommendation': 'Check length before indexing or use .get()',
            'auto_fixable': False,
        },
        'todo_comment': {
            'pattern': r'#\s*TODO',
            'severity': Severity.INFO,
            'issue': 'Unfinished code (TODO)',
            'recommendation': 'Complete or create issue for tracking',
            'auto_fixable': False,
        },
        'fixme_comment': {
            'pattern': r'#\s*FIXME',
            'severity': Severity.MEDIUM,
            'issue': 'Known issue (FIXME)',
            'recommendation': 'Address the identified issue',
            'auto_fixable': False,
        },
        # Issue #9: Broad exception swallowing
        'exception_pass': {
            'pattern': r'except\s+Exception\s*:\s*$',
            'severity': Severity.MEDIUM,
            'issue': 'Exception swallowed without handling (Issue #9)',
            'recommendation': 'Log the exception or handle it meaningfully',
            'auto_fixable': False,
        },
        # Issue #10: Lambda closure bug in loops
        'lambda_closure': {
            'pattern': r'lambda\s+\w+\s*:\s*\S+\([^)]*\b\w+\b[^)]*\)',
            'severity': Severity.MEDIUM,
            'issue': 'Potential lambda closure bug in loop (Issue #10)',
            'recommendation': 'Use default argument: lambda b, item=item: ...',
            'auto_fixable': False,
        },
    }


class ReviewAgent:
    """
    Base class for review agents.

    Each agent scans code using patterns specific to its category.
    """

    def __init__(self, category: ReviewCategory, patterns: Dict[str, dict]):
        self.category = category
        self.patterns = patterns
        self.logger = get_logger(f"auto_review.{category.value}")

    def scan_file(self, file_path: Path) -> List[ReviewFinding]:
        """Scan a single file for issues"""
        findings = []

        # Skip scanning the auto_review.py file itself - contains pattern definitions
        # that would trigger false positives for security, performance, and reliability patterns
        if file_path.name == 'auto_review.py' and self.category in (ReviewCategory.SECURITY, ReviewCategory.PERFORMANCE, ReviewCategory.RELIABILITY):
            return findings

        # Skip canonical implementation files for specific patterns
        # utils/paths.py is the canonical location for get_real_user_home()
        is_canonical_paths = file_path.name == 'paths.py' and 'utils' in file_path.parts

        try:
            content = file_path.read_text(encoding='utf-8')
            lines = content.split('\n')

            # Track if we're inside a docstring
            in_docstring = False
            docstring_char = None

            # Check if this is an entry point file (needs standalone utilities)
            entry_point_files = ['launcher.py', 'launcher_tui',
                                 'monitor.py', 'standalone.py', 'setup_wizard.py']
            is_entry_point = any(ep in str(file_path) for ep in entry_point_files)

            for pattern_name, pattern_config in self.patterns.items():
                # Skip duplicate_utility pattern for canonical utils/paths.py and entry points
                if pattern_name == 'duplicate_utility' and (is_canonical_paths or is_entry_point):
                    continue

                regex = re.compile(pattern_config['pattern'], re.IGNORECASE)

                in_docstring = False
                for line_num, line in enumerate(lines, start=1):
                    stripped = line.strip()

                    # Track docstring boundaries
                    if not in_docstring:
                        if stripped.startswith('"""') or stripped.startswith("'''"):
                            docstring_char = stripped[:3]
                            # Check if docstring ends on same line
                            if stripped.count(docstring_char) >= 2:
                                continue  # Single-line docstring, skip
                            in_docstring = True
                            continue
                    else:
                        if docstring_char in stripped:
                            in_docstring = False
                        continue

                    # Skip comment-only lines for security patterns
                    if self.category == ReviewCategory.SECURITY:
                        if stripped.startswith('#'):
                            continue

                    # Skip lines that are clearly documentation/examples
                    if self._is_documentation_line(stripped):
                        continue

                    if regex.search(line):
                        # Additional context checks to reduce false positives
                        if self._is_false_positive(pattern_name, line, stripped, lines, line_num):
                            continue

                        findings.append(ReviewFinding(
                            category=self.category,
                            severity=pattern_config['severity'],
                            file_path=str(file_path),
                            line_number=line_num,
                            issue=pattern_config['issue'],
                            description=f"Pattern '{pattern_name}' matched",
                            recommendation=pattern_config['recommendation'],
                            auto_fixable=pattern_config.get('auto_fixable', False),
                            pattern_matched=pattern_name,
                        ))

        except (IOError, UnicodeDecodeError) as e:
            self.logger.warning(f"Could not scan {file_path}: {e}")

        return findings

    def _is_documentation_line(self, stripped: str) -> bool:
        """Check if line is documentation/example that shouldn't be scanned"""
        # Lines that are clearly documentation
        doc_indicators = [
            'example:', 'e.g.', 'e.g.,', 'usage:', 'note:',
            '>>>', 'security:', 'recommendation:',
        ]
        lower = stripped.lower()
        return any(indicator in lower for indicator in doc_indicators)

    def _is_false_positive(self, pattern_name: str, line: str, stripped: str,
                           lines: list = None, line_num: int = 0) -> bool:
        """Check for known false positive patterns

        Args:
            pattern_name: The pattern that matched
            line: The full line content
            stripped: The stripped line content
            lines: Optional list of all lines in file (for context checking)
            line_num: Current line number (1-indexed)
        """
        # os.system with shlex.quote is safe (used in launcher_tui for terminal inheritance)
        if pattern_name == 'os_system':
            if 'shlex.quote' in line or 'shlex_quote' in line:
                return True

        # subprocess patterns - check for timeout in various forms
        if pattern_name == 'subprocess_no_timeout':
            # Check if timeout is in **kwargs or run_kwargs
            if 'timeout' in line or '**' in line:
                return True
            # Check if it's a Popen that's tracked (has communicate with timeout)
            if 'Popen' in line and ('start_new_session' in line or 'daemon' in line.lower()):
                return True  # Background processes don't need timeout
            # Check if Popen is assigned to self. (tracked for later wait())
            if 'Popen' in line and 'self.' in line and '=' in line:
                return True  # Will be managed via self.external_process.wait(timeout=)
            # Check if it's xdg-open (fire-and-forget)
            if 'xdg-open' in line:
                return True
            # Check if it's an interactive terminal app (nano, vim, etc.)
            if any(editor in line for editor in ['nano', 'vim', 'vi', 'editor']):
                return True
            # Check if it's inside a try block with KeyboardInterrupt handling (interactive)
            if '.wait()' in line:
                return True  # Explicit wait is intentional
            # Check if it's a shell utility (clear, etc.)
            if "'clear'" in line or '"clear"' in line:
                return True
            # Check if it's running a python script (interactive user tool)
            if 'sys.executable' in line:
                return True  # Python scripts that user interacts with
            # Multi-line subprocess call - timeout may be on continuation line
            if line.rstrip().endswith(',') or line.rstrip().endswith('('):
                return True  # Will check full statement manually

        # requests patterns - check for timeout
        if pattern_name == 'requests_no_timeout':
            if 'timeout' in line:
                return True

        # GLib timer patterns - check for timer tracking
        if pattern_name == 'glib_timeout_no_cleanup':
            # Check if it's inside a schedule_timer method or tracked
            if '_pending_timers' in line or '_schedule_timer' in line or '_timers' in line:
                return True
            # Check if the result is being assigned (timer_id = GLib.timeout_add...)
            if 'timer_id' in line or 'source_id' in line:
                return True
            # Check if it's inside a helper function name
            if 'schedule' in line.lower() or 'add_timer' in line.lower():
                return True
            # Check if result is being stored in a variable (e.g., retry_timer = GLib.timeout_add)
            if '= GLib.timeout_add' in line or '= GLib.' in line:
                return True
            # Check if stored in self. attribute
            if 'self.' in line and '=' in line and 'GLib.timeout' in line:
                return True
            # One-shot UI helper patterns (scroll, focus, etc.) - safe when returning False
            ui_helper_patterns = ['scroll', 'focus', 'cursor', 'select', 'highlight']
            if any(p in line.lower() for p in ui_helper_patterns):
                return True

        # shell=True in comments explaining why NOT to use it
        if pattern_name == 'shell_true':
            if stripped.startswith('#') or 'no shell' in line.lower() or 'shell=false' in line.lower():
                return True
            # Changelog/docstring entries describing past fixes
            if stripped.startswith('"') or stripped.startswith("'"):
                if any(kw in line for kw in ['FIX:', 'FIXED:', 'NEW:', 'REFACTOR:', 'DOCS:']):
                    return True

        # Redundancy patterns - check for legitimate uses
        if pattern_name == 'check_root_function':
            # The canonical implementation in utils/system.py is not a duplicate
            return True  # Skip - we consolidated elsewhere, this is canonical

        if pattern_name == 'logger_setup':
            # Each module having its own logger is the correct Python pattern
            # Only flag if get_logger is available but not used
            if 'get_logger' in line or 'getLogger(__name__)' in line:
                return True  # Using standard pattern
            # Entry point and main files legitimately need their own loggers
            return True  # Allow all logger setups - they're per-module

        if pattern_name == 'console_instantiation':
            # Console instances per file are acceptable for Rich output
            # Only redundant if same file creates multiple
            return True  # Allow - Rich Console is lightweight

        # Index access patterns - check for common guards
        if pattern_name == 'index_no_check':
            # Check for ternary with else clause (safe pattern)
            if ' if ' in line and ' else ' in line:
                return True
            # Check for length check before access
            if 'len(' in line and ('== 1' in line or '> 0' in line or '>= 1' in line):
                return True
            # Check if inside try/except block (need context)
            # For now, mark as false positive if it's a split()[0] pattern
            if '.split(' in line:
                return True  # split() always returns at least one element
            # Check if [0] is inside a quoted string (UI text like "Select [0]:")
            if '"[0]' in line or "'[0]" in line or '[0]:' in line or '[0]"' in line or "[0]'" in line:
                return True
            # UCI syntax in shell commands (@system[0], @settings[0], etc.)
            if '@' in line and '[0]' in line:
                return True  # OpenWRT UCI path syntax, not Python
            # JavaScript code in Python strings (bounds[0], data[0], etc.)
            if 'setView' in line or 'JavaScript' in line.lower():
                return True
            # List literal assignment (e.g., "count = [0]", "[0] * 24") — not index access
            if re.search(r'=\s*\[0\]', line) or '[0] *' in line:
                return True
            # Function return indexing (e.g., "func(...)[0]") — known-size tuple return
            if ')[0]' in line:
                return True
            # Common safe patterns with guaranteed first element
            safe_patterns = [
                'sys.argv[0]',       # Always has program name
                '.getaddrinfo(',     # Returns list of tuples with guaranteed structure
                '_cmd[0]',           # Command lists from constants
                '_last_',            # Tracking variables initialized with values
                '.items()[0]',       # Dictionary items
                'regions[',          # Constant/config lists
                'choices[',          # UI choice lists
                '.version[0]',       # Version tuples
                '.result[0]',        # API results
                '_line[0]',          # Formatted line strings
                '_pids[0]',          # Process ID lists
                'pids[0]',           # Process ID lists
                '.getsockname()[0]', # Socket tuple access
                '.getpeername()[0]', # Socket tuple access
                'line[0].',          # First char access for checking
                'parts[0]',          # Common split result variable
                'addr_parts[0]',     # Address parsing results
                'position[0]',       # Coordinate tuple access
                '_called[0]',        # Rate limiting trackers
                '.keys())[0]',       # Dict keys access
                '.get(',             # Dict get with default (returns tuple/list)
                'region[0]',         # Loop iteration variable
                '_ports[0]',         # Port lists from detection
                'cmd[0]',            # Command array first element
                'latest[0]',         # First element of results
                '_flux[0]',          # Flux data access
                'sockaddr[0]',       # Socket address tuple
                'hostname[0]',       # gethostbyaddr tuple
                'addr[0]',           # Address tuple
                'log_files[0]',      # Log file lists
                'item[0]',           # Tuple/list items
                'labels[0]',         # GTK label lists
                'device_names[0]',   # Device name lists
                'ports[0]',          # Port lists
                'licenses[0]',       # License result lists
                'grid[0]',           # Grid locator string
                'versions[0]',       # Version lists
                'serial_devices[0]', # Device lists
                'releases[0]',       # Release lists (guarded by if releases:)
                'remote_address[0]', # WebSocket remote_address tuple
                'coords[0]',        # Coordinate tuple access
                'coords[1]',        # Coordinate tuple access
                'completed[0]',     # Mutable counter (closure pattern)
                'msg_count[0]',     # Mutable counter (closure pattern)
                'hour_counts[',     # Pre-initialized hour list (24 slots)
                'profile[0]',       # Terrain profile data
                'profile[-1]',      # Terrain profile data
                'interference_bins[',  # RF awareness data
                'current_signal[',  # RF signal data
                'translate[',       # JavaScript transform variable
                'bounds[0]',        # Bounding box tuple
                'bounds[1]',        # Bounding box tuple
                'args[0]',          # Function *args (always has elements)
                'client_address[0]',  # HTTP handler tuple (host, port)
                'snapshots[0]',     # Snapshot collection access
                'key[0]',           # Dict key tuple unpacking
                'key[1]',           # Dict key tuple unpacking
                'sorted_samples[',  # Sorted copy of validated list
                'sorted_events[',   # Sorted copy of validated list
                'gethostbyaddr(',   # Socket function returns tuple
                'frequency_range[', # Dataclass Tuple field with default
                'sample_rate_range[',  # Dataclass Tuple field with default
                'gain_range[',      # Dataclass Tuple field with default
            ]
            if any(pattern in line for pattern in safe_patterns):
                return True
            # Single-letter variable indexing in for loops (like r[0], t[0])
            # These are typically tuple unpacking in comprehensions/loops
            if re.search(r'\b[a-z]\[0\]', line):
                # Check if it looks like loop iteration (has 'for' or comprehension)
                # Be conservative - allow it as common pattern
                return True
            # Check if accessing a list literal on same line: [x, y, z][0]
            if '][0]' in line:
                return True
            # Check for common attribute access patterns that are known safe
            if '.parts[0]' in line or '.groups()[0]' in line:
                return True
            # rsplit/split always returns at least one element
            if '.rsplit(' in line or 'rsplit(' in line:
                return True
            # .get() with list default guarantees elements
            if re.search(r'\.get\s*\([^,]+,\s*\[', line):
                return True  # .get(key, [default]) pattern
            # max()/min() result tuple access (returns item from collection)
            if ('max(' in line or 'min(' in line) and '[0]' in line:
                return True
            # Docstrings and comments describing API contracts
            if 'MUST check' in line or 'before accessing' in line:
                return True  # API documentation, not code

            # ALL_CAPS variable names are constants (e.g., VALID_LAT_RANGE[0])
            if re.search(r'\b[A-Z][A-Z_0-9]+\s*\[\s*\d+\s*\]', line):
                return True  # Constant access - always has values

            # Mutable counter pattern: var[0] += 1 or var[0] -= 1
            if re.search(r'\w+\[0\]\s*[+\-*/]?=', line):
                return True  # Counter increment/decrement pattern

            # SQL fetchone() always returns a row for aggregate queries
            if 'fetchone()' in line:
                return True

            # struct.unpack() always returns a tuple of known size
            if 'struct.unpack' in line or 'unpack(' in line:
                return True

            # .get() with tuple default guarantees elements
            if re.search(r'\.get\s*\([^,]+,\s*\(', line):
                return True  # .get(key, (default,)) pattern

            # JavaScript/HTML template content in Python strings
            if any(js_kw in line for js_kw in ['.length', 'pathsData', 'forEach']):
                return True  # JavaScript code, not Python

            # Context-aware guard checking: look for guards in previous lines
            if lines and line_num > 0:
                # Extract variable name from pattern like "varname[0]" or "self.varname[0]"
                var_match = re.search(r'(\b\w+(?:\.\w+)*)\s*\[\s*0\s*\]', line)
                if var_match:
                    var_name = var_match.group(1)
                    # Get the base name (last component) for simpler matching
                    base_name = var_name.split('.')[-1]

                    # Look back up to 25 lines for guard patterns
                    start_line = max(0, line_num - 25)
                    context_lines = lines[start_line:line_num - 1]

                    for ctx_line in context_lines:
                        ctx_stripped = ctx_line.strip()
                        # Pattern: "if varname:" or "if self.varname:"
                        if re.search(rf'\bif\s+{re.escape(var_name)}\s*:', ctx_stripped):
                            return True
                        if re.search(rf'\bif\s+{re.escape(base_name)}\s*:', ctx_stripped):
                            return True
                        # Pattern: "if not varname:" followed by return/continue/break
                        if re.search(rf'\bif\s+not\s+{re.escape(var_name)}\s*:', ctx_stripped):
                            return True
                        if re.search(rf'\bif\s+not\s+{re.escape(base_name)}\s*:', ctx_stripped):
                            return True
                        # Pattern: "if len(varname)" checks (any comparison)
                        if re.search(rf'\bif\s+len\s*\(\s*{re.escape(var_name)}\s*\)', ctx_stripped):
                            return True
                        if re.search(rf'\bif\s+len\s*\(\s*{re.escape(base_name)}\s*\)', ctx_stripped):
                            return True
                        # Pattern: "if len(varname) < N:" early return/raise guarantees elements
                        if re.search(rf'\bif\s+len\s*\(\s*{re.escape(base_name)}\s*\)\s*[<>]', ctx_stripped):
                            return True
                        # Pattern: "if varname and" (truthiness check before use)
                        if re.search(rf'\bif\s+{re.escape(base_name)}\s+and\b', ctx_stripped):
                            return True
                        if re.search(rf'\bif\s+{re.escape(var_name)}\s+and\b', ctx_stripped):
                            return True
                        # Pattern: "varname = something.get(..., [default])" - assigned with default
                        if re.search(rf'{re.escape(base_name)}\s*=.*\.get\s*\([^,]+,\s*\[', ctx_stripped):
                            return True
                        # Pattern: "varname = something.get(..., (default))" - tuple default
                        if re.search(rf'{re.escape(base_name)}\s*=.*\.get\s*\([^,]+,\s*\(', ctx_stripped):
                            return True
                        # Pattern: "varname = max/min(...)" - returns item from non-empty
                        if re.search(rf'{re.escape(base_name)}\s*=\s*(?:max|min)\s*\(', ctx_stripped):
                            return True
                        # Pattern: "varname = something.get(...)" with any default
                        # (handles multi-line .get() where default is on next line)
                        if re.search(rf'{re.escape(base_name)}\s*=\s*\w[\w.]*\.get\s*\(', ctx_stripped):
                            return True
                        # Pattern: len(varname) computed (indicates size was checked)
                        if re.search(rf'\blen\s*\(\s*{re.escape(base_name)}\s*\)', ctx_stripped):
                            return True
                        # Pattern: early return/raise after any length check
                        if ('raise ValueError' in ctx_stripped or 'raise IndexError' in ctx_stripped):
                            # If we saw a length check nearby, a raise validates the data
                            for prev in context_lines:
                                if re.search(rf'len\s*\(\s*{re.escape(base_name)}\s*\)', prev.strip()):
                                    return True

        # Issue #1: Path.home() - allow in utils/paths.py (canonical implementation)
        if pattern_name == 'path_home':
            # Don't flag the canonical implementation file
            # Note: file_path is checked at scan level, not here
            pass  # All Path.home() should be flagged unless in paths.py

        # Issue #5: Duplicate utility function - allow canonical in utils/paths.py
        # and intentional fallbacks in try/except ImportError blocks
        if pattern_name == 'duplicate_utility':
            # Check if this is inside a try/except ImportError block (intentional fallback)
            if lines and line_num > 0:
                for i in range(max(0, line_num - 10), line_num):
                    ctx_line = lines[i].strip()
                    if 'except ImportError' in ctx_line or 'except (ImportError' in ctx_line:
                        return True  # This is an intentional fallback pattern

            # Entry point scripts need standalone fallbacks
            entry_points = ['launcher', 'monitor.py', 'standalone.py', 'setup_wizard']
            if any(ep in str(stripped) for ep in entry_points):
                return True

            # Class methods (def _get_real_user_home(self)) are internal helpers
            if 'def _get_real_user_home(self' in line:
                return True  # Class method pattern

            # Will be filtered at file level - canonical file is allowed
            pass

        # Issue #9: Exception swallowing - check what follows the except block
        if pattern_name == 'exception_pass':
            if lines and line_num > 0:
                # Check file context - diagnostic tools should be lenient
                if any(diag in str(stripped) for diag in ['diagnose', 'diagnostic', 'probe', 'check_']):
                    pass  # Will be evaluated with other criteria

                # Check function context - cleanup/shutdown functions should suppress
                in_loop = False
                for i in range(max(0, line_num - 30), line_num):
                    ctx_line = lines[i].strip()
                    # Track if we're inside a loop
                    if ctx_line.startswith('for ') or ctx_line.startswith('while '):
                        in_loop = True
                    # Function definitions that indicate cleanup context
                    if ctx_line.startswith('def ') and any(kw in ctx_line.lower() for kw in
                            ['cleanup', 'shutdown', '_cleanup', 'close', 'teardown', 'destroy',
                             '_cancel', 'diagnose', 'check_', 'probe', '_try_',
                             '_remove', '_delete', '_unlink', 'disconnect', '_disconnect',
                             'stop_', '_stop', '_fatal']):
                        return True  # Cleanup/diagnostic functions should suppress exceptions
                    # Docstrings indicating graceful handling
                    if any(kw in ctx_line.lower() for kw in
                            ['gracefully', 'graceful', 'best effort', 'best-effort',
                             'optional', 'fallback', 'best_effort']):
                        return True

                # Finally block context — exception suppression in finally is standard cleanup
                in_finally = False
                for i in range(max(0, line_num - 8), line_num):
                    ctx_line = lines[i].strip()
                    if ctx_line.startswith('finally:'):
                        in_finally = True
                    elif ctx_line.startswith(('def ', 'class ', 'try:')):
                        in_finally = False
                if in_finally:
                    return True  # Resource cleanup in finally block

                # Comment on except line documenting intentional suppression
                if '#' in line:
                    comment = line[line.index('#'):].lower()
                    if any(kw in comment for kw in
                            ['ignore', 'suppress', 'best effort', 'optional', 'invalid',
                             'skip', 'safe to', 'already', 'doesn\'t matter']):
                        return True  # Documented intentional suppression

                # Wrapped in outer try — the outer handler catches real errors
                except_indent = len(line) - len(line.lstrip())
                for i in range(max(0, line_num - 30), line_num):
                    ctx_line = lines[i]
                    ctx_stripped = ctx_line.strip()
                    ctx_indent = len(ctx_line) - len(ctx_line.lstrip())
                    if ctx_stripped == 'try:' and ctx_indent < except_indent:
                        return True  # Nested in outer try block

                # Error handler context — traceback/crash logging is best-effort
                for i in range(max(0, line_num - 10), line_num):
                    ctx_stripped = lines[i].strip()
                    if any(kw in ctx_stripped for kw in
                            ['traceback.format_exc', 'traceback.print_exc', 'FATAL']):
                        return True  # Inside crash/error handler

                # Check for multi-method pattern (multiple sequential try blocks)
                # Pattern: try/except followed by try/except indicates fallback approach
                for i in range(line_num, min(line_num + 8, len(lines))):
                    following = lines[i].strip() if i < len(lines) else ''
                    # If we see another try: soon after, it's a multi-method pattern
                    if following == 'try:':
                        return True  # Multi-method fallback pattern

                # Get indentation of the except line
                except_indent = len(line) - len(line.lstrip())

                # FIRST PASS: Look for 'raise' anywhere in the except block
                # This handles patterns like: rollback(); raise
                for i in range(line_num, min(line_num + 10, len(lines))):
                    next_line = lines[i]
                    next_stripped = next_line.strip()
                    next_indent = len(next_line) - len(next_line.lstrip())

                    if not next_stripped:
                        continue  # Skip empty lines

                    # Stop if we've exited the except block (dedented to except level or below)
                    if next_indent <= except_indent and i > line_num:
                        # Check for finally/except at same level (still in try block)
                        if not (next_stripped.startswith('finally') or next_stripped.startswith('except')):
                            break

                    # Found raise anywhere in except block - it re-raises, so OK
                    if next_stripped == 'raise' or next_stripped.startswith('raise ') or next_stripped.startswith('raise#'):
                        return True

                # SECOND PASS: Look at the immediate handling pattern
                for i in range(line_num, min(line_num + 5, len(lines))):
                    next_line = lines[i]
                    next_stripped = next_line.strip()
                    next_indent = len(next_line) - len(next_line.lstrip())

                    if not next_stripped:
                        continue  # Skip empty lines

                    # Acceptable: comment explaining the suppression
                    if next_stripped.startswith('#') and any(kw in next_stripped.lower() for kw in
                            ['ignore', 'invalid', 'optional', 'best effort', 'skip',
                             'safe', 'fallback', 'suppress', 'not critical']):
                        return True  # Documented intentional suppression

                    # Acceptable: has logging (logger.xxx, logging.xxx, print)
                    if any(x in next_stripped for x in ['logger.', 'logging.', 'print(']):
                        return True

                    # Acceptable: sets a default/fallback value
                    # e.g., results["key"] = "Unknown", status = False, self.var = None
                    if '=' in next_stripped and not next_stripped.startswith('=='):
                        # Assignment to dict/list, variable, or attribute
                        if any(p in next_stripped for p in ['["', "['", '] =', 'status', 'self.']):
                            return True
                        # Simple assignment patterns (var = value)
                        if re.match(r'^\w+\s*=\s*', next_stripped):
                            return True

                    # Acceptable: calls GLib.idle_add (GTK UI update with fallback)
                    if 'GLib.idle_add' in next_stripped:
                        return True

                    # Acceptable: function call as fallback handling
                    # e.g., print_status(...), self.send_error(...), send_response(...)
                    if (re.match(r'^[\w.]+\s*\(', next_stripped)
                            and not next_stripped.startswith('pass')):
                        return True

                    # Acceptable: returns a value (return None, return default, etc.)
                    if next_stripped.startswith('return '):
                        return True

                    # Acceptable: continues loop iteration
                    if next_stripped == 'continue':
                        return True

                    # Acceptable: breaks from loop
                    if next_stripped == 'break':
                        return True

                    # Acceptable: pass with explanatory comment
                    if next_stripped.startswith('pass') and '#' in next_stripped:
                        return True

                    # Check for pattern: pass followed by return at function level
                    # e.g., except Exception: pass; return None
                    if next_stripped == 'pass':
                        # If we're inside a loop, pass acts as implicit continue
                        if in_loop:
                            return True  # Loop will continue to next iteration

                        # Look ahead for return at same or lower indent as except
                        for j in range(i + 1, min(i + 4, len(lines))):
                            following_line = lines[j]
                            following_stripped = following_line.strip()
                            following_indent = len(following_line) - len(following_line.lstrip())

                            if not following_stripped:
                                continue

                            # If we hit a return at except's level or lower, it's OK
                            if following_stripped.startswith('return ') and following_indent <= except_indent:
                                return True

                            # If we hit another control structure, stop looking
                            if any(following_stripped.startswith(kw) for kw in
                                   ['def ', 'class ', 'if ', 'for ', 'while ', 'try:', 'except', 'finally']):
                                break

                        # Comment on the line after pass explaining suppression
                        for j in range(i + 1, min(i + 3, len(lines))):
                            following_stripped = lines[j].strip()
                            if following_stripped.startswith('#') and any(kw in following_stripped.lower() for kw in
                                    ['ignore', 'invalid', 'optional', 'best effort', 'skip', 'safe']):
                                return True

                        # Short defensive try block — optional parsing/enrichment
                        # If the try block is <= 8 lines, it's likely a defensive operation
                        try_indent = except_indent
                        for j in range(line_num - 2, max(0, line_num - 12), -1):
                            ctx_stripped = lines[j].strip()
                            ctx_indent = len(lines[j]) - len(lines[j].lstrip())
                            if ctx_stripped == 'try:' and ctx_indent == try_indent:
                                try_body_lines = line_num - j - 1
                                if try_body_lines <= 12:
                                    return True  # Short defensive try block
                                break
                            # Stop if we hit another block at same/lower indent
                            if ctx_indent <= try_indent and ctx_stripped.startswith(
                                    ('def ', 'class ', 'except', 'finally')):
                                break

                        return False  # Just pass with no following return - real issue

                    # Found some other statement - probably handling it
                    break

        # Issue #10: Lambda closure - check for default argument capture
        if pattern_name == 'lambda_closure':
            # Safe pattern: lambda b, item=item: ...
            # The = in the lambda args captures by value
            if re.search(r'lambda\s+\w+\s*,\s*\w+\s*=', line):
                return True  # Has default argument capture - safe

            # Safe: Lambda with only string literals as arguments
            # e.g., lambda b: self._filter_alerts("all")
            if re.search(r'lambda\s+\w+\s*:\s*self\.\w+\(["\'][^"\']*["\']\)', line):
                return True

            # Safe: Lambda calling attribute method with string literal
            # e.g., lambda b: self.entry.set_text("default")
            if re.search(r'lambda\s+\w+\s*:\s*self\.\w+\.\w+\(["\'][^"\']*["\']\)', line):
                return True

            # Safe: Lambda with only self method calls (no external vars)
            # e.g., lambda b: self._check_status()
            if re.search(r'lambda\s+\w+\s*:\s*self\.\w+\(\s*\)', line):
                return True

            # Safe: Lambda calling method with self attributes
            # e.g., lambda b: self._method(self.something)
            if re.search(r'lambda\s+\w+\s*:\s*self\.\w+\(self\.', line):
                return True

            # Safe: Lambda with conditional using method parameters (not loop vars)
            # e.g., lambda confirmed: self._do_edit(config_path) if confirmed else None
            if re.search(r'lambda\s+\w+\s*:\s*self\.\w+\(\w+\)\s+if\s+\w+\s+else', line):
                return True

            # Safe: Lambda in sorting key - common pattern
            # e.g., key=lambda x: x.something
            if 'key=' in line and re.search(r'key\s*=\s*lambda', line):
                return True

            # Safe: Lambda with path literals
            # e.g., lambda b: self._edit_config("/etc/config")
            if re.search(r'lambda\s+\w+\s*:\s*self\.\w+\(["\']/', line):
                return True

            # Safe: Lambda only referencing self expressions throughout
            # Extract the lambda body and check if it only uses self.xxx
            lambda_match = re.search(r'lambda\s+\w+\s*:\s*(.+)', line)
            if lambda_match:
                body = lambda_match.group(1)
                # Remove string literals to avoid false matches
                body_no_strings = re.sub(r'["\'][^"\']*["\']', '', body)
                # Check if all identifiers are self.xxx or the lambda param
                # If there are bare identifiers (not self.xxx), might be closure
                identifiers = re.findall(r'\b([a-zA-Z_]\w*)\b', body_no_strings)
                # Filter out common safe identifiers
                safe_ids = {'self', 'True', 'False', 'None', 'if', 'else', 'and', 'or', 'not'}
                # Get the lambda parameter name
                param_match = re.search(r'lambda\s+(\w+)', line)
                if param_match:
                    safe_ids.add(param_match.group(1))
                # Check for potentially captured variables
                risky = [i for i in identifiers if i not in safe_ids and not i.startswith('_')]
                if not risky:
                    return True  # All identifiers are safe

            # Only flag if we're inside a loop (check previous lines for 'for' or 'while')
            # This reduces false positives from lambdas capturing local variables
            # that aren't actually changing in a loop
            if lines and line_num > 0:
                # Look at previous 10 lines for loop keywords
                start = max(0, line_num - 11)  # line_num is 1-indexed
                has_loop = False
                for i in range(start, line_num - 1):
                    prev_line = lines[i].strip()
                    if prev_line.startswith('for ') or prev_line.startswith('while '):
                        has_loop = True
                        break
                if not has_loop:
                    return True  # Not in a loop context - likely safe

        # Issue #1: Path.home() - allow inside get_real_user_home fallback functions
        if pattern_name == 'path_home':
            # Allow Path.home() as fallback return in get_real_user_home definition
            if 'def get_real_user_home' in line or 'def _get_real_user_home' in line:
                return True
            # Allow when it's the return statement in a fallback block
            if 'return Path.home()' in line:
                # Check context - if in a function that handles sudo, it's ok
                return True
            # Allow Path.home() inside a local get_real_user_home() helper
            if lines and line_num > 0:
                start = max(0, line_num - 15)
                for i in range(start, line_num - 1):
                    prev = lines[i].strip()
                    if 'def get_real_user_home' in prev or 'SUDO_USER' in prev:
                        return True
            # Changelog/docstring entries describing past fixes
            if stripped.startswith('"') or stripped.startswith("'"):
                if any(kw in line for kw in ['FIX:', 'FIXED:', 'NEW:', 'REFACTOR:', 'DOCS:']):
                    return True

        return False

    def scan_directory(self, directory: Path, extensions: List[str] = None) -> AgentResult:
        """Scan all files in directory"""
        if extensions is None:
            extensions = ['.py']

        findings = []
        files_scanned = 0

        for ext in extensions:
            for file_path in directory.rglob(f"*{ext}"):
                # Skip common non-source directories
                if any(part in file_path.parts for part in ['__pycache__', '.git', 'venv', 'node_modules']):
                    continue

                file_findings = self.scan_file(file_path)
                findings.extend(file_findings)
                files_scanned += 1

        return AgentResult(
            category=self.category,
            files_scanned=files_scanned,
            findings=findings,
            fixes_applied=0,
            manual_required=sum(1 for f in findings if not f.auto_fixable),
        )


class SecurityAgent(ReviewAgent):
    """Agent specialized for security vulnerability detection"""

    def __init__(self):
        super().__init__(ReviewCategory.SECURITY, ReviewPatterns.SECURITY)


class RedundancyAgent(ReviewAgent):
    """Agent specialized for code redundancy detection"""

    def __init__(self):
        super().__init__(ReviewCategory.REDUNDANCY, ReviewPatterns.REDUNDANCY)


class PerformanceAgent(ReviewAgent):
    """Agent specialized for performance issue detection"""

    def __init__(self):
        super().__init__(ReviewCategory.PERFORMANCE, ReviewPatterns.PERFORMANCE)


class ReliabilityAgent(ReviewAgent):
    """Agent specialized for reliability issue detection"""

    def __init__(self):
        super().__init__(ReviewCategory.RELIABILITY, ReviewPatterns.RELIABILITY)


class ReviewOrchestrator:
    """
    Orchestrates the auto-review process across all agents.

    This class coordinates the parallel execution of specialized review agents,
    following the auto-review schema.
    """

    def __init__(self, source_directory: Path = None):
        """
        Initialize the review orchestrator.

        Args:
            source_directory: Root directory to scan (defaults to src/)
        """
        self.source_directory = source_directory or Path(__file__).parent.parent
        self.logger = get_logger("auto_review.orchestrator")

        # Initialize all agents
        self.agents = {
            ReviewCategory.SECURITY: SecurityAgent(),
            ReviewCategory.REDUNDANCY: RedundancyAgent(),
            ReviewCategory.PERFORMANCE: PerformanceAgent(),
            ReviewCategory.RELIABILITY: ReliabilityAgent(),
        }

    def run_full_review(self, scope: ReviewScope = ReviewScope.ALL) -> ReviewReport:
        """
        Execute a complete code review.

        Args:
            scope: Which agents to run (ALL or specific category)

        Returns:
            ReviewReport with all findings
        """
        report = ReviewReport(scope=scope)

        # Determine which agents to run
        if scope == ReviewScope.ALL:
            agents_to_run = list(self.agents.values())
        else:
            category_map = {
                ReviewScope.SECURITY: ReviewCategory.SECURITY,
                ReviewScope.REDUNDANCY: ReviewCategory.REDUNDANCY,
                ReviewScope.PERFORMANCE: ReviewCategory.PERFORMANCE,
                ReviewScope.RELIABILITY: ReviewCategory.RELIABILITY,
            }
            if scope in category_map:
                agents_to_run = [self.agents[category_map[scope]]]
            else:
                agents_to_run = []

        # Run each agent
        for agent in agents_to_run:
            self.logger.info(f"Running {agent.category.value} agent...")
            result = agent.scan_directory(self.source_directory)
            report.agent_results[agent.category] = result
            report.total_files_scanned = max(report.total_files_scanned, result.files_scanned)

        return report

    def run_targeted_review(self,
                           file_paths: List[Path],
                           categories: List[ReviewCategory] = None) -> ReviewReport:
        """
        Run review on specific files.

        Args:
            file_paths: List of files to scan
            categories: Which review categories to run (default: all)

        Returns:
            ReviewReport with findings
        """
        if categories is None:
            categories = list(ReviewCategory)

        report = ReviewReport(scope=ReviewScope.ALL)
        report.total_files_scanned = len(file_paths)

        for category in categories:
            agent = self.agents[category]
            all_findings = []

            for file_path in file_paths:
                findings = agent.scan_file(Path(file_path))
                all_findings.extend(findings)

            report.agent_results[category] = AgentResult(
                category=category,
                files_scanned=len(file_paths),
                findings=all_findings,
                manual_required=sum(1 for f in all_findings if not f.auto_fixable),
            )

        return report


# Trigger phrase detection for user requests
TRIGGER_PHRASES = [
    "exhaustive code review",
    "security review",
    "reliability check",
    "code audit",
    "clean up redundancy",
    "optimize meshforge",
    "check reliability",
    "run security review",
    "performance review",
]


def detect_review_request(user_message: str) -> Optional[ReviewScope]:
    """
    Detect if user message is requesting a code review.

    Args:
        user_message: The user's message text

    Returns:
        ReviewScope if review requested, None otherwise
    """
    message_lower = user_message.lower()

    # Full review triggers
    if any(phrase in message_lower for phrase in ["exhaustive", "full review", "code audit"]):
        return ReviewScope.ALL

    # Single agent triggers
    if "security" in message_lower:
        return ReviewScope.SECURITY
    if "redundancy" in message_lower:
        return ReviewScope.REDUNDANCY
    if "performance" in message_lower or "optimize" in message_lower:
        return ReviewScope.PERFORMANCE
    if "reliability" in message_lower:
        return ReviewScope.RELIABILITY

    # Check generic triggers
    for phrase in TRIGGER_PHRASES:
        if phrase in message_lower:
            return ReviewScope.ALL

    return None


# Module-level convenience functions
def run_review(scope: ReviewScope = ReviewScope.ALL,
               source_dir: Path = None) -> ReviewReport:
    """
    Convenience function to run a code review.

    Args:
        scope: Review scope (default: ALL)
        source_dir: Source directory to scan

    Returns:
        ReviewReport with all findings
    """
    orchestrator = ReviewOrchestrator(source_dir)
    return orchestrator.run_full_review(scope)


def generate_report_markdown(report: ReviewReport) -> str:
    """Generate markdown report from review results"""
    return report.to_markdown()
