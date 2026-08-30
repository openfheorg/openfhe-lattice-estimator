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
FROM ${SAGE_IMAGE} AS openfhe-builder

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG OPENFHE_REPO=https://github.com/openfheorg/openfhe-development.git
ARG OPENFHE_REF=v1.5.1
ARG OPENFHE_PREFIX=/opt/openfhe
# Some OpenFHE translation units peak around 2 GB of RSS, so an unbounded
# -j$(nproc) will OOM a many-core machine. Default low; raise it explicitly.
ARG MAKE_JOBS=4

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libomp-dev \
    && rm -rf /var/lib/apt/lists/*

# --recurse-submodules is required: OpenFHE vendors cereal as a submodule.
RUN git clone --depth 1 --branch "${OPENFHE_REF}" \
        --recurse-submodules --shallow-submodules \
        "${OPENFHE_REPO}" /tmp/openfhe

# WITH_NOISE_DEBUG=ON is the whole point of this build. Without it OpenFHE emits
# no per-bootstrap noise values, the scripts read an empty noise file, and they
# fail deep inside statistics.stdev() with an error that names no cause.
RUN cmake -S /tmp/openfhe -B /tmp/openfhe/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="${OPENFHE_PREFIX}" \
        -DWITH_NOISE_DEBUG=ON \
        -DBUILD_UNITTESTS=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_BENCHMARKS=OFF \
    && cmake --build /tmp/openfhe/build --parallel "${MAKE_JOBS}" \
    && cmake --install /tmp/openfhe/build \
    && rm -rf /tmp/openfhe

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

# cmake/make/git are absent from the base image; the estimator's own two
# binaries are compiled here (and again at runtime if the repo is mounted over).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libomp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=openfhe-builder ${OPENFHE_PREFIX} ${OPENFHE_PREFIX}

# Register OpenFHE with the dynamic linker rather than exporting
# LD_LIBRARY_PATH, which would leak into every subprocess the scripts spawn.
RUN echo "${OPENFHE_PREFIX}/lib" > /etc/ld.so.conf.d/openfhe.conf && ldconfig

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
ENV PYTHONPATH=/opt/lattice-estimator \
    CMAKE_PREFIX_PATH=${OPENFHE_PREFIX} \
    OPENFHE_INSTALL_DIR=${OPENFHE_PREFIX} \
    HOME=/tmp \
    DOT_SAGE=/tmp/.sage \
    MPLCONFIGDIR=/tmp/.mpl

# The scripts shell out to "scripts/run_script.sh" and look for binaries under
# "build/bin", both relative -- so the workdir must be the repo root and the
# build directory must be named exactly "build".
WORKDIR /workspace
COPY . /workspace

RUN cmake -S /workspace -B /workspace/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /workspace/build --parallel "$(nproc)" \
    && chown -R ${APP_UID}:${APP_GID} /workspace

COPY docker/entrypoint.sh /usr/local/bin/estimator-entrypoint
RUN chmod 0755 /usr/local/bin/estimator-entrypoint

USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["/usr/local/bin/estimator-entrypoint"]
CMD ["bash"]
