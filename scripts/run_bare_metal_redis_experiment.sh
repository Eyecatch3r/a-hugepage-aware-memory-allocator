#!/usr/bin/env bash
set -euo pipefail

# Keep decimal output compatible with the CSV format used by the Docker runs.
export LC_ALL=C

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULT_BASE="${ROOT_DIR}/results/raw/paper-closer"
timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
RUN_DIR="${RESULT_BASE}/${timestamp}"

RUN_SETUP_FIRST="${RUN_SETUP_FIRST:-0}"
RUN_RELEASE_OFF="${RUN_RELEASE_OFF:-1}"
RUN_RELEASE_ON="${RUN_RELEASE_ON:-1}"
RUN_PERF="${RUN_PERF:-0}"
PAPER_ALLOCATOR_ORDER="${PAPER_ALLOCATOR_ORDER:-legacy-first}"
PAPER_BALANCED_RUN_NUMBER="${PAPER_BALANCED_RUN_NUMBER:-}"
PAPER_NUMA_NODE="${PAPER_NUMA_NODE:-}"
PAPER_BACKGROUND_RELEASE_RATE_BPS="${PAPER_BACKGROUND_RELEASE_RATE_BPS:-}"
PAPER_SNAPSHOT_EVERY_TRIALS="${PAPER_SNAPSHOT_EVERY_TRIALS:-250}"

REDIS_TRIALS="${REDIS_TRIALS:-2000}"
REDIS_REQUESTS_PER_TRIAL="${REDIS_REQUESTS_PER_TRIAL:-1000000}"
REDIS_CLIENTS="${REDIS_CLIENTS:-50}"
REDIS_PIPELINE="${REDIS_PIPELINE:-16}"

if [[ "${RUN_RELEASE_ON}" == "1" ]]; then
  if [[ ! "${PAPER_BACKGROUND_RELEASE_RATE_BPS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Release-on requires a positive PAPER_BACKGROUND_RELEASE_RATE_BPS." >&2
    echo "The pinned TCMalloc default is 0 B/s, which does not release pageheap bytes." >&2
    exit 1
  fi
fi

usage() {
  cat <<'EOF'
Usage: ./scripts/run_bare_metal_redis_experiment.sh [options]

Options:
  --allocator-order ORDER  Allocator order for each release mode:
                           legacy-first (default), temeraire-first, balanced
  --balanced-run-number N  Balanced run number override. Odd runs start with
                           legacy, even runs start with Temeraire.
  -h, --help               Show this help text

The balanced order alternates the first allocator across paper-close runs that
use it. Use it for repeated full runs so each release-mode comparison is not
always measured legacy-first.
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --allocator-order)
      if [[ "$#" -lt 2 ]]; then
        echo "Missing value for --allocator-order." >&2
        usage >&2
        exit 1
      fi
      PAPER_ALLOCATOR_ORDER="$2"
      shift 2
      ;;
    --balanced-run-number)
      if [[ "$#" -lt 2 ]]; then
        echo "Missing value for --balanced-run-number." >&2
        usage >&2
        exit 1
      fi
      PAPER_BALANCED_RUN_NUMBER="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

resolve_balanced_allocator_order() {
  local balanced_run_number="${PAPER_BALANCED_RUN_NUMBER}"
  local manifest
  local prior_balanced_runs=0

  if [[ -z "${balanced_run_number}" ]]; then
    if [[ -d "${RESULT_BASE}" ]]; then
      while IFS= read -r manifest; do
        if grep -qx "allocator_order=balanced" "${manifest}"; then
          prior_balanced_runs=$((prior_balanced_runs + 1))
        fi
      done < <(find "${RESULT_BASE}" -mindepth 2 -maxdepth 2 -name manifest.txt 2>/dev/null)
    fi
    balanced_run_number=$((prior_balanced_runs + 1))
  fi

  if [[ ! "${balanced_run_number}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Balanced run number must be a positive integer: ${balanced_run_number}" >&2
    exit 1
  fi

  PAPER_BALANCED_RUN_NUMBER="${balanced_run_number}"
  if (( balanced_run_number % 2 == 1 )); then
    BALANCED_ALLOCATOR_ORDER="legacy-first"
  else
    BALANCED_ALLOCATOR_ORDER="temeraire-first"
  fi
}

BALANCED_ALLOCATOR_ORDER=""
if [[ "${PAPER_ALLOCATOR_ORDER}" == "balanced" ]]; then
  resolve_balanced_allocator_order
fi

allocator_order_for_release() {
  case "${PAPER_ALLOCATOR_ORDER}" in
    legacy-first|temeraire-first)
      echo "${PAPER_ALLOCATOR_ORDER}"
      ;;
    balanced)
      echo "${BALANCED_ALLOCATOR_ORDER}"
      ;;
    *)
      echo "Unknown allocator order: ${PAPER_ALLOCATOR_ORDER}" >&2
      echo "Expected legacy-first, temeraire-first, or balanced." >&2
      exit 1
      ;;
  esac
}

