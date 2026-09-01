# syntax=docker/dockerfile:1.7
#
# openfhe-lattice-estimator
#
# Everything docs/getting-started.md lists under "Native install" is baked in
# here: SageMath, numpy/scipy, the lattice-estimator, and an OpenFHE built with
# WITH_NOISE_DEBUG.
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
# Moved 2026-09-02 to f56c301b -- the branch formerly called
# `issue1269`, now REBASED onto origin/dev d0d03be0 (the composite-scaling /
# SPARSE_ENCAPSULATED merge). Contents, newest first:
#
#   678ea50c  Make the NTT inner loops auto-vectorizable at 32 bits
#   1b8648e9  Extend the lazy inner product to AP and LMKCDEY on the 32-bit path
#   f56c301b  Run BinFHE bootstrapping at 32 bits in 64-bit builds for Q <= 2^28
#   b72753b5  parallelize LWEEncryptionScheme::KeySwitch  (was 0a1f7e95)
#   da4d1f48  bug fix in BinFHEScheme::Bootstrap
#   e50d0df2  Reject parameter combinations BinFHE cannot represent
#   f9694c39  Parallelise the CGGI accumulator, cache LMKCDEY automorphism maps
#   19ea1a3e  Remove the dead plaintext-divides-modulus guards
#   0952732e  Fix integer overflow in the LWE inner product for large moduli
#   a2b8db17  Reject GINX with non-ternary secret key distributions
#
# NOISE data collected against 252b2b1f REMAINS VALID, and that is measured
# rather than argued: gate outputs at the old and new bases are byte-identical
# across 70 comparisons (7 sets x 5 gates x 2 operand orders), because dev's
# changes are in the BigInteger backends and BinFHE is a pure NativeInteger
# consumer. All five landed changes are bit-identical to their predecessors
# except da4d1f48, which fixes a wrong ANSWER that carried normal noise -- so no
# sigma or log2Pf here can have been shifted by it.
#
# GATE_COST MUST be re-measured, and the model's SHAPE has changed:
#   - f9694c39 makes the accumulator parallel: GINX geomean 1.92x at NS64/36
#     threads but only 1.32x at NS32/36t, and +3.0%/+1.7% at ONE thread. Because
#     the two word sizes gain unequally, this NARROWS the NS32-vs-NS64 gap that
#     Addendum 19's conditional result rests on -- roughly 2.3x -> ~1.6x if the
#     36-thread ratio carries to the 8 threads our timings used. Re-measure
#     before repeating that number.
#   - b72753b5 makes the KEY SWITCH parallel, and it was the last fully-serial
#     block in a gate: 18.7x (STD128) to 26.8x (STD192) standalone at 36 threads.
#     Any cost term that folded the key switch into a constant is now
#     thread-count dependent in a way it was not.
#   - Thread width inside the key switch is capped at min(N*digitCountKS/128, 32),
#     so gate time stops improving past that rather than degrading.
#   - State the NUMA binding with any many-thread number. Sub-NUMA clustering can
#     report 4 nodes of 18 CPUs, so --cpunodebind=0,1 is ONE socket; a 2:1
#     oversubscription there produced a fake 6.9x regression.
# RESOLVED 2026-09-02. The branch head briefly did not compile:
#
#   lwe-pke.cpp:363: std::min((N * digitCount) / 128, 32)
#   error: no matching function for call to 'min'
#
# N and digitCount are both `const uint32_t`, so the first argument is uint32_t
# and the literal 32 is int; std::min is a single-template-parameter template and
# cannot deduce a common type. Force-pushed as b72753b5 with the explicit
# `std::min<uint32_t>(...)`. That single line in a single file is the ONLY
# difference between the two heads, and **da4d1f48 remains an ancestor of both**,
# so measurements taken against da4d1f48 during the gap are still valid rather
# than merely close.
#
# WHAT IS STALE AT THIS PIN: the committed GATE_COST cells were fitted at
# da4d1f48 -- parallel accumulator, SERIAL key switch. The key switch was the last
# fully-serial block in a gate (18.7-26.8x standalone at 36 threads) and its cost
# currently sits inside c0 and c1, so absolute gate times from those cells now
# understate this build's speed. Ratios between candidates sharing a
# (method, N, word) cell are far less exposed than absolutes.
#
# f56c301b adds the RUNTIME-NS32 HYBRID: a bootstrapping key held internally at 32
# bits inside a NATIVE_SIZE=64 build, for Q <= 2^28. It is OPT-IN --
# BTKeyGen(sk, mode, internal32=true), default false -- so every measurement taken
# before it stands unchanged, and b72753b5 remains an ancestor. The reviewer
# reports it bit-identical to the 64-bit path, so it moves COST and not noise.
#
# What it changes for the search: word size stops being a build decision and
# becomes a per-candidate qualification. dse_constraints.hybrid_ns32_ok() encodes
# the predicate. The w32 cost cells were fitted on a GENUINE NS32 build, which the
# reviewer measures as up to ~7% off the hybrid -- so the w32 cells want refitting
# ON THE HYBRID now that it is available.
#
# f56c301b adds the RUNTIME-NS32 HYBRID: a bootstrapping key held internally at
# 32 bits inside a NATIVE_SIZE=64 build, for Q <= 2^28. b72753b5 stays an
# ancestor, so everything measured before it holds.
#
# IT IS NOT AUTOMATIC, which is easy to get wrong. The whole feature sits behind
# `#if NATIVEINT != 32`, so a 64-bit build makes it POSSIBLE -- but BTKeyGen's
# `internal32` defaults to FALSE and both keys are additionally gated on their own
# Fits() check:
#
#     if (internal32 && LWESwitchingKey32Impl::Fits(...))  ek.KSkey32 = ...
#     if (internal32 && RingGSWACCKey32Impl::Fits(...))    ek.BSkey32 = ...
#
# With the flag off, both stay nullptr and the 64-bit keys are built. There is no
# `internal32 = true` anywhere in the tree. So a rebuild alone changes NOTHING
# measurable -- the harnesses now take `-3` to opt in, which also gives a clean
# A/B: same binary, same parameters, flag off versus on.
#
# Note the gating is PER KEY, so "qualifies" is not binary: a configuration can
# take the 32-bit switching key and fall through to the 64-bit accumulator.
# dse_constraints.hybrid_ns32_ok() returns a single bool and is therefore coarser
# than the library -- a candidate it rejects may still collect part of the gain.
#
# WHY THIS TIP AND NOT f56c301b. 1b8648e9 extends the lazy inner-product kernel
# from GINX to AP and LMKCDEY. Measuring at f56c301b would give GINX the kernel and
# not the others, skewing every method comparison toward GINX -- the same shape of
# error as the Addendum 24 retraction, where GINX had a parallel accumulator and
# LMKCDEY did not. 678ea50c then makes the 32-bit NTT butterflies
# auto-vectorizable, which mostly closes gcc's lag behind clang; this image builds
# with clang-18, which was already vectorizing, so that commit matters less here
# than it does upstream. Neither commit adds or renames files, so a plain rebuild
# suffices (unlike f56c301b, whose rgsw-acc32 -> rgsw-acckey32 rename needs a cmake
# re-run in warm build dirs).
#
# AP AND LMKCDEY NOW QUALIFY for the 32-bit path and roughly double on it (LMKCDEY
# 1.99x, AP 2.12x). dse_constraints.hybrid_ns32_ok was GINX-only, which was right
# for f56c301b and wrong one commit later; it is now transcribed from
# RingGSWACCKey32Impl::Fits, including the LMKCDEY default-base check its
# automorphism key switch requires.
#
# NOISE is unaffected: nothing below da4d1f48 changed, and noise was already shown
# unchanged across 252b2b1f -> da4d1f48 (6 sets, per-set within sampling).
#
# PIN 12858277 (2026-09-15): the head of openfhe-development `dev`. It is the
# squash-merge of the branch this tool was pinned to (#1295: the issue1269 work
# above, two per-gate guards and the 28-bit Fits cap from 09913224, e5d64a4c's
# fold of the 32-bit key GENERATION into shared templates, serialization for
# the 32-bit keys, and this tool's 108-row table as the library's parameter
# table) plus two CKKS-only PRs (#1308, #1276) that touch nothing under
# src/binfhe or src/core. Against the previous pin 94229558 the accumulator and
# key-switch inner loops are unchanged, the WITH_NOISE_DEBUG line is intact,
# and no key-layout commit is in the span, so the three layout flags stay on
# and the cost cells are CARRIED on a measured gate-time A/B
# (docs/image-and-pins.md, "What each pin moved").
#
# At this pin GetRefreshKey()/GetSwitchKey() return the 64-bit member as the
# context holds it, which is null when the 32-bit form is resident (the
# default). Serialize GetBTKey(), or read the 32-bit members' KeyBytes(); the
# harnesses do the latter (estimator::key_sizes in src/binfhe_cli.h).
#
# 94229558 itself is on no branch any more -- the squash replaced it -- which is
# exactly the "keeps resolving" hazard described beside .openfhe-ref below: it
# still builds, and describes a tree dev has moved past.
#
# FULL 40-char SHA required. `git fetch <abbrev>` fails with "couldn't find
# remote ref" -- the fetch-by-SHA protocol takes no abbreviation. Tested against
# origin: the abbreviated form fails, this one resolves.
ARG OPENFHE_REF=128582771b50ce798bfa36d63e43b550b1b17ea0
ARG OPENFHE_PREFIX=/opt/openfhe
# Some OpenFHE translation units peak around 2 GB of RSS, so an unbounded
# -j$(nproc) will OOM a many-core machine. Default low; raise it explicitly.
ARG MAKE_JOBS=4
# clang-18 measured ~2x faster than gcc for BinFHE gates in our own A/B runs --
# but that was measured SINGLE-THREADED and before the current hybrid work, and
# the ordering is reported to flip multi-threaded (gcc ahead). So treat this
# default as a starting point, not a finding: build a gcc image with
#
#   docker build --build-arg CC_BIN=gcc --build-arg CXX_BIN=g++ ...
#
# gcc-13 is already present via build-essential, so nothing else changes. The
# DSE keeps cost cells per (thread mode, compiler) regime for this reason and
# refuses to rank on a regime it has not measured; see dse_model.COST_REGIMES.
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
        gcc-14 \
        g++-14 \
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
    && git -C /tmp/openfhe log -1 --format="pinned OpenFHE: %H %d %s" \
    && git -C /tmp/openfhe log -1 --format="%H" >  /tmp/openfhe/.openfhe-ref \
    && git -C /tmp/openfhe log -1 --format="%s" >> /tmp/openfhe/.openfhe-ref
