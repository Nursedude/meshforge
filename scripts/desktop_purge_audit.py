#!/usr/bin/env python3
"""desktop_purge_audit.py — read-only: would purging the desktop stack remove
anything a live consumer on THIS box uses?

    sudo -n python3 desktop_purge_audit.py [--json out.json]

1. ROOTS  = the installed desktop root packages (fixed list below).
2. REMOVE = `apt-get -s purge --auto-remove ROOTS` (simulation only).
3. KEEP   = (a) packages owning any file mapped by any running process
            (b) packages owning any module our code imports, resolved by each
                service interpreter (static import scan of /opt/* repos +
                the RNS/LXMF/meshtastic/nomadnet trees, find_spec per name)
            (c) REVIEW: removal-set binaries whose name appears in our repos,
                crontabs or ~/*.sh (word match — over-reports, never decides)
Exit 0 = REMOVE ∩ KEEP(a,b) empty; 1 = overlap (printed); 2 = UNKNOWN.

The roll this was written for (2026-10-26, headless phase 2, 9 boxes), per box:
  1. this audit with --json → must exit 0
  2. `apt-mark manual <json keep>`; re-simulate `apt-get -s purge --auto-remove
     <json roots>` → 0 keep packages in the removal set
  3. the purge under `systemd-run` (an ssh drop cannot cut apt mid-run),
     DEBIAN_FRONTEND=noninteractive, --force-confold; baseline unit lists first
  4. verify: same active units minus desktop ones, our MainPIDs unchanged,
     0 failed, endpoints answer — then REBOOT (the purge rebuilds the
     initramfs; only a boot proves the box comes back) and verify again.
A purged package's user units keep running from deleted files until a reboot
or `systemctl --user stop` — that is how xdg-desktop-portal-gtk sat `failed`.
"""
import ast, glob, json, os, re, subprocess, sys

ROOT_CANDIDATES = [
    "rpd-applications", "rpd-common", "rpd-developer", "rpd-graphics", "rpd-plym-splash",
    "rpd-preferences", "rpd-theme", "rpd-utilities", "rpd-wallpaper", "rpd-wallpaper-trixie",
    "rpd-wallpaper-bookworm", "rpd-wayland-core", "rpd-wayland-extras", "rpd-x-core",
    "rpd-x-extras", "raspberrypi-ui-mods", "lightdm", "pi-greeter", "labwc", "wayfire",
    "wf-panel-pi", "lxpanel-pi", "pcmanfm", "chromium", "chromium-common", "chromium-l10n",
    "chromium-sandbox", "firefox", "realvnc-vnc-server", "wayvnc", "xdg-desktop-portal",
    "xdg-desktop-portal-gtk", "xdg-desktop-portal-wlr", "xserver-xorg-core", "gnome-keyring",
    "cups", "rpi-connect", "rpi-connect-lite", "pulseaudio",
]
# Kept whatever the audit says: tools our scripts/operators use, HAT tooling,
# and the security cure. Measured 2026-10-26 on moc2: autoremove took bc,
# i2c-tools, python3-smbus, python3-flask.
MUST_KEEP = [
    "unattended-upgrades", "python3-gi", "python3-apt", "python3-dbus", "python3-cairo",
    "python3-gi-cairo", "bc", "i2c-tools", "python3-smbus", "jq", "curl", "git", "rsync",
    "whiptail", "dialog", "tmux", "python3-serial", "python3-venv", "python3-pip", "pipx",
]
# python3-* the purge MAY take: GUI, camera, printing, IDE/lint. Every other
# python3-* library in the removal set is KEPT (a few MB vs an outage).
PY_REMOVABLE = re.compile(
    r"^python3-(pyqt5.*|pygame|pgzero|sense-hat|rtimulib|picamera2|libcamera|pidng|simplejpeg|"
    r"videodev2|kms\+\+|av|opengl|openexr|cups|cupshelpers|smbc|send2trash|jedi|parso|astroid|"
    r"isort|mccabe|mypy|mypy-extensions|logilab-common|libevdev|freetype|reportlab|rlpycairo|"
    r"olefile|piexif|numpy-dev|pyinotify)$")
def _live_venvs():
    """VIRTUAL_ENV of every running process — the venvs this box actually runs."""
    out = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            env = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
        except OSError:
            continue
        for kv in env:
            if kv.startswith(b"VIRTUAL_ENV="):
                out.add(kv.split(b"=", 1)[1].decode(errors="replace") + "/bin/python")
    return out


