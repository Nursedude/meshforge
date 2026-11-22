"""The RNS config is read the way rnsd reads it (WITNESS-TUI r9 fix 1).

``commands.rns._parse_interfaces`` took ANY ``[[…]]`` line as an interface:
an RNodeMulti sub-interface ``[[[High Datarate]]]`` became a config row named
"[High Datarate]" with type "?", ``[[ LoRa ]]`` kept its spaces, ``[["Q"]]``
its quotes, ``type = X # c`` its comment, and ``[[Cmt]] # note`` was not a
header at all, so its ``type`` overwrote the PREVIOUS section's. rnsd reads
the file with ConfigObj; so does this reader. Where RNS is not importable
(the CI minimal profile) a strict fallback built from ConfigObj's own
grammar takes over.

Every case below states its expected output LITERALLY, so both paths are held
to the same oracle: the ConfigObj path proves the oracle is what rnsd sees
(it runs wherever RNS is installed), the strict path proves the fallback
agrees with it (it runs everywhere).
"""

import pytest

from utils import rns_config_reader as rcr

PATHS = [
    pytest.param("configobj", marks=pytest.mark.skipif(
        not rcr.HAS_CONFIGOBJ, reason="RNS (ConfigObj) not importable")),
    "strict",
]


def _read(path, content):
    if path == "configobj":
        return rcr._read_configobj(content)
    return rcr._read_strict(content)


def _iface(name, subs=(), **settings):
    return {"name": name, "settings": dict(settings),
            "subinterfaces": [dict(s) for s in subs]}


def _sub(name, **settings):
    return {"name": name, "settings": dict(settings)}


CORPUS = {
    "basic": ("""[reticulum]
  share_instance = Yes
[interfaces]
  [[Server]]
    type = TCPServerInterface
    enabled = yes
    listen_port = 4242
  [[Client]]
    type = TCPClientInterface
    enabled = no
""", [
        _iface("Server", type="TCPServerInterface", enabled="yes",
               listen_port="4242"),
        _iface("Client", type="TCPClientInterface", enabled="no"),
    ]),
    "rnode_multi_subs_are_not_interfaces": ("""[interfaces]
  [[RNode-Multi]]
    type = RNodeMultiInterface
    interface_enabled = True
    port = /dev/ttyACM0
    [[[High Datarate]]]
      interface_enabled = True
      vport = 1
    [[[Low Datarate]]]
      interface_enabled = True
      vport = 0
  [[Default Interface]]
    type = AutoInterface
    enabled = yes
""", [
        _iface("RNode-Multi", subs=[
            _sub("High Datarate", interface_enabled="True", vport="1"),
            _sub("Low Datarate", interface_enabled="True", vport="0"),
        ], type="RNodeMultiInterface", interface_enabled="True",
            port="/dev/ttyACM0"),
        _iface("Default Interface", type="AutoInterface", enabled="yes"),
    ]),
    # ConfigObj ignores indentation: a key after a sub-section belongs to
    # the SUB-section, however it is indented.
    "key_after_subsection_belongs_to_it": ("""[interfaces]
  [[M]]
    type = RNodeMultiInterface
    [[[A]]]
      vport = 1
    port = /dev/ttyACM0
""", [
        _iface("M", subs=[_sub("A", vport="1", port="/dev/ttyACM0")],
               type="RNodeMultiInterface"),
    ]),
    "names_stripped_and_unquoted": ("""[interfaces]
  [[ LoRa ]]
    type = SerialInterface
  [["Quoted Name"]]
    type = AutoInterface
  [['single']]
    type = AutoInterface
  [ [ Spaced ] ]
    type = AutoInterface
""", [
        _iface("LoRa", type="SerialInterface"),
        _iface("Quoted Name", type="AutoInterface"),
        _iface("single", type="AutoInterface"),
        _iface("Spaced", type="AutoInterface"),
    ]),
    "inline_comments_stripped_header_with_comment_is_a_header": ("""[interfaces]
  [[First]]
    type = SerialInterface # the usb one
  [[Cmt]] # note
    type = AutoInterface
""", [
        _iface("First", type="SerialInterface"),
        _iface("Cmt", type="AutoInterface"),
    ]),
    "values": ("""[interfaces]
  [[V]]
    type = "TCPClientInterface"
    passphrase = a=b
    quoted_hash = 'x # y'
    peers = a, b , c
    trailing = a,
    empty_list = ,
    blank =
""", [
        _iface("V", type="TCPClientInterface", passphrase="a=b",
               quoted_hash="x # y", peers="a, b, c", trailing="a",
               empty_list="", blank=""),
    ]),
    "comments_blanks_and_tabs": ("""# [[Commented Out]]
[interfaces]

\t[[Tabbed]]
\t\t# type = SerialInterface
\t\ttype = AutoInterface
""", [
        _iface("Tabbed", type="AutoInterface"),
    ]),
    "double_bracket_outside_interfaces_is_not_an_interface": ("""[logging]
  loglevel = 4
  [[stray]]
    x = 1
[interfaces]
  [[Real]]
    type = AutoInterface
[after]
  [[also stray]]
    y = 2
""", [
        _iface("Real", type="AutoInterface"),
    ]),
    "no_interfaces_section": ("""[reticulum]
  share_instance = Yes
""", []),
    "section_name_is_case_sensitive_like_rnsd": ("""[Interfaces]
  [[Nope]]
    type = AutoInterface
""", []),
    "empty_interface_and_unicode": ("""[interfaces]
  [[Empty]]
  [[Kīlauea]]
    type = AutoInterface
""", [
        _iface("Empty"),
        _iface("Kīlauea", type="AutoInterface"),
    ]),
    "crlf": ("[interfaces]\r\n  [[W]]\r\n    type = AutoInterface\r\n",
             [_iface("W", type="AutoInterface")]),
}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", sorted(CORPUS))
def test_corpus(path, case):
    content, expected = CORPUS[case]
    assert _read(path, content) == expected


