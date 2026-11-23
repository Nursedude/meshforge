"""r18 NIT 3 — "which config does the RUNNING rnsd load" has ONE answer.

``rns_alignment._resolve_rnsd_configdir`` used to answer it alone: a
``--config`` regex, else a hard-coded ``/home/<user>/.reticulum``. RNS's
own order (``Reticulum.__init__``: /etc/reticulum → ~/.config/reticulum →
~/.reticulum, by EXISTENCE) was never consulted, so a unit running
``rnsd --service`` with no ``--config`` on a box carrying BOTH
``/etc/reticulum/config`` and ``~/.reticulum/config`` was read from the
wrong file. That layout is LIVE (meshanchor-server, survey 2026-11-23
08:5x HST); it agreed only because both configs resolve instance_name to
``default``.

Expected values come from RNS's OWN source (the ``Reticulum.__init__``
configdir block, exec'd with only the literal ``/etc/reticulum`` remapped)
where RNS is installed, and from known-good literals everywhere else.
"""
import importlib.util
import os
import textwrap
from pathlib import Path


import pytest

from utils.paths import ReticulumPaths
from utils.rns_alignment import RnsdConfigdirUnknown, _resolve_rnsd_configdir


def _self_ids(name, primary=None):
    """rnsd runs as the test runner (same creds → the kernel answers;
    r13.8 judges as rnsd's ids, and these users are fake)."""
    import os as _os
    return _os.geteuid(), _os.getegid(), tuple(_os.getgroups())


def _rns_reticulum_source():
    spec = importlib.util.find_spec("RNS")
    if spec is None or not spec.origin:
        return None
    src = Path(spec.origin).parent / "Reticulum.py"
    return src if src.is_file() else None


def _rns_pick(fake_etc: Path, home: Path) -> Path:
    """What RNS's own Reticulum.__init__ picks for configdir=None."""
    src = _rns_reticulum_source()
    lines = src.read_text().split("\n")
    i = next(n for n, l in enumerate(lines)
             if l.strip().startswith("if configdir != None: Reticulum.configdir = configdir"))
    j = next(n for n in range(i, i + 15) if 'userdir+"/.reticulum"' in lines[n])
    blk = textwrap.dedent("\n".join(lines[i:j + 1])).replace("/etc/reticulum", str(fake_etc))

    class Reticulum:
        userdir = str(home)
        configdir = None

    exec(blk, {"os": os, "Reticulum": Reticulum, "configdir": None})
    return Path(Reticulum.configdir)


def _layout(tmp_path, etc: bool, xdg: bool, dot: bool):
    fake_etc = tmp_path / "etc" / "reticulum"
    home = tmp_path / "home" / "op"
    home.mkdir(parents=True)
    for present, d in ((etc, fake_etc), (xdg, home / ".config" / "reticulum"),
                       (dot, home / ".reticulum")):
        if present:
            d.mkdir(parents=True)
            (d / "config").write_text("[reticulum]\n")
    return fake_etc, home


LAYOUTS = [(e, x, d) for e in (False, True) for x in (False, True) for d in (False, True)]


@pytest.mark.parametrize("etc,xdg,dot", LAYOUTS)
def test_no_config_flag_follows_rns_own_order(tmp_path, monkeypatch, etc, xdg, dot):
    if _rns_reticulum_source() is None:
        pytest.skip("RNS not installed — literal tests below still pin the order")
    fake_etc, home = _layout(tmp_path, etc, xdg, dot)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                  ids_of=_self_ids, home_of=lambda u: home)
    assert got == _rns_pick(fake_etc, home)


def test_meshanchor_server_shape_reads_etc_not_dot_reticulum(tmp_path, monkeypatch):
    """The live layout: no --config, /etc config AND ~/.reticulum config."""
    fake_etc, home = _layout(tmp_path, etc=True, xdg=False, dot=True)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                  ids_of=_self_ids, home_of=lambda u: home)
    assert got == fake_etc


def test_no_etc_config_uses_the_users_passwd_home(tmp_path, monkeypatch):
    fake_etc, home = _layout(tmp_path, etc=False, xdg=False, dot=False)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd",
                                  ids_of=_self_ids, home_of=lambda u: home)
    assert got == home / ".reticulum"


def test_unset_user_is_root_by_passwd(tmp_path, monkeypatch):
    fake_etc, home = _layout(tmp_path, etc=False, xdg=False, dot=False)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)
    asked = []
    got = _resolve_rnsd_configdir(None, "/usr/local/bin/rnsd",
                                  ids_of=_self_ids, home_of=lambda u: asked.append(u) or home)
    assert asked == ["root"]
    assert got == home / ".reticulum"


# ── --config in ExecStart: rnsd's OWN argparse decides ──────────────────
#
# rnsd.py main(): a plain argparse ``--config`` (so ``--config=X``, unique
# prefixes like ``--conf``, last-one-wins) and ``if args.config:`` (so an
# EMPTY value falls through to the default order). Unknown args make
# parse_args exit → that unit never runs rnsd → UNKNOWN, never a path.
# Rows marked OURS are this resolver's stated limits (systemd specifiers,
# shell wrappers, relative dirs): we cannot judge them, so UNKNOWN.

