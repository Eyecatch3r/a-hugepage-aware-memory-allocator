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
│   ├── redis-temeraire-reproduction-protocol.tex
│   ├── temeraire-main-presentation.tex
│   └── temeraire-seminar-report.tex
├── plots/
│   └── generated/
├── results/
│   ├── node85-import/                  # bare-metal main run, 4 balanced pairs
│   ├── node85-sensitivity-audit/       # bare-metal main run plus 2 extra rates
│   ├── processed/
│   └── raw/                            # Docker/WSL runs
├── scripts/
│   ├── aggregate_paper_closer_results.py
│   ├── audit_bare_metal_results.py
│   ├── build_results_site.py
│   ├── check_allocator_preload.sh
│   ├── collect_system_info.sh
│   ├── collect_bare_metal_system_info.sh
│   ├── run_perf.sh
│   ├── run_redis_benchmark.sh
│   ├── run_paper_closer_redis_experiment.sh
│   ├── run_bare_metal_redis_experiment.sh
│   ├── run_bare_metal_release_on_sensitivity.sh
│   ├── run_release_on_sensitivity.sh
│   ├── setup_env.sh
│   └── setup_bare_metal_env.sh
├── site/                               # static results explorer
├── tests/
├── docker-compose.yml
└── README.md
```

The setup script writes third-party sources and build outputs to
`third_party/`. The benchmark scripts write raw output to `results/raw/`.

The unsuffixed setup, metadata, and paper-close scripts belong to the original
Docker/WSL workflow. Files that contain `bare_metal` are the Debian scripts for
the node85 run. They are separate, because the cluster changes must not change
the earlier workflow.

The Python scripts need Python 3.10 or later. They use only the standard library.
The `tests/` directory holds the unit tests. Run them with:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

### Result data and archives

Git holds the manifests, summaries, Redis logs, memory samples, and system
metadata for each run. Git does not hold the per-trial CSV files, because the
node85 runs write more than 190,000 of them. Get the per-trial files from the
result archives:

| Archive | Contents |
|---|---|
| `temeraire-node85-results.tar.gz` | Main run: 4 balanced pairs at 16 MiB/s |
| `temeraire-node85-results-with-sensitivity.tar.gz` | Main run plus 4 pairs at 64 MiB/s and 4 pairs at 256 MiB/s |

Both archives are large. Publish them with a release. Do not add them to Git.
Each `.sha256` file records the path on the node where the archive was made. Do
not call `sha256sum -c` on these files. Use `audit_bare_metal_results.py`
instead. It reads the hash value and ignores the recorded path.

Two early two-trial smoke runs in the sensitivity archive have a broken
`summary.csv` in each of their eight blocks. The German numeric locale wrote
decimal commas into those files before `run_bare_metal_redis_experiment.sh` set
`LC_ALL=C`. The two runs have the manifest timestamps `20260716T152333Z` and
`20260716T154227Z`. The audit script rejects any block that does not have 2000
trials, so these blocks stay out of all results.

## Interactive Results Explorer

The static site in `site/` shows the historical matched runs, trial
distributions, process-memory snapshots, and background-release-rate
sensitivity results. Use the Test environment control to select the native
node85 results or the WSL/Docker results. The site has no runtime dependencies
or network requests.

Open `site/index.html` directly, or serve it locally:

```bash
python3 -m http.server 8000 --directory site
```

Then visit `http://localhost:8000/`.

Rebuild the checked-in data bundle after you add or change raw results:

```bash
python3 scripts/build_results_site.py
```

The site calculates headline throughput with the same harmonic-mean rule as
`aggregate_paper_closer_results.py`. Each distribution summary uses 128 evenly
spaced trial observations per run. This keeps the site build from opening all
400,000 per-operation files. Summary means, pairwise deltas, and memory series
come from the complete run summaries and snapshots.

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

Two workflows exist. Select the correct one before you start:

