from __future__ import annotations

import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import trial_bundles


def make_block(root: Path, name: str = "20260101T000000Z-legacy-paper-release-off") -> Path:
    block = root / "redis" / name
    block.mkdir(parents=True)
    for trial in range(1, 4):
        (block / f"trial-{trial:04d}-lpush.csv").write_text(
            f'"LPUSH","{trial * 100}.5"\n', encoding="utf-8"
        )
        (block / f"trial-{trial:04d}-lrange.csv").write_text(
            f'"LRANGE","{trial * 200}.5"\n', encoding="utf-8"
        )
    (block / "summary.csv").write_text("operation,mean_rps,trials\n", encoding="utf-8")
    return block


class PackAndExtractTests(unittest.TestCase):
    def test_a_packed_block_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = make_block(Path(temporary))
            before = {p.name: p.read_bytes() for p in trial_bundles.loose_trial_files(block)}

            packed = trial_bundles.create_bundle(block, remove_loose=True)
            self.assertEqual(packed, 6)
            self.assertEqual(trial_bundles.loose_trial_files(block), [])

            written = trial_bundles.ensure_extracted(block)

            self.assertEqual(written, 6)
            after = {p.name: p.read_bytes() for p in trial_bundles.loose_trial_files(block)}
            self.assertEqual(before, after)

    def test_extraction_is_skipped_when_the_files_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = make_block(Path(temporary))
            trial_bundles.create_bundle(block)
            marker = block / "trial-0001-lpush.csv"
            marker.write_text('"LPUSH","999"\n', encoding="utf-8")

            self.assertEqual(trial_bundles.ensure_extracted(block), 0)
            # The loose file wins, so a partially rebuilt tree is not overwritten.
            self.assertIn("999", marker.read_text(encoding="utf-8"))

    def test_a_block_without_a_bundle_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = Path(temporary) / "redis" / "empty"
            block.mkdir(parents=True)

            self.assertEqual(trial_bundles.ensure_extracted(block), 0)

    def test_verify_reports_a_full_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = make_block(Path(temporary))
            trial_bundles.create_bundle(block)

            self.assertEqual(trial_bundles.verify_bundle(block), (6, 6))

    def test_verify_notices_a_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = make_block(Path(temporary))
            trial_bundles.create_bundle(block)
            (block / "trial-0001-lpush.csv").write_text('"LPUSH","1"\n', encoding="utf-8")

            bundled, matching = trial_bundles.verify_bundle(block)

            self.assertEqual(bundled, 6)
            self.assertEqual(matching, 5)


class UnsafeBundleTests(unittest.TestCase):
    """A bundle may arrive from elsewhere, and tar can name paths outside the block."""

    def write_bundle_with_member(self, block: Path, member_name: str) -> None:
        payload = block / "payload"
        payload.write_text("x\n", encoding="utf-8")
        with tarfile.open(trial_bundles.bundle_path(block), "w:gz") as archive:
            archive.add(payload, arcname=member_name)
        payload.unlink()

    def test_a_parent_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = Path(temporary) / "redis" / "block"
            block.mkdir(parents=True)
            self.write_bundle_with_member(block, "../trial-0001-lpush.csv")

            with self.assertRaisesRegex(trial_bundles.BundleError, "refusing to unpack"):
                trial_bundles.ensure_extracted(block)

    def test_a_nested_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = Path(temporary) / "redis" / "block"
            block.mkdir(parents=True)
            self.write_bundle_with_member(block, "nested/trial-0001-lpush.csv")

            with self.assertRaisesRegex(trial_bundles.BundleError, "refusing to unpack"):
                trial_bundles.ensure_extracted(block)

    def test_a_non_trial_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            block = Path(temporary) / "redis" / "block"
            block.mkdir(parents=True)
            self.write_bundle_with_member(block, "summary.csv")

            with self.assertRaisesRegex(trial_bundles.BundleError, "refusing to unpack"):
                trial_bundles.ensure_extracted(block)

    def test_member_name_rules(self) -> None:
        self.assertTrue(trial_bundles.safe_member_name("trial-0001-lpush.csv"))
        for unsafe in (
            "/etc/passwd",
            "../trial-0001-lpush.csv",
            "a/trial-0001-lpush.csv",
            "summary.csv",
            "trial-0001-lpush.txt",
        ):
            self.assertFalse(trial_bundles.safe_member_name(unsafe), unsafe)


if __name__ == "__main__":
    unittest.main()