INTERPRETERS = sorted({"/usr/bin/python3", *glob.glob("/opt/*/venv/bin/python"),
                       *glob.glob("/opt/*/.venv/bin/python"), *glob.glob("/home/*/*/venv/bin/python"),
                       *glob.glob("/home/*/.local/share/pipx/venvs/*/bin/python"),
                       *glob.glob("/root/.local/share/pipx/venvs/*/bin/python"), *_live_venvs()})
SCAN_TREES = [d for d in glob.glob("/opt/*") if os.path.isdir(os.path.join(d, ".git"))]
DIST = ("/usr/lib/python3/dist-packages/", "/usr/lib/python3.")


def sh(argv, timeout=300):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def installed(pkgs):
    r = sh(["dpkg-query", "-W", "-f=${Package} ${db:Status-Abbrev}\n", *pkgs])
    return sorted(l.split()[0] for l in r.stdout.splitlines() if l.split()[1:2] == ["ii"])


def simulate(roots, keep=()):
    # `pkg+` = an install request inside the purge transaction: apt's resolver
    # treats it as manually wanted, so autoremove spares it — simulation only.
    r = sh(["apt-get", "-s", "purge", "--auto-remove", *roots, *[k + "+" for k in keep]], timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"apt-get -s rc={r.returncode}: {r.stderr.strip()[:200]}")
    return sorted({m.group(1) for m in re.finditer(r"^(?:Purg|Remv) (\S+)", r.stdout, re.M)})


def owners(paths):
    """dpkg -S over paths, batched; returns {pkg} (bare names, arch stripped)."""
    out, paths = set(), sorted(set(paths))
    for i in range(0, len(paths), 200):
        r = sh(["dpkg", "-S", *paths[i:i + 200]])
        for ln in r.stdout.splitlines():
            if ": " in ln:
                for p in ln.split(": ", 1)[0].split(", "):
                    out.add(p.split(":")[0].strip())
    return out


def mapped_files():
    files = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            files.add(os.readlink(f"/proc/{pid}/exe"))
            with open(f"/proc/{pid}/maps") as fh:
                for ln in fh:
                    parts = ln.split(None, 5)
                    if len(parts) == 6 and parts[5].startswith("/"):
                        files.add(parts[5].strip().replace(" (deleted)", ""))
        except OSError:
            continue
    return {f for f in files if os.path.exists(f) and not f.startswith(("/home", "/opt", "/tmp", "/dev"))}


def import_names():
    names, trees = set(), list(SCAN_TREES)
    for py in INTERPRETERS:
        if os.path.exists(py):
            r = sh([py, "-c", "import importlib.util as u,os\nfor n in ('RNS','LXMF','meshtastic','nomadnet','meshcore'):\n s=u.find_spec(n)\n if s and s.origin: print(os.path.dirname(s.origin))"])
            trees += [l for l in r.stdout.splitlines() if l]
    for tree in sorted(set(trees)):
        for f in glob.glob(f"{tree}/**/*.py", recursive=True):
            if "/.git/" in f or "/node_modules/" in f:
                continue
            try:
                t = ast.parse(open(f, "rb").read())
            except (SyntaxError, ValueError, OSError):
                continue
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    names |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                    names.add(n.module.split(".")[0])
    # safe_import('X') / importlib.import_module('X') string literals
    for tree in SCAN_TREES:
        r = sh(["grep", "-rhoE", r"(safe_import|import_module)\(\s*['\"][A-Za-z_][A-Za-z0-9_]*", tree])
        names |= {m.split("'")[-1].split('"')[-1] for m in r.stdout.split()}
    return sorted(n for n in names if n.isidentifier())


def resolved_dist_files(names):
    files = {}
    prog = ("import importlib.util as u,sys\nfor n in sys.argv[1:]:\n try:\n  s=u.find_spec(n)\n"
            "  o=(s.origin if s and s.origin not in (None,'built-in','frozen') else (list(s.submodule_search_locations)[0] if s and s.submodule_search_locations else ''))\n"
            "  print(n+'\\t'+(o or ''))\n except Exception: print(n+'\\t')")
    for py in INTERPRETERS:
        if not os.path.exists(py):
            continue
        r = sh([py, "-c", prog, *names])
        for ln in r.stdout.splitlines():
            n, _, o = ln.partition("\t")
            if o.startswith(DIST):
                files.setdefault(o, set()).add(f"{n}@{py}")
    return files


