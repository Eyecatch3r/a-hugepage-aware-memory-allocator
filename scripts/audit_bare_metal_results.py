#!/usr/bin/env python3
"""Audit and summarize the bare-metal Redis experiment from raw artifacts.

The script has two modes. The balanced mode audits the four balanced pairs that
run release-off and release-on at one release rate. The sensitivity mode audits
the release-on pairs at each additional release rate.

The script intentionally uses only the Python standard library. It fails closed:
missing blocks, malformed CSV, mismatched summaries, incorrect release settings,
or inconsistent system snapshots stop the audit with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable


EXPECTED_OPERATIONS = ("lpush5", "lrange5")
EXPECTED_LLVM_REF = "cd442157cff4aad209ae532cbf031abbe10bc1df"
EXPECTED_TCMALLOC_REF = "8e534f50707469baac732559494559db95732e12"
EXPECTED_REDIS_VERSION = "6.0.9"
T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    12: 2.179,
    15: 2.131,
    20: 2.086,
    30: 2.042,
}


class AuditError(RuntimeError):
    """Raised when a required reproducibility check fails."""


@dataclass(frozen=True)
class AuditConfig:
    raw_dir: Path
    expected_trials: int = 2_000
    expected_requests: int = 1_000_000
    expected_pairs: int = 4
    release_rate_bps: int = 16_777_216
    expected_llvm_ref: str = EXPECTED_LLVM_REF
    expected_tcmalloc_ref: str = EXPECTED_TCMALLOC_REF
    expected_redis_version: str = EXPECTED_REDIS_VERSION


@dataclass(frozen=True)
class BlockResult:
    run: str
    allocator: str
    release_mode: str
    lpush_mean_rps: float
    lrange_mean_rps: float
    combined_rps: float
    trial_rows: int
    raw_trial_files: int
    memory_samples: int
    release_confirmations: int


@dataclass(frozen=True)
class PairResult:
    manifest_timestamp: str
    balanced_run: int
    allocator_order: str
    release_off_delta_percent: float
    release_on_delta_percent: float
    blocks: tuple[BlockResult, ...]


@dataclass(frozen=True)
class EffectSummary:
    arithmetic_mean_percent: float
    median_percent: float
    standard_deviation_percent: float
    geometric_mean_percent: float
    interval_95_low_percent: float | None
    interval_95_high_percent: float | None
    positive_pairs: int
    pair_count: int


@dataclass(frozen=True)
class AuditReport:
    raw_dir: str
    total_blocks: int
    total_trial_rows: int
    total_raw_trial_files: int
    total_memory_samples: int
    pairs: tuple[PairResult, ...]
    release_off: EffectSummary
    release_on: EffectSummary
    environment: dict[str, str]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    archive_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SensitivityPairResult:
    manifest_timestamp: str
    release_rate_bps: int
    allocator_order: str
    pair: int
    delta_percent: float
    blocks: tuple[BlockResult, ...]


@dataclass(frozen=True)
class RateResult:
    release_rate_bps: int
    release_rate_mib: float
    pairs: tuple[SensitivityPairResult, ...]
    effect: EffectSummary


@dataclass(frozen=True)
class SensitivityReport:
    raw_dir: str
    total_blocks: int
    total_trial_rows: int
    total_raw_trial_files: int
    total_memory_samples: int
    rates: tuple[RateResult, ...]
    environment: dict[str, str]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    archive_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(archive: Path, checksum_file: Path) -> str:
    require(archive.is_file(), f"archive does not exist: {archive}")
    require(checksum_file.is_file(), f"checksum file does not exist: {checksum_file}")
    fields = checksum_file.read_text(encoding="utf-8").strip().split()
    require(bool(fields), f"checksum file is empty: {checksum_file}")
    expected = fields[0].lower()
    require(len(expected) == 64, f"invalid SHA-256 value in {checksum_file}")
    actual = file_sha256(archive)
    require(actual == expected, f"checksum mismatch: expected {expected}, got {actual}")
    return actual


def harmonic_mean(left: float, right: float) -> float:
    require(left > 0 and right > 0, "throughput values must be positive")
    return 2.0 / ((1.0 / left) + (1.0 / right))


def run_timestamp(path: Path) -> str:
    return path.name.split("-", 1)[0]


def read_summary(path: Path) -> dict[str, float]:
    require(path.is_file(), f"missing summary: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == 2, f"expected two summary rows: {path}")
    try:
        values = {row["operation"]: float(row["mean_rps"]) for row in rows}
    except (KeyError, TypeError, ValueError) as error:
        raise AuditError(f"malformed summary: {path}: {error}") from error
    require(set(values) == set(EXPECTED_OPERATIONS), f"unexpected summary operations: {path}")
    return values


def read_trials(run: Path, config: AuditConfig) -> dict[str, list[float]]:
    path = run / "trials.csv"
    require(path.is_file(), f"missing trials.csv: {run}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    expected_rows = config.expected_trials * len(EXPECTED_OPERATIONS)
    require(len(rows) == expected_rows, f"expected {expected_rows} trial rows in {path}, got {len(rows)}")
    values = {operation: [] for operation in EXPECTED_OPERATIONS}
    seen: set[tuple[int, str]] = set()
    for row_number, row in enumerate(rows, 2):
        try:
            trial = int(row["trial"])
            operation = row["operation"]
            requests = int(row["requests"])
            rate = float(row["rps"])
        except (KeyError, TypeError, ValueError) as error:
            raise AuditError(f"malformed trial row {row_number} in {path}: {error}") from error
        require(1 <= trial <= config.expected_trials, f"trial ID out of range in {path}: {trial}")
        require(operation in values, f"unexpected operation in {path}: {operation}")
        require(requests == config.expected_requests, f"request count mismatch in {path}: {requests}")
        require(math.isfinite(rate) and rate > 0, f"invalid throughput in {path}: {rate}")
        require((trial, operation) not in seen, f"duplicate trial/operation in {path}: {trial}/{operation}")
        seen.add((trial, operation))
        values[operation].append(rate)
    for operation in EXPECTED_OPERATIONS:
        require(len(values[operation]) == config.expected_trials, f"missing {operation} trials in {path}")
    return values


def expected_order(balanced_run: int) -> str:
    return "legacy-first" if balanced_run % 2 else "temeraire-first"


def audit_block(
    run: Path,
    allocator: str,
    release_mode: str,
    snapshot_every: int,
    config: AuditConfig,
) -> BlockResult:
    trials = read_trials(run, config)
    summary = read_summary(run / "summary.csv")
    for operation in EXPECTED_OPERATIONS:
        recomputed = statistics.fmean(trials[operation])
        require(
            math.isclose(recomputed, summary[operation], abs_tol=1e-5),
            f"summary mean mismatch in {run} for {operation}: "
            f"summary={summary[operation]}, recomputed={recomputed}",
        )
    raw_files = list(run.glob("trial-????-*.csv"))
    expected_raw_files = config.expected_trials * len(EXPECTED_OPERATIONS)
    require(len(raw_files) == expected_raw_files, f"raw trial file count mismatch in {run}")
    samples = list(run.glob("memory-sample-????.txt"))
    expected_samples = config.expected_trials // snapshot_every
    require(len(samples) == expected_samples, f"memory sample count mismatch in {run}")
    log_path = run / "redis-server.log"
    require(log_path.is_file(), f"missing Redis log: {run}")
    log = log_path.read_text(encoding="utf-8", errors="replace")
    confirmation = (
        "temeraire-wrapper: background release enabled "
        f"rate_bps={config.release_rate_bps}"
    )
    confirmation_count = log.count(confirmation)
    if release_mode == "on":
        require(confirmation_count > 0, f"missing release-rate confirmation in {run}")
    else:
        require(confirmation_count == 0, f"release enabled unexpectedly in {run}")
    require("Redis is now ready to exit" in log, f"clean shutdown marker missing in {run}")
    thp_path = run / "thp-before.txt"
    require(thp_path.is_file(), f"missing THP record: {run}")
    require("[always]" in thp_path.read_text(errors="replace"), f"THP was not set to always in {run}")
    memory_before = read_key_values(run / "memory-before.txt")
    require(memory_before.get("allocator") == allocator, f"allocator metadata mismatch in {run}")
    return BlockResult(
        run=run.name,
        allocator=allocator,
        release_mode=release_mode,
        lpush_mean_rps=summary["lpush5"],
        lrange_mean_rps=summary["lrange5"],
        combined_rps=harmonic_mean(summary["lpush5"], summary["lrange5"]),
        trial_rows=config.expected_trials * len(EXPECTED_OPERATIONS),
        raw_trial_files=len(raw_files),
        memory_samples=len(samples),
        release_confirmations=confirmation_count,
    )


def environment_record(path: Path, config: AuditConfig) -> dict[str, str]:
    require(path.is_file(), f"missing system snapshot: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    required_tokens = {
        "execution_environment": "execution_environment=native",
        "virtualization": "virtualization=none",
        "redis_version": f"Redis server v={config.expected_redis_version}",
        "llvm_ref": f"llvm_ref_recorded={config.expected_llvm_ref}",
        "tcmalloc_ref": config.expected_tcmalloc_ref,
        "thp": "[always] madvise never",
    }
    for label, token in required_tokens.items():
        require(token in text, f"system snapshot {path} is missing {label}: {token}")
    record = read_key_values(path)
    return {
        "execution_environment": record.get("execution_environment", "unknown"),
        "virtualization": record.get("virtualization", "unknown"),
        "kernel_release": record.get("kernel_release", "unknown"),
        "llvm_ref": config.expected_llvm_ref,
        "tcmalloc_ref": config.expected_tcmalloc_ref,
        "redis_version": config.expected_redis_version,
        "thp_policy": "always",
    }


def effect_summary(deltas: Iterable[float]) -> EffectSummary:
    values = list(deltas)
    require(bool(values), "cannot summarize an empty effect collection")
    logs = [math.log1p(value / 100.0) for value in values]
    mean_log = statistics.fmean(logs)
    low: float | None = None
    high: float | None = None
    if len(logs) > 1:
        critical = t_critical_95(len(logs) - 1)
        margin = critical * statistics.stdev(logs) / math.sqrt(len(logs))
        low = 100.0 * math.expm1(mean_log - margin)
        high = 100.0 * math.expm1(mean_log + margin)
    return EffectSummary(
        arithmetic_mean_percent=statistics.fmean(values),
        median_percent=statistics.median(values),
        standard_deviation_percent=statistics.stdev(values) if len(values) > 1 else 0.0,
        geometric_mean_percent=100.0 * math.expm1(mean_log),
        interval_95_low_percent=low,
        interval_95_high_percent=high,
        positive_pairs=sum(value > 0 for value in values),
        pair_count=len(values),
    )


def t_critical_95(degrees_of_freedom: int) -> float:
    if degrees_of_freedom in T_CRITICAL_95:
        return T_CRITICAL_95[degrees_of_freedom]
    lower = [df for df in T_CRITICAL_95 if df <= degrees_of_freedom]
    return T_CRITICAL_95[max(lower)] if lower else T_CRITICAL_95[1]


def read_manifests(manifest_root: Path) -> list[tuple[str, dict[str, str]]]:
    """Return every manifest in timestamp order."""
    require(manifest_root.is_dir(), f"missing manifest directory: {manifest_root}")
    return [
        (path.parent.name, read_key_values(path))
        for path in sorted(manifest_root.glob("*/manifest.txt"))
    ]


def block_windows(all_manifests: list[tuple[str, dict[str, str]]]) -> dict[str, str]:
    """Map each manifest timestamp to the timestamp that ends its block window.

    A run directory belongs to the newest manifest that is not newer than the
    run. The end of the last window is a value that sorts after any timestamp.
    """
    timestamps = [timestamp for timestamp, _ in all_manifests]
    windows: dict[str, str] = {}
    for index, timestamp in enumerate(timestamps):
        windows[timestamp] = timestamps[index + 1] if index + 1 < len(timestamps) else "~"
    return windows


def audit_dataset(config: AuditConfig, archive_sha256: str | None = None) -> AuditReport:
    manifest_root = config.raw_dir / "paper-closer"
    redis_root = config.raw_dir / "redis"
    system_root = config.raw_dir / "system-info"
    require(redis_root.is_dir(), f"missing Redis directory: {redis_root}")
    all_manifests = read_manifests(manifest_root)
    windows = block_windows(all_manifests)
    manifests = [
        (timestamp, values)
        for timestamp, values in all_manifests
        if values.get("trials") == str(config.expected_trials)
        and values.get("allocator_order") == "balanced"
    ]
    require(len(manifests) == config.expected_pairs, f"expected {config.expected_pairs} full manifests, got {len(manifests)}")
    balanced_runs = [int(values.get("balanced_run_number", "0")) for _, values in manifests]
    require(
        balanced_runs == list(range(min(balanced_runs), min(balanced_runs) + len(balanced_runs))),
        f"balanced run numbers are not consecutive: {balanced_runs}",
    )
    redis_runs = sorted(path for path in redis_root.glob("*-paper-release-*") if path.is_dir())
    pairs: list[PairResult] = []
    environments: list[dict[str, str]] = []
    warnings: list[str] = []
    for start, manifest in manifests:
        end = windows[start]
        runs = [path for path in redis_runs if start <= run_timestamp(path) < end]
        require(len(runs) == 4, f"expected four allocator blocks for {start}, got {len(runs)}")
        balanced_run = int(manifest.get("balanced_run_number", "0"))
        order = expected_order(balanced_run)
        require(manifest.get("run_release_off") == "1", f"release-off was not requested in {start}")
        require(manifest.get("run_release_on") == "1", f"release-on was not requested in {start}")
        require(manifest.get("release_off_allocator_order") == order, f"release-off order mismatch in {start}")
        require(manifest.get("release_on_allocator_order") == order, f"release-on order mismatch in {start}")
        require(manifest.get("background_release_rate_bps_override") == str(config.release_rate_bps), f"release rate mismatch in {start}")
        require(manifest.get("requests_per_trial") == str(config.expected_requests), f"request count mismatch in {start}")
        snapshot_every = int(manifest.get("snapshot_every_trials", "0"))
        require(snapshot_every > 0, f"invalid snapshot interval in {start}")
        blocks: list[BlockResult] = []
        for release_mode in ("off", "on"):
            mode_runs = sorted(path for path in runs if f"release-{release_mode}" in path.name)
            actual_order = ["legacy" if "-legacy-" in path.name else "temeraire" for path in mode_runs]
            expected_allocator_order = (
                ["legacy", "temeraire"]
                if order == "legacy-first"
                else ["temeraire", "legacy"]
            )
            require(
                actual_order == expected_allocator_order,
                f"actual allocator order mismatch for {start}/{release_mode}: "
                f"expected {expected_allocator_order}, got {actual_order}",
            )
            for allocator in ("legacy", "temeraire"):
                candidates = [
                    path for path in runs
                    if f"-{allocator}-" in path.name and f"release-{release_mode}" in path.name
                ]
                require(len(candidates) == 1, f"expected one {allocator}/{release_mode} block for {start}")
                blocks.append(audit_block(candidates[0], allocator, release_mode, snapshot_every, config))
        by_key = {(block.release_mode, block.allocator): block for block in blocks}
        off_delta = percent_delta(by_key[("off", "legacy")], by_key[("off", "temeraire")])
        on_delta = percent_delta(by_key[("on", "legacy")], by_key[("on", "temeraire")])
        pairs.append(PairResult(start, balanced_run, order, off_delta, on_delta, tuple(blocks)))
        environments.append(environment_record(system_root / f"{start}.txt", config))
    first_environment = environments[0]
    require(all(record == first_environment for record in environments), "full-run system snapshots are inconsistent")
    if not any("scaling_governor" in path.read_text(errors="replace") for path in system_root.glob("*.txt")):
        warnings.append("CPU governor, frequency, turbo state, and temperature are not recorded.")
    if not all("libtcmalloc" in (config.raw_dir / "redis" / block.run / "memory-before.txt").read_text(errors="replace") for pair in pairs for block in pair.blocks):
        warnings.append("Per-block /proc allocator mappings are absent; preload was verified separately before the run.")
    return AuditReport(
        raw_dir=str(config.raw_dir),
        total_blocks=sum(len(pair.blocks) for pair in pairs),
        total_trial_rows=sum(block.trial_rows for pair in pairs for block in pair.blocks),
        total_raw_trial_files=sum(block.raw_trial_files for pair in pairs for block in pair.blocks),
        total_memory_samples=sum(block.memory_samples for pair in pairs for block in pair.blocks),
        pairs=tuple(pairs),
        release_off=effect_summary(pair.release_off_delta_percent for pair in pairs),
        release_on=effect_summary(pair.release_on_delta_percent for pair in pairs),
        environment=first_environment,
        warnings=tuple(warnings),
        archive_sha256=archive_sha256,
    )


def percent_delta(legacy: BlockResult, temeraire: BlockResult) -> float:
    return 100.0 * ((temeraire.combined_rps / legacy.combined_rps) - 1.0)


def audit_sensitivity(config: AuditConfig, archive_sha256: str | None = None) -> SensitivityReport:
    """Audit the release-on pairs that run at one release rate each.

    A sensitivity manifest requests release-on only, sets a positive release
    rate, and names one allocator order. Each manifest owns two allocator
    blocks. The balanced manifests request both release modes, so this function
    skips them.
    """
    manifest_root = config.raw_dir / "paper-closer"
    redis_root = config.raw_dir / "redis"
    system_root = config.raw_dir / "system-info"
    require(redis_root.is_dir(), f"missing Redis directory: {redis_root}")
    all_manifests = read_manifests(manifest_root)
    windows = block_windows(all_manifests)
    manifests = [
        (timestamp, values)
        for timestamp, values in all_manifests
        if values.get("trials") == str(config.expected_trials)
        and values.get("run_release_off") == "0"
        and values.get("run_release_on") == "1"
        and values.get("background_release_rate_bps_override", "").isdigit()
        and int(values.get("background_release_rate_bps_override", "0")) > 0
    ]
    require(bool(manifests), f"no sensitivity manifests found in {manifest_root}")
    redis_runs = sorted(path for path in redis_root.glob("*-paper-release-on") if path.is_dir())
    pairs: list[SensitivityPairResult] = []
    environments: list[dict[str, str]] = []
    warnings: list[str] = []
    counts: dict[int, int] = {}
    for start, manifest in manifests:
        end = windows[start]
        runs = [path for path in redis_runs if start <= run_timestamp(path) < end]
        require(len(runs) == 2, f"expected two allocator blocks for {start}, got {len(runs)}")
        rate = int(manifest["background_release_rate_bps_override"])
        order = manifest.get("release_on_allocator_order", manifest.get("allocator_order", ""))
        require(order in {"legacy-first", "temeraire-first"}, f"unknown allocator order in {start}: {order}")
        require(manifest.get("requests_per_trial") == str(config.expected_requests), f"request count mismatch in {start}")
        snapshot_every = int(manifest.get("snapshot_every_trials", "0"))
        require(snapshot_every > 0, f"invalid snapshot interval in {start}")
        rate_config = replace(config, release_rate_bps=rate)
        actual_order = ["legacy" if "-legacy-" in path.name else "temeraire" for path in runs]
        expected_allocator_order = (
            ["legacy", "temeraire"] if order == "legacy-first" else ["temeraire", "legacy"]
        )
        require(
            actual_order == expected_allocator_order,
            f"actual allocator order mismatch for {start}: "
            f"expected {expected_allocator_order}, got {actual_order}",
        )
        blocks: list[BlockResult] = []
        for allocator in ("legacy", "temeraire"):
            candidates = [path for path in runs if f"-{allocator}-" in path.name]
            require(len(candidates) == 1, f"expected one {allocator} block for {start}")
            blocks.append(audit_block(candidates[0], allocator, "on", snapshot_every, rate_config))
        by_allocator = {block.allocator: block for block in blocks}
        counts[rate] = counts.get(rate, 0) + 1
        pairs.append(SensitivityPairResult(
            manifest_timestamp=start,
            release_rate_bps=rate,
            allocator_order=order,
            pair=counts[rate],
            delta_percent=percent_delta(by_allocator["legacy"], by_allocator["temeraire"]),
            blocks=tuple(blocks),
        ))
        environments.append(environment_record(system_root / f"{start}.txt", config))
    first_environment = environments[0]
    require(all(record == first_environment for record in environments), "sensitivity system snapshots are inconsistent")
    rates: list[RateResult] = []
    for rate in sorted(counts):
        rate_pairs = tuple(pair for pair in pairs if pair.release_rate_bps == rate)
        require(
            len(rate_pairs) == config.expected_pairs,
            f"expected {config.expected_pairs} pairs at {rate} bytes per second, got {len(rate_pairs)}",
        )
        rates.append(RateResult(
            release_rate_bps=rate,
            release_rate_mib=rate / (1024 * 1024),
            pairs=rate_pairs,
            effect=effect_summary(pair.delta_percent for pair in rate_pairs),
        ))
    if not any("scaling_governor" in path.read_text(errors="replace") for path in system_root.glob("*.txt")):
        warnings.append("CPU governor, frequency, turbo state, and temperature are not recorded.")
    if not all("libtcmalloc" in (redis_root / block.run / "memory-before.txt").read_text(errors="replace") for pair in pairs for block in pair.blocks):
        warnings.append("Per-block /proc allocator mappings are absent; preload was verified separately before the run.")
    warnings.append("The paper does not state a release rate. Each rate is a local experimental parameter.")
    return SensitivityReport(
        raw_dir=str(config.raw_dir),
        total_blocks=sum(len(pair.blocks) for pair in pairs),
        total_trial_rows=sum(block.trial_rows for pair in pairs for block in pair.blocks),
        total_raw_trial_files=sum(block.raw_trial_files for pair in pairs for block in pair.blocks),
        total_memory_samples=sum(block.memory_samples for pair in pairs for block in pair.blocks),
        rates=tuple(rates),
        environment=first_environment,
        warnings=tuple(warnings),
        archive_sha256=archive_sha256,
    )


def write_pair_csv(path: Path, report: AuditReport) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("balanced_run", "allocator_order", "release_off_delta_percent", "release_on_delta_percent"))
        for pair in report.pairs:
            writer.writerow((pair.balanced_run, pair.allocator_order, f"{pair.release_off_delta_percent:.6f}", f"{pair.release_on_delta_percent:.6f}"))
        writer.writerow(("mean", "", f"{report.release_off.arithmetic_mean_percent:.6f}", f"{report.release_on.arithmetic_mean_percent:.6f}"))
        writer.writerow(("median", "", f"{report.release_off.median_percent:.6f}", f"{report.release_on.median_percent:.6f}"))


def format_interval(effect: EffectSummary) -> str:
    if effect.interval_95_low_percent is None or effect.interval_95_high_percent is None:
        return "not available"
    return f"[{effect.interval_95_low_percent:+.3f}%, {effect.interval_95_high_percent:+.3f}%]"


def write_markdown(path: Path, report: AuditReport) -> None:
    lines = [
        "# Bare-metal Redis audit",
        "",
        f"- Raw directory: `{report.raw_dir}`",
        f"- Archive SHA-256: `{report.archive_sha256 or 'not checked'}`",
        f"- Full allocator blocks: {report.total_blocks}",
        f"- Aggregate trial rows: {report.total_trial_rows}",
        f"- Raw trial files: {report.total_raw_trial_files}",
        f"- Memory samples: {report.total_memory_samples}",
        "",
        "## Pair results",
        "",
        "| Balanced run | Order | Release off | Release on |",
        "|---:|---|---:|---:|",
    ]
    for pair in report.pairs:
        lines.append(f"| {pair.balanced_run} | {pair.allocator_order} | {pair.release_off_delta_percent:+.3f}% | {pair.release_on_delta_percent:+.3f}% |")
    lines.extend([
        "",
        "## Run-level summary",
        "",
        "| Mode | Mean | Median | Approximate 95% t interval | Positive pairs |",
        "|---|---:|---:|---:|---:|",
        f"| Release off | {report.release_off.arithmetic_mean_percent:+.3f}% | {report.release_off.median_percent:+.3f}% | {format_interval(report.release_off)} | {report.release_off.positive_pairs}/{report.release_off.pair_count} |",
        f"| Release on | {report.release_on.arithmetic_mean_percent:+.3f}% | {report.release_on.median_percent:+.3f}% | {format_interval(report.release_on)} | {report.release_on.positive_pairs}/{report.release_on.pair_count} |",
        "",
        "The interval treats complete run pairs as the replication unit. It does not treat the sequential trials within a block as independent replications.",
        "",
        "## Warnings",
        "",
    ])
    lines.extend(f"- {warning}" for warning in report.warnings)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_rate(rate_mib: float) -> str:
    return f"{rate_mib:g} MiB/s"


def write_rate_csv(path: Path, report: SensitivityReport) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow((
            "release_rate_mib_per_s",
            "pair",
            "allocator_order",
            "manifest_timestamp",
            "delta_percent",
        ))
        for rate in report.rates:
            for pair in rate.pairs:
                writer.writerow((
                    f"{rate.release_rate_mib:g}",
                    pair.pair,
                    pair.allocator_order,
                    pair.manifest_timestamp,
                    f"{pair.delta_percent:.6f}",
                ))
            writer.writerow((f"{rate.release_rate_mib:g}", "mean", "", "", f"{rate.effect.arithmetic_mean_percent:.6f}"))
            writer.writerow((f"{rate.release_rate_mib:g}", "median", "", "", f"{rate.effect.median_percent:.6f}"))


def write_sensitivity_markdown(path: Path, report: SensitivityReport) -> None:
    pair_count = max(len(rate.pairs) for rate in report.rates)
    header = ["Rate", *(f"Pair {index}" for index in range(1, pair_count + 1)), "Mean", "Median"]
    lines = [
        "# Bare-metal release-rate sensitivity audit",
        "",
        f"- Raw directory: `{report.raw_dir}`",
        f"- Archive SHA-256: `{report.archive_sha256 or 'not checked'}`",
        f"- Release-on allocator blocks: {report.total_blocks}",
        f"- Aggregate trial rows: {report.total_trial_rows}",
        f"- Raw trial files: {report.total_raw_trial_files}",
        f"- Memory samples: {report.total_memory_samples}",
        "",
        "## Pair results",
        "",
        "| " + " | ".join(header) + " |",
        "|---" + "|---:" * (len(header) - 1) + "|",
    ]
    for rate in report.rates:
        cells = [format_rate(rate.release_rate_mib)]
        cells.extend(f"{pair.delta_percent:+.3f}%" for pair in rate.pairs)
        cells.extend("--" for _ in range(pair_count - len(rate.pairs)))
        cells.append(f"{rate.effect.arithmetic_mean_percent:+.3f}%")
        cells.append(f"{rate.effect.median_percent:+.3f}%")
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend([
        "",
        "## Rate-level summary",
        "",
        "| Rate | Mean | Median | Approximate 95% t interval | Positive pairs |",
        "|---|---:|---:|---:|---:|",
    ])
    for rate in report.rates:
        lines.append(
            f"| {format_rate(rate.release_rate_mib)} "
            f"| {rate.effect.arithmetic_mean_percent:+.3f}% "
            f"| {rate.effect.median_percent:+.3f}% "
            f"| {format_interval(rate.effect)} "
            f"| {rate.effect.positive_pairs}/{rate.effect.pair_count} |"
        )
    lines.extend([
        "",
        "The interval treats complete run pairs as the replication unit. It does not treat the sequential trials within a block as independent replications.",
        "",
        "## Warnings",
        "",
    ])
    lines.extend(f"- {warning}" for warning in report.warnings)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(output_dir: Path, report: AuditReport) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_pair_csv(output_dir / "pair-summary.csv", report)
    write_markdown(output_dir / "audit.md", report)


def write_sensitivity_outputs(output_dir: Path, report: SensitivityReport) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_rate_csv(output_dir / "rate-summary.csv", report)
    write_sensitivity_markdown(output_dir / "audit.md", report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("results/node85-import/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/processed/node85-audit"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument("--expected-trials", type=int, default=2_000)
    parser.add_argument("--expected-requests", type=int, default=1_000_000)
    parser.add_argument("--expected-pairs", type=int, default=4)
    parser.add_argument("--release-rate-bps", type=int, default=16_777_216)
    parser.add_argument(
        "--mode",
        choices=("balanced", "sensitivity"),
        default="balanced",
        help=(
            "balanced audits the release-off and release-on pairs at one rate. "
            "sensitivity audits the release-on pairs at each rate in the manifests."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive_sha256: str | None = None
    try:
        if bool(args.archive) != bool(args.checksum):
            raise AuditError("--archive and --checksum must be supplied together")
        if args.archive:
            archive_sha256 = verify_checksum(args.archive, args.checksum)
            print(f"PASS archive checksum: {archive_sha256}")
        config = AuditConfig(
            raw_dir=args.raw_dir,
            expected_trials=args.expected_trials,
            expected_requests=args.expected_requests,
            expected_pairs=args.expected_pairs,
            release_rate_bps=args.release_rate_bps,
        )
        if args.mode == "sensitivity":
            sensitivity = audit_sensitivity(config, archive_sha256)
            write_sensitivity_outputs(args.output_dir, sensitivity)
            report_totals(sensitivity)
            for rate in sensitivity.rates:
                print(
                    f"{format_rate(rate.release_rate_mib)} mean/median: "
                    f"{rate.effect.arithmetic_mean_percent:+.3f}% / "
                    f"{rate.effect.median_percent:+.3f}%"
                )
            for warning in sensitivity.warnings:
                print(f"WARN {warning}")
            print(f"Wrote {args.output_dir}")
            return 0
        report = audit_dataset(config, archive_sha256)
        write_outputs(args.output_dir, report)
    except (AuditError, OSError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"PASS full pairs: {len(report.pairs)}/{args.expected_pairs}")
    report_totals(report)
    print(f"release off mean/median: {report.release_off.arithmetic_mean_percent:+.3f}% / {report.release_off.median_percent:+.3f}%")
    print(f"release on mean/median: {report.release_on.arithmetic_mean_percent:+.3f}% / {report.release_on.median_percent:+.3f}%")
    for warning in report.warnings:
        print(f"WARN {warning}")
    print(f"Wrote {args.output_dir}")
    return 0


def report_totals(report: AuditReport | SensitivityReport) -> None:
    print(f"PASS allocator blocks: {report.total_blocks}")
    print(f"PASS aggregate trial rows: {report.total_trial_rows}")
    print(f"PASS raw trial files: {report.total_raw_trial_files}")
    print(f"PASS memory samples: {report.total_memory_samples}")


if __name__ == "__main__":
    raise SystemExit(main())
