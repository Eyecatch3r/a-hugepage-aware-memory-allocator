#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_DIR="${ROOT_DIR}/third_party/src/redis/src"
LEGACY_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_legacy.so"
TEMERAIRE_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_temeraire.so"
PORT="${BENCH_PORT:-6390}"

require_redis_artifact() {
  local path="$1"
  local label="$2"
  if [[ ! -e "${path}" ]]; then
    echo "Missing ${label}: ${path}. Run ./scripts/setup_env.sh first." >&2
    exit 1
  fi
}

require_redis_artifact "${REDIS_DIR}/redis-server" "redis-server"
require_redis_artifact "${REDIS_DIR}/redis-cli" "redis-cli"
require_redis_artifact "${LEGACY_LIB}" "legacy allocator library"
require_redis_artifact "${TEMERAIRE_LIB}" "temeraire allocator library"

for mode in legacy temeraire; do
  case "${mode}" in
    legacy)
      export LD_PRELOAD="${LEGACY_LIB}"
      ;;
    temeraire)
      export LD_PRELOAD="${TEMERAIRE_LIB}"
      ;;
  esac

  "${REDIS_DIR}/redis-server" --port "${PORT}" --save "" --appendonly no >/tmp/"redis-${mode}".log 2>&1 &
  redis_pid=$!
  started=0

  cleanup() {
    "${REDIS_DIR}/redis-cli" -p "${PORT}" shutdown nosave >/dev/null 2>&1 || kill "${redis_pid}" >/dev/null 2>&1 || true
    wait "${redis_pid}" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  for _ in $(seq 1 30); do
    if "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
      started=1
      break
    fi
    if ! kill -0 "${redis_pid}" 2>/dev/null; then
      break
    fi
    sleep 1
  done

  if [[ "${started}" != "1" ]]; then
    echo "MODE=${mode}"
    echo "Redis failed to start for preload verification." >&2
    cat /tmp/"redis-${mode}".log >&2 || true
    exit 1
  fi

  echo "MODE=${mode}"
  grep libtcmalloc "/proc/${redis_pid}/maps" || true

  cleanup
  trap - EXIT
done
