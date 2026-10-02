#!/usr/bin/env python3
"""hs_substrate_skew.py — running SUBSTRATE vs installed substrate, per process.

The "running-code skew" leg of scripts/honest_status.sh answers "is every
resident unit running its repo's newest code?". It drops — by construction —
every process that loads no REPO code: rnsd, lxmd, nomadnet run the pinned
RNS/LXMF fork out of site-packages, so they could never read "behind".

WHY THIS FILE EXISTS (2026-10-02). The 10-01 in-place fork rolls (rns
1.3.8+mf.3 → mf.4) changed what was INSTALLED on all nine RNS boxes and what
RAN on none: every rnsd kept its pre-roll process, while the check of record
printed "every ACTIVE mf/ma unit ... started at/after its own repo's newest
CODE commit". Measured 10-02 06:5x HST: 8 of 9 rnsd BEHIND, honest_status
silent. The check existed only as a sentence in a memory file ("rnsd MainPID
start time vs the rns-*.dist-info mtime"), re-run by hand — so every "fixed in
the fork, rolled to the fleet" claim read true at the layer nobody measured.
Nothing restarts rnsd on its own, so an untested mf.N STARTUP would first run
at the next power loss, on every box at once.

WHAT IT MEASURES, per live process (never per unit NAME):
  * the interpreter the process actually runs (/proc/PID/exe), as the process's
    own uid, with the process's own environment, cwd and sys.path[0] — i.e.
    where `import RNS` WOULD resolve for that exact process if it re-imported;
  * that copy's install time: max(mtime of its dist-info dir, mtime of the
    module's __init__.py / .py) — pip writes both fresh on every install;
  * the process start time from /proc/PID/stat + btime.
Both times come from the SAME box clock, so manager-vs-box skew cannot fake
it. ⚠️ A box clock that was WRONG at install time can (review A6): btime is
re-derived against the current clock, file mtimes are not, so a pip install
made on a stale RTC-less clock (moc4 once ran ~8 days behind) carries an OLD
mtime and a process started before it reads current. The repo leg's
clock-tolerance check does not cover this; an install on a known-bad clock
must be followed by a restart regardless of what this says.

Copy resolution walks sys.path in order, like the import system, because a box
carries MORE RNS environments than one probe expects (user site + root
dist-packages + pipx venvs — persistent_issues, 1.3.8 merge arc): the first
copy on the path is the one the process loads, whichever dist-info
importlib.metadata would happen to name first.

BASIS (printed, never collapsed): `entry` = the process's own entry point
(console script or -m module) belongs to a distribution whose requirement
closure contains the watched dist — rnsd→rns, lxmd→lxmf→rns, nomadnet→lxmf→rns.
That is a FACT about what the process loads. `repo` = the process runs repo
code (argv / cwd / PYTHONPATH under /opt/meshforge* or /opt/meshanchor), whose
requirements PIN the watched dist, so it may import it lazily; it can
over-report, and is labelled so. Anything else is NOT judged: the first live
run (the canary box, 10-02) called unattended-upgrades "behind on rns" merely because
the system python could import it — an env that CAN import a package is not a
process that DID, and a false BEHIND on the check of record is still a lie.

PROCESS SET: MainPID AND its descendants (depth 3). A unit's MainPID is not
always the interpreter: nomadnet.service's MainPID is a tmux server and the
python process is its child (in a separate tmux scope). Probing MainPID alone
would leave that daemon exactly as invisible as before.

Input  (stdin): one "<scope> <unit> <MainPID>" per line.
Args         : the watched distribution names (from the MF-FORK-PIN lines).
Output       : "SB <unit> <dist>=<ver> <days> <basis>"  installed copy NEWER
                                                         than the process
               "SC <unit> <dist>=<ver> <basis>"         process started after
               "SU <unit> <reason>"                     could not observe —
                                                         never 'current'
               nothing for a process that is not Python or resolves none of
               the watched dists.
Stdlib only, python>=3.9: it runs on every box's system python3.
"""
import json
import os
import subprocess
import sys
import time

# Seconds of slack between a process start and a file mtime. pip writes the
# files, THEN a restart starts the process; within this window the order is
# not knowable from one-second-ish stamps, so it is called current.
SLACK_S = 2