- Use the **Bare-Metal Debian Path** to repeat the measurements in the report.
  The report uses only the native node85 results.
- Use the **Docker/WSL Reproduction Path** to build and diagnose the experiment
  on a workstation. This workflow found the build, preload, THP, and
  release-rate faults. Its own measurements show host drift that is larger than
  the effect under test. Do not use its numbers as final evidence.

### Docker/WSL Reproduction Path

This workflow is the development and diagnostic stage. It has the paper-shaped
Redis workload and the full metadata records, but it runs in a container above a
shared kernel.

```bash
docker compose build
docker compose run --rm temeraire-dev bash -lc "./scripts/setup_env.sh"
docker compose run --rm temeraire-dev bash -lc "./scripts/check_allocator_preload.sh"
docker compose run --rm temeraire-dev bash -lc "echo always > /sys/kernel/mm/transparent_hugepage/enabled && echo always > /sys/kernel/mm/transparent_hugepage/defrag && cat /sys/kernel/mm/transparent_hugepage/enabled && cat /sys/kernel/mm/transparent_hugepage/defrag"
docker compose run --rm temeraire-dev bash -lc "./scripts/run_paper_closer_redis_experiment.sh --allocator-order balanced"
```

Balanced order alternates between legacy-first and Temeraire-first across the
paper-close runs that use it. The script writes the selected run number and the
effective order for each release mode into the paper-close manifest, with the
other run metadata. To keep an interrupted balanced run from changing the order
of the next run, pass `--balanced-run-number N`. Odd numbers are legacy-first.
Even numbers are Temeraire-first.

This workflow gives more than the older direct benchmark flow:

- It uses the paper-shaped Redis benchmark defaults.
- It records more system metadata.
- It can run both release modes.
- It captures the THP state and periodic `smaps_rollup` snapshots during the
  long run.

The preload check must show `libtcmalloc_legacy.so` for `legacy` and
`libtcmalloc_temeraire.so` for `temeraire` in `/proc/<pid>/maps`.

Important caveats:

- THP comes from the shared Linux kernel of Docker/WSL2. It does not come from
  the image.
- The `echo always > /sys/...` step changes the kernel policy for the whole
  Docker/WSL Linux environment. It does not change one container only.
- If you skip that step, the run can stay in `madvise` mode. Redis then gets no
  hugepage backing, and the run measures a different thing than the paper.

Useful overrides:

- `RUN_RELEASE_OFF=1` and `RUN_RELEASE_ON=1` select the release modes to run.
- `--allocator-order legacy-first|temeraire-first|balanced` sets which allocator runs first in each release-mode pair. `PAPER_ALLOCATOR_ORDER` sets the same value through the environment.
- `--balanced-run-number N` sets the balanced run number. Odd numbers are legacy-first. Even numbers are Temeraire-first. `PAPER_BALANCED_RUN_NUMBER` sets the same value through the environment.
- `PAPER_NUMA_NODE=0` pins Redis and `redis-benchmark` to one NUMA node, if the node gives support.
- `RUN_PERF=1` adds a `perf stat` capture for each allocator mode.
- `PAPER_BACKGROUND_RELEASE_RATE_BPS=<bytes_per_sec>` sets the allocator background release rate for the release-on runs.
- `BUILD_EXACT_LLVM=1` builds the pinned LLVM/Clang toolchain from source. Change `LLVM_REF` and `LLVM_REPO_URL` if the paper-era commit needs a different source.

To run a release-on diagnostic again, for example after an unexpected release-on
result:

```bash
docker compose run --rm -e RUN_RELEASE_OFF=0 -e RUN_RELEASE_ON=1 temeraire-dev bash -lc "./scripts/run_paper_closer_redis_experiment.sh --allocator-order temeraire-first"
```

### Bare-Metal Debian Path

This workflow runs directly on Debian 13. It produces the measurements in the
report. It was added for the node85 run, after the Docker/WSL release-on
measurements showed host drift.

