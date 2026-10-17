#!/usr/bin/env python3
"""fork_roll_install.py — step 1 of an rns/lxmf fork roll: install the PIN into every env that carries it.

Run ON the box, as root, after the pin bump has reached its checkout:

    sudo -n python3 scripts/fork_roll_install.py lxmf            # dry run: the survey + plan
    sudo -n python3 scripts/fork_roll_install.py lxmf --apply    # install, then verify each env
    sudo -n python3 scripts/fork_roll_restart.py --apply         # step 2: restart what runs the old copy

WHY (2026-10-16). The mf.11 and mf.12 rolls installed from a scratchpad
script, hand-edited per roll (a SHA literal, an lxmf-only "fix marker"
grep as the verify). It lived in one session's tmp dir; the next roll would
have rebuilt it from memory. Here the pin is the only input: the version
from the `# MF-FORK-PIN <dist> <ver>` line (rns_version_check's own
parser), the URL@SHA from the requirement line, and the verify is pip's own
record of what it installed — the dist-info version AND direct_url.json's
commit_id — so no roll needs a marker of its own.

A box carries more RNS environments than any one probe expects (user site,
root's user site, /usr/local dist-packages, /opt/*/venv, pipx venvs —
persistent_issues, the 1.3.8 merge arc). The SURVEY finds every
`<dist>-*.dist-info` on the root filesystem and installs into exactly those:
it never adds an env (installing the "missing" thing is drift). Each env is
installed by its OWNER with its OWN interpreter version, `--no-deps` (rns is
pinned exactly by lxmf's metadata and rolls on its own pin).

Exit: 0 = every surveyed env verified at the pin (or, dry run, the plan
printed); 1 = an install failed or an env does not verify; 2 = UNKNOWN (an
env this script cannot classify, the survey could not run, not root).
"""
import argparse
import importlib.util
import json
import os
import pwd
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# Root runs (sudo -n, the documented form) must not leave root-owned bytecode
# in the repo: same cure and path as scripts/lib/pycache_prefix.sh (measured
# 2026-10-16 — this script's first sudo dry run fleet-wide wrote a root .pyc
# into src/utils/__pycache__ on 9 boxes). Set BEFORE the first-party imports.
if os.geteuid() == 0 and not sys.pycache_prefix:
    sys.pycache_prefix = os.environ.get("MF_ROOT_PYCACHE", "/var/cache/meshforge/pycache-root")

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "requirements" / "rns.txt"
# Same pruning as the roll that found the fifth env class (kiai's /root/.local).
SURVEY_PRUNE = ("/proc", "/sys", "/tmp", "/run", "/dev")
# Every root an env may live under that can be its OWN filesystem: `-xdev`
# keeps find off other mounts, so a separate /home or /opt would otherwise
# read as "nothing to do" (sixth read). Overlap dedups in the set below.
SURVEY_ROOTS = ("/", "/home", "/opt", "/root", "/usr/local")
INSTALL_TIMEOUT_S = 600


class PinError(ValueError):
    pass


class SurveyError(RuntimeError):
    """find did not complete cleanly: partial output is not a survey."""


@dataclass
class Pin:
    dist: str
    version: str
    sha: str
    spec: str            # the exact requirement line pip is given


@dataclass
class Env:
    site: str
    kind: str            # user-site | system | venv | pipx | unknown
    python: str = ""
    pipx_name: str = ""


