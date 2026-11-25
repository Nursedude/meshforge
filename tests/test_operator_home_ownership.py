"""Nothing MeshForge creates under the operator's home stays root's — the whole chain.

Gate-5 stranger drill 45652768967 (11-25, bookworm + trixie, `94ee48f2`): a
fresh account ``ham`` installed from the README, then MeshForge logged
``Failed to initialize diagnostic history DB: [Errno 13] Permission denied:
'/home/ham/.config/meshforge'``. A fresh account has NO ``~/.config``; root
made it on the way to a deeper path and handed back only the LEAF:

  * ``install_noc.sh``: ``mkdir -p ~/.config/systemd/user`` as root, then
    ``chown -R`` of ``systemd/user`` only (two sites: user units, mini units);
  * ``utils.paths``: ``ensure_user_dirs`` / ``chown_to_operator`` chown the
    paths they are handed, never the root-made ``~/.config`` / ``~/.local``
    above them.

The fleet never showed it: every box's account had a ``~/.config`` before
MeshForge arrived. One rule now, both halves: from the path up to — NOT
including — the operator's home, every root-owned real directory is handed
to the operator. The home itself, anything outside it, and symlinks are
never touched.
"""
import os
import stat as stat_mod
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils import paths

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "install_common.sh"
INSTALLER = REPO / "scripts" / "install_noc.sh"

HOME = Path("/home/ham")

#: the REAL function, captured at import (collection) — the autouse node-cache
#: isolation fixture (conftest) replaces ``chown_to_operator`` with a mock in
#: every test, so a call through ``paths.`` would test the mock.
_REAL_CHOWN_TO_OPERATOR = paths.chown_to_operator


def _fake_lstat(root_owned, symlinks=()):
    """An lstat that reports uid 0 for ``root_owned`` and 1000 otherwise."""
    root_owned = {str(p) for p in root_owned}
    symlinks = {str(p) for p in symlinks}

    def lstat(p):
        p = str(p)
        mode = (stat_mod.S_IFLNK | 0o777) if p in symlinks else (stat_mod.S_IFDIR | 0o755)
        return SimpleNamespace(st_uid=0 if p in root_owned else 1000, st_mode=mode)
    return lstat


# ── the pure rule ────────────────────────────────────────────────────────

def test_the_chain_is_every_root_owned_dir_up_to_the_home():
    target = HOME / ".config" / "meshforge" / "deployment.json"
    lstat = _fake_lstat([HOME, HOME / ".config", HOME / ".config" / "meshforge", target])
    got = paths.root_owned_chain(target, HOME, lstat=lstat)
    assert got == [target, HOME / ".config" / "meshforge", HOME / ".config"]


def test_the_home_itself_is_never_in_the_chain():
    lstat = _fake_lstat([HOME, HOME / ".config"])
    assert HOME not in paths.root_owned_chain(HOME / ".config", HOME, lstat=lstat)
    assert paths.root_owned_chain(HOME, HOME, lstat=lstat) == []


def test_a_path_outside_the_home_is_never_touched():
    # every candidate IS root-owned in the fake, so only the guard can say []
    # (Fable r2: the old fake made these pass with the guard removed)
    ham2 = Path("/home/hamster")
    lstat = _fake_lstat([Path("/etc/x"), Path("/etc"), ham2, ham2 / ".config", Path("/home")])
    assert paths.root_owned_chain(Path("/etc/x"), HOME, lstat=lstat) == []
    # a sibling whose name merely STARTS with the home's
    assert paths.root_owned_chain(ham2 / ".config", HOME, lstat=lstat) == []


def test_a_dotdot_path_is_refused():
    dotted = HOME / ".." / ".." / "etc"
    lstat = _fake_lstat([Path("/etc"), HOME / "..", HOME / ".." / "..", dotted])
    assert paths.root_owned_chain(dotted, HOME, lstat=lstat) == []


@pytest.mark.parametrize("bad_home", ["/", "home/ham"])
def test_a_root_or_relative_home_yields_no_chain(bad_home):
    """Fable r2 (security): pw_dir "/" made every path 'under' the home — the
    walk would hand /etc to a service account. Bash refuses it too (rc 2)."""
    target = Path("/etc/meshforge/x.conf")
    lstat = _fake_lstat([target, target.parent, Path("/etc")])
    assert paths.root_owned_chain(target, bad_home, lstat=lstat) == []