PROBE = r'''
import sys
# `-c` puts the target's cwd at sys.path[0]; drop it BEFORE importing anything,
# or a planted json.py in a writable cwd runs as the probe's uid (review A5).
_p0 = sys.path.pop(0)
import importlib.metadata as md, json, os, re
want, path0, entry_kind, entry_val = json.loads(sys.argv[1])
sys.path.insert(0, path0 if path0 is not None else _p0)
def norm(n): return re.sub(r"[-_.]+", "-", (n or "").lower())
def tops(d, name):
    t = (d.read_text("top_level.txt") or "").split()
    if t: return t
    seen = []
    for f in (d.files or []):
        p = str(f).split("/")[0]
        if p.endswith(".dist-info") or p.endswith(".egg-info") or p == "..": continue
        p = p[:-3] if p.endswith(".py") else p
        if p and p not in seen and not p.startswith("__"): seen.append(p)
    return seen or [name]
out = {"dists": {}, "closure": None}
for name in want:
    try: d0 = md.distribution(name)
    except md.PackageNotFoundError: continue
    found = None
    for entry in sys.path:
        if not entry or not os.path.isdir(entry): continue
        for top in tops(d0, name):
            pkg = os.path.join(entry, top, "__init__.py"); mod = os.path.join(entry, top + ".py")
            hit = pkg if os.path.isfile(pkg) else (mod if os.path.isfile(mod) else None)
            if hit: found = (entry, hit); break
        if found: break
    if not found: continue
    entry, hit = found
    mt = [os.stat(hit).st_mtime]; ver = "?"
    for d in md.distributions(path=[entry]):
        if norm(d.metadata["Name"]) == norm(name):
            ver = d.version
            p = getattr(d, "_path", None)
            if p is not None: mt.append(os.stat(str(p)).st_mtime)
            rec = os.path.join(str(p), "RECORD") if p is not None else ""
            if rec and os.path.isfile(rec): mt.append(os.stat(rec).st_mtime)
            break
    out["dists"][name] = [ver, max(mt), hit, tops(d0, name)]
owner = None
if entry_kind == "script":
    base = os.path.basename(entry_val)
    for d in md.distributions():
        if any(ep.group == "console_scripts" and ep.name == base for ep in d.entry_points):
            owner = d; break
elif entry_kind == "module":
    top = entry_val.split(".")[0]
    for d in md.distributions():
        if top in tops(d, d.metadata["Name"] or ""): owner = d; break
if owner is not None:
    seen, todo = set(), [norm(owner.metadata["Name"])]
    while todo:
        n = todo.pop()
        if n in seen: continue
        seen.add(n)
        try: d = md.distribution(n)
        except md.PackageNotFoundError: continue
        for r in (d.requires or []):
            if "extra ==" in r.replace('"', "").replace("'", ""): continue
            todo.append(norm(re.split(r"[\s<>=!~;\[(@]", r.strip(), maxsplit=1)[0]))
    out["closure"] = sorted(seen)
print(json.dumps(out))
'''


import re as _re
_SAFE = _re.compile(r"^[A-Za-z0-9_.+!-]{1,64}$")


def _norm(n):
    import re
    return re.sub(r"[-_.]+", "-", (n or "").lower())


# Longest first: /opt/meshforge is a prefix of /opt/meshforge-maps (the same
# precedence hs_skew_attr.awk uses — one ordering, hfm #5).
REPO_MARKERS = ("/opt/meshforge-maps", "/opt/meshanchor", "/opt/meshforge")
_IMPORTS = {}


def repo_bound(argv, cwd, env):
    """The repo root this process runs code from, or None."""
    hay = " ".join(argv) + " " + (cwd or "") + " " + env.get("PYTHONPATH", "")
    for m in REPO_MARKERS:
        if m in hay:
            return m
    return None


def repo_imports(root, top):
    """Does any .py under the repo import `top`? Cached per (root, top).

    Being repo-bound only says the process COULD import the substrate. The
    first fleet run called meshforge-maps behind on rns on four boxes, and
    that repo has no `import RNS` anywhere. A repo that never imports the
    module cannot be running a stale copy of it.
    """
    import re
    key = (root, top)
    if key not in _IMPORTS:
        pat = re.compile(r"^\s*(import|from)\s+%s\b" % re.escape(top), re.M)
        hit = False
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in (".git", "venv", ".venv",
                                                    "node_modules", "__pycache__")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(d, f), errors="replace") as fh:
                        if pat.search(fh.read()):
                            hit = True
                            break
                except OSError:
                    continue
            if hit:
                break
        _IMPORTS[key] = hit
    return _IMPORTS[key]


def top_of(hit):
    """Top-level module name from the resolved file path."""
    if os.path.basename(hit) == "__init__.py":
        return os.path.basename(os.path.dirname(hit))
    return os.path.basename(hit)[:-3]


