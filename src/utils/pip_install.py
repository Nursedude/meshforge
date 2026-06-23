"""Unified, hardened Python package installer for MeshForge.

THE one place pip is invoked from Python code. Born from a recurring failure
class (feedback_version_env_rigor, feedback_install_method_fragility): a fresh
user's app+env "did not install properly" and they had to hand-install pip,
because nothing in the running app checked that pip even EXISTS before calling
it — no ensurepip, no apt fallback, no actionable error. On top of that, ~13
pip-invocation sites each re-implemented PEP 668 handling and return-code
checking with varying fidelity (a drift source), and several swallowed failures
into a valid-looking success.

This module closes that class:
  * `ensure_pip()`   — guarantee `<python> -m pip` works, or fail LOUD with the
                       exact apt one-liner. The fresh-user fix.
  * `pip_install()`  — the single checked invoker: ensure_pip first, ONE PEP 668
                       decision, return-code checked (closing the
                       subprocess-doesn't-raise trap once), conflict-retry, and
                       optional import-as-consumer verification.
  * `resolve_target_python()` / `pep668_active()` / `check_import()` — the SSOTs
                       the above compose from, reusable by callers.

Design rules (honest_failure_modes.md):
  * Never return a bare bool — `PipResult` carries stdout/stderr/returncode +
    a human `detail` so callers surface honest information (#9 every swallow
    leaves a witness).
  * Never echo success — the CALLER decides UI from `.ok` / `.verified`.
  * "Installed" is not "importable": when asked, verify the package imports in
    the SAME interpreter (and as the right principal) the install targeted —
    a pip that reports success but leaves a broken/un-importable package flips
    `ok` to False (#1 degraded state must not read as healthy).

The module is import-light and has no import-time side effects, so it is safe to
import from installer, TUI handlers, and tests alike. `get_meshforge_venv_dir`
is imported lazily to avoid any circular-import risk with the updates package.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

_log = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

# pip stderr/stdout fragments that mean "an apt/distutils-owned package is in the
# way" — the cue to retry once with --ignore-installed. Lower-cased match.
_CONFLICT_SIGNATURES = (
    "externally-managed",
    "cannot uninstall",
    "installed by the package manager",
    "distutils installed project",
    "no files were found to uninstall",
)

# Generous per-step timeouts (MF004 — every subprocess call is bounded).
_PROBE_TIMEOUT = 30      # `python -m pip --version`
_ENSUREPIP_TIMEOUT = 180  # `python -m ensurepip --upgrade`
_APT_TIMEOUT = 600        # `apt-get install -y python3-pip`


@dataclass(frozen=True)
class PipResult:
    """The honest result of a pip operation.

    `ok` is the single truth the caller branches on; `detail` is the one-line
    human summary safe to put in a dialog or log; `verified` is True only when
    an import-as-consumer check was requested AND passed.
    """

    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    detail: str = ""
    verified: bool = False

    def __bool__(self) -> bool:  # ergonomic: `if pip_install(...):`
        return self.ok


def _err_text(result: subprocess.CompletedProcess) -> str:
    """Best human-readable error string from a captured run."""
    return (
        (result.stderr or "").strip()
        or (result.stdout or "").strip()
        or f"exit code {result.returncode}"
    )


def _sudo_prefix(sudo: bool, sudo_user: Optional[str]) -> List[str]:
    """Build the privilege prefix, matching service_check._sudo_cmd semantics.

    ``sudo_user`` always drops to that user (``sudo -u <user> -H``), even when
    we are already root — the point is to switch principal. Plain ``sudo`` only
    ELEVATES, so it is a no-op when already root (avoids requiring sudo in a
    minimal root environment).
    """
    if sudo_user:
        return ["sudo", "-u", sudo_user, "-H"]
    if sudo and getattr(os, "geteuid", lambda: 0)() != 0:
        return ["sudo"]
    return []


def resolve_target_python(explicit: Optional[PathLike] = None) -> str:
    """SSOT for 'which interpreter does MeshForge install into'.

    Precedence: an explicit target (the rnsd-python case, a venv path, or
    ``sys.executable``) wins; else MeshForge's venv python if the updater would
    use it (`get_meshforge_venv_dir`); else system ``python3``. Returning the
    SAME target the version reader uses is what keeps an upgrade from no-op'ing
    against a flag that never clears (the read/write split,
    feedback_version_env_rigor).
    """
    if explicit:
        return str(explicit)
    try:
        from updates.version_checker import get_meshforge_venv_dir
        venv_dir = get_meshforge_venv_dir()
    except Exception as exc:  # pragma: no cover - defensive
        _log.debug("get_meshforge_venv_dir unavailable: %s", exc)
        venv_dir = None
    if venv_dir is not None:
        return str(Path(venv_dir) / "bin" / "python")
    return "python3"


def pep668_active(python: Optional[PathLike] = None) -> bool:
    """Is PEP 668 'externally-managed' in effect for ``python``?

    ONE detector, keyed to the RESOLVED interpreter (not "always python3"), so
    the seven Python call sites stop hard-coding ``--break-system-packages``.
    A venv is NEVER externally managed (pip exempts it regardless of any marker
    in the shared stdlib), so we answer False inside a venv and only True for a
    base interpreter that actually carries the EXTERNALLY-MANAGED marker.
    """
    py = str(python) if python else sys.executable
    probe = (
        "import sys, sysconfig, os;"
        "v = sys.prefix != sys.base_prefix;"
        "p = os.path.join(sysconfig.get_path('stdlib'), 'EXTERNALLY-MANAGED');"
        "print('0' if v else ('1' if os.path.exists(p) else '0'))"
    )
    try:
        r = subprocess.run(
            [py, "-c", probe], capture_output=True, text=True, timeout=_PROBE_TIMEOUT
        )
        if r.returncode == 0:
            return r.stdout.strip() == "1"
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("pep668 probe failed for %s: %s", py, exc)
    # Fallback: a system marker present anywhere. Conservative — adding
    # --break-system-packages where it is not needed is a harmless no-op; NOT
    # adding it where it IS needed is a hard install failure.
    import glob

    return bool(glob.glob("/usr/lib/python3*/EXTERNALLY-MANAGED"))


def _pip_works(python: str) -> Optional[str]:
    """Return the `pip --version` string if `<python> -m pip` works, else None."""
    try:
        r = subprocess.run(
            [python, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("pip --version failed for %s: %s", python, exc)
    return None


def ensure_pip(
    python: Optional[PathLike] = None,
    *,
    allow_apt: bool = True,
    logger: Optional[logging.Logger] = None,
) -> PipResult:
    """Guarantee ``<python> -m pip`` works, or fail LOUD with guidance.

    THE root-cause guard for the fresh-user failure. Ladder:
      1. ``<python> -m pip --version``        → already works, done.
      2. ``<python> -m ensurepip --upgrade``  → re-check (1).
      3. if ``allow_apt`` and running as root and apt exists:
         ``apt-get install -y python3-pip``   → re-check (1).
      4. fail: ``PipResult(ok=False, detail=...)`` naming the exact interpreter
         and the apt one-liner — the message a fresh user never got.

    ``allow_apt=False`` MUST be used on *check* paths (a status checker must
    never apt-install as a side effect); it makes this a read-only probe.
    """
    log = logger or _log
    py = str(python) if python else sys.executable

    version = _pip_works(py)
    if version:
        return PipResult(True, stdout=version, returncode=0,
                         detail=f"pip present for {py}: {version}", verified=True)

    log.warning("pip not available for %s — attempting to bootstrap it", py)

    # Step 2: ensurepip (stdlib; often stripped on Debian/RPi, hence step 3).
    try:
        r = subprocess.run(
            [py, "-m", "ensurepip", "--upgrade"],
            capture_output=True,
            text=True,
            timeout=_ENSUREPIP_TIMEOUT,
        )
        log.info("ensurepip exit=%s", r.returncode)
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("ensurepip raised for %s: %s", py, exc)
    version = _pip_works(py)
    if version:
        return PipResult(True, stdout=version, returncode=0,
                         detail=f"pip bootstrapped via ensurepip for {py}: {version}",
                         verified=True)

    # Step 3: apt (only as root; apt installs pip for the system python3).
    is_root = getattr(os, "geteuid", lambda: 1)() == 0
    have_apt = shutil.which("apt-get") is not None
    if allow_apt and is_root and have_apt:
        try:
            r = subprocess.run(
                ["apt-get", "install", "-y", "python3-pip"],
                capture_output=True,
                text=True,
                timeout=_APT_TIMEOUT,
            )
            log.info("apt-get install python3-pip exit=%s", r.returncode)
        except (subprocess.SubprocessError, OSError) as exc:
            log.debug("apt-get install python3-pip raised: %s", exc)
        version = _pip_works(py)
        if version:
            return PipResult(True, stdout=version, returncode=0,
                             detail=f"pip installed via apt for {py}: {version}",
                             verified=True)

    # Step 4: fail loud with an ACTIONABLE message (the fresh-user fix).
    sudo_prefix = "" if is_root else "sudo "
    guidance = (
        f"pip is not available for {py} and could not be bootstrapped. "
        f"Install it with: {sudo_prefix}apt install -y python3-pip "
        f"(or {py} -m ensurepip --upgrade). You should not have to do this by "
        f"hand — please report which step failed."
    )
    if allow_apt and not is_root:
        guidance += " (apt auto-install was skipped because this is not running as root.)"
    log.error(guidance)
    return PipResult(False, detail=guidance, returncode=None)


def check_import(
    module: str,
    *,
    python: PathLike = "python3",
    sudo: bool = False,
    sudo_user: Optional[str] = None,
    timeout: int = 10,
) -> PipResult:
    """Import-as-consumer check: does ``module`` import in the target interpreter?

    Reuses the gateway_diagnostic.py:432 shape. ``sudo``/``sudo_user`` run the
    check as the principal that will actually consume the package (root for
    rnsd, the gateway user for LXMF/RNS), because "installed" and "importable
    by the consumer" are different facts (Issue #24).
    """
    py = str(python)
    inner = f"import {module}; print(getattr({module}, '__version__', '?'))"
    cmd = _sudo_prefix(sudo, sudo_user) + [py, "-c", inner]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return PipResult(False, detail=f"import {module} timed out", returncode=None)
    except FileNotFoundError as exc:
        # sudo or python missing (e.g. test sandbox) — unobservable, not healthy.
        return PipResult(False, detail=f"cannot run import check: {exc}", returncode=None)
    ok = r.returncode == 0 and bool(r.stdout.strip())
    detail = (
        f"{module} {r.stdout.strip()} importable"
        if ok
        else f"{module} NOT importable: {_err_text(r)}"
    )
    return PipResult(ok, stdout=r.stdout, stderr=r.stderr,
                     returncode=r.returncode, detail=detail, verified=ok)


def pip_install(
    packages: Sequence[str],
    *,
    python: Optional[PathLike] = None,
    upgrade: bool = False,
    break_system: Optional[bool] = None,
    ignore_installed: bool = False,
    extra_args: Optional[Sequence[str]] = None,
    sudo: bool = False,
    sudo_user: Optional[str] = None,
    timeout: int = 300,
    retry_on_conflict: bool = True,
    verify_import: Optional[str] = None,
    requirements_file: Optional[PathLike] = None,
    logger: Optional[logging.Logger] = None,
) -> PipResult:
    """The ONE checked pip invoker. Never echoes success — returns a PipResult.

    Steps:
      (a) ``ensure_pip(target)`` first — refuses to proceed (returns its failing
          PipResult) if pip is absent;
      (b) resolves the target interpreter via ``resolve_target_python``;
      (c) decides PEP 668 ONCE: ``break_system=None`` auto-detects via
          ``pep668_active(target)``; an explicit True/False overrides;
      (d) runs ``[<target>, '-m', 'pip', 'install', ...]`` captured, and CHECKS
          the return code (subprocess.run does NOT raise on nonzero when
          capturing — that trap is closed here, once);
      (e) on a conflict signature, retries exactly once with
          ``--ignore-installed`` (the pattern previously duplicated 3×);
      (f) if ``verify_import`` is given, runs an import-as-consumer check against
          the SAME target/principal and sets ``.verified``; a verify failure
          flips ``ok`` to False with honest detail.

    Keep ``python=`` explicit for the rnsd-python and #24 dual-install cases —
    collapsing them to the venv re-opens the read/write split.
    """
    log = logger or _log
    pkgs = list(packages)
    req = str(requirements_file) if requirements_file else None
    if not pkgs and not req:
        return PipResult(False, detail="pip_install called with no packages and no requirements_file")

    target = resolve_target_python(python)

    ep = ensure_pip(target, allow_apt=True, logger=log)
    if not ep.ok:
        return ep  # fail loud — cannot install without pip

    if break_system is None:
        break_system = pep668_active(target)

    def _build_cmd(force_ignore: bool) -> List[str]:
        cmd = _sudo_prefix(sudo, sudo_user)
        cmd += [target, "-m", "pip", "install"]
        if upgrade:
            cmd.append("--upgrade")
        if break_system:
            cmd.append("--break-system-packages")
        if ignore_installed or force_ignore:
            cmd.append("--ignore-installed")
        if extra_args:
            cmd += list(extra_args)
        if req:
            cmd += ["-r", req]
        cmd += pkgs
        return cmd

    def _run(force_ignore: bool) -> subprocess.CompletedProcess:
        cmd = _build_cmd(force_ignore)
        log.debug("pip_install: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    label = req or " ".join(pkgs)
    try:
        r = _run(force_ignore=False)
        if r.returncode != 0 and retry_on_conflict and not ignore_installed:
            blob = f"{r.stderr or ''}\n{r.stdout or ''}".lower()
            if any(sig in blob for sig in _CONFLICT_SIGNATURES):
                log.info("pip install conflict for %s — retrying with --ignore-installed", label)
                r = _run(force_ignore=True)
    except subprocess.TimeoutExpired:
        return PipResult(False, detail=f"pip install timed out after {timeout}s ({label})",
                         returncode=None)
    except (subprocess.SubprocessError, OSError) as exc:
        return PipResult(False, detail=f"pip install could not run ({label}): {exc}",
                         returncode=None)

    if r.returncode != 0:
        detail = f"pip install failed ({label}): {_err_text(r)}"
        log.warning(detail)
        return PipResult(False, stdout=r.stdout, stderr=r.stderr,
                         returncode=r.returncode, detail=detail)

    # Installed — but is it importable by the consumer?
    if verify_import:
        vr = check_import(verify_import, python=target, sudo=sudo, sudo_user=sudo_user)
        if not vr.ok:
            detail = (
                f"pip reported success for {label} but '{verify_import}' is not "
                f"importable in {target} — {vr.detail}"
            )
            log.warning(detail)
            return PipResult(False, stdout=r.stdout, stderr=r.stderr,
                             returncode=r.returncode, detail=detail, verified=False)
        return PipResult(True, stdout=r.stdout, stderr=r.stderr, returncode=0,
                         detail=f"installed {label}; {vr.detail}", verified=True)

    return PipResult(True, stdout=r.stdout, stderr=r.stderr, returncode=0,
                     detail=f"installed {label}")


def pipx_install(
    package: str,
    *,
    owner_uid: Optional[int] = None,
    force: bool = False,
    timeout: int = 300,
    logger: Optional[logging.Logger] = None,
) -> PipResult:
    """Thin CHECKED wrapper around ``pipx install`` for the #24 'also for rnsd'
    and pipx-owner cases.

    This deliberately does NOT resolve the owner itself — the owner-aware brains
    stay in the caller (e.g. updates._pipx_upgrade_cli) so we don't regress the
    read/write-split fix. Pass an already-resolved ``owner_uid`` to run pipx as
    that user; otherwise it runs as the current principal.
    """
    log = logger or _log
    if not shutil.which("pipx"):
        return PipResult(False, detail="pipx not found on PATH")
    cmd: List[str] = []
    if owner_uid is not None and getattr(os, "geteuid", lambda: owner_uid)() != owner_uid:
        cmd += ["sudo", "-u", f"#{owner_uid}", "-H"]
    cmd += ["pipx", "install", package]
    if force:
        cmd.append("--force")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return PipResult(False, detail=f"pipx install {package} timed out", returncode=None)
    except (subprocess.SubprocessError, OSError) as exc:
        return PipResult(False, detail=f"pipx install {package} could not run: {exc}",
                         returncode=None)
    ok = r.returncode == 0
    detail = f"pipx installed {package}" if ok else f"pipx install {package} failed: {_err_text(r)}"
    if not ok:
        log.warning(detail)
    return PipResult(ok, stdout=r.stdout, stderr=r.stderr, returncode=r.returncode, detail=detail)