Use a directory on the local filesystem of the node, such as `/var/tmp`. Do not
use the shared home directory. The Bazel output tree has many small files, and
the shared filesystem is much slower for them.

`run_bare_metal_redis_experiment.sh` sets `LC_ALL=C`. Without that setting, the
German numeric locale on node85 writes decimal commas into `summary.csv` and
breaks its three-column layout.

The compute node cannot fetch the pinned repositories from GitHub. Download the
source archives, Bazel 4.2.2, and the Bazel dependency archives on a different
machine, then copy them to the node. Each staged source directory must have a
`.temeraire-source-ref` file. If you set `TEMERAIRE_OFFLINE_SOURCES=1`,
`setup_bare_metal_env.sh` checks that file before it builds.

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
paper-derived constant.

#### Workload selection

`PAPER_WORKLOAD` selects which reading of the paper's trial sentence to run. The
runner records the value in the manifest and in `memory-before.txt` for each
block.

| Value | Trial structure |
|---|---|
| `sequential` (default) | One LPUSH run, then one LRANGE run. Two commands per request. All pushes run before all reads. |
| `combined` | One run of an `EVAL` script that pushes five elements and reads those five back. One command per request. |

Run both values to measure the effect of the choice. Neither value is correct on
its own. The `sequential` value doubles the command count and separates the
phases. The `combined` value adds Lua interpreter allocations to a measurement of
allocation.

```bash
PAPER_WORKLOAD=combined \
PAPER_NUMA_NODE=0 \
PAPER_BACKGROUND_RELEASE_RATE_BPS=16777216 \
./scripts/run_bare_metal_redis_experiment.sh --allocator-order balanced
```

#### CPU time

Each block records the CPU time of the Redis server in the `## cpu` section of
`memory-before.txt`, every memory sample, and `memory-after.txt`. Redis restarts
for each block, so `used_cpu_user` plus `used_cpu_sys` in `memory-after.txt` is
the CPU total for that block. The audit script divides the request count by that
total to report throughput in the paper's unit of requests per second per core.

The audit reports the normalized result only when every block in the comparison
recorded CPU time. Blocks from earlier runs have no `## cpu` section, so the
audit reports their unnormalized deltas and states that the normalized summary is
not available.

A release-on sensitivity series can be run separately:

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

The paper states five properties of its Redis experiment. This artifact matches
three of them and does not match two:

| Property the paper states | This artifact |
|---|---|
| Redis 6.0.9 | matched |
| Legacy TCMalloc pageheap as the baseline | matched, through `want_no_hpaa` |
| LLVM commit `cd442157cf` with `-O3` | matched, built from source |
| Intel Skylake Xeon processors | **not matched.** Node85 has a Xeon Gold 5318N, an Ice Lake part |
| Throughput normalized for CPU | **not matched.** This artifact reports the unnormalized rate |

The paper does not state the Linux distribution, the kernel version, the THP
policy, or the background release rate for Redis. Record those values locally,
because you cannot match them.

Further deviations apply to the Docker/WSL runs: the container adds its own
behavior, and the shared kernel supplies the THP policy. The public TCMalloc
source stays a historical approximation of the internal paper artifact in both
workflows.

## Current Result Interpretation

Read this result set as a local reproduction attempt. It is not an exact
reproduction of the Redis rows in Table 1 of the paper.

The paper gives two Redis values: `+0.75%` with periodic release off, and
`+0.44%` with periodic release on.

Three differences apply to every comparison with those two values. Read them
before you read a matching percentage as a reproduction.

1. **The metric is a different quantity.** The caption of Table 1 in the paper
   states that its throughput is "normalized for CPU", and the text gives the
   unit as requests per second per core. This artifact reports the unnormalized
   request rate from `redis-benchmark`, and it joins two operation rates with a
   harmonic mean that the paper does not specify. A ratio of unnormalized rates
   cannot separate a real throughput gain from the same throughput at a higher
   CPU cost. No local number can confirm or contradict a normalized figure.
