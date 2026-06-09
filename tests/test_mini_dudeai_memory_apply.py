"""Tests for the deterministic mini-dudeai memory writer.

Uses pytest ``tmp_path`` as ``memory_dir`` exclusively — NEVER the live store
at ~/.claude/projects/-opt-meshforge/memory/.
"""

from __future__ import annotations

import re

import pytest

from mini_dudeai.memory_apply import (
    ALLOWED_MEM_TYPES,
    ARCHIVE_FILENAME,
    MEMORY_INDEX_LIMIT_ENV,
    MEMORY_INDEX_SOFT_LIMIT_BYTES,
    MemoryCandidate,
    Provenance,
    apply_memory_candidate,
    check_index_size,
    demote_memory,
    render_index_line,
    render_memory_file,
    supersede_memory,
    validate_candidate,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_provenance(**overrides):
    base = dict(
        origin="claude",
        host="moc1",
        session_id="11111111-2222-3333-4444-555555555555",
        confidence="high",
        verified=True,
    )
    base.update(overrides)
    return Provenance(**base)


def make_candidate(**overrides):
    """A valid reference-type candidate by default (no Why/How needed)."""
    prov = overrides.pop("provenance", None) or make_provenance()
    base = dict(
        name="example-fact",
        description="A one line description of the fact.",
        mem_type="reference",
        body="This is the body of the fact with detail.\n",
        index_title="Example Fact",
        index_hook="the short hook text",
        provenance=prov,
        links=[],
    )
    base.update(overrides)
    return MemoryCandidate(**base)


def make_feedback_candidate(**overrides):
    body = (
        "Some observed feedback about the workflow.\n\n"
        "**Why:** because the failure mode bit us.\n\n"
        "**How to apply:** do the careful thing next time.\n"
    )
    base = dict(
        name="careful-thing",
        description="Always do the careful thing.",
        mem_type="feedback",
        body=body,
        index_title="Careful Thing",
        index_hook="do the careful thing",
    )
    base.update(overrides)
    return make_candidate(**base)


# ---------------------------------------------------------------------------
# render_memory_file
# ---------------------------------------------------------------------------


def test_render_memory_file_frontmatter_shape():
    cand = make_candidate(
        name="shape-test",
        mem_type="project",
        body="body\n\n**Why:** w\n\n**How to apply:** h\n",
    )
    text = render_memory_file(cand)

    # Frontmatter delimiters.
    assert text.startswith("---\n")
    assert "\n---\n" in text

    # Canonical store keys (matching the real store superset shape).
    assert 'name: "shape-test"' in text
    assert "description:" in text
    assert "metadata:" in text
    assert "node_type: memory" in text
    assert 'type: "project"' in text
    assert "originSessionId:" in text
    assert "origin:" in text
    assert "host:" in text
    assert "confidence:" in text
    assert "verified:" in text

    # Body present after frontmatter.
    fm_end = text.index("\n---\n", 4) + len("\n---\n")
    body_region = text[fm_end:]
    assert "**Why:** w" in body_region

    # Single trailing newline.
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_render_memory_file_origin_session_id_null_when_missing():
    cand = make_candidate(provenance=make_provenance(session_id=None))
    text = render_memory_file(cand)
    assert "originSessionId: null" in text


def test_render_memory_file_appends_related_for_unembedded_links():
    cand = make_candidate(
        links=["other-fact", "third-fact"],
        body="body without inline links\n",
    )
    text = render_memory_file(cand)
    assert "Related: [[other-fact]] [[third-fact]]" in text


def test_render_memory_file_skips_related_when_links_already_in_body():
    cand = make_candidate(
        links=["other-fact"],
        body="body that already cites [[other-fact]] inline\n",
    )
    text = render_memory_file(cand)
    assert "Related:" not in text


def test_render_memory_file_description_quoting_handles_colon():
    cand = make_candidate(description="topic: a tricky one with: colons")
    text = render_memory_file(cand)
    assert 'description: "topic: a tricky one with: colons"' in text


# ---------------------------------------------------------------------------
# render_index_line
# ---------------------------------------------------------------------------


def test_render_index_line_shape():
    cand = make_candidate(
        name="my-fact", index_title="My Fact", index_hook="the hook"
    )
    line = render_index_line(cand)
    assert line == "- [My Fact](my-fact.md) — the hook"
    # Em-dash, not hyphen, as the separator.
    assert " — " in line


# ---------------------------------------------------------------------------
# apply: write + index
# ---------------------------------------------------------------------------


def test_apply_writes_file_and_one_index_line(tmp_path):
    cand = make_candidate(name="written-fact")
    result = apply_memory_candidate(cand, tmp_path)

    assert result.status == "written"
    assert result.index_updated is True
    target = tmp_path / "written-fact.md"
    assert target.exists()
    assert result.path == target

    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    lines = [ln for ln in index.split("\n") if "written-fact.md" in ln]
    assert len(lines) == 1


def test_apply_is_idempotent(tmp_path):
    cand = make_candidate(name="idem-fact")
    first = apply_memory_candidate(cand, tmp_path)
    assert first.status == "written"

    second = apply_memory_candidate(cand, tmp_path)
    assert second.status == "skipped_exists"
    assert second.index_updated is False

    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    matches = [ln for ln in index.split("\n") if "idem-fact.md" in ln]
    assert len(matches) == 1  # no duplicate line


def test_apply_dry_run_touches_nothing(tmp_path):
    cand = make_candidate(name="dry-fact")
    result = apply_memory_candidate(cand, tmp_path, dry_run=True)

    assert result.status == "dry_run"
    assert result.content is not None
    assert 'name: "dry-fact"' in result.content
    # Nothing written to disk.
    assert not (tmp_path / "dry-fact.md").exists()
    assert not (tmp_path / "MEMORY.md").exists()


def test_apply_allow_overwrite(tmp_path):
    cand = make_candidate(name="ow-fact", body="original body\n")
    apply_memory_candidate(cand, tmp_path)

    updated = make_candidate(name="ow-fact", body="updated body\n")
    blocked = apply_memory_candidate(updated, tmp_path)
    assert blocked.status == "skipped_exists"
    assert "original body" in (tmp_path / "ow-fact.md").read_text(
        encoding="utf-8"
    )

    allowed = apply_memory_candidate(updated, tmp_path, allow_overwrite=True)
    assert allowed.status == "written"
    assert "updated body" in (tmp_path / "ow-fact.md").read_text(
        encoding="utf-8"
    )


def test_apply_index_idempotent_when_line_already_present(tmp_path):
    """If the file's index line exists but the file was removed, re-apply
    writes the file but does not duplicate the index line."""
    cand = make_candidate(name="reindex-fact")
    apply_memory_candidate(cand, tmp_path)

    # Remove just the .md file, keep the index line.
    (tmp_path / "reindex-fact.md").unlink()

    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "written"
    assert result.index_updated is False  # line already present

    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    matches = [ln for ln in index.split("\n") if "reindex-fact.md" in ln]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "BadName",
        "bad_name",
        "bad name",
        "-leading",
        "trailing-",
        "double--hyphen",
        "",
        "UPPER",
    ],
)
def test_validate_bad_kebab_name(tmp_path, bad_name):
    cand = make_candidate(name=bad_name)
    assert validate_candidate(cand) is not None
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_validate_bad_type(tmp_path):
    cand = make_candidate(mem_type="bogus")
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert "mem_type" in result.reason
    assert list(tmp_path.iterdir()) == []


