# Security

Two LWE instances have to meet the level, and every tool here checks both:

```
LWE    log2(q_KS) <= boundary(n)      the switching key encrypts the secret at q_KS, not at q
RLWE   logQ       <= boundary(N)      N = cyclOrder / 2
```

The boundary is the largest integer `log2(modulus)` at which the
lattice-estimator still certifies the level for that dimension and secret
distribution. Security falls monotonically in the modulus at fixed dimension,
which is what makes the boundary well defined and lets a bisection find it in
about six estimator calls.

## Tables versus the estimator

`scripts/paramsestimator/paramstable.py` transcribes OpenFHE's own
`StandardLatticeParmSets` (from `src/core/lib/lattice/stdlatticeparms.cpp`,
encoding the HomomorphicEncryption.org standard) for the uniform, error and
ternary distributions at each level, classical and quantum. `max_logq`
interpolates between the tabulated dimensions, so it is exact at every
dimension OpenFHE tabulates. Those tables hold power-of-two ring dimensions
from 1024 up; 14 of the 20 quantum shipped sets have an LWE dimension below
1024 that the table cannot index, and the fractional "table" values below
1024 are this repo's linear extrapolation off the n = 1024 row.

The tables are a starting point only. Measured against the estimator at the
table's own boundary moduli (2026-09-01, estimator `53da5982`, model
`standard`):

| dim | level | dist | table logq | estimator bits | target | gap |
|---|---|---|---|---|---|---|
| 556 | STD128 | ternary | 14.66 | 137 | 128 | +9 |
| 601 | STD128 | ternary | 15.85 | 138 | 128 | +10 |
| 821 | STD192 | ternary | 15.23 | 191 | 192 | -1 |
| 1024 | STD192 | ternary | 19.00 | 188 | 192 | -4 |
| 1299 | STD256 | ternary | 18.03 | 258 | 256 | +2 |
| 716 | STD192 | error | 14.68 | 208 | 192 | +16 |

The disagreement runs in both directions. Conservative entries cost the search
candidates it could have used, and 9 to 10 bits of `q_KS` is not marginal
when key switching is the dominant noise term at the 128 family. The STD192
n = 1024 entry is optimistic by 4 bits: the table permits a modulus the
estimator says is short of the level. Both directions are reasons not to prune
on the table alone. `dse.py search --security table` still exists for a fast
first pass; `--security estimator` is the one to trust.

`generate_std_tables.py` recomputes a table cell from the estimator and is the
tool for a table update: run for (ternary, STD128) it reproduces OpenFHE's own
rows exactly at both N = 1024 (27) and N = 2048 (54) under `standard`, which is
what shows the shipped tables were derived in the same pre-hybrid, pre-MATZOV
era. It is interactive-only.

## Which attacks are priced

`ESTIMATOR_SECURITY_MODEL` selects the cost model and the deny list, and every
run prints the model it used next to its results:

| value | reduction cost model | denied attacks | STD128 keyswitch pair (n=558, q_KS=2^15, ternary) |
|---|---|---|---|
| `standard` (default) | `BDGL16` | the hybrid families, `bkw`, `arora-gb` | **128 bits**, about 1 s |
| `conservative` | `MATZOV` | `bkw`, `arora-gb` only | **124 bits**, about 35 s |

`standard` reproduces the era OpenFHE's shipped tables were derived in, so
results stay comparable with them; that is the only reason it is the default,
and it is a policy decision left to the user rather than a silent code change.
`conservative` admits `dual_hybrid`, which at the STD128 pair is the strongest
attack of all and 4 bits below what `standard` reports. If you are choosing
parameters to deploy rather than to compare against the shipped table, use it:

```
ESTIMATOR_SECURITY_MODEL=conservative docker compose run --rm estimator \
    sage -python scripts/paramsestimator/binfhe_params.py -t 2 -p STD128
```

The gap is **level-dependent**; do not carry one level's number to another. A
trailing `Q` selects the quantum path (`LaaMosPol14`), and both models use the
same quantum cost model, differing only in the deny list, so the quantum gap is
a different quantity. Measured at the shipped keyswitch pairs (ternary,
2026-08-31):

| set | classical: standard to conservative | quantum: standard to conservative |
|---|---|---|
| STD128Q (n=601, q_KS=2^15) | 138 to 134 (4 bits) | 127 to 127 (0) |
| STD192Q (n=890, q_KS=2^15) | 208 to 197 (11 bits) | 191 to 187 (4) |

Both shipped `*Q` pairs price about a bit below their nominal level even under
`standard` (127 against 128, 191 against 192). STD256 under `conservative` is
not yet measured; measure before deciding, since the gap grew from 4 to 11 bits
between STD128 and STD192.

The estimator call takes its minimum over every attack the model prices, not
over a fixed list: with the hybrids admitted, a fixed list of three reports 128
bits at the STD128 pair where the true minimum is 126.

## Tolerance

A strict `bits >= target` test on the estimator's integer output rejects most
of what OpenFHE ships, by exactly one bit. Measured at the shipped parameters,
`standard`, classical:

```
STD128   n=556  q_KS=2^15   127 against 128      STD128   n=559  128
STD128_3 n=595  q_KS=2^16   127 against 128      STD128_4 n=635  128
STD192   n=821  q_KS=2^15   191 against 192      STD192_LMKCDEY  192
STD192_3 n=876  q_KS=2^16   191 against 192      STD256   n=1299 258
STD256   N=2048 logQ=29     255 against 256      STD256_3 n=1241 261
```