def test_a_symlinked_component_ends_the_walk():
    """r1: below a link every lstat/chown resolves THROUGH it — possibly out of
    the home (~/.config -> /srv/shared). Nothing at or below a link is chowned;
    a root-owned dir ABOVE it still is."""
    cfg = HOME / ".config"
    lstat = _fake_lstat([cfg / "meshforge", cfg], symlinks=[cfg])
    assert paths.root_owned_chain(cfg / "meshforge", HOME, lstat=lstat) == []
    deep = HOME / ".local" / "share" / "meshforge"
    lstat = _fake_lstat([HOME / ".local", deep], symlinks=[deep.parent])
    assert paths.root_owned_chain(deep, HOME, lstat=lstat) == [HOME / ".local"]


def test_a_relative_path_is_judged_where_it_really_is(monkeypatch):
    """r1 nit: a relative path is made absolute (cwd), never silently skipped."""
    monkeypatch.chdir("/")
    lstat = _fake_lstat([HOME / ".config"])
    assert paths.root_owned_chain(Path("home/ham/.config"), HOME, lstat=lstat) == [HOME / ".config"]


def test_a_missing_component_is_skipped_not_raised():
    def lstat(p):
        if str(p).endswith("absent"):
            raise FileNotFoundError(p)
        return SimpleNamespace(st_uid=0, st_mode=stat_mod.S_IFDIR | 0o755)
    got = paths.root_owned_chain(HOME / ".config" / "absent", HOME, lstat=lstat)
    assert got == [HOME / ".config"]


# ── the consumer: chown_to_operator hands back the chain ──────────────────

def test_chown_to_operator_hands_back_the_root_made_config_above_a_file(monkeypatch):
    target = HOME / ".config" / "meshforge" / "deployment.json"
    lstat = _fake_lstat([HOME / ".config", HOME / ".config" / "meshforge", target])
    monkeypatch.setenv("SUDO_USER", "ham")
    pw = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir=str(HOME))
    chowned = []
    # the path ITSELF keeps the old rule (exists + stat uid 0 -> chown)
    with patch("pwd.getpwnam", return_value=pw), \
         patch.object(paths.os, "lstat", side_effect=lstat), \
         patch.object(paths.Path, "exists", lambda self: True), \
         patch.object(paths.Path, "stat", lambda self: lstat(self)), \
         patch.object(paths.os, "chown", side_effect=lambda p, u, g, **kw: chowned.append(p)):
        _REAL_CHOWN_TO_OPERATOR(target)
    assert set(chowned) == {str(target), str(HOME / ".config" / "meshforge"),
                            str(HOME / ".config")}


@pytest.mark.parametrize("pw_dir", [str(HOME), "/"])
def test_a_root_owned_file_outside_the_home_is_still_handed_back_alone(monkeypatch, pw_dir):
    """r1 regression pinned (Fable r2): callers write /etc files through
    chown_to_operator and always got the FILE handed back — keep exactly that,
    and never its /etc parents (nor with a "/" home)."""
    target = Path("/etc/meshforge/x.conf")
    lstat = _fake_lstat([target, target.parent, Path("/etc")])
    monkeypatch.setenv("SUDO_USER", "ham")
    pw = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir=pw_dir)
    chowned = []
    with patch("pwd.getpwnam", return_value=pw), \
         patch.object(paths.os, "lstat", side_effect=lstat), \
         patch.object(paths.Path, "exists", lambda self: True), \
         patch.object(paths.Path, "stat", lambda self: lstat(self)), \
         patch.object(paths.os, "chown", side_effect=lambda p, u, g, **kw: chowned.append(p)):
        _REAL_CHOWN_TO_OPERATOR(target)
    assert chowned == [str(target)]


def test_a_file_the_operator_already_owns_is_left_alone(monkeypatch):
    """Fable r3: the path-itself rule's uid-0 check was unpinned — dropping it
    (chown whatever it is handed) passed every test."""
    target = HOME / ".config" / "meshforge" / "deployment.json"
    lstat = _fake_lstat([])                         # nothing is root's
    monkeypatch.setenv("SUDO_USER", "ham")
    pw = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir=str(HOME))
    with patch("pwd.getpwnam", return_value=pw), \
         patch.object(paths.os, "lstat", side_effect=lstat), \
         patch.object(paths.Path, "exists", lambda self: True), \
         patch.object(paths.Path, "stat", lambda self: lstat(self)), \
         patch.object(paths.os, "chown") as chown:
        _REAL_CHOWN_TO_OPERATOR(target)
    chown.assert_not_called()


