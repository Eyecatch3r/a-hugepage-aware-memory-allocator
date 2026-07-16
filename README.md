# Temeraire Redis Reproduction Artifact

Reproduction artifact for the Redis case study from:

> A.H. Hunter et al. "Beyond malloc efficiency to fleet efficiency: a hugepage-aware memory allocator." *OSDI 2021.*

This artifact compares historical public TCMalloc with the legacy pageheap against the same codebase with the hugepage-aware Temeraire backend, using repeated Redis list-operation benchmarks. It does **not** reproduce Google's fleet-scale production experiment, which depends on internal infrastructure, production telemetry, and workload diversity unavailable externally.

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
│   ├── collect_bare_metal_system_info.sh
│   ├── run_perf.sh
│   ├── run_redis_benchmark.sh
│   ├── run_paper_closer_redis_experiment.sh
│   ├── run_bare_metal_redis_experiment.sh
│   ├── run_bare_metal_release_on_sensitivity.sh
│   ├── setup_env.sh
│   └── setup_bare_metal_env.sh
├── docker-compose.yml
└── README.md
```

Third-party sources and build outputs are placed under `third_party/` after setup. Raw benchmark output is written to `results/raw/`.

The unsuffixed setup, metadata, and paper-close scripts belong to the original
Docker/WSL workflow. Files containing `bare_metal` are the Debian scripts used
for the node85 run. They are separate so that the cluster adaptations do not
silently change the earlier workflow.

## Docker/WSL Prerequisites

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
| llvm/clang | `cd442157cff4aad209ae532cbf031abbe10bc1df` (when `BUILD_EXACT_LLVM=1`) |

The pinned `google/tcmalloc` commit is required: it exposes the `want_no_hpaa` hook needed to build the legacy pageheap variant alongside the Temeraire-capable variant.
By default, the Docker configuration now also attempts to build a pinned LLVM/Clang toolchain so Redis and the TCMalloc wrapper can be compiled closer to the paper's stated setup.

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

### Docker/WSL Reproduction Path

This is the primary workflow for the seminar reproduction. It is the closest
supported path in this repository to the Redis case study described in the
paper, and it should be the default command sequence for the main reproduction.

```bash
docker compose build
docker compose run --rm temeraire-dev bash -lc "./scripts/setup_env.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/check_allocator_preload.sh"
docker compose run --rm temeraire-dev bash -lc "echo always > /sys/kernel/mm/transparent_hugepage/enabled && echo always > /sys/kernel/mm/transparent_hugepage/defrag && cat /sys/kernel/mm/transparent_hugepage/enabled && cat /sys/kernel/mm/transparent_hugepage/defrag"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_paper_closer_redis_experiment.sh --allocator-order balanced"
```

Balanced order alternates between legacy-first and Temeraire-first across
paper-close runs that use it. The selected run number and effective per-release
mode order are written to the paper-close manifest with the rest of the run
metadata. If an interrupted balanced run should not affect the next order, pass
`--balanced-run-number N` to select the desired odd or even balanced run number.

Why this is the central path:

- it uses the paper-shaped Redis benchmark defaults
- it records richer system metadata than the older direct benchmark flow
- it can run both release-off and release-on configurations
- it now captures THP state and periodic `smaps_rollup` snapshots during the long run

The preload check must show `libtcmalloc_legacy.so` for `legacy` and
`libtcmalloc_temeraire.so` for `temeraire` in `/proc/<pid>/maps`.

Important caveats:

- THP comes from the shared Linux kernel used by Docker/WSL2, not from the image.
- The `echo always > /sys/...` step changes kernel policy for the Docker/WSL Linux environment, not just one container.
- If you skip that step, the run may remain in `madvise` mode and may fail to exercise the hugepage mechanism the paper studies.

Useful overrides:

- `RUN_RELEASE_OFF=1` and `RUN_RELEASE_ON=1` control whether to run the two release modes.
- `--allocator-order legacy-first|temeraire-first|balanced` controls which allocator is measured first within each release-mode pair. `PAPER_ALLOCATOR_ORDER` can set the same value through the environment.
- `--balanced-run-number N` overrides the balanced run number: odd numbers are legacy-first and even numbers are Temeraire-first. `PAPER_BALANCED_RUN_NUMBER` can set the same value through the environment.
- `PAPER_NUMA_NODE=0` pins Redis and `redis-benchmark` to one NUMA node when supported.
- `RUN_PERF=1` adds `perf stat` captures for each allocator mode.
- `PAPER_BACKGROUND_RELEASE_RATE_BPS=<bytes_per_sec>` overrides the allocator background release rate for the release-on runs.
- `BUILD_EXACT_LLVM=1` builds a pinned LLVM/Clang toolchain from source. `LLVM_REF` and `LLVM_REPO_URL` can be overridden if the paper-era commit needs adjustment.

For a targeted release-on-only diagnostic rerun, for example after an anomalous
release-on result:

```bash
docker compose run --rm -e RUN_RELEASE_OFF=0 -e RUN_RELEASE_ON=1 temeraire-dev bash -lc "./scripts/run_paper_closer_redis_experiment.sh --allocator-order temeraire-first"
```

### Bare-Metal Debian Path

The second workflow runs directly on Debian 13. It was added for the node85
rerun after the Docker/WSL release-on measurements showed host drift. Use a
directory on the node's local filesystem, such as `/var/tmp`, rather than the
shared home directory. Bazel's output tree contains many small files and was
markedly slower on the shared filesystem.

`run_bare_metal_redis_experiment.sh` sets `LC_ALL=C`. Without that setting,
node85's German numeric locale writes decimal commas into `summary.csv`, which
breaks its three-column CSV layout.

The compute node could not fetch the pinned repositories from GitHub. The
source archives, Bazel 4.2.2, and Bazel dependency archives were therefore
downloaded elsewhere and copied to the node. Each staged source directory has a
`.temeraire-source-ref` file. `setup_bare_metal_env.sh` checks that marker before
building when `TEMERAIRE_OFFLINE_SOURCES=1`.

Run the setup from the local work directory:

```bash
cd /var/tmp/temeraire-costa-20260716

