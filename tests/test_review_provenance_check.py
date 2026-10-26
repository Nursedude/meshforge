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


class TestTableStructurePure(unittest.TestCase):
    """malformed_table_rows — added 2026-08-06 after an audit found the live
    ledger had carried a MERGED row for 12 days (the 2026-07-25 Pri-2 commit
    overwrote the taxonomy row's leading cells: 36 recorded passes, 35
    parseable rows) and literal ``|| true`` prose had truncated the 07-29
    row's fix/residual cells. A parser-invisible row is a silent hole in
    review coverage — exactly the absence this ledger exists to make legible."""

    def test_wellformed_rows_pass(self):
        text = (
            "| Date | Scope | Mechanism | Fix commits | Residuals |\n"
            "|------|-------|-----------|-------------|-----------|\n"
            "| 2026-08-06 | scope | mech | `abc1234` | none |\n"
            "| Pri | Scope | Why |\n"
            "| Pri-1 | queued arc | frontier-shaped |\n"
            "prose outside tables is ignored\n"
        )
        self.assertEqual(rpc.malformed_table_rows(text), [])

    def test_merged_row_detected(self):
        merged = ("| 2026-08-06 | scope | mech | fix | residual "
                  "| second-row scope | second mech | second fix | second residual |")
        bad = rpc.malformed_table_rows(merged)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][1], 9)

    def test_stray_pipe_in_prose_detected(self):
        row = "| 2026-08-06 | scope | mech | fix | fell through via `|| true` guard |"
        bad = rpc.malformed_table_rows(row)
        self.assertEqual(len(bad), 1, "a literal | in prose truncates cells")

    def test_short_date_row_detected(self):
        bad = rpc.malformed_table_rows("| 2026-08-06 | scope | mech | fix |")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][1], 4)

    def test_queued_row_wrong_cells_detected(self):
        bad = rpc.malformed_table_rows("| **BIG ARC — scope text** | why |")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][1], 2)


class TestTableStructureIntegration(unittest.TestCase):
    MERGED = ("| 2026-08-06 | scope | mech | fix | residual "
              "| ghost scope | ghost mech | ghost fix | ghost residual |\n")

    def _ledger(self, repo):
        path = os.path.join(repo, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"kind":"claim","ts":100,"model_id":"claude-fable-5"}\n')
        return path

    def test_new_malformed_row_blocks(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + self.MERGED + WORKLIST)
            _commit(repo, "docs: record pass (merged row)")
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base,
                                   ledger_path=self._ledger(repo),
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 1, f"new malformed row must block; out={out}")
            self.assertIn("structure", "\n".join(out))

    def test_preexisting_malformed_row_warns_not_blocks(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + self.MERGED + WORKLIST)
            base = _commit(repo, "chore: base already carries the damage")
            _write(repo, "src/x.py", "x = 1\n")
            _commit(repo, "fix: unrelated change")
            code, out, warn = rpc.run(repo, f"{base}..HEAD", base,
                                      ledger_path=self._ledger(repo),
                                      witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, f"pre-existing damage must not block; out={out}")
            self.assertTrue(any("structure" in w for w in warn), warn)


class TestLiveLedgerStructure(unittest.TestCase):
    """Pins the REAL ledger file — the repaired state, so the two damage
    classes found 2026-08-06 (merged row, stray pipe) cannot silently recur."""

    LIVE = os.path.join(os.path.dirname(__file__), "..", rpc.PROVENANCE_REL)

    def _text(self):
        with open(self.LIVE, encoding="utf-8") as fh:
            return fh.read()

    def test_no_malformed_rows(self):
        self.assertEqual(rpc.malformed_table_rows(self._text()), [])

    def test_taxonomy_row_is_its_own_row(self):
        rows = rpc.parse_completed_rows(self._text())
        tax = [r for r in rows if "SECOND-BRAIN TAXONOMY design pass" in r[2]]
        self.assertEqual(len(tax), 1,
                         "the row un-merged 2026-08-06 must stay parseable")
        self.assertEqual(tax[0][0], "2026-07-26")