DEFAULT = object()   # falls through to RNS's default order
UNKNOWN = object()

ARGV_ROWS = [
    ("/usr/local/bin/rnsd --config /etc/reticulum --service", Path("/etc/reticulum")),
    ("/usr/local/bin/rnsd --config=/srv/rns --service", Path("/srv/rns")),
    ("/usr/local/bin/rnsd --conf /srv/rns", Path("/srv/rns")),
    ("/usr/local/bin/rnsd --config /a --config /srv/b", Path("/srv/b")),
    ('/usr/local/bin/rnsd --config "/srv/my rns"', Path("/srv/my rns")),
    ("/usr/local/bin/rnsd -s --config /srv/rns -v", Path("/srv/rns")),
    ("/usr/bin/python3 /usr/local/bin/rnsd --config /srv/rns", Path("/srv/rns")),
    ("/usr/bin/python3 -m RNS.Utilities.rnsd --config /srv/rns", Path("/srv/rns")),
    ("-/usr/local/bin/rnsd --config /srv/rns", Path("/srv/rns")),
    ('/usr/local/bin/rnsd --config ""', DEFAULT),
    ("/usr/local/bin/rnsd --service", DEFAULT),
    ("/usr/local/bin/rnsd --service --config", UNKNOWN),       # rnsd exits 2
    ("/usr/local/bin/rnsd --bogus", UNKNOWN),                  # rnsd exits 2
    ("/usr/local/bin/rnsd --config %h/.reticulum", UNKNOWN),   # OURS
    ("/usr/local/bin/rnsd --config $HOME/rns", UNKNOWN),       # OURS
    ("/usr/local/bin/rnsd --config /home/%u/rns", UNKNOWN),    # OURS (absolute)
    ("/usr/local/bin/rnsd --config /srv/${X}", UNKNOWN),       # OURS (absolute)
    ("-rnsd --config /srv/rns", Path("/srv/rns")),             # bare name, `-` prefix
    ("/usr/local/bin/rnsd --config rel/dir", UNKNOWN),         # OURS
    ('/bin/sh -c "exec rnsd --config /srv/rns"', UNKNOWN),     # OURS
    (None, UNKNOWN),                                           # no unit
]


@pytest.mark.parametrize("exec_start,expected", ARGV_ROWS)
def test_execstart_argv_table(tmp_path, monkeypatch, exec_start, expected):
    fake_etc, home = _layout(tmp_path, etc=False, xdg=False, dot=True)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)

    def call():
        return _resolve_rnsd_configdir("op", exec_start, ids_of=_self_ids, home_of=lambda u: home)

    if expected is UNKNOWN:
        with pytest.raises(RnsdConfigdirUnknown):
            call()
    elif expected is DEFAULT:
        assert call() == home / ".reticulum"
    else:
        assert call() == expected


def _rnsd_source():
    spec = importlib.util.find_spec("RNS")
    if spec is None or not spec.origin:
        return None
    src = Path(spec.origin).parent / "Utilities" / "rnsd.py"
    return src if src.is_file() else None


def _rnsd_own_config_arg(args):
    """Run rnsd.py main()'s parser block verbatim on ``args``.
    Returns args.config-as-rnsd-uses-it (None = default order), or raises
    SystemExit where rnsd itself would exit."""
    import argparse
    lines = _rnsd_source().read_text().split("\n")
    i = next(n for n, l in enumerate(lines) if "argparse.ArgumentParser(" in l)
    j = next(n for n in range(i, i + 20) if "parser.parse_args()" in lines[n])
    blk = textwrap.dedent("\n".join(lines[i:j + 1])).replace(
        "parser.parse_args()", "parser.parse_args(_ARGV)")
    ns = {"argparse": argparse, "__version__": "x", "_ARGV": args}
    exec(blk, ns)
    return ns["args"].config or None


@pytest.mark.parametrize("exec_start,expected",
                         [r for r in ARGV_ROWS
                          if r[0] and "OURS" not in r[0] and "rnsd " in r[0]
                          and "%" not in r[0] and "$" not in r[0]
                          and "rel/dir" not in r[0] and "/bin/sh" not in r[0]])
def test_argv_table_agrees_with_rnsds_own_parser(exec_start, expected):
    """The table above is not my reading of rnsd — rnsd's parser says so."""
    if _rnsd_source() is None:
        pytest.skip("RNS not installed — literal table still pins the cases")
    import shlex
    argv = shlex.split(exec_start)
    args = argv[argv.index(next(t for t in argv if t.endswith("rnsd"))) + 1:]
    if expected is UNKNOWN:
        with pytest.raises(SystemExit):
            _rnsd_own_config_arg(args)
    elif expected is DEFAULT:
        assert _rnsd_own_config_arg(args) is None
    else:
        assert Path(_rnsd_own_config_arg(args)) == expected