def test_chown_to_operator_without_sudo_does_nothing(monkeypatch):
    monkeypatch.delenv("SUDO_USER", raising=False)
    with patch.object(paths.os, "chown") as chown:
        _REAL_CHOWN_TO_OPERATOR(HOME / ".config")
    chown.assert_not_called()


def test_ensure_user_dirs_hands_back_dot_local_and_dot_config(monkeypatch, tmp_path):
    """ensure_user_dirs makes ~/.config/meshforge AND ~/.local/share/... with
    parents=True — on a fresh account ``~/.config``, ``~/.local`` and
    ``~/.local/share`` are root's. Driven through the REAL chown_to_operator
    with every dir reporting uid 0 (r1 nit: a mocked helper pinned nothing)."""
    home = tmp_path / "home" / "ham"
    home.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_real_user_home", lambda: home)
    monkeypatch.setattr(paths, "chown_to_operator", _REAL_CHOWN_TO_OPERATOR)
    monkeypatch.setenv("SUDO_USER", "ham")
    pw = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir=str(home))
    real_lstat, real_stat = os.lstat, Path.stat
    rootish = lambda st: os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                         0, st.st_gid, st.st_size, 0, 0, 0))
    chowned = set()
    with patch("pwd.getpwnam", return_value=pw), \
         patch.object(paths.os, "lstat", lambda p: rootish(real_lstat(p))), \
         patch.object(paths.Path, "stat", lambda self, **kw: rootish(real_stat(self))), \
         patch.object(paths.os, "chown", side_effect=lambda p, u, g, **kw: chowned.add(p)):
        paths.MeshForgePaths.ensure_user_dirs()
    for d in (".config", ".local", ".local/share", ".config/meshforge"):
        assert str(home / d) in chowned, d
    assert str(home) not in chowned


# ── the installer half: one shell helper, both sites ──────────────────────

def _run_helper(home, target, root_owned):
    """Source the REAL install_common.sh, fake stat/chown, call the helper."""
    script = f'''
        set -u
        source "{LIB}"
        ROOTOWNED="{" ".join(str(p) for p in root_owned)}"
        stat() {{ local p="${{@: -1}}"; for r in $ROOTOWNED; do [[ "$p" == "$r" ]] && {{ echo 0; return 0; }}; done; echo 1000; }}
        chown() {{ echo "CHOWN $*"; }}
        mf_mkdir_for_user ham "{home}" "{target}"; echo "rc=$?"
    '''
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    return out.stdout


def test_shell_helper_hands_back_every_root_made_dir_not_the_home(tmp_path):
    home = tmp_path / "home" / "ham"
    home.mkdir(parents=True)
    leaf = home / ".config" / "systemd" / "user"
    out = _run_helper(home, leaf, [home, home / ".config", home / ".config" / "systemd", leaf])
    chowned = [l.split()[-1] for l in out.splitlines() if l.startswith("CHOWN")]
    assert chowned == [str(home / ".config"), str(leaf.parent), str(leaf)]   # top-down
    assert "rc=0" in out and leaf.is_dir()


def test_shell_helper_refuses_outside_the_home_and_dotdot(tmp_path):
    home = tmp_path / "home" / "ham"
    home.mkdir(parents=True)
    assert "rc=2" in _run_helper(home, tmp_path / "elsewhere", [])
    assert "rc=2" in _run_helper(home, f"{home}/../evil", [])
    assert not (tmp_path / "elsewhere").exists()


def test_installer_creates_both_user_unit_dirs_through_the_helper():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'mkdir -p "$USER_SYSTEMD_DIR"' not in text
    assert 'mkdir -p "$MINI_USER_SYSTEMD_DIR"' not in text
    assert 'mf_mkdir_for_user "$REAL_USER" "$REAL_HOME" "$USER_SYSTEMD_DIR"' in text
    assert 'mf_mkdir_for_user "$MINI_USER" "$MINI_HOME" "$MINI_USER_SYSTEMD_DIR"' in text


