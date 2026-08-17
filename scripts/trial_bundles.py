#!/usr/bin/env python3
"""Pack and unpack the per-trial benchmark output of a block.

Each allocator block writes one CSV file per trial and operation, so a full
series comes to roughly half a million files of about 52 bytes each. Git handles
the volume badly: the content is 24 MB but the file count pushes a clone past
four minutes. One gzipped tar per block holds the same data in about 76 KB, so
the whole set travels as 182 files instead.

Readers call `ensure_extracted` before they glob for trial files. It unpacks a
block's bundle only when the loose files are absent, so a fresh clone pays the
cost once per block it actually reads and every later run finds the files in
place.

Run this module directly to pack or verify bundles.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path
from typing import Iterator

BUNDLE_NAME = "trial-outputs.tar.gz"
TRIAL_PREFIX = "trial-"
TRIAL_SUFFIX = ".csv"


class BundleError(RuntimeError):
    """Raised when a bundle is missing, malformed, or unsafe to unpack."""


def bundle_path(block_dir: Path) -> Path:
    return block_dir / BUNDLE_NAME


def loose_trial_files(block_dir: Path) -> list[Path]:
    return sorted(block_dir.glob(f"{TRIAL_PREFIX}*{TRIAL_SUFFIX}"))


def iter_blocks(raw_root: Path) -> Iterator[Path]:
    """Yield every block directory under a raw results tree."""
    redis_root = raw_root / "redis"
    if not redis_root.is_dir():
        return
    for path in sorted(redis_root.iterdir()):
        if path.is_dir():
            yield path


def safe_member_name(name: str) -> bool:
    """Whether a tar member is a plain trial file in the block's own directory.

    Bundles are built by this module, but a reader may receive one from
    elsewhere, and tar archives can name absolute or parent paths. Only
    `trial-*.csv` with no directory component is accepted.
    """
    if name.startswith("/") or name.startswith("\\"):
        return False
    if ".." in Path(name).parts:
        return False
    if len(Path(name).parts) != 1:
        return False
    return name.startswith(TRIAL_PREFIX) and name.endswith(TRIAL_SUFFIX)


def create_bundle(block_dir: Path, remove_loose: bool = False) -> int:
    """Pack a block's trial files. Returns the number packed."""
    files = loose_trial_files(block_dir)
    if not files:
        return 0
    target = bundle_path(block_dir)
    temporary = target.with_suffix(".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.name)
    temporary.replace(target)
    if remove_loose:
        for path in files:
            path.unlink()
    return len(files)


def ensure_extracted(block_dir: Path) -> int:
    """Unpack a block's bundle if the loose trial files are not already there.

    Returns the number of files written, or 0 when nothing was needed. A block
    with neither loose files nor a bundle is left alone; the caller decides
    whether that is an error.
    """
    if loose_trial_files(block_dir):
        return 0
    source = bundle_path(block_dir)
    if not source.is_file():
        return 0
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if not member.isfile() or not safe_member_name(member.name):
                raise BundleError(f"{source}: refusing to unpack member {member.name!r}")
        archive.extractall(block_dir, members=members)
    return len(members)


def ensure_tree_extracted(raw_root: Path) -> int:
    """Unpack every bundled block under a raw results tree."""
    return sum(ensure_extracted(block) for block in iter_blocks(raw_root))


def verify_bundle(block_dir: Path) -> tuple[int, int]:
    """Compare a bundle against the loose files beside it.

    Returns (bundled, matching). Only meaningful while both forms are present.
    """
    source = bundle_path(block_dir)
    if not source.is_file():
        return (0, 0)
    loose = {path.name: path.read_bytes() for path in loose_trial_files(block_dir)}
    matching = 0
    with tarfile.open(source, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        for member in members:
            handle = archive.extractfile(member)
            if handle is not None and loose.get(member.name) == handle.read():
                matching += 1
    return (len(members), matching)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_dirs", nargs="+", type=Path, help="Raw results trees to work on")
    parser.add_argument("--pack", action="store_true", help="Create a bundle for each block")
    parser.add_argument("--unpack", action="store_true", help="Extract every bundle")
    parser.add_argument("--verify", action="store_true", help="Compare bundles against loose files")
    parser.add_argument(
        "--remove-loose", action="store_true",
        help="With --pack, delete the loose files once the bundle is written",
    )
    args = parser.parse_args()
    if not (args.pack or args.unpack or args.verify):
        parser.error("choose one of --pack, --unpack, or --verify")

    totals = {"blocks": 0, "files": 0, "mismatched": 0}
    try:
        for raw_dir in args.raw_dirs:
            for block in iter_blocks(raw_dir):
                if args.pack:
                    count = create_bundle(block, remove_loose=args.remove_loose)
                elif args.unpack:
                    count = ensure_extracted(block)
                else:
                    bundled, matching = verify_bundle(block)
                    count = bundled
                    if bundled != matching:
                        totals["mismatched"] += 1
                        print(f"MISMATCH {block}: {matching}/{bundled} files match", file=sys.stderr)
                if count:
                    totals["blocks"] += 1
                    totals["files"] += count
    except (BundleError, OSError, tarfile.TarError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1

    verb = "packed" if args.pack else ("extracted" if args.unpack else "checked")
    print(f"{verb} {totals['files']} trial files across {totals['blocks']} blocks")
    if totals["mismatched"]:
        print(f"{totals['mismatched']} blocks did not match", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