2. **The workload is one reading of an underspecified sentence.** The paper
   specifies 2000 trials, each trial making one million requests "to push 5
   elements and read those 5 elements". Read literally, one request covers the
   push and the read. Each local trial instead runs 1,000,000 LPUSH commands and
   then 1,000,000 LRANGE commands, which is two million commands. All pushes run
   before all reads, so `LRANGE 0 4` returns the same final five elements on
   every read while the list holds five million. An interleaved implementation
   keeps the live set small and touches memory that was just written. That is a
   different access pattern for the allocator under test.
3. **The processor generation differs.** The paper ran Redis on Intel Skylake
   Xeon processors. Node85 has a Xeon Gold 5318N, which is an Ice Lake part. TLB
   capacity and hugepage handling changed between the two generations, so the
   hardware that matters most to a TLB-pressure result is not matched.

### Reported result: the bare-metal node85 run

The report uses these results only. Each value is one matched pair of a legacy
block and a Temeraire block, at 2000 trials for each block. A positive value
means that Temeraire was faster.

| Condition | Pairs | Mean | Median | 95% interval | Paper |
|---|---:|---:|---:|---:|---:|
| Release off | 4 | -0.28% | -0.34% | [-1.04%, +0.48%] | +0.75% |
| Release on, 16 MiB/s | 4 | +0.51% | +0.55% | [-0.69%, +1.73%] | +0.44% |
| Release on, 64 MiB/s | 4 | -0.49% | -0.60% | [-0.92%, -0.07%] | not stated |
| Release on, 256 MiB/s | 4 | -0.29% | -0.08% | [-1.54%, +0.96%] | not stated |

The interval is a Student-t interval on the log-transformed pair ratios, with the
four complete pairs as the replication unit. It belongs to the mean, not to the
median beside it. For these four conditions the two centers differ by less than
0.01 percentage points.

Read these four rows together:

- Release-off does not reproduce the paper. Its 95% interval excludes `+0.75%`.
- Release-on at 16 MiB/s is close to the paper. Its interval also covers zero.
  This is agreement in one configuration. It is not a confirmed effect.
- The paper does not state a release rate. The 16 MiB/s value is a local
  parameter. Do not read it as the value that the paper used.
- At 64 MiB/s all four pairs favor legacy TCMalloc, and the interval excludes
  zero. This is the only condition here whose unadjusted interval does so. Four
  conditions were tested, and no correction for multiple comparisons was applied.
  Record this result, but do not call it an established effect.

The defensible claim is narrow. The public reconstruction lands in the small
positive Redis range of the paper in one recorded release configuration. It does
not land there across release modes and rates. The three differences listed above
also apply, so this is proximity between two differently built numbers.

### Historical result: the Docker/WSL runs

These results are superseded. Keep them as the record of the development stage.
Do not compare them with the paper. Regenerate the table with:

```bash
python3 scripts/aggregate_paper_closer_results.py --dataset historical
```

| Run family | Release off | Release on | Note |
|---|---:|---:|---|
| THP fixed-order | +1.88% | +0.26% | first run with THP set to `always` |
| Balanced 1 | +0.43% | +0.12% | legacy first |
| Balanced 2 | +0.48% | +0.38% | Temeraire first |
| Balanced 3 | -0.76% | -2.34% | negative run |
| Balanced 4 | +3.46% | -12.02% | see the two warnings below |
| Targeted release-on | n/a | +0.12% | run of balanced 4 again |

Two faults limit this table:

1. **The release-on column does not measure periodic release.** The runner
   started the TCMalloc background-actions thread but did not set the background
   release rate. The pinned TCMalloc revision sets that rate to zero. The thread
   continued its per-CPU-cache work and calculated zero bytes for each pageheap
   release call. No run in this column performed periodic pageheap release. The
   `-12.02%` value is part of this group.