def test_shell_helper_chowns_only_root_owned_dirs(tmp_path):
    """Fable r2: no shell case had a NON-root dir — 'chown regardless of
    owner' survived. ~/.config owned by someone else is never taken over."""
    home = tmp_path / "home" / "ham"
    home.mkdir(parents=True)
    leaf = home / ".config" / "systemd" / "user"
    out = _run_helper(home, leaf, [leaf])          # only the leaf is root's
    assert [l.split()[-1] for l in out.splitlines() if l.startswith("CHOWN")] == [str(leaf)]


def test_shell_helper_refuses_a_missing_home(tmp_path):
    """r1: root's ``mkdir -p`` would CREATE a missing home and leave it root's."""
    home = tmp_path / "home" / "ghost"
    assert "rc=2" in _run_helper(home, home / ".config" / "systemd" / "user", [])
    assert not home.exists()


def test_shell_helper_stops_at_a_symlinked_component(tmp_path):
    home = tmp_path / "home" / "ham"
    (home / ".local").mkdir(parents=True)
    elsewhere = tmp_path / "srv"
    elsewhere.mkdir()
    (home / ".config").symlink_to(elsewhere)
    leaf = home / ".config" / "systemd" / "user"
    out = _run_helper(home, leaf, [home / ".config", leaf.parent, leaf])
    assert [l for l in out.splitlines() if l.startswith("CHOWN")] == []
    assert "rc=0" in out


def test_a_refused_unit_dir_warns_and_never_aborts_the_install():
    """``set -e``: a bare helper call that refuses (rc 2, e.g. MINI_HOME empty
    for a user not in passwd) would ABORT the install mid-way; the old bare
    ``mkdir -p`` instead made ``/.config/systemd/user`` at the filesystem
    root, silently. Each site says why and carries on."""
    text = INSTALLER.read_text(encoding="utf-8")
    for call, rc in (('mf_mkdir_for_user "$REAL_USER" "$REAL_HOME" "$USER_SYSTEMD_DIR" || uud_rc=$?', "uud_rc"),
                     ('mf_mkdir_for_user "$MINI_USER" "$MINI_HOME" "$MINI_USER_SYSTEMD_DIR" || mud_rc=$?', "mud_rc")):
        after = text[text.index(call) + len(call):].lstrip()
        # r1: the refusal SKIPS the deploy block (no copy into a missing dir,
        # no green "deployed" line) — it is the if-branch, the work the else
        assert after.startswith(f"if [[ ${rc} -ne 0 ]]; then"), call
        branch, rest = after.split("else", 1)
        assert "NOT" in branch and "cp " not in branch, call
    # the green success lines live INSIDE the else (Fable r2: moving one out
    # = a success line for work not done, and it passed)
    site1 = text.split("|| uud_rc=$?", 1)[1].split("\n        fi\n", 1)[0]
    assert "✓ User service templates deployed" in site1.split("else", 1)[1]
    site2 = text.split("|| mud_rc=$?", 1)[1].split("\n    fi\n", 1)[0]
    assert "✓ mini-dudeai user units enrolled" in site2.split("else", 1)[1]


# ── THE SEAM: one case table drives BOTH halves (Fable r1-r3 each found a
# guard pinned in only one language; "same kind twice -> one reader + guard
# test"). A REAL tmp tree (links, `..`, slashes are real); only uid 0 is
# faked — bash via a `stat` function, Python via an lstat wrapper. Every case
# states the expected hand-back; bash and Python must BOTH equal it.

def _tree(root, spec):
    """spec: {"dirs": [...], "links": {name: target}} under ``root``."""
    for d in spec.get("dirs", ()):
        (root / d).mkdir(parents=True, exist_ok=True)
    for name, target in spec.get("links", {}).items():
        dst = Path(target) if str(target).startswith("/") else root / target
        dst.mkdir(parents=True, exist_ok=True)
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).symlink_to(dst)


def _py_chain(home, target, root_real):
    real_lstat = os.lstat

    def lstat(p):
        st = real_lstat(p)
        if os.path.realpath(p) in root_real and not stat_mod.S_ISLNK(st.st_mode):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 0,
                                   st.st_gid, st.st_size, 0, 0, 0))
        return st
    return {str(p) for p in paths.root_owned_chain(target, home, lstat=lstat)}


