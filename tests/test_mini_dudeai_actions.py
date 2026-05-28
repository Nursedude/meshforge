"""Action adapter tests."""
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai import (
    Condition, FileAnnotateAction, NoopAction, NtfyAction,
    ProposeEscalationAction,
)


def test_ntfy_requires_topic():
    with pytest.raises(ValueError):
        NtfyAction(topic="")


def test_ntfy_url_construction():
    a = NtfyAction(topic="my-topic", base_url="https://ntfy.example.com")
    assert a.url == "https://ntfy.example.com/my-topic"


def test_ntfy_post_on_edge_up():
    a = NtfyAction(topic="t")
    rule = {"id": "r1",
            "action": {"kind": "ntfy", "title": "T",
                       "message": "{subject}: {detail}", "priority": "high"}}
    cond = Condition(kind="x", subject="foo", detail="bar")
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b""
        outcome = a.execute(rule, cond, "edge_up")
    assert outcome.ok is True
    assert mock_open.called
    # Verify request body equals the formatted message
    req = mock_open.call_args[0][0]
    assert req.data == b"foo: bar"
    assert req.get_header("Priority") == "high"
    assert req.get_header("Title") == "T"


def test_ntfy_edge_down_uses_min_priority():
    a = NtfyAction(topic="t")
    rule = {"id": "r1", "action": {"kind": "ntfy"}}
    cond = Condition(kind="x", subject="foo", detail="")
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b""
        a.execute(rule, cond, "edge_down")
    req = mock_open.call_args[0][0]
    assert req.get_header("Priority") == "min"


def test_ntfy_records_error_on_network_failure():
    import urllib.error
    a = NtfyAction(topic="t", timeout_s=0.001)
    rule = {"id": "r1", "action": {"kind": "ntfy"}}
    cond = Condition(kind="x", subject="foo", detail="")
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("connection refused")):
        outcome = a.execute(rule, cond, "edge_up")
    assert outcome.ok is False
    assert "URLError" in outcome.error


def test_file_annotate_appends_line(tmp_path):
    p = tmp_path / "anno.md"
    a = FileAnnotateAction(path=str(p))
    rule = {"id": "r1", "annotation": "the note"}
    cond = Condition(kind="x", subject="foo", detail="bar")
    a.execute(rule, cond, "edge_up")
    a.execute(rule, cond, "edge_down")
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert "**r1**" in lines[0]
    assert "edge_up" in lines[0]
    assert "edge_down" in lines[1]
    assert "the note" in lines[0]


def test_file_annotate_records_error_on_perms(tmp_path):
    # Append to a directory path — will fail with IsADirectoryError
    a = FileAnnotateAction(path=str(tmp_path))
    cond = Condition(kind="x", subject="foo", detail="")
    outcome = a.execute({"id": "r1"}, cond, "edge_up")
    assert outcome.ok is False


def test_propose_escalation_returns_payload():
    a = ProposeEscalationAction()
    rule = {"id": "r1", "annotation": "noteworthy"}
    cond = Condition(kind="x", subject="foo", detail="bar")
    outcome = a.execute(rule, cond, "edge_up")
    assert outcome.ok is True
    esc = outcome.extras["escalation"]
    assert esc["rule"] == "r1"
    assert esc["subject"] == "foo"
    assert esc["note"] == "noteworthy"
    assert esc["transition"] == "edge_up"


def test_noop_does_nothing():
    a = NoopAction()
    out = a.execute({"id": "r1"}, Condition(kind="x", subject="s", detail=""), "edge_up")
    assert out.ok is True
    assert out.action == "none"
