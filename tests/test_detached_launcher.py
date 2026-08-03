import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "launch_bare_metal_rerun_detached.sh"
BASH = os.environ.get("TEMERAIRE_TEST_BASH") or shutil.which("bash")


@unittest.skipUnless(BASH, "bash is required")
class DetachedLauncherTests(unittest.TestCase):
    def test_launches_runner_with_environment_and_records_exit_status(self) -> None:
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            work = Path(temporary)
            fake_setsid = work / "setsid"
            fake_runner = work / "runner.sh"
            output = work / "runner-output.txt"

            fake_setsid.write_text(
                "#!/usr/bin/env bash\n"
                "[[ \"$1\" == --fork ]] && shift\n"
                '"$@" &\n',
                encoding="utf-8",
            )
            fake_runner.write_text(
                "#!/usr/bin/env bash\n"
                'printf "workload=%s\\narg=%s\\n" "$PAPER_WORKLOAD" "$1" > "$FAKE_OUTPUT"\n'
                "sleep 2\n"
                "exit 7\n",
                encoding="utf-8",
            )
            fake_setsid.chmod(0o755)
            fake_runner.chmod(0o755)
            relative_work = work.relative_to(ROOT).as_posix()

            environment = os.environ.copy()
            environment.update(
                {
                    "DETACHED_SETSID": f"{relative_work}/setsid",
                    "TEMERAIRE_DETACHED_RUNNER": f"{relative_work}/runner.sh",
                    "TEMERAIRE_DETACHED_LOG_DIR": relative_work,
                    "TEMERAIRE_LAUNCH_ID": "test-run",
                    "PAPER_WORKLOAD": "combined",
                    "FAKE_OUTPUT": f"{relative_work}/runner-output.txt",
                }
            )
            result = subprocess.run(
                [BASH, str(LAUNCHER.relative_to(ROOT)), "--allocator-order", "balanced"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("test-run.log", result.stdout)
            self.assertIn("test-run.pid", result.stdout)
            output_deadline = time.monotonic() + 5
            while not output.exists() and time.monotonic() < output_deadline:
                time.sleep(0.05)
            self.assertTrue(output.exists(), "detached runner did not start")
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "workload=combined\narg=--allocator-order\n",
            )

            status = work / "test-run.status"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if status.exists() and "exit_code=" in status.read_text(encoding="utf-8"):
                    break
                time.sleep(0.05)
            self.assertTrue(status.exists(), "detached wrapper did not write status")
            self.assertIn("exit_code=7", status.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
