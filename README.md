OpenFHE Lattice Estimator
=========================

A parameter generation tool for DM/FHEW, CGGI/TFHE and LMKCDEY. Give it a
design (bootstrapping method, failure probability, security level) and it
returns optimized parameter sets (lattice dimension, ciphertext modulus, digit
sizes) with the bootstrapping runtime of each, predicted from calibrated cells
and then measured for the sets that matter. Security is priced with
the [Lattice Estimator](https://github.com/malb/lattice-estimator); noise,
failure probability and gate cost are measured against a real OpenFHE build.

## Quick start

Everything is in the image: SageMath, numpy and scipy, the lattice-estimator,
and an OpenFHE built with `WITH_NOISE_DEBUG=ON`. Nothing needs installing on
the host but Docker.

```
MAKE_JOBS=8 docker compose build          # ~2 min of compile, plus a 3 GB pull the first time
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/dse.py doctor
```

`MAKE_JOBS` defaults to 4, and the binding limit is memory, not cores: budget
about 2 GB per job. Run every script with `sage -python`, never `python3`.

Then pick a flow:

```
# guided: answer a few questions, then it runs the whole search-based flow for one set
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/dse_wizard.py

# search: rank the whole space in closed form, then measure the few that matter
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/dse.py search --level STD128 --target -64 \
        --security estimator --tolerance 1

# select: bisect the lattice dimension on measured noise, one digit count at a time
docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 2 -p STD128 -f -64 -i 200 -K 8
```

Full cold start, including the recalibration step most people skip:
**[docs/getting-started.md](docs/getting-started.md)**.

## Two flows

**Predict, then verify** (`dse.py`) enumerates the reachable parameter space,
prunes it in closed form, ranks the survivors on a Pareto front over gate time,
key material and failure margin, and hands you a measurement plan for the
handful whose verdict a measurement would actually change. A hundred million
configurations are scored in minutes; the library runs only on the few that
survive. It searches per-coefficient gadget maps, both accumulator word sizes,
and the certified security boundary in the lattice dimension.
[docs/dse-flow.md](docs/dse-flow.md)

**The parameter selector** (`binfhe_params.py`) measures every candidate it
considers: for each gadget digit count it bisects the lattice dimension on
measured noise until the failure target is met, then names the fastest
configuration that met it, with a `table entry:` line pastable into OpenFHE.
Slower, single-base, and independent of the models, which is what makes it a
useful control on them. [docs/legacy-flow.md](docs/legacy-flow.md)

Both share the security cache, the measurement harness and the noise model.

## Before trusting a number

- **Only measurement is evidence.** Everything a search prints before its plan
  is run is a prediction, and every prediction carries a band.
- **Gate times are per machine and per build.** The shipped cost cells describe
  one 8-core i7-9700 at the pinned OpenFHE, under one thread count and one
  OpenMP runtime, and each regime's table names the commit its cells were timed
  on. On your hardware they describe someone else's CPU: recalibrate before
  believing a ranking ([docs/cost-model.md](docs/cost-model.md)). Cells never
  extrapolate across methods, so recalibrating the one method you search is a
  complete answer for that method -- and the default; a ranking of one method
  against another needs all of them measured on your box. Noise and security are
  arithmetic and carry over.
- **The models refuse rather than extrapolate.** A candidate outside the
  measured domain, or with no cost cell, or whose security was never priced, is
  reported as unrankable and counted, not scored on a guess.
- **A low noise figure is not correctness.** Every measurement records the
  binary's own failure count alongside sigma, because a gate can return a
  clean, low-noise encryption of the wrong bit.
- **The failure probabilities in OpenFHE's parameter-set comments are this
  tool's output, on 105 of the 109 rows.** At the pinned commit the labels in
  `BINFHE_PARAMSET_LIST` come from a measured, per-key certification, rounded
  toward zero from its pessimistic end, so reproducing one is a check and not a
  coincidence. The four rows this work never searched (`TOY`,
  `TOY_MULTI_BASE`, `MEDIUM`, `SIGNED_MOD_TEST`) keep their earlier labels, as
  does any older OpenFHE release: those predate multi-key measurement, and a
  disagreement with them is an output of this work.

## The pin

The image builds one pinned OpenFHE revision,
`128582771b50ce798bfa36d63e43b550b1b17ea0`, the head of openfhe-development's
`dev` branch. It appears in `Dockerfile` and twice in `docker-compose.yml`, and
all three move together.
At this pin the library's own parameter table is the re-selected table this
tool produced, so the tools that read "the shipped sets" read this tool's output;
the table the library shipped before it is kept in
`scripts/paramsestimator/fixtures/binfhecontext-238153db.cpp`.

```
OPENFHE_REF=<full 40-char sha> MAKE_JOBS=8 docker compose build
```

Do not fall back to a release tag: `v1.5.1` and earlier lack the
digit-decomposition carry fix these noise measurements depend on, the
per-dimension gadget bases, and the `BTKeyGen` fix multi-key measurement needs.
What each pin moved, and what to re-measure after a bump, is in
[docs/image-and-pins.md](docs/image-and-pins.md).

## Documentation

| page | contents |
|---|---|
| [getting-started.md](docs/getting-started.md) | cold start in eight phases, with commands and outputs; editing; native install |
| [dse-flow.md](docs/dse-flow.md) | search, plan, measure, decide, validate; reading a frontier |
| [legacy-flow.md](docs/legacy-flow.md) | the selector and the validator, and what their output means |
| [methodology.md](docs/methodology.md) | predict-then-verify, the eleven gates, and the rules the design follows |
| [noise-model.md](docs/noise-model.md) | the noise terms, what is derived and what is calibrated, bands, domain, validation |
| [cost-model.md](docs/cost-model.md) | the gate-time form, cells, regimes, and how to recalibrate |
| [security.md](docs/security.md) | tables against the estimator, security models, tolerance, the cache, boundary dimensions |
| [verification.md](docs/verification.md) | per-key certification, the four verdicts, sizing a run |
| [measurement-practice.md](docs/measurement-practice.md) | how to take a measurement that means something |
| [image-and-pins.md](docs/image-and-pins.md) | the image, the pin, one library, the 32-bit hybrid |
| [tooling.md](docs/tooling.md) | every script and module, one line each |
| [glossary.md](docs/glossary.md) | the symbols and the jargon |

## Tests

```
sage -python tests/test_dse.py   # inside the container
python3 tests/test_dse.py        # on a host that has numpy and scipy
```

Prints `all checks passed`. Use `sage -python` in the container: the suite
reaches `dse_model`, which imports SciPy, and the container's bare `python3`
has none.
