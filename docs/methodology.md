# Methodology: predict, then verify

The parameter space of a BinFHE bootstrapping scheme has about a dozen dials:
ring dimension, ring modulus, ciphertext modulus, LWE dimension, key-switching
modulus and base, gadget base (or a per-coefficient map of bases), automorphism
key count, method, secret distribution, gate arity, word size. Measuring a
single configuration takes minutes to hours. Measuring them all would take
centuries, and the parameter selector's approach, bisecting on measured noise
one digit count at a time, is both slow and steered by the noise it measures
([legacy-flow.md](legacy-flow.md#the-search-is-not-deterministic)).

The search-based flow instead **predicts** in closed form what every
configuration will do, keep only the few that could possibly be the answer,
and use the library only to **verify** those. Six stages, and only one needs
OpenFHE:

1. **Enumerate** the grid lazily; nothing is stored.
2. **Gate** every point through eleven correctness and security checks.
3. **Predict** noise, failure probability, its uncertainty, and gate cost for
   each survivor.
4. **Rank** them on a Pareto front over gate time, key material and margin.
5. **Measure** the handful that the model cannot decide or that the policy
   picks. This is the only stage that produces evidence.
6. **Decide** on the measurement alone, and score the model against it.

Stage 5 is minutes to hours per candidate; stages 1 to 4 run at thousands of
candidates per second. The design rules below all serve one idea: **a search
that quietly answers when it does not know is worse than one that stops and
says so.**

## The eleven gates

Every candidate meets the same gates in the same order, and the first one it
fails ends it. The order is a design decision: cheap tests before expensive
ones, and the two that can crash the library near the front, before anything
spends effort on a candidate that would take the process down. Each gate
exists because something real went wrong without it; the reason is recorded
next to the check in `dse_constraints.py` and `dse_enumerate.prune`.

1. **method / keyDist.** GINX stores the LWE secret as an indicator pair over
   {-1, 0, +1}; hand it a Gaussian secret and 64 percent of the coefficients
   are lost, so the gate produces clean, low-noise encryptions of the wrong
   bit. Measured: 202 failures in 400 gates with sigma only 25 percent above
   the ternary configuration's. Nothing in OpenFHE rejects the pairing.
2. **autokeys >= n.** LMKCDEY's automorphism keys live in a vector of length
   `n` indexed up to `numAutoKeys`, so `numAutoKeys == n` writes one past the
   end: a segfault, measured exactly at the allocation bound. Early because a
   candidate that reaches measurement with this takes the whole run down.
3. **gadget word width.** Digits of a decomposed value must fit in a machine
   word with one bit spare: `digitsG * gBits + 1 <= 32` or `<= 64`.
4. **hybrid cannot narrow.** A 32-bit candidate that the library's own
   eligibility rule for the 32-bit key forms would refuse is not merely
   mispriced, it is unreachable on a default build. Inside `logQ <= 28` the
   rule reduces to the method check plus gate 3, so this gate is defensive
   rather than discriminating.
5. **carry precondition.** `Q/2 + H < baseG^digitsG`, with `H` the
   signed-decomposition bias. It needs the real modulus, not a power-of-two
   stand-in, because the margin is often a single bit; when the real value is
   unavailable the check is skipped rather than run on a number that would
   answer wrongly.
6. **keyswitch saturated.** Past a point one noise source drowns the others
   and the model's shape stops describing reality; the model over-predicted by
   up to 38x there before this guard.
7. **outside measured (N, logQ).** The noise model is validated over
   N 512..2048 and logQ 21..54. Outside that it would be extrapolating.
8. **accumulator unidentified.** Fewer than two emitted gadget digits leaves
   no accumulator term to evaluate.
9. **security.** The expensive one, so it runs last among the correctness
   gates: certified boundary from the cache, both LWE instances (`q_KS` at
   dimension `n`, `Q` at dimension `N`). A dimension that was never priced is
   refused, not assumed fine ([security.md](security.md)).
10. **predicted saturated.** Predicted noise so large that the decryption
    window closes; the gate would be unreliable regardless of speed.
11. **cost uncalibrated.** No timing constants for this method, ring size and
    word size in the active regime. The model returns nothing rather than
    interpolating between things it did measure. One deliberate exception: a
    term the measurement can bound but not resolve (LMKCDEY's automorphism cost
    at small rings, under 170 µs a gate) is priced as zero with that bound
    recorded, since refusing it would drop four fifths of a method's candidates
    for a cost the data says is not there.

Gates 7, 8, 9 and 11 all reject for the same underlying reason: **we do not
know.**

Counted on one run (a GINX search against the shipped STD192 set, word size
held at 64, on a grid narrower than the current one at two edges):

| stage | alive |
|---|---|
| every grid point, enumerated lazily | 99 516 912 |
| survived the eleven gates | 13 732 740 |
| could be scored (cells exist for this shape) | 10 696 544 |
| beat the shipped set on correctness (both bands applied) | 5 007 608 |
| beat the shipped set on speed | 5 415 |
| beat it on both | 132 |

The last two rows are not a chain: correctness and speed are separate tests on
the same 10.7 million, and the winners are the overlap. Five million settings
are more correct than the shipped set and only five thousand are faster,
because the easy way to be correct is to be slow. Improvements have to come
from the slice that is both, and that slice is 0.0001 percent of the grid. The
current grid (key-switching modulus down to 2^12, base up to 512) enumerates
about twice as many points with the same proportions. A count from a different
level, method or word size is a different search, not a correction to this one.

## Refuse, don't extrapolate

Where the model lacks measured constants it returns `None`, and the candidate
is counted under a named refusal reason rather than scored. A search that
silently ranks 8 candidates is obviously incomplete; one that ranks 8 million
on invented numbers looks exactly like success. Every refusal is counted and
attributed, so a search that prunes everything says why.

The reason: a cost model that keeps predicting across a library change it was
not measured on reorders the frontier silently, because parallelism does not
arrive evenly across methods.

A derived term is not the same as a measured one, and the difference decides
whether a candidate may be *picked* rather than whether it may be priced. The
approximate key-switching decomposition is the case: its rounding term follows
from the construction with no free constant, so a candidate using it can be
priced and shown, but nothing in the harness has ever isolated that term, so
such a row is withheld from selection until something does. Pricing it is
honest; picking on it would be trusting arithmetic nobody has checked against
the library.

The rule cuts both ways. A term that is missing from the formulas is a coverage
gap, not a reason to refuse a method forever: AP's refresh-key base drives its
noise, its key size and its gate time, and once its effect is read out of the
accumulator's schedule and calibrated on one designed measurement, AP is ranked
like anything else, with the base a required input.

The corollary is that the model is **not fitted to the shipped sets**. Its
constants are derived from the implementation or measured on designed
calibration cells far from any deployable configuration; the shipped sets and
the 8h45m sweep are out-of-sample checks ([noise-model.md](noise-model.md)).
When a target search at STD128 finds nothing much faster than the shipped set,
that is because two thirds of STD128's noise is key switching and `n` is pinned
by security, not because the model has memorised the incumbent. The check is
the legacy selector run as an independent control: its 32 measured candidates
score against the model at a median of 0.998.

## Compare both sides

Both the candidate's score and the baseline's are predictions, and both carry
error. So a candidate must clear the baseline's **pessimistic** end, not its
midpoint:

```
cand_log2pf + cand_band <= ship_log2pf - ship_band
```

The reason: a one-sided test accepts a STD256 candidate predicted at -64.2 ± 2.8
against a shipped point prediction of -59.6, and measured the pair comes out
-61.2 against -61.7, the candidate half a bit worse than the set it is supposed
to beat. The rule lives in one function, `beats_at_no_worse_pf`, so that every
tool applies the same one.

## Control against a known answer first

Every tool that parses measurements checks itself against values already known
to be right before reporting anything new. The control is `dse_measured.CONTROL`:
four sets with their parameters and measured sigma recorded, checked two ways
against the live library table (the parser reads the same parameters for that
name; the model predicts the measured sigma within 6 percent), plus five
earlier sets checked on the model alone. If the control fails it says so and
stops.

The reason: a parser that reads one column of OpenFHE's parameter table as N
where it holds 2N is wrong by exactly a factor of two everywhere downstream,
which shows up as a uniform +30 percent model error on every validated set and
reads as a finding that every shipped ring is twice too large. A model wrong by a
uniform ratio across independent configurations is a units error, not a physics
error, and a known answer is what separates the two.

## One rule, one place

A rule with two copies has two behaviours eventually. The two-sided comparison,
the 32-bit eligibility predicate (`hybrid_ns32_ok`, transcribed from the
library's `Fits`), the digit-count function and the gate-work feature all live
in exactly one function that everything calls. The cost fitter derives its
features by calling the same `gate_work` the predictor evaluates, so a refit
cannot drift from the model it feeds.

## The build is part of the answer

Timing constants are only valid for the OpenFHE commit, the thread count and
the OpenMP runtime they were measured under, so all three are part of the
model's key rather than a footnote. A regime nobody measured holds no cells,
and every search on it refuses. Every ranking prints the pin it was priced on;
a cross-method comparison additionally prints the pending upstream changes
that would move it ([cost-model.md](cost-model.md#regimes)).

The reason: parallelism does not arrive evenly across methods. One commit that
parallelised the CGGI accumulator and gave LMKCDEY only map caching improved
GINX's cost coefficient 2.58x geomean against LMKCDEY's 0.95x, and candidates
that won by 1.12x on the earlier cells lose by 0.55x on the later ones. Both
measurements are correct; the build is different.

## Noise alone cannot certify a gate

Every measurement records the binary's own count of wrong answers alongside
the noise figure (`run-plan.sh`, `FAILURES=`). A low noise reading is not
evidence of correctness: the GINX-with-Gaussian defect above sits at 202 wrong
gates in 400 with a normal-looking sigma, and a failure rate computed from sigma
alone cannot see it.

## Margin against the next step, not against zero

Failure margin is only worth something where a discrete step exists to spend
it: a coarser gadget base for the whole map, a coarser base for one grid-split
of the coefficients (a two-base map is exactly a partial step), a key-switching
digit tier. A candidate with 37 bits of surplus and nowhere to put it is not
better than one with 3; scoring against zero penalises exactly the candidates
that have run out of places to spend. `next_step_cost` prices the cheapest
available step in bits and the frontier flags a row `margin-unspendable` when
its remaining margin cannot pay for one.

What margin is worth, priced by the model: at STD128 the
shipped set's 66 bits of surplus buy under 1 percent of gate time at equal key
material, because key switching dominates the noise and `n` is pinned by
security; at STD256Q, 11 bits buy 2 percent at equal keys or 4.7 percent if the
switching key may grow 2.3x. The large STD192 and STD256 gains (about 2x) are
the 32-bit path, not margin.

## A frontier, not a winner

Speed and key material pull against each other, so the search reports the
non-dominated set over (gate time, key material, margin) and a replaceable
policy names one row. Key material varied 264 MiB to 2283 MiB across winners
in one sweep, a deployment-defining spread that "fastest that beats the
target" never surfaces. The front is built incrementally (`front_insert`),
because a batch form holds every survivor at once and one STD128 search keeps
56 million of them.

## Margin must exceed uncertainty, per candidate

A 1.7-bit margin at ±0.8 bits of model uncertainty is not a pass. The
uncertainty is computed per candidate, because it depends on how that
candidate divides its variance between terms with different bands: the same
accumulator band is ±1.3 bits at a 5 percent share and ±26 bits at 100
percent. Feeding the shares in also tells the verifier where to spend its
budget ([noise-model.md](noise-model.md#the-band)).

Uncertainty is not the only thing the margin has to clear. Selection and
certification score the **same statistic**: the verifier accepts on the
0.9-quantile key, so the search selects on the certifiable log2Pf, the mean-key
prediction plus the quantile penalty. Selecting on the mean key is optimistic by
that penalty: on the 97 certified cells of the 2026-09-08 table, by a median 4.7
bits on 92 of them, putting seven short of target, every one at arity 3 or 4
([verification.md](verification.md#the-search-must-select-on-the-same-statistic)).

## A dimension that improves two axes at once is not a search dimension

`numAutoKeys` is not a search dimension. Both of its effects move the same way:
noise falls as `K_AUTO / w` and gate time falls as `c3 * N / w`, with `c3`
non-negative in all four cost regimes. The only thing it costs is key material.
So its optimum is always the largest priced value, which is a closed form rather
than something to enumerate (`max_priced_autokeys`); a five-value sweep
multiplies LMKCDEY's enumeration by five and returns the ceiling in every cell
(the 2026-09-08 table: 40 in all 106). LMKCDEY is 52.7 percent of a full table
search, so leaving it out of the grid is worth 1.73x on the whole table.

The direction does not reverse with thread count, which is the obvious worry.
`c3` is *larger* single-threaded (103.76 against 51.68 at N = 2048, 64-bit,
libomp) because the automorphism work is partly hidden by parallelism when
threaded and paid in full when serial. Where a cell prices it at zero the term
measured below the 125 microsecond quantum; `dse_gatefit` refuses a wrong-sign
majority outright instead of fitting it, so zero means "invisible", never
"negative" ([cost-model.md](cost-model.md#regimes)).

The measured curve, on the most autokeys-sensitive pick in that table
(`STD256Q_3_LMKCDEY`, accumulator at 63 percent of variance):

| w | log2Pf | gate us | key MiB |
|---|---|---|---|
| 2 | -64.5 | 125 587 | 165 |
| 10 | -69.5 | 106 253 | 165 |
| 40 | -70.6 | 102 629 | 167 |
| 512 | -70.9 | 101 515 | 197 |

Saturating by w = 40. `--autokeys-grid` restores the sweep.

## Refine the frontier, don't enumerate the refinement

The gadget map is the opposite case: a genuine trade, and therefore a genuine
frontier axis. But it is a *straight* trade -- noise variance rising and gate
work and key size falling linearly in the split count -- so there is no interior
optimum to discover, only a segment between two single-base endpoints. Measured:
of 252 admissible two-base maps at fixed everything-else, **zero** were both
faster and better on log2Pf, in each of two cells checked. OpenFHE's own
two-base sets behave the same way; `LPF_STD128`'s `{128:547, 512:9}` gives up
4.7 bits against plain `{128:556}` to save 0.2 percent of gate time.

A dimension with no interior optimum does not need a grid sweep, and the sweep
is not cheap: maps in the grid multiply the gadget-map space by 28.2x, turning a
5-hour table search into 146 hours. So the map is reached as a **local move from
each frontier point** instead, in seconds.

From *each* point, not from the chosen pick. Refining one configuration cannot
discover that a split makes a different `(n, logQ, baseKS)` win, and the front is
hundreds of points rather than billions, so refining all of it is still free.

This is not a cost-saving that gives something up. Checked against the
exhaustive sweep on four cells, staging returned the **identical pick** every
time, 25 to 33 times faster -- including two cells whose winner is itself a
two-base map, and one, `STD128` at arity 2, where the winner
(`{128:347, 512:207}`, 16 735 us) is the configuration an independent 12-key
measurement had already certified at -68.14
([verification.md](verification.md#reading-a-report)). It also matters for
correctness, not just speed: of the three cells pinned at STD256Q's ring-security
ceiling, where no 64-bit modulus is available at all, a two-base split is the
only lever that reaches the target for `STD256Q_4_LMKCDEY` -- at +2.6 percent
gate time and no extra key material, where a larger `baseKS` cannot get there at
any price.

Two results from Hong and Lee (ePrint 2025/1892), who solve the same problem
as a knapsack, shape the move. Their Theorems 4.1 and 4.2 prove the optimal
heterogeneous map has exactly two bases and that they are *adjacent*: cost
grows linearly in the digit count while noise falls geometrically, so the
cost-per-noise ratio is smallest between neighbours. Every winning map seen here
obeys it, including OpenFHE's shipped `{128:547, 512:9}`. One reading matters:
their "d and d+1" assumes every digit length is available, and with
power-of-two bases the achievable counts at logQ 26 are 26, 13, 9, 7, 6, 5, 4,
3, 2 -- so bases 2, 4 and 8 have no d+1 neighbour, and a literal reading refined
nothing there and lost a cell. `adjacent_pairs` therefore means neighbours in
the undominated list, which is what the proof uses. Their Theorem 4.3 proves
relax-and-round is exactly optimal, so `boundary_split` bisects the coarse count
to the admissibility boundary instead of sampling it at seven points: worth a
median 0.64 percent of gate time over the ladder across 105 cells, up to 21.8
in one, faster in 104 and slower in none.

Because the refinement works on a *frontier* rather than a grid, a table whose
selection rule or cost cells have changed does not need a new search. `dse.py
table repick --dir T --out-dir T2` re-prices every stored frontier row under
the current model, refines it, and picks: 108 cells in about two minutes
against three hours for the search, and on the tables checked so far it agrees
with a fresh search on 103 to 105 of 105 cells. Its caveat is recorded in the
manifest as `repicked_from`: a stored front is the non-dominated set under the
prices of the search that produced it, so a row that a cost change would have
promoted onto the front is not there to be found, and a fresh `plan` remains
the ground truth.

`--multi-base` restores the exhaustive grid sweep, which is how the equivalence
above was checked; `--no-refine` turns the refinement off entirely, and
`--no-boundary` keeps the ladder without the bisection, which is the A/B that
measured the theorem's contribution.

## Key material is free up to a cap, and priced beyond it

A gate-first rule with key size as a tiebreak takes any trade of key material
for speed or margin, however lopsided: on the frontiers of the 2026-09-08 table
it spends a median 1.09x the pre-re-selection row's key material and up to 7.2x
for a median gate change of zero. Whether that is the right rule is a
deployment question, and the answer the table uses is this: **up to a certain
level key size does not matter, because the keys are manageable; past it, the
key-size/gate-time trade-off is what needs scrutiny.**

The level is absolute and **per method**: `--key-cap-gib 4,AP=8` by default, 4
GiB for GINX and LMKCDEY and 8 GiB for AP, because AP's keys run 5 to 10x
GINX's at the same gate time and one level for all three either starves AP or
frees GINX. The levels sit where the trade changes character: at any cap from 2
to 8 GiB the GINX and LMKCDEY picks move by -0.3 to -1.1 percent of gate time,
while AP pays +72 / +44 / +28 percent at the median at 2 / 4 / 8 GiB, so AP is
the only method the cap constrains and 8 GiB is where its penalty stops being
the dominant term.

Every admissible row is scored `gate_us * (1 + lambda * max(0, key / cap - 1))`:
a row within its level scores its own gate time, so among themselves the
under-cap rows rank exactly as `default_policy` ranks them, and a row beyond it
wins only when its speed advantage beats `lambda` times its excess -- exceeding
the cap by a factor of two costs the same as running `lambda` times slower.
`--key-cap-lambda` is that exchange rate, 0 being gate-first everywhere and
`inf` never exceeding the level while a row fits, with **2.0 the default**. The
pick is marked `over_cap` when it exceeds the level and the manifest records
the cap, rate and rule, so a table says which rows exceeded the level rather
than hiding it in a weight.

The score is soft, over every row, rather than two-stage (the fastest row that
fits, and the score only when nothing fits), because a two-stage rule never
looks past a fitting row and so puts a cliff at the level: three STD256 and
STD256Q AP cells take 5.5 to 7.8 GiB rows at 558 to 587 ms while a 9 GiB row
runs 312 ms. Under the soft score at `lambda` 2 those three take the 9 GiB rows
(they would up to `lambda` of 6 to 18) along with six AP cells that gain 24 to
28 percent for keys of 8.1 to 8.9 GiB; `lambda` 1 moves fifteen cells and 0.5
twenty-four, so 2 removes the cliff without re-opening the trade the level was
set to close. `--key-cap-hard` selects the two-stage rule for comparison.

`--key-cap-mult M` is the relative form: `M` times the key the library shipped
for that set before the re-selection (`fixtures/binfhecontext-238153db.cpp`),
resolved through a chain (same name, then the GINX row of the same level and
arity, then without `LPF_`). It exists for comparison against that table, not
as a policy: AP's keys run 5 to 10x GINX's at the same gate time, so
referencing an AP cell to a GINX row forces it onto rows 88 to 240 percent
slower. A manageability threshold is method-agnostic; a ratio to another
method's row is not.

## Directed sampling

Two questions an adaptive step could answer, and only one has anything to bite
on.

**Where is the model weak?** `dse_lso.py` refits the one fitted noise constant
on every region of the design space but one, applies it to the held-out
region, and compares with the in-sample error. Over 381 usable measured records
the model sits at a median of -0.1 percent with p10/p90 of -2.2 and +2.5
percent, and the held-out error equals the in-sample error on every axis to
within 0.5 percent. Refitting where residuals are large has nothing to bite on.

**Where is a verdict in doubt?** `dse.py plan --order decision` sorts frontier
rows by how many of their own bands their predicted margin sits from the
target, and `--model-limited` keeps only rows under one band: the ones a
measurement can decide and the model cannot. That loop converges: predict,
order by decision distance, measure the undecided rows, decide on the
measurement alone. It is what produced the STD128 and STD192 rows in
[dse-flow.md](dse-flow.md#step-5-decide).

## What the search cannot do

Two kinds of limit, and the difference is the whole point of this section. A
**structural** limit is something the design cannot express. A **coverage** gap
is something nobody has measured yet, and every one of those names the command
that closes it -- `search` and `doctor` both print the remedy rather than
leaving you to map a refusal onto an arm.

Coverage gaps, which you can close:

- **Cost cells for a regime, method or ring dimension nobody has timed.** The
  shipped cells describe one machine at 8 threads, so a ranking for a 36-core
  deployment wants cells measured there; the arm takes `THREADS=<n>` and, since
  it also takes `METHODS` and `NS`, you can measure only the cells you lack.
  Until then those candidates are refused rather than guessed at.
- **Security for a curve or dimension nobody has priced.** A dimension outside
  the cache is refused, not assumed safe. `dse_security price` and `boundary`
  fill it, once per estimator revision.
- **The noise model's calibrated region**, currently ring dimensions 512 to 2048
  and moduli of 21 to 54 bits. Outside it the model refuses. Widening it means
  designed noise cells (`dse_calibrate`), not a cost measurement.

Structural limits, which no measurement changes:

- **Nothing it prints before the measurement is evidence.** The whole ranking is
  a prediction, and the four verdicts exist because that distinction is the
  point.
- **The security figures are a model, not an attack.** They are the lattice
  estimator's, under a chosen cost model and deny list, and the choice is worth
  4 to 11 bits depending on the level.
- **The policy optimises one axis at a time.** It reports the whole front for
  that reason, and prints an equal-speed alternative with better failure
  probability when one exists, but choosing among the front is yours.
