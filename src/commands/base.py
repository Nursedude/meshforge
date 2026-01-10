"""
Base classes for the commands layer.

CommandResult provides a consistent return type across all commands.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from enum import Enum


class ResultStatus(Enum):
    """Command execution status."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    NOT_AVAILABLE = "not_available"


@dataclass
class CommandResult:
    """
    Unified result type for all commands.

    Attributes:
        success: Whether the command succeeded
        status: Detailed status enum
        message: Human-readable message
        data: Command-specific result data
        error: Error message if failed
        raw_output: Raw command output (for debugging)
    """
    success: bool
    status: ResultStatus = ResultStatus.SUCCESS
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    raw_output: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success

    @classmethod
    def ok(cls, message: str = "Success", data: Dict[str, Any] = None, raw: str = None) -> 'CommandResult':
        """Create a successful result."""
        return cls(
            success=True,
            status=ResultStatus.SUCCESS,
            message=message,
            data=data or {},
            raw_output=raw
        )

    @classmethod
    def fail(cls, message: str, error: str = None, raw: str = None, data: Dict[str, Any] = None) -> 'CommandResult':
        """Create a failed result."""
        return cls(
            success=False,
            status=ResultStatus.ERROR,
            message=message,
            error=error or message,
            data=data or {},
            raw_output=raw
        )

    @classmethod
    def warn(cls, message: str, data: Dict[str, Any] = None) -> 'CommandResult':
        """Create a warning result (partial success)."""
        return cls(
            success=True,
            status=ResultStatus.WARNING,
            message=message,
            data=data or {}
        )

    @classmethod
    def not_available(cls, message: str, fix_hint: str = "") -> 'CommandResult':
        """Create a not-available result (service/tool missing)."""
        return cls(
            success=False,
            status=ResultStatus.NOT_AVAILABLE,
            message=message,
            error=fix_hint or message,
            data={'fix_hint': fix_hint}
        )


class CommandError(Exception):
    """Exception raised by commands."""

    def __init__(self, message: str, result: CommandResult = None):
        super().__init__(message)
        self.result = result or CommandResult.fail(message)
