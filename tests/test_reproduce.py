from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

import reproduce


@contextlib.contextmanager
def quiet():
    """Swallow the progress output that the tested commands print."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


class VerifyStepTests(unittest.TestCase):
    def test_argv_matches_the_documented_command(self) -> None:
        step = reproduce.VERIFY_STEPS[0]
        argv = step.argv()
        self.assertEqual(argv[1], reproduce.AUDIT)
        self.assertIn("--skip-raw-files", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "balanced")
        self.assertEqual(argv[argv.index("--workload") + 1], "sequential")
        self.assertEqual(argv[argv.index("--raw-dir") + 1], "results/node85-rerun/raw")
        self.assertEqual(
            argv[argv.index("--output-dir") + 1],
            "results/processed/node85-rerun-sequential",
        )

    def test_workload_is_omitted_when_the_step_sets_none(self) -> None:
        step = next(s for s in reproduce.VERIFY_STEPS if s.key == "first-bare-metal")
        self.assertNotIn("--workload", step.argv())

    def test_sensitivity_step_selects_sensitivity_mode(self) -> None:
        step = next(s for s in reproduce.VERIFY_STEPS if s.key == "sensitivity")
        argv = step.argv()
        self.assertEqual(argv[argv.index("--mode") + 1], "sensitivity")

    def test_step_keys_are_unique(self) -> None:
        keys = [s.key for s in reproduce.VERIFY_STEPS]
        self.assertCountEqual(keys, set(keys))

    def test_every_step_is_selectable_from_the_command_line(self) -> None:
        parser = reproduce.build_parser()
        for step in reproduce.VERIFY_STEPS:
            with self.subTest(step=step.key):
                args = parser.parse_args(["verify", "--which", step.key])
                self.assertEqual(args.which, step.key)


class ReportTests(unittest.TestCase):
    def test_a_report_without_failures_is_not_failed(self) -> None:
        report = reproduce.Report()
        report.add(reproduce.PASS, "a")
        report.add(reproduce.WARN, "b", "a warning does not block")
        self.assertFalse(report.failed)

    def test_one_failure_marks_the_report_failed(self) -> None:
        report = reproduce.Report()
        report.add(reproduce.PASS, "a")
        report.add(reproduce.FAIL, "b")
        self.assertTrue(report.failed)


class PreflightTests(unittest.TestCase):
    def test_this_clone_can_verify(self) -> None:
        report = reproduce.Report()
        reproduce.check_verify(report)
        failures = [c.name for c in report.checks if c.status == reproduce.FAIL]
        self.assertEqual(failures, [], f"preflight failed on: {failures}")

    def test_each_raw_directory_is_reported_once(self) -> None:
        report = reproduce.Report()
        reproduce.check_verify(report)
        names = [c.name for c in report.checks]
        self.assertCountEqual(names, set(names))

    def test_run_is_blocked_off_linux(self) -> None:
        report = reproduce.Report()
        with mock.patch.object(reproduce.sys, "platform", "win32"):
            reproduce.check_run(report, "bare-metal")
        self.assertTrue(report.failed)
        self.assertEqual(report.checks[0].name, "operating system")


class RunGateTests(unittest.TestCase):
    def _args(self, argv: list[str] | None = None):
        return reproduce.build_parser().parse_args(["run", *(argv or [])])

    def test_run_refuses_off_linux_without_dispatching(self) -> None:
        patch_platform = mock.patch.object(reproduce.sys, "platform", "win32")
        patch_dispatch = mock.patch.object(reproduce, "dispatch")
        with quiet(), patch_platform, patch_dispatch as dispatched:
            code = reproduce.cmd_run(self._args())
        self.assertEqual(code, 1)
        dispatched.assert_not_called()

    def test_run_is_a_dry_run_unless_yes_is_given(self) -> None:
        patch_check = mock.patch.object(reproduce, "check_run")
        patch_dispatch = mock.patch.object(reproduce, "dispatch")
        with quiet(), patch_check, patch_dispatch as dispatched:
            code = reproduce.cmd_run(self._args())
        self.assertEqual(code, 0)
        dispatched.assert_not_called()

    def test_yes_dispatches_the_documented_runner(self) -> None:
        patch_check = mock.patch.object(reproduce, "check_run")
        patch_dispatch = mock.patch.object(reproduce, "dispatch", return_value=0)
        with quiet(), patch_check, patch_dispatch as dispatched:
            reproduce.cmd_run(self._args(["--yes"]))
        argv = dispatched.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertEqual(argv[1], reproduce.BARE_METAL_RUNNER)
        self.assertEqual(argv[2:4], ["--allocator-order", "balanced"])

    def test_docker_path_selects_the_docker_runner(self) -> None:
        patch_check = mock.patch.object(reproduce, "check_run")
        patch_dispatch = mock.patch.object(reproduce, "dispatch", return_value=0)
        with quiet(), patch_check, patch_dispatch as dispatched:
            reproduce.cmd_run(self._args(["--path", "docker", "--yes"]))
        self.assertEqual(dispatched.call_args[0][0][1], reproduce.DOCKER_RUNNER)

    def test_extra_arguments_reach_the_runner(self) -> None:
        patch_check = mock.patch.object(reproduce, "check_run")
        patch_dispatch = mock.patch.object(reproduce, "dispatch", return_value=0)
        with quiet(), patch_check, patch_dispatch as dispatched:
            reproduce.cmd_run(self._args(["--yes", "--", "--balanced-run-number", "3"]))
        self.assertEqual(dispatched.call_args[0][0][-2:],
                         ["--balanced-run-number", "3"])


class MenuTests(unittest.TestCase):
    def test_every_menu_entry_parses(self) -> None:
        parser = reproduce.build_parser()
        for key, label, argv in reproduce.MENU:
            with self.subTest(entry=key):
                args = parser.parse_args(argv)
                self.assertTrue(callable(args.func), f"{label} has no handler")

    def test_menu_keys_are_unique(self) -> None:
        keys = [k for k, _, _ in reproduce.MENU]
        self.assertCountEqual(keys, set(keys))

    def test_quitting_the_menu_returns_zero(self) -> None:
        with quiet(), mock.patch("builtins.input", return_value="q"):
            self.assertEqual(reproduce.menu(), 0)

    def test_end_of_input_returns_zero(self) -> None:
        with quiet(), mock.patch("builtins.input", side_effect=EOFError):
            self.assertEqual(reproduce.menu(), 0)

    def test_an_unknown_choice_returns_one(self) -> None:
        with quiet(), mock.patch("builtins.input", return_value="zzz"):
            self.assertEqual(reproduce.menu(), 1)


if __name__ == "__main__":
    unittest.main()
