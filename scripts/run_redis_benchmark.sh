#!/usr/bin/env bash
set -euo pipefail

ALLOCATOR="${1:-legacy}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_DIR="${ROOT_DIR}/third_party/src/redis/src"
GPERFTOOLS_LIB="${ROOT_DIR}/third_party/install/gperftools/lib/libtcmalloc.so"
GOOGLE_TCMALLOC_TEMERAIRE_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_temeraire.so"
GOOGLE_TCMALLOC_LEGACY_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_legacy.so"
RESULT_BASE="${ROOT_DIR}/results/raw/redis"
PORT="${BENCH_PORT:-6380}"
TRIALS="${REDIS_TRIALS:-2000}"
REQUESTS_PER_TRIAL="${REDIS_REQUESTS_PER_TRIAL:-1000000}"
CLIENTS="${REDIS_CLIENTS:-50}"
PIPELINE="${REDIS_PIPELINE:-16}"
RUN_LABEL="${RUN_LABEL:-}"
NUMA_NODE="${REDIS_NUMA_NODE:-}"
SNAPSHOT_EVERY_TRIALS="${REDIS_SNAPSHOT_EVERY_TRIALS:-250}"
# sequential: one redis-benchmark run of LPUSH, then one of LRANGE. Two commands
#   per request, all pushes before all reads. This is the historical shape.
# combined: one redis-benchmark run of an EVAL script that pushes five elements
#   and reads those five back. One command per request, matching the literal
#   reading of the paper's sentence. Adds Lua interpreter allocations.
WORKLOAD="${REDIS_WORKLOAD:-sequential}"
if [[ "${WORKLOAD}" != "sequential" && "${WORKLOAD}" != "combined" ]]; then
  echo "REDIS_WORKLOAD must be 'sequential' or 'combined', got '${WORKLOAD}'." >&2
  exit 1
fi
PUSH_READ_SCRIPT="redis.call('LPUSH', KEYS[1], 'v1', 'v2', 'v3', 'v4', 'v5'); return redis.call('LRANGE', KEYS[1], 0, 4)"
# A single benchmark invocation can fail transiently on a shared node. Retrying
# the trial is cheaper than discarding a whole block.
MAX_TRIAL_ATTEMPTS="${REDIS_MAX_TRIAL_ATTEMPTS:-4}"
RETRY_SLEEP_SECONDS="${REDIS_RETRY_SLEEP_SECONDS:-5}"
timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
run_suffix=""
if [[ -n "${RUN_LABEL}" ]]; then
  run_suffix="-${RUN_LABEL}"
fi
RUN_DIR="${RESULT_BASE}/${timestamp}-${ALLOCATOR}${run_suffix}"
mkdir -p "${RUN_DIR}"

if [[ ! -x "${REDIS_DIR}/redis-server" ]]; then
  echo "Redis has not been built. Run ./scripts/setup_env.sh first." >&2
  exit 1
fi

command_prefix=()
if [[ -n "${NUMA_NODE}" ]]; then
  command_prefix=( numactl "--cpunodebind=${NUMA_NODE}" "--membind=${NUMA_NODE}" )
fi

server_cmd=( "${command_prefix[@]}" "${REDIS_DIR}/redis-server" "--port" "${PORT}" "--save" "" "--appendonly" "no" )

allocator_mode_note=""
PRELOAD_LIB=""
case "${ALLOCATOR}" in
  glibc)
    allocator_mode_note="system glibc allocator"
    ;;
  legacy)
    if [[ ! -f "${GOOGLE_TCMALLOC_LEGACY_LIB}" ]]; then
      echo "Missing ${GOOGLE_TCMALLOC_LEGACY_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    PRELOAD_LIB="${GOOGLE_TCMALLOC_LEGACY_LIB}"
    allocator_mode_note="paper-era public google/tcmalloc build with want_no_hpaa linked to force the legacy pageheap path"
    ;;
  temeraire)
    if [[ ! -f "${GOOGLE_TCMALLOC_TEMERAIRE_LIB}" ]]; then
      echo "Missing ${GOOGLE_TCMALLOC_TEMERAIRE_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    PRELOAD_LIB="${GOOGLE_TCMALLOC_TEMERAIRE_LIB}"
    allocator_mode_note="paper-era public google/tcmalloc build using the hugepage-aware Temeraire path"
    ;;
  gperftools)
    if [[ ! -f "${GPERFTOOLS_LIB}" ]]; then
      echo "Missing ${GPERFTOOLS_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    PRELOAD_LIB="${GPERFTOOLS_LIB}"
    allocator_mode_note="open-source gperftools tcmalloc side baseline"
    ;;
  *)
    echo "Usage: $0 [glibc|legacy|temeraire|gperftools]" >&2
    exit 1
    ;;
