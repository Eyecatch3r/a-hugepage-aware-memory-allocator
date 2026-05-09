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
REDIS_OPT_FLAGS="${REDIS_OPT_FLAGS:--O3}"
GPERFTOOLS_CFLAGS="${GPERFTOOLS_CFLAGS:--O3}"
GPERFTOOLS_CXXFLAGS="${GPERFTOOLS_CXXFLAGS:--O3}"
BAZEL_COMPILATION_MODE="${BAZEL_COMPILATION_MODE:-opt}"
BAZEL_COPT="${BAZEL_COPT:--O3}"
BAZEL_CXXOPT="${BAZEL_CXXOPT:--O3}"

mkdir -p "${BUILD_DIR}" "${INSTALL_DIR}" "${SRC_DIR}"

sync_repo() {
  local url="$1"
  local ref="$2"
  local dir="$3"

  if [[ ! -d "${dir}/.git" ]]; then
    git clone --branch "${ref}" --depth 1 "${url}" "${dir}"
    return
  fi

  git -C "${dir}" fetch --depth 1 origin "${ref}"
  git -C "${dir}" checkout --force FETCH_HEAD
}

sync_repo "https://github.com/redis/redis.git" "${REDIS_VERSION}" "${SRC_DIR}/redis"
sync_repo "https://github.com/gperftools/gperftools.git" "${GPERFTOOLS_REF}" "${SRC_DIR}/gperftools"
sync_repo "https://github.com/google/tcmalloc.git" "${GOOGLE_TCMALLOC_REF}" "${SRC_DIR}/google-tcmalloc"

if ! command -v bazelisk >/dev/null 2>&1; then
  curl -fsSL -o /usr/local/bin/bazelisk https://github.com/bazelbuild/bazelisk/releases/download/v1.19.0/bazelisk-linux-amd64
  chmod +x /usr/local/bin/bazelisk
  ln -sf /usr/local/bin/bazelisk /usr/local/bin/bazel
fi

pushd "${SRC_DIR}/redis" >/dev/null
make distclean >/dev/null 2>&1 || true
make -j"$(nproc)" CC="${CC:-clang}" MALLOC=libc OPT="${REDIS_OPT_FLAGS}"
popd >/dev/null

pushd "${SRC_DIR}/gperftools" >/dev/null
autoreconf -fi
CFLAGS="${GPERFTOOLS_CFLAGS}" CXXFLAGS="${GPERFTOOLS_CXXFLAGS}" \
  ./configure --prefix="${INSTALL_DIR}/gperftools"
make -j"$(nproc)"
make install
popd >/dev/null

cp -f "${ROOT_DIR}/docker/tcmalloc_bazel_wrapper/wrapper.cc" "${SRC_DIR}/google-tcmalloc/tcmalloc/testing/codex_wrapper.cc"

if ! grep -q 'codex_libtcmalloc_temeraire.so' "${SRC_DIR}/google-tcmalloc/tcmalloc/testing/BUILD"; then
  cat >> "${SRC_DIR}/google-tcmalloc/tcmalloc/testing/BUILD" <<'EOF'

cc_binary(
    name = "codex_libtcmalloc_temeraire.so",
    srcs = ["codex_wrapper.cc"],
    linkshared = 1,
    malloc = "//tcmalloc",
)

cc_binary(
    name = "codex_libtcmalloc_legacy.so",
    srcs = ["codex_wrapper.cc"],
    linkshared = 1,
    malloc = "//tcmalloc",
    deps = ["//tcmalloc:want_no_hpaa"],
)
EOF
fi

pushd "${SRC_DIR}/google-tcmalloc" >/dev/null
USE_BAZEL_VERSION="${USE_BAZEL_VERSION:-4.2.2}" bazelisk build \
  -c "${BAZEL_COMPILATION_MODE}" \
  --copt="${BAZEL_COPT}" \
  --cxxopt="${BAZEL_CXXOPT}" \
  //tcmalloc/testing:codex_libtcmalloc_temeraire.so \
  //tcmalloc/testing:codex_libtcmalloc_legacy.so
mkdir -p "${INSTALL_DIR}/google-tcmalloc/lib"
cp -f bazel-bin/tcmalloc/testing/codex_libtcmalloc_temeraire.so "${INSTALL_DIR}/google-tcmalloc/lib/libtcmalloc_temeraire.so"
cp -f bazel-bin/tcmalloc/testing/codex_libtcmalloc_legacy.so "${INSTALL_DIR}/google-tcmalloc/lib/libtcmalloc_legacy.so"
popd >/dev/null

echo "Environment setup complete."
echo "Redis source: ${SRC_DIR}/redis"
echo "gperftools install: ${INSTALL_DIR}/gperftools"
echo "google tcmalloc install: ${INSTALL_DIR}/google-tcmalloc"
