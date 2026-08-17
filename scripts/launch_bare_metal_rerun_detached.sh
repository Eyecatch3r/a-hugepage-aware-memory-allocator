#!/usr/bin/env bash
set -euo pipefail

# Start the bare-metal experiment outside the SSH terminal's session. The
# benchmark inherits the caller's PAPER_*/REDIS_* environment and receives all
# arguments passed to this launcher.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="${TEMERAIRE_DETACHED_RUNNER:-${ROOT_DIR}/scripts/run_bare_metal_redis_experiment.sh}"
SETSID_BIN="${DETACHED_SETSID:-setsid}"
LOG_DIR="${TEMERAIRE_DETACHED_LOG_DIR:-${ROOT_DIR}/results/run-logs}"
LAUNCH_ID="${TEMERAIRE_LAUNCH_ID:-bare-metal-rerun-$(date -u +%Y%m%dT%H%M%SZ)}"

if [[ ! "${LAUNCH_ID}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "TEMERAIRE_LAUNCH_ID may contain only letters, digits, dot, underscore, and hyphen." >&2
  exit 1
fi
if [[ ! -x "${RUNNER}" ]]; then
  echo "Bare-metal runner is not executable: ${RUNNER}" >&2
  exit 1
fi
if ! command -v "${SETSID_BIN}" >/dev/null 2>&1; then
  echo "setsid is required but was not found: ${SETSID_BIN}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${LAUNCH_ID}.log"
PID_FILE="${LOG_DIR}/${LAUNCH_ID}.pid"
STATUS_FILE="${LOG_DIR}/${LAUNCH_ID}.status"

if [[ -f "${PID_FILE}" ]]; then
  prior_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ "${prior_pid}" =~ ^[1-9][0-9]*$ ]] && kill -0 "${prior_pid}" 2>/dev/null; then
    echo "Launch ${LAUNCH_ID} is already running as PID ${prior_pid}." >&2
    exit 1
  fi
fi

umask 077
{
  echo "launch_id=${LAUNCH_ID}"
  echo "started_utc=$(date -u +%FT%TZ)"
  echo "working_directory=${ROOT_DIR}"
  printf "command="
  printf "%q " "${RUNNER}" "$@"
  echo
} > "${LOG_FILE}"

detached_wrapper='set +e
pid_file="$1"
status_file="$2"
log_file="$3"
work_dir="$4"
shift 4
trap "" HUP
cd "${work_dir}" || exit 125
printf "%s\n" "$$" > "${pid_file}"
{
  echo "state=running"
  echo "pid=$$"
  echo "started_utc=$(date -u +%FT%TZ)"
} > "${status_file}"
"$@" </dev/null >> "${log_file}" 2>&1
exit_code=$?
{
  echo "state=finished"
  echo "pid=$$"
  echo "finished_utc=$(date -u +%FT%TZ)"
  echo "exit_code=${exit_code}"
} > "${status_file}.tmp"
mv "${status_file}.tmp" "${status_file}"
exit "${exit_code}"'

# --fork makes setsid create a child even if its own process is already eligible
# to become a session leader. All terminal descriptors are redirected before
# the SSH shell regains control; the detached wrapper also ignores SIGHUP.
"${SETSID_BIN}" --fork bash -c "${detached_wrapper}" temeraire-detached \
  "${PID_FILE}" "${STATUS_FILE}" "${LOG_FILE}" "${ROOT_DIR}" \
  "${RUNNER}" "$@" </dev/null >> "${LOG_FILE}" 2>&1

for _ in {1..20}; do
  [[ -s "${PID_FILE}" ]] && break
  sleep 0.1
done

detached_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [[ ! "${detached_pid}" =~ ^[1-9][0-9]*$ ]] || ! kill -0 "${detached_pid}" 2>/dev/null; then
  echo "The detached rerun did not stay alive. Inspect ${LOG_FILE}." >&2
  [[ -f "${STATUS_FILE}" ]] && cat "${STATUS_FILE}" >&2
  exit 1
fi

echo "Detached rerun started. It is safe to close SSH."
echo "PID:      ${detached_pid}"
echo "PID file: ${PID_FILE}"
echo "Log:      ${LOG_FILE}"
echo "Status:   ${STATUS_FILE}"
echo "Watch:    tail -f '${LOG_FILE}'"
