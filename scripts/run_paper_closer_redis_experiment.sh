#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULT_BASE="${ROOT_DIR}/results/raw/paper-closer"
timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_DIR="${RESULT_BASE}/${timestamp}"
mkdir -p "${RUN_DIR}"

RUN_SETUP_FIRST="${RUN_SETUP_FIRST:-0}"
RUN_RELEASE_OFF="${RUN_RELEASE_OFF:-1}"
RUN_RELEASE_ON="${RUN_RELEASE_ON:-1}"
RUN_PERF="${RUN_PERF:-0}"
PAPER_NUMA_NODE="${PAPER_NUMA_NODE:-}"
PAPER_BACKGROUND_RELEASE_RATE_BPS="${PAPER_BACKGROUND_RELEASE_RATE_BPS:-}"

REDIS_TRIALS="${REDIS_TRIALS:-2000}"
REDIS_REQUESTS_PER_TRIAL="${REDIS_REQUESTS_PER_TRIAL:-1000000}"
REDIS_CLIENTS="${REDIS_CLIENTS:-50}"
REDIS_PIPELINE="${REDIS_PIPELINE:-16}"

if [[ "${RUN_SETUP_FIRST}" == "1" ]]; then
  "${ROOT_DIR}/scripts/setup_env.sh"
fi

SYSTEM_INFO_BEFORE="$(find "${ROOT_DIR}/results/raw/system-info" -maxdepth 1 -type f 2>/dev/null | wc -l)"
"${ROOT_DIR}/scripts/collect_system_info.sh"
SYSTEM_INFO_AFTER="$(find "${ROOT_DIR}/results/raw/system-info" -maxdepth 1 -type f 2>/dev/null | wc -l)"

{
  echo "# Paper-Closer Redis Reproduction"
  echo "timestamp_utc=${timestamp}"
  echo
  echo "## intended match to paper"
  echo "redis_version=6.0.9"
  echo "allocator_comparison=legacy_vs_temeraire"
  echo "trials=${REDIS_TRIALS}"
  echo "requests_per_trial=${REDIS_REQUESTS_PER_TRIAL}"
  echo "clients=${REDIS_CLIENTS}"
  echo "pipeline=${REDIS_PIPELINE}"
  echo "requested_numa_node=${PAPER_NUMA_NODE:-none}"
  echo
  echo "## release modes requested"
  echo "run_release_off=${RUN_RELEASE_OFF}"
  echo "run_release_on=${RUN_RELEASE_ON}"
  echo "background_release_rate_bps_override=${PAPER_BACKGROUND_RELEASE_RATE_BPS:-default}"
  echo
  echo "## known unavoidable deviations"
  echo "single_machine=yes"
  echo "dockerized_environment=yes"
  echo "public_tcmalloc_historical_approximation=yes"
  echo "exact_llvm_commit_cd442157cf=no"
  echo "exact_skylake_hardware=host_dependent"
  echo
  echo "## notes"
  echo "This run aims to get closer to the paper's Redis case study on one machine."
  echo "It cannot reproduce Google's internal execution environment."
  if [[ "${SYSTEM_INFO_AFTER}" -gt "${SYSTEM_INFO_BEFORE}" ]]; then
    latest_system_info="$(find "${ROOT_DIR}/results/raw/system-info" -maxdepth 1 -type f | sort | tail -n 1)"
    echo "system_info_file=${latest_system_info}"
  fi
} > "${RUN_DIR}/manifest.txt"

run_mode() {
  local release_mode="$1"
  local allocator="$2"
  local run_label="paper-${release_mode}"

  export RUN_LABEL="${run_label}"
  export REDIS_NUMA_NODE="${PAPER_NUMA_NODE}"

  case "${release_mode}" in
    release-off)
      export CODEX_TCMALLOC_ENABLE_BACKGROUND_RELEASE=0
      unset CODEX_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS || true
      ;;
    release-on)
      export CODEX_TCMALLOC_ENABLE_BACKGROUND_RELEASE=1
      if [[ -n "${PAPER_BACKGROUND_RELEASE_RATE_BPS}" ]]; then
        export CODEX_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS="${PAPER_BACKGROUND_RELEASE_RATE_BPS}"
      else
        unset CODEX_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS || true
      fi
      ;;
    *)
      echo "Unknown release mode: ${release_mode}" >&2
      exit 1
      ;;
  esac

  "${ROOT_DIR}/scripts/run_redis_benchmark.sh" "${allocator}"

  if [[ "${RUN_PERF}" == "1" ]]; then
    "${ROOT_DIR}/scripts/run_perf.sh" "${allocator}"
  fi
}

if [[ "${RUN_RELEASE_OFF}" == "1" ]]; then
  run_mode release-off legacy
  run_mode release-off temeraire
fi

if [[ "${RUN_RELEASE_ON}" == "1" ]]; then
  run_mode release-on legacy
  run_mode release-on temeraire
fi

echo "Wrote paper-closer run metadata to ${RUN_DIR}"
