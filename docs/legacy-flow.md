# The parameter selector (`binfhe_params.py`) and validator

The measurement-driven selector. It takes the desired design (bootstrapping method, secret
distribution, security level, failure target, gate arity) and, for each gadget
digit count in a range, bisects the LWE dimension on **measured** noise until
the target is met, then reports the fastest configuration that met it. It
measures every candidate it considers, so a run is hours rather than minutes,
and it produces single-base sets only. The search-based flow in
[dse-flow.md](dse-flow.md) is the alternative; the two share the security
cache ([security.md](security.md#shared-with-the-legacy-selector)) and the
measurement harness.

Run everything under `sage -python` inside the container
([getting-started.md](getting-started.md#use-sage--python-never-python3)).
`binfhe_params_validator.py` imports the same helper, so it needs Sage too.

## Running the selector

Interactively, answering the prompts (ENTER takes the default):

```
docker compose run --rm estimator sage -python scripts/paramsestimator/binfhe_params.py
```

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
```

Or non-interactively, with any of `{t, d, p, f, I, i, K, x, k, l, u, n}`
(`-h` lists them all):

```
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 3 -d 0 -p STD128Q -f -40 -I 2 -i 200 -k 3 -l 2 -u 4 -n 8
```

`--all` sweeps every combination of security level `-p` and gate arity `-I`
for one bootstrapping technique; every other argument is honoured as in a
single run, including the secret distribution.

```
sage -python scripts/paramsestimator/binfhe_params.py -t 3 --all
```

For single-core keygen and gate timings set `OMP_NUM_THREADS=1` on the host;
compose forwards it into the container.

### Which named sets it may touch

The selector's `-p` choices are the six standard levels only. It never emits
`TOY*`, `LPF*`, `MEDIUM` or `SIGNED_MOD_TEST`; the validator measures all of
them, because its job is to check what actually ships. `LPF_X` is not a
separate kind of set: it is `X` generated against a lower failure target, so
`-p STD128 -f -128` is how one is produced.

## How a measurement is made

Each candidate is measured twice, because speed and noise want opposite
setups:

- **Speed** is one process with OpenMP unrestricted. A gate uses every core it
  is given, so that is the representative number. Measured single-threaded the
  same gate reads about 30 percent slower.
- **Noise** is `-K` independent keys, one single-threaded process per key, run
  in parallel, with the noise pooled. Keys parallelise properly where OpenMP
  inside a gate does not: 5.1x on 8 cores in our measurements.

`-i` is therefore **samples per key**; the total is `-i * -K`. `-i` sizes the
search, where a measurement only has to rank one candidate against another.
The run that confirms the winner uses `max(-i, 1000)` samples per key, because
that number is quoted as a failure probability rather than used for a
comparison. The `command args:` line printed with the result shows the count
actually used.

`-K` defaults to **8**. One key leaves a noise spread of roughly 1 to 2
percent that no amount of sampling reduces, which is several bits of failure
probability; the same parameter set measured with one key read 2^-164 and with
eight read 2^-128. The reasoning, and why a per-key quantile is the honest
figure, is in [verification.md](verification.md).

Every key is always measured; the fan-out only controls how many run at once.
It is sized from the key material the speed probe just reported, because
memory binds before CPU does: a key-switching key runs to hundreds of MB and
past 30 GB at the largest dimensions. Cores the fan-out leaves idle are handed
back to OpenMP, so a candidate too large to run 8-up still gets the whole
machine one process at a time. `ESTIMATOR_MAX_PARALLEL` overrides the count.
The container has no memory limit by default; the fan-out reads `MemAvailable`
and honours a cgroup limit if you impose one (`mem_limit:` in
`docker-compose.yml`, or `docker run -m`), which is worth doing on a shared
machine.

Because speed is cheap to measure and noise is not, `-x PCT` skips the noise
run for any candidate more than `PCT` percent slower than the fastest
configuration already known to meet the target: such a candidate could not be
the one you ship even if its noise were fine.

```
sage -python scripts/paramsestimator/binfhe_params.py -t 2 -p STD128 -i 200 -K 8 -x 25
```

Since OpenFHE `9e8045db` the library narrows each bootstrapping key to its
32-bit internal form whenever that key's moduli fit, so a candidate whose ring
modulus fits in 28 bits is measured on that path automatically; the `speed:`
and `noise:` lines say `32-bit key forms where they fit`. There is no second
build and nothing to pass ([image-and-pins.md](image-and-pins.md#one-library-and-the-hybrid-default)).

## Reading the output

One block per digit count, then a summary. Values here are illustrative:

```
d_g loop:  3
(q, N): (1024, 1024)
target noise for this iteration:  25.7...
speed: .../build/bin/boolean_noise_estimate_script ... -i 5 -K 1 (32-bit key forms where they fit)
noise: 8 keys, 8 at a time, 1 thread(s) each: ... -K 1 -Z (32-bit key forms where they fit)
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

- `NumKeys: 1` is not a mistake: the performance figures come from the speed
  probe, which is deliberately a single-key single process. The noise that
  produced the failure rate came from the eight parallel processes on the
  `noise:` line.
- The `table entry:` line is pastable into OpenFHE's `BinFHEContext` parameter
  table. Its field order tracks `BinFHEContextParams`: `numberBits, cyclOrder,
  latticeParam, mod, modKS, baseKS, gadgetBase, baseRK, numAutoKeys, keyDist,
  stdDev, gadgetBaseMap`. Note that `cyclOrder` is **2N**, not N. The trailing
  `gadgetBaseMap` assigns every one of the `n` coefficients the same base,
  because this search evaluates one base at a time; the counts must sum to
  `latticeParam` or OpenFHE rejects the set.
- OpenFHE allows several bases per set, and most rows of the current library
  table carry two-base maps (they are this tool's re-selected output), but the
  four `LPF_*` sets of the pre-re-selection table are not worked examples of the
  split: three of the four move only 1.6 to 5.7 percent of their coefficients to
  the larger base and gain nothing measurable. Searching over the split is the
  search-based flow's job; every set this tool emits is single-base.
- The digit size influences runtime and noise, and therefore the failure rate.
  The key-switching digit size `d_ks` influences keygen time, switching-key size
  and noise: a higher value gives faster keygen and smaller keys but more noise.

### Picking a winner

The sweep keeps every completed configuration and names one at the end:

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

The winner is the fastest configuration whose measured failure probability
beats the target. The `d_g 4` row shows why both halves matter: it is the
fastest of the four and still loses, because 2^-12 misses a 2^-40 target.
Digit counts that produced nothing are listed with the reason, so a set skipped
by `-x` pruning is not confused with one where no parameters exist. The `-x`
prune measures against this same winner rather than tracking its own notion of
"best", so the two cannot drift apart.

Each record also carries noise mean and stddev, key sizes and keygen time,
which is what a Pareto rule would need; picking on speed alone is a starting
point, not the last word. The search-based flow reports the whole front.

### `d_g` is a request, not an outcome

`B_g` is quantised to a power of two, so OpenFHE may span `Q` in fewer digits
than asked: at `logQ=28`, `-u 9` asks for 7, 8 and 9 and gets 7 every time.
Rows are labelled with the count OpenFHE will actually use (`8->7` when the two
differ), and a request that lands on a configuration already searched in this
sweep is skipped rather than measured again.

### The search is not deterministic

Measured noise steers the bisection on `n`, so two runs of one identical
search need not converge to the same place. Three draws of the same search
(STD128, GINX, `B_g=2^4`, deliberately noisy at `-i 20 -K 2`) landed on
n = 521, 526 and 540 with 2^-44.6, 2^-42.2 and 2^-40.4: a 4.2-bit spread,
wider than the gaps the sweep is being asked to rank. At the defaults
(`-i 200 -K 8`, 1600 samples against 40) the amplitude is smaller, but the
mechanism does not go away. Raise `-i` and `-K` before believing a small
difference between two candidates, and treat a single sweep's ranking between
close candidates as a coin flip. This is one of the reasons the search-based
flow lets a deterministic predictor choose the candidate and uses the library
only to confirm it.

## The validator

`binfhe_params_validator.py` measures the same way the selector does: `-i`
samples per key, pooled over `-K` independent keys (default 8), with the gate
time taken separately on the whole machine.

A named set:

```
sage -python scripts/paramsestimator/binfhe_params_validator.py -p STD128_4 -t 2 -I 4 -i 1000
```

Every named set OpenFHE ships:

```
sage -python scripts/paramsestimator/binfhe_params_validator.py -p ALL -i 1000
```

The `command args:` a selector run printed, without `-p`:

```
sage -python scripts/paramsestimator/binfhe_params_validator.py -n 483 -N 1024 -q 2048 -Q 27 -k 16384 -g 512 -r 32 -b 32 -s 3.19 -t 3 -d 0 -I 2 -i 1000
```

The failure probabilities in the comments beside each `BINFHE_PARAMSET` mean
two different things at the pinned commit. On the 105 rows the search-based flow
re-selected they are its own measured certifications: the per-key 0.9-quantile,
taken from the pessimistic end of its interval and rounded toward zero, which
is what `dse.py table finish --emit-labels` prints. On the four rows it never
touched (`TOY`, `TOY_MULTI_BASE`, `MEDIUM`, `SIGNED_MOD_TEST`), and on every
row of an older release, they predate multi-key measurement: treat those as
approximate, do not gate anything on reproducing them, and read a disagreement
as an output of this work rather than a defect in it.

## Security in the selector

`optimize_params_security` prices every candidate `(n, q_KS)` with the
lattice-estimator, answering from `dse_security_cache.json` when it can and
pricing into it when it cannot (a hit costs about 10 ms, a miss about 5 s), at
tolerance 0, so a repeat run at the same level spends no estimator time on
dimensions it has seen, and every dimension its bisection visits becomes a
point the search-based flow enumerates. The security model,
the standard-versus-conservative gap and the tolerance policy are in
[security.md](security.md). Every run prints the model in force next to its
results.

## As a control for the search-based flow

Because the selector measures everything it considers, a run of it is also a
set of out-of-sample checks on the noise model. A STD128 run at OpenFHE
`238153db` (GINX, ternary, target -64, 8 keys × 200 samples, digit counts 2 to
4) measured 49 candidates. Of the 32 inside the model's domain and below the
wrap ceiling, predicted over measured sigma has median 0.998 and range 0.944 to
1.029, and predicted over measured gate time runs 0.93 to 1.05. Its winner
(n=558, N=1024, q=1024, logQ 27, q_KS 2^15, base 2^7, b_KS 32; 17 238 µs,
2^-86.2) is what the search-based flow's model prices at 17 541 µs, and it is
the configuration the search's STD128 pick is compared against.
`scripts/dse-tools/score_legacy.py` scores any selector log this way.

That comparison is also what exposes the one structural gap a fixed grid has:
the selector's bisection lands on the smallest secure `n` at a given `q_KS`,
where a 32-step grid straddles it by up to 18 coefficients, about 2 percent of
gate time at STD128. The search prices those boundary dimensions into the cache
and enumerates them ([security.md](security.md#boundary-dimensions)).
