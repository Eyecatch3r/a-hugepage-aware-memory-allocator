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
    verify_checksum,
)


def write_run(
    redis_dir: Path,
    timestamp: str,
    allocator: str,
    release_mode: str,
    lpush: list[float],
    lrange: list[float],
) -> None:
    run = redis_dir / f"{timestamp}-{allocator}-paper-release-{release_mode}"
    run.mkdir(parents=True)
    with (run / "trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("trial", "operation", "requests", "rps"))
        for trial, (lpush_rate, lrange_rate) in enumerate(zip(lpush, lrange), 1):
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
    (run / "summary.csv").write_text(
        "operation,mean_rps,trials\n"
        f"lpush5,{sum(lpush) / len(lpush):.6f},{len(lpush)}\n"
        f"lrange5,{sum(lrange) / len(lrange):.6f},{len(lrange)}\n",
        encoding="utf-8",
    )
    release_line = ""
    if release_mode == "on":
        release_line = (
            "temeraire-wrapper: background release enabled "
            "rate_bps=16777216\n"
        )
    (run / "redis-server.log").write_text(
        release_line + "Redis is now ready to exit\n", encoding="utf-8"
    )
    (run / "thp-before.txt").write_text(
        "[always] madvise never\n", encoding="utf-8"
    )
    (run / "memory-before.txt").write_text(
        f"allocator={allocator}\nnuma_node=0\n", encoding="utf-8"
    )


def create_dataset(root: Path) -> Path:
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
    write_run(redis, "20260101T000000Z", "legacy", "off", [80, 80], [120, 120])
    write_run(redis, "20260101T000100Z", "temeraire", "off", [84, 84], [126, 126])
    write_run(redis, "20260101T000200Z", "legacy", "on", [80, 80], [120, 120])
    write_run(redis, "20260101T000300Z", "temeraire", "on", [88, 88], [132, 132])
    return raw


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


if __name__ == "__main__":
    unittest.main()