# Configs rnsd itself cannot load. Both paths refuse loudly — a list of
# guessed rows from a file rnsd rejects is the defect this replaces.
BROKEN = {
    "duplicate_key": "[interfaces]\n  [[A]]\n    type = x\n    type = y\n",
    "duplicate_section": "[interfaces]\n  [[A]]\n  [[A]]\n",
    "unbalanced_brackets": "[interfaces]\n  [[A]\n",
    "too_many_close": "[interfaces]\n  [[A]]]\n",
    "junk_line": "[interfaces]\n  [[A]]\n    junk line\n",
    "trailing_text_after_header": "[interfaces]\n  [[A]] trailing\n",
    "orphan_double_bracket": "[[Orphan]]\n  type = AutoInterface\n",
    "depth_jump": "[interfaces]\n  [[[TooDeep]]]\n",
    "unclosed_quote": "[interfaces]\n  [[A]]\n    type = \"Auto\n",
    "bare_key_under_interfaces": "[interfaces]\n  foo = bar\n",
    "interfaces_is_a_key": "interfaces = x\n",
}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", sorted(BROKEN))
def test_broken_config_refused(path, case):
    with pytest.raises(rcr.RNSConfigError):
        _read(path, BROKEN[case])


def test_strict_refuses_what_it_cannot_read_rather_than_guess():
    """Triple-quoted values are legal ConfigObj; the fallback does not
    implement them, so it says so instead of mis-reading them."""
    content = "[interfaces]\n  [[A]]\n    note = '''x'''\n"
    with pytest.raises(rcr.RNSConfigError, match="triple"):
        rcr._read_strict(content)


def test_read_interfaces_dispatches(monkeypatch):
    content, expected = CORPUS["basic"]
    monkeypatch.setattr(rcr, "HAS_CONFIGOBJ", False)
    assert rcr.read_interfaces(content) == expected


