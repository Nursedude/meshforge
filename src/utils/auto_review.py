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

    These patterns align with the MeshForge Auto-Review Principles
    documented in .claude/foundations/auto_review_principles.md
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

        try:
            content = file_path.read_text(encoding='utf-8')
            lines = content.split('\n')

            for pattern_name, pattern_config in self.patterns.items():
                regex = re.compile(pattern_config['pattern'], re.IGNORECASE)

                for line_num, line in enumerate(lines, start=1):
                    if regex.search(line):
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
    following the schema defined in auto_review_principles.md.
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
