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

if [[ ! -x build/bin/boolean_noise_estimate_script || ! -x build/bin/boolean_estimate_time ]]; then
    echo "[entrypoint] estimator binaries missing, compiling into ./build ..." >&2
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release > /dev/null
    cmake --build build --parallel "${MAKE_JOBS:-$(nproc)}"
fi

exec "$@"
