from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.audit_bare_metal_results import (
    AuditConfig,
    AuditError,
    audit_dataset,
    audit_sensitivity,
    verify_checksum,
)


def write_run(
    redis_dir: Path,
    timestamp: str,
    allocator: str,
    release_mode: str,
    lpush: list[float],
    lrange: list[float],
    release_rate_bps: int = 16_777_216,
    workload: str = "sequential",
    cpu_seconds: float | None = None,
) -> None:
    run = redis_dir / f"{timestamp}-{allocator}-paper-release-{release_mode}"
    run.mkdir(parents=True)
    combined = workload == "combined"
    with (run / "trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("trial", "operation", "requests", "rps"))
        for trial, (lpush_rate, lrange_rate) in enumerate(zip(lpush, lrange), 1):
            if combined:
                writer.writerow((trial, "pushread5", 1_000, lpush_rate))
                (run / f"trial-{trial:04d}-pushread.csv").write_text(
                    f'"eval","{lpush_rate}"\n', encoding="utf-8"
                )
            else:
                writer.writerow((trial, "lpush5", 1_000, lpush_rate))
                writer.writerow((trial, "lrange5", 1_000, lrange_rate))
                (run / f"trial-{trial:04d}-lpush.csv").write_text(
                    f'"lpush","{lpush_rate}"\n', encoding="utf-8"
                )
                (run / f"trial-{trial:04d}-lrange.csv").write_text(
                    f'"lrange","{lrange_rate}"\n', encoding="utf-8"
                )
            (run / f"memory-sample-{trial:04d}.txt").write_text(
                "AnonHugePages: 2048 kB\n", encoding="utf-8"
            )
    if combined:
        (run / "summary.csv").write_text(
            "operation,mean_rps,trials\n"
            f"pushread5,{sum(lpush) / len(lpush):.6f},{len(lpush)}\n",
            encoding="utf-8",
        )
    else:
        (run / "summary.csv").write_text(
            "operation,mean_rps,trials\n"
            f"lpush5,{sum(lpush) / len(lpush):.6f},{len(lpush)}\n"
            f"lrange5,{sum(lrange) / len(lrange):.6f},{len(lrange)}\n",
            encoding="utf-8",
        )
    after = "AnonHugePages: 2048 kB\n"
    if cpu_seconds is not None:
        after += f"used_cpu_user={cpu_seconds / 2:.6f}\nused_cpu_sys={cpu_seconds / 2:.6f}\n"
    (run / "memory-after.txt").write_text(after, encoding="utf-8")
    release_line = ""
    if release_mode == "on":
        release_line = (
            "temeraire-wrapper: background release enabled "
            f"rate_bps={release_rate_bps}\n"
        )
    (run / "redis-server.log").write_text(
        release_line + "Redis is now ready to exit\n", encoding="utf-8"
    )
    (run / "thp-before.txt").write_text(
        "[always] madvise never\n", encoding="utf-8"
    )
    (run / "memory-before.txt").write_text(
        f"allocator={allocator}\nnuma_node=0\nworkload={workload}\n", encoding="utf-8"
    )


def create_dataset(root: Path, workload: str = "sequential", cpu: dict[str, float] | None = None) -> Path:
    raw = root / "raw"
    manifest_dir = raw / "paper-closer" / "20260101T000000Z"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.txt").write_text(
        "trials=2\n"
        "requests_per_trial=1000\n"
        "snapshot_every_trials=1\n"
        "run_release_off=1\n"
        "run_release_on=1\n"
        "allocator_order=balanced\n"
        "balanced_run_number=1\n"
        "release_off_allocator_order=legacy-first\n"
        "release_on_allocator_order=legacy-first\n"
        "background_release_rate_bps_override=16777216\n"
        "execution_environment=native\n"
        "virtualization=none\n",
        encoding="utf-8",
    )
    system = raw / "system-info"
    system.mkdir()
    (system / "20260101T000000Z.txt").write_text(
        'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n'
        "execution_environment=native\n"
        "virtualization=none\n"
        "kernel_release=6.12.95+deb13-amd64\n"
        "[always] madvise never\n"
        "clang version 13.0.0\n"
        "llvm_ref_recorded=cd442157cff4aad209ae532cbf031abbe10bc1df\n"
        "Redis server v=6.0.9\n"
        "8e534f50707469baac732559494559db95732e12\n",
        encoding="utf-8",
    )
    redis = raw / "redis"
    cpu = cpu or {}
    plan = [
        ("20260101T000000Z", "legacy", "off", [80, 80], [120, 120], "legacy-off"),
        ("20260101T000100Z", "temeraire", "off", [84, 84], [126, 126], "temeraire-off"),
        ("20260101T000200Z", "legacy", "on", [80, 80], [120, 120], "legacy-on"),
        ("20260101T000300Z", "temeraire", "on", [88, 88], [132, 132], "temeraire-on"),
    ]
    for stamp, allocator, mode, lpush, lrange, key in plan:
        write_run(
            redis, stamp, allocator, mode, lpush, lrange,
            workload=workload, cpu_seconds=cpu.get(key),
        )
    return raw


