"""Tests for scripts/review_provenance_check.py — the upshift-witness guard.

Born with the guard (2026-07-14, Opus executing the Fable design
`.claude/plans/upshift_witness_design.md`). RED-first: the integration cases
build a real temp git repo so the leg-1/leg-2/leg-3 behavior is exercised
end-to-end, and each asserts BOTH the blocking and the passing side so a
regression that neuters the gate fails a test.

Constants (frontier prefixes, claim/marker patterns, the nudge threshold) are
imported from the module — the tests never re-hardcode them (honest_failure_modes
#5: the vocabulary has ONE home).
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import review_provenance_check as rpc  # noqa: E402


# --------------------------------------------------------------------------- #
# Git fixture helper — a real, isolated repo (deterministic, no network).      #
# --------------------------------------------------------------------------- #

def _run(repo, *args):
    subprocess.run(args, cwd=repo, check=True, timeout=30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _init_repo(repo):
    _run(repo, "git", "init", "-q")
    _run(repo, "git", "config", "user.email", "t@example.com")
    _run(repo, "git", "config", "user.name", "T")
    _run(repo, "git", "config", "commit.gpgsign", "false")


def _write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _commit(repo, message):
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-q", "-m", message)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True, timeout=30)
    return out.stdout.strip()


# A minimal but shape-accurate provenance file (completed table + worklist).
PROV_HEADER = (
    "# Review provenance ledger\n\n"
    "| Date | Scope (range + paths) | Mechanism | Fix commits | Residuals |\n"
    "|------|----------------------|-----------|-------------|-----------|\n"
)
WORKLIST = (
    "\n## Frontier worklist — queued upshift ranges\n\n"
    "| Pri | Scope | Why it's frontier-shaped |\n"
    "|-----|-------|--------------------------|\n"
    "| 1 | Opus interregnum review | written under Opus |\n"
)


class TestPureFrontierModel(unittest.TestCase):
    def test_fable_is_frontier(self):
        self.assertIs(rpc.is_frontier_model("claude-fable-5"), True)

    def test_opus_is_not_frontier(self):
        self.assertIs(rpc.is_frontier_model("claude-opus-4-8[1m]"), False)

    def test_unknown_is_none_not_false(self):
        # Unobservable ≠ violation (honest_failure_modes #2) — must be None,
        # so leg 2 warns instead of blocking-as-non-frontier.
        self.assertIsNone(rpc.is_frontier_model(None))
        self.assertIsNone(rpc.is_frontier_model(""))

    def test_prefix_list_is_the_single_source(self):
        # Guards the "shared, not hardcoded twice" invariant.
        self.assertIn("claude-fable-", rpc.FRONTIER_MODEL_PREFIXES)


class TestNewestLedgerModel(unittest.TestCase):
    def test_picks_max_ts_claim(self):
        events = [
            {"kind": "claim", "ts": 10, "model_id": "claude-fable-5"},
            {"kind": "claim", "ts": 30, "model_id": "claude-opus-4-8[1m]"},
            {"kind": "claim", "ts": 20, "model_id": "claude-haiku-4-5"},
            {"kind": "verdict", "claim_id": "x", "ts": 99, "outcome": "held"},
        ]
        self.assertEqual(rpc.newest_ledger_model_id(events), "claude-opus-4-8[1m]")

    def test_no_claims_is_none(self):
        self.assertIsNone(rpc.newest_ledger_model_id(
            [{"kind": "verdict", "ts": 5, "claim_id": "x", "outcome": "held"}]))

    def test_ignores_non_numeric_ts(self):
        events = [{"kind": "claim", "ts": "bad", "model_id": "x"},
                  {"kind": "claim", "ts": 5, "model_id": "claude-fable-5"}]
        self.assertEqual(rpc.newest_ledger_model_id(events), "claude-fable-5")


class TestCommitClaimsReview(unittest.TestCase):
    def test_canonical_selfreview_subject(self):
        # The real 7282dad4 subject — must be caught by multiple patterns.
        msg = ("fix: self-review of the perf arc — 11 findings from the "
               "6-angle adversarial pass")
        self.assertTrue(rpc.commit_claims_review(msg))

    def test_plain_fix_is_not_a_review_claim(self):
        self.assertFalse(rpc.commit_claims_review("fix: bounded RPC timeout"))

    def test_bare_reviewed_word_excluded_by_design(self):
        # Precision bias — "reviewed the docs" is not a review-provenance claim.
        self.assertFalse(rpc.commit_claims_review("docs: reviewed the README"))

    def test_code_review_and_findings(self):
        self.assertTrue(rpc.commit_claims_review("chore: /code-review high"))
        self.assertTrue(rpc.commit_claims_review("fix: 3 findings from pass"))


class TestClaimDetectionFalsePositives(unittest.TestCase):
    """Three false positives measured on 2026-07-25 — each cost a real push.

    The gate's own docstring says it biases to precision because a false block
    costs a real push. These are the cases where it didn't. The gate reads
    tokens, not meaning; these tests pin the two places that matters most.
    """

    def test_iso_date_before_the_keyword_is_not_a_count(self):
        """"2026-07-25 findings" — the DATE's day number matched the
        numeric-count pattern. An ordinary dated summary line tripped it."""
        self.assertFalse(rpc.commit_claims_review(
            "docs: MF012 demotion pass + the two 2026-07-25 findings"))

    def test_iso_date_across_a_newline_is_not_a_count(self):
        r"""\s spans newlines, so a wrapped date+keyword matched too."""
        self.assertFalse(rpc.commit_claims_review(
            "docs: notes\n\nthe DATE in \"2026-07-25\nfindings\" tripped it"))

    def test_denying_a_review_is_not_claiming_one(self):
        """The honest disclaimer was itself unpublishable: a sentence DENYING
        a pass happened matched as strongly as one asserting it."""
        self.assertFalse(rpc.commit_claims_review(
            "docs: reworded — no review pass was run here, so a provenance "
            "row would have been a false witness"))

    def test_other_negations_are_honoured(self):
        for msg in (
            "docs: this was not a code-review, just a typo fix",
            "docs: shipped without a self-review",
            "chore: never ran an adversarial pass on this",
        ):
            with self.subTest(msg=msg):
                self.assertFalse(rpc.commit_claims_review(msg))


class TestClaimDetectionStillCatchesRealClaims(unittest.TestCase):
    """The precision fixes must not open a hole — a real claim still blocks."""

    def test_real_counts_still_claim(self):
        for msg in ("fix: 3 findings from the pass",
                    "fix: 11 findings, all confirmed",
                    "fix: 6-finder sweep",
                    "fix: 6-angle adversarial pass"):
            with self.subTest(msg=msg):
                self.assertTrue(rpc.commit_claims_review(msg))

    def test_wrapped_real_count_still_claims(self):
        """Precision must not cost a genuine wrapped claim — a commit body
        wrapped at 72 chars can split the number from the keyword."""
        self.assertTrue(rpc.commit_claims_review(
            "fix: the arc\n\nthe pass produced 8\nfindings, all confirmed"))

    def test_negation_does_not_leak_past_a_sentence_boundary(self):
        """A negator in a PRIOR clause must not suppress a later real claim."""
        self.assertTrue(rpc.commit_claims_review(
            "fix: this was not a quick patch; a full code-review found bugs"))
        self.assertTrue(rpc.commit_claims_review(
            "fix: no shortcuts taken. self-review of the whole arc"))

    def test_dated_row_with_a_real_count_still_claims(self):
        """A date elsewhere must not immunise a genuine count."""
        self.assertTrue(rpc.commit_claims_review(
            "docs: 2026-07-25 audit — 8 findings, all confirmed"))


class TestHyphenatedLabelVsAttributedFinding(unittest.TestCase):
    """Both from REAL history — the only two commits in 800 whose verdict the
    2026-07-25 precision fix changed. They look alike and must not be treated
    alike, so the discriminator is pinned here.

    The numeric guard stops a hyphenated LABEL ("pri-1", "thread-3") posing as
    a count. That is right for both — but one of them attributes its finding to
    a REVIEW and so must still demand a witness row.
    """

    def test_finding_attributed_to_a_review_still_claims(self):
        """6afe194c — genuinely review-derived; it has a provenance row."""
        self.assertTrue(rpc.commit_claims_review(
            "fix: gateway_heartbeat peer-liveness on monotonic clock (Pri-1)\n\n"
            "The Pri-1 finding from the 2026-07-23 gateway review. _check_peers "
            "aged peer liveness on wall-clock time.time()."))

    def test_label_only_finding_with_no_review_does_not_claim(self):
        """7867f9f5 — 'Thread-3 finding' is a scope-doc thread, not a review.
        Caught before only because '3' read as a count."""
        self.assertFalse(rpc.commit_claims_review(
            "docs(provisioner): config-delta assertion-upgrade DONE\n\n"
            "Records the Thread-3 finding: the map defaults are code-baked / "
            "deployment-specific (not operator-settable)."))

    def test_attribution_does_not_reach_across_a_sentence(self):
        """Same-clause only — an unrelated later mention must not attribute."""
        self.assertFalse(rpc.commit_claims_review(
            "docs: records the thread-3 finding. separately, the review "
            "cadence doc moved"))


class TestProvenanceMentionsSha(unittest.TestCase):
    def test_short_sha_prefix_matches_full(self):
        full = "7282dad4835d4fd09598d303492cf7c397d0561e"
        self.assertTrue(rpc.provenance_mentions_sha("... fix `7282dad4` ...", full))

    def test_range_endpoints_are_tokens(self):
        full = "8a6d61c1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.assertTrue(rpc.provenance_mentions_sha("`bd911479..8a6d61c1`", full))

    def test_absent_sha_not_matched(self):
        self.assertFalse(rpc.provenance_mentions_sha("no shas here", "deadbeef" * 5))

    def test_empty_inputs(self):
        self.assertFalse(rpc.provenance_mentions_sha("", "abc1234"))
        self.assertFalse(rpc.provenance_mentions_sha("abc1234", ""))


class TestRowParsing(unittest.TestCase):
    def test_completed_rows_discriminated_by_date(self):
        text = (PROV_HEADER
                + "| 2026-07-13 | scope | /code-review high | `abc1234` | none |\n"
                + WORKLIST)
        rows = rpc.parse_completed_rows(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "2026-07-13")
        self.assertIn("code-review", rows[0][1])

    def test_worklist_and_separator_rows_ignored(self):
        # Only the dated completed row parses — not the Pri worklist row,
        # header, or |---| separator.
        text = PROV_HEADER + WORKLIST
        self.assertEqual(rpc.parse_completed_rows(text), [])

    def test_frontier_tier_markers(self):
        self.assertTrue(rpc.row_claims_frontier_tier("/code-review ultra ×2"))
        self.assertTrue(rpc.row_claims_frontier_tier("frontier inline deep review"))
        self.assertTrue(rpc.row_claims_frontier_tier("Fable adversarial pass"))
        self.assertFalse(rpc.row_claims_frontier_tier("/code-review high 8-finder"))


class TestEvaluateLeg2Pure(unittest.TestCase):
    def _head_with_frontier_row(self):
        return (PROV_HEADER
                + "| 2026-07-14 | scope | frontier inline deep review | `abc` | x |\n")

    def test_nonfrontier_stamping_frontier_row_blocks(self):
        rows, warn = rpc.evaluate_leg2(self._head_with_frontier_row(),
                                       PROV_HEADER, session_is_frontier=False)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(warn)

    def test_frontier_session_may_stamp(self):
        rows, warn = rpc.evaluate_leg2(self._head_with_frontier_row(),
                                       PROV_HEADER, session_is_frontier=True)
        self.assertEqual(rows, [])
        self.assertIsNone(warn)

    def test_unknown_model_warns_not_blocks(self):
        rows, warn = rpc.evaluate_leg2(self._head_with_frontier_row(),
                                       PROV_HEADER, session_is_frontier=None)
        self.assertEqual(rows, [])
        self.assertEqual(warn, "unknown-model")

    def test_non_frontier_row_is_fine(self):
        head = (PROV_HEADER
                + "| 2026-07-14 | scope | /code-review high 8-finder | `abc` | x |\n")
        rows, warn = rpc.evaluate_leg2(head, PROV_HEADER, session_is_frontier=False)
        self.assertEqual(rows, [])

    def test_grandfathered_row_unchanged_not_flagged(self):
        # A frontier row that already existed at base is NOT new → not blocked.
        existing = (PROV_HEADER
                    + "| 2026-07-01 | scope | frontier deep review | `abc` | x |\n")
        rows, warn = rpc.evaluate_leg2(existing, existing, session_is_frontier=False)
        self.assertEqual(rows, [])


# --------------------------------------------------------------------------- #
# Integration — real temp git repos exercised through main()/run().           #
# --------------------------------------------------------------------------- #

class TestLeg1Integration(unittest.TestCase):
    def test_review_claim_without_row_blocks_then_passes_with_row(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, "README.md", "base\n")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")

            # A commit that CLAIMS a review but adds no provenance row.
            _write(repo, "src/x.py", "x = 1\n")
            sha = _commit(repo, "fix: self-review — 3 findings adversarial pass")

            rng = f"{base}..HEAD"
            code, out, warn = rpc.run(repo, rng, base, ledger_path="/nonexistent",
                                      witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 1, f"leg1 should block; out={out} warn={warn}")
            self.assertIn("leg 1", "\n".join(out))

            # Now add a witness row naming the SHA (rides the same push range).
            _write(repo, rpc.PROVENANCE_REL,
                   PROV_HEADER
                   + f"| 2026-07-14 | src/x.py | /code-review | `{sha[:9]}` | none |\n"
                   + WORKLIST)
            _commit(repo, "docs: provenance row for the self-review pass")
            code2, out2, _ = rpc.run(repo, f"{base}..HEAD", base,
                                     ledger_path="/nonexistent",
                                     witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code2, 0, f"should pass with the row; out={out2}")

    def test_commit_that_adds_the_row_is_exempt(self):
        # A single commit that BOTH claims a review AND edits the provenance
        # file records provenance — it must not false-block on its own SHA.
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, "README.md", "base\n")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")

            _write(repo, "src/y.py", "y = 2\n")
            _write(repo, rpc.PROVENANCE_REL,
                   PROV_HEADER
                   + "| 2026-07-14 | src/y.py | self-review | `pending` | none |\n"
                   + WORKLIST)
            _commit(repo, "fix: self-review of y — 2 findings; provenance row added")

            code, out, _ = rpc.run(repo, f"{base}..HEAD", base,
                                   ledger_path="/nonexistent",
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, f"provenance-touching commit exempt; out={out}")

    def test_plain_fix_never_triggers_leg1(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            _write(repo, "src/z.py", "z = 3\n")
            _commit(repo, "fix: correct the timeout bound")
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base,
                                   ledger_path="/nonexistent",
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0)


class TestLeg2Integration(unittest.TestCase):
    def _repo_with_new_frontier_row(self, repo, mechanism):
        _init_repo(repo)
        _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
        base = _commit(repo, "chore: base")
        _write(repo, rpc.PROVENANCE_REL,
               PROV_HEADER
               + f"| 2026-07-14 | scope | {mechanism} | `abc1234` | none |\n"
               + WORKLIST)
        _commit(repo, "docs: record review pass")
        return base

    def _ledger(self, repo, model_id):
        path = os.path.join(repo, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            if model_id is not None:
                fh.write('{"kind":"claim","ts":100,"model_id":"%s"}\n' % model_id)
        return path

    def test_nonfrontier_stamping_frontier_row_blocks(self):
        with tempfile.TemporaryDirectory() as repo:
            base = self._repo_with_new_frontier_row(repo, "frontier deep review")
            ledger = self._ledger(repo, "claude-opus-4-8[1m]")
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base, ledger_path=ledger,
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 1, f"leg2 should block; out={out}")
            self.assertIn("leg 2", "\n".join(out))

    def test_frontier_session_may_stamp_frontier_row(self):
        with tempfile.TemporaryDirectory() as repo:
            base = self._repo_with_new_frontier_row(repo, "frontier deep review")
            ledger = self._ledger(repo, "claude-fable-5")
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base, ledger_path=ledger,
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, f"fable may stamp; out={out}")

    def test_absent_ledger_model_warns_not_blocks(self):
        with tempfile.TemporaryDirectory() as repo:
            base = self._repo_with_new_frontier_row(repo, "frontier deep review")
            ledger = self._ledger(repo, None)  # no claim → no model_id
            code, out, warn = rpc.run(repo, f"{base}..HEAD", base,
                                      ledger_path=ledger,
                                      witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, "unknown model must not block")
            self.assertTrue(any("leg2" in w for w in warn), warn)


class TestLeg3Integration(unittest.TestCase):
    def _big_src_diff_repo(self, repo, lines):
        _init_repo(repo)
        _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
        _write(repo, "src/base.py", "x = 0\n")
        _commit(repo, "chore: base + provenance boundary")
        # Change src heavily AFTER the last provenance-touching commit.
        _write(repo, "src/big.py", "\n".join(f"a{i} = {i}" for i in range(lines)) + "\n")
        _commit(repo, "feat: large src change (no review claim)")
        return

    def test_big_diff_nonfrontier_prints_advisory(self):
        with tempfile.TemporaryDirectory() as repo:
            self._big_src_diff_repo(repo, rpc.UNREVIEWED_SRC_LINES_NUDGE + 100)
            ledger = os.path.join(repo, "ledger.jsonl")
            with open(ledger, "w") as fh:
                fh.write('{"kind":"claim","ts":100,"model_id":"claude-opus-4-8[1m]"}\n')
            base = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                                  cwd=repo, capture_output=True, text=True, timeout=30).stdout.strip()
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base, ledger_path=ledger,
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, "leg3 never blocks")
            self.assertIn("leg 3", "\n".join(out))

    def test_big_diff_frontier_silent(self):
        with tempfile.TemporaryDirectory() as repo:
            self._big_src_diff_repo(repo, rpc.UNREVIEWED_SRC_LINES_NUDGE + 100)
            ledger = os.path.join(repo, "ledger.jsonl")
            with open(ledger, "w") as fh:
                fh.write('{"kind":"claim","ts":100,"model_id":"claude-fable-5"}\n')
            base = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                                  cwd=repo, capture_output=True, text=True, timeout=30).stdout.strip()
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base, ledger_path=ledger,
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0)
            self.assertNotIn("leg 3", "\n".join(out))


class TestClosingBoundaryPure(unittest.TestCase):
    """F3 (2026-07-26): the leg-3 boundary must come from a commit that ADDS a
    completed-table row (a closing commit), never from one that merely queues
    a worklist row — queuing debt used to silence the nudge about that debt."""

    SHA_A = "a" * 40
    SHA_B = "b" * 40
    SHA_C = "c" * 40

    @staticmethod
    def _chunk(sha, added_lines):
        diff = "\n".join(f"+{l}" for l in added_lines)
        return f"\x1e{sha}\ndiff --git a/x b/x\n@@ -1 +1 @@\n{diff}\n"

    def test_queuing_commit_does_not_advance_boundary(self):
        log = (self._chunk(self.SHA_A, ["| 1 | queued scope | why |"]) +
               self._chunk(self.SHA_B, ["| 2026-07-23 | scope | mech | fix | res |"]))
        self.assertEqual(rpc.closing_boundary_from_log(log), self.SHA_B)

    def test_closing_commit_is_the_boundary(self):
        log = self._chunk(self.SHA_A, ["| 2026-07-26 | scope | mech | fix | res |"])
        self.assertEqual(rpc.closing_boundary_from_log(log), self.SHA_A)

    def test_no_closing_row_falls_back_to_oldest_walked(self):
        # Direction matters: with no closing row in the window the advisory
        # must OVER-fire (oldest boundary), never go quiet on the newest.
        log = (self._chunk(self.SHA_A, ["| — | queued | why |"]) +
               self._chunk(self.SHA_B, ["| Pri | Scope | Why |"]) +
               self._chunk(self.SHA_C, ["prose only, no table row"]))
        self.assertEqual(rpc.closing_boundary_from_log(log), self.SHA_C)

    def test_removed_completed_row_is_not_a_close(self):
        log = (f"\x1e{self.SHA_A}\ndiff --git a/x b/x\n@@ -1 +1 @@\n"
               "-| 2026-07-23 | scope | mech | fix | res |\n" +
               self._chunk(self.SHA_B, ["| 2026-07-20 | s | m | f | r |"]))
        self.assertEqual(rpc.closing_boundary_from_log(log), self.SHA_B)

    def test_empty_or_garbage_log(self):
        self.assertIsNone(rpc.closing_boundary_from_log(""))
        self.assertIsNone(rpc.closing_boundary_from_log(None))
        self.assertIsNone(rpc.closing_boundary_from_log("\x1enot-a-sha\n+| x |"))


class TestLeg3QueuingDoesNotSilence(unittest.TestCase):
    """Integration form of F3 — red against the pre-fix boundary (last commit
    touching the file), green with the closing-row boundary."""

    COMPLETED_ROW = "| 2026-07-23 | full window | mech | abc1234 | none |\n"

    def test_queue_commit_after_big_diff_still_nudges(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL,
                   PROV_HEADER + self.COMPLETED_ROW + WORKLIST)
            _commit(repo, "docs: review pass row (closing boundary)")
            n = rpc.UNREVIEWED_SRC_LINES_NUDGE + 100
            _write(repo, "src/big.py",
                   "\n".join(f"a{i} = {i}" for i in range(n)) + "\n")
            _commit(repo, "feat: large src change (no review claim)")
            # The F3 shape: a later commit QUEUES a worklist row. This used to
            # move the boundary here and zero the nudge.
            _write(repo, rpc.PROVENANCE_REL,
                   PROV_HEADER + self.COMPLETED_ROW + WORKLIST +
                   "| 1 | the big unreviewed window | frontier-shaped |\n")
            _commit(repo, "docs: queue unreviewed window for frontier")
            ledger = os.path.join(repo, "ledger.jsonl")
            with open(ledger, "w") as fh:
                fh.write('{"kind":"claim","ts":100,'
                         '"model_id":"claude-opus-4-8[1m]"}\n')
            base = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=repo,
                capture_output=True, text=True, timeout=30).stdout.strip()
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base,
                                   ledger_path=ledger,
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, "leg3 never blocks")
            self.assertIn("leg 3", "\n".join(out),
                          "queuing debt must not silence the nudge about it")


class TestWitnessAndFailOpen(unittest.TestCase):
    def test_witness_written_on_block(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            _write(repo, "src/x.py", "x = 1\n")
            _commit(repo, "fix: self-review — 3 findings adversarial")
            wit = os.path.join(repo, "wit.log")
            rpc.run(repo, f"{base}..HEAD", base, ledger_path="/nonexistent",
                    witness_path=wit)
            with open(wit, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("leg1 BLOCK", body)

    def test_unresolvable_range_warns_not_crashes(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            _commit(repo, "chore: base")
            code, out, warn = rpc.run(repo, "deadbeef..HEAD", "deadbeef",
                                      ledger_path="/nonexistent",
                                      witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0)
            self.assertTrue(any("leg1" in w for w in warn), warn)


class TestLeg2BlockMessage(unittest.TestCase):
    """The leg-2 block must name the SANCTIONED unblock for a genuinely-frontier
    session judged by a stale non-frontier ledger read (the gate's first live
    firing, 2026-07-16: the first Fable session after the Opus interregnum was
    false-blocked stamping its own real pass). Pointing only at --no-verify
    trains the override habit; the honest path is a calibrated_claims rule-6
    manual record_claim, after which the gate re-reads the ledger."""

    def test_leg2_message_names_rule6_manual_claim_path(self):
        rows = [("2026-07-16", "frontier inline deep review", "| 2026-07-16 | x |")]
        msg = rpc._fmt_leg2_block(rows, "claude-opus-4-8")
        self.assertIn("record_claim", msg)
        self.assertIn("source=\"manual\"", msg)
        self.assertIn("--no-verify", msg)  # override stays documented, last


class TestCliSubprocess(unittest.TestCase):
    """The exact contract .githooks/pre-push depends on: the script run as a
    subprocess exits 1 on a hard-leg violation (with remediation text on stdout)
    and 0 when clean. Direct run()-level tests can't catch an argparse/exit-code
    regression in the CLI wrapper."""

    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "review_provenance_check.py")

    def _invoke(self, repo, base):
        return subprocess.run(
            [sys.executable, self.SCRIPT, "--repo", repo, "--range",
             f"{base}..HEAD", "--base-ref", base, "--ledger", "/nonexistent",
             "--witness", os.path.join(repo, "wit.log")],
            capture_output=True, text=True, timeout=60)

    def test_leg1_violation_exits_1_with_remediation(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            _write(repo, "src/x.py", "x = 1\n")
            _commit(repo, "fix: self-review — 3 findings adversarial")
            r = self._invoke(repo, base)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("leg 1", r.stdout)
            self.assertIn("--no-verify", r.stdout)

    def test_clean_push_exits_0(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            _write(repo, "src/x.py", "x = 1\n")
            _commit(repo, "fix: correct a timeout bound")  # no review claim
            r = self._invoke(repo, base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
