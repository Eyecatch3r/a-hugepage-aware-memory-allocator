FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    bash \
    clang \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    gdb \
    git \
    jq \
    libtool \
    linux-perf \
    ninja-build \
    numactl \
    pkg-config \
    procps \
    python3 \
    python3-pip \
    redis-tools \
    sudo \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