2. **Host drift is larger than the effect.** Absolute throughput moved by more
   than 200 kRPS across one series and then recovered. Each allocator block ran
   for approximately one hour. The speed of the host during that hour therefore
   sets the result of the block.

The corrected Docker/WSL release-on sweep sets a positive rate and confirms it in
the Redis log. Its results still show the drift. Regenerate that matrix with:

```bash
python3 scripts/aggregate_paper_closer_results.py --dataset sensitivity
```

| Rate | Pair 1 | Pair 2 | Pair 3 | Pair 4 | Median |
|---|---:|---:|---:|---:|---:|
| 16 MiB/s | +1.91% | -0.16% | -0.27% | -1.37% | -0.21% |
| 64 MiB/s | -6.88% | +2.10% | +11.49% | +12.21% | +6.80% |
| 256 MiB/s | +15.73% | -0.09% | +0.50% | -0.77% | +0.20% |

Compare the 16 MiB/s row with the node85 row above. The same rate gives `-0.21%`
here and `+0.55%` on the native node. The environment sets the sign.

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

## Bare-Metal Result Audit

`audit_bare_metal_results.py` re-derives every reported bare-metal value from the
raw trial files. It needs no third-party Python packages. It has two modes.

Every value in the result table above comes from these two commands. Run both to
reproduce the table.

### Balanced mode: release off and release on at 16 MiB/s

```bash
python3 scripts/audit_bare_metal_results.py \
  --mode balanced \
  --raw-dir results/node85-import/raw \
  --archive temeraire-node85-results.tar.gz \
  --checksum temeraire-node85-results.tar.gz.sha256 \
  --output-dir results/processed/node85-audit
```

The script finds the four balanced 2000-trial manifests. Each manifest owns four
allocator blocks: legacy and Temeraire, with release off and release on.

### Sensitivity mode: release on at each higher rate

```bash
python3 scripts/audit_bare_metal_results.py \
  --mode sensitivity \
  --raw-dir results/node85-sensitivity-audit/raw \
  --archive temeraire-node85-results-with-sensitivity.tar.gz \
  --checksum temeraire-node85-results-with-sensitivity.tar.gz.sha256 \
  --output-dir results/processed/node85-sensitivity-audit
```

The script finds each 2000-trial manifest that requests release-on only and sets
a positive release rate. Each manifest owns two allocator blocks. The script
groups the pairs by rate and reports one summary for each rate. It reads the rate
from the manifest, so it audits 64 MiB/s and 256 MiB/s together. It skips the
balanced manifests, because those request both release modes.

### What the script checks

In both modes the script verifies the archive checksum, then checks these items
for each block:

- Trial identifiers, request counts, and per-trial throughput values
- The count of raw per-trial CSV files
- The count of memory samples against the snapshot interval
- The block mean, recomputed from the raw trial files
- The THP state before the run
- The release-rate confirmation line in the Redis log
- A clean-shutdown marker in the Redis log
- The allocator order against the order in the manifest
- The system metadata: Redis version, TCMalloc commit, LLVM commit, execution
  environment, and virtualization

The `--expected-trials 2000` default rejects the smoke tests and the 10-trial
pilots. Any missing or inconsistent item stops the script with a non-zero exit
code.

The throughput of each block is `2 / (1 / LPUSH + 1 / LRANGE)`. The script
calculates the percentage change of Temeraire against the matched legacy block.
It treats each complete run pair as one replication. It does not treat the 2000
sequential trials in a block as 2000 replications, because they are
autocorrelated.

### Output files

| File | Contents |
|---|---|
| `audit.json` | All checks, block metrics, and warnings, in machine-readable form |
| `pair-summary.csv` | Balanced mode: one row per balanced run, plus mean and median |
| `rate-summary.csv` | Sensitivity mode: one row per pair, plus mean and median for each rate |
| `audit.md` | Short report for a human reader |