def descendants(pid, depth=3):
    """MainPID plus its descendants, by ppid, breadth-first."""
    kids = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % d) as f:
                st = f.read()
            ppid = int(st[st.rindex(")") + 2:].split()[1])
        except (OSError, ValueError):
            continue
        kids.setdefault(ppid, []).append(int(d))
    out, level = [pid], [pid]
    for _ in range(depth):
        level = [c for p in level for c in kids.get(p, [])]
        out.extend(level)
    return out


def is_python(exe):
    b = os.path.basename(exe)
    return b.startswith("python") or b.startswith("pypy")


def parse_entry(argv):
    """(entry_kind, entry_val, sys.path[0]-source) from a python argv.

    argv[0] is the interpreter. Returns kind in {"script","module",None}.
    sys.path[0] follows CPython: a script's directory, or the cwd for -m/-c.
    """
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "-m" and i + 1 < len(argv):
            return "module", argv[i + 1], "cwd"
        if a.startswith("-m") and len(a) > 2:
            return "module", a[2:], "cwd"
        if a == "-c":
            return None, None, "cwd"
        if a in ("-W", "-X", "--check-hash-based-pycs") and i + 1 < len(argv):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return "script", a, "scriptdir"
    return None, None, "cwd"


def classify(start, mtime, slack=SLACK_S):
    """'behind' iff the installed copy was written after the process started."""
    return "behind" if mtime > start + slack else "current"


def proc_start_epoch(pid):
    with open("/proc/%d/stat" % pid) as f:
        stat = f.read()
    # comm may contain spaces/parens: split after the LAST ')'
    fields = stat[stat.rindex(")") + 2:].split()
    start_ticks = int(fields[19])  # field 22 overall
    with open("/proc/stat") as f:
        btime = next(int(l.split()[1]) for l in f if l.startswith("btime "))
    return btime + start_ticks / os.sysconf("SC_CLK_TCK")


def read_environ(pid):
    with open("/proc/%d/environ" % pid, "rb") as f:
        raw = f.read()
    env = {}
    for kv in raw.split(b"\0"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


def proc_uid_gid(pid):
    uid = gid = None
    with open("/proc/%d/status" % pid) as f:
        for line in f:
            if line.startswith("Uid:"):
                uid = int(line.split()[1])
            elif line.startswith("Gid:"):
                gid = int(line.split()[1])
    return uid, gid


def probe_process(pid, want, timeout=15):
    """Return (start_epoch, probe_dict) or raise OSError/ValueError with a reason."""
    exe = os.readlink("/proc/%d/exe" % pid)
    deleted = exe.endswith(" (deleted)")
    if deleted:
        exe = exe[:-len(" (deleted)")]
    if not is_python(exe):
        return None, None
    if deleted:
        # The PYTHON binary was replaced under the process (an apt python
        # upgrade): it runs an interpreter that no longer exists on disk, so
        # nothing on disk describes what it runs. Unknown, never current.
        raise ValueError("interpreter-replaced")
    with open("/proc/%d/cmdline" % pid, "rb") as f:
        argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    cwd = os.readlink("/proc/%d/cwd" % pid)
    # Run the interpreter by the path the PROCESS used, not /proc/PID/exe:
    # exe resolves a venv's bin/python symlink to the base interpreter, which
    # never finds pyvenv.cfg and so resolves a DIFFERENT RNS copy (review A1,
    # measured: a stale venv copy read as nothing at all — the pipx nomadnet
    # shape). argv[0] keeps the venv; fall back to exe when it is relative,
    # not a python, or gone.
    a0 = argv[0] if argv else ""
    if a0 and not os.path.isabs(a0):
        a0 = os.path.join(cwd, a0) if "/" in a0 else ""
    if a0 and is_python(a0) and os.path.isfile(a0):
        exe = a0
    env = read_environ(pid)
    uid, gid = proc_uid_gid(pid)
    start = proc_start_epoch(pid)
    kind, val, p0src = parse_entry(argv)
    if kind == "script" and not os.path.isabs(val):
        val = os.path.join(cwd, val)
    path0 = os.path.dirname(os.path.realpath(val)) if p0src == "scriptdir" else cwd
    kw = {}
    if uid is not None and uid != os.getuid():
        if os.getuid() != 0:
            raise ValueError("uid-%d-needs-root" % uid)
        kw = {"user": uid, "group": gid, "extra_groups": []}
    # The target's env, minus what would turn `-c` into an interactive REPL
    # reading OUR stdin (the unit list) — review A4. stdin is /dev/null anyway.
    for k in ("PYTHONINSPECT", "PYTHONSTARTUP"):
        env.pop(k, None)
    r = subprocess.run(
        [exe, "-c", PROBE, json.dumps([list(want), path0, kind, val])],
        env=env, cwd=cwd if os.path.isdir(cwd) else "/", capture_output=True,
        stdin=subprocess.DEVNULL, text=True, timeout=timeout, **kw)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["?"])[-1][:60].replace(" ", "_")
        raise ValueError("probe-rc%d:%s" % (r.returncode, tail))
    data = json.loads(r.stdout)
    data["repo_bound"] = repo_bound(argv, cwd, env)
    return start, data


