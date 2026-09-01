# The noise model

Closed-form noise, failure probability and key-size models live in
`scripts/paramsestimator/dse_model.py`. They are pure functions with no
OpenFHE and no SageMath dependency, so they can be developed, tested and
calibrated without a container. Nothing there measures anything;
`dse_sweeplog` supplies measured records and `dse_validate`, `dse_shipfit` and
`dse_lso` compare.

The point of the model is to let the search rank candidates without running
OpenFHE on each one, and then verify the few that survive. A model that is
wrong in a *correlated* way silently prunes good candidates, so every constant
is either derived from the implementation or calibrated on a designed
measurement and carried with an error band. None are guessed.

Conventions: a gadget map is `{base: coefficient_count}`, matching OpenFHE's
`gadgetBaseMap`; a single-base set is `{base: n}`. "@q" means a figure
expressed at the LWE ciphertext modulus `q`, where decryption sees it.

## The terms

A bootstrapped ciphertext's noise variance at `q` is the sum of four terms:

| term | form (variance at q) | status |
|---|---|---|
| accumulator | `k * N * SUM_j count_j * gadget_shape(Q, b_j) * (q/Q)^2` | one constant per method, calibrated: GINX 6.8, LMKCDEY 2.116 plus an automorphism term, AP 1.762 against its own product count |
| key switching | `N * sigma^2 * (q/q_KS)^2 * SUM_pos (1 - 1/r_pos)` | derived, no free constant |
| first modulus switch (Q to q_KS) | `Var(s)/12 * N * (q/q_KS)^2` | derived |
| second modulus switch (q_KS to q) | `Var(s)/12 * (1 - 1/M^2) * n`, `M = q_KS/q` | derived |

