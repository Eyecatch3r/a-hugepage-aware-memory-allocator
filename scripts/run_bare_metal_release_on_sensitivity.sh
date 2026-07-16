#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RATES_MIB="${RELEASE_RATES_MIB:-16 64 256}"
REPEATS="${RELEASE_SENSITIVITY_REPEATS:-4}"
RATES_MIB="${RATES_MIB//,/ }"

if [[ ! "${REPEATS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "RELEASE_SENSITIVITY_REPEATS must be a positive integer." >&2
  exit 1
fi

for rate_mib in ${RATES_MIB}; do
  if [[ ! "${rate_mib}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Every RELEASE_RATES_MIB value must be a positive integer: ${rate_mib}" >&2
    exit 1
  fi

  rate_bps=$((rate_mib * 1024 * 1024))
  for repeat in $(seq 1 "${REPEATS}"); do
    if (( repeat % 2 == 1 )); then
      allocator_order="legacy-first"
    else
      allocator_order="temeraire-first"
    fi

    echo "release-on sensitivity: rate=${rate_mib} MiB/s repeat=${repeat}/${REPEATS} order=${allocator_order}"
    RUN_RELEASE_OFF=0 \
      RUN_RELEASE_ON=1 \
      PAPER_BACKGROUND_RELEASE_RATE_BPS="${rate_bps}" \
      "${ROOT_DIR}/scripts/run_bare_metal_redis_experiment.sh" \
        --allocator-order "${allocator_order}"
  done
done