def test_validate_multiline_description(tmp_path):
    cand = make_candidate(description="line one\nline two")
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert "single line" in result.reason
    assert list(tmp_path.iterdir()) == []


def test_validate_empty_description(tmp_path):
    cand = make_candidate(description="   ")
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert list(tmp_path.iterdir()) == []


def test_validate_empty_body(tmp_path):
    cand = make_candidate(body="   \n  ")
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert list(tmp_path.iterdir()) == []


def test_validate_feedback_missing_why_and_howto(tmp_path):
    cand = make_candidate(mem_type="feedback", body="just a plain body\n")
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert "Why" in result.reason
    assert "How to apply" in result.reason
    assert list(tmp_path.iterdir()) == []


def test_validate_project_missing_howto_only(tmp_path):
    cand = make_candidate(
        mem_type="project",
        body="body\n\n**Why:** because reasons\n",
    )
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert "How to apply" in result.reason
    assert "Why" not in result.reason  # Why was present
    assert list(tmp_path.iterdir()) == []


def test_validate_feedback_with_why_howto_is_valid(tmp_path):
    cand = make_feedback_candidate()
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "written"


def test_validate_empty_index_fields(tmp_path):
    cand = make_candidate(index_title="  ")
    assert apply_memory_candidate(cand, tmp_path).status == "invalid"
    cand2 = make_candidate(index_hook="")
    assert apply_memory_candidate(cand2, tmp_path).status == "invalid"
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# provenance gate
# ---------------------------------------------------------------------------


