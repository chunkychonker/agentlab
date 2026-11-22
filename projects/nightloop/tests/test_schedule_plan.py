"""Milestone 9, criterion 1 and I5: the install and uninstall plan tables, asserted on the planners directly."""

from __future__ import annotations

import unittest

from nightloop.schedule import JobState, Step, mutates, plan_install, plan_uninstall

DESIRED = b"<plist>desired</plist>"
OTHER = b"<plist>old</plist>"
NOT_LOADED, IDLE, RUNNING = JobState.NOT_LOADED, JobState.LOADED_IDLE, JobState.RUNNING


class PlanInstallTest(unittest.TestCase):
    def test_table(self) -> None:
        rows = [
            (DESIRED, IDLE, (Step.NOTHING,)),
            (DESIRED, NOT_LOADED, (Step.BOOTSTRAP,)),
            (OTHER, NOT_LOADED, (Step.WRITE_PLIST, Step.BOOTSTRAP)),
            (None, NOT_LOADED, (Step.WRITE_PLIST, Step.BOOTSTRAP)),
            (OTHER, IDLE, (Step.BOOTOUT, Step.WRITE_PLIST, Step.BOOTSTRAP)),
            (None, IDLE, (Step.BOOTOUT, Step.WRITE_PLIST, Step.BOOTSTRAP)),
        ]
        for installed, state, expected in rows:
            with self.subTest(installed=installed, state=state):
                self.assertEqual(plan_install(DESIRED, installed, state), expected)

    def test_running_always_refuses_and_never_mutates(self) -> None:  # I5
        for installed in (DESIRED, OTHER, None):
            with self.subTest(installed=installed):
                steps = plan_install(DESIRED, installed, RUNNING)
                self.assertEqual(steps, (Step.REFUSE_RUNNING,))
                self.assertFalse(mutates(steps))

    def test_a_one_byte_difference_is_a_difference(self) -> None:
        self.assertEqual(plan_install(DESIRED, DESIRED + b"\n", IDLE),
                         (Step.BOOTOUT, Step.WRITE_PLIST, Step.BOOTSTRAP))


class PlanUninstallTest(unittest.TestCase):
    def test_table(self) -> None:
        rows = [
            (None, NOT_LOADED, (Step.NOTHING,)),
            (OTHER, IDLE, (Step.BOOTOUT, Step.REMOVE_PLIST)),
            (OTHER, NOT_LOADED, (Step.REMOVE_PLIST,)),
            (None, IDLE, (Step.BOOTOUT,)),
        ]
        for installed, state, expected in rows:
            with self.subTest(installed=installed, state=state):
                self.assertEqual(plan_uninstall(installed, state), expected)

    def test_running_always_refuses(self) -> None:  # I5
        for installed in (OTHER, None):
            self.assertEqual(plan_uninstall(installed, RUNNING), (Step.REFUSE_RUNNING,))


class MutatesTest(unittest.TestCase):
    def test_only_effects_count(self) -> None:
        self.assertFalse(mutates((Step.NOTHING,)))
        for step in (Step.WRITE_PLIST, Step.BOOTOUT, Step.BOOTSTRAP, Step.REMOVE_PLIST):
            self.assertTrue(mutates((step,)), step)


if __name__ == "__main__":
    unittest.main()