def test_strict_is_the_dispatch_without_rns(monkeypatch):
    """Without ConfigObj the strict path answers, never the old any-[[ reader."""
    content, expected = CORPUS["rnode_multi_subs_are_not_interfaces"]
    monkeypatch.setattr(rcr, "HAS_CONFIGOBJ", False)
    assert [i["name"] for i in rcr.read_interfaces(content)] == [
        "RNode-Multi", "Default Interface"]


# ── consumers ───────────────────────────────────────────────────────────


def test_parse_interfaces_uses_the_reader():
    from commands import rns
    content, expected = CORPUS["rnode_multi_subs_are_not_interfaces"]
    assert rns._parse_interfaces(content) == expected


def _cfg(tmp_path, monkeypatch, text):
    from commands import rns
    p = tmp_path / "config"
    p.write_text(text)
    monkeypatch.setattr(rns, "get_config_path", lambda: p)
    return rns, p


def test_read_config_fails_loud_on_unparseable(tmp_path, monkeypatch):
    rns, _ = _cfg(tmp_path, monkeypatch, BROKEN["duplicate_section"])
    res = rns.read_config()
    assert not res.success
    assert "Duplicate" in res.message
    # The text is still handed back so an editor can show and fix it.
    assert res.data["content"] == BROKEN["duplicate_section"]
    assert res.data["exists"] is True


def test_add_interface_never_overwrites_an_unparseable_config(
        tmp_path, monkeypatch):
    """add_interface used to create a DEFAULT config whenever read_config
    failed — fine for a missing file, destructive for one that exists."""
    rns, p = _cfg(tmp_path, monkeypatch, BROKEN["duplicate_section"])
    wrote = []
    monkeypatch.setattr(rns, "write_config",
                        lambda *a, **k: wrote.append(a) or rns.CommandResult.ok("w"))
    res = rns.add_interface("New", "AutoInterface", {})
    assert not res.success
    assert wrote == []
    assert p.read_text() == BROKEN["duplicate_section"]


def test_get_status_interface_count_unknown_not_zero(tmp_path, monkeypatch):
    from types import SimpleNamespace
    rns, _ = _cfg(tmp_path, monkeypatch, BROKEN["junk_line"])
    monkeypatch.setattr(rns, "get_identity_path", lambda: tmp_path / "none")
    monkeypatch.setattr(rns, "check_service", lambda _n: SimpleNamespace(
        available=False, state="inactive", message="stub"))
    status = rns.get_status().data
    assert status["interface_count"] is None
    assert "config_error" in status


def test_blocking_iter_names_match_config_rows():
    """blocking_map is keyed by name: the blocking parse and the config
    rows must name an interface identically (one reader)."""
    from launcher_tui.handlers._rns_interface_mgr import iter_enabled_interfaces
    from commands import rns
    content = """[interfaces]
  [["Quoted"]]
    type = TCPClientInterface # remote hub
    enabled = yes
    target_host = 192.0.2.1
  [[RNode-Multi]]
    type = RNodeMultiInterface
    interface_enabled = True
    [[[High Datarate]]]
      interface_enabled = True
  [[Off]]
    type = AutoInterface
    enabled = no
"""
    got = list(iter_enabled_interfaces(content))
    assert [(n, t) for n, t, _b in got] == [
        ("Quoted", "TCPClientInterface"), ("RNode-Multi", "RNodeMultiInterface")]
    names = {i["name"] for i in rns._parse_interfaces(content)}
    assert {n for n, _t, _b in got} <= names
    assert "target_host = 192.0.2.1\n" in got[0][2]


def test_blocking_iter_yields_nothing_on_unloadable():
    from launcher_tui.handlers._rns_interface_mgr import iter_enabled_interfaces
    assert list(iter_enabled_interfaces(BROKEN["duplicate_section"])) == []


def test_config_doctor_fails_unloadable_config(tmp_path, monkeypatch):
    from launcher_tui.handlers import _config_doctor_checks as cd
    p = tmp_path / "config"
    p.write_text(BROKEN["junk_line"])
    from utils.paths import ReticulumPaths
    monkeypatch.setattr(ReticulumPaths, "get_config_file",
                        staticmethod(lambda: p))
    res = cd.check_rns_interface_devices()
    assert res.status == cd.FAIL
    assert "not loadable" in res.message


