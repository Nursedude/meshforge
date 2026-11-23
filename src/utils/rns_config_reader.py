"""Read the RNS config's interfaces the way rnsd reads them — one reader.

rnsd loads its config with ConfigObj (``RNS.vendor.configobj``) and brings
up every section under ``[interfaces]`` (``for name in
self.config["interfaces"]``). This module is the ONE place MeshForge turns a
config file into a list of interfaces, so it uses the same parser:

* ``_read_configobj`` — RNS's own ConfigObj, when RNS is importable;
* ``_read_strict`` — a fallback for boxes/CI without RNS, built from
  ConfigObj's own section/keyword/value regexes (copied below; ConfigObj is
  BSD-3, (C) 2005-2014 Michael Foord, Nicola Larosa, Rob Dennis, Eli
  Courtwright). It refuses — never guesses — what it does not implement
  (triple-quoted values).

``tests/test_rns_config_reader.py`` holds both paths to one literal oracle.

Output (both paths): ``[{"name", "settings": {str: str},
"subinterfaces": [{"name", "settings"}]}]`` in file order. List values are
joined with ``", "`` so ``settings`` stays ``str -> str`` for its consumers;
a sub-section (an RNodeMulti sub-interface ``[[[High Datarate]]]``) is a
``subinterfaces`` entry, never an interface of its own.

A config rnsd cannot load (duplicate key or section, bad nesting, a line
that is neither section nor ``key = value``, a bare key directly under
``[interfaces]``) raises ``RNSConfigError`` — a list of guessed rows from a
file rnsd rejects is the defect this replaced (WITNESS-TUI r9, 2026-11-22:
the old reader took any ``[[…]]`` line as an interface).
"""

import io
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.safe_import import safe_import

_ConfigObj, HAS_CONFIGOBJ = safe_import('RNS.vendor.configobj', 'ConfigObj')


class RNSConfigError(ValueError):
    """The RNS config cannot be read as rnsd would read it."""


Interface = Dict[str, Any]
Content = Union[str, bytes]

# ConfigObj's ConfigParser-style interpolation key (rnsd's default):
# `%(name)s`. MeshForge does not evaluate it — both paths refuse it by name.
_INTERP = re.compile(r"%\(([^)]*)\)s")
_UTF8_BOM = b"\xef\xbb\xbf"


def _raw_lines(content: Content) -> List[bytes]:
    """The file as rnsd's ConfigObj(path) sees it: bytes, readlines()
    (split on b"\\n" ONLY — not \\f, \\x85, U+2028 or a lone \\r, which
    str.splitlines() would also split on; Fable r9 #2)."""
    if isinstance(content, str):
        try:
            content = content.encode("utf-8")
        except UnicodeEncodeError as e:   # a lone surrogate (Fable r10 NIT)
            raise RNSConfigError(f"UnicodeEncodeError: {e}") from e
    return io.BytesIO(content).readlines()


# ── the file as TEXT, for every hand scanner (r11 seam 1) ───────────────
#
# Every scanner of the RNS config that is not the interface reader
# (share_instance, shared_instance_type, rpc_key, instance_name, loglevel,
# validate_config, the shadow diff, …) reads its text HERE, so each sees the
# lines rnsd sees: BOM gone, utf-8, split on "\n" only. read_text() kept the
# BOM in front of "[reticulum]", turned a lone "\r" into a ghost line, and
# raised an untyped UnicodeDecodeError through its caller (Fable r10 #2).
# tests/test_rns_config_file_read.py gates new read_text() readers.


def config_text(content: Content, *, strict: bool = False) -> str:
    """The config as text the way rnsd decodes it: UTF-8 BOM stripped,
    utf-8, NO newline translation (a lone ``\\r`` stays inside its line).

    ``strict=False`` (scanners): undecodable bytes become U+FFFD — a scan
    never aborts; whether rnsd can LOAD the file is ``read_interfaces``'s
    verdict, not this one's. ``strict=True`` (read-modify-write callers):
    undecodable bytes raise ``RNSConfigError``, so nothing is written back
    over bytes the writer could not read."""
    if isinstance(content, bytes):
        if content.startswith(_UTF8_BOM):
            content = content[len(_UTF8_BOM):]
        try:
            return content.decode("utf-8", errors="strict" if strict else "replace")
        except UnicodeDecodeError as e:
            raise RNSConfigError(f"UnicodeDecodeError: {e}") from e
    return content[1:] if content.startswith("\ufeff") else content


def config_lines(content: Content) -> List[str]:
    """rnsd's lines: split on ``\\n`` only, trailing CR/LF stripped."""
    text = config_text(content)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.rstrip("\r") for line in lines]