class TestLeg3CountsScripts(unittest.TestCase):
    """The two most recent Pri-1 arcs (pytest-rc trust, guard-spine) lived in
    scripts/ and the hooks — a src/-only debt counter was blind to exactly the
    enforcement layer whose 5.5-month inertness the 08-05 arc exposed."""

    def test_scripts_only_diff_still_nudges(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            _commit(repo, "chore: base + provenance boundary")
            n = rpc.UNREVIEWED_SRC_LINES_NUDGE + 100
            _write(repo, "scripts/big.sh",
                   "\n".join(f"echo {i}" for i in range(n)) + "\n")
            _commit(repo, "feat: large scripts change (no review claim)")
            ledger = os.path.join(repo, "ledger.jsonl")
            with open(ledger, "w") as fh:
                fh.write('{"kind":"claim","ts":100,"model_id":"claude-opus-4-8[1m]"}\n')
            base = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                                  cwd=repo, capture_output=True, text=True,
                                  timeout=30).stdout.strip()
            code, out, _ = rpc.run(repo, f"{base}..HEAD", base, ledger_path=ledger,
                                   witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0, "leg3 never blocks")
            self.assertIn("leg 3", "\n".join(out))


class TestInternalErrorWitness(unittest.TestCase):
    """A gate bug passes open by design (must not wedge a push) — but a silent
    pass-open is a fail-dark (#9): it must leave a witness-log line."""

    def test_pass_open_leaves_witness(self):
        with tempfile.TemporaryDirectory() as d:
            wit = os.path.join(d, "wit.log")
            orig = rpc.run

            def boom(*_a, **_k):
                raise RuntimeError("drill")

            rpc.run = boom
            try:
                code = rpc.main(["--witness", wit])
            finally:
                rpc.run = orig
            self.assertEqual(code, 0, "internal error must pass open")
            with open(wit, encoding="utf-8") as fh:
                self.assertIn("PASSED OPEN", fh.read())


if __name__ == "__main__":
    unittest.main()


class TestLayerCScopeAnnouncement(unittest.TestCase):
    """Layer C (2026-09-07): a checker must announce WHAT IT JUDGED.

    Defect 1 of the exit-code-gate plan was this gate reporting GATE_RC=0 over
    an EMPTY `origin/main..HEAD` while an uncommitted edit sat in the tree. A
    check that judged nothing and exited 0 is indistinguishable from one that
    judged the work and found it clean. "The check ran against nothing" is not
    syntactic — it cannot be linted or hooked — so the cure is legibility.
    """

    def _scope(self, repo, rng, base):
        sink = []
        rpc.run(repo, rng, base, ledger_path="/nonexistent",
                witness_path=os.path.join(repo, "wit.log"), scope_sink=sink)
        return sink

    def test_empty_range_reports_zero_not_silence(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, "README.md", "base\n")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            sink = self._scope(repo, f"{base}..HEAD", base)
            self.assertEqual(sink[0]["commits"], 0)

    def test_real_range_reports_the_count(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, "README.md", "base\n")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            _write(repo, "a.py", "a = 1\n"); _commit(repo, "feat: a")
            _write(repo, "b.py", "b = 1\n"); _commit(repo, "feat: b")
            sink = self._scope(repo, f"{base}..HEAD", base)
            self.assertEqual(sink[0]["commits"], 2)

    def test_unresolvable_range_is_UNKNOWN_not_zero(self):
        """`None` and `0` are different claims: "I could not see the range" is
        not "the range is empty". Collapsing them would let a broken base ref
        read as a clean, empty push (honest_failure_modes #2)."""
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, "README.md", "base\n")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            sink = self._scope(repo, "nosuchref..HEAD", base)
            self.assertIsNone(sink[0]["commits"])

    def test_scope_sink_is_optional_so_existing_callers_are_untouched(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _write(repo, "README.md", "base\n")
            _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
            base = _commit(repo, "chore: base")
            code, _out, _warn = rpc.run(repo, f"{base}..HEAD", base,
                                        ledger_path="/nonexistent",
                                        witness_path=os.path.join(repo, "wit.log"))
            self.assertEqual(code, 0)




# --------------------------------------------------------------------------- #
# Leg 2 — a frontier SUBAGENT read on a non-frontier session (2026-10-26).    #
# The session ledger only sees the parent model; the harness-written subagent #
# transcript is the witness. Bound to the row by a file/sha from the row's    #
# Scope or Fix cell that the SUBAGENT ITSELF named in its own tool calls —    #
# the parent's prompt and the CLAUDE.md every transcript carries never count  #
# (Fable non-author read 2026-10-26: 14/14 transcripts carried CLAUDE.md's    #
# paths, so a whole-file substring bound nothing).                            #
# --------------------------------------------------------------------------- #

import json as _json  # noqa: E402
import time as _time  # noqa: E402


def _iso(ts):
    return _time.strftime("%Y-%m-%dT%H:%M:%S.000Z", _time.gmtime(ts))


def _read_call(path):
    return [{"type": "tool_use", "name": "Read", "input": {"file_path": path}}]


def _transcript(claude_dir, model="claude-fable-5-1", sidechain=True, ts=None,
                prompt="CLAUDE.md names src/gateway/canonical_message.py",
                said=None, name="agent-x.jsonl", line_type="assistant"):
    d = os.path.join(claude_dir, "projects", "-opt-repo", "sess-1", "subagents")
    os.makedirs(d, exist_ok=True)
    ts = _time.time() - 60 if ts is None else ts
    said = _read_call("/opt/repo/scripts/foo.py") if said is None else said
    lines = [
        {"isSidechain": sidechain, "type": "user", "timestamp": _iso(ts),
         "message": {"role": "user", "content": prompt}},
        {"isSidechain": sidechain, "type": line_type, "timestamp": _iso(ts),
         "message": {"role": "assistant", "model": model, "content": said}},
    ]
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_json.dumps(x) for x in lines) + "\n")
    return p


