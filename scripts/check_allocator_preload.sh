#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_DIR="${ROOT_DIR}/third_party/src/redis/src"
LEGACY_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_legacy.so"
TEMERAIRE_LIB="${ROOT_DIR}/third_party/install/google-tcmalloc/lib/libtcmalloc_temeraire.so"
PORT="${BENCH_PORT:-6390}"

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

  cleanup() {
    "${REDIS_DIR}/redis-cli" -p "${PORT}" shutdown nosave >/dev/null 2>&1 || kill "${redis_pid}" >/dev/null 2>&1 || true
    wait "${redis_pid}" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  for _ in $(seq 1 30); do
    if "${REDIS_DIR}/redis-cli" -p "${PORT}" ping >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  echo "MODE=${mode}"
  grep libtcmalloc "/proc/${redis_pid}/maps" || true

  cleanup
  trap - EXIT
done