SENSITIVITY_RATE_BPS = 67_108_864


def add_sensitivity_pair(raw: Path, timestamp: str, order: str, temeraire_scale: float) -> None:
    """Append one release-on pair at SENSITIVITY_RATE_BPS to an existing dataset."""
    manifest_dir = raw / "paper-closer" / timestamp
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.txt").write_text(
        "trials=2\n"
        "requests_per_trial=1000\n"
        "snapshot_every_trials=1\n"
        "run_release_off=0\n"
        "run_release_on=1\n"
        f"allocator_order={order}\n"
        "balanced_run_number=none\n"
        f"release_on_allocator_order={order}\n"
        f"background_release_rate_bps_override={SENSITIVITY_RATE_BPS}\n"
        "execution_environment=native\n"
        "virtualization=none\n",
        encoding="utf-8",
    )
    system = raw / "system-info"
    (system / f"{timestamp}.txt").write_text(
        (system / "20260101T000000Z.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    redis = raw / "redis"
    legacy_stamp = timestamp
    temeraire_stamp = timestamp[:-2] + "50Z"
    if order == "temeraire-first":
        legacy_stamp, temeraire_stamp = temeraire_stamp, legacy_stamp
    write_run(
        redis, legacy_stamp, "legacy", "on", [80, 80], [120, 120],
        release_rate_bps=SENSITIVITY_RATE_BPS,
    )
    write_run(
        redis, temeraire_stamp, "temeraire", "on",
        [80 * temeraire_scale, 80 * temeraire_scale],
        [120 * temeraire_scale, 120 * temeraire_scale],
        release_rate_bps=SENSITIVITY_RATE_BPS,
    )


class ChecksumTests(unittest.TestCase):
    def test_gnu_checksum_file_is_verified_against_the_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "result.tar.gz"
            archive.write_bytes(b"archive")
            digest = hashlib.sha256(b"archive").hexdigest()
            checksum = root / "result.tar.gz.sha256"
            checksum.write_text(f"{digest}  /remote/result.tar.gz\n", encoding="utf-8")

            self.assertEqual(verify_checksum(archive, checksum), digest)

    def test_checksum_mismatch_stops_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "result.tar.gz"
            archive.write_bytes(b"archive")
            checksum = root / "result.tar.gz.sha256"
            checksum.write_text(f"{'0' * 64}  result.tar.gz\n", encoding="utf-8")

            with self.assertRaisesRegex(AuditError, "checksum mismatch"):
                verify_checksum(archive, checksum)


class DatasetAuditTests(unittest.TestCase):
    def test_complete_balanced_pair_is_recomputed_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            config = AuditConfig(
                raw_dir=raw,
                expected_trials=2,
                expected_requests=1_000,
                expected_pairs=1,
                release_rate_bps=16_777_216,
            )

            report = audit_dataset(config)

        self.assertEqual(report.total_blocks, 4)
        self.assertEqual(report.total_trial_rows, 16)
        self.assertEqual(report.total_raw_trial_files, 16)
        self.assertAlmostEqual(report.pairs[0].release_off_delta_percent, 5.0)
        self.assertAlmostEqual(report.pairs[0].release_on_delta_percent, 10.0)
        self.assertEqual(report.pairs[0].allocator_order, "legacy-first")

    def test_summary_that_does_not_match_trials_stops_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            summary = (
                raw
                / "redis"
                / "20260101T000000Z-legacy-paper-release-off"
                / "summary.csv"
            )
            summary.write_text(
                "operation,mean_rps,trials\nlpush5,999,2\nlrange5,120,2\n",
                encoding="utf-8",
            )
            config = AuditConfig(
                raw_dir=raw,
                expected_trials=2,
                expected_requests=1_000,
                expected_pairs=1,
                release_rate_bps=16_777_216,
            )

            with self.assertRaisesRegex(AuditError, "summary mean mismatch"):
                audit_dataset(config)

    def test_actual_directory_order_must_match_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            manifest = raw / "paper-closer" / "20260101T000000Z" / "manifest.txt"
            contents = manifest.read_text(encoding="utf-8")
            contents = contents.replace("balanced_run_number=1", "balanced_run_number=2")
            contents = contents.replace("legacy-first", "temeraire-first")
            manifest.write_text(contents, encoding="utf-8")
            config = AuditConfig(
                raw_dir=raw,
                expected_trials=2,
                expected_requests=1_000,
                expected_pairs=1,
                release_rate_bps=16_777_216,
            )

            with self.assertRaisesRegex(AuditError, "actual allocator order mismatch"):
                audit_dataset(config)


class WorkloadTests(unittest.TestCase):
    def config(self, raw: Path) -> AuditConfig:
        return AuditConfig(
            raw_dir=raw, expected_trials=2, expected_requests=1_000,
            expected_pairs=1, release_rate_bps=16_777_216,
        )

    def test_combined_workload_uses_the_single_recorded_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary), workload="combined")

            report = audit_dataset(self.config(raw))

        self.assertEqual(report.workloads, ("combined",))
        # One operation per trial, so half the rows and files of the sequential form.
        self.assertEqual(report.total_trial_rows, 8)
        self.assertEqual(report.total_raw_trial_files, 8)
        block = report.pairs[0].blocks[0]
        self.assertIsNone(block.lpush_mean_rps)
        self.assertEqual(block.combined_rps, 80)
        # Temeraire 84 against legacy 80 is +5%, with no harmonic mean involved.
        self.assertAlmostEqual(report.pairs[0].release_off_delta_percent, 5.0)

    def test_sequential_summary_is_rejected_for_a_combined_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary), workload="combined")
            before = raw / "redis" / "20260101T000000Z-legacy-paper-release-off" / "memory-before.txt"
            before.write_text("allocator=legacy\nworkload=sequential\n", encoding="utf-8")

            with self.assertRaisesRegex(AuditError, "expected 4 trial rows"):
                audit_dataset(self.config(raw))

    def test_workload_filter_selects_matching_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary), workload="combined")
            manifest = raw / "paper-closer" / "20260101T000000Z" / "manifest.txt"
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + "workload=combined\n",
                encoding="utf-8",
            )

            wanted = AuditConfig(
                raw_dir=raw, expected_trials=2, expected_requests=1_000,
                expected_pairs=1, workload="combined",
            )
            self.assertEqual(audit_dataset(wanted).workloads, ("combined",))

            # A manifest without the key counts as sequential, so asking for the
            # other reading must find nothing rather than audit the wrong blocks.
            other = AuditConfig(
                raw_dir=raw, expected_trials=2, expected_requests=1_000,
                expected_pairs=1, workload="sequential",
            )
            with self.assertRaisesRegex(AuditError, "expected 1 full manifests, got 0"):
                audit_dataset(other)

    def test_unknown_workload_stops_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            before = raw / "redis" / "20260101T000000Z-legacy-paper-release-off" / "memory-before.txt"
            before.write_text("allocator=legacy\nworkload=interleaved\n", encoding="utf-8")

            with self.assertRaisesRegex(AuditError, "unknown workload"):
                audit_dataset(self.config(raw))


