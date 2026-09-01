#!/usr/bin/env bash
#
# Entrypoint for the openfhe-lattice-estimator image.
#
# Two jobs: verify the OpenFHE we link against is actually usable for noise
# estimation, and make sure the estimator binaries exist -- they will not when
# docker-compose.dev.yml mounts the host repo over the baked-in build.

set -euo pipefail

# Who the container is against who owns the mount. Both of the ways /workspace
# comes out unusable need this, and they need OPPOSITE fixes, so it is one
# function rather than a guess in two places.
workspace_owner_report() {
    local ws_uid ws_gid ws_mode
    ws_uid=$(stat -c %u /workspace 2>/dev/null || echo '?')
    ws_gid=$(stat -c %g /workspace 2>/dev/null || echo '?')
    ws_mode=$(stat -c %A /workspace 2>/dev/null || echo '?')
    echo "       this container runs as uid $(id -u), gid $(id -g)" >&2
    echo "       /workspace is owned by uid ${ws_uid}, gid ${ws_gid}, mode ${ws_mode}" >&2
    echo >&2
    if [[ "${ws_uid}" == "0" ]]; then
        echo "       The mount is owned by ROOT, so no ordinary uid can write it and" >&2
        echo "       matching APP_UID to your own will not help. On the HOST, either" >&2
        echo "       take ownership of the checkout:" >&2
        echo "         sudo chown -R \$(id -u):\$(id -g) ." >&2
        echo "       or run the container as root:" >&2
        echo "         APP_UID=0 APP_GID=0 docker compose ... run --rm estimator" >&2
    else
        echo "       Make the container's uid match the owner. On the HOST, in the repo" >&2
        echo "       root, write it once into .env so every compose command picks it up:" >&2
        echo "         printf 'APP_UID=%s\\nAPP_GID=%s\\n' \"\$(id -u)\" \"\$(id -g)\" > .env" >&2
        echo "       (or export APP_UID/APP_GID for this shell only), then re-run." >&2
        echo "       If ${ws_uid} is not your uid either, the checkout belongs to someone" >&2
        echo "       else and wants chown-ing first." >&2
    fi
}

# A mount the container cannot even traverse fails here, and `cd` alone reports
# nothing but "Permission denied" on a line number. Diagnose it the same way the
# writability check does.
if ! cd /workspace 2>/dev/null; then
    echo "ERROR: cannot enter /workspace." >&2
    workspace_owner_report
    exit 1
fi

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

# A bind-mounted checkout the container cannot write otherwise fails several
# layers down, as an opaque "Unable to (re)create the private pkgRedirects
# directory" from CMake.
#
# The message reports WHO the container is and WHO owns the mount, because the
# two ways to get here need opposite fixes and guessing between them wastes the
# reader's time: the container's uid may not match a checkout owned by you, or
# the checkout may be owned by root and match nobody. Telling someone to set
# APP_UID to their own uid is useless in the second case, and that is the case
# where the owner is a uid they never chose.
if [[ ! -w /workspace ]]; then
    echo "ERROR: /workspace is not writable." >&2
    workspace_owner_report
    exit 1
fi

# One build against the one installed OpenFHE. (There were two, one per native
# word size, until the library's 32-bit key forms became the default and the
# genuine NATIVE_SIZE=32 build stopped being the path anyone runs.)
build_variant() {                    # dir, openfhe prefix
    local cache="$1/CMakeCache.txt"

    # A cache configured for a different OpenFHE -- a native build bind-mounted
    # in from the host, say -- would link the wrong library, so start over
    # rather than build on top of it.
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
        # Same toolchain OpenFHE was built with. Left unset, cmake picks its
        # default `cc` -- which on this base image is gcc even when OpenFHE was
        # built with clang, silently mixing toolchains in a build the DSE then
        # labels with a single compiler.
        #
        # CXX only, deliberately. This project is `project(... CXX)` with no C
        # source, so a -DCMAKE_C_COMPILER is never consulted and cmake says so
        # on every start: "Manually-specified variables were not used by the
        # project: CMAKE_C_COMPILER". Harmless, and still worth not printing --
        # a warning that a compiler setting was ignored is exactly what someone
        # checking that their toolchain took effect does not need to see.
        cmake -S . -B "$1" -DCMAKE_BUILD_TYPE=Release \
              ${ESTIMATOR_CXX:+-DCMAKE_CXX_COMPILER="${ESTIMATOR_CXX}"} \
              -DCMAKE_PREFIX_PATH="$2" -DCMAKE_BUILD_RPATH="$2/lib" > /dev/null
    fi

    # Always build. It is incremental, so this costs a second when nothing
    # changed -- and it is the only thing that picks up an edited src/*.cpp.
    # Guarding it on the binary already existing meant a bind-mounted build/
    # from an earlier session kept running the OLD binary against new sources,
    # reporting stale measurements as if they were current.
    cmake --build "$1" --parallel "${MAKE_JOBS:-$(nproc)}" >&2
}

build_variant build "${OPENFHE_INSTALL_DIR:-/opt/openfhe}"

exec "$@"
