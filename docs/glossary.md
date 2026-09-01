# Glossary

Parameter names follow OpenFHE's, so a symbol here is the one in
`BinFHEContextParams` and in the harness flags.

**aligned / misaligned.** A decomposition is aligned when its top digit
position is full: `base_KS^d_KS == q_KS` for key switching, `baseG^digitsG ==
2^logQ` for the gadget. A misaligned position re-uses one fixed key row, which
contributes a per-key **offset** rather than noise; the model applies that bias
and the frontier flags the rows `ks-misaligned` and `g-misaligned`. Neither
flag is a warning.

**band.** The one-sigma model uncertainty on a candidate's predicted log2Pf,
propagated through that candidate's own variance shares, so it differs per row.
It belongs to a prediction; once a candidate is measured, its uncertainty is
the measurement's. See [noise-model.md](noise-model.md#the-band).

**b_KS, d_KS, q_KS.** The key-switching base, digit count and modulus. The
switching key encrypts the LWE secret at `q_KS`, which is the modulus the LWE
security instance is checked at, not `q`.

**baseG, digitsG, gadget map.** The gadget base and the number of digits it
spans `Q` in, `digits_for_base(logQ, baseG)`. A gadget map is
`{base: coefficient_count}` summing to `n`, OpenFHE's `gadgetBaseMap`; a
single-base set is `{base: n}`. `-G 128:277,512:277` on the command line.