# The RESOLVED sha is written to a file (and carried into the final image below)
# because the line printed just above is only visible while this layer actually
# executes: a cached rebuild does not replay it, and neither does anyone who did
# not keep the build log. Without a record inside the image there is no way to
# ask "which OpenFHE am I actually linked against?", and two hazards go unseen.
#
#   1. A force-pushed sha KEEPS RESOLVING. GitHub serves it directly even once it
#      is no longer any branch tip, so the build never fails -- it happily keeps
#      describing superseded code. `git branch -r --contains <sha>` returning
#      nothing is the only tell, and nobody runs that.
#   2. Gate-cost cells are valid only for the commit they were measured on. With
#      the sha recorded, `dse.py doctor` compares the library it found against
#      the pin the active regime's cells carry and says so.
#
# Two lines: the sha, then the commit subject.

# WITH_NOISE_DEBUG=ON is the whole point of this build. Without it OpenFHE emits
# no per-bootstrap noise values, the scripts read an empty noise file, and they
# fail deep inside statistics.stdev() with an error that names no cause.
# ---------------------------------------------------------------------------
# Stage 1b -- build and install OpenFHE, once, at NATIVE_SIZE=64.
#
# There used to be a second build at NATIVE_SIZE=32, because BinFHE gates ran
# ~2x faster on 32-bit words and NATIVE_SIZE was a build-wide choice. Since
# OpenFHE 9e8045db a 64-bit build narrows each bootstrapping key to 32-bit
# internal forms by default whenever that key's moduli fit, so the 64-bit build
# now gets that speed on its own -- and a genuine NATIVE_SIZE=32 build is the
# WORSE measurement: the build system forces HAVE_INT128 off there, so it cannot
# use the lazy 128-bit inner product, and past six gadget digits it loses to the
# path a default caller gets. One library, one harness.
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
        gcc-14 \
        g++-14 \
        cmake \
        git \
        libomp-18-dev \
        libomp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=openfhe-ns64 /opt/openfhe /opt/openfhe