def judge(unit, start, data, want, now):
    """Turn one probe result into output lines (pure; unit-tested)."""
    lines = []
    closure = set(_norm(n) for n in (data.get("closure") or []))
    for name in want:
        hit = data.get("dists", {}).get(name)
        if not hit:
            continue
        # A non-root target's probe runs as that target and controls its own
        # stdout: refuse anything that is not the shape we asked for, so a
        # version string cannot inject an SC line (review A4).
        try:
            ver, mtime, path = hit[0], float(hit[1]), hit[2]
            names = list(hit[3]) if len(hit) > 3 else [top_of(path)]
            ok = (isinstance(ver, str) and _SAFE.match(ver)
                  and isinstance(path, str) and "\n" not in path
                  and all(isinstance(t, str) and _SAFE.match(t) for t in names))
        except (TypeError, ValueError, IndexError):
            ok = False
        if not ok:
            return ["SU %s bad-probe-output" % unit]
        if _norm(name) in closure:
            basis = "entry"
        elif data.get("repo_bound") and any(
                repo_imports(data["repo_bound"], t) for t in names):
            # EVERY top-level name: rns ships CRNS + RNS, and grepping only
            # the first hid every repo unit running stale RNS (review A2).
            basis = "repo"
        else:
            # Importable is not imported — not judged, but COUNTED, so a
            # process loading RNS from a path we cannot attribute leaves a
            # trace instead of nothing (review A3).
            lines.append("SX %s %s" % (unit, name))
            continue
        if classify(start, mtime) == "behind":
            days = int(max(0, now - mtime) // 86400)
            lines.append("SB %s %s=%s %d %s" % (unit, name, ver, days, basis))
        else:
            lines.append("SC %s %s=%s %s" % (unit, name, ver, basis))
    return lines


def main(argv=None, stdin=None):
    args = [w for w in (argv if argv is not None else sys.argv[1:]) if w]
    # --no-descend: the caller supplies EXACT pids (a unit's own cgroup
    # members) — probe only those. The descendant walk is right for the
    # honest_status disclosure leg and wrong for anything that ACTS on the
    # answer: sshd's descendants include every `ssh box 'cd /opt/meshforge &&
    # python3 ...'`, which made ssh.service read as an RNS client (review D,
    # 2026-10-02 — a hold would have stopped sshd).
    no_descend = "--no-descend" in args
    want = [w for w in args if w != "--no-descend"]
    if not want:
        print("SU * no-watched-dists", flush=True)
        return 0
    now = time.time()
    for line in (stdin if stdin is not None else sys.stdin):
        parts = line.split()
        if len(parts) < 3:
            continue
        _scope, unit, pid_s = parts[0], parts[1], parts[2]
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid <= 0:
            continue
        emitted = set()
        # A systemd USER MANAGER's descendants are every user unit's processes;
        # those are enumerated (and labelled) in user scope already.
        tree = ([pid] if no_descend or unit.startswith("user@")
                else descendants(pid))
        for p in tree:
            try:
                start, data = probe_process(p, want)
            except subprocess.TimeoutExpired:
                res = ["SU %s probe-timeout" % unit]
            except PermissionError:
                res = ["SU %s no-access" % unit]
            except FileNotFoundError:
                continue  # exited between enumeration and probe: not a finding
            except (OSError, ValueError) as e:
                res = ["SU %s %s" % (unit, str(e).replace(" ", "_")[:80])]
            else:
                if data is None:
                    continue
                res = judge(unit, start, data, want, now)
            for out in res:
                if out not in emitted:
                    emitted.add(out)
                    # flushed per line: if the outer `timeout` kills us, the
                    # findings already printed must survive (review A7).
                    print(out, flush=True)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
