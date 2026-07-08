#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
THIRD_PARTY_DIR="${ROOT_DIR}/third_party"
BUILD_DIR="${THIRD_PARTY_DIR}/build"
INSTALL_DIR="${THIRD_PARTY_DIR}/install"
SRC_DIR="${THIRD_PARTY_DIR}/src"

REDIS_VERSION="${REDIS_VERSION:-6.0.9}"
GPERFTOOLS_REF="${GPERFTOOLS_REF:-gperftools-2.16}"
GOOGLE_TCMALLOC_REF="${GOOGLE_TCMALLOC_REF:-8e534f50707469baac732559494559db95732e12}"
LLVM_REPO_URL="${LLVM_REPO_URL:-https://github.com/llvm/llvm-project.git}"
LLVM_REF="${LLVM_REF:-cd442157cff4aad209ae532cbf031abbe10bc1df}"
BUILD_EXACT_LLVM="${BUILD_EXACT_LLVM:-0}"
REDIS_OPT_FLAGS="${REDIS_OPT_FLAGS:--O3}"
GPERFTOOLS_CFLAGS="${GPERFTOOLS_CFLAGS:--O3}"
GPERFTOOLS_CXXFLAGS="${GPERFTOOLS_CXXFLAGS:--O3}"
BAZEL_COMPILATION_MODE="${BAZEL_COMPILATION_MODE:-opt}"
BAZEL_COPT="${BAZEL_COPT:--O3}"
BAZEL_CXXOPT="${BAZEL_CXXOPT:--O3}"
LLVM_CMAKE_BUILD_TYPE="${LLVM_CMAKE_BUILD_TYPE:-Release}"
LLVM_TARGETS_TO_BUILD="${LLVM_TARGETS_TO_BUILD:-X86}"
LLVM_PROJECTS="${LLVM_PROJECTS:-clang}"
LLVM_SUPPRESS_DEV_WARNINGS="${LLVM_SUPPRESS_DEV_WARNINGS:-1}"

mkdir -p "${BUILD_DIR}" "${INSTALL_DIR}" "${SRC_DIR}"

is_commit_ref() {
  local ref="$1"
  [[ "${ref}" =~ ^[0-9a-f]{7,40}$ ]]
}

sync_repo() {
  local url="$1"
  local ref="$2"
  local dir="$3"

  if [[ ! -d "${dir}/.git" ]]; then
    if is_commit_ref "${ref}"; then
      # A raw commit hash cannot be cloned via --branch, so fetch history first
      # and resolve the commit locally.
      git clone --filter=blob:none --no-checkout "${url}" "${dir}"
    else
      git clone --branch "${ref}" --depth 1 "${url}" "${dir}"
      return
    fi
  fi

  if is_commit_ref "${ref}"; then
    git -C "${dir}" fetch --filter=blob:none origin
    git -C "${dir}" checkout --force "${ref}"
    return
  fi

  git -C "${dir}" fetch --depth 1 origin "${ref}"
  git -C "${dir}" checkout --force FETCH_HEAD
}

sync_repo "https://github.com/redis/redis.git" "${REDIS_VERSION}" "${SRC_DIR}/redis"
sync_repo "https://github.com/gperftools/gperftools.git" "${GPERFTOOLS_REF}" "${SRC_DIR}/gperftools"
sync_repo "https://github.com/google/tcmalloc.git" "${GOOGLE_TCMALLOC_REF}" "${SRC_DIR}/google-tcmalloc"

LLVM_CC="${CC:-clang}"
LLVM_CXX="${CXX:-clang++}"

if [[ "${BUILD_EXACT_LLVM}" == "1" ]]; then
  sync_repo "${LLVM_REPO_URL}" "${LLVM_REF}" "${SRC_DIR}/llvm-project"

  llvm_src_dir="${SRC_DIR}/llvm-project/llvm"
  if [[ ! -f "${llvm_src_dir}/CMakeLists.txt" ]]; then
    llvm_src_dir="${SRC_DIR}/llvm-project"
  fi

  llvm_build_dir="${BUILD_DIR}/llvm-project"
  rm -rf "${llvm_build_dir}"
  mkdir -p "${llvm_build_dir}"

  cmake_args=(
    -S "${llvm_src_dir}"
    -B "${llvm_build_dir}"
    -G Ninja
    -DCMAKE_BUILD_TYPE="${LLVM_CMAKE_BUILD_TYPE}"
    -DLLVM_ENABLE_PROJECTS="${LLVM_PROJECTS}"
    -DLLVM_TARGETS_TO_BUILD="${LLVM_TARGETS_TO_BUILD}"
    -DLLVM_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF
    -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_DOCS=OFF
  )
  if [[ "${LLVM_SUPPRESS_DEV_WARNINGS}" == "1" ]]; then
    cmake_args+=( -Wno-dev )
  fi

  cmake "${cmake_args[@]}"
  cmake --build "${llvm_build_dir}" --target clang llvm-ar llvm-ranlib --parallel "$(nproc)"

  # Use the freshly built compiler from the build tree. Installing a partial
  # LLVM build is fragile because CMake expects artifacts for unbuilt targets.
  LLVM_CC="${llvm_build_dir}/bin/clang"
  LLVM_CXX="${llvm_build_dir}/bin/clang++"

  if ! printf '#include <stddef.h>\n' | "${LLVM_CC}" -E - >/dev/null 2>&1; then
    echo "Built LLVM toolchain cannot find standard headers." >&2
    echo "Expected a usable clang at ${LLVM_CC}, but preprocessing <stddef.h> failed." >&2
    exit 1
  fi