# One SOURCE file rides along with the install: dse_shipfit reads OpenFHE's
# shipped parameter table straight from binfhecontext.cpp rather than
# transcribing 45 rows into Python (a transcription is how cyclOrder was once
# read as N). Without it dse_beats and the shipped-set controls fail on a fresh
# machine, which is exactly where they are needed. BINFHECONTEXT_SRC below points
# the scripts at it.
COPY --from=openfhe-base /tmp/openfhe/src/binfhe/lib/binfhecontext.cpp /opt/openfhe/share/openfhe-src/binfhecontext.cpp

# And the resolved commit of that OpenFHE, so the image can answer "which
# library is this?" without a build log. `dse.py doctor` reads it and compares it
# against the pin the active cost regime's cells were measured on.
COPY --from=openfhe-base /tmp/openfhe/.openfhe-ref /opt/openfhe/share/openfhe-src/OPENFHE_REF

# Register OpenFHE with the dynamic linker rather than exporting
# LD_LIBRARY_PATH, which would leak into every subprocess the scripts spawn.
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
# `from estimator import *`. The plain `python3` form documented for a native
# install is fine on a host where apt's sagemath puts sagelib in the system
# python3, but this image keeps Sage in its own venv, so it does not apply here.
# CC/CXX so the runtime rebuild in docker/entrypoint.sh uses the same compiler
ENV CC=${CC_BIN} \
    CXX=${CXX_BIN} \
    PYTHONPATH=/opt/lattice-estimator \
    CMAKE_PREFIX_PATH=${OPENFHE_PREFIX} \
    OPENFHE_INSTALL_DIR=${OPENFHE_PREFIX} \
    HOME=/tmp \
    DOT_SAGE=/tmp/.sage \
    MPLCONFIGDIR=/tmp/.mpl \
    BINFHECONTEXT_SRC=/opt/openfhe/share/openfhe-src/binfhecontext.cpp \
    OPENFHE_REF_FILE=/opt/openfhe/share/openfhe-src/OPENFHE_REF

