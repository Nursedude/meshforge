"""update.sh must not overwrite an installer-RENDERED user unit with its raw template.

2026-10-21, MeshAnchor meshcore-chat: `update.sh` copied every
`templates/systemd/*-user.service` verbatim into ~/.config/systemd/user/. A
template carrying an install-time placeholder (`__CHAT_PANE_EXEC__`, rendered
by the TUI installer) landed there raw on 2026-10-19; the running process hid
it for ~40 h, and the next restart (the rns mf.11 roll's #69 hold) left tmux
running the literal token — the unit cycled in auto-restart. The copy now
follows utils.user_unit_templates (the rule the TUI uses too). These run the
REAL update.sh block, under `set -e` like update.sh itself, against a sandbox.
Byte-identical in MeshForge and MeshAnchor.
"""
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
BEGIN = "# BEGIN user-template deploy"
END = "# END user-template deploy"
PLACEHOLDER = re.compile(r"__[A-Z0-9][A-Z0-9_]*__")


def _block() -> str:
    text = (REPO / "scripts" / "update.sh").read_text()
    i = text.index(BEGIN)
    return text[i:text.index(END, i) + len(END)]


def _run(install_dir: Path, user_dir: Path) -> subprocess.CompletedProcess:
    script = (f'set -e\nGREEN=""; YELLOW=""; NC=""; INSTALL_DIR="{install_dir}"; '
              f'USER_SYSTEMD_DIR="{user_dir}"\n{_block()}\necho BLOCK_DONE\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60)


def _sandbox(tmp_path: Path, with_module: bool = True):
    repo = tmp_path / "repo"
    tdir = repo / "templates" / "systemd"
    tdir.mkdir(parents=True)
    if with_module:
        (repo / "src" / "utils").mkdir(parents=True)
        (repo / "src" / "utils" / "__init__.py").write_text("")
        shutil.copy(REPO / "src" / "utils" / "user_unit_templates.py", repo / "src" / "utils")
    (tdir / "plain-user.service").write_text("[Service]\nExecStart=/bin/true\n")
    (tdir / "pane-user.service").write_text(
        "# MF-DEPLOY: rendered PANE_EXEC by scripts/install_pane.sh\n"
        "[Service]\nExecStart=/usr/bin/tmux new-session -d -s pane '__PANE_EXEC__'\n")
    user = tmp_path / "user"
    user.mkdir()
    return repo, user


def test_a_plain_template_is_still_deployed(tmp_path):
    repo, user = _sandbox(tmp_path)
    r = _run(repo, user)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    assert (user / "plain.service").read_text() == \
        (repo / "templates/systemd/plain-user.service").read_text()


def test_a_rendered_unit_is_not_overwritten_and_the_skip_is_said(tmp_path):
    repo, user = _sandbox(tmp_path)
    rendered = "[Service]\nExecStart=/usr/bin/tmux new-session -d -s pane '/home/op/wrap.sh'\n"
    (user / "pane.service").write_text(rendered)
    r = _run(repo, user)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    assert (user / "pane.service").read_text() == rendered
    assert "pane.service" in r.stdout and "install_pane.sh" in r.stdout


def test_a_rendered_template_is_never_installed_raw_and_stays_quiet(tmp_path):
    """Not installed here: a raw copy would be a dead unit waiting to be
    enabled; and a box that never installed the app hears nothing about it."""
    repo, user = _sandbox(tmp_path)
    r = _run(repo, user)
    assert not (user / "pane.service").exists()
    assert "pane.service" not in r.stdout


def test_a_plan_that_cannot_run_copies_nothing_and_says_so(tmp_path):
    repo, user = _sandbox(tmp_path, with_module=False)
    r = _run(repo, user)
    assert "BLOCK_DONE" in r.stdout, r.stderr  # set -e did not abort update.sh
    assert not list(user.iterdir())
    assert "plan failed" in r.stdout


def test_every_placeholder_template_in_this_repo_is_skipped(tmp_path):
    """Against the REAL templates and module: no installed file may carry a token."""
    user = tmp_path / "user"
    user.mkdir()
    r = _run(REPO, user)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    raw = sorted(p.name for p in (REPO / "templates" / "systemd").glob("*-user.service")
                 if PLACEHOLDER.search(p.read_text()))
    assert raw, "no template carries a placeholder — this test compares nothing"
    leaked = sorted(p.name for p in user.iterdir() if PLACEHOLDER.search(p.read_text()))
    assert not leaked, leaked
    assert len(list(user.iterdir())) >= 1, "the real plan copied nothing at all"