Over the 41 sets of the table the library shipped before the re-selection
(`fixtures/binfhecontext-238153db.cpp`), checked on both LWE instances: 10 clear
both at tolerance 0, 29 are one bit short on the LWE side, 6 (every STD256
classical) one bit short on the RLWE side, none short by more than a bit, none
over-provisioned. The RLWE modulus equals the certified boundary exactly in 35
of 41. That is what parameters pinned to a boundary that has since moved about
a bit look like. The re-selected table is searched at tolerance 1 and certifies
there by construction.

So tolerance is a policy input, not a constant. `DEFAULT_TOLERANCE_BITS` is 0:
whether 127 bits satisfies "STD128" is not a question a module should answer
silently. The searches take `--tolerance 1` to admit exactly what ships and
still reject anything genuinely weaker; `dse_beats` uses the minimum tolerance
at which the shipped set itself certifies (2 for STD256Q, which delivers 254
bits; 1 elsewhere), because holding a candidate to a stricter bar than the
incumbent is not a comparison. The legacy selector certifies at tolerance 0, as
it always did. Tolerance is part of the cache key: a boundary at one tolerance
is not the boundary at another.

## The cache

`scripts/paramsestimator/dse_security_cache.json` is read by the enumerator
with no Sage dependency, and written by `dse_security.py` under Sage. For each
key `(level, secret_dist, model, quantum, tolerance)` it stores, per dimension,
the largest integer `log2(modulus)` the estimator certifies; this collapses a
two-dimensional lookup to one number per dimension, and the enumerator's
question "is `(dim, q)` secure?" becomes `log2(q) <= boundary`. It is priced
**once per distinct instance**, not per candidate: the grid has hundreds of
millions of candidates but only a few hundred distinct `(dimension, level,
distribution)` triples.

As of 2026-09-06 it holds 2 037 priced dimensions over 26 keys (six levels,
two distributions, the tolerances in use, classical and quantum) plus 169
boundary dimensions on 13 of those keys, all against lattice-estimator
`53da5982`, whose sha is recorded in the file and printed by `doctor`.

The cache is keyed by the estimator revision and the security model, **not** by
the OpenFHE pin: an OpenFHE repin leaves it valid, a lattice-estimator repin
does not, and a lookup under a different model misses rather than silently
answering.

```
sage -python scripts/paramsestimator/dse_security.py price --level STD128 [--dist ternary] [--quantum] [--dims 32:2048:32] [--tolerance 1] [--threads 4]
sage -python scripts/paramsestimator/dse_security.py boundary --level STD128 --tolerance 1 [--logqks 12:24]
python3      scripts/paramsestimator/dse_security.py coverage [--level STD128]
sage -python scripts/paramsestimator/dse_security.py compare --level STD192
```

`price` fills a dimension grid (default every 32 from 32 to 2048), saving
incrementally. `coverage` lists which dimensions are priced per key, so a gap
is visible rather than inferred. `compare` puts the certified boundary next to
the closed-form table.

## Boundary dimensions

A fixed-step grid in `n` straddles the cheapest secure dimension by
construction. At STD128 and `q_KS = 2^15` the boundary is n = 554 at tolerance
1 (558 at tolerance 0, where the legacy selector's bisection lands), where a
32-step grid offers 544 (insecure there, so a grid-only search falls back to
2^14 and worse noise) or 576 (18 wasted coefficients, about 2 percent of the
gate). The legacy selector, run as a control, is what exposes the gap
([legacy-flow.md](legacy-flow.md#as-a-control-for-the-search-based-flow)).

`dse_security.py boundary` bisects `n` at each `q_KS = 2^k` on the search grid
(k 12..24) with single estimator calls (about 1 s each, about five per
boundary), prices the boundary dimension fully, and records it under
`boundaries[curve][k]`. About 20 minutes of Sage per level, once per estimator
revision. The search then enumerates every priced off-grid `n` for its level
(boundaries and the shipped sets' own dimensions) alongside the 32-step grid,
via `dse_security.extra_dims`. What the boundary dimensions are worth on the
frontier: the STD192 single-base row at n = 820 runs 44 207 µs against 45 443
at the grid's n = 832 (-2.7 percent), and the STD128 pick at n = 554 runs
16 505 against 17 518 at n = 576 (-5.8 percent).

## Shared with the legacy selector

`binfhe_params_helper.optimize_params_security` maps its bit count onto a
cache curve, reads the certified boundary at tolerance 0, and on a miss prices
that one dimension into the cache (about 5 s). Every dimension the selector's
bisection visits therefore becomes a point the search enumerates, and a
dimension priced once by either tool is free to both afterwards. The cache on a
remote machine gains entries from any run there; copy it back before syncing
over it.

## What each shipped set delivers

For the table the library shipped before the re-selection: all 20 quantum sets
at their exact `(n, q_KS)`, quantum, `standard`: 4 at or
above nominal, 13 exactly one bit below (127 / 191 / 255), 2 two bits below
(STD256Q and STD256Q_LMKCDEY at n = 1242, delivering 254), and one
over-provisioned by 8 (STD128Q_LMKCDEY delivers 136 at n = 640 where STD128Q
reaches 127 at n = 601 with the same `q_KS`). The named parameter sets are
hardcoded in OpenFHE and never security-checked there: `FindRingDim` is
consulted only in the custom-logQ constructor and only for the ring dimension.
