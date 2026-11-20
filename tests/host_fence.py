"""The test HOST FENCE — refusing ``sudo``, process-killers/daemons, and a
``systemctl`` that refuses mutating verbs, first on PATH for every pytest run and
everything it spawns. Moved out of tests/conftest.py on 2026-11-20.

ONE SHARED FENCE DIR PER (uid, shim content), NEVER DELETED (2026-11-20).
Each run used to mkdtemp its own dir and remove it in ``pytest_unconfigure``;
SIGTERM / SIGKILL / pytest-timeout's ``thread`` method skip that hook, and 3,478
dirs piled up in tmpfs /tmp. Four review rounds of a REAPER (delete a dead run's
dir) each found a new way a LIVE process still on that PATH was invisible to it
(an exec'd orphan; a symlinked or relative spelling; a fork-without-exec child; a
scan/lock race) — and every miss failed toward handing that process the REAL
sudo/pkill. The cure is structural: the shims are constant (per-run values ride
env vars), so every run shares one dir that no code path removes or renames. A
killed run leaks no dir. Only the per-run refusal LOG is per run — losing a
stale log is harmless.

The one way a shim can still vanish (Fable r5): systemd-tmpfiles ages /tmp
(`q /tmp 1777 root root 10d` on the fleet's Pi OS). Every start TOUCHES every
shim, so that needs 10 days with NO pytest start at all — and even then the
next start repairs it (header: "repaired N"). An orphan older than that, on an
idle box, is the residual.
"""

import os

_SUDO_FENCE = r"""#!/bin/sh
# tests/host_fence.py sudo fence — NO test may run a privileged command on the
# box it runs on. 2026-09-27: a mutation drill's mutants (bare rnsd restarts)
# ran REAL `sudo systemctl stop/start/restart rnsd` on the manager box, twice, mid
# experiment. One narrow allowance: a script inside pytest's own temp tree is
# run UNPRIVILEGED (fleet_hosts_selfheal tests drive `sudo -n <tmp stub>`).
echo "$(date +%T) ${PYTEST_CURRENT_TEST:-?} sudo $*" >> "$MF_SUDO_FENCE_LOG"
while [ $# -gt 0 ]; do
  case "$1" in
    -n|-E|-H|-k|-S) shift ;;
    -u|-g) shift 2 ;;
    --) shift; break ;;
    -*) shift ;;
    *) break ;;
  esac
done
# Fable r5: an EMPTY prefix matched everything (env-scrubbed child) and exec'd
# e.g. `sudo -n /usr/bin/pkill` — the allowance needs a real, absolute prefix.
# Fable r6: a string prefix let `<tmp>/pytest-of-x/../../usr/bin/pkill` out —
# match the RESOLVED path (no realpath, or no resolution = refuse).
# A BARE name is never in the allowance (Fable r8: realpath resolved it against
# cwd — inside pytest's tmp that exec'd <cwd>/<name> or failed 127, not a refusal).
case "$MF_SUDO_FENCE_TMP:$1" in
  /?*:*/*)
    t="$(realpath -m -- "$1" 2>/dev/null)" || t=""
    case "$t" in
      "$MF_SUDO_FENCE_TMP"?*) shift; exec "$t" "$@" ;;
    esac ;;
esac
echo "sudo refused under pytest (tests/host_fence.py): $*" >&2
exit 1
"""


_REFUSE = r"""#!/bin/sh
echo "$(date +%T) ${PYTEST_CURRENT_TEST:-?} $(basename "$0") $*" >> "$MF_SUDO_FENCE_LOG"
echo "$(basename "$0") refused under pytest (tests/host_fence.py): $*" >&2
exit 1
"""