def test_user_without_passwd_entry_is_unknown(tmp_path, monkeypatch):
    fake_etc, _ = _layout(tmp_path, etc=False, xdg=False, dot=False)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)

    def no_such_user(u):
        raise KeyError(u)

    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir("ghost", "/usr/local/bin/rnsd", ids_of=_self_ids, home_of=no_such_user)


# ── the consumer END: the @rns/<name> the watchdog probes ──────────────


def _box(tmp_path, monkeypatch, *, exec_start, etc_text, dot_text):
    """A box: one rnsd unit, a fake /etc/reticulum, the real user's passwd
    entry (so the resolver's passwd lookup is the real one) but a fake home
    for every other reader."""
    import pwd
    import utils.paths as paths_mod
    import utils.rns_alignment as ra
    me = pwd.getpwuid(os.getuid()).pw_name
    unit = tmp_path / "rnsd.service"
    unit.write_text(f"[Service]\nUser={me}\nExecStart={exec_start}\n")
    monkeypatch.setattr(ra, "rnsd_unit_paths", lambda: [str(unit)])
    fake_etc = tmp_path / "etc" / "reticulum"
    fake_etc.mkdir(parents=True)
    if etc_text is not None:
        (fake_etc / "config").write_text(etc_text)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", fake_etc)
    monkeypatch.setattr(ra, "CANONICAL_CONFIGDIR", fake_etc)
    home = tmp_path / "home"
    (home / ".reticulum").mkdir(parents=True)
    if dot_text is not None:
        (home / ".reticulum" / "config").write_text(dot_text)
    monkeypatch.setattr(paths_mod, "get_real_user_home", lambda: home)
    return ra, fake_etc


def test_meshanchor_server_shape_watchdog_probes_rnsds_name(tmp_path, monkeypatch):
    """Live shape (meshanchor-server, 2026-11-23): ``rnsd --service``, /etc
    config with NO instance_name (RNS → ``default``), a stale ~/.reticulum
    naming something else. The watchdog must probe what rnsd serves."""
    ra, fake_etc = _box(
        tmp_path, monkeypatch, exec_start="/usr/local/bin/rnsd --service",
        etc_text="[reticulum]\n  share_instance = Yes\n",
        dot_text="[reticulum]\n  instance_name = stale-box\n")
    assert ra.rnsd_configdir_of_record() == fake_etc
    assert ra.read_rns_instance_name() == "default"


def test_rnsds_named_config_wins_over_a_stale_home_config(tmp_path, monkeypatch):
    ra, _ = _box(
        tmp_path, monkeypatch, exec_start="/usr/local/bin/rnsd --service",
        etc_text="[reticulum]\n  instance_name = real name\n",
        dot_text="[reticulum]\n  instance_name = stale-box\n")
    assert ra.read_rns_instance_name() == "real name"


def test_unjudgeable_unit_is_unknown_not_a_guessed_dir(tmp_path, monkeypatch):
    ra, _ = _box(
        tmp_path, monkeypatch,
        exec_start='/bin/sh -c "exec rnsd --config /srv/rns"',
        etc_text=None, dot_text=None)
    assert ra.rnsd_configdir_of_record() is None


# ── UNKNOWN reaches the alignment verdict as UNKNOWN ───────────────────


def _unknown_state():
    from utils.rns_alignment import RNSAlignmentState, ConfigFileFacts, CANONICAL_CONFIGDIR
    return RNSAlignmentState(
        hostname="t", rnsd_active=True, rnsd_user="op",
        rnsd_exec_start='/bin/sh -c "exec rnsd"',
        rnsd_configdir=None,
        rnsd_configdir_unknown="rnsd ExecStart= does not start rnsd in a shape we can judge: /bin/sh",
        etc_config=ConfigFileFacts(path=Path("/etc/reticulum/config"), exists=True,
                                   owner="root:root", has_rpc_key=True),
        nomadnet_unit_installed=True, nomadnet_unit_rnsconfig=CANONICAL_CONFIGDIR,
    )


def test_unknown_configdir_is_worded_unknown_not_rnsd_uses_none():
    from utils.rns_alignment import analyze_drift
    reasons = analyze_drift(_unknown_state())
    assert not any("rnsd uses" in r for r in reasons)
    assert any("UNKNOWN" in r and "/bin/sh" in r for r in reasons)


def test_unknown_configdir_plans_no_dropin_write():
    from utils.rns_alignment import plan_normalize
    s = _unknown_state()
    actions = plan_normalize(s)
    assert not any("drop-in" in a.description for a in actions)


def test_probe_local_on_a_box_with_no_rnsd_unit_does_not_raise(monkeypatch):
    import utils.rns_alignment as ra
    monkeypatch.setattr(ra, "rnsd_unit_paths", lambda: ["/nonexistent/rnsd.service"])
    st = ra.probe_local()
    assert st.rnsd_configdir is None
    assert st.rnsd_configdir_unknown
