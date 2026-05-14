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
    libxml2-dev \
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
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