def test_config_drift_counts_interfaces_like_rnsd():
    from utils.config_drift import _parse_reticulum_section
    content, _ = CORPUS["rnode_multi_subs_are_not_interfaces"]
    assert _parse_reticulum_section(content)["interface_count"] == 2
    assert _parse_reticulum_section(
        BROKEN["junk_line"])["interface_count"] is None


# ── r10 (Fable r9 #2, #3): bytes the way rnsd reads them ───────────────
# rnsd hands ConfigObj the PATH: bytes, readlines() (splits on \n only),
# BOM stripped by ConfigObj, utf-8. The oracle below was MEASURED on
# RNS 1.3.9's ConfigObj(path) (2026-11-22), not imagined.

BYTES_CASES = {
    "bom": (b"\xef\xbb\xbf[interfaces]\n  [[LoRa]]\n    type = X\n",
            [_iface("LoRa", type="X")]),
    "formfeed_is_not_a_newline": (
        b"[interfaces]\n  [[A]]\n  # c\x0c[[Ghost]]\n    type = X\n",
        [_iface("A", type="X")]),
    "u2028_is_not_a_newline": (
        "[interfaces]\n  [[A]]\n  # c [[Ghost]]\n    type = X\n".encode(),
        [_iface("A", type="X")]),
    "lone_cr_is_one_line": (b"[interfaces]\r  [[A]]\r    type = X\r", []),
    "crlf": (b"[interfaces]\r\n  [[A]]\r\n    type = X\r\n",
             [_iface("A", type="X")]),
    "dollar_is_literal": (b"[interfaces]\n  [[A]]\n    p = $x\n",
                          [_iface("A", p="$x")]),
}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", sorted(BYTES_CASES))
def test_bytes_corpus(path, case):
    raw, expected = BYTES_CASES[case]
    assert _read(path, raw) == expected


@pytest.mark.parametrize("path", PATHS)
def test_str_with_bom_reads_like_bytes(path):
    raw, expected = BYTES_CASES["bom"]
    assert _read(path, raw.decode("utf-8")) == expected


@pytest.mark.skipif(not rcr.HAS_CONFIGOBJ, reason="RNS (ConfigObj) not importable")
@pytest.mark.parametrize("case", sorted(BYTES_CASES))
def test_bytes_oracle_is_configobj_from_a_path(tmp_path, case):
    """The oracle above IS what rnsd's call shape — ConfigObj(path) —
    yields: re-measured every run where RNS is installed."""
    raw, expected = BYTES_CASES[case]
    p = tmp_path / "config"
    p.write_bytes(raw)
    root = rcr._ConfigObj(str(p))
    got = rcr._shape(root) if "interfaces" in root else []
    assert got == expected


INTERP = {
    "resolvable": b"[interfaces]\n  [[A]]\n    host = h\n    t = %(host)s\n",
    "missing": b"[interfaces]\n  [[A]]\n    p = 100%(sure)s\n",
    "in_list": b"[interfaces]\n  [[A]]\n    p = a, %(b)s\n",
}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", sorted(INTERP))
def test_interpolation_refused_by_both_paths(path, case):
    """rnsd interpolates %(name)s; MeshForge does not evaluate it, so BOTH
    paths refuse with a named reason — never a raw-vs-interpolated split,
    never an untyped exception through the consumers."""
    with pytest.raises(rcr.RNSConfigError, match="interpolation"):
        _read(path, INTERP[case])


@pytest.mark.parametrize("path", PATHS)
def test_not_utf8_refused(path):
    with pytest.raises(rcr.RNSConfigError):
        _read(path, b"[interfaces]\n  [[K\xeelauea]]\n    type = X\n")


def test_read_config_reads_bytes_so_a_bom_config_loads(tmp_path, monkeypatch):
    from commands import rns
    p = tmp_path / "config"
    p.write_bytes(BYTES_CASES["bom"][0])
    monkeypatch.setattr(rns, "get_config_path", lambda: p)
    res = rns.read_config()
    assert res.success and [i["name"] for i in res.data["interfaces"]] == ["LoRa"]


