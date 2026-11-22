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
