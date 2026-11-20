"""Milestone 7b, pure half: the claim steps decide what to read, write and publish.

Driven with in-memory ports (``ScriptedSource``, ``MemoryBacklog``,
``RecordingPublisher``) and the real reconcilers. The git half (W2-W6 on a
temp repo) is in ``test_claims_git_publisher.py``. Numbers refer to
research/2026-11-20-nightloop-claim-wiring.md.
"""

from __future__ import annotations

import unittest

from nightloop.claims import (
    Claim,
    ClaimPorts,
    ClaimSourceError,
    NoClaim,
    Nothing,
    PrNumber,
    Published,
    PublishFailed,
    ShippedStatus,
    Skipped,
    StrandedBranch,
    StrandedStatus,
    Unreadable,
    UnshippedBranch,
    claim_step,
    pre_night_step,
    shipped_step,
)

from .claims_support import MemoryBacklog, RecordingPublisher, ScriptedSource

KEY_A = "First item `with code`"
KEY_B = "Second item [building] in its text"
BACKLOG = f"# BACKLOG\n\n- [ ] {KEY_A}\n- [ ] {KEY_B}\n- [building] {KEY_A} done\n"
BRANCH_1 = StrandedBranch("cycle/2026-11-20-unshipped-010203-1")
BRANCH_2 = StrandedBranch("cycle/2026-11-20-unshipped-020304-2")


def claim(key: str) -> Claim:
    return Claim(key, key)


class FailingSource(ScriptedSource):
    def unshipped_claims(self):
        raise ClaimSourceError("git for-each-ref failed: fatal: bad object\nsecond line")


class ClaimStepTest(unittest.TestCase):
    def ports(self, source, *, not_ready=None, failure=None) -> tuple[ClaimPorts, MemoryBacklog, RecordingPublisher]:
        backlog = MemoryBacklog(BACKLOG)
        publisher = RecordingPublisher(not_ready=not_ready, failure=failure)
        return ClaimPorts(source, backlog, publisher), backlog, publisher

    def test_w1_no_branches_is_nothing_with_no_write_and_no_publish(self) -> None:
        ports, backlog, publisher = self.ports(ScriptedSource([]))
        result, outcomes = claim_step(ports)
        self.assertEqual(result, Nothing())
        self.assertEqual(outcomes, ())
        self.assertEqual((backlog.text, backlog.writes), (BACKLOG, 0))
        self.assertEqual(publisher.events, ["ready"])

    def test_w1_branches_with_nothing_to_mark_are_nothing(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, NoClaim()),
                                 UnshippedBranch(BRANCH_2, Unreadable("no merge base"))])
        ports, backlog, publisher = self.ports(source)
        result, outcomes = claim_step(ports)
        self.assertEqual(result, Nothing())
        self.assertEqual([o.status for o in outcomes], [StrandedStatus.NO_CLAIM, StrandedStatus.UNREADABLE])
        self.assertEqual(backlog.writes, 0)
        self.assertEqual(publisher.events, ["ready"])

    def test_w2_changes_are_published_once_with_the_count_in_the_message(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A)),
                                 UnshippedBranch(BRANCH_2, claim(KEY_B))])
        ports, backlog, publisher = self.ports(source)
        result, outcomes = claim_step(ports)
        self.assertEqual(result, Published(2))
        self.assertEqual([o.status for o in outcomes], [StrandedStatus.MARKED, StrandedStatus.MARKED])
        self.assertEqual(publisher.events, ["ready", "publish"])
        self.assertEqual(publisher.messages,
                         ["chore(backlog): reconcile 2 claim(s) stranded on unshipped branches"])
        self.assertIn(f"- [stranded {BRANCH_1}] {KEY_A}\n", backlog.text)
        self.assertIn(f"- [stranded {BRANCH_2}] {KEY_B}\n", backlog.text)

    def test_w2_only_changed_outcomes_are_counted(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A)),
                                 UnshippedBranch(StrandedBranch(f"origin/{BRANCH_1}"), claim(KEY_A)),
                                 UnshippedBranch(BRANCH_2, NoClaim())])
        ports, _, publisher = self.ports(source)
        result, _ = claim_step(ports)
        self.assertEqual(result, Published(1))
        self.assertIn("reconcile 1 claim(s)", publisher.messages[0])

    def test_w4_not_ready_is_skipped_before_anything_is_read_or_written(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A))])
        ports, backlog, publisher = self.ports(source, not_ready=Skipped("the checkout is on dev, not main"))
        result, outcomes = claim_step(ports)
        self.assertEqual(result, Skipped("the checkout is on dev, not main"))
        self.assertEqual(outcomes, ())
        self.assertEqual((backlog.reads, backlog.writes, backlog.text), (0, 0, BACKLOG))
        self.assertEqual(publisher.events, ["ready"])

    def test_w5_a_failed_publish_is_reported_as_it_came(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A))])
        failed = PublishFailed("git push failed: rejected")
        ports, backlog, _ = self.ports(source, failure=failed)
        result, _ = claim_step(ports)
        self.assertEqual(result, failed)
        self.assertEqual(backlog.writes, 1, "the edit is made; only publishing failed")

    def test_w6_a_second_step_on_the_published_text_is_nothing(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A))])
        ports, backlog, publisher = self.ports(source)
        self.assertEqual(claim_step(ports)[0], Published(1))
        self.assertEqual(claim_step(ports)[0], Nothing())
        self.assertEqual(backlog.writes, 1)
        self.assertEqual(publisher.events, ["ready", "publish", "ready"])

    def test_w10_a_source_error_propagates_between_cycles_and_is_skipped_before_the_night(self) -> None:
        ports, backlog, _ = self.ports(FailingSource())
        with self.assertRaises(ClaimSourceError):
            claim_step(ports)
        result, outcomes = pre_night_step(ports)
        self.assertIsInstance(result, Skipped)
        self.assertEqual(result.reason, "cannot list recovery branches: git for-each-ref failed: fatal: bad object")
        self.assertEqual(outcomes, ())
        self.assertEqual(backlog.writes, 0)

    def test_pre_night_is_claim_step_otherwise(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A))])
        ports, _, _ = self.ports(source)
        self.assertEqual(pre_night_step(ports)[0], Published(1))

    def test_an_os_error_propagates_from_both(self) -> None:
        class Broken(MemoryBacklog):
            def read(self) -> str:
                raise PermissionError(13, "Permission denied", "BACKLOG.md")

        source = ScriptedSource([UnshippedBranch(BRANCH_1, claim(KEY_A))])
        ports = ClaimPorts(source, Broken(BACKLOG), RecordingPublisher())
        for step in (claim_step, pre_night_step):
            with self.subTest(step=step.__name__), self.assertRaises(PermissionError):
                step(ports)


