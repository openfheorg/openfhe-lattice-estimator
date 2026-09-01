# Verification

The enumerator ranks; `dse_verify.py` decides. It is the half of
predict-then-verify that does the verifying, and without it the pipeline stops
at a predicted frontier and nothing is ever confirmed. Three things it does
differently from "measure eight keys and compare sigma":

1. It certifies on a **per-key quantile**, not on pooled sigma.
2. It folds each key's **realised bias** into that key's statistic rather than
   averaging it away.
3. It **escalates the key count** until the decision is safe in both
   directions, with the quantile's own estimation error inside the stopping
   rule.

## Why a per-key quantile

A deployment runs one key. Pooling samples across keys estimates the *mean*
log2Pf, and a 2^-128 claim is about the tail of the per-key distribution, not
its centre. Two keys of the same parameters have different sigma, because the
switching key's fixed rows and the rounding offsets are drawn once per key;
measured, that scatter is 0.89 percent of sigma with a one-sigma upper bound of
1.38 ([noise-model.md](noise-model.md#per-key-scatter)), and it does not shrink
with more samples. Since `|d log2Pf| ~= 2 * |log2Pf| * dsigma/sigma`, the
0.9-quantile penalty against the mean key grows with the target. At the per-key
log2Pf scatter the budget assumes (2.5 percent, `PER_KEY_LOG2PF_SCATTER`) it is
`Z_90 * sensitivity * scatter`, about 6 percent of |log2Pf|:

| target | 0.9-quantile penalty |
|---|---|
| -64 | about 4 bits |
| -128 | about 8 bits |
| -192 | about 12 bits |

A set tuned so the *average* key hits 2^-128 delivers about 2^-120 for the worst
10 percent of keys. That is why a single pooled sigma flatters a deployment, and
why the same parameter set measured with one key reads 2^-164 and with eight
reads 2^-128.

The quantile is estimated parametrically, mean plus `z * spread`, rather than
as an order statistic: at K = 4 the 0.9 order statistic is just the worst of
four.

## The search must select on the same statistic

`certify` accepts on the 0.9-quantile key, so the enumerator selects on the same
statistic:

```
log2pf_certifiable = log2pf + m.quantile_penalty(sigma, inputs, q)
```

using the same `Z_90` and the same sensitivity `certify` uses -- asserted equal
by the tests, because the whole point is that the two halves agree. A margin is
`target - certifiable - band`. The penalty is added to the *statistic*, not to
the model band: it is a systematic offset certification always applies, where
the band is a two-sided uncertainty. Adding it to the band instead would make
`--model-band 0` wrong for a measured candidate
([the model band](#the-model-band)).

Selecting on the mean key instead is optimistic by the whole penalty, and the
size of that is not subtle. On the 97 certified cells of the 2026-09-08
parameter table:

| selection statistic | error against `certified` | cells where the prediction was optimistic |
|---|---|---|
| predicted, mean key | median **-4.69** bits | **92 of 97** |
| certifiable (mean + penalty) | median **+1.33** bits | 30 of 97 |

Selected on the mean key, seven of those cells certify 1.7 to 5.4 bits short of
target while every one has a predicted log2Pf that clears it, **all seven at
arity 3 or 4**, where the per-key offset is amplified 3x and 4x ([bias](#bias)).
That reads as a model failure and is nothing of the kind: the model predicts
those rows' sigma to within 3 percent. `--no-quantile-budget` selects on the
mean key, and a `table` manifest records which rule produced it
(`quantile_budget`).

`m.PER_KEY_LOG2PF_SCATTER` (2.5 percent) is the scatter the budget assumes, and
it is **not** `KEY_SCATTER_BOUND` (1.38 percent). That constant is the scatter of
*sigma*; what the quantile acts on is per-key *log2Pf*, which also carries each
key's realised offset, and essentially all of the resolvable per-key spread in
log2Pf is the offset rather than sigma variation. The value is the p90 of the
measured per-key spread, chosen on the p90 rather than the median because the
two errors are not symmetric: over-budgeting picks a slower set that certifies,
under-budgeting picks a faster set that fails and costs a full measurement to
discover.

## Bias

Sigma is taken about the sample mean, so a per-key offset is invisible to it,
and the offsets are real: the second modulus switch's rounding bias is
`(1/(2M)) * (1 - SUM_i s_i)`, measured exactly, and the switching key
contributes its own offset on top. `per_key_log2pf` takes each key's mean with
amplification `inputs`, because both gate inputs come from the same switching
key and the offsets add coherently rather than in quadrature.

## One key per process

`boolean_noise_estimate_script -K k` pools its noise stream, so per-key figures
are not recoverable from it. `plan` therefore emits `-K 1` once per key, and
each process seeds its own PRNG so the keys really are independent (verified by
comparing the streams of concurrent runs). `run-plan.sh` records one `record|`
line per key under the candidate's label, and with `JOBS=<n>` runs n of those
processes at once, one OpenMP thread each, which is where the measurement's
parallelism comes from ([measurement-practice.md](measurement-practice.md)).

## `certify`

Given K keys' `(sigma, mean)`, `certify` computes each key's log2Pf, the mean
and the observed spread across keys, and then **subtracts the sampling
variance** before calling the remainder scatter:

```
d(log2Pf) per key from sampling = 2 * |log2Pf| / sqrt(2 * S)      S = gates per key
true_var = observed_var - sampling_var
```

At 16 keys of 400 gates and |log2Pf| about 63.5 the sampling term is 4.49 bits
against an observed spread of 4.46: the whole spread is sampling, and without
the subtraction the candidate is rejected for the harness's sample count.
`--samples` is therefore required for a deconvolved estimate.

Where the subtraction leaves nothing, the data cannot see the scatter, and the
certified quantile **assumes it at the measured bound** (1.38 percent of sigma,
`KEY_SCATTER_BOUND`) rather than at zero. Assuming zero there would certify the
*mean* key as the 90th-percentile key, which is optimistic by the whole quantile
penalty -- about 2.3 bits at a 2^-64 target. The report says `scatter_assumed`
when the bound is in use; a resolved scatter is used as measured.

The uncertainty on the quantile has two parts: the mean's standard error,
`obs / sqrt(K)`, and the spread's own error, computed deliberately from the
*observed* variance so that when sampling dominates the band says so instead of
collapsing to zero.

## `decide`: four verdicts

```
pass           certified + uncertainty <= target     even the pessimistic end clears
fail           certified - uncertainty >  target     even the optimistic end misses
more keys      otherwise, and MEASUREMENT is what is blocking it
model-limited  otherwise, and the MODEL BAND is what is blocking it
```

The rule is two-sided on purpose. A one-sided "is the estimate past target"
rule is what accepts a 3-key reading that eight keys contradict: the precedent
is a candidate that read -118.7 at 3 keys and -110.9 at 8, and would have
shipped 17 bits short. Requiring the whole interval to land on one side is
what makes the escalation terminate honestly rather than at the first
favourable draw.

The last two verdicts are both "undecided", and they are split because they
call for opposite actions: more keys shrink the quantile's error and do nothing
at all to a model band. Collapsing them into one verdict tells you to spend
measurement on a candidate no measurement can decide.

## The model band

`--model-band` is for a candidate whose log2Pf is still a **prediction**: the
frontier's band, added in quadrature. A **measured** candidate's uncertainty is
the measurement's, and adding the model's prediction error on top double-counts.
It also makes the verdict unreachable: model bands run 2.6 to 3.8 bits where a
twelve-key measurement's uncertainty is 0.7 to 1.7, so the band would decide
every row and every row would come back model-limited. `dse.py decide` defaults
it to 0, which is right for a measured plan.

## Sizing a run

The per-key scatter and the sampling error shrink with different things:
`sqrt(scatter^2 / K + 1 / (2 * S * K))`. `resolving_keys(S)` says how many keys
are needed at S gates each to *resolve* the scatter (using the point estimate,
0.89 percent, because overstating the scatter asks for too few keys), and
`sizing_advice` prints what to change. At 1250 gates per key it takes about 75
keys to resolve; 12 keys still bound the pooled sigma and decide most rows, they
just cannot separate scatter from sampling, and the plan header says so. 1250
recurs because it is where the sampling error equals the scatter term. The
escalation schedule is 4, 8, 16, 32, 64 keys: each doubling buys a factor 1.41
in the band, and a finer schedule spends measurements on a band already
dominated by the model term past about 16 keys.

## Reading a report

For each label `decide` prints the per-key log2Pf values, the pooled sigma, the
deconvolved (or assumed) spread, the certified quantile with its uncertainty,
the verdict, and the sizing advice. The rows from the STD128 and STD192
adaptive test at OpenFHE `238153db` (12 keys × 1250 gates each):

| level | row | gate | keys | predicted | measured pooled | certified | verdict |
|---|---|---|---|---|---|---|---|
| STD128 | n=516, 2^7:516, bKS 128 | 16 218 µs | 565 MiB | -64.8 ± 2.6 | -66.3 | -62.35 ± 1.23 | fail |
| STD128 | n=516, 2^6:65 + 2^7:451, bKS 128 | 16 432 | 567 | -65.3 ± 2.7 | -64.2 | -60.44 ± 1.06 | fail |
| STD128 | n=516, 2^6:129 + 2^7:387, bKS 128 | 16 642 | 569 | -65.7 ± 2.7 | -66.9 | -63.00 ± 1.72 | more keys (~75) |
| STD128 | n=554, 2^7:277 + 2^9:277, bKS 256 | 16 505 | 1 153 | -68.5 ± 3.8 | -68.3 | -64.92 ± 0.74 | pass |
| STD128 | n=554, 2^7:347 + 2^9:207, bKS 32 | 16 735 | 254 | -71.2 ± 3.6 | -72.8 | -68.14 ± 1.88 | pass |
| STD192 | n=820, 2^7:615 + 2^10:205, bKS 256 | 42 941 | 3 425 | -65.1 ± 3.2 | -65.9 | -62.72 ± 1.00 | fail |
| STD192 | n=820, 2^7:718 + 2^10:102, bKS 32 | 43 577 | 763 | -66.3 ± 2.9 | -67.8 | -62.93 ± 1.18 | more keys (~75) |
| STD192 | n=820, 2^7:718 + 2^10:102, bKS 256 | 43 577 | 3 431 | -83.8 ± 3.8 | -82.6 | -78.28 ± 0.83 | pass |
| STD192 | n=820, 2^7:820, bKS 32 | 44 207 | 770 | -85.7 ± 3.5 | -88.5 | -83.89 ± 1.41 | pass |

Predicted sigma is within 1.7 percent of measured on all nine. Note how the
pooled log2Pf clears -64 on every row while the certified quantile fails four
of them: that gap is the quantile penalty, and it is the reason the pooled
figure is never what gets certified. The two `bKS 256` passes buy their margin
with 4.4x and 2.4x the key material of their `bKS 32` twins at zero gate cost,
which the frontier shows and the default policy does not weigh.

## Failures

Every measurement also carries the binary's own `Failures:` count, and a
candidate with any failure is wrong regardless of its sigma
([methodology.md](methodology.md#noise-alone-cannot-certify-a-gate)).

**`decide` and `table finish` read that count and it overrides the verdict.**
A cell with wrong answers reports `FAIL` whatever its margin, `decide` exits 2,
and `table finish` excludes it from the emitted rows AND labels and exits 1 --
a label is a claim about failure probability that ships in OpenFHE's enum
comments, and no noise distribution supports one for a gate that decrypted
incorrectly. A clean run says so explicitly (`correctness: 0 wrong answers over
12 measured key(s)`), because a silent verdict cannot be told apart from one
that never looked. Where a log carries no `FAILURES` field the report says
**correctness not checked** rather than treating absence as zero: the verdict
above it is then about sigma alone.

Every set measured so far carries a failure count of zero: the 45 sets of the table the
library shipped before the re-selection (2026-09-01, including the three with
`modKS = PRIME` where `q_KS := Q`, a path nothing else exercises) and the 848
runs of the 2026-09-08 parameter table.
