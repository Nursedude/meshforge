"""The CLOSED inventory of every raw file read in ``src/`` (+ the standalone
root ``rns_config_utils.py``) — WITNESS-TUI r12, Fable r11 #2.

r11's gate keyed on functions that CALL ``get_config_file``/``get_config_path``
— the wrong quantity: a reader that takes the config as a path ARGUMENT, a
helper with another name, ``open()``, ``Path.open``, ``io.open``, ``sudo cat``
were all invisible (Fable planted 12 shapes, it saw 1). r12 measures the
thing itself: EVERY raw read call site, keyed ``file::qualname::kind`` with a
count, frozen in ``fixtures/file_read_inventory.json`` with a CLASS each:

* ``rns-config`` — reads the RNS config as BYTES and hands them to the one
  reader (``utils.rns_config_reader``), or inlines its rule where the seam
  cannot be imported (the MeshAnchor-parity ``rns_init.py``; the standalone
  ``rns_config_utils.py``). Only a BYTES read (``read_bytes``, a ``cat`` argv
  captured as bytes) or rnsd's own ``ConfigObj`` may carry this class.
* ``other`` — reads a different file. ``audited`` says how it was judged.

A new site, a raised count, or a vanished site fails the test with the
remedy. Regenerate after classifying by hand:
``python3 tests/_file_read_inventory.py --write`` (keeps existing classes;
new keys land ``UNCLASSIFIED``, which fails until a human names them).
"""

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = Path(__file__).resolve().parent / "fixtures" / "file_read_inventory.json"
READ_ATTRS = {"open", "read_text", "read_bytes", "fdopen"}
CONFIG_PARSERS = {"ConfigObj"}
RNS_CONFIG_KINDS = {"read_bytes", "cat", "ConfigObj"}   # bytes in; ConfigObj = rnsd's own parser
# argv readers: ["sudo", "-n", "cat", path] etc. Only a BYTES ``cat`` may be
# rns-config: grep/head see lines their own way (Fable r12 #1: sudo grep
# disagreed with the seam on 12/14 shapes), and ``cat`` with text=True decodes
# before the seam does — both read as kinds (``grep``, ``cat+text``) that the
# rns-config class refuses (r13, Fable r12 #3).
ARGV_READERS = ("cat", "grep", "head", "tail")
_TEXT_KWARGS = {"text", "universal_newlines", "encoding", "errors"}

# STATED LIMITS — this gate is a SEATBELT against ACCIDENTAL drift, not a jail
# against a determined author (memory: string guard = seatbelt). It sees a
# literal call by name: open/read_text/read_bytes/fdopen, a *ConfigObj, and an
# argv LITERAL (list/tuple) naming one of ARGV_READERS. It does NOT see, by
# design: an argv built in a variable / concatenated / passed as args=; a
# reader reached through getattr, an alias, map(Path.read_text, …), a bound
# method; configparser/fileinput/linecache; a reader inside `bash -c` text;
# a seam read that is then re-split by hand. Fable r12 planted 20 shapes, 4
# were seen; r13 adds grep/head/tail and text-mode cat. Review catches the rest.


def scanned_files(root=ROOT):
    files = sorted((root / "src").rglob("*.py"))
    extra = root / "rns_config_utils.py"
    return files + ([extra] if extra.exists() else [])


def _kind(call):
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    if name == "open" or name in READ_ATTRS:
        return name
    for parser in CONFIG_PARSERS:               # the _ConfigObj safe_import alias too
        if name and name.endswith(parser):
            return parser
    for arg in call.args:                       # ["sudo", "-n", "cat", path]
        if not isinstance(arg, (ast.List, ast.Tuple)):
            continue
        for e in arg.elts:
            if isinstance(e, ast.Constant) and e.value in ARGV_READERS:
                text = any(k.arg in _TEXT_KWARGS and not (
                    isinstance(k.value, ast.Constant) and k.value.value in (False, None))
                    for k in call.keywords)
                return f"{e.value}+text" if text else e.value
    return None


def sites(root=ROOT):
    """Counter of ``rel::qualname::kind`` over every read call (module level
    = ``<module>``; methods/nested functions dotted)."""
    out = Counter()
    for f in scanned_files(root):
        rel = f.relative_to(root).as_posix()
        tree = ast.parse(f.read_bytes().decode("utf-8"))

        def walk(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, scope + [child.name])
                    continue
                if isinstance(child, ast.Call):
                    k = _kind(child)
                    if k:
                        out[f"{rel}::{'.'.join(scope) or '<module>'}::{k}"] += 1
                walk(child, scope)
        walk(tree, [])
    return out


def load():
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def problems(found, inv):
    """Every way ``found`` disagrees with the inventory, each with its remedy."""
    out = []
    for key, n in sorted(found.items()):
        entry = inv.get(key)
        if entry is None:
            out.append(f"NEW read site {key} (x{n}): if it reads the RNS config, read it "
                       "with utils.rns_config_reader.read_config_text (strict=True to "
                       "write back); otherwise name the file it reads — then "
                       "`python3 tests/_file_read_inventory.py --write` and classify it")
        elif n > entry["count"]:
            out.append(f"MORE read sites in {key}: {entry['count']} -> {n} — classify the new "
                       "one as above, then --write")
    for key, entry in sorted(inv.items()):
        if key not in found:
            out.append(f"STALE inventory entry {key} — the site is gone; --write to drop it")
        elif found[key] < entry["count"]:
            out.append(f"FEWER read sites in {key}: {entry['count']} -> {found[key]}; --write")
        cls = entry.get("class")
        if cls not in ("rns-config", "other"):
            out.append(f"{key} is {cls!r} — a human must classify it rns-config/other")
        elif cls == "rns-config" and key.rsplit("::", 1)[1] not in RNS_CONFIG_KINDS:
            out.append(f"{key} reads the RNS config with "
                       f"{key.rsplit('::', 1)[1]}() — read BYTES and decode via the seam")
    return out


def write():
    old = load() if INVENTORY.exists() else {}
    new = {}
    for key, n in sorted(sites().items()):
        entry = dict(old.get(key, {"class": "UNCLASSIFIED", "audited": ""}))
        entry["count"] = n
        new[key] = entry
    INVENTORY.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return new


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python3 tests/_file_read_inventory.py --write")
    inv = write()
    left = sum(1 for e in inv.values() if e["class"] == "UNCLASSIFIED")
    print(f"{len(inv)} keys written to {INVENTORY}; {left} UNCLASSIFIED")
