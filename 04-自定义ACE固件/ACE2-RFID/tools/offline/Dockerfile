FROM debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-dev python3 \
    && rm -rf /var/lib/apt/lists/*
ENV ACE_ARM_BIN=/usr/bin
WORKDIR /work/integration-preload/unified-experiment
