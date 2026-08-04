from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.rebuild_trial_summaries import RebuildError, extract_rate, rebuild_block


SOURCE = Path("trial-0001-pushread.csv")


class ExtractRateTests(unittest.TestCase):
    def test_plain_command_name(self) -> None:
        self.assertAlmostEqual(extract_rate('"LPUSH","123456.78"\n', SOURCE), 123456.78)

    def test_eval_name_containing_commas(self) -> None:
        # The reason the runner's original extraction failed: redis-benchmark
        # builds the test name from the command line, and a Lua script is full
        # of commas, so splitting the line on commas finds the wrong field.
        line = (
            '"eval redis.call(\'LPUSH\', KEYS[1], \'v1\', \'v2\', \'v3\', \'v4\', \'v5\'); '
            "return redis.call('LRANGE', KEYS[1], 0, 4) 1 benchmark:list\",\"116859.09\"\n"
        )
        self.assertAlmostEqual(extract_rate(line, SOURCE), 116859.09)

    def test_extra_latency_columns_are_ignored(self) -> None:
        line = '"LPUSH","123456.78","0.512","0.100","0.500","0.900","1.200"\n'
        self.assertAlmostEqual(extract_rate(line, SOURCE), 123456.78)

    def test_missing_data_line_is_an_error(self) -> None:
        with self.assertRaisesRegex(RebuildError, "no CSV data line"):
            extract_rate("not a csv line\n", SOURCE)

    def test_non_positive_rate_is_an_error(self) -> None:
        with self.assertRaisesRegex(RebuildError, "non-positive rate"):
            extract_rate('"LPUSH","0"\n', SOURCE)


class RebuildBlockTests(unittest.TestCase):
    def make_block(self, root: Path, rates: list[float]) -> Path:
        block = root / "redis" / "20260101T000000Z-legacy-paper-release-off"
        block.mkdir(parents=True)
        (block / "memory-before.txt").write_text(
            "allocator=legacy\nrequests_per_trial=1000\n", encoding="utf-8"
        )
        for index, rate in enumerate(rates, 1):
            (block / f"trial-{index:04d}-pushread.csv").write_text(
                f'"eval a, b 1 k","{rate}"\n', encoding="utf-8"
            )
        # A summary carrying the fault the runner produced.
        (block / "summary.csv").write_text(
            "operation,mean_rps,trials\npushread5,0.000000,2\n", encoding="utf-8"
        )
        return block

    def test_dry_run_reports_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = self.make_block(Path(temporary), [100.0, 200.0])

            report = rebuild_block(block, apply_changes=False)

            self.assertTrue(report["changed"])
            self.assertAlmostEqual(report["means_after"]["pushread5"], 150.0)
            self.assertIn("0.000000", (block / "summary.csv").read_text(encoding="utf-8"))

    def test_apply_writes_trials_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = self.make_block(Path(temporary), [100.0, 200.0])

            rebuild_block(block, apply_changes=True)

            summary = (block / "summary.csv").read_text(encoding="utf-8")
            self.assertIn("pushread5,150.000000,2", summary)
            trials = (block / "trials.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(trials[0], "trial,operation,requests,rps")
            self.assertEqual(trials[1], "1,pushread5,1000,100.00")
            self.assertEqual(trials[2], "2,pushread5,1000,200.00")

    def test_a_missing_operation_stops_the_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = self.make_block(Path(temporary), [100.0, 200.0])
            (block / "trial-0002-lpush.csv").write_text('"LPUSH","5"\n', encoding="utf-8")

            with self.assertRaisesRegex(RebuildError, "missing"):
                rebuild_block(block, apply_changes=False)


if __name__ == "__main__":
    unittest.main()