class ShippedStepTest(unittest.TestCase):
    def ports(self, merged, *, not_ready=None, failure=None):
        backlog = MemoryBacklog(BACKLOG.replace(f"- [ ] {KEY_B}", f"- [building] {KEY_B}"))
        publisher = RecordingPublisher(not_ready=not_ready, failure=failure)
        return ClaimPorts(ScriptedSource(merged=merged), backlog, publisher), backlog, publisher

    def test_no_prs_is_nothing_and_touches_nothing(self) -> None:
        ports, backlog, publisher = self.ports({})
        self.assertEqual(shipped_step(ports, ()), (Nothing(), ()))
        self.assertEqual(publisher.events, [])
        self.assertEqual(backlog.reads, 0)

    def test_w9_an_unmerged_pr_is_pending_not_an_error_and_not_published(self) -> None:
        ports, backlog, publisher = self.ports({7: Unreadable("gh reports no merge commit for PR #7: got ''")})
        result, outcomes = shipped_step(ports, [PrNumber(7)])
        self.assertEqual(result, Nothing())
        self.assertEqual([(o.pr, o.status) for o in outcomes], [(PrNumber(7), ShippedStatus.UNREADABLE)])
        self.assertEqual(backlog.writes, 0)
        self.assertEqual(publisher.events, ["ready"])

    def test_w9_a_pr_merged_during_the_night_is_marked_done_and_published(self) -> None:
        ports, backlog, publisher = self.ports({
            7: Unreadable("not merged"),
            8: claim(KEY_B),
        })
        result, outcomes = shipped_step(ports, [PrNumber(7), PrNumber(8)])
        self.assertEqual(result, Published(1))
        self.assertEqual([o.status for o in outcomes], [ShippedStatus.UNREADABLE, ShippedStatus.MARKED_DONE])
        self.assertEqual(publisher.messages, ["chore(backlog): mark 1 claim(s) done for merged PR(s) #8"])
        self.assertIn(f"- [done #8] {KEY_B}\n", backlog.text)

    def test_not_ready_skips_before_asking_about_any_pr(self) -> None:
        ports, backlog, publisher = self.ports({}, not_ready=Skipped("main is 1 commit(s) behind origin/main"))
        result, outcomes = shipped_step(ports, [PrNumber(7)])  # ScriptedSource would KeyError if asked
        self.assertEqual((result, outcomes), (Skipped("main is 1 commit(s) behind origin/main"), ()))
        self.assertEqual(backlog.reads, 0)

    def test_a_failed_publish_is_reported(self) -> None:
        failed = PublishFailed("git commit failed: hook said no")
        ports, _, _ = self.ports({8: claim(KEY_B)}, failure=failed)
        self.assertEqual(shipped_step(ports, [PrNumber(8)])[0], failed)

    def test_a_non_pr_number_is_a_type_error_before_any_effect(self) -> None:
        ports, _, publisher = self.ports({})
        with self.assertRaises(TypeError):
            shipped_step(ports, [7])  # type: ignore[list-item]
        self.assertEqual(publisher.events, [])


class ResultTypesTest(unittest.TestCase):
    def test_bad_values_cannot_be_built(self) -> None:
        for build, error in (
            (lambda: Published(0), ValueError),
            (lambda: Published(True), TypeError),
            (lambda: Skipped(""), ValueError),
            (lambda: Skipped("two\nlines"), ValueError),
            (lambda: PublishFailed(None), TypeError),  # type: ignore[arg-type]
        ):
            with self.assertRaises(error):
                build()


if __name__ == "__main__":
    unittest.main()
