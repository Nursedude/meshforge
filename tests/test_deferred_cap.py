"""Focus gates (operator 2026-11-23: "the product is???").

Measured that day: 30 d = 637 commits, 219 (34 %) touch src/; the deferred
ledger held 117 live rows and every session ended by adding more. Two gates:

1. scripts/deferred_cap.py — PreToolUse Write|Edit|MultiEdit on any file
   named deferred_work.json: an edit may NOT raise the live-row count
   (status not done/dropped). Adding one means closing one in the same edit.
   No baseline file: the invariant itself only shrinks.
2. scripts/roadmap.py --now — the session-intake line also prints the
   measured product share (commits touching src/ over 7 d) and the live
   deferred count, so the first thing a session reads is whether the work
   is the product.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = _load("deferred_cap")
roadmap = _load("roadmap")


def _ledger(statuses):
    return {"_comment": "x", "tasks": [{"id": f"t{i}", "status": s} for i, s in enumerate(statuses)]}


def _write_ledger(tmp_path, statuses) -> Path:
    p = tmp_path / "deferred_work.json"
    p.write_text(json.dumps(_ledger(statuses), indent=4))
    return p


def _pre(tool, tool_input):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})


class TestLiveCount:
    def test_done_and_dropped_are_not_live(self):
        assert cap.live_count(_ledger(["open", "blocked", "done", "dropped", "paused", "deferred"])) == 4

    @pytest.mark.parametrize("bad", [{}, {"tasks": {}}, [], {"tasks": [1]}, None])
    def test_unjudgeable_shapes_raise(self, bad):
        with pytest.raises(cap.LedgerShapeError):
            cap.live_count(bad)


class TestCapHook:
    def test_write_that_adds_a_live_row_is_refused(self, tmp_path):
        p = _write_ledger(tmp_path, ["open", "done"])
        new = json.dumps(_ledger(["open", "done", "open"]))
        rc, msg = cap.hook_main(_pre("Write", {"file_path": str(p), "content": new}))
        assert rc == 2 and "1 → 2" in msg and "close" in msg.lower()

    def test_write_that_keeps_the_count_is_allowed(self, tmp_path):
        p = _write_ledger(tmp_path, ["open", "done"])
        new = json.dumps(_ledger(["done", "done", "open"]))  # closed one, added one
        assert cap.hook_main(_pre("Write", {"file_path": str(p), "content": new})) == (0, "")

    def test_edit_that_adds_a_row_is_refused(self, tmp_path):
        p = _write_ledger(tmp_path, ["open"])
        old = '"status": "open"\n        }'
        new = '"status": "open"\n        },\n        {"id": "x", "status": "open"}'
        assert old in p.read_text()
        rc, msg = cap.hook_main(_pre("Edit", {"file_path": str(p), "old_string": old, "new_string": new}))
        assert rc == 2

    def test_edit_that_closes_one_and_adds_one_is_allowed(self, tmp_path):
        p = _write_ledger(tmp_path, ["open", "open"])
        text = p.read_text()
        old = '"id": "t0",\n            "status": "open"'
        assert old in text
        new = '"id": "t0",\n            "status": "done"'
        old2 = '"id": "t1",\n            "status": "open"\n        }'
        new2 = old2 + ',\n        {"id": "x", "status": "open"}'
        rc = cap.hook_main(_pre("MultiEdit", {"file_path": str(p), "edits": [
            {"old_string": old, "new_string": new},
            {"old_string": old2, "new_string": new2}]}))
        assert rc == (0, "")

    def test_replace_all_is_applied(self, tmp_path):
        p = _write_ledger(tmp_path, ["done", "done"])
        rc, _ = cap.hook_main(_pre("Edit", {"file_path": str(p), "old_string": '"done"',
                                            "new_string": '"open"', "replace_all": True}))
        assert rc == 2

    def test_an_edit_that_breaks_the_json_is_refused(self, tmp_path):
        p = _write_ledger(tmp_path, ["open"])
        rc, msg = cap.hook_main(_pre("Write", {"file_path": str(p), "content": "{not json"}))
        assert rc == 2 and "parse" in msg

    def test_other_files_are_ignored(self, tmp_path):
        p = tmp_path / "other.json"
        p.write_text("{}")
        assert cap.hook_main(_pre("Write", {"file_path": str(p), "content": "x"})) == (0, "")

    def test_edit_whose_old_string_is_absent_is_left_to_the_tool(self, tmp_path):
        p = _write_ledger(tmp_path, ["open"])
        assert cap.hook_main(_pre("Edit", {"file_path": str(p), "old_string": "NOPE",
                                           "new_string": "x"})) == (0, "")

    @pytest.mark.parametrize("bad_status", [["open"], {"a": 1}, 3])
    def test_non_string_status_counts_live_and_cannot_bypass(self, tmp_path, bad_status):
        p = _write_ledger(tmp_path, ["open"])
        new = _ledger(["open", "open"])
        new["tasks"][0]["status"] = bad_status
        rc, _ = cap.hook_main(_pre("Write", {"file_path": str(p), "content": json.dumps(new)}))
        assert rc == 2

    def test_edit_create_of_a_missing_ledger_is_judged_like_write(self, tmp_path):
        p = tmp_path / "deferred_work.json"
        body = json.dumps(_ledger(["open", "open"]))
        w = cap.hook_main(_pre("Write", {"file_path": str(p), "content": body}))
        e = cap.hook_main(_pre("Edit", {"file_path": str(p), "old_string": "", "new_string": body}))
        assert w[0] == e[0] == 2

    def test_an_unjudgeable_ledger_may_be_repaired(self, tmp_path):
        p = tmp_path / "deferred_work.json"
        p.write_text("{broken")
        good = json.dumps(_ledger(["open"] * 5))
        assert cap.hook_main(_pre("Write", {"file_path": str(p), "content": good})) == (0, "")
        assert cap.hook_main(_pre("Write", {"file_path": str(p), "content": "{still broken"}))[0] == 2

    def test_unparseable_payload_is_witnessed_not_blocking(self):
        rc, msg = cap.hook_main("{not json")
        assert rc == 1 and msg

    def test_main_reads_stdin_and_reports_on_stderr(self, tmp_path):
        p = _write_ledger(tmp_path, ["open"])
        payload = _pre("Write", {"file_path": str(p), "content": json.dumps(_ledger(["open", "open"]))})
        r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "deferred_cap.py")],
                           input=payload.encode(), capture_output=True, timeout=30)
        assert r.returncode == 2 and b"deferred" in r.stderr


class TestWiring:
    def test_pretooluse_write_edit_runs_the_cap(self):
        s = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for g in s["hooks"]["PreToolUse"]
                if "Edit" in g.get("matcher", "") and "Write" in g.get("matcher", "")
                for h in g["hooks"]]
        assert any("scripts/deferred_cap.py" in c for c in cmds)

    def test_live_excluded_equals_the_ledger_owners_terminal_statuses(self):
        tree = ast.parse((REPO_ROOT / "scripts" / "deferred_work_watch.py").read_text())
        watcher = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                       and any(getattr(t, "id", "") == "TERMINAL_STATUSES" for t in n.targets))
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from mini_dudeai.warmstart import DEFERRED_TERMINAL_STATUSES
        assert cap.LIVE_EXCLUDED == frozenset(watcher) == frozenset(DEFERRED_TERMINAL_STATUSES)

    def test_roadmap_counts_with_the_caps_live_count(self, tmp_path, monkeypatch):
        import deferred_cap as shared  # the module roadmap imports lazily
        p = _write_ledger(tmp_path, ["open"])
        monkeypatch.setattr(shared, "live_count", lambda data: 4242)
        assert "4242 live" in roadmap.focus_line(str(tmp_path), ledger=str(p))


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path, monkeypatch):
    # git walks up to a parent repo and reads global config (gpgsign, hooksPath):
    # pin both so a verdict never depends on where basetemp lives.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


class TestFocusLine:
    def _repo(self, tmp_path, n_src, n_other):
        r = tmp_path / "r"
        r.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True,  # noqa: E731
                                      capture_output=True, timeout=30)
        g("init", "-q")
        g("config", "user.email", "t@example.invalid")
        g("config", "user.name", "t")
        (r / "src").mkdir()
        for i in range(n_src):
            (r / "src" / f"f{i}.py").write_text("x")
            g("add", "-A")
            g("commit", "-q", "-m", f"s{i}")
        for i in range(n_other):
            (r / f"d{i}.md").write_text("x")
            g("add", "-A")
            g("commit", "-q", "-m", f"d{i}")
        return r

    def test_share_counts_commits_touching_src(self, tmp_path):
        r = self._repo(tmp_path, 1, 3)
        assert roadmap.product_share(str(r), days=7) == (1, 4)

    def test_line_warns_below_half(self, tmp_path):
        r = self._repo(tmp_path, 1, 3)
        line = roadmap.focus_line(str(r), ledger=None)
        assert "1/4" in line and "25%" in line and "⚠" in line

    def test_line_is_quiet_at_or_above_half(self, tmp_path):
        r = self._repo(tmp_path, 2, 2)
        line = roadmap.focus_line(str(r), ledger=None)
        assert "2/4" in line and "⚠" not in line

    def test_no_commits_in_window_is_unknown_not_zero_percent(self, tmp_path):
        r = self._repo(tmp_path, 0, 0)
        assert "UNKNOWN" in roadmap.focus_line(str(r), ledger=None)

    def test_empty_ledger_override_is_unknown_not_the_real_ledger(self, tmp_path, monkeypatch):
        r = self._repo(tmp_path, 2, 2)
        monkeypatch.setenv("DEFERRED_WORK_LEDGER", "")
        assert "deferred UNKNOWN" in roadmap.focus_line(str(r))

    def test_not_a_repo_is_unknown_not_zero(self, tmp_path):
        line = roadmap.focus_line(str(tmp_path), ledger=None)
        assert "UNKNOWN" in line

    def test_deferred_count_is_shown_and_unreadable_is_unknown(self, tmp_path):
        r = self._repo(tmp_path, 2, 2)
        p = _write_ledger(tmp_path, ["open", "open", "done"])
        assert "2 live" in roadmap.focus_line(str(r), ledger=str(p))
        p.write_text("{oops")
        assert "deferred UNKNOWN" in roadmap.focus_line(str(r), ledger=str(p))
