# Temeraire Redis Reproduction Artifact

This repository contains a Docker-based reproduction artifact for the Redis case
study from:

> A.H. Hunter et al. "Beyond malloc efficiency to fleet efficiency: a
> hugepage-aware memory allocator." OSDI 2021.

The artifact focuses on the part of the paper that can be reproduced with
public code and commodity hardware: comparing a historical public TCMalloc build
using the legacy pageheap against the same public code path with the
hugepage-aware Temeraire backend.

It does not attempt to reproduce Google's warehouse-scale fleet experiment, the
1% production experiment, or the final rollout. Those results depend on
Google-internal infrastructure, production telemetry, workload diversity, and
hardware that are not available here.

## Reproduction Claim

This project is intended to support the following limited claim:

> The Redis case-study methodology from the Temeraire paper can be approximated
> with public Redis and TCMalloc code by running repeated Redis list-operation
> benchmarks under legacy TCMalloc and Temeraire allocator modes.

The project is not a byte-identical reconstruction of the paper environment.
Expected deviations include host CPU, kernel version, Transparent Huge Page
(THP) settings, Docker behavior, compiler version, and the fact that the public
TCMalloc source is only an approximation of the internal paper artifact.

## What Is Reproduced

The paper describes a Redis experiment with:

- Redis 6.0.9.
- TCMalloc's legacy pageheap as the baseline.
- Temeraire as the hugepage-aware allocator variant.
- 2000 trials of `redis-benchmark` per configuration.
- 1,000,000 requests per trial.
- A workload that pushes five list elements and reads those five elements.

This repository encodes that experiment shape in `scripts/run_redis_benchmark.sh`.
It also records system, allocator, THP, and benchmark metadata so results can be
interpreted and compared later.

## Repository Layout

```text
.
|-- docker/
|   |-- Dockerfile
|   `-- tcmalloc_bazel_wrapper/
|-- notes/
|   `-- redis-temeraire-reproduction-protocol.tex
|-- plots/
|   `-- generated/
|-- results/
|   |-- processed/
|   `-- raw/
|-- scripts/
|   |-- check_allocator_preload.sh
|   |-- collect_system_info.sh
|   |-- run_perf.sh
|   |-- run_redis_benchmark.sh
|   `-- setup_env.sh
|-- docker-compose.yml
`-- README.md
```

Generated third-party sources and build outputs live under `third_party/` after
setup. Raw benchmark output is written to `results/raw/`.

## Prerequisites

- Docker Desktop or Docker Engine with Linux containers.
- Docker Compose.
- Enough CPU time for the full Redis experiment. The default 2000-trial runs are
  intentionally expensive.

The container runs with elevated privileges because `perf`, `/proc` inspection,
and memory-management observations are otherwise limited or unavailable.

## Quick Smoke Test

Use this path first to verify that the artifact builds and that both allocator
modes can run. The smoke test intentionally reduces the benchmark size.

```bash
docker compose build
docker compose run --rm temeraire-dev bash -lc "./scripts/setup_env.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/check_allocator_preload.sh"
docker compose run --rm -e REDIS_TRIALS=2 -e REDIS_REQUESTS_PER_TRIAL=1000 temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh legacy"
docker compose run --rm -e REDIS_TRIALS=2 -e REDIS_REQUESTS_PER_TRIAL=1000 temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh temeraire"
```

The preload check should show `libtcmalloc_legacy.so` for `legacy` mode and
`libtcmalloc_temeraire.so` for `temeraire` mode in `/proc/<pid>/maps`.

## Full Experiment

After the smoke test succeeds, collect environment metadata and run both
allocator modes with the paper-shaped defaults from `docker-compose.yml`.

```bash
docker compose run --rm temeraire-dev bash -lc "./scripts/collect_system_info.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh legacy"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh temeraire"
```

Default benchmark parameters:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `REDIS_TRIALS` | `2000` | Trials per allocator mode |
| `REDIS_REQUESTS_PER_TRIAL` | `1000000` | Requests per Redis benchmark invocation |
| `REDIS_CLIENTS` | `50` | Concurrent benchmark clients |
| `REDIS_PIPELINE` | `16` | Redis benchmark pipeline depth |
| `BENCH_PORT` | `6380` | Redis server port inside the container |