_SYSTEMCTL_FENCE = r"""#!/bin/sh
# ALLOWLIST, VERBS AND OPTIONS (2026-11-20). A denylist of mutating verbs missed
# new ones twice (Fable r5: 23; r6: sleep, cancel, log-level…); then an allowlist
# of verbs with an OPEN option table let `-qp show restart` / `--stat status stop`
# (getopt clusters + abbreviations) hide the real verb behind an allowed word
# (r7). Now every token is classified against CLOSED lists — the options this
# repo uses — and anything unrecognised (a new option, a cluster, an
# abbreviation, an attached value, an empty arg) REFUSES. Only read-only verbs pass.
verb="" skip="" dd="" bad=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  if [ -n "$dd" ]; then
    [ -z "$verb" ] && verb="$a"
    continue
  fi
  case "$a" in
    "") bad="(empty argument)"; break ;;
    --) dd=1 ;;
    --user|--system|--no-pager|--no-legend|--no-block|--no-ask-password|--value|\
    --plain|--all|--failed|--full|--quiet|--now|--recursive|--version|--help|\
    -q|-a|-l|-r|-h) ;;
    -p|-P|-n|-t|-o|-H|-M|--property|--lines|--type|--output|--state|--host|\
    --machine|--timestamp) skip=1 ;;
    --property=*|--lines=*|--type=*|--output=*|--state=*|--host=*|--machine=*|\
    --timestamp=*) ;;
    -*) bad="$a"; break ;;
    *) [ -z "$verb" ] && verb="$a" ;;
  esac
done
if [ -n "$bad" ]; then
  echo "$(date +%T) ${PYTEST_CURRENT_TEST:-?} systemctl $*" >> "$MF_SUDO_FENCE_LOG"
  echo "systemctl option $bad refused under pytest (tests/host_fence.py): not on the read-only allowlist" >&2
  exit 1
fi
case "$verb" in
  ""|status|show|cat|help|is-active|is-enabled|is-failed|is-system-running|\
  list-units|list-unit-files|list-timers|list-sockets|list-paths|\
  list-automounts|list-jobs|list-dependencies|list-machines|get-default|\
  show-environment|whoami)
    exec "$MF_REAL_SYSTEMCTL" "$@" ;;
esac
echo "$(date +%T) ${PYTEST_CURRENT_TEST:-?} systemctl $*" >> "$MF_SUDO_FENCE_LOG"
echo "systemctl $verb refused under pytest (tests/host_fence.py): only read-only verbs pass" >&2
exit 1
"""

# Commands a test must never run for real on the box it runs on. 2026-09-27:
# a sudo-only fence let a mutant's non-systemd fallback `pkill -x rnsd` kill
# the LIVE rnsd (it runs as the operator's user — no sudo needed).
_REFUSED_BINARIES = ("pkill", "killall", "reboot", "shutdown", "poweroff",
                     "rnsd", "lxmd", "nomadnet", "meshtasticd",
                     # review S4 (2026-09-28): four src sites exec
                     # ['kill', ..., pid] — a mutant reaching one with a real
                     # pid kills for real, the pkill shape the fence was built
                     # from. systemd-run/busctl/dbus-send start or drive units
                     # around the systemctl fence. (`kill` here is the /bin
                     # binary; the shell builtin is untouched.) Residual: the
                     # module form `python -m RNS.Utilities.rnsd` cannot be
                     # fenced by PATH.
                     "kill", "systemd-run", "busctl", "dbus-send")



_FENCE_PREFIX = "mf-host-fence-"
#: Legacy per-run dirs (pre-2026-11-20) are ``mf-host-fence-<8 random chars>``;
#: the shared dir is ``mf-host-fence-u<uid>-<digest>`` — never confused.
_SHARED_TAG = "u"
#: A stale per-run log older than this is removed at start (a LOG, not a shim:
#: deleting it can never unfence anyone).
_LOG_MAX_AGE_S = 7 * 24 * 3600
#: Witness (honest_failure_modes #9), rendered by conftest's pytest_report_header.
_FENCE_STATS = {"fallback": "", "created": 0, "repaired": 0, "unknown": [], "legacy": 0,
                "tmpdir": "", "current": "",
                "logs_pruned": 0}


def _shims():
    """name -> body, the WHOLE fence content (its digest names the dir)."""
    out = {"sudo": _SUDO_FENCE, "systemctl": _SYSTEMCTL_FENCE}
    for name in _REFUSED_BINARIES:
        out[name] = _REFUSE
    return out


def _shared_fence_path(tmpdir, uid=None):
    import hashlib
    h = hashlib.sha256()
    for name, body in sorted(_shims().items()):
        h.update(name.encode() + b"\0" + body.encode() + b"\0")
    uid = os.getuid() if uid is None else uid
    return os.path.join(tmpdir, f"{_FENCE_PREFIX}{_SHARED_TAG}{uid}-{h.hexdigest()[:12]}")