def read_config_text(path, *, strict: bool = False) -> str:
    """``config_text`` of the file at ``path`` (read as bytes). OSError
    propagates — a file that could not be read is the caller's to say."""
    return config_text(path.read_bytes(), strict=strict)


# ── shared output shaping ───────────────────────────────────────────────


def _flat(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _is_section(value: Any) -> bool:
    return hasattr(value, "items") and hasattr(value, "keys")


def _split(section) -> Tuple[Dict[str, str], List[Tuple[str, Any]]]:
    settings: Dict[str, str] = {}
    subs: List[Tuple[str, Any]] = []
    for key, value in section.items():
        if _is_section(value):
            subs.append((key, value))
            continue
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if _INTERP.search(str(item)):
                raise RNSConfigError(
                    f"value of '{key}' uses %(…)s interpolation — rnsd would "
                    f"substitute it; MeshForge does not evaluate it (write the "
                    f"value literally to see this interface here)")
        settings[key] = _flat(value)
    return settings, subs


def _shape(root) -> List[Interface]:
    if "interfaces" not in root:
        return []
    ifaces = root["interfaces"]
    if not _is_section(ifaces):
        raise RNSConfigError("'interfaces' is a key, not a [interfaces] section")
    out: List[Interface] = []
    for name, section in ifaces.items():
        if not _is_section(section):
            raise RNSConfigError(
                f"'{name}' is a key directly under [interfaces] — rnsd "
                f"expects only [[interface]] sections there")
        settings, subs = _split(section)
        out.append({
            "name": name,
            "settings": settings,
            "subinterfaces": [
                {"name": sub_name, "settings": _split(sub)[0]}
                for sub_name, sub in subs
            ],
        })
    return out


# ── path 1: RNS's own ConfigObj ─────────────────────────────────────────


def _configobj_root(content: Content):
    # The same call shape as rnsd's ConfigObj(path): a list of BYTES lines
    # (ConfigObj strips the BOM and decodes). interpolation=False so values
    # read RAW and _split refuses %(…)s by name — ConfigObj would otherwise
    # raise MissingInterpolationOption lazily, on value access, past any
    # try around the constructor (Fable r9 #3).
    try:
        return _ConfigObj(_raw_lines(content), interpolation=False)
    except RNSConfigError:
        raise
    except Exception as e:  # ConfigObjError family, UnicodeDecodeError, …
        raise RNSConfigError(f"{type(e).__name__}: {e}") from e


def _read_configobj(content: Content) -> List[Interface]:
    root = _configobj_root(content)
    try:
        return _shape(root)
    except RNSConfigError:
        raise
    except Exception as e:
        raise RNSConfigError(f"{type(e).__name__}: {e}") from e


# ── path 2: strict fallback with ConfigObj's grammar ────────────────────

_KEYWORD = re.compile(r'''^ # line start
    (\s*)                   # indentation
    (                       # keyword
        (?:".*?")|          # double quotes
        (?:'.*?')|          # single quotes
        (?:[^'"=].*?)       # no quotes
    )
    \s*=\s*                 # divider
    (.*)                    # value (including list values and comments)
    $   # line end
    ''', re.VERBOSE)

_SECTIONMARKER = re.compile(r'''^
    (\s*)                     # 1: indentation
    ((?:\[\s*)+)              # 2: section marker open
    (                         # 3: section name open
        (?:"\s*\S.*?\s*")|    # at least one non-space with double quotes
        (?:'\s*\S.*?\s*')|    # at least one non-space with single quotes
        (?:[^'"\s].*?)        # at least one non-space unquoted
    )                         # section name close
    ((?:\s*\])+)              # 4: section marker close
    \s*(\#.*)?                # 5: optional comment
    $''', re.VERBOSE)

_VALUEEXP = re.compile(r'''^
    (?:
        (?:
            (
                (?:
                    (?:
                        (?:".*?")|              # double quotes
                        (?:'.*?')|              # single quotes
                        (?:[^'",\#][^,\#]*?)    # unquoted
                    )
                    \s*,\s*                     # comma
                )*      # match all list items ending in a comma (if any)
            )
            (
                (?:".*?")|                      # double quotes
                (?:'.*?')|                      # single quotes
                (?:[^'",\#\s][^,]*?)|           # unquoted
                (?:(?<!,))                      # Empty value
            )?          # last item in a list - or string value
        )|
        (,)             # alternatively a single comma - empty list
    )
    \s*(\#.*)?          # optional comment
    $''', re.VERBOSE)

_LISTVALUEEXP = re.compile(r'''
    (
        (?:".*?")|          # double quotes
        (?:'.*?')|          # single quotes
        (?:[^'",\#]?.*?)       # unquoted
    )
    \s*,\s*                 # comma
    ''', re.VERBOSE)


class _Sec(dict):
    """An ordered mapping of keys and sub-sections, like a ConfigObj Section."""

    def __init__(self, depth: int, parent: Optional["_Sec"]):
        super().__init__()
        self.depth = depth
        self.parent = parent if parent is not None else self


def _unquote(value: str, lineno: int) -> str:
    if not value:
        raise RNSConfigError(f"empty value at line {lineno}")
    if value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value


def _handle_value(raw: str, lineno: int):
    """ConfigObj ``_handle_value`` (list_values=True), errors raised."""
    if raw[:3] in ('"""', "'''"):
        raise RNSConfigError(
            f"triple-quoted value at line {lineno} — the fallback reader "
            f"does not implement it (install RNS so its ConfigObj reads it)")
    mat = _VALUEEXP.match(raw)
    if mat is None:
        raise RNSConfigError(f"Parse error in value at line {lineno}")
    list_values, single, empty_list, _comment = mat.groups()
    if list_values == '' and single is None:
        raise RNSConfigError(f"Parse error in value at line {lineno}")
    if empty_list is not None:
        return []
    if single is not None:
        if list_values and not single:
            single = None
        else:
            single = _unquote(single or '""', lineno)
    if list_values == '':
        return single
    the_list = [_unquote(v, lineno) for v in _LISTVALUEEXP.findall(list_values)]
    if single is not None:
        the_list.append(single)
    return the_list


def _match_depth(sect: _Sec, depth: int, lineno: int) -> _Sec:
    while depth < sect.depth:
        if sect is sect.parent:
            break
        sect = sect.parent
    if sect.depth == depth:
        return sect
    raise RNSConfigError(f"Cannot compute nesting level at line {lineno}")


def _decoded_lines(content: Content) -> List[str]:
    """ConfigObj's load, for the strict path: bytes lines, the UTF-8 BOM
    stripped from the first, utf-8 decode, trailing CR/LF stripped."""
    raw = _raw_lines(content)
    if raw and raw[0].startswith(_UTF8_BOM):
        raw[0] = raw[0][len(_UTF8_BOM):]
    try:
        return [line.decode("utf-8").rstrip("\r\n") for line in raw]
    except UnicodeDecodeError as e:
        raise RNSConfigError(f"UnicodeDecodeError: {e}") from e


def _read_strict(content: Content) -> List[Interface]:
    return _shape(_strict_root(content))


def _strict_root(content: Content) -> "_Sec":
    root = _Sec(0, None)
    this = root
    for lineno, line in enumerate(_decoded_lines(content), 1):
        sline = line.strip()
        if not sline or sline.startswith('#'):
            continue
        mat = _SECTIONMARKER.match(line)
        if mat is not None:
            _indent, sect_open, sect_name, sect_close, _c = mat.groups()
            depth = sect_open.count('[')
            if depth != sect_close.count(']'):
                raise RNSConfigError(
                    f"Cannot compute the section depth at line {lineno}")
            if depth < this.depth:
                parent = _match_depth(this, depth, lineno).parent
            elif depth == this.depth:
                parent = this.parent
            elif depth == this.depth + 1:
                parent = this
            else:
                raise RNSConfigError(f"Section too nested at line {lineno}")
            sect_name = _unquote(sect_name, lineno)
            if sect_name in parent:
                raise RNSConfigError(
                    f"Duplicate section name at line {lineno}")
            this = _Sec(depth, parent)
            parent[sect_name] = this
            continue
        mat = _KEYWORD.match(line)
        if mat is None:
            raise RNSConfigError(
                f"Invalid line ({line!r}) (matched as neither section nor "
                f"keyword) at line {lineno}")
        _indent, key, raw = mat.groups()
        value = _handle_value(raw, lineno)
        key = _unquote(key, lineno)
        if key in this:
            raise RNSConfigError(f"Duplicate keyword name at line {lineno}")
        this[key] = value
    return root


# ── the one entry point ─────────────────────────────────────────────────


def read_interfaces(content: Content) -> List[Interface]:
    """The interfaces rnsd would bring up sections for, in file order.

    Raises ``RNSConfigError`` when rnsd could not load the file either."""
    if HAS_CONFIGOBJ:
        return _read_configobj(content)
    return _read_strict(content)


# ── the whole file as rnsd sees it (r13: the write-boundary seam) ───────


def _plain(section) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in section.items():
        if _is_section(value):
            out[key] = _plain(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) for v in value]
        else:
            out[key] = str(value)
    return out