def _pinned_versions(pin_file: Path) -> dict:
    """rns_version_check.pinned_versions, pointed at ``pin_file`` (one parser)."""
    spec = importlib.util.spec_from_file_location(
        "rns_version_check", ROOT / "scripts" / "rns_version_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REQ = str(pin_file)
    return mod.pinned_versions()


def read_pin(dist: str, pin_file: Path = PIN_FILE) -> Pin:
    version = _pinned_versions(pin_file).get(dist)
    if not version:
        raise PinError(f"no MF-FORK-PIN for {dist} in {pin_file}")
    pat = re.compile(r"^\s*(%s\s*@\s*git\+\S+@([0-9a-f]{40}))\s*$" % re.escape(dist), re.M)
    m = pat.search(pin_file.read_text())
    if not m:
        raise PinError(f"no `{dist} @ git+<url>@<40-hex sha>` line in {pin_file}")
    return Pin(dist=dist, version=version, sha=m.group(2), spec=m.group(1))


_PYVER = re.compile(r"/lib/(python3\.\d+)/(site|dist)-packages/?$")


def classify(site: str) -> Env:
    m = _PYVER.search(site)
    if not m:
        return Env(site, "unknown")
    py = m.group(1)
    pipx = re.match(r"^((?:/home/[^/]+|/root)/\.local/share/pipx/venvs/([^/]+))/lib/", site)
    if pipx:
        return Env(site, "pipx", f"{pipx.group(1)}/bin/python", pipx.group(2))
    if re.match(r"^(/home/[^/]+|/root)/\.local/lib/", site):
        return Env(site, "user-site", f"/usr/bin/{py}")
    if site.startswith("/usr/local/lib/") and m.group(2) == "dist":
        return Env(site, "system", f"/usr/bin/{py}")
    venv = re.match(r"^(/opt/[^/]+/venv)/lib/", site)
    if venv:
        return Env(site, "venv", f"{venv.group(1)}/bin/python")
    return Env(site, "unknown")


def install_argv(env: Env, pin: Pin, owner: str) -> List[str]:
    pip = ["install", "--no-deps", "--no-cache-dir", pin.spec]
    as_owner = ["sudo", "-n", "-u", owner, "-H"]
    if env.kind == "pipx":
        return as_owner + ["pipx", "runpip", env.pipx_name] + pip
    if env.kind == "user-site":
        return as_owner + [env.python, "-m", "pip", "install", "--user",
                           "--break-system-packages", "--no-deps", "--no-cache-dir", pin.spec]
    if env.kind == "system":
        return ["sudo", "-n", env.python, "-m", "pip", "install",
                "--break-system-packages", "--no-deps", "--no-cache-dir", pin.spec]
    if env.kind == "venv":
        return as_owner + [env.python, "-m", "pip"] + pip
    raise ValueError(f"no install form for {env.kind}")


def _owner(site: str) -> str:
    return pwd.getpwuid(os.stat(site).st_uid).pw_name


def survey(dist: str) -> List[str]:
    """Every site dir on / that holds a ``<dist>-*.dist-info`` (sorted)."""
    prune: List[str] = []
    for p in SURVEY_PRUNE:
        prune += ["-path", p, "-o"]
    prune += ["-path", "*/.git"]
    roots = [r for r in SURVEY_ROOTS if os.path.isdir(r)] or ["/"]
    r = subprocess.run(["find", *roots, "-xdev", "(", *prune, ")", "-prune", "-o",
                        "-type", "d", "-iname", f"{dist}-*.dist-info", "-print"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        first = (r.stderr.strip().splitlines() or ["no stderr"])[0]
        raise SurveyError(f"find rc={r.returncode}: {first[:160]}")
    return sorted({os.path.dirname(x) for x in r.stdout.splitlines() if x.strip()})


def verify(site: str, pin: Pin) -> Tuple[bool, str]:
    """pip's own record: exactly one dist-info, at the pinned version AND commit."""
    infos = [d for d in os.listdir(site)
             if d.lower().startswith(pin.dist.lower() + "-") and d.endswith(".dist-info")]
    if len(infos) != 1:
        return False, f"{len(infos)} dist-info dirs for {pin.dist}: {' '.join(sorted(infos))}"
    d = os.path.join(site, infos[0])
    ver = ""
    try:
        with open(os.path.join(d, "METADATA"), errors="replace") as fh:
            for ln in fh:
                if ln.startswith("Version:"):
                    ver = ln.split(":", 1)[1].strip()
                    break
    except OSError as e:
        return False, f"METADATA unreadable: {e}"
    try:
        with open(os.path.join(d, "direct_url.json")) as fh:
            sha = (json.load(fh).get("vcs_info") or {}).get("commit_id", "")
    except (OSError, ValueError) as e:
        return False, f"{ver} — direct_url.json unreadable ({e}): not a pinned git install"
    got = f"{ver} @ {sha[:8]}"
    return (ver == pin.version and sha == pin.sha), got


def run(dist: str, pin_file: Path, apply: bool, out: Callable[[str], None] = print) -> int:
    try:
        pin = read_pin(dist, pin_file)
    except (PinError, OSError) as e:
        out(f"UNKNOWN — {e}")
        return 2
    try:
        sites = survey(dist)
    except (subprocess.SubprocessError, OSError, SurveyError) as e:
        out(f"UNKNOWN — survey did not run: {e}")
        return 2
    out(f"pin: {pin.dist} {pin.version} @ {pin.sha[:8]}")
    if not sites:
        out(f"  no env carries {dist} on this box — nothing installed (never adds an env)")
        return 0
    rc = 0
    for site in sites:
        env = classify(site)
        if env.kind == "unknown":
            out(f"  UNKNOWN env {site}: no install form — NOT installed; check it by hand")
            rc = max(rc, 2)
            continue
        owner = _owner(site)
        if not apply:
            ok, got = verify(site, pin)
            out(f"  plan {env.kind:9} as {owner:10} {site}  (now: {got}{'' if ok else ' -> pin'})")
            continue
        argv = install_argv(env, pin, owner)
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
            irc, msg = r.returncode, (r.stdout + r.stderr).strip().splitlines()
        except (subprocess.SubprocessError, OSError) as e:
            irc, msg = -1, [str(e)]
        if irc != 0:
            out(f"  FAIL install rc={irc} {site}: {msg[-1] if msg else ''}"[:200])
            rc = max(rc, 1)
            continue
        ok, got = verify(site, pin)
        out(f"  {'ok      ' if ok else 'MISMATCH'} {env.kind:9} {site}  {got}")
        if not ok:
            rc = max(rc, 1)
    out(f"rc={rc}")
    return rc


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dist", choices=("rns", "lxmf"))
    ap.add_argument("--apply", action="store_true", help="install (default: survey + plan)")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("UNKNOWN — run as root (sudo -n): the survey must see /root/.local and "
              "other users' envs, or it under-counts them silently")
        return 2
    return run(a.dist, PIN_FILE, apply=a.apply)


if __name__ == "__main__":
    sys.exit(main())