# The scripts resolve "build/bin" against the repository root (not the working
# directory), so that name is what they look for unless ESTIMATOR_BUILD_DIR says
# otherwise.
# Carried into the image so the entrypoint's runtime reconfigure (which happens
# whenever build/ is cleared, e.g. between regime measurements) uses the same
# toolchain the image was built with rather than cmake's default.
ENV ESTIMATOR_CC="${CC_BIN}" \
    ESTIMATOR_CXX="${CXX_BIN}"

WORKDIR /workspace
COPY . /workspace

# The harness must be built with the SAME compiler as OpenFHE. Without this
# flag cmake picked its default `cc` (gcc on Ubuntu) while OpenFHE was built
# with clang-18, so the image was a mixed toolchain and a ranking labelled
# "clang" was only half clang. The gate loop lives in OpenFHE, so the practical
# error was small -- but the DSE keys its cost cells by compiler, and a regime
# label has to mean what it says.
#
# CXX only: this project declares no C language and has no C source, so a
# -DCMAKE_C_COMPILER is unused and cmake warns that it is. The OpenFHE stages
# above do pass it, because OpenFHE's build does compile C.
RUN cmake -S /workspace -B /workspace/build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
        -DCMAKE_PREFIX_PATH=/opt/openfhe -DCMAKE_BUILD_RPATH=/opt/openfhe/lib \
    && cmake --build /workspace/build --parallel "$(nproc)" \
    && chown -R ${APP_UID}:${APP_GID} /workspace

COPY docker/entrypoint.sh /usr/local/bin/estimator-entrypoint
RUN chmod 0755 /usr/local/bin/estimator-entrypoint

USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["/usr/local/bin/estimator-entrypoint"]
CMD ["bash"]