def rnsd_view(content: Content) -> Dict[str, Any]:
    """Every section and value rnsd would load from ``content``, as plain
    nested dicts (values RAW: str, or a list for a list value; no
    interpolation). Raises ``RNSConfigError`` when rnsd could not load it.

    This is the one answer to "what does rnsd read here?" for every
    question that is not the interface list — ``reticulum.rpc_key``,
    ``reticulum.instance_name`` — and the oracle every rpc_key WRITER checks
    its new bytes against before it writes (``rns_config_setup``)."""
    try:
        root = _configobj_root(content) if HAS_CONFIGOBJ else _strict_root(content)
        return _plain(root)
    except RNSConfigError:
        raise
    except Exception as e:
        raise RNSConfigError(f"{type(e).__name__}: {e}") from e


def reticulum_section(view: Dict[str, Any]) -> Dict[str, Any]:
    """``view["reticulum"]``, ``{}`` when absent. A ``reticulum`` that is a
    key rather than a section is a config rnsd cannot use: refused."""
    sect = view.get("reticulum", {})
    if not isinstance(sect, dict):
        raise RNSConfigError("'reticulum' is a key, not a [reticulum] section")
    return sect


def section_header(line: str) -> Optional[Tuple[int, str]]:
    """``(depth, name)`` when ``line`` is a section marker as ConfigObj
    reads one (``[ reticulum ]``, ``["reticulum"]``, ``[reticulum]  # c``),
    else None. For a writer locating WHERE to edit; whether the edit landed
    is ``rnsd_view``'s verdict, never this one's."""
    mat = _SECTIONMARKER.match(line.rstrip("\r"))
    if mat is None:
        return None
    _indent, sect_open, name, sect_close, _c = mat.groups()
    depth = sect_open.count("[")
    if depth != sect_close.count("]"):
        return None
    if len(name) > 1 and name[0] == name[-1] and name[0] in ('"', "'"):
        name = name[1:-1]
    return depth, name


