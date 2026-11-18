#!/usr/bin/env python3
"""bench_guard — Axiometa Headless stays on the bench (operator 2026-11-15).

PreToolUse hook (matcher ``Bash|Monitor|mcp__.*``). Invariant
``bench_flashers_lab_only`` in docs/fleet_roles.yaml:

  A. Axiometa Headless (the ``axiometa`` CLI / ``axiometa-cli`` package / its MCP
     server / its private toolchain under ``~/.axiometa``) runs only on a box
     whose declared role is exactly ``lab`` (``~/.config/meshforge/deployment.json``).
     Anywhere else — the manager box included — installing, registering,
     invoking or calling its MCP tools is refused. An unreadable role is not
     ``lab``. ssh is refused outright (the remote role is unknowable).
  B. On the lab box, a device touch (``upload`` / ``monitor`` / MCP
     ``read_serial``) must NAME every port as a ``/dev/serial/by-id/<name>`` link
     whose serial is on ``~/.config/meshforge/bench_boards``. The tool's own
     default (``pick_port`` -> ports[0], no confirm) is what this refuses.

Why: the Heltec V4 radio and the Axiometa board both enumerate as Espressif
303a:1001 — only the by-id serial tells them apart.

HOW (v4, review r3, 2026-11-15) — the ALPHABET is the grammar. Rounds 1-3
(Fable, by running) were one class: a lexer that groups characters differently
from bash, then a judge reading what the lexer left. v4 has no lexer to
disagree with bash. A Bash/Monitor command that mentions axiometa may contain
ONLY ``[A-Za-z0-9 _./:=,+~@-]`` plus ``|`` between pipeline stages. Nothing
in that alphabet quotes, expands, substitutes, redirects, globs, chains or
comments, so ``stage.split()`` is exactly the argv bash will build. Then:

stage 1 is the judged program (a reader, a package/registration verb, or the
plain CLI); later stages are filters; closed option tables refuse the reader
options that execute or write. The MENTION (what wakes the judge) is the
LITERAL name, contiguous once quoting is removed (2026-11-18). r4-r5 made it
an over-approximating construction scan on the belief "a false mention only
costs a re-phrase"; nobody measured that cost until the 11-18 replays —
~2,050–2,080 of ~17,300 real commands (11.9 %, the 14 d window slides) refused
while unnamed, every session paying it. A
REFUSAL HAS A PRICE: any change to what this guard refuses must replay the
corpus before AND after (`scripts/bench_guard_replay.py`) and state both
counts — UNNAMED (refused without the contiguous name) and named refusals —
beside the catch it buys.

THE JOB (re-scoped by the operator 2026-11-15 after review r7): stop a
COOPERATING session from ACCIDENTALLY running Headless off the lab box or
letting it pick the default port. It is a seatbelt, not a jail: a
session that DELIBERATELY constructs the name so its letters are not all in
one word ($ / ${…} / $(…) / backtick expansion, globs, brace expansion,
variables) is out of scope — ledger
bg-accept-deliberate-name-construction-2026-11-15 (one row,
replacing three earlier construction rows). Seven review rounds showed a
string guard cannot model bash completely; plain-shape commands are what it
must get right. Quotes, backslashes and bracket/brace characters INSIDE a
word that still spells the name ARE caught (the literal rule above).

SCOPE (honest): command strings and MCP tool names/args only. Not seen: a
script FILE that calls axiometa, aliases, variable indirection, deliberate
name construction (above), an MCP server named without "axiometa", a
Write/Edit of an MCP config or of bench_boards, sessions outside this
project, a human in a browser. Not a sandbox — off the lab box the lock is
that Headless is NOT INSTALLED; on the lab box it is device permissions.

Unreadable payload: mentions axiometa -> DENY, else allow; both leave a
witness (honest_failure_modes 9).

    python3 scripts/bench_guard.py hook < payload.json
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
BY_ID = "/dev/serial/by-id/"
MAX_LEN = 20000
SHELL_TOOLS = frozenset({"Bash", "Monitor"})
NAMES = ("axiometa", "axiometa-cli", "axiometa_cli")

# the 2.0.2 CLI (read from the published wheel): subcommands that touch no device
CLI_SAFE = frozenset({"boards", "board", "parts", "part", "devices", "provision",
                      "compile", "lib"})
CLI_DEVICE = frozenset({"upload", "monitor"})
MCP_SAFE = frozenset({"list_boards", "get_board", "list_modules", "get_module",
                      "list_devices", "provision", "compile"})
MCP_DEVICE = frozenset({"upload", "read_serial"})

ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                     " _./:=,+~@-")
READERS = frozenset({"grep", "egrep", "fgrep", "cat", "head", "tail", "wc", "ls", "stat",
                     "file", "du", "which", "type", "hash", "echo", "printf", "diff", "cut",
                     "tr", "jq", "sha256sum", "md5sum", "readlink", "realpath", "basename",
                     "dirname", "pgrep", "journalctl", "rm", "rmdir", "mkdir", "touch",
                     "true", "false", "test", "pkill"})
FILTERS = frozenset({"grep", "head", "tail", "wc", "sort", "uniq", "cut", "tr"})
GIT_VERBS = frozenset({"log", "show", "diff", "status", "add", "rm", "mv", "commit",
                       "ls-files", "rev-parse"})
PIP_READ = frozenset({"show", "uninstall", "list", "freeze", "index", "check"})
FIND_BANNED = ("-exec", "-ok", "-delete", "-fprint", "-fls")
_DEV = re.compile(r"(^|[/=])(dev|proc)(/|$)")
_CONFIG = re.compile(r"(^|[/=])(\.mcp\.json|\.claude\.json|settings(\.local)?\.json)$")
_SERIAL = re.compile(r"_([^_/]+)-if\d+(?:-port\d+)?")
_PY = re.compile(r"python3?(\.\d+)?")

FIX_A = ("Axiometa Headless runs only on the lab box (docs/fleet_roles.yaml invariant "
         "bench_flashers_lab_only). Run it in a session ON the lab-role box, or ask the "
         "operator to change this box's declared role.")
FIX_SHAPE = ("an axiometa-mentioning command may use only [A-Za-z0-9 _./:=,+~@-] and '|' "
             "into read-only filters (no quotes, $, globs, braces, redirects, chains, "
             "newlines, backslashes). Use the Grep/Read/Write tools; one plain command.")
FIX_B = ("name every port explicitly as /dev/serial/by-id/<name> (ls -l /dev/serial/by-id/) "
         "whose serial is listed in ~/.config/meshforge/bench_boards — adding a board "
         "there is the operator's deliberate act. The tool's default port is 'first "
         "enumerated', which can be a fleet radio.")


class Refuse(Exception):
    """A refusal reason."""


# ── box facts ──────────────────────────────────────────────────────────────────

def _home() -> Path:
    sys.path.insert(0, str(_REPO / "src"))
    from utils.paths import get_real_user_home  # first-party, MF001
    return get_real_user_home()


def _env_path(var: str, *rel: str) -> Path:
    env = os.environ.get(var)
    return Path(env) if env else _home().joinpath(*rel)


def is_lab() -> bool:
    """True only for a readable deployment.json whose role is exactly 'lab'."""
    try:
        p = _env_path("BENCH_GUARD_DEPLOYMENT", ".config", "meshforge", "deployment.json")
        role = json.loads(p.read_text()).get("role")
    except (OSError, ValueError, AttributeError, ImportError, RecursionError):
        return False
    return role == "lab"


def bench_serials() -> frozenset:
    try:
        text = _env_path("BENCH_GUARD_BOARDS", ".config", "meshforge",
                         "bench_boards").read_text()
    except (OSError, ValueError, ImportError):
        return frozenset()
    return frozenset(s.upper() for s in
                     (line.split("#", 1)[0].strip() for line in text.splitlines()) if s)


def port_is_bench(port, bench: frozenset) -> bool:
    if not isinstance(port, str) or not port.startswith(BY_ID):
        return False
    name = port[len(BY_ID):]
    if not name or "/" in name or name in (".", "..") or "_" not in name:
        return False
    m = _SERIAL.fullmatch(name[name.rfind("_"):])
    return bool(m) and m.group(1).upper() in bench


# ── the mention: the LITERAL name (2026-11-18, operator "Literal name only") ──
# r4 (star patterns) and r5 (letters-in-order) over-approximated DELIBERATE
# construction — out of scope since r7 (ledger
# bg-accept-deliberate-name-construction-2026-11-15). Replayed over 14 d of this
# project's Bash (first replay 11-18: 2,079 of 17,404, 11.95 %) they refused commands that
# never name the tool; 2,046 from letters-in-order alone. An ACCIDENT types the
# name CONTIGUOUSLY. The rule, exactly (Fable r3 #2: a per-word split was dead
# code — no break character is ever deleted, so it never changed a verdict):
# join `\`-newline continuations, case-fold, delete ONLY the characters quoting
# or an accident inserts inside a name (quotes ' " , backslash, the $ of
# $'…'/$"…", NUL, CR, [ ] { }), then look for the name. So `ax'io'meta`,
# `ax\iometa`, `a[x]iometa` read as the name; letters separated by anything
# else — a blank (`--max-io --metadata`, review 11-18 #1), ; & | < > ( ), or a
# character bash keeps literally (`relax.io/metadata`, Fable r2 #4) — do not.
# Accepted residual (r3 #6, same as HEAD; 0 in 17.3 k real commands before r3, 1 since = the r3 demo): letters
# joined ONLY by deleted characters (`cfg["max"]["io"]["metadata"]`) are refused, as are letters joined by `\`-newline continuations.

_NAME_FLAT = NAMES[0]
_INSERTED = re.compile(r"[\"'\\$\x00\r\[\]{}]")


def _mentions(s) -> bool:
    """`s`, continuations joined, case-folded, inserted characters deleted,
    contains the name contiguously. Linear."""
    if not isinstance(s, str):
        return False
    joined = s.lower().replace("\\\r\n", "").replace("\\\n", "")
    return _NAME_FLAT in _INSERTED.sub("", joined)


# ── the shape ──────────────────────────────────────────────────────────────────

def _stages(cmd: str):
    if len(cmd) > MAX_LEN:
        raise Refuse("command too long to judge. " + FIX_SHAPE)
    bad = sorted({c for c in cmd if c not in ALPHABET and c != "|"})
    if bad:
        shown = ", ".join(repr(c) for c in bad[:8])
        raise Refuse(f"character(s) {shown} in an axiometa command. " + FIX_SHAPE)
    stages = [st.split() for st in cmd.split("|")]
    if any(not st for st in stages):
        raise Refuse("empty pipeline stage. " + FIX_SHAPE)
    return stages


def _long_hits(word: str, banned, min_len: int = 3) -> bool:
    """A banned long option or an abbreviation of it (getopt and git take prefixes)."""
    if not word.startswith("--"):
        return False
    flag = word.partition("=")[0]
    return any(flag.startswith(b) or (len(flag) >= min_len and b.startswith(flag))
               for b in banned)


def _short_has(word: str, letters: str) -> bool:
    return word.startswith("-") and not word.startswith("--") and \
        any(c in word[1:] for c in letters)


def _no_devices(words):
    """No device/proc path (but /dev/null) and no MCP/Claude config file as an argument."""
    for w in words[1:]:
        if w != "/dev/null" and _DEV.search(w):
            raise Refuse(f"{w!r} is a device path in an axiometa command. " + FIX_B)
        if _CONFIG.search(w):
            raise Refuse(f"{w!r} is an MCP/Claude config file in an axiometa command. "
                         + FIX_SHAPE)


def _filter_ok(words) -> bool:
    prog = words[0]
    rest = words[1:]
    if prog == "sort":
        return not any(_short_has(w, "oT") or
                       _long_hits(w, ("--output", "--compress-program", "--temporary"))
                       for w in rest)
    return prog in FILTERS                      # uniq OUT: the dev/config rule judges OUT


def _reader(words) -> bool:
    prog = words[0]
    rest = words[1:]
    if prog == "command":
        return rest[:1] in (["-v"], ["-V"])
    if prog == "find":
        return not any(w.startswith(FIND_BANNED) for w in rest)
    if prog == "git":
        if any(_short_has(w, "Ocx") or
               _long_hits(w, ("--output", "--open-files-in-pager", "--ext-diff", "--exec",
                              "--textconv", "--upload-pack", "--config-env"))
               for w in rest):
            return False
        verb = next((w for w in rest if not w.startswith("-")), None)
        return verb in GIT_VERBS and rest[0] == verb
    if prog in ("sort", "uniq"):
        return _filter_ok(words)
    return prog in READERS


def _package_verb(words):
    """read|install|remove|get, or None. The verb must come FIRST (`pip --log show install x`)."""
    prog = words[0]
    rest = words[1:]
    if _PY.fullmatch(prog) and rest[:2] == ["-m", "pip"]:
        prog, rest = "pip", rest[2:]
    v = rest[0] if rest and not rest[0].startswith("-") else None
    if re.fullmatch(r"pip3?(\.\d+)?", prog):
        if any(_long_hits(w, ("--python",)) for w in rest):
            return None
        return "read" if v in PIP_READ else "install" if v == "install" else None
    if prog == "pipx":
        return "read" if v in ("uninstall", "list") else "install" if v == "install" else None
    if prog == "uv" and rest[:1] == ["tool"] and len(rest) > 1:
        return ("read" if rest[1] in ("uninstall", "list")
                else "install" if rest[1] == "install" else None)
    if prog == "claude" and rest[:1] == ["mcp"] and len(rest) > 1:
        return {"get": "get", "list": "get", "remove": "remove", "add": "mcp-add"}.get(rest[1])
    return None


def _mcp_add_is_the_server(words) -> bool:
    """`claude mcp add [-s|--scope S] NAME -- axiometa mcp` and nothing else: the
    stored command runs at every server start (review r4 F3)."""
    rest = words[3:]
    if rest[:1] in (["-s"], ["--scope"]):
        rest = rest[2:]
    elif rest[:1] and rest[0].startswith("--scope="):
        rest = rest[1:]
    return len(rest) > 2 and rest[1] == "--" and _cli_args(rest[2:]) == ["mcp"]


def _cli_args(words):
    if "=" in words[0]:
        return None                             # r7: bash runs the NEXT word (assignment)
    prog = os.path.basename(words[0])
    if prog == "axiometa" and "/.axiometa/" not in words[0]:
        return words[1:]
    if _PY.fullmatch(prog) and len(words) > 2 and words[1] == "-m" and \
            re.fullmatch(r"axiometa_cli(\.cli|\.__main__)?", words[2]):
        return words[3:]
    return None


def _ports(args):
    ports = []
    for k, a in enumerate(args):
        flag, eq, val = a.partition("=")
        nxt = args[k + 1] if k + 1 < len(args) else ""
        if a == "-p":
            ports.append(nxt)
        elif a.startswith("-p") and not a.startswith("--"):
            ports.append(a[2:])
        elif flag.startswith("--p") and "--port".startswith(flag):
            ports.append(val if eq else nxt)
    return ports


def _port_value(a: str) -> str:
    """The port inside one argument: `P`, `-pP` or `--port=P` (prefixes as in _ports)."""
    if a.startswith("-p") and not a.startswith("--"):
        return a[2:]
    flag, eq, val = a.partition("=")
    if eq and flag.startswith("--p") and "--port".startswith(flag):
        return val
    return a


def _judge_cli(args, lab: bool, bench: frozenset):
    sub = next((a for a in args if not a.startswith("-")), None)
    if sub == "login":
        raise Refuse("`axiometa login` stores an Axiometa account key — the operator's "
                     "act, never a session's (operator 2026-11-15).")
    if not lab:
        raise Refuse("this box is not the lab box. " + FIX_A)
    if sub == "mcp":
        raise Refuse("`axiometa mcp` is started by the harness as an MCP server, not "
                     "from Bash.")
    if sub is None or sub in CLI_SAFE:
        return
    if sub not in CLI_DEVICE:
        raise Refuse(f"`axiometa {sub}` is not a subcommand this guard knows (2.0.2); a "
                     "new one may touch a device. Update bench_guard first.")
    if "--" in args:
        raise Refuse(f"`--` in `axiometa {sub}`: after it a word like -h is a sketch, "
                     "not help. " + FIX_SHAPE)
    if "-h" in args or "--help" in args:
        return
    ports = _ports(args)
    if not ports or not all(port_is_bench(p, bench) for p in ports):
        raise Refuse(f"`axiometa {sub}` touches a device: " + FIX_B)


def judge_shell(cmd: str, lab: bool, bench: frozenset):
    """None if allowed; raises Refuse otherwise."""
    if not _mentions(cmd):
        return
    stages = _stages(cmd)
    first, later = stages[0], stages[1:]
    for st in later:
        if not _filter_ok(st):
            raise Refuse(f"pipe into {' '.join(st[:2])!r} from an axiometa command. "
                         + FIX_SHAPE)
        _no_devices(st)
    if not _mentions(" ".join(first)):
        _no_devices(first)                      # r5: `uniq src /dev/x | grep NAME`
        return                                  # the mention is only in a filter's pattern
    prog = first[0]
    if prog == "ssh":
        raise Refuse("ssh with axiometa: the remote box's role is unknowable from here. "
                     + FIX_A)
    args = _cli_args(first)
    if args is not None:
        # r7: no device path on the CLI but the bench by-id port(s) it names
        _no_devices(first[:1] + [a for a in args
                                 if not port_is_bench(_port_value(a), bench)])
        _judge_cli(args, lab, bench)
        return
    _no_devices(first)
    if _reader(first):
        return
    pv = _package_verb(first)
    if pv == "read" or (pv == "get" and lab):
        return
    if pv == "remove":
        raise Refuse("`claude mcp remove` of the Axiometa server is the operator's act.")
    if pv == "mcp-add" and lab and not _mcp_add_is_the_server(first):
        raise Refuse("`claude mcp add` on the lab box registers only `NAME -- axiometa mcp` "
                     "(the stored command runs at every server start).")
    if pv in ("install", "mcp-add") and lab:
        return
    if pv in ("install", "get", "mcp-add"):
        raise Refuse("installing, registering or starting Headless (`claude mcp get|list` "
                     "health-check = start the server) off the lab box. " + FIX_A)
    raise Refuse(f"{prog!r} with axiometa is not a form this guard can judge"
                 + (" — on the lab box use the plain `axiometa` CLI. " if lab
                    else ", on a box that is not the lab box. " + FIX_A + " ") + FIX_SHAPE)


def decide_bash(cmd: str, lab: bool, bench: frozenset):
    """A refusal reason for a shell command on a box of the given role, or None."""
    try:
        judge_shell(cmd, lab, bench)
    except Refuse as r:
        return str(r)
    return None


# ── MCP ────────────────────────────────────────────────────────────────────────

def judge_mcp(tool_name: str, tool_input: dict, lab: bool, bench: frozenset):
    if not lab:
        raise Refuse(f"MCP tool {tool_name} is Axiometa Headless; this box is not the lab "
                     "box. " + FIX_A)
    tool = tool_name.rsplit("__", 1)[-1].strip().lower()
    if tool in MCP_SAFE:
        return
    if tool in MCP_DEVICE:
        if port_is_bench(tool_input.get("port"), bench):
            return
        raise Refuse(f"MCP tool {tool_name} touches a device: pass port= and " + FIX_B)
    raise Refuse(f"MCP tool {tool_name} is not one this guard knows (2.0.2 has 9). "
                 "Update bench_guard first.")


def decide(payload: dict):
    """Return a refusal reason, or None to allow."""
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise ValueError("tool_name is not a non-empty string")
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        raise ValueError("tool_input is not an object")
    try:
        if tool in SHELL_TOOLS:
            cmd = ti.get("command") or ""
            if not isinstance(cmd, str):
                raise ValueError("command is not a string")
            judge_shell(cmd, is_lab(), bench_serials())
        elif tool.startswith("mcp__") and _mentions(tool):
            judge_mcp(tool, ti, is_lab(), bench_serials())
    except Refuse as r:
        return str(r)
    return None


# ── hook wire ──────────────────────────────────────────────────────────────────

def _witness(what: str, raw: str) -> None:
    try:
        p = _env_path("BENCH_GUARD_WITNESS", ".local", "state", "meshforge",
                      "bench_guard_witness.log")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(f"{_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')} "
                    f"{what} payload_len={len(raw)}\n")
    except Exception:  # noqa: BLE001 — a witness must never turn a decision into a crash
        pass


_JSON_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def _json_unescape(m) -> str:
    e = m.group(1)
    if e[0] == "u" and len(e) == 5:
        return chr(int(e[1:], 16))
    return _JSON_ESC.get(e, e)                  # \\ \" \/ and unknown -> the char


def hook(raw: str):
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        why = decide(payload)
    except Exception as e:  # noqa: BLE001 — a hook must never crash open; witnessed below
        _witness(f"parse-failed {type(e).__name__}", raw)
        # r6/r7: DECODE JSON escapes in ONE pass (deleting \uXXXX left "xiometa";
        # decode-then-strip turned an encoded backslash + \n into a stray "n")
        dec = re.sub(r"\\(u[0-9a-fA-F]{4}|.)", _json_unescape, raw)
        if _mentions(raw) or _mentions(dec):
            return ("bench_guard: could not read a tool call that mentions axiometa — "
                    "refused (fail closed). " + FIX_A)
        return None
    return f"bench_guard: {why}" if why else None


def main(argv) -> int:
    if argv[1:2] != ["hook"]:
        print(__doc__.strip().splitlines()[0])
        print("usage: bench_guard.py hook < pretooluse.json")
        return 2
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    why = hook(raw)
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