**boundary dimension.** The smallest LWE dimension the estimator certifies at
a given level and `q_KS`, found by bisection and stored in the security cache.
A fixed-step grid in `n` straddles it by construction, which is worth about 2
percent of the gate at STD128. See [security.md](security.md#boundary-dimensions).

**cell.** One `(method, N, word_size)` of the cost model, holding the
coefficients `(c0, c1, c2, c3)`. Twelve per regime for GINX and LMKCDEY, plus
six for AP where its arm has run. A candidate whose cell is absent is refused,
not interpolated.

**certifiable.** A candidate's predicted log2Pf plus the 0.9-quantile penalty
`certify` will apply: the statistic the search selects on, so that selection
and certification judge the same thing. See
[verification.md](verification.md#the-search-must-select-on-the-same-statistic).

**cyclOrder.** OpenFHE's parameter-table column, equal to **2N**. Reading it as
the ring dimension doubles every N; every shipped set has `q` in {N, 2N}, which
is the check that catches the mistake.

**decision distance.** How many of its own bands a candidate's predicted
margin sits from the target. `plan --order decision` measures the smallest
first: those are the rows a measurement can decide and the model cannot.

**droppedDigitsKS (`delta`).** OpenFHE's approximate key-switching
decomposition: the number of low digit positions the key switch rounds away
instead of switching. Zero everywhere by default. It shrinks the switching key
and adds a rounding term against the ring secret that is large at every base in
use, so the search prices it but does not select it. See
[noise-model.md](noise-model.md#key-sizes-and-the-calibration-arms).

**digitsG2.** `2 * (digitsG - 1)`, the number of RGSW rows the evaluation key
is allocated at, and the width of the accumulator's parallel region. Base 2^5
at logQ 27 gives 10, not 12.

**gate_work.** `SUM_j count_j * digitsG2_j`, the accumulator work per gate in
RGSW row-operations, and the feature `c2` multiplies. The cost fitter derives
it by calling the same function the predictor uses.

**key cap (level).** The key-material size up to which a table pick ignores key
size, per method: 4 GiB for GINX and LMKCDEY, 8 GiB for AP by default. Beyond
it a row is scored `gate * (1 + lambda * (key/cap - 1))`, `lambda` 2 by default.
See [methodology.md](methodology.md#key-material-is-free-up-to-a-cap-and-priced-beyond-it).

**LPF_\*.** Shipped sets generated against a lower failure-probability target.
Not a separate kind of set: `-p STD128 -f -128` produces one. The four in the
library carry two-base gadget maps but are not worked examples
([legacy-flow.md](legacy-flow.md#reading-the-output)).

**log2Pf.** The base-2 logarithm of the probability that one gate decrypts
incorrectly, for a given arity and ciphertext modulus. Always negative; a
*worse* failure probability is the less negative number.

**margin.** `target - log2Pf - band`: the bits a candidate clears its target by
after its own model uncertainty is charged. Negative means the model cannot
promise it. **Spendable** margin is margin large enough to pay for a discrete
step (a coarser gadget base for the whole map or for one grid split, a
key-switch tier); a row whose margin cannot buy any step is flagged
`margin-unspendable`.

**method.** The bootstrapping algorithm: AP (`-t 1`, also DM/FHEW), GINX
(`-t 2`, also CGGI/TFHE), LMKCDEY (`-t 3`). GINX requires a ternary secret. AP
takes a refresh base `baseR` that sets its external-product count, and with it
its noise, gate time and key size, so the base is a required input.

**min-of-N.** The statistic for a thread sweep or a runtime comparison, because
at saturating widths one gate in eight runs slow and the mean carries that
skew while the floor does not.

**model-limited.** A verdict: undecided, and the *model band* is what blocks
it, so more keys cannot help. Distinct from **more keys**, where the
measurement is what blocks it. See [verification.md](verification.md#decide-four-verdicts).

**numAutoKeys (`w`).** LMKCDEY's automorphism key count. Legal range 1 to
`n - 1`; `w == n` is an out-of-bounds write. Accumulator variance and gate time
both fall as `1/w` and saturate by about 20 to 40, so the search fixes it at the
largest value the cost model prices rather than enumerating it.

**pin.** The OpenFHE commit the image builds, named in three places that move
together. Every cost number carries the pin it was measured on; noise and
security do not depend on it. See [image-and-pins.md](image-and-pins.md).

**q, Q, logQ.** `q` is the LWE ciphertext modulus, where decryption sees the
noise; `Q = 2^logQ` is the ring modulus the accumulator works at. OpenFHE
derives `Q` as `LastPrime(logQ, 2N)`, which guarantees exactly `logQ` bits or
throws.

**regime.** A `(thread mode, OpenMP runtime)` pair: `multi|single` and
`libomp|libgomp` (`clang`/`gcc` accepted as aliases). Cost cells are keyed by
it, because the runtime reorders builds rather than scaling them. An unmeasured
regime holds no cells and refuses every candidate.

**saturated.** Two different things. A **measurement** is saturated when its
sigma sits at the wrap ceiling `q / (p * sqrt(12))`, so the number carries no
information. A **candidate** is key-switch saturated when one noise source
drowns the others and the model's shape stops describing it; that is a domain
guard, not a measurement property.

**refinement (two-base).** How a two-base gadget map enters the frontier: from
each single-base frontier point, the adjacent coarser base is tried and the
coarse-base count bisected to the largest that still meets the target
(`refine_maps`, `boundary_split`). Seconds per table; `--multi-base` sweeps the
maps in the grid instead.

**scatter (per-key).** The spread of sigma across independent keys of the same
parameters: 0.89 percent measured, 1.38 percent as a one-sigma upper bound
(`KEY_SCATTER_BOUND`). It does not shrink with more samples per key, only with
more keys. The per-key spread of *log2Pf*, which also carries each key's
realised offset, is budgeted at 2.5 percent (`PER_KEY_LOG2PF_SCATTER`).

**tolerance.** Bits below nominal that a security level may certify at.
Default 0; 1 admits exactly what OpenFHE ships. Part of every security cache
key. See [security.md](security.md#tolerance).

**variance shares.** How a candidate's predicted noise variance divides
between accumulator, key switching and rounding. They set the band and say
which knob is worth turning: two thirds of STD128's variance is key switching.

**width share (`map_width_share`).** The fraction of `c2` a two-base LMKCDEY map
pays per digit its coarse-base indices lack, because every accumulator region
runs at the widest base's team width: 0.40 in the multi-thread libomp regime,
0 at one thread. See [cost-model.md](cost-model.md#the-form).

**w32, w64, `w32*`.** The accumulator word size. `w32*` in a ranking means the
candidate reaches the 32-bit path by *fitting* (Q at most 2^28 and every base
narrow enough) on an ordinary 64-bit build, where the shipped set it is
compared against does not fit and stays 64-bit. No flag, no rebuild, since
OpenFHE `9e8045db`. The key figure on such a row is resident memory:
serialization widens the key back to 64 bits.
