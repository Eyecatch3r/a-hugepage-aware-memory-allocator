#!/usr/bin/env python3
"""Aggregate historical and corrected Redis paper-closer run pairs."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAPER_CLOSER_DIR = ROOT / "results/raw/paper-closer"
REDIS_RESULTS_DIR = ROOT / "results/raw/redis"


@dataclass(frozen=True)
class HistoricalPair:
    label: str
    order: str
    release_off: bool
    legacy_summary: Path | None
    temeraire_summary: Path


@dataclass(frozen=True)
class SensitivityPair:
    timestamp: str
    rate_bps: int
    order: str
    legacy_run: Path
    temeraire_run: Path

    @property
    def rate_mib(self) -> float:
        return self.rate_bps / (1024 * 1024)


HISTORICAL_PAIRS = [
    HistoricalPair("fixed-order", "L first", True,
        Path("results/raw/redis/20260519T073959Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260519T085048Z-temeraire-paper-release-off/summary.csv")),
    HistoricalPair("fixed-order", "L first", False,
        Path("results/raw/redis/20260519T100018Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260519T111125Z-temeraire-paper-release-on/summary.csv")),
    HistoricalPair("balanced 1", "L first", True,
        Path("results/raw/redis/20260521T220303Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260521T231307Z-temeraire-paper-release-off/summary.csv")),
    HistoricalPair("balanced 1", "L first", False,
        Path("results/raw/redis/20260522T002248Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260522T013145Z-temeraire-paper-release-on/summary.csv")),
    HistoricalPair("balanced 2", "T first", True,
        Path("results/raw/redis/20260522T101036Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260522T090113Z-temeraire-paper-release-off/summary.csv")),
    HistoricalPair("balanced 2", "T first", False,
        Path("results/raw/redis/20260522T122949Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260522T112025Z-temeraire-paper-release-on/summary.csv")),
    HistoricalPair("balanced 3", "L first", True,
        Path("results/raw/redis/20260522T151818Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260522T162735Z-temeraire-paper-release-off/summary.csv")),
    HistoricalPair("balanced 3", "L first", False,
        Path("results/raw/redis/20260522T173723Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260522T184739Z-temeraire-paper-release-on/summary.csv")),
    HistoricalPair("balanced 4", "T first", True,
        Path("results/raw/redis/20260523T090033Z-legacy-paper-release-off/summary.csv"),
        Path("results/raw/redis/20260523T075102Z-temeraire-paper-release-off/summary.csv")),
    HistoricalPair("balanced 4", "T first", False,
        Path("results/raw/redis/20260523T113351Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260523T101319Z-temeraire-paper-release-on/summary.csv")),
    HistoricalPair("targeted on", "T first", False,
        Path("results/raw/redis/20260524T105556Z-legacy-paper-release-on/summary.csv"),
        Path("results/raw/redis/20260524T094419Z-temeraire-paper-release-on/summary.csv")),
]


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def read_summary(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = {
            row["operation"]: float(row["mean_rps"])
            for row in csv.DictReader(handle)
        }
    missing = {"lpush5", "lrange5"} - rows.keys()
    if missing:
        raise ValueError(f"{path} is missing operations: {', '.join(sorted(missing))}")
    return rows


def combined_rate(summary_path: Path) -> float:
    summary = read_summary(summary_path)
    return 2.0 / ((1.0 / summary["lpush5"]) + (1.0 / summary["lrange5"]))


def delta_percent(legacy_summary: Path, temeraire_summary: Path) -> float:
    legacy = combined_rate(legacy_summary)
    temeraire = combined_rate(temeraire_summary)
    return ((temeraire / legacy) - 1.0) * 100.0


def signed(value: float) -> str:
    return f"{value:+.2f}%"


def run_timestamp(path: Path) -> str:
    return path.name.split("-", 1)[0]


def discover_sensitivity_pairs() -> list[SensitivityPair]:
    manifests: list[tuple[str, Path, dict[str, str]]] = []
    for manifest in sorted(PAPER_CLOSER_DIR.glob("*/manifest.txt")):
        values = read_key_values(manifest)
        rate = values.get("background_release_rate_bps_override", "")
        if values.get("run_release_on") != "1" or not rate.isdigit() or int(rate) <= 0:
            continue
        manifests.append((manifest.parent.name, manifest, values))

    redis_runs = sorted(
        path for path in REDIS_RESULTS_DIR.glob("*-paper-release-on")
        if path.is_dir()
    )
    pairs: list[SensitivityPair] = []
    for index, (start, manifest, values) in enumerate(manifests):
        end = manifests[index + 1][0] if index + 1 < len(manifests) else "~"
        candidates = [
            path for path in redis_runs
            if start <= run_timestamp(path) < end
        ]
        legacy = [path for path in candidates if "-legacy-" in path.name]
        temeraire = [path for path in candidates if "-temeraire-" in path.name]
        if len(legacy) != 1 or len(temeraire) != 1:
            raise ValueError(
                f"{manifest}: expected one legacy and one Temeraire run in "
                f"[{start}, {end}), found {len(legacy)} and {len(temeraire)}"
            )

        rate_bps = int(values["background_release_rate_bps_override"])
        expected = f"temeraire-wrapper: background release enabled rate_bps={rate_bps}"
        for run in (legacy[0], temeraire[0]):
            summary = run / "summary.csv"
            log = run / "redis-server.log"
            if not summary.is_file() or not log.is_file():
                raise ValueError(f"Incomplete Redis run: {run}")
            confirmations = {
                line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
            }
            if expected not in confirmations:
                raise ValueError(f"{run}: missing release-rate confirmation: {expected}")

        pairs.append(SensitivityPair(
            timestamp=start,
            rate_bps=rate_bps,
            order=values.get("release_on_allocator_order", values.get("allocator_order", "unknown")),
            legacy_run=legacy[0],
            temeraire_run=temeraire[0],
        ))
    return pairs


def historical_rows() -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for pair in HISTORICAL_PAIRS:
        row = grouped.setdefault(
            pair.label,
            {"Run": pair.label, "Order": pair.order, "Off": "--", "On": "--"},
        )
        value = "--" if pair.legacy_summary is None else signed(delta_percent(
            ROOT / pair.legacy_summary, ROOT / pair.temeraire_summary
        ))
        row["Off" if pair.release_off else "On"] = value
    return list(grouped.values())


def print_historical(fmt: str) -> None:
    rows = historical_rows()
    if fmt == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=["Run", "Order", "Off", "On"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return
    print("| Run | Order | Off | On |")
    print("|---|---|---:|---:|")
    for row in rows:
        print(f"| {row['Run']} | {row['Order']} | {row['Off']} | {row['On']} |")


def sensitivity_records(pairs: list[SensitivityPair]) -> list[dict[str, object]]:
    counts: dict[int, int] = {}
    records: list[dict[str, object]] = []
    for pair in pairs:
        counts[pair.rate_bps] = counts.get(pair.rate_bps, 0) + 1
        records.append({
            "rate_bps": pair.rate_bps,
            "rate_mib": pair.rate_mib,
            "pair": counts[pair.rate_bps],
            "order": pair.order,
            "timestamp": pair.timestamp,
            "legacy_run": pair.legacy_run.relative_to(ROOT).as_posix(),
            "temeraire_run": pair.temeraire_run.relative_to(ROOT).as_posix(),
            "delta_percent": delta_percent(pair.legacy_run / "summary.csv", pair.temeraire_run / "summary.csv"),
        })
    return records


def format_rate(rate_mib: float) -> str:
    return f"{rate_mib:g} MiB/s"


def print_sensitivity(fmt: str) -> None:
    records = sensitivity_records(discover_sensitivity_pairs())
    if not records:
        raise ValueError("No corrected positive-rate release-on manifests were found")
    if fmt == "csv":
        fields = ["rate_bps", "rate_mib", "pair", "order", "timestamp",
                  "legacy_run", "temeraire_run", "delta_percent"]
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
        return

    grouped: dict[int, list[dict[str, object]]] = {}
    for record in records:
        grouped.setdefault(int(record["rate_bps"]), []).append(record)
    max_pairs = max(len(group) for group in grouped.values())
    headers = ["Rate", *(f"Pair {i}" for i in range(1, max_pairs + 1)), "Median", "Mean"]
    print("| " + " | ".join(headers) + " |")
    print("|---" + "|---:" * (len(headers) - 1) + "|")
    for rate_bps, group in grouped.items():
        deltas = [float(record["delta_percent"]) for record in group]
        cells = [format_rate(float(group[0]["rate_mib"])), *(signed(value) for value in deltas)]
        cells.extend("--" for _ in range(max_pairs - len(deltas)))
        cells.extend([signed(statistics.median(deltas)), signed(statistics.mean(deltas))])
        print("| " + " | ".join(cells) + " |")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate Redis reproduction results")
    parser.add_argument(
        "--dataset", choices=["sensitivity", "historical", "all"], default="sensitivity",
        help="Result family to print (default: corrected release-on sensitivity matrix)",
    )
    parser.add_argument("--format", choices=["markdown", "csv"], default="markdown")
    args = parser.parse_args()

    if args.dataset == "all" and args.format == "csv":
        parser.error("--dataset all is Markdown-only; select one dataset for CSV output")

    try:
        if args.dataset in {"historical", "all"}:
            print_historical(args.format)
        if args.dataset == "all":
            print()
        if args.dataset in {"sensitivity", "all"}:
            print_sensitivity(args.format)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
