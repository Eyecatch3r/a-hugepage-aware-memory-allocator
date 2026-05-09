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
case "${ALLOCATOR}" in
  glibc)
    allocator_mode_note="system glibc allocator"
    ;;
  legacy)
    if [[ ! -f "${GOOGLE_TCMALLOC_LEGACY_LIB}" ]]; then
      echo "Missing ${GOOGLE_TCMALLOC_LEGACY_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    export LD_PRELOAD="${GOOGLE_TCMALLOC_LEGACY_LIB}"
    allocator_mode_note="paper-era public google/tcmalloc build with want_no_hpaa linked to force the legacy pageheap path"
    ;;
  temeraire)
    if [[ ! -f "${GOOGLE_TCMALLOC_TEMERAIRE_LIB}" ]]; then
      echo "Missing ${GOOGLE_TCMALLOC_TEMERAIRE_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    export LD_PRELOAD="${GOOGLE_TCMALLOC_TEMERAIRE_LIB}"
    allocator_mode_note="paper-era public google/tcmalloc build using the hugepage-aware Temeraire path"
    ;;
  gperftools)
    if [[ ! -f "${GPERFTOOLS_LIB}" ]]; then
      echo "Missing ${GPERFTOOLS_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    export LD_PRELOAD="${GPERFTOOLS_LIB}"
    allocator_mode_note="open-source gperftools tcmalloc side baseline"
    ;;
  *)
    echo "Usage: $0 [glibc|legacy|temeraire|gperftools]" >&2
    exit 1
    ;;
esac

"${server_cmd[@]}" > "${RUN_DIR}/redis-server.log" 2>&1 &
redis_pid=$!

cleanup() {
  if kill -0 "${redis_pid}" 2>/dev/null; then
    "${REDIS_DIR}/redis-cli" -p "${PORT}" shutdown nosave >/dev/null 2>&1 || kill "${redis_pid}" >/dev/null 2>&1 || true
    wait "${redis_pid}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for _ in {1..30}; do
  if "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
  echo "Redis did not start successfully." >&2
  exit 1
fi

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
  echo "background_release_enabled=${CODEX_TCMALLOC_ENABLE_BACKGROUND_RELEASE:-0}"
  echo "background_release_rate_bps=${CODEX_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS:-default}"
  echo
  echo "## benchmark_methodology"
  echo "Each trial runs two redis-benchmark invocations:"
  echo "1. LPUSH benchmark:list v1 v2 v3 v4 v5"
  echo "2. LRANGE benchmark:list 0 4"
  echo
  echo "## status"
  grep -E "VmRSS|VmHWM|VmSize|RssAnon|RssFile" "/proc/${redis_pid}/status" || true
  echo
  echo "## smaps_rollup"
  cat "/proc/${redis_pid}/smaps_rollup" || true
} > "${RUN_DIR}/memory-before.txt"

RESULTS_CSV="${RUN_DIR}/trials.csv"
echo "trial,operation,requests,rps" > "${RESULTS_CSV}"

extract_rps() {
  awk -F',' 'NF >= 2 {gsub(/"/, "", $2); print $2}' | tail -n 1
}

for trial in $(seq 1 "${TRIALS}"); do
  "${REDIS_DIR}/redis-cli" -p "${PORT}" flushall >/dev/null

  lpush_output="$("${command_prefix[@]}" "${REDIS_DIR}/redis-benchmark" \
    -p "${PORT}" \
    -n "${REQUESTS_PER_TRIAL}" \
    -c "${CLIENTS}" \
    -P "${PIPELINE}" \
    --csv \
    lpush benchmark:list v1 v2 v3 v4 v5)"
  lpush_rps="$(printf '%s\n' "${lpush_output}" | extract_rps)"
  printf "%s\n" "${lpush_output}" > "${RUN_DIR}/trial-$(printf '%04d' "${trial}")-lpush.csv"
  echo "${trial},lpush5,${REQUESTS_PER_TRIAL},${lpush_rps}" >> "${RESULTS_CSV}"

  lrange_output="$("${command_prefix[@]}" "${REDIS_DIR}/redis-benchmark" \
    -p "${PORT}" \
    -n "${REQUESTS_PER_TRIAL}" \
    -c "${CLIENTS}" \
    -P "${PIPELINE}" \
    --csv \
    lrange benchmark:list 0 4)"
  lrange_rps="$(printf '%s\n' "${lrange_output}" | extract_rps)"
  printf "%s\n" "${lrange_output}" > "${RUN_DIR}/trial-$(printf '%04d' "${trial}")-lrange.csv"
  echo "${trial},lrange5,${REQUESTS_PER_TRIAL},${lrange_rps}" >> "${RESULTS_CSV}"
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

{
  echo "allocator=${ALLOCATOR}"
  echo "allocator_note=${allocator_mode_note}"
  echo "timestamp_utc=${timestamp}"
  echo "pid=${redis_pid}"
  echo "port=${PORT}"
  echo
  echo "## status"
  grep -E "VmRSS|VmHWM|VmSize|RssAnon|RssFile" "/proc/${redis_pid}/status" || true
  echo
  echo "## info memory"
  "${REDIS_DIR}/redis-cli" -p "${PORT}" info memory || true
  echo
  echo "## tcmalloc stats"
  "${REDIS_DIR}/redis-cli" -p "${PORT}" memory malloc-stats 2>/dev/null || true
  echo
  echo "## smaps_rollup"
  cat "/proc/${redis_pid}/smaps_rollup" || true
} > "${RUN_DIR}/memory-after.txt"

echo "Wrote benchmark results to ${RUN_DIR}"
