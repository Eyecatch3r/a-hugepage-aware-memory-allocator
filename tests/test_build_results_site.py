from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_results_site import (
    harmonic_mean,
    parse_memory_snapshot,
    read_run,
    summarise_values,
    write_javascript_bundle,
)


class ResultMathTests(unittest.TestCase):
    def test_harmonic_mean_matches_the_benchmark_aggregation_rule(self) -> None:
        self.assertAlmostEqual(harmonic_mean(800_000, 1_200_000), 960_000)

    def test_summary_includes_quantiles_and_a_fixed_histogram(self) -> None:
        summary = summarise_values([10, 20, 30, 40], bin_count=2)

        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["median"], 25)
        self.assertEqual(summary["p25"], 17.5)
        self.assertEqual(summary["p75"], 32.5)
        self.assertEqual(summary["histogram"], [
            {"from": 10, "to": 25, "count": 2},
            {"from": 25, "to": 40, "count": 2},
        ])


class ResultParsingTests(unittest.TestCase):
    def test_memory_parser_uses_process_huge_pages_not_host_thp_state(self) -> None:
        snapshot = parse_memory_snapshot(
            """timestamp_utc=20260521T221203Z
## status
VmRSS: 27592 kB
## smaps_rollup
AnonHugePages: 10240 kB
## thp_state
AnonHugePages: 32768 kB
""",
            trial=250,
        )

        self.assertEqual(snapshot, {
            "trial": 250,
            "timestamp": "20260521T221203Z",
            "rssMiB": 26.945,
            "anonHugeMiB": 10.0,
        })

    def test_read_run_collects_summary_trials_and_memory_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "20260101T010101Z-legacy-paper-release-off"
            run_dir.mkdir()
            (run_dir / "summary.csv").write_text(
                "operation,mean_rps,trials\n"
                "lpush5,800000,2\n"
                "lrange5,1200000,2\n",
                encoding="utf-8",
            )
            (run_dir / "trial-0001-lpush.csv").write_text('"lpush","700000"\n', encoding="utf-8")
            (run_dir / "trial-0002-lpush.csv").write_text('"lpush","900000"\n', encoding="utf-8")
            (run_dir / "trial-0001-lrange.csv").write_text('"lrange","1100000"\n', encoding="utf-8")
            (run_dir / "trial-0002-lrange.csv").write_text('"lrange","1300000"\n', encoding="utf-8")
            (run_dir / "memory-before.txt").write_text(
                "timestamp_utc=20260101T010101Z\n## status\nVmRSS: 1024 kB\n"
                "## smaps_rollup\nAnonHugePages: 0 kB\n",
                encoding="utf-8",
            )
            (run_dir / "memory-sample-0002.txt").write_text(
                "timestamp_utc=20260101T010201Z\n## status\nVmRSS: 2048 kB\n"
                "## smaps_rollup\nAnonHugePages: 1024 kB\n",
                encoding="utf-8",
            )

            run = read_run(run_dir, "legacy")

        self.assertEqual(run["throughput"]["combined"], 960000)
        self.assertEqual(run["distributions"]["lpush"]["median"], 800000)
        self.assertEqual(run["memory"][-1]["trial"], 2)
        self.assertEqual(run["memory"][-1]["rssMiB"], 2)


class BundleTests(unittest.TestCase):
    def test_javascript_bundle_is_file_protocol_safe_and_script_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "results-data.js"
            write_javascript_bundle(output, {"note": "</script>"})
            contents = output.read_text(encoding="utf-8")

        self.assertTrue(contents.startswith("window.TEMERAIRE_RESULTS = "))
        self.assertNotIn("</script>", contents)
        payload = contents.removeprefix("window.TEMERAIRE_RESULTS = ").removesuffix(";\n")
        self.assertEqual(json.loads(payload), {"note": "</script>"})


if __name__ == "__main__":
    unittest.main()