esac

if [[ -n "${PRELOAD_LIB}" ]]; then
  server_cmd=( env "LD_PRELOAD=${PRELOAD_LIB}" "${server_cmd[@]}" )
fi

"${server_cmd[@]}" > "${RUN_DIR}/redis-server.log" 2>&1 &
redis_pid=$!

cleanup() {
  if kill -0 "${redis_pid}" 2>/dev/null; then
    "${REDIS_DIR}/redis-cli" -p "${PORT}" shutdown nosave >/dev/null 2>&1 || kill "${redis_pid}" >/dev/null 2>&1 || true
    wait "${redis_pid}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# CPU time consumed by the Redis server process. The paper normalizes Redis
# throughput for CPU and reports requests per second per core, so a rerun needs
# the CPU seconds behind each block. Redis restarts for every allocator block,
# which makes the INFO counters a per-block total rather than a running one. The
# /proc fields are recorded as a cross-check that does not depend on Redis.
write_cpu_fields() {
  local clk_tck
  clk_tck="$(getconf CLK_TCK 2>/dev/null || echo 100)"
  echo "clk_tck=${clk_tck}"
  awk -v tck="${clk_tck}" '{
    print "proc_utime_ticks=" $14
    print "proc_stime_ticks=" $15
    print "proc_cutime_ticks=" $16
    print "proc_cstime_ticks=" $17
    printf "proc_cpu_seconds=%.6f\n", ($14 + $15) / tck
  }' "/proc/${redis_pid}/stat" 2>/dev/null || true
  # INFO emits "field:value". Rewrite to "field=value" so the same key/value
  # parser that reads the rest of this file can read these fields too.
  "${REDIS_DIR}/redis-cli" -p "${PORT}" info cpu 2>/dev/null \
    | tr -d '\r' \
    | awk -F':' '/^used_cpu_/ {print $1 "=" $2}' || true
  echo "nproc_online=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"
}

write_thp_state() {
  local outfile="$1"
  {
    echo "timestamp_utc=$(date -u +"%Y%m%dT%H%M%SZ")"
    echo
    echo "## meminfo"
    grep -E "AnonHugePages|ShmemHugePages|FileHugePages|HugePages_Total|HugePages_Free|HugePages_Rsvd|HugePages_Surp" /proc/meminfo || true
    echo
    echo "## transparent_hugepage"
    cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
    cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
    cat /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none 2>/dev/null || true
  } > "${outfile}"
}

capture_memory_snapshot() {
  local outfile="$1"
  local include_redis_info="${2:-0}"

  {
    echo "allocator=${ALLOCATOR}"
    echo "allocator_note=${allocator_mode_note}"
    echo "run_label=${RUN_LABEL:-none}"
    echo "timestamp_utc=$(date -u +"%Y%m%dT%H%M%SZ")"
    echo "pid=${redis_pid}"
    echo "port=${PORT}"
    echo "trials=${TRIALS}"
    echo "requests_per_trial=${REQUESTS_PER_TRIAL}"
    echo "clients=${CLIENTS}"
    echo "pipeline=${PIPELINE}"
    echo "numa_node=${NUMA_NODE:-none}"
    echo "snapshot_every_trials=${SNAPSHOT_EVERY_TRIALS}"
    echo "workload=${WORKLOAD}"
    echo "trial_retries=${TRIAL_RETRIES:-0}"
    echo "background_release_enabled=${TEMERAIRE_TCMALLOC_ENABLE_BACKGROUND_RELEASE:-0}"
    echo "background_release_rate_bps=${TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS:-unset}"
    echo "build_exact_llvm=${BUILD_EXACT_LLVM:-0}"
    echo "llvm_ref_requested=${LLVM_REF:-unknown}"
    echo
    echo "## status"
    grep -E "VmRSS|VmHWM|VmSize|RssAnon|RssFile" "/proc/${redis_pid}/status" || true
    echo
    echo "## cpu"
    write_cpu_fields
    if [[ "${include_redis_info}" == "1" ]]; then
      echo
      echo "## info memory"
      "${REDIS_DIR}/redis-cli" -p "${PORT}" info memory || true
      echo
      echo "## tcmalloc stats"
      "${REDIS_DIR}/redis-cli" -p "${PORT}" memory malloc-stats 2>/dev/null || true
    fi
    echo
    echo "## smaps_rollup"
    cat "/proc/${redis_pid}/smaps_rollup" || true
    echo
    echo "## thp_state"
    grep -E "AnonHugePages|ShmemHugePages|FileHugePages|HugePages_Total|HugePages_Free|HugePages_Rsvd|HugePages_Surp" /proc/meminfo || true
    cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
    cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
    cat /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none 2>/dev/null || true
  } > "${outfile}"
}

