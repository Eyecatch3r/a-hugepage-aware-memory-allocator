#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/results/raw/system-info"
mkdir -p "${OUT_DIR}"

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
outfile="${OUT_DIR}/${timestamp}.txt"

{
  echo "# System Information"
  echo "timestamp_utc=${timestamp}"
  echo
  echo "## uname"
  uname -a
  echo
  echo "## operating system"
  cat /etc/os-release
  echo
  echo "## execution environment"
  echo "debian_version=$(cat /etc/debian_version 2>/dev/null || true)"
  if [[ -f /.dockerenv ]]; then
    echo "execution_environment=docker"
    echo "dockerenv_present=yes"
    echo "container_base_image=${TEMERAIRE_CONTAINER_BASE_IMAGE:-unknown}"
  else
    echo "execution_environment=native"
    echo "dockerenv_present=no"
  fi
  virtualization="$(systemd-detect-virt 2>/dev/null || true)"
  echo "virtualization=${virtualization:-unknown}"
  echo
  echo "## kernel and container context"
  echo "kernel_release=$(uname -r)"
  echo "kernel_version=$(uname -v)"
  echo "machine=$(uname -m)"
  echo "hostname=$(hostname 2>/dev/null || true)"
  echo "pid1_comm=$(cat /proc/1/comm 2>/dev/null || true)"
  echo "cgroup_summary"
  cat /proc/self/cgroup 2>/dev/null || true
  echo
  echo "## cpuinfo model"
  grep -m1 "model name" /proc/cpuinfo || true
  echo
  echo "## lscpu"
  lscpu 2>/dev/null || true
  echo
  echo "## numa"
  numactl --hardware 2>/dev/null || true
  echo
  echo "## meminfo summary"
  grep -E "MemTotal|MemFree|MemAvailable|HugePages|AnonHugePages" /proc/meminfo || true
  echo
  echo "## transparent hugepages"
  cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
  cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
  cat /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none 2>/dev/null || true
  echo
  echo "## compilers"
  clang --version 2>/dev/null || true
  echo
  gcc --version 2>/dev/null || true
  echo
  echo "## exact llvm toolchain"
  llvm_clang="${ROOT_DIR}/third_party/build/llvm-project/bin/clang"
  if [[ -x "${llvm_clang}" ]]; then
    "${llvm_clang}" --version 2>/dev/null || true
    echo
    echo "llvm_repo_url=${LLVM_REPO_URL:-unknown}"
    echo "llvm_ref_requested=${LLVM_REF:-unknown}"
    if [[ -d "${ROOT_DIR}/third_party/src/llvm-project/.git" ]]; then
      git -C "${ROOT_DIR}/third_party/src/llvm-project" rev-parse HEAD
    elif [[ -f "${ROOT_DIR}/third_party/src/llvm-project/.temeraire-source-ref" ]]; then
      echo "llvm_ref_recorded=$(cat "${ROOT_DIR}/third_party/src/llvm-project/.temeraire-source-ref")"
    fi
  else
    echo "exact llvm toolchain not built"
  fi
  echo
  echo "## redis version"
  "${ROOT_DIR}/third_party/src/redis/src/redis-server" --version 2>/dev/null || echo "redis not built yet"
  echo
  echo "## gperftools version hint"
  if [[ -f "${ROOT_DIR}/third_party/src/gperftools/ChangeLog" ]]; then
    head -n 5 "${ROOT_DIR}/third_party/src/gperftools/ChangeLog"
  else
    echo "gperftools not built yet"
  fi
  echo
  echo "## google tcmalloc commit"
  if [[ -d "${ROOT_DIR}/third_party/src/google-tcmalloc/.git" ]]; then
    git -C "${ROOT_DIR}/third_party/src/google-tcmalloc" rev-parse HEAD
  elif [[ -f "${ROOT_DIR}/third_party/src/google-tcmalloc/.temeraire-source-ref" ]]; then
    cat "${ROOT_DIR}/third_party/src/google-tcmalloc/.temeraire-source-ref"
  else
    echo "google tcmalloc not built yet"
  fi
  echo
  echo "## allocator artifacts"
  ls -l "${ROOT_DIR}/third_party/install/gperftools/lib" 2>/dev/null || true
  ls -l "${ROOT_DIR}/third_party/install/google-tcmalloc/lib" 2>/dev/null || true
} > "${outfile}"

echo "Wrote ${outfile}"
