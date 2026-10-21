"""update.sh must not overwrite an installer-RENDERED user unit with its raw template.

2026-10-21, MeshAnchor meshcore-chat: `update.sh` copies every
`templates/systemd/*-user.service` verbatim into ~/.config/systemd/user/. A
template carrying an install-time placeholder (`__CHAT_PANE_EXEC__`, rendered
by the TUI installer) landed there raw on 2026-10-19; the running process hid
it for ~40 h, and the next restart (the rns mf.11 roll's #69 hold) left tmux
running the literal token — the unit cycled in auto-restart. These run the
REAL update.sh loop against a sandbox. Byte-identical in MeshForge and
MeshAnchor (both installers carry the loop).
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
START = 'for tmpl in "$INSTALL_DIR/templates/systemd/"*-user.service; do'
PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]+__")


def _loop() -> str:
    text = (REPO / "scripts" / "update.sh").read_text()
    i = text.index(START)
    j = text.index("\n    done\n", i) + len("\n    done\n")
    return text[i:j]


def _run(install_dir: Path, user_dir: Path) -> subprocess.CompletedProcess:
    script = (f'GREEN=""; YELLOW=""; NC=""; INSTALL_DIR="{install_dir}"; '
              f'USER_SYSTEMD_DIR="{user_dir}"\n{_loop()}\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30)


def _sandbox(tmp_path: Path):
    tdir = tmp_path / "repo" / "templates" / "systemd"
    tdir.mkdir(parents=True)
    (tdir / "plain-user.service").write_text("[Service]\nExecStart=/bin/true\n")
    (tdir / "pane-user.service").write_text(
        "[Service]\nExecStart=/usr/bin/tmux new-session -d -s pane '__PANE_EXEC__'\n")
    user = tmp_path / "user"
    user.mkdir()
    return tmp_path / "repo", user


def test_a_plain_template_is_still_deployed(tmp_path):
    repo, user = _sandbox(tmp_path)
    r = _run(repo, user)
    assert r.returncode == 0, r.stderr
    assert (user / "plain.service").read_text() == \
        (repo / "templates/systemd/plain-user.service").read_text()


def test_a_rendered_unit_is_not_overwritten_by_its_raw_template(tmp_path):
    repo, user = _sandbox(tmp_path)
    rendered = "[Service]\nExecStart=/usr/bin/tmux new-session -d -s pane '/home/op/wrap.sh'\n"
    (user / "pane.service").write_text(rendered)
    r = _run(repo, user)
    assert r.returncode == 0, r.stderr
    assert (user / "pane.service").read_text() == rendered


def test_a_placeholder_template_is_never_installed_raw(tmp_path):
    """No unit installed yet: a raw copy would be a dead unit waiting for
    someone to enable it. Leave it to its installer, and SAY so."""
    repo, user = _sandbox(tmp_path)
    r = _run(repo, user)
    assert not (user / "pane.service").exists()
    assert "pane.service" in r.stdout and "installer" in r.stdout


def test_every_placeholder_template_in_this_repo_is_skipped(tmp_path):
    """Against the REAL templates: no installed file may carry a token."""
    user = tmp_path / "user"
    user.mkdir()
    r = _run(REPO, user)
    assert r.returncode == 0, r.stderr
    raw = sorted(p.name for p in (REPO / "templates" / "systemd").glob("*-user.service")
                 if PLACEHOLDER.search(p.read_text()))
    assert raw, "no template carries a placeholder — this test compares nothing"
    leaked = sorted(p.name for p in user.iterdir() if PLACEHOLDER.search(p.read_text()))
    assert not leaked, leaked
