# TEMERAIRE Seminar Reproduction

This repository is a reproducible starting point for a seminar project on:

> Beyond malloc efficiency to fleet efficiency: a hugepage-aware memory allocator

The seminar goal is to reproduce the paper's experiments as closely as practical on accessible hardware. This repository therefore focuses on the paper's Redis case study methodology and on comparing TCMalloc's legacy pageheap against the hugepage-aware Temeraire backend as faithfully as the public open-source code allows.

It does **not** claim to reproduce Google's fleet-scale evaluation, 1% production experiment, or warehouse-scale rollout. Those parts of the paper depend on internal infrastructure, workload diversity, telemetry, and hardware that are not available in this environment.

## Scope

This scaffold is designed for three practical outcomes:

1. Recreate the Redis allocator comparison from the paper as closely as possible with public code.
2. Record system, allocator, kernel, THP, and benchmark metadata for reproducibility.
3. Leave a clean place to add microbenchmarks, plots, notes, and report artifacts.

The intended experiment target is:

- Redis `6.0.9`
- a paper-era public `google/tcmalloc` ref that still exposes both Temeraire and the `want_no_hpaa` legacy opt-out hook
- repeated `redis-benchmark` trials shaped after the paper's Redis evaluation

The main unavoidable limitations are:

- no Google-internal fleet telemetry or A/B framework
- no warehouse-scale colocated workload mix
- no identical Skylake production servers unless your host happens to match them
- no guarantee that the public ref is byte-for-byte identical to Google's exact internal paper artifact

## Repository Layout

```text
.
├── docker/
│   └── Dockerfile
├── notes/
│   └── .gitkeep
├── plots/
│   └── generated/
│       └── .gitkeep
├── results/
│   ├── processed/
│   │   └── .gitkeep
│   └── raw/
│       └── .gitkeep
├── scripts/
│   ├── collect_system_info.sh
│   ├── run_perf.sh
│   ├── run_redis_benchmark.sh
│   └── setup_env.sh
└── docker-compose.yml
```

## Prerequisites

- Docker Desktop with Linux containers enabled
- Docker Compose

The container is intentionally configured with elevated privileges because `perf` and some memory-management observations are hard or impossible to do meaningfully in an unprivileged container.

## Quick Start

This is the practical container workflow. It sets up the experiment environment and runs the public-code approximation of the Redis case study.

Build the container image:

```bash
docker compose build
```

Enter the experiment container:

```bash
docker compose run --rm temeraire-dev
```

Inside the container, build Redis and the allocator dependencies:

```bash
./scripts/setup_env.sh
```

Collect environment metadata:

```bash
./scripts/collect_system_info.sh
```

Collect environment metadata before benchmarking:

```bash
./scripts/collect_system_info.sh
```

Run the Redis comparison workflow:

```bash
./scripts/run_redis_benchmark.sh legacy
./scripts/run_redis_benchmark.sh temeraire
```

In this repository:

- `legacy` means a paper-era public `google/tcmalloc` build with `want_no_hpaa` linked, forcing the legacy pageheap path
- `temeraire` means the matching public `google/tcmalloc` build using the hugepage-aware pageheap
- `gperftools` remains available only as an optional side baseline

Optionally collect `perf stat` metrics:

```bash
./scripts/run_perf.sh legacy
./scripts/run_perf.sh temeraire
```

## Important Notes

### 1. This is a Linux experiment environment

The project folder can live on Windows, but the actual experiment should run inside the Linux container. That is where Redis, `perf`, `/sys/kernel/mm/transparent_hugepage`, and allocator preload behavior are closest to the paper's assumptions.

### 2. THP settings come from the host kernel

Containers share the host kernel. That means:

- Transparent Huge Pages are not fully controlled by the image itself.
- The values in `/sys/kernel/mm/transparent_hugepage/*` reflect the Docker Linux VM or host kernel backing Docker Desktop.
- These settings must be recorded in the report, because they can materially affect results.

### 3. Docker is good for reproducibility, not for perfect fidelity

This setup is a defensible small-scale reproduction platform, but it is not equivalent to:

- Google's warehouse-scale machines
- fleet-wide workload mixes
- Google's production telemetry and experiment framework
- the paper's 1% fleet rollout

### 4. This repository targets the Redis case study, not the fleet experiment

The paper contains several layers of evaluation:

- application case studies
- a Redis experiment
- a 1% fleet experiment
- a full rollout analysis

This repository can realistically target only the Redis-style public-code experiment. Any report using this repository should explicitly say that it is a **small-scale reproduction of the Redis case study methodology**, not a reproduction of the fleet-scale results.

### 5. The allocator comparison must be framed correctly

The paper's relevant allocator comparison is not:

- `glibc` versus open-source `gperftools`

It is closer to:

- TCMalloc with the legacy pageheap
- TCMalloc with the hugepage-aware Temeraire backend

Because the public `google/tcmalloc` repository evolved after the paper, this repository pins a historical public ref that still exposes the legacy opt-out hook. Any remaining gaps versus the paper should still be stated plainly in the seminar write-up.

## Suggested Next Steps

1. Pin the allocator source to a documented public ref and record that exact ref in the report.
2. Run repeated Redis trials for both `legacy` and `temeraire`.
3. Save all raw outputs in `results/raw/`.
4. Add summary scripts that compute mean, spread, and deltas across repeated trials.
5. Optionally add a side experiment with `glibc` for context, but keep it clearly separate from the paper-faithful comparison.

## Reporting Guidance

Document at minimum:

- host OS and Docker version
- container image base
- kernel version seen inside the container
- THP status
- CPU model
- Redis version
- `google/tcmalloc` commit or release
- compiler and build flags
- benchmark trial count
- benchmark commands
- all run counts and averaging method
- every known deviation from the paper

That documentation will matter as much as the benchmark numbers themselves.
