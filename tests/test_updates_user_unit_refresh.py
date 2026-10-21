"""TUI Software Updates must obey the same user-unit rule as update.sh.

2026-10-21 review of the update.sh cure: the TUI's
UpdatesHandler._refresh_service_files held its OWN verbatim copy of
templates/systemd/*-user.service — an update run from the TUI would have
re-broken meshcore-chat exactly as update.sh did. Both now follow
utils.user_unit_templates. Byte-identical in MeshForge and MeshAnchor.
"""
import shutil
from pathlib import Path
from unittest.mock import patch

from handlers.updates import UpdatesHandler

REPO = Path(__file__).parent.parent


def _sandbox(tmp_path):
    repo = tmp_path / "repo"
    tdir = repo / "templates" / "systemd"
    tdir.mkdir(parents=True)
    (tdir / "plain-user.service").write_text("[Service]\nExecStart=/bin/true\n")
    (tdir / "pane-user.service").write_text(
        "# MF-DEPLOY: rendered PANE_EXEC by scripts/install_pane.sh\n"
        "[Service]\nExecStart=/usr/bin/tmux new-session -d -s pane '__PANE_EXEC__'\n")
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    return repo, home


def _refresh(repo, home):
    h = UpdatesHandler.__new__(UpdatesHandler)
    with patch("handlers.updates.repo_root", return_value=repo), \
            patch("utils.paths.get_real_user_home", return_value=home), \
            patch("handlers.updates.daemon_reload") as reload:
        ok, detail = h._refresh_service_files()
    return ok, detail, reload


def test_the_tui_copier_never_overwrites_a_rendered_unit(tmp_path):
    repo, home = _sandbox(tmp_path)
    udir = home / ".config" / "systemd" / "user"
    rendered = "[Service]\nExecStart=/usr/bin/tmux new-session -d -s pane '/home/op/w.sh'\n"
    (udir / "pane.service").write_text(rendered)
    ok, detail, reload = _refresh(repo, home)
    assert ok
    assert (udir / "pane.service").read_text() == rendered
    assert (udir / "plain.service").exists()
    assert "pane.service not copied" in detail and "install_pane.sh" in detail
    reload.assert_called_once()


def test_the_tui_copier_never_installs_a_rendered_template_raw(tmp_path):
    repo, home = _sandbox(tmp_path)
    ok, detail, _ = _refresh(repo, home)
    assert ok
    assert not (home / ".config/systemd/user/pane.service").exists()
    assert "pane.service" not in detail  # not installed here: nothing to say


def test_the_tui_copier_against_this_repos_real_templates(tmp_path):
    home = tmp_path / "home"
    udir = home / ".config" / "systemd" / "user"
    udir.mkdir(parents=True)
    repo = tmp_path / "repo"
    shutil.copytree(REPO / "templates" / "systemd", repo / "templates" / "systemd")
    ok, _, _ = _refresh(repo, home)
    assert ok
    import re
    tok = re.compile(r"__[A-Z0-9][A-Z0-9_]*__")
    leaked = [p.name for p in udir.iterdir()
              if any(tok.search(l) for l in p.read_text().splitlines() if not l.lstrip().startswith("#"))]
    assert not leaked, leaked


# ── the TUI surface: Software Updates > User Unit Templates + report line ──

from unittest.mock import MagicMock  # noqa: E402


def _pane(repo, home):
    h = UpdatesHandler.__new__(UpdatesHandler)
    h.ctx = MagicMock()
    with patch("handlers.updates.repo_root", return_value=repo), \
            patch("utils.paths.get_real_user_home", return_value=home):
        h._user_unit_audit()
        verdict = h._user_unit_verdict()
    title, text = h.ctx.dialog.msgbox.call_args[0][:2]
    return title, text, verdict


def test_the_pane_says_BROKEN_first_and_names_the_fix(tmp_path):
    repo, home = _sandbox(tmp_path)
    udir = home / ".config" / "systemd" / "user"
    (udir / "pane.service").write_text((repo / "templates/systemd/pane-user.service").read_text())
    title, text, (ok, verdict, _) = _pane(repo, home)
    assert title == "User Unit Templates"
    first = text.splitlines()[0]
    assert "BROKEN" in first and not ok
    assert "[RAW    ] pane.service" in text and "install_pane.sh" in text


def test_the_pane_on_a_clean_box_says_current(tmp_path):
    repo, home = _sandbox(tmp_path)
    udir = home / ".config" / "systemd" / "user"
    (udir / "plain.service").write_text((repo / "templates/systemd/plain-user.service").read_text())
    _, text, (ok, verdict, _) = _pane(repo, home)
    assert ok and text.splitlines()[0] == "all 1 installed user units current"


def test_the_menu_offers_the_pane():
    import inspect
    src = inspect.getsource(UpdatesHandler._updates_menu)
    assert '"user-units"' in src and "self._user_unit_audit" in src


def test_the_update_report_carries_a_user_units_line():
    import inspect
    src = inspect.getsource(UpdatesHandler)
    assert 'steps.append(("user units",) + self._user_unit_verdict()[:2])' in src
