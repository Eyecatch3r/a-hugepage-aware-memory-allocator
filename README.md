# Temeraire Redis Reproduction Artifact

Reproduction artifact for the Redis case study from:

> A.H. Hunter et al. "Beyond malloc efficiency to fleet efficiency: a hugepage-aware memory allocator." *OSDI 2021.*

This artifact compares historical public TCMalloc with the legacy pageheap against the same codebase with the hugepage-aware Temeraire backend, using repeated Redis list-operation benchmarks. It does **not** reproduce Google's fleet-scale production experiment, which depends on internal infrastructure, production telemetry, and workload diversity unavailable externally.

**Reproduction scope.** Results should be framed as:
> This artifact approximates the Redis case-study methodology from the Temeraire paper using public source code and commodity hardware. It does not reproduce the paper's fleet-scale production evaluation.

## Repository Layout

```
.
├── docker/
│   ├── Dockerfile
│   └── tcmalloc_bazel_wrapper/
├── notes/
│   └── redis-temeraire-reproduction-protocol.tex
├── plots/
│   └── generated/
├── results/
│   ├── processed/
│   └── raw/
├── scripts/
│   ├── check_allocator_preload.sh
│   ├── collect_system_info.sh
│   ├── run_perf.sh
│   ├── run_redis_benchmark.sh
│   └── setup_env.sh
├── docker-compose.yml
└── README.md
```

Third-party sources and build outputs are placed under `third_party/` after setup. Raw benchmark output is written to `results/raw/`.

## Prerequisites

- Docker Engine (Linux containers) or Docker Desktop
- Docker Compose
- Sufficient CPU time for the full benchmark (2000 trials per allocator mode is intentionally expensive)

The container runs with elevated privileges; `perf`, `/proc` inspection, and memory-management observations are otherwise unavailable.

## Source Versions

| Component | Ref |
|---|---|
| Redis | `6.0.9` |
| gperftools | `gperftools-2.16` |
| google/tcmalloc | `8e534f50707469baac732559494559db95732e12` |

The pinned `google/tcmalloc` commit is required: it exposes the `want_no_hpaa` hook needed to build the legacy pageheap variant alongside the Temeraire-capable variant.

## Allocator Modes

| Mode | Purpose |
|---|---|
| `legacy` | **Baseline.** Historical `google/tcmalloc` with `want_no_hpaa` to force the legacy pageheap path. |
| `temeraire` | **Treatment.** Matching build using the hugepage-aware path. |
| `glibc` | Optional. System allocator baseline. |
| `gperftools` | Optional. Open-source gperftools TCMalloc baseline. |

For paper-aligned comparisons, use `legacy` vs. `temeraire` exclusively. The `glibc` and `gperftools` modes provide supplementary context only.

## Benchmark Parameters

Defaults are configured in `docker-compose.yml` and match the paper's experimental shape.

| Variable | Default | Description |
|---|---:|---|
| `REDIS_TRIALS` | `2000` | Trials per allocator mode |
| `REDIS_REQUESTS_PER_TRIAL` | `1000000` | Requests per `redis-benchmark` invocation |
| `REDIS_CLIENTS` | `50` | Concurrent benchmark clients |
| `REDIS_PIPELINE` | `16` | Pipeline depth |
| `BENCH_PORT` | `6380` | Redis server port inside the container |

Any deviation from these defaults must be recorded in the report.

## Usage

### Smoke Test

Run this first to verify the build and both allocator paths before committing to a full run.

```bash
docker compose build
docker compose run --rm temeraire-dev bash -lc "./scripts/setup_env.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/check_allocator_preload.sh"
docker compose run --rm -e REDIS_TRIALS=2 -e REDIS_REQUESTS_PER_TRIAL=1000 temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh legacy"
docker compose run --rm -e REDIS_TRIALS=2 -e REDIS_REQUESTS_PER_TRIAL=1000 temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh temeraire"
```