def keyword_name(line: str) -> Optional[str]:
    """The key of a ``key = value`` line as ConfigObj reads it (quotes
    stripped), else None. Comment lines are not keywords."""
    line = line.rstrip("\r")
    if line.strip().startswith("#"):
        return None
    mat = _KEYWORD.match(line)
    if mat is None:
        return None
    key = mat.group(2)
    if len(key) > 1 and key[0] == key[-1] and key[0] in ('"', "'"):
        key = key[1:-1]
    return key


# ── which rpc_key rnsd uses (r13.1: ONE definition for every reader) ────

_RPC_KEY_HEX = re.compile(r"[0-9a-f]{64}")


def rnsd_rpc_key(content: Content) -> Tuple[str, Optional[str]]:
    """``(state, value)`` for the rpc_key rnsd pins from ``content``.

    * ``("valid", key)`` — ``[reticulum] rpc_key`` is ONE string of 64 hex
      digits (returned lowercased). rnsd: ``bytes.fromhex(value)``; 64 hex is
      MeshForge's per-box policy on top.
    * ``("invalid", raw)`` — set, but rnsd cannot use it as a pinned key: not
      64 hex, a LIST (``rpc_key = <hex>,`` — rnsd's fromhex raises and it
      falls back to the derived key), a sub-section, the ``__RPC_KEY__``
      placeholder. ``raw`` is a display string (never treat it as a key).
    * ``("absent", None)`` — no ``[reticulum] rpc_key``. A key anywhere else
      (top level, ``[logging]``) is NOT rnsd's key.

    Raises ``RNSConfigError`` when rnsd could not load ``content``.

    Every reader of "the rpc_key" — the pinner, the alignment scan, the
    client-config resolver ``ReticulumPaths.get_shared_rpc_key``, the doctor,
    the flow audit — answers from HERE (Fable r13 #1/#2: three line scans
    took the first ``rpc_key =`` anywhere, and the pinner joined a list)."""
    value = reticulum_section(rnsd_view(content)).get("rpc_key")
    if value is None:
        return "absent", None
    if isinstance(value, dict):
        return "invalid", "[[rpc_key]] (a section, not a value)"
    if isinstance(value, list):
        return "invalid", ", ".join(value) + ("," if len(value) == 1 else "")
    low = value.lower()
    if _RPC_KEY_HEX.fullmatch(low):
        return "valid", low
    return "invalid", value


def file_rpc_key(path) -> Optional[str]:
    """The valid rpc_key rnsd uses from the file at ``path``, else None (no
    key, an invalid one, unreadable, or a file rnsd cannot load). For
    readers that only compare keys; a caller that must tell those apart
    uses ``rnsd_rpc_key`` on the bytes itself."""
    try:
        state, value = rnsd_rpc_key(path.read_bytes())
    except (OSError, RNSConfigError):
        return None
    return value if state == "valid" else None
