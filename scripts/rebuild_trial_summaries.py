#!/usr/bin/env python3
"""Rebuild trials.csv and summary.csv from the per-trial redis-benchmark files.

The per-trial CSV files are the raw record: the runner writes each
redis-benchmark invocation's output verbatim. trials.csv and summary.csv are
derived from them, so a fault in the runner's rate extraction can be repaired
without repeating the benchmark.

The script needs only the standard library. It refuses to write anything unless
every trial file in a block parses, and it reports what changed.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path

try:
    from . import trial_bundles
except ImportError:  # pragma: no cover - direct script execution
    import trial_bundles


OPERATION_FOR_SUFFIX = {
    "lpush": "lpush5",
    "lrange": "lrange5",
    "pushread": "pushread5",
}
TRIAL_FILE = re.compile(r"^trial-(\d{4})-([a-z]+)\.csv$")


class RebuildError(RuntimeError):
    """Raised when a block cannot be rebuilt from its raw files."""


def extract_rate(text: str, source: Path) -> float:
    """Return the requests-per-second value from one redis-benchmark CSV line.

    The first field is the test name, which redis-benchmark builds from the
    command line. An EVAL test name embeds the Lua script, and the script
    contains commas, so the line cannot be split on commas. Drop the quoted
    name, then read the field that follows it.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        remainder = re.sub(r'^"[^"]*",', "", line)
        fields = [field.strip().strip('"') for field in remainder.split(",")]
        if not fields or not fields[0]:
            continue
        try:
            rate = float(fields[0])
        except ValueError as error:
            raise RebuildError(f"{source}: cannot read a rate from {line!r}") from error
        if rate <= 0:
            raise RebuildError(f"{source}: non-positive rate {rate}")
        return rate
    raise RebuildError(f"{source}: no CSV data line found")


def read_block(block: Path) -> tuple[dict[tuple[int, str], float], int]:
    """Return {(trial, operation): rate} and the requests-per-trial value."""
    trial_bundles.ensure_extracted(block)
    rates: dict[tuple[int, str], float] = {}
    for path in sorted(block.iterdir()):
        match = TRIAL_FILE.match(path.name)
        if not match:
            continue
        trial = int(match.group(1))
        suffix = match.group(2)
        operation = OPERATION_FOR_SUFFIX.get(suffix)
        if operation is None:
            raise RebuildError(f"{path}: unknown trial file suffix {suffix!r}")
        rates[(trial, operation)] = extract_rate(path.read_text(encoding="utf-8", errors="replace"), path)
    if not rates:
        raise RebuildError(f"{block}: no per-trial files found")
    requests = requests_per_trial(block)
    return rates, requests


def requests_per_trial(block: Path) -> int:
    """Read requests_per_trial from the block metadata."""
    path = block / "memory-before.txt"
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("requests_per_trial="):
                return int(line.split("=", 1)[1])
    # Fall back to the existing trials.csv, whose request column was never
    # affected by the extraction fault.
    trials = block / "trials.csv"
    if trials.is_file():
        with trials.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                return int(row["requests"])
    raise RebuildError(f"{block}: cannot determine requests_per_trial")


def existing_summary(block: Path) -> dict[str, float]:
    path = block / "summary.csv"
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[str, float] = {}
    for row in rows:
        try:
            result[row["operation"]] = float(row["mean_rps"])
        except (KeyError, TypeError, ValueError):
            continue
    return result


def rebuild_block(block: Path, apply_changes: bool) -> dict[str, object]:
    rates, requests = read_block(block)
    operations = sorted({operation for _, operation in rates})
    trials = sorted({trial for trial, _ in rates})
    for trial in trials:
        for operation in operations:
            if (trial, operation) not in rates:
                raise RebuildError(f"{block}: trial {trial} is missing {operation}")

    means = {
        operation: statistics.fmean([rates[(trial, operation)] for trial in trials])
        for operation in operations
    }
    before = existing_summary(block)
    changed = any(
        operation not in before or abs(before[operation] - means[operation]) > 1e-6
        for operation in operations
    )

    if apply_changes:
        with (block / "trials.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("trial", "operation", "requests", "rps"))
            for trial in trials:
                for operation in operations:
                    writer.writerow((trial, operation, requests, f"{rates[(trial, operation)]:.2f}"))
        with (block / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("operation", "mean_rps", "trials"))
            for operation in operations:
                writer.writerow((operation, f"{means[operation]:.6f}", len(trials)))

    return {
        "block": block.name,
        "trials": len(trials),
        "operations": operations,
        "means_before": {op: before.get(op) for op in operations},
        "means_after": means,
        "changed": changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True, help="Directory holding redis/<block>/")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the rebuilt files. Without it the script only reports differences.",
    )
    args = parser.parse_args()

    redis_root = args.raw_dir / "redis"
    if not redis_root.is_dir():
        print(f"FAIL missing directory: {redis_root}", file=sys.stderr)
        return 1

    blocks = [path for path in sorted(redis_root.iterdir()) if path.is_dir()]
    if not blocks:
        print(f"FAIL no blocks under {redis_root}", file=sys.stderr)
        return 1

    changed_count = 0
    try:
        for block in blocks:
            report = rebuild_block(block, args.apply)
            marker = "CHANGED" if report["changed"] else "same"
            if report["changed"]:
                changed_count += 1
            print(f"{marker:8s} {report['block']}  trials={report['trials']}")
            for operation in report["operations"]:
                before = report["means_before"][operation]
                after = report["means_after"][operation]
                shown = "missing" if before is None else f"{before:.2f}"
                print(f"           {operation}: {shown} -> {after:.2f}")
    except (RebuildError, OSError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1

    verb = "rebuilt" if args.apply else "would change"
    print(f"\n{changed_count} of {len(blocks)} blocks {verb}.")
    if changed_count and not args.apply:
        print("Re-run with --apply to write the rebuilt files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