BUILD_EXACT_LLVM=1 \
TEMERAIRE_OFFLINE_SOURCES=1 \
LLVM_BOOTSTRAP_CXXFLAGS="-include cstdint" \
BAZEL_DISTDIR="$PWD/third_party/distdir" \
./scripts/setup_bare_metal_env.sh

./scripts/check_allocator_preload.sh
./scripts/collect_bare_metal_system_info.sh
```

The `-include cstdint` flag is a build workaround for the paper-era LLVM commit
on Debian 13's newer host compiler. It is applied while bootstrapping LLVM; the
pinned LLVM source revision remains `cd442157cff4aad209ae532cbf031abbe10bc1df`.

Before a long run, use reduced trial and request counts to check the allocator,
release mode, NUMA binding, THP state, and output path:

```bash
REDIS_TRIALS=2 \
REDIS_REQUESTS_PER_TRIAL=1000 \
PAPER_NUMA_NODE=0 \
PAPER_BACKGROUND_RELEASE_RATE_BPS=16777216 \
./scripts/run_bare_metal_redis_experiment.sh --allocator-order balanced
```

Remove the two reduced benchmark variables for the full 2000-trial run. The
paper does not state a public background-release rate, so
`PAPER_BACKGROUND_RELEASE_RATE_BPS` is a recorded local parameter rather than a
paper-derived constant. A release-on sensitivity series can be run separately:

```bash
RELEASE_RATES_MIB="16 64 256" \
RELEASE_SENSITIVITY_REPEATS=4 \
./scripts/run_bare_metal_release_on_sensitivity.sh
```

### Trial Test Run

Use this only as a smoke test before the main reproduction path.

```bash
docker compose build
docker compose run --rm temeraire-dev bash -lc "./scripts/setup_env.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/check_allocator_preload.sh"
docker compose run --rm -e REDIS_TRIALS=2 -e REDIS_REQUESTS_PER_TRIAL=1000 temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh legacy"
docker compose run --rm -e REDIS_TRIALS=2 -e REDIS_REQUESTS_PER_TRIAL=1000 temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh temeraire"
```

Do not compare smoke-test results against the paper; reduced parameters are for build validation only.

### Secondary: Direct Legacy-vs-Temeraire Run

This is the older direct benchmark path. It remains useful for debugging,
sanity-checking allocator selection, or collecting supplementary local data, but
it is no longer the preferred headline workflow for the report.

```bash
docker compose run --rm temeraire-dev bash -lc "./scripts/collect_system_info.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh legacy"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_redis_benchmark.sh temeraire"
```

This still does **not** reproduce the paper's original execution environment.
The exact LLVM commit, THP behavior, and hardware platform remain
host-dependent deviations. The public TCMalloc revision is an approximation
chosen to preserve the legacy-vs-Temeraire comparison.

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

`collect_system_info.sh` writes Docker and host-kernel metadata to
`results/raw/system-info/`. `collect_bare_metal_system_info.sh` writes the same
class of record for a native Linux run and identifies the execution environment
and detected virtualization. The Docker record separates the container
distribution from the kernel context: the image user space is
`debian:bookworm-slim`, while the active kernel, THP state, cgroup behavior, and
hardware-visible topology come from Docker/WSL2 or the Linux host.

The latest recorded system snapshot in this artifact is
`results/raw/system-info/20260524T094418Z.txt`. Since the Docker image base has
not changed, it documents the Linux environment used for the reported runs:

| Field | Recorded value |
|---|---|
| Container user space | Debian GNU/Linux 12 (bookworm) |
| Container base image | `debian:bookworm-slim` |
| Shared kernel | `6.6.114.1-microsoft-standard-WSL2` |
| Kernel build string | `#1 SMP PREEMPT_DYNAMIC Mon Dec 1 20:46:23 UTC 2025` |
| Architecture | `x86_64 GNU/Linux` |
| THP enabled policy | `[always] madvise never` |
| THP defrag policy | `[always] defer defer+madvise madvise never` |
| `khugepaged/max_ptes_none` | `511` |

