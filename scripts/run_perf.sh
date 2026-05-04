#!/usr/bin/env bash
set -euo pipefail

ALLOCATOR="${1:-legacy}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_DIR="${ROOT_DIR}/third_party/src/redis/src"
GPERFTOOLS_LIB="${ROOT_DIR}/third_party/install/gperftools/lib/libtcmalloc.so"
GOOGLE_TCMALLOC_TEMERAIRE_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_temeraire.so"
GOOGLE_TCMALLOC_LEGACY_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_legacy.so"
RESULT_BASE="${ROOT_DIR}/results/raw/perf"
PORT="${BENCH_PORT:-6380}"
REQUESTS_PER_TRIAL="${REDIS_REQUESTS_PER_TRIAL:-1000000}"
CLIENTS="${REDIS_CLIENTS:-50}"
PIPELINE="${REDIS_PIPELINE:-16}"
timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_DIR="${RESULT_BASE}/${timestamp}-${ALLOCATOR}"
mkdir -p "${RUN_DIR}"

if [[ ! -x "${REDIS_DIR}/redis-server" ]]; then
  echo "Redis has not been built. Run ./scripts/setup_env.sh first." >&2
  exit 1
fi

server_cmd=( "${REDIS_DIR}/redis-server" "--port" "${PORT}" "--save" "" "--appendonly" "no" )

case "${ALLOCATOR}" in
  glibc)
    ;;
  legacy)
    if [[ ! -f "${GOOGLE_TCMALLOC_LEGACY_LIB}" ]]; then
      echo "Missing ${GOOGLE_TCMALLOC_LEGACY_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    export LD_PRELOAD="${GOOGLE_TCMALLOC_LEGACY_LIB}"
    ;;
  temeraire)
    if [[ ! -f "${GOOGLE_TCMALLOC_TEMERAIRE_LIB}" ]]; then
      echo "Missing ${GOOGLE_TCMALLOC_TEMERAIRE_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    export LD_PRELOAD="${GOOGLE_TCMALLOC_TEMERAIRE_LIB}"
    ;;
  gperftools)
    if [[ ! -f "${GPERFTOOLS_LIB}" ]]; then
      echo "Missing ${GPERFTOOLS_LIB}. Run ./scripts/setup_env.sh first." >&2
      exit 1
    fi
    export LD_PRELOAD="${GPERFTOOLS_LIB}"
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

perf stat \
  -e dTLB-load-misses,dTLB-loads,cycles,instructions,page-faults \
  -p "${redis_pid}" \
  --output "${RUN_DIR}/perf-stat.txt" \
  --append &
perf_pid=$!

"${REDIS_DIR}/redis-cli" -p "${PORT}" flushall >/dev/null
"${REDIS_DIR}/redis-benchmark" \
  -p "${PORT}" \
  -n "${REQUESTS_PER_TRIAL}" \
  -c "${CLIENTS}" \
  -P "${PIPELINE}" \
  lpush benchmark:list v1 v2 v3 v4 v5 > "${RUN_DIR}/lpush.txt"
"${REDIS_DIR}/redis-benchmark" \
  -p "${PORT}" \
  -n "${REQUESTS_PER_TRIAL}" \
  -c "${CLIENTS}" \
  -P "${PIPELINE}" \
  lrange benchmark:list 0 4 > "${RUN_DIR}/lrange.txt"

wait "${perf_pid}" || true

echo "Wrote perf results to ${RUN_DIR}"