def test_provenance_gate_mini_verified_true_invalid(tmp_path):
    cand = make_candidate(
        provenance=make_provenance(origin="mini", verified=True),
    )
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert "mini" in result.reason
    assert list(tmp_path.iterdir()) == []


def test_provenance_gate_mini_verified_false_written(tmp_path):
    cand = make_candidate(
        name="mini-fact",
        provenance=make_provenance(origin="mini", verified=False),
    )
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "written"
    text = (tmp_path / "mini-fact.md").read_text(encoding="utf-8")
    assert 'origin: "mini"' in text
    assert "verified: false" in text


def test_provenance_gate_claude_verified_true_written(tmp_path):
    cand = make_candidate(
        name="claude-fact",
        provenance=make_provenance(origin="claude", verified=True),
    )
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "written"
    text = (tmp_path / "claude-fact.md").read_text(encoding="utf-8")
    assert "verified: true" in text


def test_provenance_gate_operator_verified_true_written(tmp_path):
    cand = make_candidate(
        name="operator-fact",
        provenance=make_provenance(origin="operator", verified=True),
    )
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "written"


def test_provenance_bad_origin_invalid(tmp_path):
    cand = make_candidate(provenance=make_provenance(origin="robot"))
    result = apply_memory_candidate(cand, tmp_path)
    assert result.status == "invalid"
    assert list(tmp_path.iterdir()) == []


def test_provenance_stamps_host_and_session(tmp_path):
    cand = make_candidate(
        name="stamped-fact",
        provenance=make_provenance(host="moc3", session_id="abc-123"),
    )
    apply_memory_candidate(cand, tmp_path)
    text = (tmp_path / "stamped-fact.md").read_text(encoding="utf-8")
    assert 'host: "moc3"' in text
    assert 'originSessionId: "abc-123"' in text


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


def test_supersede_marks_file_and_keeps_it(tmp_path):
    cand = make_candidate(name="old-fact")
    apply_memory_candidate(cand, tmp_path)

    result = supersede_memory(
        "old-fact",
        tmp_path,
        superseded_by="new-fact",
        note="replaced by better understanding",
        date="2026-05-30",
    )
    assert result.status == "written"

    target = tmp_path / "old-fact.md"
    assert target.exists()  # never deleted
    text = target.read_text(encoding="utf-8")
    assert "⚠️ SUPERSEDED" in text
    assert "replaced by better understanding" in text
    assert "2026-05-30" in text
    assert 'superseded_by: "new-fact"' in text


def test_supersede_updates_index_hook(tmp_path):
    cand = make_candidate(name="hooked-fact", index_hook="original hook")
    apply_memory_candidate(cand, tmp_path)

    supersede_memory(
        "hooked-fact", tmp_path, superseded_by=None, note="retracted"
    )
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    hooked_line = [
        ln for ln in index.split("\n") if "hooked-fact.md" in ln
    ][0]
    assert "[SUPERSEDED] original hook" in hooked_line


def test_supersede_index_hook_idempotent(tmp_path):
    cand = make_candidate(name="twice-fact", index_hook="hook")
    apply_memory_candidate(cand, tmp_path)

    supersede_memory("twice-fact", tmp_path, superseded_by=None, note="first")
    supersede_memory("twice-fact", tmp_path, superseded_by=None, note="second")
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    line = [ln for ln in index.split("\n") if "twice-fact.md" in ln][0]
    # Only one [SUPERSEDED] prefix, not stacked.
    assert line.count("[SUPERSEDED]") == 1


def test_supersede_missing_file_invalid(tmp_path):
    result = supersede_memory(
        "nope-fact", tmp_path, superseded_by=None, note="x"
    )
    assert result.status == "invalid"
    assert "does not exist" in result.reason


def test_supersede_empty_note_invalid(tmp_path):
    cand = make_candidate(name="note-fact")
    apply_memory_candidate(cand, tmp_path)
    result = supersede_memory(
        "note-fact", tmp_path, superseded_by=None, note="  "
    )
    assert result.status == "invalid"


