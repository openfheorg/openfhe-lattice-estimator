# syntax=docker/dockerfile:1.7
#
# openfhe-lattice-estimator
#
# Everything the README lists under "Pre-requisites" is baked in here: SageMath,
# numpy/scipy, the lattice-estimator, and an OpenFHE built with WITH_NOISE_DEBUG.
#
# The base image is a plain apt-based Ubuntu 24.04 with Sage installed into a
# venv under /home/sage -- there is no conda involved, so OpenFHE, the estimator
# binaries and Sage all share one system libstdc++.
#
#   docker compose build
#   docker compose run --rm estimator \
#       sage -python scripts/paramsestimator/binfhe_params.py -t 3 -d 0 -p STD128Q -n 8
#
# To move to a newer OpenFHE, bump OPENFHE_REF (or pass --build-arg).

ARG SAGE_IMAGE=sagemath/sagemath:10.9

# ---------------------------------------------------------------------------
# Stage 1 -- build and install OpenFHE.
#
# Kept in its own stage so the ~2 GB source/object tree never reaches the final
# image, and so this (slow) layer is cached until OPENFHE_REF actually changes.
# ---------------------------------------------------------------------------
FROM ${SAGE_IMAGE} AS openfhe-base

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG OPENFHE_REPO=https://github.com/openfheorg/openfhe-development.git
# Head of openfhe-development dev as of 2026-08-30.
# A tag, branch name or full commit SHA all work here.
#
# This is the squash-merge of PR #1268 (issue1144-fhew-param-opt), so dev now
# carries three things this tool depends on, none of which are in v1.5.1:
#   - the excess-H lost-carry fix in both SignedDigitDecompose overloads. The
#     defect (campaign PR #1238, 0473a7b5) silently corrupted measured noise for
#     any (paramset, baseG) pair whose base overflows -- up to 74 bits of log2Pf.
#   - per-dimension gadget bases, which add a column to the parameter table.
#   - the BTKeyGen fix: m_BTKey_map is keyed by gadget base alone, so a second
#     BTKeyGen on one context used to hand back the first key regardless of sk.
#     Multi-key noise measurement (-K) reads as ~10x the true stdev without it.
ARG OPENFHE_REF=252b2b1f410b0bd5f7b25faa4a4791d8a0882fdf
ARG OPENFHE_PREFIX=/opt/openfhe
# Some OpenFHE translation units peak around 2 GB of RSS, so an unbounded
# -j$(nproc) will OOM a many-core machine. Default low; raise it explicitly.
ARG MAKE_JOBS=4
# clang-18 measured ~2x faster than gcc for BinFHE gates in our own A/B runs.
ARG CC_BIN=clang-18
ARG CXX_BIN=clang++-18
# WARNING: ON compiles -march=native, so the image only runs on a CPU at least
# as capable as the one that BUILT it -- otherwise SIGILL. Set OFF to produce a
# portable image; keep ON when building on the machine you will measure on.
ARG WITH_NATIVEOPT=ON

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        clang-18 \
        cmake \
        git \
        libomp-18-dev \
        libomp-dev \
    && rm -rf /var/lib/apt/lists/*

# init+fetch rather than `clone --branch`, which accepts only tags and branch
# names. This form also takes a full commit SHA, which is how a dev-branch head
# gets pinned. Submodules are required: OpenFHE vendors cereal.
RUN git init -q /tmp/openfhe \
    && git -C /tmp/openfhe remote add origin "${OPENFHE_REPO}" \
    && git -C /tmp/openfhe fetch --depth 1 origin "${OPENFHE_REF}" \
    && git -C /tmp/openfhe checkout -q --detach FETCH_HEAD \
    && git -C /tmp/openfhe submodule update --init --recursive --depth 1 \
    && git -C /tmp/openfhe log -1 --format="pinned OpenFHE: %H %d %s"

# WITH_NOISE_DEBUG=ON is the whole point of this build. Without it OpenFHE emits
# no per-bootstrap noise values, the scripts read an empty noise file, and they
# fail deep inside statistics.stdev() with an error that names no cause.
# ---------------------------------------------------------------------------
# Stage 1b/1c -- the same source, built at each native word size.
#
# BinFHE is roughly 2x faster at NATIVE_SIZE=32 (halved bandwidth streaming the
# bootstrapping key, twice the SIMD lanes, and 32-bit modular loops vectorise at
# baseline SSE2 where 64-bit needs AVX-512). NATIVE_SIZE is a build-wide choice,
# so getting that speedup for the parameter sets whose modulus fits means
# shipping both libraries and dispatching per candidate.
# ---------------------------------------------------------------------------
FROM openfhe-base AS openfhe-ns64
ARG MAKE_JOBS
ARG CC_BIN
ARG CXX_BIN
ARG WITH_NATIVEOPT
RUN cmake -S /tmp/openfhe -B /tmp/build64 \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER="${CC_BIN}" \
        -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
        -DCMAKE_INSTALL_PREFIX=/opt/openfhe \
        -DNATIVE_SIZE=64 \
        -DWITH_NOISE_DEBUG=ON \
        -DWITH_NATIVEOPT="${WITH_NATIVEOPT}" \
        -DBUILD_UNITTESTS=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_BENCHMARKS=OFF \
    && cmake --build /tmp/build64 --parallel "${MAKE_JOBS}" \
    && cmake --install /tmp/build64 \
    && rm -rf /tmp/build64

FROM openfhe-base AS openfhe-ns32
ARG MAKE_JOBS
ARG CC_BIN
ARG CXX_BIN
ARG WITH_NATIVEOPT
RUN cmake -S /tmp/openfhe -B /tmp/build32 \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER="${CC_BIN}" \
        -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
        -DCMAKE_INSTALL_PREFIX=/opt/openfhe32 \
        -DNATIVE_SIZE=32 \
        -DWITH_NOISE_DEBUG=ON \
        -DWITH_NATIVEOPT="${WITH_NATIVEOPT}" \
        -DBUILD_UNITTESTS=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_BENCHMARKS=OFF \
    && cmake --build /tmp/build32 --parallel "${MAKE_JOBS}" \
    && cmake --install /tmp/build32 \
    && rm -rf /tmp/build32

# ---------------------------------------------------------------------------
# Stage 2 -- the estimator image itself.
# ---------------------------------------------------------------------------
FROM ${SAGE_IMAGE} AS estimator

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG OPENFHE_PREFIX=/opt/openfhe
ARG LATTICE_ESTIMATOR_REPO=https://github.com/malb/lattice-estimator.git
ARG LATTICE_ESTIMATOR_REF=53da5982597709ba0fdf94ea37a84d822310fd84
# uid:gid that owns /workspace and runs the container. The default matches the
# usual first-user id on Linux, so a bind-mounted repo stays writable.
ARG APP_UID=1000
ARG APP_GID=1000
ARG CC_BIN=clang-18
ARG CXX_BIN=clang++-18

# cmake/make/git are absent from the base image; the estimator's own two
# binaries are compiled here (and again at runtime if the repo is mounted over).
# Same toolchain as stage 1 on purpose: this project's CMakeLists adopts
# OpenFHE_CXX_FLAGS verbatim, which were recorded by that compiler.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        clang-18 \
        cmake \
        git \
        libomp-18-dev \
        libomp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=openfhe-ns64 /opt/openfhe /opt/openfhe
COPY --from=openfhe-ns32 /opt/openfhe32 /opt/openfhe32

# Register OpenFHE with the dynamic linker rather than exporting
# LD_LIBRARY_PATH, which would leak into every subprocess the scripts spawn.
# Only the 64-bit install is registered with the dynamic linker: both builds
# carry identical SONAMEs, so a cache entry can only point at one of them. The
# 32-bit binaries locate their libraries through an RPATH baked in at link time.
RUN echo "/opt/openfhe/lib" > /etc/ld.so.conf.d/openfhe.conf && ldconfig

RUN git clone "${LATTICE_ESTIMATOR_REPO}" /opt/lattice-estimator \
    && git -C /opt/lattice-estimator checkout --detach "${LATTICE_ESTIMATOR_REF}" \
    && rm -rf /opt/lattice-estimator/.git

# Sage's tree ships as mode 0750 owned by the image's built-in `sage` user
# (uid 1001). Relaxing this one directory lets the container run under the
# host's uid instead, which is what keeps bind-mounted files writable.
RUN chmod 0755 /home/sage

# Deliberately NOT putting Sage's venv bin on PATH. Importing sage.all from a
# python that lacks Sage's own environment makes it retry its interface
# subprocesses (GAP, Singular, PARI) without bound -- a fork storm, not merely a
# slow import. Use `sage -python` for every script, including
# binfhe_params_validator.py: it imports binfhe_params_helper, which does
# `from estimator import *`. The README's plain `python3` form for that script
# is fine on a host where apt's sagemath puts sagelib in the system python3,
# but this image keeps Sage in its own venv, so it does not apply here.
# CC/CXX so the runtime rebuild in docker/entrypoint.sh uses the same compiler
ENV CC=${CC_BIN} \
    CXX=${CXX_BIN} \
    PYTHONPATH=/opt/lattice-estimator \
    CMAKE_PREFIX_PATH=${OPENFHE_PREFIX} \
    OPENFHE_INSTALL_DIR=${OPENFHE_PREFIX} \
    HOME=/tmp \
    DOT_SAGE=/tmp/.sage \
    MPLCONFIGDIR=/tmp/.mpl

# The scripts resolve "build/bin" and "build32/bin" against the repository root
# (not the working directory), so these two names are what they look for unless
# ESTIMATOR_BUILD_DIR / ESTIMATOR_BUILD32_DIR say otherwise.
WORKDIR /workspace
COPY . /workspace

RUN cmake -S /workspace -B /workspace/build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/opt/openfhe -DCMAKE_BUILD_RPATH=/opt/openfhe/lib \
    && cmake --build /workspace/build --parallel "$(nproc)" \
    && cmake -S /workspace -B /workspace/build32 -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/opt/openfhe32 -DCMAKE_BUILD_RPATH=/opt/openfhe32/lib \
    && cmake --build /workspace/build32 --parallel "$(nproc)" \
    && chown -R ${APP_UID}:${APP_GID} /workspace

COPY docker/entrypoint.sh /usr/local/bin/estimator-entrypoint
RUN chmod 0755 /usr/local/bin/estimator-entrypoint

USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["/usr/local/bin/estimator-entrypoint"]
CMD ["bash"]
