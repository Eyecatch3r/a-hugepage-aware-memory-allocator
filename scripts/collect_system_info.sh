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
  echo "## os-release"
  cat /etc/os-release
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
  if [[ -x "${ROOT_DIR}/third_party/install/llvm/bin/clang" ]]; then
    "${ROOT_DIR}/third_party/install/llvm/bin/clang" --version 2>/dev/null || true
    echo
    echo "llvm_repo_url=${LLVM_REPO_URL:-unknown}"
    echo "llvm_ref_requested=${LLVM_REF:-unknown}"
    if [[ -d "${ROOT_DIR}/third_party/src/llvm-project/.git" ]]; then
      git -C "${ROOT_DIR}/third_party/src/llvm-project" rev-parse HEAD
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
  else
    echo "google tcmalloc not built yet"
  fi
  echo
  echo "## allocator artifacts"
  ls -l "${ROOT_DIR}/third_party/install/gperftools/lib" 2>/dev/null || true
  ls -l "${ROOT_DIR}/third_party/install/google-tcmalloc/lib" 2>/dev/null || true
} > "${outfile}"

echo "Wrote ${outfile}"