def _real_systemctl():
    """The real systemctl, never a fence's. Review 11-20: a nested pytest's
    ``which`` found the PARENT's fence script, so the child's fence exec'd that
    script, which exec'd itself forever on every read-only verb."""
    import shutil
    inherited = os.environ.get("MF_REAL_SYSTEMCTL", "")
    if inherited and _FENCE_PREFIX not in inherited and os.access(inherited, os.X_OK):
        return inherited
    clean = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and not os.path.basename(os.path.realpath(p)).startswith(_FENCE_PREFIX))  # r6: `<fence>/.`, symlinks
    return shutil.which("systemctl", path=clean) or "/usr/bin/systemctl"


def _dir_is_ours_and_safe(path):
    """A real dir (not a symlink), owned by us, not group/other-writable."""
    import stat
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
            and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _shim_is_correct(path, body):
    import stat
    try:
        st = os.lstat(path)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or not st.st_mode & stat.S_IXUSR
                or st.st_nlink != 1):      # a hardlink can be changed via its other name
            return False
        with open(path, encoding="utf-8") as fh:
            return fh.read() == body
    except (OSError, UnicodeDecodeError):
        return False


def _write_shims(d, *, only_wrong):
    """Write every shim into ``d`` ATOMICALLY (tmp file + os.replace): a running
    suite sharing ``d`` sees either the old file or the new one, never a gap or
    a half-written script. Returns how many were (re)written."""
    import tempfile
    n = 0
    for name, body in _shims().items():
        target = os.path.join(d, name)
        if only_wrong and _shim_is_correct(target, body):
            continue
        fd, tmp = tempfile.mkstemp(prefix=".shim-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        n += 1
    return n


#: Names that legitimately live in the shared dir besides the shims.
def _is_known_extra(name):
    return (name.startswith("calls-") and name.endswith(".log")) or name.startswith(".shim-")


def _ensure_shared_fence(tmpdir):
    """Return ``(dir, "")`` — the shared fence, created or repaired IN PLACE —
    or ``(None, reason)`` when it cannot be trusted or repaired (caller falls
    back to a per-run dir and the header says why). Never removes or renames it."""
    d = _shared_fence_path(tmpdir)
    created = False
    try:
        os.mkdir(d, 0o755)
        created = True
    except FileExistsError:
        pass
    except OSError as e:
        return None, f"cannot create {d}: {e}"
    if not _dir_is_ours_and_safe(d):
        return None, (f"{d} is not ours or not safe (symlink, foreign owner, not a "
                      "dir, or group/other-writable)")
    try:
        n = _write_shims(d, only_wrong=True)
    except OSError as e:
        # Fable r5: a dir at a shim name / a read-only dir raised a bare OSError
        # in pytest_configure on EVERY start, forever. Fail over and SAY it.
        return None, f"cannot repair {d} ({e}); fix by hand: rm -rf '{d}'"
    _FENCE_STATS["created" if created else "repaired"] = n
    # Fable r5: refresh every shim's timestamps so /tmp's 10-day tmpfiles
    # age-out never sees an idle shim while pytest runs at all on this box
    for name in _shims():
        try:
            os.utime(os.path.join(d, name))
        except OSError:
            pass
    try:
        _FENCE_STATS["unknown"] = sorted(
            n for n in os.listdir(d) if n not in _shims() and not _is_known_extra(n))
    except OSError:
        _FENCE_STATS["unknown"] = []
    return d, ""


def _housekeep(tmpdir, shared):
    """Count legacy per-run dirs (witness only — never touched: whether a dead
    run's orphan still uses one cannot be proven) and prune stale per-run LOGS
    in the shared dir (safe: a log is not a shim)."""
    import stat
    import time
    # every fence dir but THIS version's shared one: legacy per-run dirs, fallbacks,
    # and shared dirs of an OLDER shim digest (Fable r6: those were invisible)
    current = os.path.basename(_shared_fence_path(tmpdir))
    legacy = 0
    try:
        for e in os.scandir(tmpdir):
            if e.name.startswith(_FENCE_PREFIX) and e.name != current:
                legacy += 1
    except OSError:
        pass
    _FENCE_STATS["legacy"] = legacy
    _FENCE_STATS["tmpdir"] = tmpdir
    _FENCE_STATS["current"] = current
    if not shared:
        return
    now, pruned = time.time(), 0
    try:
        for e in os.scandir(shared):
            if not _is_known_extra(e.name):   # a log, or an install killed mid-write
                continue
            st = os.lstat(e.path)
            if stat.S_ISREG(st.st_mode) and now - st.st_mtime > _LOG_MAX_AGE_S:
                os.unlink(e.path)
                pruned += 1
    except OSError:
        pass
    _FENCE_STATS["logs_pruned"] = pruned


def _install_sudo_fence():
    """Put the fence first on PATH for this process and every subprocess.
    Returns ``(fence_dir, log_path, is_shared)``; conftest removes only the log at
    a clean exit — never a fence dir (a per-run FALLBACK dir leaks by design, and
    the header says so)."""

    import shutil
    import tempfile
    tmp = tempfile.gettempdir()
    real_systemctl = _real_systemctl()
    d, why = _ensure_shared_fence(tmp)
    shared = d is not None
    # counted BEFORE any fallback dir exists: never count our own (Fable r5)
    _housekeep(tmp, d)
    if not shared:
        # refuse to trust / unable to repair the shared path: run on a private
        # per-run dir (the pre-11-20 shape — it leaks one dir per run, by design
        # loudly) and SAY so
        _FENCE_STATS["fallback"] = why + " — using a per-run fence dir"
        d = tempfile.mkdtemp(prefix=_FENCE_PREFIX)
        try:
            _write_shims(d, only_wrong=False)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
    log = os.path.join(d, f"calls-{os.getpid()}-{os.urandom(4).hex()}.log")
    os.environ["MF_REAL_SYSTEMCTL"] = real_systemctl
    os.environ["MF_SUDO_FENCE_LOG"] = log
    os.environ["MF_SUDO_FENCE_TMP"] = os.path.realpath(tmp).rstrip("/") + "/pytest-of-"  # the shim matches RESOLVED paths
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    return d, log, shared


def _fence_clear_command(tmpdir, current):
    """The one-line operator command the report header prints to clear other
    fence dirs (>1 day old, never the current shared one) while no pytest runs.

    2026-11-20, live: the r6 text echoed 'pytest running…' — that put the literal
    word back into the command line, so `pgrep -f '[p]ytest'` matched the
    operator's OWN shell and the command always declined. No part of this string
    may contain that word except the bracketed pattern (test-pinned)."""
    return ("pgrep -f '[p]ytest' >/dev/null && echo 'tests running - not clearing' "
            "|| find '" + tmpdir + "' -maxdepth 1 -name 'mf-host-fence-*' ! -name '"
            + current + "' -mmin +1440 -exec rm -rf {} +")


def _report_lines():
    """Host-fence witness lines for ``pytest_report_header`` — silent when the
    shape is healthy. Lives HERE (byte-locked with the MeshAnchor twin) so both
    repos' conftests render one implementation, never two hand-synced copies."""
    out = []
    if _FENCE_STATS["fallback"]:
        out.append(f"host fence: ⚠️ {_FENCE_STATS['fallback']}")
    if _FENCE_STATS["repaired"]:
        out.append(f"host fence: repaired {_FENCE_STATS['repaired']} shim(s) in the "
                   "shared fence dir (missing or wrong — e.g. /tmp age-out)")
    if _FENCE_STATS["unknown"]:
        out.append("host fence: ⚠️ unexpected entr(ies) in the shared fence dir, "
                   "first on PATH for every run: " + ", ".join(_FENCE_STATS["unknown"]))
    if _FENCE_STATS["legacy"]:
        # name the LITERAL dir this run counted (not the operator shell's $TMPDIR);
        # >1 day old only. An orphan of a killed run is NOT detectable here.
        out.append(f"host fence: {_FENCE_STATS['legacy']} other fence dir(s) in "
                   f"{_FENCE_STATS['tmpdir']} (pre-2026-11-20 per-run dirs, fallbacks, "
                   "older-version shared dirs; never auto-removed — an orphan of a "
                   "killed run may still use one). To clear those >1 day old while "
                   "no pytest runs: " + _fence_clear_command(_FENCE_STATS["tmpdir"],
                                                            _FENCE_STATS["current"]))
    return out


def _release_log(log):
    """Clean-exit release: this run's refusal LOG only — never the fence dir (a
    dir removed at exit unfenced any child a test left running)."""
    if log:
        try:
            os.unlink(log)
        except OSError:
            pass                         # no refusal happened = no log was written
