"""Fallback stubs for Rich when the library is unavailable.

Rich is a required MeshForge dependency, but several modules guard against
ImportError so the installer itself can bootstrap before Rich is present.
This module provides minimal no-op stand-ins for the Rich symbols those
modules import, so module-level attribute access does not raise NameError.

Usage pattern:

    try:
        from rich.console import Console
        from rich.table import Table
        HAS_RICH = True
    except ImportError:
        from utils.rich_fallback import Console, Table
        HAS_RICH = False

    console = Console()

Code that actually renders rich output must gate on ``HAS_RICH`` or accept
that the stub will print plain text (via ``Console.print``) or be a no-op.
"""

from __future__ import annotations

import builtins
import re
import sys
from typing import Any


_MARKUP_RE = re.compile(r"\[/?[A-Za-z0-9_\- ,#]+\]")


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags like [bold red] for plain output."""
    return _MARKUP_RE.sub("", text)


class Console:
    """Minimal Console stub that degrades to builtins.print."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._file = kwargs.get("file", sys.stdout)

    def print(self, *args: Any, **kwargs: Any) -> None:
        file = kwargs.pop("file", self._file)
        parts = [_strip_markup(str(a)) for a in args]
        builtins.print(*parts, file=file)

    def log(self, *args: Any, **kwargs: Any) -> None:
        self.print(*args, **kwargs)

    def rule(self, title: str = "", **_: Any) -> None:
        builtins.print(_strip_markup(str(title)), file=self._file)

    def status(self, *_: Any, **__: Any) -> "_NullContext":
        return _NullContext()

    def capture(self, *_: Any, **__: Any) -> "_NullContext":
        return _NullContext()

    def __getattr__(self, _name: str) -> Any:
        # Any unexpected attribute becomes a harmless no-op callable.
        return lambda *a, **kw: None


class _NullContext:
    def __enter__(self) -> "_NullContext":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _Stub:
    """Generic no-op stub for Rich classes we never render without Rich."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self._kwargs = kwargs

    def __call__(self, *args: Any, **kwargs: Any) -> "_Stub":
        return self

    def __getattr__(self, _name: str) -> Any:
        return lambda *a, **kw: None


class _PromptStub:
    """Stub for Rich prompts. Falls back to builtins.input."""

    @staticmethod
    def ask(prompt: str = "", default: Any = None, **_: Any) -> Any:
        try:
            response = builtins.input(_strip_markup(str(prompt)) + " ")
        except EOFError:
            return default
        return response or default


class _ConfirmStub:
    @staticmethod
    def ask(prompt: str = "", default: bool = False, **_: Any) -> bool:
        try:
            response = builtins.input(_strip_markup(str(prompt)) + " [y/N]: ")
        except EOFError:
            return default
        return response.strip().lower().startswith("y")


class _IntPromptStub:
    @staticmethod
    def ask(prompt: str = "", default: int = 0, **_: Any) -> int:
        try:
            response = builtins.input(_strip_markup(str(prompt)) + " ")
        except EOFError:
            return default
        try:
            return int(response)
        except (TypeError, ValueError):
            return default


# Public aliases matching Rich's import paths.
Table = _Stub
Panel = _Stub
Layout = _Stub
Text = _Stub
Progress = _Stub
SpinnerColumn = _Stub
TextColumn = _Stub
BarColumn = _Stub
Prompt = _PromptStub
Confirm = _ConfirmStub
IntPrompt = _IntPromptStub

# Rich re-exports these as module attributes.
box = _Stub()
ROUNDED = None
HEAVY = None