for _ in {1..30}; do
  if "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
  echo "Redis did not start successfully." >&2
  cat "${RUN_DIR}/redis-server.log" >&2 || true
  exit 1
fi

if [[ "${TEMERAIRE_TCMALLOC_ENABLE_BACKGROUND_RELEASE:-0}" == "1" ]]; then
  expected_release_line="temeraire-wrapper: background release enabled rate_bps=${TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS}"
  if ! grep -Fqx "${expected_release_line}" "${RUN_DIR}/redis-server.log"; then
    echo "Redis started, but the wrapper did not confirm the requested background release rate." >&2
    echo "Expected: ${expected_release_line}" >&2
    cat "${RUN_DIR}/redis-server.log" >&2 || true
    exit 1
  fi
fi

write_thp_state "${RUN_DIR}/thp-before.txt"
{
  echo "allocator=${ALLOCATOR}"
  echo "allocator_note=${allocator_mode_note}"
  echo "run_label=${RUN_LABEL:-none}"
  echo "timestamp_utc=${timestamp}"
  echo "pid=${redis_pid}"
  echo "port=${PORT}"
  echo "trials=${TRIALS}"
  echo "requests_per_trial=${REQUESTS_PER_TRIAL}"
  echo "clients=${CLIENTS}"
  echo "pipeline=${PIPELINE}"
  echo "numa_node=${NUMA_NODE:-none}"
  echo "snapshot_every_trials=${SNAPSHOT_EVERY_TRIALS}"
  echo "workload=${WORKLOAD}"
  echo "background_release_enabled=${TEMERAIRE_TCMALLOC_ENABLE_BACKGROUND_RELEASE:-0}"
  echo "background_release_rate_bps=${TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS:-unset}"
  echo "build_exact_llvm=${BUILD_EXACT_LLVM:-0}"
  echo "llvm_ref_requested=${LLVM_REF:-unknown}"
  echo
  echo "## benchmark_methodology"
  if [[ "${WORKLOAD}" == "combined" ]]; then
    echo "Each trial runs one redis-benchmark invocation of an EVAL script."
    echo "One request pushes five elements and reads those five back:"
    echo "${PUSH_READ_SCRIPT}"
    echo "Requests per trial therefore equals commands per trial."
    echo "Lua interpreter allocations are part of this measurement."
  else
    echo "Each trial runs two redis-benchmark invocations:"
    echo "1. LPUSH benchmark:list v1 v2 v3 v4 v5"
    echo "2. LRANGE benchmark:list 0 4"
    echo "Commands per trial is twice requests_per_trial, and every push"
    echo "precedes every read."
  fi
  echo
  echo "## status"
  grep -E "VmRSS|VmHWM|VmSize|RssAnon|RssFile" "/proc/${redis_pid}/status" || true
  echo
  echo "## cpu"
  write_cpu_fields
  echo
  echo "## smaps_rollup"
  cat "/proc/${redis_pid}/smaps_rollup" || true
  echo
  echo "## thp_state"
  grep -E "AnonHugePages|ShmemHugePages|FileHugePages|HugePages_Total|HugePages_Free|HugePages_Rsvd|HugePages_Surp" /proc/meminfo || true
  cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
  cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
  cat /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none 2>/dev/null || true
} > "${RUN_DIR}/memory-before.txt"

RESULTS_CSV="${RUN_DIR}/trials.csv"
echo "trial,operation,requests,rps" > "${RESULTS_CSV}"

# Pull the requests-per-second value out of redis-benchmark --csv output.
#
# The first CSV field is the test name, which redis-benchmark builds from the
# command line. For an EVAL run that name contains the Lua script, and the
# script contains commas, so splitting the line on commas does not locate the
# rate. Drop the quoted test name first, then take the field that follows it.
extract_rps() {
  awk '
    /^"/ {
      line = $0
      sub(/^"[^"]*",/, "", line)
      gsub(/"/, "", line)
      split(line, fields, ",")
      if (fields[1] != "") print fields[1]
    }
  ' | tail -n 1
}

TRIAL_RETRIES=0
RETRY_LOG="${RUN_DIR}/retries.log"