def test_get_status_config_error_not_doubled(tmp_path, monkeypatch):
    from types import SimpleNamespace
    rns, _ = _cfg(tmp_path, monkeypatch, BROKEN["junk_line"])
    monkeypatch.setattr(rns, "get_identity_path", lambda: tmp_path / "none")
    monkeypatch.setattr(rns, "check_service", lambda _n: SimpleNamespace(
        available=False, state="inactive", message="stub"))
    err = rns.get_status().data["config_error"]
    assert "not loadable" not in err and "Invalid line" in err


# ── r10 (Fable r9 #4): [5/6] never "all met" over an unloadable config ──

def test_dependency_leg_unknown_when_config_not_loadable():
    from launcher_tui.handlers._rns_diagnostics_engine import dependency_leg
    called = []
    lines, issues, unobs = dependency_leg(lambda: called.append(1) or [], "Invalid line x")
    assert called == []
    assert lines == ["  UNKNOWN — not checked: RNS config not loadable (Invalid line x)"]
    assert issues == [] and unobs == ["interface dependencies (RNS config not loadable)"]
    assert not any("dependencies met" in ln for ln in lines)


def test_dependency_leg_paths():
    from launcher_tui.handlers._rns_diagnostics_engine import dependency_leg
    assert dependency_leg(lambda: [], None) == (
        ["  All enabled interfaces have their dependencies met"], [], [])
    lines, issues, unobs = dependency_leg(lambda: [("LoRa", "no /dev/x", "plug it")], None)
    assert lines == ["  ! [LoRa] no /dev/x", "    Fix: plug it"]
    assert issues == ["Blocking interface: LoRa"] and unobs == []

    def boom():
        raise OSError("nope")
    lines, issues, unobs = dependency_leg(boom, None)
    assert lines == ["  Could not check: nope"] and unobs == ["interface dependencies (OSError)"]


def test_diagnostics_end_to_end_unloadable_config_never_says_all_met(tmp_path):
    """Fable r9 diag.py shape: the whole diagnostics run, every external
    call mocked, over an unloadable config with a missing serial device."""
    import contextlib
    import io
    import logging
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    from utils.paths import ReticulumPaths
    from launcher_tui.handlers import _rns_diagnostics_engine as E
    from launcher_tui.handlers._rns_interface_mgr import find_blocking_interfaces
    cfg = tmp_path / "config"
    cfg.write_bytes(b"[reticulum]\nshare_instance = yes\n[interfaces]\n[[LoRa]]\n"
                    b"type = RNodeInterface\nenabled = yes\nport = /dev/nonexistent_r10\n"
                    b"garbage {{{\n")
    h = MagicMock()
    h._find_blocking_interfaces = lambda: find_blocking_interfaces()
    h._wait_for_rns_shared_instance = lambda max_wait=10: False
    run = MagicMock(return_value=SimpleNamespace(returncode=1, stdout="", stderr=""))
    buf = io.StringIO()
    logging.disable(logging.CRITICAL)
    try:
        with patch.object(ReticulumPaths, "get_config_file", return_value=cfg), \
             patch.object(ReticulumPaths, "get_config_dir", return_value=tmp_path), \
             patch("commands.rns.get_config_path", return_value=cfg), \
             patch("subprocess.run", run), \
             patch("subprocess.Popen", MagicMock(side_effect=OSError("no"))), \
             patch("socket.socket", MagicMock(side_effect=OSError("no sockets"))), \
             patch("builtins.input", return_value=""), \
             contextlib.redirect_stdout(buf):
            try:
                E.run_rns_diagnostics(h)
            except SystemExit:
                pass
    finally:
        logging.disable(logging.NOTSET)
    out = buf.getvalue()
    assert "[5/6]" in out
    assert "dependencies met" not in out
    assert "UNKNOWN — not checked: RNS config not loadable" in out
    assert "not loadable: RNS config is not loadable" not in out    # not doubled