RELEASE_OFF_ALLOCATOR_ORDER="$(allocator_order_for_release release-off)"
RELEASE_ON_ALLOCATOR_ORDER="$(allocator_order_for_release release-on)"
mkdir -p "${RUN_DIR}"

if [[ "${RUN_SETUP_FIRST}" == "1" ]]; then
  "${ROOT_DIR}/scripts/setup_bare_metal_env.sh"
fi

SYSTEM_INFO_BEFORE="$(find "${ROOT_DIR}/results/raw/system-info" -maxdepth 1 -type f 2>/dev/null | wc -l)"
"${ROOT_DIR}/scripts/collect_bare_metal_system_info.sh"
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
  echo "snapshot_every_trials=${PAPER_SNAPSHOT_EVERY_TRIALS}"
  echo
  echo "## release modes requested"
  echo "run_release_off=${RUN_RELEASE_OFF}"
  echo "run_release_on=${RUN_RELEASE_ON}"
  echo "allocator_order=${PAPER_ALLOCATOR_ORDER}"
  echo "balanced_run_number=${PAPER_BALANCED_RUN_NUMBER:-none}"
  echo "release_off_allocator_order=${RELEASE_OFF_ALLOCATOR_ORDER}"
  echo "release_on_allocator_order=${RELEASE_ON_ALLOCATOR_ORDER}"
  echo "background_release_rate_bps_override=${PAPER_BACKGROUND_RELEASE_RATE_BPS:-unset}"
  echo
  echo "## known unavoidable deviations"
  echo "single_machine=yes"
  if [[ -f /.dockerenv ]]; then
    echo "execution_environment=docker"
    echo "dockerized_environment=yes"
  else
    echo "execution_environment=native"
    echo "dockerized_environment=no"
  fi
  virtualization="$(systemd-detect-virt 2>/dev/null || true)"
  echo "virtualization=${virtualization:-unknown}"
  echo "public_tcmalloc_historical_approximation=yes"
  if [[ "${BUILD_EXACT_LLVM:-0}" == "1" ]]; then
    echo "exact_llvm_commit_cd442157cf=yes"
  else
    echo "exact_llvm_commit_cd442157cf=no"
  fi
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
  export REDIS_SNAPSHOT_EVERY_TRIALS="${PAPER_SNAPSHOT_EVERY_TRIALS}"

  case "${release_mode}" in
    release-off)
      export TEMERAIRE_TCMALLOC_ENABLE_BACKGROUND_RELEASE=0
      unset TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS || true
      ;;
    release-on)
      export TEMERAIRE_TCMALLOC_ENABLE_BACKGROUND_RELEASE=1
      if [[ -n "${PAPER_BACKGROUND_RELEASE_RATE_BPS}" ]]; then
        export TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS="${PAPER_BACKGROUND_RELEASE_RATE_BPS}"
      else
        unset TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS || true
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

run_allocator_pair() {
  local release_mode="$1"
  local allocator_order="$2"

  case "${allocator_order}" in
    legacy-first)
      run_mode "${release_mode}" legacy
      run_mode "${release_mode}" temeraire
      ;;
    temeraire-first)
      run_mode "${release_mode}" temeraire
      run_mode "${release_mode}" legacy
      ;;
    *)
      echo "Unknown allocator order: ${allocator_order}" >&2
      exit 1
      ;;
  esac
}

if [[ "${RUN_RELEASE_OFF}" == "1" ]]; then
  run_allocator_pair release-off "${RELEASE_OFF_ALLOCATOR_ORDER}"
fi

if [[ "${RUN_RELEASE_ON}" == "1" ]]; then
  run_allocator_pair release-on "${RELEASE_ON_ALLOCATOR_ORDER}"
fi

echo "Wrote paper-closer run metadata to ${RUN_DIR}"