#: (id, tree, home, target, root-owned (rel to root), expected (rel), bash rc)
_CASES = [
    ("fresh-account", {"dirs": ["h"]}, "h", "h/.config/systemd/user",
     ["h/.config", "h/.config/systemd", "h/.config/systemd/user"],
     ["h/.config", "h/.config/systemd", "h/.config/systemd/user"], 0),
    ("config-owned-by-the-user", {"dirs": ["h/.config"]}, "h", "h/.config/systemd/user",
     ["h/.config/systemd/user"], ["h/.config/systemd/user"], 0),
    ("symlinked-home-resolves", {"links": {"h": "ssd/h"}}, "h", "h/.config/x",
     ["ssd/h/.config", "ssd/h/.config/x"], ["ssd/h/.config", "ssd/h/.config/x"], 0),
    ("symlinked-home-double-slash", {"links": {"h": "ssd/h"}}, "h//", "h//.config/x",
     ["ssd/h/.config", "ssd/h/.config/x"], ["ssd/h/.config", "ssd/h/.config/x"], 0),
    ("symlinked-component-stops", {"dirs": ["h"], "links": {"h/.config": "srv"}}, "h",
     "h/.config/systemd", ["srv", "srv/systemd"], [], 0),
    ("trailing-slash-target", {"dirs": ["h"]}, "h", "h/.config/",
     ["h/.config"], ["h/.config"], 0),
    ("dir-is-home-double-slash", {"dirs": ["h"]}, "h", "h//", ["h"], [], 2),
    ("outside-home", {"dirs": ["h", "etc/x"]}, "h", "etc/x", ["etc", "etc/x"], [], 2),
    ("prefix-sibling", {"dirs": ["h", "hamster/.config"]}, "h", "hamster/.config",
     ["hamster", "hamster/.config"], [], 2),
    ("dotdot", {"dirs": ["h", "etc"]}, "h", "h/../etc", ["etc"], [], 2),
]


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_both_halves_hand_back_exactly_the_same_dirs(tmp_path, case):
    name, spec, home_rel, target_rel, root_rel, want_rel, want_rc = case
    root = tmp_path / "fs"
    root.mkdir()
    _tree(root, spec)
    home = f"{root}/{home_rel}"
    target = f"{root}/{target_rel}"
    root_real = {os.path.realpath(root / r) for r in root_rel}
    want = {os.path.realpath(root / r) for r in want_rel}
    # bash: the REAL library; ROOTOWNED keyed by real path (the walk runs on
    # the resolved home, so it passes real paths to stat)
    out = _run_helper(home, target, sorted(root_real))
    sh = {l.split()[-1] for l in out.splitlines() if l.startswith("CHOWN")}
    assert f"rc={want_rc}" in out, (name, out)
    assert {os.path.realpath(p) for p in sh} == want, (name, "bash", sh)
    # python: after bash made the tree (mkdir -p), the same question
    py = _py_chain(home, target, root_real)
    assert {os.path.realpath(p) for p in py} == want, (name, "python", py)


def test_both_halves_refuse_a_home_that_resolves_to_root(tmp_path):
    link = tmp_path / "rooty"
    link.symlink_to("/")
    assert "rc=2" in _run_helper(str(link), f"{link}/etc/x", ["/etc"])
    assert paths.root_owned_chain(Path(f"{link}/etc"), link) == []


def test_a_mkdir_failure_is_rc_1_not_a_refusal(tmp_path):
    """Fable r4: the r3 mkdir-failure path was unpinned. ~/.config is a FILE:
    mkdir fails -> the helper passes mkdir's rc (1), never the refusal's 2,
    and chowns nothing; each site words rc 2 and the rest differently."""
    home = tmp_path / "home" / "ham"
    home.mkdir(parents=True)
    (home / ".config").write_text("not a dir")
    out = _run_helper(home, home / ".config" / "systemd" / "user", [home / ".config"])
    assert "rc=1" in out and "CHOWN" not in out
    text = INSTALLER.read_text(encoding="utf-8")
    for rc in ("uud_rc", "mud_rc"):
        assert f'[[ ${rc} -eq 2 ]] && ' in text, rc
        line = text.split(f'[[ ${rc} -eq 2 ]] && ', 1)[1].split("\n", 1)[0]
        refused, failed = line.split(" || ", 1)
        assert "refused" in refused and "mkdir failed" in failed, rc