fi

if ! command -v bazelisk >/dev/null 2>&1; then
  curl -fsSL -o /usr/local/bin/bazelisk https://github.com/bazelbuild/bazelisk/releases/download/v1.19.0/bazelisk-linux-amd64
  chmod +x /usr/local/bin/bazelisk
  ln -sf /usr/local/bin/bazelisk /usr/local/bin/bazel
fi

pushd "${SRC_DIR}/redis" >/dev/null
make distclean >/dev/null 2>&1 || true
make -j"$(nproc)" CC="${LLVM_CC}" MALLOC=libc OPT="${REDIS_OPT_FLAGS}"
popd >/dev/null

pushd "${SRC_DIR}/gperftools" >/dev/null
autoreconf -fi
CFLAGS="${GPERFTOOLS_CFLAGS}" CXXFLAGS="${GPERFTOOLS_CXXFLAGS}" CC="${LLVM_CC}" CXX="${LLVM_CXX}" \
  ./configure --prefix="${INSTALL_DIR}/gperftools"
make -j"$(nproc)"
make install
popd >/dev/null

cp -f "${ROOT_DIR}/docker/tcmalloc_bazel_wrapper/wrapper.cc" "${SRC_DIR}/google-tcmalloc/tcmalloc/testing/temeraire_wrapper.cc"

if ! grep -q 'temeraire_libtcmalloc_temeraire.so' "${SRC_DIR}/google-tcmalloc/tcmalloc/testing/BUILD"; then
  cat >> "${SRC_DIR}/google-tcmalloc/tcmalloc/testing/BUILD" <<'EOF'

cc_binary(
    name = "temeraire_libtcmalloc_temeraire.so",
    srcs = ["temeraire_wrapper.cc"],
    linkshared = 1,
    malloc = "//tcmalloc",
)

cc_binary(
    name = "temeraire_libtcmalloc_legacy.so",
    srcs = ["temeraire_wrapper.cc"],
    linkshared = 1,
    malloc = "//tcmalloc",
    deps = ["//tcmalloc:want_no_hpaa"],
)
EOF
fi

pushd "${SRC_DIR}/google-tcmalloc" >/dev/null
CC="${LLVM_CC}" CXX="${LLVM_CXX}" \
USE_BAZEL_VERSION="${USE_BAZEL_VERSION:-4.2.2}" bazelisk build \
  -c "${BAZEL_COMPILATION_MODE}" \
  --copt="${BAZEL_COPT}" \
  --cxxopt="${BAZEL_CXXOPT}" \
  --repo_env=CC="${LLVM_CC}" \
  --repo_env=CXX="${LLVM_CXX}" \
  --action_env=CC="${LLVM_CC}" \
  --action_env=CXX="${LLVM_CXX}" \
  //tcmalloc/testing:temeraire_libtcmalloc_temeraire.so \
  //tcmalloc/testing:temeraire_libtcmalloc_legacy.so
mkdir -p "${INSTALL_DIR}/google-tcmalloc/lib"
cp -f bazel-bin/tcmalloc/testing/temeraire_libtcmalloc_temeraire.so "${INSTALL_DIR}/google-tcmalloc/lib/libtcmalloc_temeraire.so"
cp -f bazel-bin/tcmalloc/testing/temeraire_libtcmalloc_legacy.so "${INSTALL_DIR}/google-tcmalloc/lib/libtcmalloc_legacy.so"
popd >/dev/null

echo "Environment setup complete."
echo "Redis source: ${SRC_DIR}/redis"
echo "gperftools install: ${INSTALL_DIR}/gperftools"
echo "google tcmalloc install: ${INSTALL_DIR}/google-tcmalloc"
if [[ "${BUILD_EXACT_LLVM}" == "1" ]]; then
  echo "llvm build dir: ${BUILD_DIR}/llvm-project"
  "${LLVM_CC}" --version || true
fi