If any value is changed, record it in the report and in result notes.

## Optional Perf Collection

`scripts/run_perf.sh` records a smaller `perf stat` run for one allocator mode.

```bash
docker compose run --rm temeraire-dev bash -lc "./scripts/run_perf.sh legacy"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_perf.sh temeraire"
```

The script tracks:

- `dTLB-load-misses`
- `dTLB-loads`
- `cycles`
- `instructions`
- `page-faults`

Depending on the host kernel and Docker setup, some counters may be unavailable
or multiplexed. Treat perf output as environment-dependent metadata unless the
counter availability is verified.

## Outputs

`scripts/run_redis_benchmark.sh` creates timestamped directories under:

```text
results/raw/redis/<timestamp>-<allocator>/
```

Each run directory contains:

- `trials.csv`: one row per trial and operation.
- `summary.csv`: mean requests per second per operation.
- `memory-before.txt`: process and memory metadata before the trials.
- `memory-after.txt`: process, Redis, malloc, and memory metadata after the
  trials.
- `trial-XXXX-lpush.csv` and `trial-XXXX-lrange.csv`: raw Redis benchmark CSV
  output for each trial.
- `redis-server.log`: Redis server log for the run.

`scripts/collect_system_info.sh` writes timestamped host/container metadata to:

```text
results/raw/system-info/
```

Keep raw outputs unchanged. Put derived tables, plots, and summaries in
`results/processed/` or `plots/generated/`.

## Exact Source Versions

The default pinned sources are configured in `docker-compose.yml`:

| Component | Ref |
| --- | --- |
| Redis | `6.0.9` |
| gperftools | `gperftools-2.16` |
| google/tcmalloc | `8e534f50707469baac732559494559db95732e12` |

The historical `google/tcmalloc` ref is used because it still exposes the
`want_no_hpaa` hook needed to build a legacy pageheap variant next to the
Temeraire-capable variant.

## Allocator Modes

`scripts/run_redis_benchmark.sh` accepts four modes:

| Mode | Purpose |
| --- | --- |
| `legacy` | Main baseline: historical public `google/tcmalloc` with `want_no_hpaa` linked to force the legacy pageheap path. |
| `temeraire` | Main treatment: matching public `google/tcmalloc` build using the hugepage-aware path. |
| `glibc` | Optional side baseline using the system allocator. |
| `gperftools` | Optional side baseline using open-source gperftools TCMalloc. |

For paper-aligned results, compare `legacy` against `temeraire`. Treat `glibc`
and `gperftools` only as additional context.

## Reproducibility Checklist

Include the following in any report or external result package:

- Git commit of this repository.
- Docker image rebuild date.
- Host OS and Docker version.
- Kernel version reported inside the container.
- CPU model and core count.
- Memory size and NUMA topology if relevant.
- THP `enabled` and `defrag` settings.
- Redis version.
- TCMalloc commit.
- Compiler and build flags, if changed.
- Exact benchmark command lines.
- All non-default environment variables.
- Trial count, request count, client count, and pipeline depth.
- Raw result directory names.
- Method used to aggregate results.
- Known deviations from the OSDI paper.

## Common Pitfalls

- Redis may report `mem_allocator: libc` because it was built with
  `MALLOC=libc`. The active allocator is still changed at runtime through
  `LD_PRELOAD`; use `scripts/check_allocator_preload.sh` to verify the loaded
  shared object.
- THP settings come from the host kernel or Docker VM, not from the image alone.
  Always record them.
- Docker improves setup reproducibility but does not make the benchmark
  hardware-identical to the paper.
- The full benchmark can take a long time. Use the smoke test first.
- Do not compare a smoke-test run against the paper. Smoke-test parameters are
  only for build and workflow validation.

## Suggested Reporting Framing

Use cautious wording such as:

> This artifact reproduces the Redis case-study methodology from the Temeraire
> paper as closely as practical with public source code and commodity hardware.
> It does not reproduce the paper's fleet-scale production evaluation.

That distinction is central to interpreting the results honestly.
