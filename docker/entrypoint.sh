#!/usr/bin/env bash
#
# Entrypoint for the openfhe-lattice-estimator image.
#
# Two jobs: verify the OpenFHE we link against is actually usable for noise
# estimation, and make sure the estimator binaries exist -- they will not when
# docker-compose.dev.yml mounts the host repo over the baked-in build.

set -euo pipefail

cd /workspace

openfhe_config="${OPENFHE_INSTALL_DIR:-/opt/openfhe}/lib/OpenFHE/OpenFHEConfig.cmake"

# OpenFHEConfig.cmake records the flag as: set(OpenFHE_NOISEDEBUG "ON")
# Catching this here turns a confusing statistics.stdev() traceback (raised on
# an empty noise file, hundreds of lines into the Python) into a clear message.
if [[ -r "${openfhe_config}" ]]; then
    if ! grep -q 'set(OpenFHE_NOISEDEBUG "ON")' "${openfhe_config}"; then
        echo "ERROR: the installed OpenFHE was built without WITH_NOISE_DEBUG=ON;" >&2
        echo "       it will not emit the noise values these scripts parse." >&2
        echo "       Rebuild the image: docker compose build --no-cache" >&2
        exit 1
    fi
else
    echo "ERROR: no OpenFHE installation found at ${OPENFHE_INSTALL_DIR:-/opt/openfhe}" >&2
    exit 1
fi

# A bind-mounted checkout owned by a different uid otherwise fails several
# layers down, as an opaque "Unable to (re)create the private pkgRedirects
# directory" from CMake.
if [[ ! -w /workspace ]]; then
    echo "ERROR: /workspace is not writable by uid $(id -u)." >&2
    echo "       If you bind-mounted your checkout (docker-compose.dev.yml)," >&2
    echo "       re-run with APP_UID=\$(id -u) APP_GID=\$(id -g) so the" >&2
    echo "       container's uid matches the files on the host." >&2
    exit 1
fi

# Two builds, one per OpenFHE native word size. Both libraries carry the same
# SONAMEs, so each binary finds its own through an RPATH rather than the cache.
build_variant() {                    # dir, openfhe prefix
    local cache="$1/CMakeCache.txt"

    # A cache configured for a different OpenFHE -- the other native word size,
    # or a native build bind-mounted in from the host -- would link the wrong
    # library, so start that variant over rather than build on top of it.
    #
    # The entry is matched WITHOUT its type: cmake records a -D with no declared
    # type as ":UNINITIALIZED", not ":PATH", so pinning the type here made the
    # comparison fail every time and wiped the build dir on every start.
    if [[ -f "${cache}" ]]; then
        local configured_for
        configured_for=$(sed -n 's|^CMAKE_PREFIX_PATH:[^=]*=||p' "${cache}" | head -1)
        if [[ "${configured_for}" != "$2" ]]; then
            echo "[entrypoint] ./$1 was configured against '${configured_for:-nothing}', not $2; reconfiguring" >&2
            rm -rf "$1"
        fi
    fi

    if [[ ! -f "${cache}" ]]; then
        echo "[entrypoint] configuring ./$1 against $2 ..." >&2
        cmake -S . -B "$1" -DCMAKE_BUILD_TYPE=Release \
              -DCMAKE_PREFIX_PATH="$2" -DCMAKE_BUILD_RPATH="$2/lib" > /dev/null
    fi

    # Always build. It is incremental, so this costs a second when nothing
    # changed -- and it is the only thing that picks up an edited src/*.cpp.
    # Guarding it on the binary already existing meant a bind-mounted build/
    # from an earlier session kept running the OLD binary against new sources,
    # reporting stale measurements as if they were current.
    cmake --build "$1" --parallel "${MAKE_JOBS:-$(nproc)}" >&2
}

build_variant build   "${OPENFHE_INSTALL_DIR:-/opt/openfhe}"
[[ -d "${OPENFHE32_INSTALL_DIR:-/opt/openfhe32}" ]] && build_variant build32 "${OPENFHE32_INSTALL_DIR:-/opt/openfhe32}"

exec "$@"
