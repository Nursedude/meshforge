"""RNS interface enable/disable WRITERS stay inside their own section.

Non-author review 2026-10-08 (both VERIFIED by running):
C1 — Repair's disable regex was DOTALL + lazy, so for a section using the
     canonical ``interface_enabled = True`` it ran on into the NEXT section and
     set ITS ``enabled = no``: the blocking interface stayed up, a working leg
     (often AutoInterface) went dark, and the name returned said otherwise.
C2 — Enable/Disable Interface rewrote only lines starting ``enabled``: on a
     canonical RNode it changed nothing and still reported success.
RNS brings an interface up when interface_enabled OR enabled is true, so a
disable must turn off EVERY true key in that section.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))

from utils.rns_interface_flags import set_section_enabled  # noqa: E402

CFG = """[reticulum]
  share_instance = Yes

[interfaces]
  [[TCP Client to dead host]]
    type = TCPClientInterface
    interface_enabled = True   # the canonical key
    target_host = 10.9.9.9

  [[Default Interface]]
    type = AutoInterface
    enabled = yes
"""


def _section(text, name):
    start = text.index(f"[[{name}]]")
    nxt = text.find("[[", start + len(name) + 4)
    return text[start: nxt if nxt != -1 else len(text)]


def test_disable_touches_only_its_own_section():
    out, found, changed = set_section_enabled(CFG, "TCP Client to dead host", False)
    assert found and changed
    assert "interface_enabled = no   # the canonical key" in _section(out, "TCP Client to dead host")
    assert "enabled = yes" in _section(out, "Default Interface")      # untouched


def test_disable_turns_off_every_true_key_because_rns_ors_them():
    cfg = CFG.replace("interface_enabled = True   # the canonical key",
                      "interface_enabled = True\n    enabled = on")
    out, found, changed = set_section_enabled(cfg, "TCP Client to dead host", False)
    sec = _section(out, "TCP Client to dead host")
    assert "interface_enabled = no" in sec and "enabled = no" in sec


def test_enable_sets_the_present_keys_or_adds_one():
    off = CFG.replace("interface_enabled = True   # the canonical key", "interface_enabled = no")
    out, found, changed = set_section_enabled(off, "TCP Client to dead host", True)
    assert changed and "interface_enabled = yes" in _section(out, "TCP Client to dead host")
    bare = CFG.replace("    interface_enabled = True   # the canonical key\n", "")
    out, found, changed = set_section_enabled(bare, "TCP Client to dead host", True)
    assert changed and "enabled = yes" in _section(out, "TCP Client to dead host")


def test_already_in_state_is_found_unchanged_and_absent_is_not_found():
    out, found, changed = set_section_enabled(CFG, "Default Interface", True)
    assert found and not changed and out == CFG
    out, found, changed = set_section_enabled(CFG, "Nope", False)
    assert not found and not changed and out == CFG


def test_keys_after_a_subsection_belong_to_the_sub_not_the_parent():
    # ConfigObj: every key after [[[sub]]] is the SUB's (the first cut of this
    # test asserted the opposite and pinned the bug — review 2026-10-08)
    cfg = CFG.replace("    target_host = 10.9.9.9\n",
                      "    target_host = 10.9.9.9\n    [[[sub0]]]\n      port = /dev/x\n      enabled = yes\n")
    out, found, changed = set_section_enabled(cfg, "TCP Client to dead host", False)
    assert "[[[sub0]]]\n      port = /dev/x\n      enabled = yes" in out   # sub untouched
    assert "interface_enabled = no" in out


MULTI = """[interfaces]
  [[RNode Multi]]
    type = RNodeMultiInterface
    interface_enabled = {parent}
    [[[High]]]
      interface_enabled = True
    [[[Low]]]
      interface_enabled = False
  [[Default Interface]]
    enabled = yes
"""


def test_disable_then_enable_never_turns_on_a_sub_the_operator_turned_off():
    off, _, _ = set_section_enabled(MULTI.format(parent="True"), "RNode Multi", False)
    on, _, changed = set_section_enabled(off, "RNode Multi", True)
    assert changed
    assert "[[[High]]]\n      interface_enabled = True" in on
    assert "[[[Low]]]\n      interface_enabled = False" in on
    assert "    interface_enabled = yes\n    [[[High]]]" in on


def test_a_subs_true_key_is_not_the_parent_already_up():
    out, found, changed = set_section_enabled(MULTI.format(parent="False"), "RNode Multi", True)
    assert changed and "    interface_enabled = yes\n    [[[High]]]" in out


def test_key_case_is_exact_and_quoted_keys_count():
    cap = CFG.replace("interface_enabled = True   # the canonical key", "Enabled = no")
    out, found, changed = set_section_enabled(cap, "TCP Client to dead host", True)
    assert changed and "enabled = yes" in out          # a real key added; `Enabled` is not one
    quoted = CFG.replace("interface_enabled = True   # the canonical key", '"enabled" = yes')
    out, found, changed = set_section_enabled(quoted, "TCP Client to dead host", False)
    assert changed and '"enabled" = no' in out
    out, found, changed = set_section_enabled(quoted, "TCP Client to dead host", True)
    assert not changed and out.count("enabled") == quoted.count("enabled")   # no duplicate key


def test_repair_disable_disables_the_named_interface(tmp_path, monkeypatch):
    from handlers import _rns_interface_mgr as m
    f = tmp_path / "config"
    f.write_text(CFG)
    monkeypatch.setattr(m.ReticulumPaths, "get_config_file", classmethod(lambda cls: f))
    assert m.disable_interfaces_in_config(["TCP Client to dead host"]) == ["TCP Client to dead host"]
    out = f.read_text()
    assert "interface_enabled = no" in _section(out, "TCP Client to dead host")
    assert "enabled = yes" in _section(out, "Default Interface")


def test_disable_interface_command_changes_the_file_or_fails(tmp_path, monkeypatch):
    import commands.rns as cr
    store = {"c": CFG}
    monkeypatch.setattr(cr, "read_config", lambda: cr.CommandResult.ok("r", data={"content": store["c"]}))

    def write(c):
        store["c"] = c
        return cr.CommandResult.ok("w")
    monkeypatch.setattr(cr, "write_config", write)
    r = cr.disable_interface("TCP Client to dead host")
    assert r.success and (r.data or {}).get("changed") is True
    assert "interface_enabled = no" in _section(store["c"], "TCP Client to dead host")
    again = cr.disable_interface("TCP Client to dead host")
    assert again.success and again.data.get("changed") is False and "already" in again.message
    assert not cr.disable_interface("Nope").success