# Run one redis-benchmark invocation, retrying a transient failure.
#
# A single failed invocation used to abort the whole block through set -e,
# discarding an hour of completed trials. Trials are independent samples, so
# repeating one is sound, but a silent retry would hide a systematic fault.
# Every attempt is therefore logged and the count lands in the block metadata,
# and the run still stops if the server is gone or the retries run out.
run_benchmark_attempt() {
  local label="$1"
  shift
  local attempt=1 output="" rate=""
  while true; do
    if output="$("${command_prefix[@]}" "${REDIS_DIR}/redis-benchmark" "$@" 2>&1)"; then
      rate="$(printf '%s\n' "${output}" | extract_rps)"
      if [[ -n "${rate}" ]]; then
        BENCH_OUTPUT="${output}"
        BENCH_RPS="${rate}"
        return 0
      fi
    fi
    echo "$(date -u +%FT%TZ) ${label} attempt ${attempt} failed" >> "${RETRY_LOG}"
    printf '%s\n' "${output}" | tail -3 >> "${RETRY_LOG}"
    if ! "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
      echo "$(date -u +%FT%TZ) ${label} server is not responding, giving up" >> "${RETRY_LOG}"
      echo "Redis stopped responding during ${label}. See ${RETRY_LOG}." >&2
      return 1
    fi
    if (( attempt >= MAX_TRIAL_ATTEMPTS )); then
      echo "$(date -u +%FT%TZ) ${label} exhausted ${MAX_TRIAL_ATTEMPTS} attempts" >> "${RETRY_LOG}"
      echo "${label} failed ${MAX_TRIAL_ATTEMPTS} times. See ${RETRY_LOG}." >&2
      return 1
    fi
    TRIAL_RETRIES=$((TRIAL_RETRIES + 1))
    attempt=$((attempt + 1))
    sleep "${RETRY_SLEEP_SECONDS}"
  done
}

for trial in $(seq 1 "${TRIALS}"); do
  trial_id="$(printf '%04d' "${trial}")"
  if ! "${REDIS_DIR}/redis-cli" -p "${PORT}" flushall >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) trial ${trial_id} flushall failed" >> "${RETRY_LOG}"
    echo "flushall failed before trial ${trial_id}." >&2
    exit 1
  fi

  if [[ "${WORKLOAD}" == "combined" ]]; then
    run_benchmark_attempt "trial ${trial_id} pushread" \
      -p "${PORT}" \
      -n "${REQUESTS_PER_TRIAL}" \
      -c "${CLIENTS}" \
      -P "${PIPELINE}" \
      --csv \
      eval "${PUSH_READ_SCRIPT}" 1 benchmark:list
    printf "%s\n" "${BENCH_OUTPUT}" > "${RUN_DIR}/trial-${trial_id}-pushread.csv"
    echo "${trial},pushread5,${REQUESTS_PER_TRIAL},${BENCH_RPS}" >> "${RESULTS_CSV}"
  else
    run_benchmark_attempt "trial ${trial_id} lpush" \
      -p "${PORT}" \
      -n "${REQUESTS_PER_TRIAL}" \
      -c "${CLIENTS}" \
      -P "${PIPELINE}" \
      --csv \
      lpush benchmark:list v1 v2 v3 v4 v5
    printf "%s\n" "${BENCH_OUTPUT}" > "${RUN_DIR}/trial-${trial_id}-lpush.csv"
    echo "${trial},lpush5,${REQUESTS_PER_TRIAL},${BENCH_RPS}" >> "${RESULTS_CSV}"

    run_benchmark_attempt "trial ${trial_id} lrange" \
      -p "${PORT}" \
      -n "${REQUESTS_PER_TRIAL}" \
      -c "${CLIENTS}" \
      -P "${PIPELINE}" \
      --csv \
      lrange benchmark:list 0 4
    printf "%s\n" "${BENCH_OUTPUT}" > "${RUN_DIR}/trial-${trial_id}-lrange.csv"
    echo "${trial},lrange5,${REQUESTS_PER_TRIAL},${BENCH_RPS}" >> "${RESULTS_CSV}"
  fi

  if [[ "${SNAPSHOT_EVERY_TRIALS}" =~ ^[0-9]+$ ]] && [[ "${SNAPSHOT_EVERY_TRIALS}" -gt 0 ]] && (( trial % SNAPSHOT_EVERY_TRIALS == 0 )); then
    capture_memory_snapshot "${RUN_DIR}/memory-sample-$(printf '%04d' "${trial}").txt" 0
  fi
done

awk -F',' '
  NR == 1 {next}
  {
    sum[$2] += $4
    count[$2] += 1
  }
  END {
    print "operation,mean_rps,trials"
    for (op in sum) {
      printf "%s,%.6f,%d\n", op, sum[op] / count[op], count[op]
    }
  }
' "${RESULTS_CSV}" > "${RUN_DIR}/summary.csv"

capture_memory_snapshot "${RUN_DIR}/memory-after.txt" 1
write_thp_state "${RUN_DIR}/thp-after.txt"

echo "Wrote benchmark results to ${RUN_DIR}"