class CpuNormalizationTests(unittest.TestCase):
    def config(self, raw: Path) -> AuditConfig:
        return AuditConfig(
            raw_dir=raw, expected_trials=2, expected_requests=1_000,
            expected_pairs=1, release_rate_bps=16_777_216,
        )

    def test_normalized_delta_divides_requests_by_cpu_seconds(self) -> None:
        # Both allocators serve the same request count. Temeraire is 5% faster in
        # raw throughput but burns 10% more CPU, so the normalized delta is
        # negative even though the unnormalized one is positive.
        cpu = {
            "legacy-off": 100.0, "temeraire-off": 110.0,
            "legacy-on": 100.0, "temeraire-on": 110.0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary), cpu=cpu)

            report = audit_dataset(self.config(raw))

        pair = report.pairs[0]
        self.assertAlmostEqual(pair.release_off_delta_percent, 5.0)
        expected = 100.0 * ((1 / 110.0) / (1 / 100.0) - 1.0)
        self.assertAlmostEqual(pair.release_off_normalized_delta_percent, expected)
        self.assertLess(pair.release_off_normalized_delta_percent, 0.0)
        self.assertIsNotNone(report.release_off_normalized)

    def test_summary_is_withheld_when_any_pair_lacks_cpu_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary), cpu={"legacy-off": 100.0})

            report = audit_dataset(self.config(raw))

        self.assertIsNone(report.release_off_normalized)
        self.assertIsNone(report.pairs[0].release_off_normalized_delta_percent)

    def test_blocks_without_cpu_time_still_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))

            report = audit_dataset(self.config(raw))

        self.assertIsNone(report.release_off_normalized)
        self.assertAlmostEqual(report.pairs[0].release_off_delta_percent, 5.0)


