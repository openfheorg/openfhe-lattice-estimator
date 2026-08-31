OpenFHE Lattice Estimator
=====================================

A parameter generation tool for DM/FHEW, CGGI/TFHE, and LMKCDEY. The tool takes as user input the desired design (e.g., bootstrapping method, failure probability, security level, etc.) and outputs several sets of optimized parameters (e.g., lattice parameter, ciphertext modulus, digit size), along with the runtime of bootstrapping for each set of parameters. The tool is based on [Lattice Estimator](https://github.com/malb/lattice-estimator), which provides functions for estimating the concrete security of Learning with Errors (LWE) instances.

## Running in Docker

The image below bundles every prerequisite -- SageMath, numpy/scipy, the
lattice-estimator, and an OpenFHE built with `WITH_NOISE_DEBUG=ON` -- so nothing
in the `Pre-requisites` section below needs to be installed on the host. Those
instructions remain for building natively.

```
docker compose build
```

The first build compiles OpenFHE from source **twice** -- once at
`NATIVE_SIZE=64` and once at 32, for the reasons in "32-bit and 64-bit native
words" below. That is about 4-5 minutes of compilation measured at
`MAKE_JOBS=8` on 8 cores, since unit tests, examples and benchmarks are all
disabled; on a first build the ~3 GB SageMath base image usually takes longer to
pull than the compile takes to run. Afterwards both are cached layers and
rebuilds are quick.

`MAKE_JOBS` sets the parallelism of those two OpenFHE builds and defaults to 4.
**The binding limit is memory, not cores**: the heavier translation units peak
near 2 GB of RSS each, so budget roughly 2 GB per job and raise it only as far
as the RAM allows -- an unbounded `-j$(nproc)` is how a many-core machine OOMs
here.

```
MAKE_JOBS=8 docker compose build     # ~16 GB of headroom wanted
```

Run the parameter selector exactly as documented below, prefixed by
`docker compose run --rm estimator`:

```
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 3 -d 0 -p STD128Q -f -40 -I 2 -i 200 -k 3 -l 2 -u 4 -n 8
```

Or start a shell in the repo root inside the container and work from there:

```
docker compose run --rm estimator
```

For single-core KeyGen/EvalBinGate timings, set `OMP_NUM_THREADS` on the host --
compose forwards it into the container:

```
OMP_NUM_THREADS=1 docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 3 -d 0 -p STD128Q -n 8
```

### Use `sage -python`, not `python3`

Inside the container **both** scripts must be run with `sage -python`, including
`binfhe_params_validator.py`:

```
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params_validator.py -p STD128_4 -t 2 -I 4 -i 1000
```

`binfhe_params_validator.py` imports `binfhe_params_helper`, which does
`from estimator import *`, so it needs SageMath just as `binfhe_params.py` does.
The plain `python3` form documented further below works on a Debian/Ubuntu host
where `apt install sagemath` puts sagelib into the system python3; it does not
work here, because this image's Sage lives in its own venv and the system
python3 cannot see it.

Do not work around that by putting Sage's venv on `PATH`. Importing `sage.all`
from a python that lacks Sage's environment does not fail cleanly: it retries
its interface subprocesses (GAP, Singular, PARI) without bound and spawns
processes until the machine is unusable.

### How a measurement is made

Each candidate is measured twice, because speed and noise want opposite setups:

- **Speed** is one process with OpenMP unrestricted. A gate uses every core it
  is given, so that is the representative number. Measured single-threaded the
  same gate reads ~30% slower.
- **Noise** is `-K` independent keys, one single-threaded process per key, run in
  parallel, with the noise pooled. Keys parallelise properly where OpenMP inside
  a gate does not -- 5.1x on 8 cores in our measurements.

`-i` is therefore **samples per key**: the total is `-i * -K`.

`-i` sizes the *search*, where a measurement only has to rank one candidate
against another. The run that confirms the winner uses `max(-i, 1000)` samples
per key instead, because that number is the one quoted as a failure probability
rather than used for a comparison. The `command args:` line printed with the
result shows the count actually used.

Every key is always measured; the fan-out only controls how many run at once.
It is sized from the key material the speed probe just reported, because memory
binds before CPU does -- a key-switching key runs to hundreds of MB and past
30 GB at the largest dimensions. Cores the fan-out leaves idle are handed back
to OpenMP, so a candidate too large to run 8-up still gets the whole machine
one process at a time. `ESTIMATOR_MAX_PARALLEL` overrides the count.

The container has **no memory limit by default**, so it may use all of host RAM;
the fan-out reads `MemAvailable` and honours a cgroup limit if you impose one
(`mem_limit:` in `docker-compose.yml`, or `docker run -m`). Setting a limit is
worth it on a shared machine -- without one, an over-eager fan-out is a
host-wide OOM rather than a container-level one.

`-K` defaults to **8**. One key leaves a ~2% noise spread that no amount of
sampling reduces, which is several bits of failure probability; the same
parameter set measured with one key read 2^-164 and with eight read 2^-128.
Each parallel process holds its own bootstrapping key (hundreds of MB, GBs at
STD256), so on a memory-constrained machine cap the fan-out with
`ESTIMATOR_MAX_PARALLEL`.

Because speed is cheap to measure and noise is not, `binfhe_params.py -x PCT`
skips the noise run for any candidate more than `PCT` percent slower than the
fastest configuration already known to meet the target failure rate -- such a
candidate could not be the one you ship even if its noise were fine.

```
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 2 -p STD128 -i 200 -K 8 -x 25
```

### Picking a winner

The search evaluates one configuration per gadget digit count `d_g`, and used to
leave you to compare them by eye. It now keeps every completed configuration and
names one at the end:

```
sweep summary: STD128, ternary, 2-input, GINX, target 2^-40
d_g        gate(ms)       log2Pf   keys(MiB)           n   target
4              80.0        -12.0         400         530   MISSED
2              97.5        -90.4         940         558   met
3             140.0       -120.0         500         541   met
5        all 3 candidates measured were slower than the best so far
WINNER (fastest of 2 meeting 2^-40): d_g 2 at 97.5 ms/gate, 2^-90.4
  command args: -n 558 -N 2048 ... -i 1000 -K 8
  table entry:  { 54, 4096, 558, 2048, 32768, 32, 134217728, 64, 10, ... }
```

The winner is the **fastest configuration whose measured failure probability
beats the target**. The `d_g 4` row above shows why both halves matter: it is
the fastest of the four and still loses, because 2^-12 misses a 2^-40 target.

Digit counts that produced nothing are listed with the reason, so a set skipped
by `-x` pruning is not confused with one where no parameters exist.

`d_g` is a **request, not an outcome**. `B_g` is quantised to a power of two, so
OpenFHE may span `Q` in fewer digits than asked -- at `logQ=28`, `-u 9` asks for
7, 8 and 9 and gets 7 every time. Rows are labelled with the count OpenFHE will
actually use (`8->7` when the two differ), and a request that lands on a
configuration already searched in this sweep is skipped rather than measured
again. Repeated runs of one identical search are not independent data points:
measured noise steers the bisection in `binary_search_n`, so it need not
converge to the same place twice. Three draws of the same search (STD128, GINX,
`B_g=2^4`, deliberately noisy at `-i 20 -K 2`) landed on **n = 521, 526 and 540**
with **2^-44.6, 2^-42.2 and 2^-40.4** -- a 4.2-bit spread, wider than the gaps
the sweep is being asked to rank. Raise `-i` and `-K` before believing a small
difference between two candidates.

The `-x` prune measures against this same winner rather than tracking its own
notion of "best", so the two cannot drift apart. Each record also carries noise
mean and stddev, key sizes and keygen time, which is what a Pareto rule would
need -- picking on speed alone is a starting point, not the last word.

### 32-bit and 64-bit native words

The image builds OpenFHE **twice**, at `NATIVE_SIZE=32` and `NATIVE_SIZE=64`,
and the estimator binaries are linked against both (`build32/` and `build/`).
Each candidate runs on the 32-bit build when its ring modulus fits in 28 bits
(`MAX_MODULUS_SIZE` for `NATIVEINT=32`) and on the 64-bit build otherwise.

Measured as a controlled A/B (identical parameters, only `NATIVE_SIZE` differing)
at Q=27: **2.59x at one thread** (108,858 -> 42,039 us) and **2.70x at eight**
(84,020 -> 31,114 us), with the noise statistically unchanged (stddev 13.68 vs
13.39, inside the per-key scatter). The same comparison on other hardware gives
2.06x, so treat the factor as machine- and toolchain-dependent rather than fixed.
The 32-bit build also halves the key material, so more measurement processes
fit in memory at once.

Outside Docker this is optional. A plain `cmake -B build` produces only the
64-bit binary and every candidate runs there; nothing needs configuring. To get
the same dispatch natively, install OpenFHE twice (once per `NATIVE_SIZE`) and
build against each:

```
cmake -S . -B build   -DCMAKE_PREFIX_PATH=/path/to/openfhe64 -DCMAKE_BUILD_RPATH=/path/to/openfhe64/lib
cmake -S . -B build32 -DCMAKE_PREFIX_PATH=/path/to/openfhe32 -DCMAKE_BUILD_RPATH=/path/to/openfhe32/lib
cmake --build build && cmake --build build32
```

The RPATH matters: both installs carry the same SONAMEs, so without it the
dynamic linker resolves both binaries to whichever it finds first.

Build directories are found relative to the repository root, so the scripts do
not have to be run from it. For an out-of-tree layout, set `ESTIMATOR_BUILD_DIR`
and optionally `ESTIMATOR_BUILD32_DIR`.

`NATIVE_SIZE` is a build-wide choice in OpenFHE, which is why this needs two
libraries rather than a flag. Both carry the same SONAMEs, so only the 64-bit
install is registered with the dynamic linker and the 32-bit binaries find
theirs through an RPATH baked in at link time.

A named parameter set (`-p STD128`) keeps its modulus inside OpenFHE, so there
is nothing to dispatch on: the 32-bit build is tried first and, if the modulus
does not fit, it fails loudly (`Requested bit length 54 exceeds maximum allowed
length 28`) and the run is retried on the 64-bit build.

### Security tables

`scripts/paramsestimator/paramstable.py` holds OpenFHE's own
`StandardLatticeParmSets` (from `src/core/lib/lattice/stdlatticeparms.cpp`,
which encodes the HomomorphicEncryption.org standard), for the uniform, error
and ternary secret distributions at each level, classical and quantum.
`max_logq` interpolates between the tabulated dimensions, so it is exact at
every dimension OpenFHE tabulates.

These are only a starting point: `optimize_params_security` prices every
candidate with the lattice-estimator and corrects it. The two do not always
agree -- at STD192 the estimator certifies 188 bits for the table's own
n=1024/logq=19 row -- which is why nothing here is trusted without an
estimator run.

**Which attacks get priced is a choice, and it is worth about 4 bits.**
`ESTIMATOR_SECURITY_MODEL` selects it, and every run prints the model it used
next to its results:

| value | cost model | denied attacks | STD128 keyswitch pair |
|---|---|---|---|
| `standard` (default) | `BDGL16` | the hybrid families, `bkw`, `arora-gb` | **128 bits** |
| `conservative` | `MATZOV` | `bkw`, `arora-gb` only | **124 bits** |

`standard` reproduces the era OpenFHE's shipped tables were derived in, so
results stay comparable with them; that is the only reason it is the default.
`conservative` admits `dual_hybrid`, which at (n=558, q=2^15, uniform ternary)
is the strongest attack of all and 4 bits below what `standard` reports. If you
are choosing parameters to deploy rather than to compare against the shipped
table, use it:

```
ESTIMATOR_SECURITY_MODEL=conservative docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 2 -p STD128
```

The figures above are the classical path. A trailing `Q` selects `LaaMosPol14`
instead -- and **both models use the same quantum cost model**, differing only in
the deny list, so the quantum gap is a different quantity rather than the same
one. Measured at the shipped keyswitch pairs (ternary):

| set | classical: standard -> conservative | quantum: standard -> conservative |
|---|---|---|
| STD128Q (n=601, qKS=2^15) | 138 -> 134 | **127 -> 127** |
| STD192Q (n=890, qKS=2^15) | 208 -> 197 | **191 -> 187** |

The gap is **level-dependent**, so do not carry one level's number to another:
4 bits classically at STD128Q against 11 at STD192Q, and 0 bits on the quantum
path at STD128Q (the hybrids never beat `LaaMosPol14` there) against 4 at
STD192Q. Note also that both shipped `*Q` pairs price about a bit below their
nominal level even under `standard` -- 127 against 128, 191 against 192.

### Changing the OpenFHE version

The image pins one OpenFHE revision, currently the head of `dev`
(`252b2b1f`). Do not fall back to a release tag: `v1.5.1` and earlier predate
the per-dimension gadget bases, the digit-decomposition fix that this tool's
noise measurements depend on, and the `BTKeyGen` fix that multi-key measurement
(`-K`) depends on. `OPENFHE_REF` accepts a tag, a branch name, or a full commit
SHA:

```
OPENFHE_REF=v1.5.1 docker compose build
```

The entrypoint refuses to start if the OpenFHE it finds was not built with
`WITH_NOISE_DEBUG=ON`, since without that flag no noise values are emitted and
the scripts fail far from the cause.

### Editing the source

`docker-compose.dev.yml` mounts your working copy over the image's baked-in
copy, so changes to the Python scripts and to `src/*.cpp` take effect without a
rebuild (the C++ sources are recompiled on entry when needed):

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 3 --all
```

The container runs as uid 1000 by default so bind-mounted files stay writable.
If your uid differs, export `APP_UID=$(id -u) APP_GID=$(id -g)` before both the
`build` and the `run`.

The entrypoint runs an incremental `cmake --build` on every start, so an edited
`.cpp` is always recompiled before anything measures with it. It also discards a
`build/` that was configured against a different OpenFHE -- a native build left
in the working copy, say -- rather than linking the wrong library.

## Pre-requisites (native install, not needed when using Docker)

1. Install python with `sudo apt install python3` (tested with 3.8.10).
2. Install sage with `sudo apt install sagemath` (tested with SageMath version 9.0).
3. Install `numpy` and `scipy` using `pip3 install`.
4. Clone the lattice-estimator repository (`git clone https://github.com/malb/lattice-estimator.git`).
5. Add path for the cloned lattice-estimator directory to your PYTHONPATH environment variable.
6. Clone the [openfhe-development](https://github.com/openfheorg/openfhe-development) repository and checkout the `main` branch.
7. Follow the `Installation` instructions of the openfhe-development README to build and install the library.

   **NOTE: The `WITH_NOISE_DEBUG` flag must be set to `ON` while running cmake (e.g., `cmake -DWITH_NOISE_DEBUG=ON ..`) for propper integration with openfhe-lattice-estimator scripts.**

## Installation (native)

1. Clone the [openfhe-lattice-estimator](https://github.com/openfheorg/openfhe-lattice-estimator) repository.
2. Change to the openfhe-lattice-estimator directory and run
   ```
   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
   cmake --build build
   ```
   This produces `build/bin/boolean_noise_estimate_script`, which the scripts
   locate relative to the repository root. Set `ESTIMATOR_BUILD_DIR` if you
   build somewhere else. See "32-bit and 64-bit native words" above for the
   optional second build.

   `build/bin/boolean_estimate_time` is also produced. Nothing in the Python
   calls it; it is a standalone demo that builds one context, reports keygen
   time and key sizes, and evaluates a handful of gates. Use it to sanity-check
   a parameter set by hand -- `-h` lists its options.
3. Optionally, if single core runtimes for KeyGen or EvalBinGate are needed while generating optimal parameters, set OMP_NUM_THREADS to 1 (e.g., `export OMP_NUM_THREADS=1`)

## Instructions for binfhe_params.py

From the openfhe-lattice-estimator directory, execute `sage -python scripts/paramsestimator/binfhe_params.py` and answer the `Parameter Selector` prompts (press ENTER to select the default values).

Below is an example run for a set of input parameters and the corresponding output parameters from the script:

> sample input:
```
Parameter selector for FHEW like schemes
Enter Bootstrapping technique (1 = AP, 2 = GINX, 3 = LMKCDEY) [default = 2]: 3
Enter Secret distribution (0 = error, 1 = ternary) [default = 1]: 0
Enter Security level (STD128, STD128Q, STD192, STD192Q, STD256, STD256Q) [default = STD128]: STD128Q
Enter expected decryption failure rate (for example, enter -32 for 2^-32 failure rate) [default = -40]:
Enter expected number of inputs to the boolean gate (2, 3, or 4) [default = 2]:
Enter number of noise samples per key [default = 200]:
Enter key switching digit size (2, 3, or 4) [default = 3]:
Enter lower bound for digit decomposition digits [default = 2]:
Enter upper bound for digit decomposition digits [default = 4]:
Enter number of threads that can be used to run the lattice-estimator (only used for the estimator) [default = 1]: 8
Enter number of independent keys to pool noise over [default = 8]:
Enter % slower than a working set at which to skip a candidate's noise run [default = none]:
input parameters
bootstrapping_tech:  3
dist_type:  error
sec_level:  STD128Q
expected decryption failure rate:  -40
num_of_inputs:  2
num_of_samples:  200
num_of_keys:  8
prune candidates slower than (%):  None
d_ks:  3
d_g lower bound:  2
d_g upper bound:  4
num_of_threads:  8
security model:  standard (classical BDGL16, quantum LaaMosPol14; denied: ...)
command args:  -t 3 -d 0 -p STD128Q -f -40 -I 2 -i 200 -K 8 -k 3 -l 2 -u 4 -n 8
```

> sample output (one `d_g` block, then the summary; values are illustrative):
```
d_g loop:  3
(q, N): (1024, 1024)
target noise for this iteration:  25.7...
speed: .../build32/bin/boolean_noise_estimate_script ... -i 5 -K 1 (32-bit words)
noise: 8 keys, 8 at a time, 1 thread(s) each: ... -K 1 -Z (32-bit words)
(actual noise, mean, EvalBinGate us) (13.58, 0.82, 95238.1)
...
final parameters
dist_type:  error
bootstrapping_tech:  3
sec_level:  STD128Q
expected decryption failure rate:  -40
actual decryption failure rate:  -49.84773922673193
num_of_inputs:  2
num_of_samples:  200
num_of_keys:  8
lattice dimension n:  483
ringsize N:  1024
lattice modulus n:  2048
size of ring modulus Q:  27
optimal key switching modulus  Qks:  16384
gadget digit base B_g:  512
key switching digit base B_ks:  32
key switching digit size B_ks:  3
BootstrappingKeySize: 32168833
KeySwitchingKeySize: 382746661
CiphertextSize: 3909
BootstrapKeyGenTime: 1837
EvalBinGateTime: 95
EvalBinGateTimeUs: 95238.1
NumKeys: 1
Gate: 0
Failures: 0
ctmodq: 2048
command args:  -n 483 -N 1024 -q 2048 -Q 27 -k 16384 -g 512 -r 32 -b 32 -s 3.19 -t 3 -d 0 -I 2 -i 1000 -K 8
table entry:  { 27, 2048, 483, 2048, 16384, 32, 512, 32, 10, GAUSSIAN, 3.19, {{512, 483}} }
```

The `table entry:` line is pastable straight into `StandardLatticeParmSets` in
`src/core/lib/lattice/stdlatticeparms.cpp`, and its field order tracks
`BinFHEContextParams`: `numberBits, cyclOrder, latticeParam, mod, modKS, baseKS,
gadgetBase, baseRK, numAutoKeys, keyDist, stdDev, gadgetBaseMap`. The trailing
`gadgetBaseMap` assigns every one of the `n` LWE secret-key coefficients the
same gadget base, because this search evaluates one base at a time; the counts
in that map must sum to `latticeParam` or OpenFHE rejects the set.

OpenFHE allows several bases per set, and the four `LPF_*` sets carry two-base
maps -- but do not read those as worked examples. Three of the four move only
1.6-5.7% of their coefficients to the larger base and gain nothing measurable;
they came from an external contributor, are slated for regeneration, and any
noise measured for them before the digit-decomposition fix is confounded,
because all four pick a second base that triggered it. Searching over the split
is a dimension this tool does not yet explore -- every set it emits is
single-base.

`LPF_*` is not a separate kind of parameter set, either: it is the same
configuration generated against a lower failure-probability target, so
`-p STD128 -f -128` is how you produce one.

`NumKeys: 1` in that block is not a mistake: the performance figures come from
the speed probe, which is deliberately a single-key single process. The noise
that produced the failure rate came from the eight parallel processes on the
`noise:` line above it.

A run covers every digit size from `-l` to `-u`, and ends with the summary
described in "Picking a winner" above -- you no longer have to compare the
blocks by eye.
- Digit size influences runtime performance and noise level, which consequently affects the decryption failure rate.
- The 32-bit native word size outperforms 64-bit, so a candidate whose ring modulus fits in 28 bits is measured there automatically (28 bits is the maximum modulus size supported for 32-bit words in OpenFHE).
- The input parameter key-switching digit size d_ks influences the bootstrapping keygen time (in generating the internal key switching key), key switching key size and the noise level. A higher value of d_ks results in faster keygen time and smaller key size but larger noise.

To bypass the `Parameter Selector` prompts, execute binfhe_params.py with any of {t, d, p, f, I, i, K, x, k, l, u, n} arguments (`-h` lists them all) e.g.,
```
sage -python scripts/paramsestimator/binfhe_params.py -t 3 -d 0 -p STD128Q -f -40 -I 2 -i 200 -k 3 -l 2 -u 2 -n 8
```
or
```
sage -python scripts/paramsestimator/binfhe_params.py -t 3 -d 0 -p STD128Q -n 8
```
to select the same set of input parameters used in the example run from above.

To sweep every combination of security level `-p` and gate inputs `-I` for one
bootstrapping technique, pass `--all`:
```
sage -python scripts/paramsestimator/binfhe_params.py -t 3 --all
```
for LMKCDEY. `--all` varies only those two; `-d` and every other argument are
honoured exactly as in a single run. It used to force `-d 0` (GAUSSIAN), which
explored a secret distribution none of the shipped `*_LMKCDEY` sets use -- 43 of
the 45 shipped sets are `UNIFORM_TERNARY` -- so a full sweep produced parameters
that were not comparable with the library's own.
 
## Instructions for binfhe_params_validator.py

The validator measures the same way the selector does: `-i` samples per key,
pooled over `-K` independent keys (default 8), with the gate time taken
separately on the whole machine.

To calculate noise std deviation and probability of failure for a specific named BINFHE_PARAMSET within OpenFHE, execute binfhe_params_validator.py with the appropriate set of {p, t, I, i, K} arguments e.g.,
```
sage -python scripts/paramsestimator/binfhe_params_validator.py -p STD128_4 -t 2 -I 4 -i 1000
```
for 1000 iterations of 4-input GINX at STD128.

To calculate noise std deviation and probability of failure for all named BINFHE_PARAMSET within OpenFHE, execute binfhe_params_validator.py with -p ALL e.g.,
```
sage -python scripts/paramsestimator/binfhe_params_validator.py -p ALL -i 1000
```

To calculate noise std deviation and probability of failure for the `commandline arguments` printed out by the binfhe_params.py script, execute binfhe_params_validator.py without a -p argument and with the `commandline arguments` output e.g.,
```
sage -python scripts/paramsestimator/binfhe_params_validator.py -n 483 -N 1024 -q 2048 -Q 27 -k 16384 -g 512 -r 32 -b 32 -s 3.19 -t 3 -d 0 -I 2 -i 1000
```
for the `d_g loop: 3` parameters from above.