`Var(s)` is the secret's per-coefficient variance: 2/3 for uniform ternary,
3.19² for the Gaussian secret. That coefficient swings 15x between the two
distributions, which is why it is derived rather than fitted on ternary-only
data. `d_KS` is the key-switching digit count, `digits_for_base(q_KS, base_KS)`,
which mirrors OpenFHE's exact-integer `GetDigitCount` (verified equal over
1 328 modulus/base pairs). The key-switch sum runs over the `d_KS` digit
positions, each contributing `(1 - 1/r_pos)` of a Gaussian rather than one:
`r_pos` is the number of values that position can take (`base_KS`, or
`top_digit_extent` at the top), a digit of value zero selects no key row, and
the library stores no row for it. That factor is worth about 0.4 bits of log2Pf
across the table, 1.0 at `b_KS` 32 and under 0.1 at 256 and above, and it
follows the pinned library through `KSK_ZERO_ROWS_DROPPED`
([key sizes](#key-sizes-and-the-calibration-arms)).

### The accumulator shape

`gadget_shape` is read out of `SignedDigitDecompose`, not fitted:

```
gadget_shape(Q, b) = (d-2) * b^2 + min(b, Q / b^(d-1))^2
```

The decomposition starts its shift at `gBits`, so it emits digit positions
`1..d-1` and drops position 0, and the excess-H window confines the top
position to `Q / b^(d-1)` values. Over-coverage is not a correction; it *is*
the top digit's range. The accumulator carries an explicit factor of `N`;
measured, the dependence is N^0.985.

Evidence for the shape: seven designed cells give `k` in 6.62..6.99 (±2.7
percent) across bases 2^5, 2^7, 2^9, digit counts 3 to 5, logQ 21..27, N
512..2048, where the naive `(d-1) * b^2` form spans 3.36..6.99 (±35 percent) on
the same cells. Out of sample on the 8h45m sweep the median `k` is 6.87 / 6.83 /
6.67 at d = 3 / 4 / 5, flat. Sharpest test: base and digit count fixed, only
logQ moving, `k * shape` rises 2.08x where the naive form predicts no change and
this one says 1.97x.

LMKCDEY calls the same decomposition, so the shape carries over, but its
variance is affine in `n` rather than linear, with an intercept that falls as
1/numAutoKeys (the automorphism keys' own noise, which GINX has no analogue
of):

```
acc_var@Q = N * shape * (k_ext * n + k_auto / w)      k_ext = 2.116, k_auto = 929
```

Fitted on six designed points to ±4.5 percent. `k_ext` at 2.12 against GINX's
6.8 is the expected shape: GINX does two external products per coefficient
(an indicator pair over {-1, 0, +1}), LMKCDEY one. On the one shipped set where
the accumulator carries real weight, STD128_LMKCDEY at 40 percent of variance,
it predicts -0.5 percent where the GINX constant read +33 percent. The
accumulator is independent of the secret distribution (`k` 5.03 Gaussian
against 5.02 ternary on the same cell), so the 15x distribution swing is
confined to the rounding terms.

The 1/w benefit saturates by w of about 20. Driving `numAutoKeys` from 10 to
its legal maximum `n - 1` is worth at most 5.5 bits of log2Pf across the twenty
LMKCDEY sets of the pre-re-selection table, under 0.1 bits for the STD192
family. If a configuration misses its target, the automorphism count cannot
rescue it; look at `q_KS` or the gadget base. Gate time falls with `w` too, so
the search fixes `w` at the largest value the cost model prices instead of
enumerating it ([methodology.md](methodology.md#a-dimension-that-improves-two-axes-at-once-is-not-a-search-dimension)).

AP calls the same decomposition again, so the shape carries a third time. What
differs is how many external products a gate does. Its schedule walks each
coefficient's base-`baseR` digits and **skips a zero digit**, so the count is
not the digit count:

```
acc_var@Q = k_AP * N * products * SUM_j count_j * gadget_shape(Q, b_j)
products  = SUM over digit positions of P(digit != 0)          k_AP = 1.762
```

That probability is `1/baseR` for every position except the highest, which is
truncated because `a < q` leaves it fewer than `baseR` values. Counting the top
position exactly matters: the approximation `digitsR * (1 - 1/baseR)` predicts
the base 2 against base 4 variance ratio as 1.222 where measurement says 1.290
and the exact count says 1.294, and it inverts the ordering of bases 64 and 128.

The consequence for a search is that a coarser refresh base is better on **both**
noise and gate time -- both follow the product count -- and costs only key
material, since the refresh key holds `(baseR - 1) * digitsR` keys per
coefficient ([key sizes](#key-sizes-and-the-calibration-arms)). That is a clean
two-axis trade rather than a tuning parameter.

### Alignment bias

A digit position whose range the modulus does not fill re-uses one fixed key
row, which contributes a per-key **offset** rather than noise. Each key's
realised mean is folded into that key's log2Pf with amplification `inputs`,
because both gate inputs come from the same switching key and the offsets add
coherently. The second modulus switch's mean is `(1/(2M)) * (1 - SUM_i s_i)`,
computable from the key. A candidate is clean iff `base_KS^d_KS == q_KS` and
`baseG^digitsG == 2^logQ`; the frontier flags the others `ks-misaligned` and
`g-misaligned`, and the model applies the bias rather than omitting it.

## Derived versus calibrated

The three post-accumulator terms are derived, not fitted, because a sweep
occupies a narrow region of the space and a constant fitted there encodes the
region as physics:

- the second-switch coefficient is `Var(s)/12`, which swings 15.3x between a
  ternary and a Gaussian secret; a ternary-only sweep fits it as one number;
- the finite-grid correction `(1 - 1/M^2)` is 1.6 percent at M = 8 and 25
  percent at M = 2, so a sweep held at large M cannot see it;
- the first-switch term is 0.22 at M = 16 and 14.22 at M = 2, and is invisible
  in a gate measurement because `(q/q_KS)^2` scales it away, so a fit returns
  zero for it.

Each is measured in isolation
(`boolean_keyswitch_isolate`, parsed by `dse_isolate.py`): feed the
deterministic tail `(N,Q) -> (N,q_KS) -> (n,q_KS) -> (n,q)` a ciphertext whose
error is exactly zero and read each stage on its own. None has a free
constant, so this is a test, not a calibration. Results: key switching within
0.7 percent (within-key sigma within 0.33 percent of `(1 - 1/base_KS)` of the
total, the remaining `1/base_KS` appearing as the per-key offset the model
predicts), both modulus switches within 0.5 percent, both secret distributions,
over twelve sets and 800 000 samples.

The rule: never fit a free constant to a shape that has not been read out of
the implementation. A constant fitted to the wrong shape absorbs the structural
error and looks plausible.

## The band

Fractional one-sigma bands on each variance term (`TERM_VAR_BAND`):

| term | band | basis |
|---|---|---|
| ks | 0.02 | isolated measurement within 0.7 percent; 2 percent is 3x the worst case, kept as headroom for per-key offset structure K = 32 could not resolve |
| round | 0.02 | isolated measurement within 0.52 percent, both distributions |
| acc | 0.06 | seven designed cells agree on `k` to ±2.7 percent; the sweep's per-record scatter over [6.16, 7.77] is sampling at 200 × 8, not model error |

The log2Pf uncertainty of one candidate is propagated through its own variance
shares:

```
|d log2Pf| ~= 2 * |log2Pf| * dsigma/sigma
dsigma/sigma ~= sqrt( SUM_i (0.5 * share_i * band_i)^2  +  sampling^2 )
```

This cannot be a scalar: the same accumulator band is ±1.3 bits at a 5 percent
share and ±26 bits at 100 percent, at -128.

## Failure probability

`log2_pf(sigma, inputs, q, bias)` is the log2 probability that a decryption of
a gate with `inputs` inputs at ciphertext modulus `q` lands outside its
window, given the key's realised offset. It is monotone in arity (3-input
worse than 2, 4 worse than 3), and the verifier certifies it per key
([verification.md](verification.md)).

A **failed** measurement reports a sigma of `q / (p * sqrt(12))`, the standard
deviation of a uniform wrap (147.8 at q = 2048 for a two-input gate),
independent of `n`. `measurement_saturated` marks a reading at 0.9 of that
ceiling as "this number IS the ceiling"; `measurement_usable_for_fit` requires
0.5, because two points whose sigma is identical by construction read 26
percent apart at 0.72 and 0.57 of the ceiling. A ceiling-pinned record read as
data looks like a digit-count anomaly, which is why both marks exist.

## Domain limits

The model refuses to predict where it has not been measured
(`within_model_domain`, `within_calibrated_envelope`, `accumulator_identified`):

- **N 512..2048 and logQ 21..54.** Measured; the N dependence is linear.
  Outside the ranges the model refuses.
- **Key-switch saturation** (`KS_SATURATION_LIMIT = 0.015`): where key
  switching's per-coefficient variance passes a fraction of `q`, the shape
  stops describing reality. The guard is conservative: in the legacy control
  five of six refused rows were predicted within 1 to 2 percent anyway.
- **At least two emitted gadget digits.** d = 1 has no emitted digit. d = 2 is
  in domain: the 45 d = 2 records below half the ceiling predict to a median of
  -0.1 percent.
- **AP's refresh base must be supplied.** Its product count, and so its noise,
  its gate time and its key size, all follow from that base, so the model raises
  rather than defaulting it: a default would price a different refresh key from
  the one the candidate carries, over a range where the term moves 2.8x.
- **GINX with a Gaussian secret** computes the wrong function
  ([methodology.md](methodology.md#the-eleven-gates)).

## Validation

Every number here is a comparison of predicted against freshly measured sigma.
Noise has been measured unchanged across every OpenFHE pin move (paired A/Bs
at each move, within sampling; 20 of 20 rows FAILURES=0 at `41709fbc`), so
records from different pins combine.

| set of records | result |
|---|---|
| 8h45m target -64 sweep, 284 in-domain GINX records (pin `ec57c4b4`) | 274 within ±5 percent, median -0.1 percent, no residual structure in N, logQ, q, arity or word |
| 26 shipped sets measured 2026-09-01, GINX and LMKCDEY, classical and quantum, two-base maps included | median error 1.07 percent, worst 3.14 percent |
| legacy selector run at `238153db`, 32 in-domain candidates | predicted/measured median 0.998, range 0.944..1.029 |
| adaptive test at `238153db`, 9 two-base and single-base rows, 12 keys × 1250 gates | all within 1.7 percent |
| leave-region-out map over 381 records (2026-09-06) | median -0.1 percent, p10/p90 -2.2/+2.5, held-out equals in-sample on every axis to 0.5 percent |
| AP's designed refresh-base arm, 11 cells (2026-09-07) | median 1.002, range 0.996 to 1.021 over a 2.8x span in the external-product count |
| AP's one shipped set, out of sample against the arm's constant | +0.63 percent |
| Gaussian-secret arm, 12 cells with ternary twins (2026-09-07) | median 0.988, range 0.976 to 1.010; the Gaussian-to-ternary variance ratio predicted to -0.2/+4.0 percent |
| parameter-table measurement, the first 48 searched picks over 6 levels x 3 methods x 3 arities, 8 keys x 1250 gates each (2026-09-08) | median 1.010, range 0.970 to 1.060; the 24 LMKCDEY rows median 1.009 and the four Gaussian picks among the best at +0.9 to +3.0 percent |
| the four re-selected sets the correctness gate measures as the library ships them at `41709fbc` (STD128, STD128_LMKCDEY, STD128_AP, STD256Q; 28 800 gates each) | median error 0.9 percent, worst 1.7; these are the live half of the known-answer control |

The validation is measurement-limited: a sigma from K keys of S gates carries
`sqrt(1/(2KS) + scatter^2/K)`, about 1.66 percent for the shipped-set runs
against an observed 1.85, so the model's own error is under about 1 percent.
Demonstrating 0.5 percent would need 32 keys at 1250 gates per set.

The tools: `dse.py validate LOG` (known-answer control first),
`dse_shipfit.py` (all shipped sets against the model, reading the table from
OpenFHE's source), `dse_lso.py LOG...` (the region map).

## Per-key scatter

Two keys of the same parameters have different sigma: the switching key's
fixed rows and the rounding offsets are drawn once per key. Measured
2026-09-01 over twelve candidate-runs (156 degrees of freedom), deconvolved in
sigma space where a stddev's sampling error is exactly `1/sqrt(2S)`: pooled
point estimate **0.89 percent**, one-sigma upper bound **1.38 percent**.
Per-candidate structure is not resolvable, so it ships as one constant, under
two names because the two uses want opposite conservatism: `KEY_SCATTER_BOUND =
0.0138` widens a decision band (overstating withholds a pass, which is safe),
`KEY_SCATTER_POINT = 0.0089` sizes a run (overstating asks for too few keys,
which is not). The per-key spread of *log2Pf* is a third quantity, larger
because it carries each key's realised offset; the selection budget uses its
p90, `PER_KEY_LOG2PF_SCATTER = 0.025`
([verification.md](verification.md#the-search-must-select-on-the-same-statistic)).

## Key sizes and the calibration arms

`ksk_bytes` and `btkey_bytes` are closed forms: the switching key within 1.2
percent and the bootstrapping key within 1.7 percent of the serialized sizes
over the pre-re-selection table, and the two together match the measured peak
resident memory of 105 re-selected sets to a median ratio of 1.009. The
bootstrapping key uses `digitsG - 1` rows per coefficient, verified against the
source and a shipped calibration point, with 8 ciphertexts per coefficient for
GINX and 4 for LMKCDEY; AP's refresh key holds `(baseR - 1) * digitsR` RGSW keys
per coefficient. Each key is priced at its own word width
([image-and-pins.md](image-and-pins.md#one-library-and-the-hybrid-default)).

The layout follows the pinned library, through three flags in `dse_model.py`
that move with the pin. `ksk_rows` sums over the stored digit positions, each
holding its extent in rows, less one where no row exists for digit value zero:

| flag | effect on the switching key | at `12858277` (on since `94229558`) |
|---|---|---|
| `KSK_TOP_COMPACT` | the top position holds `top = floor((q_KS - 1) / base_KS^(d_KS - 1)) + 1` rows, not `base_KS` (128 of 256 at `q_KS` 2^15 base 256, so -25 percent; a full position on an aligned pair, so unchanged) | on |
| `KSK_ZERO_ROWS_DROPPED` | no row for digit value zero, so a further `1/base_KS`, **and** the `(1 - 1/r)` noise factor above | on |
| `RK_TOP_COMPACT` | the same top-position compaction for DM's refresh key, `(digitsR - 1) * (baseR - 1) + (top - 1)` keys per coefficient (30 to 44 percent of it on 27 of the 35 AP rows) | on |

Only the middle one changes noise, which is why the flags flip with the pin and
not before: a measurement taken at an earlier commit carries the zero rows'
noise and reads about 0.4 bits pessimistic. Every manifest records the layout it
was priced under (`key_layout`).

**The approximate key-switching decomposition.** With `droppedDigitsKS = delta`
the key switch rounds each coefficient to the nearest multiple of
`base_KS^delta` before decomposing, so the low `delta` positions are neither
stored nor switched: the key loses `delta * (base_KS - 1)` rows per slot and the
switch does `d_KS - delta` row additions. What replaces them is the discarded
remainder, uniform on `[-base_KS^delta/2, base_KS^delta/2)`, left in the phase
against the ring secret:

```
ks_round_var@q = N * (base_KS^(2*delta) - 1)/12 * Var(z) * (q/q_KS)^2
```

Derived, with no free constant, and **unmeasured**: nothing in the harness
isolates it. It is also large. Against the position it removes it costs a factor
of 5.8 at `base_KS` 32, 22.7 at 64 and 359 at 256, so it is a small-base knob at
best, and `dse_enumerate` holds `delta` at 0 unless asked
([tooling.md](tooling.md#measurement-runners-and-arms)). A candidate with
`delta > 0` is priced and shown but withheld from selection until the term is
measured.

`dse_calibrate.py plan` and `ladder` design the measurement cells the model
cannot get from a sweep. A sweep drives `n` down and `q_KS` up, exactly where
the accumulator vanishes (under 5 percent of variance in 251 of 284 sweep
records), and at fixed Q the gadget base determines the digit count, so base
and digits are perfectly collinear. The fix is to choose `logQ = log2(base) *
digits`, so the two are set independently and every cell is exactly aligned; a
separate misaligned arm holds base and digits fixed and moves only Q, so the
bias term is measured by difference. Those configurations identify noise
physics and are not deployable; most sit far below any security level. The
measured data is checked in as `dse_measured.py`, because regenerating it costs
about two hours of machine time and container logs do not survive.