SUB_ROW = ("| 2026-10-26 | fix in `scripts/foo.py` | Fable subagent adversarial "
           "read | `d9878c32` | queued `src/other/thing.py` |")


class TestSubagentWitnessPure(unittest.TestCase):
    def test_row_claims_subagent_read(self):
        self.assertTrue(rpc.row_claims_subagent_read("Fable subagent read"))
        self.assertTrue(rpc.row_claims_subagent_read("a Fable sub-agent pass"))
        self.assertFalse(rpc.row_claims_subagent_read("frontier deep review"))

    def test_denied_subagent_is_not_a_claim(self):
        self.assertFalse(rpc.row_claims_subagent_read(
            "Fable 5.1 SESSION pass (no subagent)"))
        self.assertFalse(rpc.row_claims_subagent_read("without a subagent"))

    def test_anchors_come_from_scope_and_fix_cells_only(self):
        a = rpc.row_anchor_tokens(SUB_ROW)
        self.assertIn("scripts/foo.py", a)
        self.assertIn("d9878c32", a)
        self.assertNotIn("src/other/thing.py", a)   # residuals cell

    def test_anchor_tokens_must_be_file_shaped(self):
        row = ("| 2026-10-26 | `tests/` `and/or` `10/10` `TX/RX` "
               "`https://github.com/x/y.py` `/code-review` `@rns/` "
               "`foo.py` `src/a/b.py` | m | `none` | x |")
        self.assertEqual(rpc.row_anchor_tokens(row), ["src/a/b.py"])

    def test_line_suffix_and_sha_range_still_anchor(self):
        row = ("| 2026-10-26 | `scripts/foo.py:12` `src/a/b.py:3-9` "
               "| m | `d9878c32..9b6d80de` | x |")
        self.assertEqual(rpc.row_anchor_tokens(row),
                         ["scripts/foo.py", "src/a/b.py", "d9878c32",
                          "9b6d80de"])

    def test_short_hex_words_are_not_shas(self):
        self.assertEqual(rpc.row_anchor_tokens(
            "| 2026-10-26 | `cafe` `bad` `fab1e` | m | `abc12` | x |"), [])

    def test_window_constant_is_a_day(self):
        self.assertEqual(rpc.SUBAGENT_WITNESS_WINDOW_S, 24 * 3600)

    def _w(self, text, anchors, lo=None, hi=None):
        now = _time.time()
        return rpc.transcript_witnesses(
            text, anchors, lo=now - 3600 if lo is None else lo,
            hi=now + 300 if hi is None else hi)

    def _text(self, tmp, **kw):
        with open(_transcript(tmp, **kw), encoding="utf-8") as fh:
            return fh.read()

    def test_subagent_that_read_the_file_witnesses(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(self._w(self._text(d), ["scripts/foo.py"]))

    def test_anchor_only_in_the_parent_prompt_does_not_witness(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, prompt="please review scripts/foo.py",
                           said=_read_call("/opt/repo/src/x/other.py"))
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_claude_md_paths_in_the_prompt_do_not_witness(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(self._w(self._text(d),
                                     ["src/gateway/canonical_message.py"]))

    def test_bash_that_runs_against_the_file_witnesses(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, said=[{"type": "tool_use", "name": "Bash",
                                     "input": {"command": "sed -n 1,40p "
                                               "scripts/foo.py"}}])
            self.assertTrue(self._w(t, ["scripts/foo.py"]))

    def test_writing_or_reporting_a_path_is_not_reading_it(self):
        # live 2026-10-26: the gate's own adversarial reviewer typed a path
        # into its attack scripts (Write x3) and its hand-back — and so
        # vouched for a row about a file it never opened
        for said in (
                [{"type": "tool_use", "name": "Write",
                  "input": {"file_path": "/tmp/a.py",
                            "content": "row = 'scripts/foo.py'"}}],
                [{"type": "tool_use", "name": "Edit",
                  "input": {"file_path": "scripts/foo.py"}}],
                [{"type": "tool_use", "name": "SubagentHandback",
                  "input": {"message": "reviewed scripts/foo.py"}}],
                [{"type": "text", "text": "I read scripts/foo.py"}]):
            with tempfile.TemporaryDirectory() as d:
                t = self._text(d, said=said)
                self.assertFalse(self._w(t, ["scripts/foo.py"]), said)

    def test_only_the_field_that_names_a_file_counts(self):
        # Fable 2nd read: a Bash `description` is prose, a Grep/Glob
        # `pattern` is a string the model typed — neither opened the file
        for said in (
                [{"type": "tool_use", "name": "Bash",
                  "input": {"command": "ls", "description": "Read scripts/foo.py"}}],
                [{"type": "tool_use", "name": "Grep",
                  "input": {"pattern": "scripts/foo.py", "path": "/opt/other"}}],
                [{"type": "tool_use", "name": "Glob",
                  "input": {"pattern": "scripts/foo.py"}}]):
            with tempfile.TemporaryDirectory() as d:
                self.assertFalse(self._w(self._text(d, said=said),
                                         ["scripts/foo.py"]), said)
        for said in (
                [{"type": "tool_use", "name": "Grep",
                  "input": {"pattern": "def x", "path": "/r/scripts/foo.py"}}],
                [{"type": "tool_use", "name": "Glob",
                  "input": {"pattern": "*.py", "path": "/r/scripts/foo.py"}}]):
            with tempfile.TemporaryDirectory() as d:
                self.assertTrue(self._w(self._text(d, said=said),
                                        ["scripts/foo.py"]), said)

    def test_anchor_in_the_line_but_outside_the_tool_input_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, said=[
                {"type": "text", "text": "scripts/foo.py"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}])
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_nonfrontier_model_does_not_witness(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, model="claude-opus-5-5")
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_main_thread_is_not_a_subagent(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, sidechain=False)
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_non_assistant_line_does_not_witness(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, line_type="user")
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_out_of_window_does_not_witness(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, ts=_time.time() - 3 * 86400)
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_future_stamp_does_not_witness(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._text(d, ts=_time.time() + 86400)
            self.assertFalse(self._w(t, ["scripts/foo.py"]))

    def test_timestamp_without_z_is_not_trusted(self):
        self.assertIsNone(rpc._parse_iso_ts("2026-10-26T17:00:56.607"))
        self.assertIsNotNone(rpc._parse_iso_ts("2026-10-26T17:00:56.607Z"))

    def test_no_anchors_never_witnesses(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(self._w(self._text(d), []))

    def test_torn_and_garbage_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            t = "not json\n{\"torn\": \n" + self._text(d)
            self.assertTrue(self._w(t, ["scripts/foo.py"]))


class TestLeg2SubagentIntegration(unittest.TestCase):
    def _repo(self, repo, mechanism, commit_ts=None):
        _init_repo(repo)
        _write(repo, rpc.PROVENANCE_REL, PROV_HEADER + WORKLIST)
        base = _commit(repo, "chore: base")
        _write(repo, rpc.PROVENANCE_REL, PROV_HEADER
               + f"| 2026-10-26 | fix in `scripts/foo.py` | {mechanism} | "
                 f"`d9878c32` | none |\n" + WORKLIST)
        env = dict(os.environ)
        if commit_ts is not None:
            env["GIT_COMMITTER_DATE"] = f"@{int(commit_ts)} +0000"
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, timeout=30)
        subprocess.run(["git", "commit", "-q", "-m", "docs: provenance"],
                       cwd=repo, check=True, timeout=30, env=env)
        ledger = os.path.join(repo, "ledger.jsonl")
        with open(ledger, "w") as fh:
            fh.write('{"kind":"claim","ts":100,"model_id":"claude-opus-5-5"}\n')
        return base, ledger

    def _run(self, repo, base, ledger, claude_dir):
        return rpc.run(repo, f"{base}..HEAD", base, ledger_path=ledger,
                       witness_path=os.path.join(repo, "wit.log"),
                       claude_dir=claude_dir)

    def test_witnessed_subagent_row_passes(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent adversarial read")
            _transcript(cd)
            code, out, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 0, out)
            with open(os.path.join(repo, "wit.log")) as fh:
                self.assertIn("leg2 PASS via frontier subagent", fh.read())

    def test_no_transcript_still_blocks_and_says_why(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent adversarial read")
            code, out, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 1)
            self.assertIn("subagent transcript", "\n".join(out))

    def test_prompt_only_transcript_still_blocks(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent adversarial read")
            _transcript(cd, prompt="review scripts/foo.py",
                        said=[{"type": "text", "text": "done"}])
            code, _, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 1)

    def test_session_claim_without_subagent_word_still_blocks(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "frontier deep review")
            _transcript(cd)
            code, _, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 1)

    def test_denied_subagent_session_claim_still_blocks(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable SESSION pass (no subagent)")
            _transcript(cd)
            code, _, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 1)

    def test_nonfrontier_subagent_transcript_still_blocks(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent adversarial read")
            _transcript(cd, model="claude-opus-5-5")
            code, _, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 1)

    def test_window_is_anchored_on_head_not_now(self):
        # HEAD committed 10 days ago: a read 9 days ago is after HEAD → block;
        # a read 10.5 days ago (12 h before HEAD) → pass
        old = _time.time() - 10 * 86400
        for read_ts, want in ((old + 86400, 1), (old - 12 * 3600, 0)):
            with tempfile.TemporaryDirectory() as repo, \
                    tempfile.TemporaryDirectory() as cd:
                base, ledger = self._repo(repo, "Fable subagent read",
                                          commit_ts=old)
                p = _transcript(cd, ts=read_ts)
                os.utime(p, (read_ts, read_ts))
                code, out, _ = self._run(repo, base, ledger, cd)
                self.assertEqual(code, want, (read_ts - old, out))

    def test_window_follows_the_rows_commit_not_a_later_head(self):
        # Fable 2nd read: read at T, row committed T+1h, unrelated commit
        # at T+30h is HEAD → the honest row must still pass
        t = _time.time() - 2 * 86400
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent read",
                                      commit_ts=t + 3600)
            _write(repo, "src/later.py", "x = 1\n")
            env = dict(os.environ,
                       GIT_COMMITTER_DATE=f"@{int(t + 30 * 3600)} +0000")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                           timeout=30)
            subprocess.run(["git", "commit", "-q", "-m", "feat: later"],
                           cwd=repo, check=True, timeout=30, env=env)
            p = _transcript(cd, ts=t)
            os.utime(p, (t, t))
            code, out, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 0, out)

    def test_future_dated_row_commit_is_clamped_to_now(self):
        # a clock that ran ahead must not stretch the window into the future
        future = _time.time() + 3 * 86400
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent read",
                                      commit_ts=future)
            p = _transcript(cd, ts=future - 3600)
            code, _, _ = self._run(repo, base, ledger, cd)
            self.assertEqual(code, 1)

    def test_unreadable_transcript_blocks_not_crashes(self):
        with tempfile.TemporaryDirectory() as repo, \
                tempfile.TemporaryDirectory() as cd:
            base, ledger = self._repo(repo, "Fable subagent adversarial read")
            p = _transcript(cd)
            os.chmod(p, 0)
            try:
                if os.access(p, os.R_OK):
                    self.skipTest("running as root — mode 000 still readable")
                code, _, _ = self._run(repo, base, ledger, cd)
                self.assertEqual(code, 1)
            finally:
                os.chmod(p, 0o600)

    def test_absent_claude_dir_blocks_not_crashes(self):
        with tempfile.TemporaryDirectory() as repo:
            base, ledger = self._repo(repo, "Fable subagent adversarial read")
            code, _, _ = self._run(repo, base, ledger,
                                   os.path.join(repo, "nope"))
            self.assertEqual(code, 1)
