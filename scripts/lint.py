#!/usr/bin/env python3
"""
MeshForge Linter - Check for common issues and coding standards.

Checks:
- Path.home() violations (must use get_real_user_home for sudo compatibility)
- shell=True in subprocess calls (security risk)
- Bare except: clauses (should use except Exception:)
- Missing timeout in subprocess calls
- Command injection risks

Usage:
    python3 scripts/lint.py [files...]
    python3 scripts/lint.py --all
    python3 scripts/lint.py --staged
"""

import argparse
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

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except (IOError, OSError) as e:
            return [LintIssue(filepath, 0, Severity.ERROR, "MF000", f"Cannot read file: {e}")]

        content = ''.join(lines)

        # Check each line
        for i, line in enumerate(lines, 1):
            issues.extend(self._check_line(filepath, i, line, content))

        return issues

    def _check_line(self, filepath: str, lineno: int, line: str, content: str) -> List[LintIssue]:
        """Check a single line for issues."""
        issues = []
        stripped = line.strip()

        # Skip comments
        if stripped.startswith('#'):
            return issues

        # MF001: Path.home() violation
        # Skip the paths.py utility file that defines get_real_user_home()
        if 'Path.home()' in line and 'paths.py' not in filepath:
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
            context_start = max(0, content.find(line) - 500)
            nearby_context = content[context_start:content.find(line) + len(line)]
            has_import_fallback = (
                'from utils.paths import' in nearby_context and
                'except ImportError' in nearby_context
            )
            if not is_fallback_pattern and not has_import_fallback:
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
                start_idx = content.find(line)
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

        # MF005: GLib.idle_add check - UI updates from threads
        if 'self.' in line and ('set_text' in line or 'set_label' in line or 'append' in line):
            # Check if we're in a thread context (simplistic check)
            func_start = content.rfind('def ', 0, content.find(line))
            if func_start != -1:
                func_block = content[func_start:content.find(line)]
                if 'Thread' in func_block or 'threading' in func_block:
                    if 'GLib.idle_add' not in line and 'idle_add' not in content[func_start:content.find(line) + len(line) + 200]:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.INFO, "MF005",
                            "UI update in thread context - ensure GLib.idle_add() is used"
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


def get_all_python_files(directory: str = 'src') -> List[str]:
    """Get all Python files in directory."""
    files = []
    for root, _, filenames in os.walk(directory):
        for f in filenames:
            if f.endswith('.py'):
                files.append(os.path.join(root, f))
    return files


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