def test_supersede_preserves_frontmatter_keys(tmp_path):
    cand = make_candidate(name="keep-fact")
    apply_memory_candidate(cand, tmp_path)
    supersede_memory(
        "keep-fact", tmp_path, superseded_by="new", note="moved"
    )
    text = (tmp_path / "keep-fact.md").read_text(encoding="utf-8")
    # Original frontmatter intact.
    assert 'name: "keep-fact"' in text
    assert "node_type: memory" in text
    assert "metadata:" in text
    # Still a valid frontmatter block.
    assert text.startswith("---\n")
    assert text.count("---") >= 2


def test_supersede_bad_name_invalid(tmp_path):
    result = supersede_memory(
        "Bad Name", tmp_path, superseded_by=None, note="x"
    )
    assert result.status == "invalid"


# ---------------------------------------------------------------------------
# misc: type set lock + render purity
# ---------------------------------------------------------------------------


def test_allowed_mem_types_locked():
    assert set(ALLOWED_MEM_TYPES) == {
        "user",
        "feedback",
        "project",
        "reference",
    }


def test_render_is_pure_no_disk(tmp_path):
    """render_memory_file/render_index_line never touch the filesystem."""
    cand = make_candidate(name="pure-fact")
    render_memory_file(cand)
    render_index_line(cand)
    assert list(tmp_path.iterdir()) == []


def test_frontmatter_well_formed_yaml_block():
    cand = make_candidate(name="yaml-fact")
    text = render_memory_file(cand)
    # At least two frontmatter delimiters before the body.
    parts = text.split("---")
    assert len(parts) >= 3
    fm = parts[1]
    # Each non-empty frontmatter line is key: value or an indented child.
    for line in fm.strip().split("\n"):
        if not line.strip():
            continue
        assert re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*:", line), line


# ---------------------------------------------------------------------------
# CLI entrypoint (memory_apply.main) — thin wrapper over the pure logic above.
# ---------------------------------------------------------------------------

import json as _json

from mini_dudeai.memory_apply import candidate_from_dict, main


def _candidate_dict(**overrides):
    """A valid reference-type candidate as the CLI's JSON wire shape."""
    prov = dict(
        origin="claude", host="moc1", session_id=None,
        confidence="high", verified=True,
    )
    prov.update(overrides.pop("provenance", {}))
    base = dict(
        name="cli-fact",
        description="A one line description.",
        mem_type="reference",
        body="Body of the CLI fact with detail.\n",
        index_title="CLI Fact",
        index_hook="the hook text",
        links=[],
        provenance=prov,
    )
    base.update(overrides)
    return base


def _write_candidate_json(tmp_path, **overrides):
    p = tmp_path / "cand.json"
    p.write_text(_json.dumps(_candidate_dict(**overrides)), encoding="utf-8")
    return p