The preload check must show `libtcmalloc_legacy.so` for `legacy` and `libtcmalloc_temeraire.so` for `temeraire` in `/proc/<pid>/maps`. Do not compare smoke-test results against the paper; reduced parameters are for build validation only.

### Full Experiment

```bash
docker compose run --rm temeraire-dev bash -lc "./scripts/collect_system_info.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh legacy"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh temeraire"
```

### Paper-Closer Single-Machine Run

This path aims to get closer to the Redis case study from the paper while still
running on one machine with public code. It keeps the paper-shaped Redis
benchmark defaults, records richer system metadata, and can optionally run both
release-off and release-on configurations.

```bash
docker compose run --rm temeraire-dev bash -lc "./scripts/setup_env.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_paper_closer_redis_experiment.sh"
```

Useful overrides:

- `RUN_RELEASE_OFF=1` and `RUN_RELEASE_ON=1` control whether to run the two release modes.
- `PAPER_NUMA_NODE=0` pins Redis and `redis-benchmark` to one NUMA node when supported.
- `RUN_PERF=1` adds `perf stat` captures for each allocator mode.
- `PAPER_BACKGROUND_RELEASE_RATE_BPS=<bytes_per_sec>` overrides the allocator background release rate for the release-on runs.

Important caveats:

- This still does **not** reproduce the paper's original execution environment.
- The exact LLVM commit and exact Skylake hardware remain host-dependent deviations.
- The public TCMalloc revision is an approximation chosen to preserve the legacy-vs-Temeraire comparison.

### Optional: Perf Counters

```bash
docker compose run --rm temeraire-dev bash -lc "./scripts/run_perf.sh legacy"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_perf.sh temeraire"
```

Tracked counters: `dTLB-load-misses`, `dTLB-loads`, `cycles`, `instructions`, `page-faults`. Counter availability depends on host kernel and Docker configuration; treat perf output as environment-dependent metadata and verify availability before drawing conclusions.

## Outputs

`run_redis_benchmark.sh` writes timestamped directories to `results/raw/redis/<timestamp>-<allocator>/`:

| File | Contents |
|---|---|
| `trials.csv` | One row per trial and operation |
| `summary.csv` | Mean requests/second per operation |
| `memory-before.txt` | Process and memory metadata before trials |
| `memory-after.txt` | Process, Redis, malloc, and memory metadata after trials |
| `trial-XXXX-{lpush,lrange}.csv` | Raw `redis-benchmark` CSV output per trial |
| `redis-server.log` | Redis server log for the run |

`collect_system_info.sh` writes host and container metadata to `results/raw/system-info/`.

Raw outputs must not be modified. Derived tables, plots, and summaries go in `results/processed/` or `plots/generated/`.

## Known Deviations from the Paper

Expected deviations include host CPU, kernel version, Transparent Huge Page (THP) settings, Docker behavior, compiler version, and the fact that public TCMalloc source is only an approximation of the internal paper artifact. Results should be interpreted accordingly.

## Reproducibility Checklist

The following must be included in any report or result package:

- Git commit of this repository and Docker image rebuild date
- Host OS, Docker version, and kernel version inside the container
- CPU model, core count, memory size, and NUMA topology
- THP `enabled` and `defrag` settings
- Redis version and TCMalloc commit
- Compiler and build flags (if changed from defaults)
- Exact benchmark command lines and all non-default environment variables
- Trial count, request count, client count, and pipeline depth
- Raw result directory names and aggregation method
- All known deviations from the OSDI paper

## Common Pitfalls

- **Allocator verification.** Redis may report `mem_allocator: libc` regardless of `LD_PRELOAD` because it was built with `MALLOC=libc`. Use `check_allocator_preload.sh` to confirm the active shared object via `/proc/<pid>/maps`.
- **THP settings.** Transparent Huge Page configuration comes from the host kernel or Docker VM, not from the image. Always record `enabled` and `defrag` values.
- **Hardware non-equivalence.** Docker improves setup reproducibility but does not produce hardware-identical conditions to the paper environment.
