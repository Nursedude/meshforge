"""T1 (second-brain taxonomy, 2026-07-26): read the coverage block for BLINDNESS.

`note_disposition` -> `build_coverage` -> `watchdog.json.coverage` has been a
complete, per-class, per-tick, fail-dark record since fleet-truth Phase 0, and
all 58 probes call it. Nothing ever read it to ask *which detectors cannot
currently see*.

The distinction that makes this scoreable without ranking detectors by fire
count: **fire count measures the phenomenon, disposition measures the
detector.** A probe reading `indeterminate` for three weeks has been blind for
three weeks whether or not the phenomenon occurred — so this never rewards a
noisy probe and never punishes a quiet wedge guard.

Disjoint from the Pri-2 hold, deliberately:

    blind AND signalling  -> Pri-2 hold (signal persists, no false recovery)
    blind AND quiet       -> THIS (nothing to hold; silence reads as health)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mini_dudeai.presets.meshforge_fleet import (  # noqa: E402
    _coverage_blind_extractor,
)


def _cov(**classes) -> dict:
    return {"coverage": {k: v for k, v in classes.items()}}


class TestBlindDetectorProjection:

    def test_indeterminate_class_is_emitted(self):
        out = _coverage_blind_extractor(_cov(
            kernel_reboot_pending={
                "disp": "indeterminate",
                "reason": "no same-flavor kernel entries readable under modules dir",
            },
        ))
        assert len(out) == 1
        assert out[0]["subject"] == "kernel_reboot_pending"
        assert out[0]["disp"] == "indeterminate"
        assert "modules dir" in out[0]["detail"], "the reason must survive"

    def test_unknown_class_is_emitted(self):
        out = _coverage_blind_extractor(_cov(x={"disp": "unknown"}))
        assert [c["subject"] for c in out] == ["x"]

    def test_clean_inert_and_active_are_not_blind(self):
        """The whole point: a probe that RAN is not a blind probe.

        `inert` especially — a legitimately-absent organ is not a failure, and
        scoring it would make the coverage score itself dishonest.
        """
        out = _coverage_blind_extractor(_cov(
            a={"disp": "clean"}, b={"disp": "inert"}, c={"disp": "active"},
        ))
        assert out == []

    def test_mixed_map_emits_only_the_blind_ones(self):
        out = _coverage_blind_extractor(_cov(
            a={"disp": "clean"},
            b={"disp": "indeterminate", "reason": "r"},
            c={"disp": "inert"},
            d={"disp": "unknown"},
        ))
        assert sorted(c["subject"] for c in out) == ["b", "d"]

    def test_missing_reason_does_not_crash_or_fabricate(self):
        out = _coverage_blind_extractor(_cov(a={"disp": "indeterminate"}))
        assert out[0]["detail"].endswith("no reason reported")

    def test_output_is_deterministic_order(self):
        """Stable subjects keep the annotation log diffable across ticks."""
        out = _coverage_blind_extractor(_cov(
            z={"disp": "unknown"}, a={"disp": "unknown"}, m={"disp": "unknown"},
        ))
        assert [c["subject"] for c in out] == ["a", "m", "z"]


class TestPartialBlindnessProjection:
    """2026-08-13 Pri-1: a probe that judged only n of its m enrolled subjects
    notes `clean` about the judged subset, with the shortfall in the
    STRUCTURED coverage field. Before this leg the shortfall survived only as
    prose inside the reason string, so a box permanently 50% blind escalated
    identically to a fully-observed healthy one — the same laundering one
    level up (a long-running detector_blind is a finding, not furniture)."""

    def test_clean_with_shortfall_is_emitted(self):
        out = _coverage_blind_extractor(_cov(
            user_timer_unit_failing={
                "disp": "clean",
                "reason": ("2 of 4 enrolled timer(s) judged; no failing job. "
                           "Unjudged: meshanchor-map-restart.service"),
                "coverage": {"judged": 2, "enrolled": 4},
            },
        ))
        assert len(out) == 1
        assert out[0]["subject"] == "user_timer_unit_failing"
        assert "judged 2 of 4" in out[0]["detail"]
        assert "meshanchor-map-restart" in out[0]["detail"], \
            "the unjudged names must survive into the condition"

    def test_full_coverage_clean_is_not_blind(self):
        out = _coverage_blind_extractor(_cov(
            a={"disp": "clean", "coverage": {"judged": 4, "enrolled": 4}},
        ))
        assert out == []

    def test_indeterminate_with_coverage_emits_exactly_one(self):
        """Full blindness owns the class — the partial leg must not add a
        second condition for the same subject."""
        out = _coverage_blind_extractor(_cov(
            a={"disp": "indeterminate", "reason": "r",
               "coverage": {"judged": 0, "enrolled": 4}},
        ))
        assert len(out) == 1
        assert out[0]["disp"] == "indeterminate"

    def test_inert_with_shortfall_is_not_blind(self):
        """An organ absent by design is not a half-blind detector."""
        out = _coverage_blind_extractor(_cov(
            a={"disp": "inert", "coverage": {"judged": 0, "enrolled": 2}},
        ))
        assert out == []

    def test_malformed_coverage_never_fabricates(self):
        """honest_failure_modes #1 at the reader: a writer bug must not
        become a confident blindness claim. Note bool IS an int subclass."""
        for cov in (
            {"judged": "2", "enrolled": 4},
            {"judged": 2, "enrolled": "4"},
            {"judged": -1, "enrolled": 4},
            {"judged": 5, "enrolled": 4},      # over-full is a writer bug
            {"judged": False, "enrolled": True},
            {"judged": 2},
            "not-a-dict",
            None,
        ):
            out = _coverage_blind_extractor(_cov(
                a={"disp": "clean", "coverage": cov}))
            assert out == [], f"fabricated a condition from {cov!r}"


class TestDegradedInputNeverFabricates:
    """honest_failure_modes #1 — a degraded input must not become a confident
    claim. A torn or legacy writer must not read as "all 57 detectors blind"."""

    def test_absent_coverage_block_emits_nothing(self):
        assert _coverage_blind_extractor({"signals": []}) == []

    def test_none_payload_emits_nothing(self):
        assert _coverage_blind_extractor(None) == []

    def test_non_dict_coverage_emits_nothing(self):
        assert _coverage_blind_extractor({"coverage": ["not", "a", "map"]}) == []
        assert _coverage_blind_extractor({"coverage": "broken"}) == []

    def test_malformed_entry_is_skipped_not_guessed(self):
        out = _coverage_blind_extractor({"coverage": {
            "good": {"disp": "indeterminate", "reason": "r"},
            "bad": "not-a-dict",
            "also_bad": None,
        }})
        assert [c["subject"] for c in out] == ["good"]

    def test_entry_without_disp_is_not_blind(self):
        """An entry with no disposition is malformed, not blind — guessing
        would manufacture the exact false page this rule exists to avoid."""
        assert _coverage_blind_extractor({"coverage": {"a": {"reason": "x"}}}) == []


class TestWiredIntoTheFleetPreset:

    def test_preset_registers_the_coverage_source(self, tmp_path, monkeypatch):
        from mini_dudeai.presets import meshforge_fleet as mf

        monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
        engine = mf.build_engine(
            home=str(tmp_path),
            watchdog_path=str(tmp_path / "watchdog.json"),
            enable_federation=False,
            enable_digest=False,
            enable_boot_health=False,
            enable_watchdog=True,
        )
        names = {s.name for s in engine.sources}
        assert "watchdog" in names, "the signal projection must remain"
        assert "watchdog_coverage" in names, "the blindness projection is wired"
