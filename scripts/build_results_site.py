#!/usr/bin/env python3
"""Build the self-contained data bundle used by the static results explorer."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

try:
    from . import aggregate_paper_closer_results as aggregate
except ImportError:  # pragma: no cover - direct script execution
    import aggregate_paper_closer_results as aggregate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "site/assets/results-data.js"
WSL_RAW_ROOT = ROOT / "results/raw"
BAREMETAL_HISTORICAL_RAW_ROOT = ROOT / "results/node85-import/raw"
BAREMETAL_SENSITIVITY_RAW_ROOT = ROOT / "results/node85-sensitivity-audit/raw"
BAREMETAL_AUDIT = ROOT / "results/processed/node85-audit/audit.json"
NUMBER_PATTERN = re.compile(r"^([A-Za-z]+):\s+(\d+)\s+kB$")
TRIAL_PATTERN = re.compile(r"memory-sample-(\d+)$")


def rounded(value: float) -> float | int:
    result = round(value, 3)
    return int(result) if result.is_integer() else result


def harmonic_mean(first: float, second: float) -> float:
    if first <= 0 or second <= 0:
        raise ValueError("Throughput values must be positive")
    return 2.0 / ((1.0 / first) + (1.0 / second))


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("Cannot calculate a percentile for an empty sequence")
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] + ((values[upper] - values[lower]) * weight)


def build_histogram(values: list[float], bin_count: int) -> list[dict[str, float | int]]:
    if bin_count <= 0:
        raise ValueError("Histogram bin count must be positive")
    low, high = values[0], values[-1]
    if low == high:
        return [{"from": rounded(low), "to": rounded(high), "count": len(values)}]

    width = (high - low) / bin_count
    counts = [0] * bin_count
    for value in values:
        index = min(int((value - low) / width), bin_count - 1)
        counts[index] += 1
    return [
        {
            "from": rounded(low + (index * width)),
            "to": rounded(low + ((index + 1) * width)),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def summarise_values(values: Iterable[float], bin_count: int = 24) -> dict[str, object]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("Cannot summarise an empty sequence")
    return {
        "count": len(ordered),
        "min": rounded(ordered[0]),
        "p05": rounded(percentile(ordered, 0.05)),
        "p25": rounded(percentile(ordered, 0.25)),
        "median": rounded(percentile(ordered, 0.5)),
        "mean": rounded(statistics.fmean(ordered)),
        "p75": rounded(percentile(ordered, 0.75)),
        "p95": rounded(percentile(ordered, 0.95)),
        "max": rounded(ordered[-1]),
        "histogram": build_histogram(ordered, bin_count),
    }


def parse_memory_snapshot(contents: str, trial: int) -> dict[str, object]:
    section = ""
    timestamp = ""
    rss_kib: int | None = None
    anon_huge_kib: int | None = None

    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:]
            continue
        if line.startswith("timestamp_utc="):
            timestamp = line.split("=", 1)[1]
            continue
        match = NUMBER_PATTERN.match(line)
        if not match:
            continue
        key, value = match.groups()
        if section == "status" and key == "VmRSS":
            rss_kib = int(value)
        if section == "smaps_rollup" and key == "AnonHugePages":
            anon_huge_kib = int(value)

    if rss_kib is None:
        raise ValueError("Memory snapshot is missing status/VmRSS")
    return {
        "trial": trial,
        "timestamp": timestamp,
        "rssMiB": rounded(rss_kib / 1024),
        "anonHugeMiB": rounded((anon_huge_kib or 0) / 1024),
    }


def sample_paths(paths: list[Path], maximum: int = 128) -> list[Path]:
    if len(paths) <= maximum:
        return paths
    return [
        paths[round(index * (len(paths) - 1) / (maximum - 1))]
        for index in range(maximum)
    ]


def read_trial_values(run_dir: Path, operation: str) -> list[float]:
    values: list[float] = []
    paths = sample_paths(sorted(run_dir.glob(f"trial-*-{operation}.csv")))
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            row = next(csv.reader(handle), None)
        if row is None or len(row) < 2:
            raise ValueError(f"Malformed trial output: {path}")
        values.append(float(row[1]))
    return values


def read_trial_count(summary_path: Path) -> int:
    with summary_path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle), None)
    if row is None or not row.get("trials"):
        raise ValueError(f"Summary is missing its trial count: {summary_path}")
    return int(row["trials"])


def read_memory_series(run_dir: Path, trial_count: int) -> list[dict[str, object]]:
    snapshots: list[tuple[int, Path]] = []
    before = run_dir / "memory-before.txt"
    if before.is_file():
        snapshots.append((0, before))
    for path in sorted(run_dir.glob("memory-sample-*.txt")):
        match = TRIAL_PATTERN.match(path.stem)
        if match:
            snapshots.append((int(match.group(1)), path))
    after = run_dir / "memory-after.txt"
    if after.is_file() and not any(trial == trial_count for trial, _ in snapshots):
        snapshots.append((trial_count, after))
    return [
        parse_memory_snapshot(path.read_text(encoding="utf-8"), trial)
        for trial, path in sorted(snapshots)
    ]


def read_run(run_dir: Path, allocator: str) -> dict[str, object]:
    summary_path = run_dir / "summary.csv"
    summary = aggregate.read_summary(summary_path)
    trial_count = read_trial_count(summary_path)
    lpush_values = read_trial_values(run_dir, "lpush")
    lrange_values = read_trial_values(run_dir, "lrange")
    if len(lpush_values) != len(lrange_values):
        raise ValueError(f"Mismatched LPUSH and LRANGE trial counts in {run_dir}")

    distributions: dict[str, object] = {}
    if lpush_values:
        combined_values = [
            harmonic_mean(lpush, lrange)
            for lpush, lrange in zip(lpush_values, lrange_values, strict=True)
        ]
        distributions = {
            "combined": summarise_values(combined_values),
            "lpush": summarise_values(lpush_values),
            "lrange": summarise_values(lrange_values),
        }

    return {
        "id": run_dir.name,
        "allocator": allocator,
        "path": run_dir.relative_to(ROOT).as_posix() if run_dir.is_relative_to(ROOT) else run_dir.as_posix(),
        "timestamp": aggregate.run_timestamp(run_dir),
        "trials": trial_count,
        "distributionSampleSize": len(lpush_values),
        "throughput": {
            "combined": rounded(harmonic_mean(summary["lpush5"], summary["lrange5"])),
            "lpush": rounded(summary["lpush5"]),
            "lrange": rounded(summary["lrange5"]),
        },
        "distributions": distributions,
        "memory": read_memory_series(run_dir, trial_count),
    }


def metric_deltas(legacy: dict[str, object], temeraire: dict[str, object]) -> dict[str, float | int]:
    legacy_rates = legacy["throughput"]
    temeraire_rates = temeraire["throughput"]
    assert isinstance(legacy_rates, dict) and isinstance(temeraire_rates, dict)
    return {
        metric: rounded(((float(temeraire_rates[metric]) / float(legacy_rates[metric])) - 1) * 100)
        for metric in ("combined", "lpush", "lrange")
    }


def build_wsl_historical_pairs() -> list[dict[str, object]]:
    pairs: list[dict[str, object]] = []
    for definition in aggregate.HISTORICAL_PAIRS:
        if definition.legacy_summary is None:
            continue
        legacy_path = ROOT / definition.legacy_summary.parent
        temeraire_path = ROOT / definition.temeraire_summary.parent
        legacy = read_run(legacy_path, "legacy")
        temeraire = read_run(temeraire_path, "temeraire")
        release_mode = "off" if definition.release_off else "on"
        pairs.append({
            "id": f"{definition.label.replace(' ', '-')}-{release_mode}",
            "family": definition.label,
            "releaseMode": release_mode,
            "order": definition.order,
            "legacy": legacy,
            "temeraire": temeraire,
            "deltaPercent": metric_deltas(legacy, temeraire),
        })
    return pairs


def build_baremetal_historical_pairs() -> list[dict[str, object]]:
    audit = json.loads(BAREMETAL_AUDIT.read_text(encoding="utf-8"))
    pairs: list[dict[str, object]] = []
    for audited_pair in audit["pairs"]:
        run_number = int(audited_pair["balanced_run"])
        order = "L first" if audited_pair["allocator_order"] == "legacy-first" else "T first"
        blocks = audited_pair["blocks"]
        for release_mode in ("off", "on"):
            legacy_block = next(
                block for block in blocks
                if block["allocator"] == "legacy" and block["release_mode"] == release_mode
            )
            temeraire_block = next(
                block for block in blocks
                if block["allocator"] == "temeraire" and block["release_mode"] == release_mode
            )
            legacy = read_run(BAREMETAL_HISTORICAL_RAW_ROOT / "redis" / legacy_block["run"], "legacy")
            temeraire = read_run(BAREMETAL_HISTORICAL_RAW_ROOT / "redis" / temeraire_block["run"], "temeraire")
            pairs.append({
                "id": f"balanced-{run_number}-{release_mode}",
                "family": f"balanced {run_number}",
                "releaseMode": release_mode,
                "order": order,
                "legacy": legacy,
                "temeraire": temeraire,
                "deltaPercent": metric_deltas(legacy, temeraire),
            })
    return pairs


def build_sensitivity_records(
    raw_root: Path,
    execution_environment: str,
) -> list[dict[str, object]]:
    manifest_paths = sorted((raw_root / "paper-closer").glob("*/manifest.txt"))
    redis_runs = sorted(
        path for path in (raw_root / "redis").glob("*-paper-release-on")
        if path.is_dir()
    )
    records: list[dict[str, object]] = []
    rate_counts: dict[int, int] = {}
    for index, manifest in enumerate(manifest_paths):
        values = aggregate.read_key_values(manifest)
        rate_text = values.get("background_release_rate_bps_override", "")
        if (
            values.get("run_release_on") != "1"
            or values.get("trials") != "2000"
            or not rate_text.isdigit()
            or int(rate_text) <= 0
        ):
            continue
        is_native = values.get("execution_environment") == "native"
        if is_native != (execution_environment == "native"):
            continue

        start = manifest.parent.name
        end = manifest_paths[index + 1].parent.name if index + 1 < len(manifest_paths) else "~"
        candidates = [
            path for path in redis_runs
            if start <= aggregate.run_timestamp(path) < end
        ]
        legacy_runs = [path for path in candidates if "-legacy-" in path.name]
        temeraire_runs = [path for path in candidates if "-temeraire-" in path.name]
        if len(legacy_runs) != 1 or len(temeraire_runs) != 1:
            if not legacy_runs or not temeraire_runs:
                continue
            raise ValueError(
                f"{manifest}: expected one legacy and one Temeraire release-on run; "
                f"found {len(legacy_runs)} and {len(temeraire_runs)}"
            )

        legacy_run = legacy_runs[0]
        temeraire_run = temeraire_runs[0]
        if not (legacy_run / "summary.csv").is_file() or not (temeraire_run / "summary.csv").is_file():
            continue
        expected_confirmation = (
            f"temeraire-wrapper: background release enabled rate_bps={rate_text}"
        )
        for run in (legacy_run, temeraire_run):
            log = run / "redis-server.log"
            if not log.is_file() or expected_confirmation not in log.read_text(
                encoding="utf-8",
                errors="replace",
            ):
                raise ValueError(
                    f"{run}: missing release-rate confirmation: {expected_confirmation}"
                )
        legacy_summary = aggregate.read_summary(legacy_run / "summary.csv")
        temeraire_summary = aggregate.read_summary(temeraire_run / "summary.csv")
        legacy_rates = {
            "combined": harmonic_mean(legacy_summary["lpush5"], legacy_summary["lrange5"]),
            "lpush": legacy_summary["lpush5"],
            "lrange": legacy_summary["lrange5"],
        }
        temeraire_rates = {
            "combined": harmonic_mean(temeraire_summary["lpush5"], temeraire_summary["lrange5"]),
            "lpush": temeraire_summary["lpush5"],
            "lrange": temeraire_summary["lrange5"],
        }
        rate_bps = int(rate_text)
        rate_counts[rate_bps] = rate_counts.get(rate_bps, 0) + 1
        records.append({
            "id": f"{rate_bps / (1024 * 1024):g}-{rate_counts[rate_bps]}",
            "rateMiB": rounded(rate_bps / (1024 * 1024)),
            "pair": rate_counts[rate_bps],
            "order": values.get("release_on_allocator_order", values.get("allocator_order", "unknown")),
            "timestamp": start,
            "legacyRun": legacy_run.relative_to(ROOT).as_posix(),
            "temeraireRun": temeraire_run.relative_to(ROOT).as_posix(),
            "legacyThroughput": {key: rounded(value) for key, value in legacy_rates.items()},
            "temeraireThroughput": {key: rounded(value) for key, value in temeraire_rates.items()},
            "deltaPercent": {
                key: rounded(((temeraire_rates[key] / legacy_rates[key]) - 1) * 100)
                for key in legacy_rates
            },
        })
    return records


def methodology(scope: str) -> dict[str, object]:
    return {
        "workload": "Redis 6.0.9 list operations",
        "allocators": ["Legacy pageheap", "Temeraire"],
        "operations": ["LPUSH ×5", "LRANGE 0–4"],
        "aggregation": "Harmonic mean of LPUSH and LRANGE mean requests per second",
        "scope": scope,
    }


def build_results_data() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "defaultEnvironment": "baremetal",
        "environments": {
            "baremetal": {
                "id": "baremetal",
                "label": "Bare metal",
                "shortLabel": "Native Debian 13 · node85",
                "methodology": methodology(
                    "Native Debian 13 on node85. The test used no virtualization."
                ),
                "outlier": None,
                "historical": build_baremetal_historical_pairs(),
                "releaseSensitivity": build_sensitivity_records(
                    BAREMETAL_SENSITIVITY_RAW_ROOT,
                    "native",
                ),
            },
            "wslDocker": {
                "id": "wslDocker",
                "label": "WSL / Docker",
                "shortLabel": "Docker container · WSL2 kernel",
                "methodology": methodology(
                    "Docker container in WSL2. The container used the shared WSL2 kernel."
                ),
                "outlier": {
                    "id": "balanced-4-on",
                    "label": "balanced 4, release on",
                },
                "historical": build_wsl_historical_pairs(),
                "releaseSensitivity": build_sensitivity_records(
                    WSL_RAW_ROOT,
                    "container",
                ),
            },
        },
    }


def write_javascript_bundle(output: Path, payload: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    output.write_text(f"window.TEMERAIRE_RESULTS = {encoded};\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_results_data()
    write_javascript_bundle(args.output, payload)
    environments = payload["environments"]
    assert isinstance(environments, dict)
    print(
        f"Wrote {sum(len(item['historical']) for item in environments.values())} historical pairs and "
        f"{sum(len(item['releaseSensitivity']) for item in environments.values())} sensitivity pairs "
        f"across {len(environments)} environments to {args.output}"
    )


if __name__ == "__main__":
    main()