def test_candidate_from_dict_roundtrips_provenance(tmp_path):
    cand = candidate_from_dict(_candidate_dict(name="rt-fact"))
    assert cand.name == "rt-fact"
    assert cand.provenance.origin == "claude"
    assert cand.provenance.verified is True


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    mem = tmp_path / "memory"
    mem.mkdir()
    cand = _write_candidate_json(tmp_path, name="dry-fact")

    rc = main(["--candidate", str(cand), "--dir", str(mem), "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "dry_run" in out
    assert "would write" in out
    # Nothing materialized.
    assert not (mem / "dry-fact.md").exists()
    assert not (mem / "MEMORY.md").exists()


def test_cli_real_run_writes_file_and_index(tmp_path, capsys):
    mem = tmp_path / "memory"
    mem.mkdir()
    cand = _write_candidate_json(tmp_path, name="written-fact")

    rc = main(["--candidate", str(cand), "--dir", str(mem)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "written" in out
    target = mem / "written-fact.md"
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "origin: \"claude\"" in body
    assert "verified: true" in body
    index = (mem / "MEMORY.md").read_text(encoding="utf-8")
    assert "](written-fact.md)" in index


def test_cli_provenance_gate_rejects_mini_verified(tmp_path, capsys):
    """The load-bearing trust boundary: mini-origin verified=True is barred,
    and the CLI surfaces it as a non-zero exit with nothing written."""
    mem = tmp_path / "memory"
    mem.mkdir()
    cand = _write_candidate_json(
        tmp_path, name="rot-fact",
        provenance={"origin": "mini", "verified": True},
    )

    rc = main(["--candidate", str(cand), "--dir", str(mem)])
    err = capsys.readouterr().err

    assert rc == 1
    assert "provenance gate" in err
    assert not (mem / "rot-fact.md").exists()


def test_cli_mini_origin_unverified_is_allowed(tmp_path, capsys):
    """mini may PROPOSE (verified=False); only verified=True is barred."""
    mem = tmp_path / "memory"
    mem.mkdir()
    cand = _write_candidate_json(
        tmp_path, name="mini-proposal",
        provenance={"origin": "mini", "verified": False},
    )

    rc = main(["--candidate", str(cand), "--dir", str(mem)])
    assert rc == 0
    assert (mem / "mini-proposal.md").exists()


def test_cli_malformed_candidate_json_is_invalid(tmp_path, capsys):
    mem = tmp_path / "memory"
    mem.mkdir()
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")

    rc = main(["--candidate", str(bad), "--dir", str(mem)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "invalid" in err.lower()


def test_cli_missing_required_field_is_invalid(tmp_path, capsys):
    mem = tmp_path / "memory"
    mem.mkdir()
    d = _candidate_dict()
    del d["body"]
    p = tmp_path / "nobody.json"
    p.write_text(_json.dumps(d), encoding="utf-8")

    rc = main(["--candidate", str(p), "--dir", str(mem)])
    assert rc == 1


def test_cli_supersede_marks_existing(tmp_path, capsys):
    mem = tmp_path / "memory"
    mem.mkdir()
    # First write a real memory via the CLI, then supersede it.
    cand = _write_candidate_json(tmp_path, name="old-fact")
    assert main(["--candidate", str(cand), "--dir", str(mem)]) == 0
    capsys.readouterr()  # drain

    rc = main([
        "--supersede", "old-fact", "--dir", str(mem),
        "--note", "replaced by newer finding", "--date", "2026-05-30",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    body = (mem / "old-fact.md").read_text(encoding="utf-8")
    assert "SUPERSEDED" in body
    assert "replaced by newer finding" in body


def test_cli_supersede_requires_note(tmp_path, capsys):
    mem = tmp_path / "memory"
    mem.mkdir()
    rc = main(["--supersede", "whatever", "--dir", str(mem)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "note" in err.lower()


# ---------------------------------------------------------------------------
# PART A1: MEMORY.md size guard (Defect #2 — index over-limit silent truncation)
# ---------------------------------------------------------------------------


def _seed_index_over(tmp_path, limit):
    """Write a MEMORY.md whose size exceeds ``limit`` bytes."""
    index = tmp_path / "MEMORY.md"
    # One byte per 'x' plus the newline — pad past the cap.
    index.write_text("x" * (limit + 50) + "\n", encoding="utf-8")
    return index


def test_check_index_size_none_when_under_cap(tmp_path):
    (tmp_path / "MEMORY.md").write_text("- [a](a.md) — hook\n", encoding="utf-8")
    assert check_index_size(tmp_path, size_limit=10_000) is None


def test_check_index_size_none_when_index_absent(tmp_path):
    # No MEMORY.md at all → size 0 → no warning.
    assert check_index_size(tmp_path) is None


def test_check_index_size_warns_over_cap(tmp_path):
    _seed_index_over(tmp_path, 100)
    warning = check_index_size(tmp_path, size_limit=100)
    assert warning is not None
    assert "over the" in warning
    assert "MEMORY_ARCHIVE.md" in warning


def test_check_index_size_uses_env_override(tmp_path, monkeypatch):
    _seed_index_over(tmp_path, 80)
    monkeypatch.setenv(MEMORY_INDEX_LIMIT_ENV, "80")
    # No explicit arg → env value (80) governs → over cap → warns.
    assert check_index_size(tmp_path) is not None


def test_check_index_size_explicit_arg_beats_env(tmp_path, monkeypatch):
    # Index is ~150 bytes; env says 80 (would warn) but explicit arg 10_000
    # (would not) wins.
    _seed_index_over(tmp_path, 80)
    monkeypatch.setenv(MEMORY_INDEX_LIMIT_ENV, "80")
    assert check_index_size(tmp_path, size_limit=10_000) is None


def test_check_index_size_bad_env_falls_through_to_default(tmp_path, monkeypatch):
    # A garbage env value must NOT disable the guard — fall through to default.
    _seed_index_over(tmp_path, MEMORY_INDEX_SOFT_LIMIT_BYTES)
    monkeypatch.setenv(MEMORY_INDEX_LIMIT_ENV, "not-an-int")
    assert check_index_size(tmp_path) is not None


def test_apply_warns_when_index_over_cap(tmp_path):
    # Pre-seed a near-cap index so the append crosses a tiny cap.
    (tmp_path / "MEMORY.md").write_text("- [a](a.md) — hook\n", encoding="utf-8")
    cand = make_candidate(name="overcap-fact")
    result = apply_memory_candidate(cand, tmp_path, size_limit=5)
    # Append still succeeds — never blocked.
    assert result.status == "written"
    assert (tmp_path / "overcap-fact.md").exists()
    # ...but the warning fires.
    assert result.index_warning is not None
    assert "soft cap" in result.index_warning


def test_apply_no_warning_under_cap(tmp_path):
    cand = make_candidate(name="undercap-fact")
    result = apply_memory_candidate(cand, tmp_path, size_limit=10_000)
    assert result.status == "written"
    assert result.index_warning is None


def test_apply_never_blocks_append_on_over_cap(tmp_path):
    """Losing a memory is worse than an over-cap index — append must persist."""
    cand = make_candidate(name="persist-fact")
    result = apply_memory_candidate(cand, tmp_path, size_limit=1)
    assert result.status == "written"
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "](persist-fact.md)" in index  # the line is really there
    assert result.index_warning is not None


def test_apply_logs_warning_at_warning_level(tmp_path, caplog):
    import logging
    cand = make_candidate(name="logged-fact")
    with caplog.at_level(logging.WARNING, logger="mini_dudeai.memory_apply"):
        apply_memory_candidate(cand, tmp_path, size_limit=1)
    assert any("soft cap" in rec.message for rec in caplog.records)


def test_apply_dry_run_surfaces_preexisting_over_cap(tmp_path):
    _seed_index_over(tmp_path, 50)
    cand = make_candidate(name="dry-overcap")
    result = apply_memory_candidate(
        cand, tmp_path, dry_run=True, size_limit=50
    )
    assert result.status == "dry_run"
    assert result.index_warning is not None
    # Dry run still touches nothing.
    assert not (tmp_path / "dry-overcap.md").exists()


# ---------------------------------------------------------------------------
# PART A2: demote_memory (mechanize the move-to-archive discipline)
# ---------------------------------------------------------------------------


def test_demote_moves_pointer_to_archive(tmp_path):
    cand = make_candidate(name="stale-fact", index_hook="the stale hook")
    apply_memory_candidate(cand, tmp_path)

    result = demote_memory("stale-fact", tmp_path)
    assert result.status == "demoted"
    assert result.index_updated is True

    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    archive = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")

    # Pointer line moved out of the index...
    assert "- [Example Fact](stale-fact.md)" not in index
    # ...into the archive verbatim...
    assert "](stale-fact.md) — the stale hook" in archive
    # ...with a breadcrumb left behind in the index.
    assert "demoted to MEMORY_ARCHIVE.md: stale-fact.md" in index


def test_demote_never_deletes_canonical_file(tmp_path):
    cand = make_candidate(name="keepfile-fact")
    apply_memory_candidate(cand, tmp_path)
    demote_memory("keepfile-fact", tmp_path)
    # The canonical .md is untouched.
    assert (tmp_path / "keepfile-fact.md").exists()


def test_demote_is_idempotent(tmp_path):
    cand = make_candidate(name="idem-demote")
    apply_memory_candidate(cand, tmp_path)

    first = demote_memory("idem-demote", tmp_path)
    assert first.status == "demoted"
    archive_after_first = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")

    second = demote_memory("idem-demote", tmp_path)
    assert second.status == "demoted"
    assert second.index_updated is False  # nothing more to move
    archive_after_second = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")

    # Archive not duplicated by the second call.
    assert archive_after_first == archive_after_second
    assert archive_after_second.count("](idem-demote.md)") == 1


def test_demote_loses_no_content(tmp_path):
    """The exact pointer text survives the move (no information lost)."""
    cand = make_candidate(name="content-fact", index_hook="precise hook text")
    apply_memory_candidate(cand, tmp_path)
    original_line = [
        ln for ln in (tmp_path / "MEMORY.md").read_text(
            encoding="utf-8"
        ).split("\n") if "content-fact.md" in ln
    ][0]

    demote_memory("content-fact", tmp_path)
    archive = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert original_line in archive


def test_demote_brings_index_back_under_cap(tmp_path):
    """The operator tool actually shrinks the index (the whole point)."""
    # Write several real entries, then demote one and confirm the index shrank.
    for i in range(3):
        apply_memory_candidate(make_candidate(name=f"f{i}-fact"), tmp_path)
    before = (tmp_path / "MEMORY.md").stat().st_size
    demote_memory("f0-fact", tmp_path)
    after = (tmp_path / "MEMORY.md").stat().st_size
    assert after < before


def test_demote_accepts_underscore_filename_stem(tmp_path):
    """Regression: the REAL store is underscore-named (project_session_handoff_*).

    demote validated `name` against kebab-case (_KEBAB_RE, hyphens only), so it
    rejected every underscore entry as 'not kebab-case' — unusable on the live
    index (caught 2026-06-09 dogfooding it). The earlier tests all used hyphen
    names, so they passed green over a broken tool. This pins the real shape.
    """
    name = "project_session_handoff_2026_06_09"
    (tmp_path / f"{name}.md").write_text("# body\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(
        f"# Index\n\n- [Old Handoff]({name}.md) — a hook\n", encoding="utf-8")

    result = demote_memory(name, tmp_path)
    assert result.status == "demoted", result.reason  # NOT 'invalid'
    assert result.index_updated is True
    archive = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert f"]({name}.md) — a hook" in archive


def test_demote_breadcrumb_false_removes_line_cleanly(tmp_path):
    """breadcrumb=False removes the pointer outright (no breadcrumb clutter) —

    the mode for bulk demote passes on a load-capped index, where accumulated
    breadcrumbs would themselves re-bloat what loads every session.
    """
    name = "bulk_demote_entry_2026_06_09"
    (tmp_path / f"{name}.md").write_text("# body\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(
        f"# Index\n\n- [Entry]({name}.md) — hook\n"
        f"- [Keep](keep-fact.md) — keep\n", encoding="utf-8")

    result = demote_memory(name, tmp_path, breadcrumb=False)
    assert result.status == "demoted"
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    archive = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert name not in index                       # line fully gone
    assert "demoted to MEMORY_ARCHIVE.md" not in index   # no breadcrumb clutter
    assert "](keep-fact.md)" in index              # siblings preserved
    assert f"]({name}.md) — hook" in archive       # moved, not lost

    # Idempotent in clean mode — detected via the archive, not a breadcrumb.
    second = demote_memory(name, tmp_path, breadcrumb=False)
    assert second.status == "demoted"
    assert second.index_updated is False


def test_demote_missing_pointer_invalid(tmp_path):
    # No MEMORY.md / no such pointer → invalid, nothing written.
    result = demote_memory("nope-fact", tmp_path)
    assert result.status == "invalid"
    assert "nothing to demote" in result.reason
    assert not (tmp_path / ARCHIVE_FILENAME).exists()


def test_demote_bad_name_invalid(tmp_path):
    # A space (or any non [a-z0-9_-]) is not a valid file stem.
    result = demote_memory("Bad Name", tmp_path)
    assert result.status == "invalid"
    assert "memory-file stem" in result.reason


def test_demote_appends_to_existing_archive(tmp_path):
    a = make_candidate(name="alpha-fact")
    b = make_candidate(name="beta-fact")
    apply_memory_candidate(a, tmp_path)
    apply_memory_candidate(b, tmp_path)

    demote_memory("alpha-fact", tmp_path)
    demote_memory("beta-fact", tmp_path)

    archive = (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert "](alpha-fact.md)" in archive
    assert "](beta-fact.md)" in archive
    # Header written once, not per demote.
    assert archive.count("# MeshForge Memory Archive") == 1


def test_demote_writes_atomically_no_tmp_left(tmp_path):
    cand = make_candidate(name="atomic-demote")
    apply_memory_candidate(cand, tmp_path)
    demote_memory("atomic-demote", tmp_path)
    # No stray .tmp files left from the atomic write.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_cli_demote_moves_pointer(tmp_path, capsys):
    from mini_dudeai.memory_apply import main as _main

    mem = tmp_path / "memory"
    mem.mkdir()
    cand = _write_candidate_json(tmp_path, name="cli-demote-fact")
    assert _main(["--candidate", str(cand), "--dir", str(mem)]) == 0
    capsys.readouterr()  # drain

    rc = _main(["--demote", "cli-demote-fact", "--dir", str(mem)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "demoted" in out
    archive = (mem / ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert "](cli-demote-fact.md)" in archive