def review_binaries(remove):
    hits = []
    r = sh(["dpkg", "-L", *remove])
    bins = {os.path.basename(p) for p in r.stdout.splitlines()
            if re.match(r"^/(usr/)?s?bin/[^/]+$", p)}
    texts = SCAN_TREES + glob.glob("/home/*/*.sh") + glob.glob("/home/*/bin")
    cron = "\n".join(sh(["crontab", "-l", "-u", u]).stdout for u in os.listdir("/home") + ["root"])
    for b in sorted(bins):
        if len(b) < 3:
            continue
        pat = rf"(^|[\"' /(\[]){re.escape(b)}([\"' )\],]|$)"
        found = [t for t in texts if sh(["grep", "-rlE", "--include=*.py", "--include=*.sh",
                                          "--include=*.service", "--include=*.timer", pat, t]).stdout.strip()]
        if re.search(pat, cron, re.M):
            found.append("crontab")
        if found:
            pkg = sh(["dpkg", "-S", shutil_which(b) or b]).stdout.split(":")[0]
            hits.append({"bin": b, "pkg": pkg, "where": found})
    return hits


def shutil_which(b):
    for d in ("/usr/bin", "/usr/sbin", "/bin", "/sbin"):
        if os.path.exists(f"{d}/{b}"):
            return f"{d}/{b}"
    return None


def keep_set(remove, imported, roots):
    """What the purge must spare: every package a consumer IMPORTS (even a
    GUI one — lehua's bot imports pygame/picamera2), the MUST_KEEP tools, and
    every python3-* library outside PY_REMOVABLE. A root is never kept."""
    rm = set(remove)
    keep = set(imported) & rm | (set(MUST_KEEP) & rm)
    keep |= {p for p in rm if p.startswith("python3-") and not PY_REMOVABLE.match(p)}
    return sorted(k for k in keep if k not in set(roots))


def main():
    if os.geteuid() != 0:
        print("UNKNOWN — run as root (maps of other uids)"); return 2
    roots = installed(ROOT_CANDIDATES)
    if not roots:
        print("OK — no desktop root package installed"); return 0
    try:
        remove = simulate(roots)
    except (RuntimeError, subprocess.SubprocessError) as e:
        print(f"UNKNOWN — {e}"); return 2
    rm = set(remove)
    keep_a = owners(mapped_files())
    names = import_names()
    dist = resolved_dist_files(names)
    keep_b_map = {}
    for f, who in dist.items():
        for p in owners([f]):
            keep_b_map.setdefault(p, set()).update(who)
    over_a = sorted(rm & keep_a)
    over_b = sorted(rm & set(keep_b_map))
    review = review_binaries(remove)
    keep = keep_set(rm, over_b, roots)
    try:
        remove2 = simulate(roots, keep)
    except (RuntimeError, subprocess.SubprocessError) as e:
        print(f"UNKNOWN — keep-simulation failed: {e}"); return 2
    rm2 = set(remove2)
    left_b = sorted(rm2 & set(keep_b_map))
    left_must = sorted(rm2 & set(MUST_KEEP))
    left_kept = sorted(rm2 & set(keep))
    # running overlaps that remain must ALL be desktop-session processes
    rep = {"roots": roots, "remove_n": len(remove), "remove": remove,
           "overlap_running": over_a,
           "overlap_imports": {p: sorted(keep_b_map[p])[:6] for p in over_b},
           "review_binaries": review, "import_names_n": len(names),
           "interpreters": [p for p in INTERPRETERS if os.path.exists(p)],
           "keep": keep, "remove_with_keep_n": len(remove2), "remove_with_keep": remove2,
           "left_imports": left_b, "left_must_keep": left_must, "left_kept": left_kept,
           "running_after_keep": sorted(rm2 & keep_a)}
    if "--json" in sys.argv:
        json.dump(rep, open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=1)
    print(f"roots={len(roots)} remove={len(remove)} import_names={len(names)} "
          f"interpreters={len(rep['interpreters'])}")
    print(f"overlap_running ({len(over_a)}): {' '.join(over_a)}")
    print(f"overlap_imports ({len(over_b)}): " + "; ".join(f"{p} <- {','.join(sorted(keep_b_map[p])[:3])}" for p in over_b))
    print(f"review_binaries ({len(review)}): " + "; ".join(f"{h['bin']}[{h['pkg']}]" for h in review))
    print(f"--- with keep ({len(keep)} pkgs): remove={len(remove2)} "
          f"left_imports={left_b} left_must_keep={left_must} left_kept={left_kept}")
    print(f"running_after_keep ({len(rep['running_after_keep'])}): {' '.join(rep['running_after_keep'])}")
    return 1 if (left_b or left_must or left_kept) else 0


if __name__ == "__main__":
    sys.exit(main())