class SensitivityAuditTests(unittest.TestCase):
    def sensitivity_config(self, raw: Path, expected_pairs: int) -> AuditConfig:
        return AuditConfig(
            raw_dir=raw,
            expected_trials=2,
            expected_requests=1_000,
            expected_pairs=expected_pairs,
        )

    def test_release_on_pairs_are_grouped_by_release_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            add_sensitivity_pair(raw, "20260102T000000Z", "legacy-first", 1.05)
            add_sensitivity_pair(raw, "20260103T000000Z", "temeraire-first", 0.95)

            report = audit_sensitivity(self.sensitivity_config(raw, 2))

        self.assertEqual(len(report.rates), 1)
        rate = report.rates[0]
        self.assertEqual(rate.release_rate_bps, SENSITIVITY_RATE_BPS)
        self.assertEqual(rate.release_rate_mib, 64)
        self.assertEqual(report.total_blocks, 4)
        self.assertAlmostEqual(rate.pairs[0].delta_percent, 5.0)
        self.assertAlmostEqual(rate.pairs[1].delta_percent, -5.0)
        self.assertEqual([pair.pair for pair in rate.pairs], [1, 2])
        self.assertEqual(rate.effect.positive_pairs, 1)

    def test_balanced_manifests_are_excluded_from_the_sensitivity_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            add_sensitivity_pair(raw, "20260102T000000Z", "legacy-first", 1.05)

            report = audit_sensitivity(self.sensitivity_config(raw, 1))

        runs = [block.run for rate in report.rates for pair in rate.pairs for block in pair.blocks]
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(run.startswith("20260102T") for run in runs), runs)

    def test_missing_pair_at_a_rate_stops_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            add_sensitivity_pair(raw, "20260102T000000Z", "legacy-first", 1.05)

            with self.assertRaisesRegex(AuditError, "expected 4 pairs at"):
                audit_sensitivity(self.sensitivity_config(raw, 4))

    def test_wrong_release_rate_in_the_log_stops_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = create_dataset(Path(temporary))
            add_sensitivity_pair(raw, "20260102T000000Z", "legacy-first", 1.05)
            log = raw / "redis" / "20260102T000000Z-legacy-paper-release-on" / "redis-server.log"
            log.write_text(
                "temeraire-wrapper: background release enabled rate_bps=16777216\n"
                "Redis is now ready to exit\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AuditError, "missing release-rate confirmation"):
                audit_sensitivity(self.sensitivity_config(raw, 1))


if __name__ == "__main__":
    unittest.main()