The node85 setup was inspected before the full bare-metal benchmark was launched:

| Field | Recorded value |
|---|---|
| Operating system | Debian GNU/Linux 13 (trixie) |
| Kernel | `6.12.95+deb13-amd64` |
| Processor | Intel Xeon Gold 5318N |
| CPU topology | 24 cores, 48 threads, one NUMA node |
| Memory | 188 GiB |
| Virtualization | none detected |
| THP enabled policy | `[always] madvise never` |
| THP defrag policy | always defer defer+madvise `[madvise]` never |

Raw outputs must not be modified. Derived tables, plots, and summaries go in `results/processed/` or `plots/generated/`.

## Known Deviations from the Paper

Expected deviations include host CPU, kernel version, Transparent Huge Page (THP) settings, Docker behavior, compiler version, and the fact that public TCMalloc source is only an approximation of the internal paper artifact. Results should be interpreted accordingly.

## Current Result Interpretation

The current result set should be read as a local reproduction attempt, not as an
exact reproduction of the Redis rows in Table 1 of the paper.

Regenerate the table below from the checked-in raw summaries with:

```bash
python3 scripts/aggregate_paper_closer_results.py
```

The script combines LPUSH and LRANGE means with the harmonic mean
`2 / (1 / LPUSH + 1 / LRANGE)`, then reports the Temeraire-over-legacy
percentage delta for each matched run pair.

| Run family | Release off | Release on | Interpretation |
|---|---:|---:|---|
| THP fixed-order | +1.88% | +0.26% | first valid THP-enabled run |
| Balanced 1 | +0.43% | +0.12% | legacy first |
| Balanced 2 | +0.48% | +0.38% | Temeraire first |
| Balanced 3 | -0.76% | -2.34% | negative run |
| Balanced 4 | +3.46% | -12.02% | release-on outlier |
| Targeted release-on | n/a | +0.12% | rerun of the anomaly |

The strongest local signal is release-off: it is positive in most THP-enabled
runs and its median is close to the paper's small Redis improvement. Release-on
is smaller and noisier. The severe `-12.02%` release-on result did not reproduce
in the targeted rerun, so it should be treated as a transient outlier unless it
appears again.

The defensible claim is that the artifact reproduces the Redis methodology and
often lands in the paper's small-effect regime under active THP. It does not
robustly reproduce the exact Redis Table 1 statistics.

## Reproducibility Checklist

The following must be included to accurately reproduce the artifact:

- Git commit of this repository and Docker image rebuild date
- Host OS, Docker version, container OS release, and shared kernel version
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
