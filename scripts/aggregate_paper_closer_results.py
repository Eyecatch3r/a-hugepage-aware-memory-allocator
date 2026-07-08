#!/usr/bin/env python3
"""Regenerate the paper-closer Redis result table from raw summaries."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RunPair:
    label: str
    order: str
    release_off: bool
    legacy_summary: Path | None
    temeraire_summary: Path


RUN_PAIRS = [
    RunPair(
        "fixed-order",
        "L first",
        True,
        Path("results/raw/redis/20260519T073959Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260519T085048Z-temeraire-paper-release-off/summary.csv"),
    ),
    RunPair(
        "fixed-order",
        "L first",
        False,
        Path("results/raw/redis/20260519T100018Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260519T111125Z-temeraire-paper-release-on/summary.csv"),
    ),
    RunPair(
        "balanced 1",
        "L first",
        True,
        Path("results/raw/redis/20260521T220303Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260521T231307Z-temeraire-paper-release-off/summary.csv"),
    ),
    RunPair(
        "balanced 1",
        "L first",
        False,
        Path("results/raw/redis/20260522T002248Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260522T013145Z-temeraire-paper-release-on/summary.csv"),
    ),
    RunPair(
        "balanced 2",
        "T first",
        True,
        Path("results/raw/redis/20260522T101036Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260522T090113Z-temeraire-paper-release-off/summary.csv"),
    ),
    RunPair(
        "balanced 2",
        "T first",
        False,
        Path("results/raw/redis/20260522T122949Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260522T112025Z-temeraire-paper-release-on/summary.csv"),
    ),
    RunPair(
        "balanced 3",
        "L first",
        True,
        Path("results/raw/redis/20260522T151818Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260522T162735Z-temeraire-paper-release-off/summary.csv"),
    ),
    RunPair(
        "balanced 3",
        "L first",
        False,
        Path("results/raw/redis/20260522T173723Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260522T184739Z-temeraire-paper-release-on/summary.csv"),
    ),
    RunPair(
        "balanced 4",
        "T first",
        True,
        Path("results/raw/redis/20260523T090033Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260523T075102Z-temeraire-paper-release-off/summary.csv"),
    ),
    RunPair(
        "balanced 4",
        "T first",
        False,
        Path("results/raw/redis/20260523T113351Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260523T101319Z-temeraire-paper-release-on/summary.csv"),
    ),
    RunPair(
        "targeted on",
        "T first",
        False,
        Path("results/raw/redis/20260524T105556Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260524T094419Z-temeraire-paper-release-on/summary.csv"),
    ),
]


def read_summary(path: Path) -> dict[str, float]:
    full_path = ROOT / path
    with full_path.open(newline="") as handle:
        rows = {
            row["operation"]: float(row["mean_rps"])
            for row in csv.DictReader(handle)
        }

    missing = {"lpush5", "lrange5"} - rows.keys()
    if missing:
        raise ValueError(f"{path} is missing operations: {', '.join(sorted(missing))}")
    return rows


def combined_push_read_rate(summary: dict[str, float]) -> float:
    lpush = summary["lpush5"]
    lrange = summary["lrange5"]
    return 2.0 / ((1.0 / lpush) + (1.0 / lrange))


def delta_percent(legacy_summary: Path, temeraire_summary: Path) -> float:
    legacy = combined_push_read_rate(read_summary(legacy_summary))
    temeraire = combined_push_read_rate(read_summary(temeraire_summary))
    return ((temeraire / legacy) - 1.0) * 100.0


def signed(value: float) -> str:
    return f"{value:+.2f}%"


def build_rows() -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for pair in RUN_PAIRS:
        row = grouped.setdefault(
            pair.label,
            {"Run": pair.label, "Order": pair.order, "Off": "--", "On": "--"},
        )
        if pair.legacy_summary is None:
            value = "--"
        else:
            value = signed(delta_percent(pair.legacy_summary, pair.temeraire_summary))
        row["Off" if pair.release_off else "On"] = value
    return list(grouped.values())


def print_markdown(rows: list[dict[str, str]]) -> None:
    print("| Run | Order | Off | On |")
    print("|---|---|---:|---:|")
    for row in rows:
        print(f"| {row['Run']} | {row['Order']} | {row['Off']} | {row['On']} |")


def print_csv(rows: list[dict[str, str]]) -> None:
    import sys

    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=["Run", "Order", "Off", "On"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate the paper-closer Redis result table."
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "csv"],
        default="markdown",
        help="Output format.",
    )
    args = parser.parse_args()

    rows = build_rows()
    if args.format == "csv":
        print_csv(rows)
    else:
        print_markdown(rows)


if __name__ == "__main__":
    main()
