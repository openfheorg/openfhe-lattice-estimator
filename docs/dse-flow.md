# The predict-then-verify flow (`dse.py`)

`binfhe_params.py` ([legacy-flow.md](legacy-flow.md)) answers "given a design,
what parameters?" by construction: it bisects on measured noise, one
configuration per gadget digit count. The `dse_*` modules answer a different
question by search: over the whole reachable parameter space, which candidates
meet a failure target, and which is cheapest? Then they hand you a measurement
plan to check the answer against the library rather than trusting the model.
The reasoning behind that split is in [methodology.md](methodology.md).

`dse.py` is the entry point. The individual modules each have their own
`--help` and are usable alone, but `dse.py` is how they compose. For one
parameter set end to end, `dse_wizard.py` asks the questions and runs the
pipeline below in order, printing each command as it goes
([getting-started.md](getting-started.md#the-guided-flow-phases-5-to-7-from-a-few-questions)).

## The pipeline

| step | command | needs the image? | produces |
|---|---|---|---|
| 1 | `dse.py doctor` | no | whether this checkout is usable |
| 2 | `dse.py search --level L --target T --save picks.json` | no | ranked candidates |
| 3 | `dse.py plan --picks picks.json --out plan.cmds` | no | a measurement plan |
| 4 | `bash scripts/run-plan.sh plan.cmds > plan.log` | **yes** | **the measurement** |
| 5 | `dse.py decide plan.log --target T -q Q` | no | pass / fail / more-keys / model-limited |
| 6 | `dse.py validate plan.log` | no | model against measurement |

Only step 4 produces evidence. Everything before it is a prediction and
everything after it is bookkeeping on a measurement, which is worth keeping in
mind when reading any number the search prints.

The round trip is deliberately not one command. Step 4 takes minutes to hours
per candidate, wants an idle machine if you also care about timing, and is
often run on a different box from the search. A single wrapper would hide both
the step that costs real time and the step that can silently produce numbers
from the wrong build.

Steps 2, 3, 5 and 6 are pure Python and run anywhere SciPy is available. Inside
the image use `sage -python`, because the container's bare `python3` has no
SciPy ([getting-started.md](getting-started.md#use-sage--python-never-python3)).

## Worked example

Every command below assumes the dev-compose alias from getting-started, so that
files written in one step survive to the next. It is a **host** alias, and each
line below is a host command that runs one thing in a container and exits:

```
alias est='docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm'
```

At a container prompt, drop the `est estimator` prefix and run the rest by
itself ([getting-started.md](getting-started.md#phase-4-first-run-and-where-your-files-live)).

```
# 1. is the checkout usable at all?
est estimator sage -python scripts/paramsestimator/dse.py doctor

# 2. search. --security estimator uses the priced lattice-estimator boundaries in
#    dse_security_cache.json; --security table uses paramstable's interpolated
#    rows, which are faster and disagree with the estimator by -4 to +16 bits.
est estimator sage -python scripts/paramsestimator/dse.py search \
    --level STD128 --target -64 --security estimator --tolerance 1 \
    --save picks.json

# 3. emit a plan. One key per process on purpose: `-K k` pools its noise stream,
#    so per-key figures are not recoverable from a single pooled run.
est estimator sage -python scripts/paramsestimator/dse.py plan \
    --picks picks.json --keys 12 --samples 1250 --out plan.cmds

# 4. measure. run-plan.sh records the binary's own Failures: count as well as
#    sigma -- see the warning in its header for why that is not optional.
est estimator env JOBS=8 bash scripts/run-plan.sh plan.cmds > plan.log

# 5. decide. The verdict rests on the MEASUREMENT: the per-key 90th-percentile
#    log2Pf, with the per-key scatter assumed at its measured bound when the run
#    cannot resolve it. Leave --model-band at 0 for a measured plan.
est estimator sage -python scripts/paramsestimator/dse.py decide plan.log \
    --target -64 -q 2048 --samples 1250

# 6. score the model against what was measured, after a known-answer control
est estimator sage -python scripts/paramsestimator/dse.py validate plan.log
```

## Step 2: `search`

```
dse search --target T [--level STD128] [--method GINX|AP|LMKCDEY]
           [--key-dist UNIFORM_TERNARY|GAUSSIAN] [--word-size 32|64] [--inputs 2|3|4]
           [--multi-base] [--security table|estimator] [--tolerance BITS]
           [--limit N] [--explain] [--save FILE]
```

`search` enumerates the grid lazily (ring dimension, ring modulus, ciphertext
modulus, key-switching modulus and base, LWE dimension, gadget base, secret
distribution and word size), passes every point through the eleven gates
described in [methodology.md](methodology.md#the-eleven-gates), predicts noise,
failure probability, uncertainty and gate cost for the survivors, streams a
Pareto front over (gate time, key material, margin), and then refines every
frontier point with the two-base gadget maps adjacent to it
([methodology.md](methodology.md#refine-the-frontier-dont-enumerate-the-refinement)).
LMKCDEY's automorphism key count is fixed at the largest value the cost model
prices, since it improves noise and gate time together. The front is held
incrementally because a batch front over every survivor does not fit in memory
(56 million on one STD128 search).

- `--security estimator` reads certified boundaries from the cache; a
  candidate whose dimension is unpriced is refused, not assumed fine.
  `--tolerance 1` admits a level that certifies one bit below nominal, which
  is what the shipped sets themselves need ([security.md](security.md#tolerance)).
- `--multi-base` sweeps two-base gadget maps in the grid itself instead of
  refining the frontier: 28x the enumeration for the same picks on every cell
  checked, kept for that check. A two-base map is how a candidate spends part
  of its margin: the coarse-base fraction is a partial step toward the next
  digit tier. `table plan` additionally takes `--no-refine`, `--no-boundary`
  and `--autokeys-grid`.
- `--word-size` pins the accumulator width. Left unset, the search offers both
  and holds 32-bit candidates to the library's own eligibility rule for the
  32-bit key forms.
- `--explain` prints a line per pruned candidate with the gate that refused it.
- `--save` writes the whole front, plus the policy pick, for `plan`.

An illustrative run at STD128 (a `--multi-base` target search at -64, with the
boundary dimensions priced into the cache, on the cells of an earlier pin):

```
grid: 359759900 candidates enumerated
pruned in closed form, by reason:
  keyswitch saturated          56909350
  predicted saturated          20782031
  security (estimator)         132922300
  security (unpriced)          33720240
  below target                 29482428
  SURVIVED                     85943551

predicted Pareto frontier over (gate time, key material, margin): 525 of 85943551
  gate us   keyMiB N     n      q     logQ  logqKS bKS  gadget    log2Pf       band  margin  flags
      16218    565  1024    516  2048    27     14  128 2^7:516       -64.8     2.6    -1.8  g-misaligned margin-unspendable
      16432    567  1024    516  2048    27     14  128 2^6:65,2^7:451     -65.3     2.7    -1.4  g-misaligned margin-unspendable
      16505   1153  1024    554  2048    27     15  256 2^7:277,2^9:277     -68.5     3.8     0.7  ks-misaligned g-misaligned margin-unspendable
      ...
      16735    254  1024    554  2048    27     15   32 2^7:347,2^9:207     -71.2     3.6     3.6  g-misaligned margin-unspendable
      ...
      17415    260  1024    554  2048    27     15   32 2^7:554      -131.1     5.3    61.8  g-misaligned

policy (fastest with margin exceeding its own uncertainty, ties on key size):
  N=1024 n=554 q=2048 logQ=27 q_KS=2^15 baseKS=256 gadget=2^7:277,2^9:277 word=32
  predicted sigma 19.086  log2Pf -68.5 +-3.8  margin 0.7 bits over target
  gate 16505 us   keys 1153 MiB   variance shares acc 69% ks 22% round 8%
  GATE_COST: 660 timings at OpenFHE 238153db, clang-18 (libomp) (8 threads), multi, single-socket i7-9700, 8 cores, 1 NUMA node (2026-09-05)

saved 525 frontier candidates to picks.json  (feed it to `dse.py plan`)
```

### Reading the frontier table

- **gate us** is the predicted per-gate time on the active cost regime, whose
  provenance line closes every report. **keyMiB** is bootstrapping key plus
  switching key, resident.
- **gadget** is the per-coefficient gadget map, `base:count`. A single-base set
  is `2^7:554`; the two-base rows spend margin.
- **log2Pf** is the predicted failure probability for a two-input gate at this
  ciphertext modulus; **band** is its one-sigma model uncertainty, propagated
  through the candidate's own variance shares, so it differs per row.
- **margin** is `target - certifiable - band`, where certifiable is the
  predicted log2Pf plus the 0.9-quantile penalty certification will apply
  ([verification.md](verification.md#the-search-must-select-on-the-same-statistic)):
  how many bits the candidate clears the target by after its own uncertainty is
  charged. Negative means the model cannot promise it. The `search` policy pick
  is the fastest row with positive margin; `table` applies the key cap on top
  ([methodology.md](methodology.md#key-material-is-free-up-to-a-cap-and-priced-beyond-it)).
- **flags.** `g-misaligned` and `ks-misaligned` say the gadget or key-switch
  decomposition does not fill its top digit, so a rounding bias term applies
  (it is modelled, not a warning). `margin-unspendable` says no discrete step
  exists (whole-map or one-split digit tier, key-switch tier) that this
  candidate's remaining margin could pay for. A flag that appears on every row
  A flag that fires on every row is telling you about itself rather than about
  the candidates, so treat that as a defect to investigate.
- The front is not a chain of winners. Rows below the pick are slower but
  smaller or safer; only you know which axis matters for the deployment. The
  policy is one replaceable function, `default_policy` in `dse_enumerate.py`.

### When a search refuses, it says what to run

Four of the gates refuse because the model does not know something, and they are
not equivalent. Three are coverage gaps that a measurement closes; one is a
region the noise model has never been calibrated over. A search prints the
distinction, with the command:

```
refusals a command would fix:
  security (unpriced) (244992 candidates)
      this dimension was never priced against the lattice estimator. Price it (needs Sage):
      sage -python scripts/paramsestimator/dse_security.py price --level STD192 --dist ternary --tolerance 1
      sage -python scripts/paramsestimator/dse_security.py boundary --level STD192 --dist ternary --tolerance 1
```

`table` prints the same thing per empty cell, grouped, so a sweep tells you in
one place which curves to price or which cells to time. A refusal for genuine
insecurity says so instead, since there is nothing to run.

### Same speed, better failure probability

The policy takes the fastest row whose margin clears its own band, and gate time
is quantised by the cost model, so a row costing the same within the model's own
error may sit many bits safer. The report names it when one exists:

```
  same speed, better failure probability (within 2.0% of the pick's gate time):
    log2Pf -80.2 against the pick's -68.5 (11.3 bits more margin), gate +1.39%, keys 1155 MiB (+0%)
    n=554 q=2048 logQ=27 q_KS=2^15 baseKS=256 gadget=2^7:347,2^9:207
```

Eleven bits of margin for 1.4 percent of gate time and no extra key material.
The window is 2 percent because that is smaller than the cost model's own
out-of-sample error, so a difference inside it is not one anybody can measure --
which makes the margin difference free. Nothing about "fastest" implies
preferring the riskier of two indistinguishable configurations, and the front
already holds both.

### Frontier, beats, and target search

Three different questions, three tools:

- **Target search** (`dse.py search --target T`): the cheapest configuration
  that clears a failure target of your choosing. This is the tool for "what is
  the failure-probability budget worth?"
- **Beats** (`dse_beats.py LEVEL[:TOL] ...`): the Pareto front of
  configurations that beat a *shipped* set on gate time while being **no worse
  than that set on failure probability**, both sides banded. "Shipped" means the
  row in the library's table the image carries; at the current pin that table is
  this tool's re-selected output, so point `BINFHECONTEXT_SRC` at
  `fixtures/binfhecontext-238153db.cpp` to compare against the table the library
  shipped before it.

  Read that criterion carefully, because it is stricter than a target and
  deliberately so. `beats` does **not** ask whether a candidate meets a target
  you chose; it asks whether it is a strict improvement on what ships. So a
  configuration that comfortably meets 2^-64 is rejected by `beats` if the
  shipped set happens to deliver 2^-131 -- not because the candidate is bad, but
  because adopting it would silently lower the set's failure probability by 67
  bits. That is the right default for "should we replace this row", and the
  wrong tool for "what does my failure budget buy". Use `search --target` or
  `table` for the latter. Tolerance is per level, set to the minimum at which the
  shipped set itself certifies. An illustrative run against the pre-re-selection
  table, multi/libomp regime, on the cells of an earlier pin:

  ```
  STD128 shipped: gate 17582 us, keys 261 MiB, log2Pf -130.9   (tol=1, w32)
     -> NOTHING beats STD128

  STD192 shipped: gate 89345 us, keys 822 MiB, log2Pf -85.2   (tol=1, w64)
     -> 160843 beat it; Pareto front on (time, keys) has 3 points:
        method   w    word  configuration                                      gate us   speedup  keys    log2Pf
        GINX     -    w32*  n=896 q=2048 lQ=28 {2^7:784,2^10:112} qKS=2^16 bKS=32  48269   1.851x   1.29x  -93.5 +-4.5
        GINX     -    w32*  n=832 q=4096 lQ=28 {2^6:832} qKS=2^15 bKS=32           50409   1.772x   1.01x  -93.2 +-3.8
        LMKCDEY  40   w32*  n=832 q=4096 lQ=28 {2^7:832} qKS=2^15 bKS=32           59133   1.511x   0.86x  -92.5 +-3.8

  STD256Q shipped: gate 75241 us, keys 2175 MiB, log2Pf -75.7   (tol=2, w32)
     -> NOTHING beats STD256Q
  ```

  The key ratios are against a baseline whose two keys are priced at their own
  word widths. That correction matters: a set at logQ 37 holds a **64-bit
  accumulator key and a 32-bit switching key**, and the switching key is the
  larger of the two there, so pricing both at one width overstated STD192's
  baseline as 1438 MiB against a measured 826. The starred candidates' own
  figures are unaffected, since both of their keys narrow.

  STD128 and STD256Q are unbeaten because those sets already run the 32-bit key
  forms on a default build; the STD192 and STD256 wins are the 32-bit path
  (moving Q down to 2^28), not margin.
- **The frontier itself** is what both report instead of a winner. A set that
  is 5 percent slower and 4 times smaller is often the one you want, and a
  single-axis rule cannot express that.

### `w32*`

A `w32*` row runs the 32-bit accumulator inside the ordinary 64-bit build,
which is the library default whenever the key fits: Q at most 2^28 and
`digitsG * gBits + 1 <= 32` for every base in the map. No flag, no rebuild. The
shipped set it is compared against does not fit (its modulus is too wide) and
stays 64-bit, which is why the row is starred. Gate time on that path is what
the w32 cells are measured on (a genuine 32-bit build agrees within 3.3 percent
on four matched configurations). The key column of a starred row is resident
memory, and since `12858277` the library serializes a key at the width it holds
it, so that column is also what a saved key occupies; the harnesses report the
resident form's bytes for the same reason. The tool prints this legend under
every front that contains a starred row.

### Every ranking names the build it was priced on

Gate-cost cells are measured against one specific OpenFHE commit under one
thread count and one OpenMP runtime, and a method-vs-method ordering is a
property of that build rather than of the algorithms. So `search` and
`dse_beats` print the pin their numbers came from, and a cross-method
comparison additionally prints the changes known to be pending that would move
it. Select the regime with `dse.py --threads single|multi --compiler clang|gcc`
or `DSE_COST_THREADS` / `DSE_COST_COMPILER`; a regime nobody measured holds no
cells and refuses every candidate ([cost-model.md](cost-model.md#regimes)).

## Step 3: `plan`

```
dse plan --picks picks.json [--keys K] [--samples S] [--order decision|frontier]
         [--model-limited] [--all] [--out plan.cmds]
```

`plan` turns saved candidates into a runner-ready command file: one
`boolean_noise_estimate_script` invocation per key, `-K 1` each, grouped under
a `# label:` line so the per-key rows can be regrouped by candidate.

- By default it emits the policy pick. `--all` emits every frontier point.
- `--order decision` (the default) sorts rows by **decision distance**: how
  many of its own bands a candidate's predicted margin sits from the target.
  Rows under one band are ones the model cannot decide and one measurement
  can; rows far above it only confirm what the model already says.
  `--model-limited` emits only the undecidable rows. This is the adaptive
  sampling rule made concrete: spend the expensive step where it changes a
  verdict ([methodology.md](methodology.md#directed-sampling)).
- `--keys` and `--samples` size the run. The header says how many keys
  `dse_verify.resolving_keys` would need to resolve the per-key scatter at
  that sample count; fewer keys still bound the pooled sigma, they just cannot
  separate scatter from sampling ([verification.md](verification.md#sizing-a-run)).
- Labels are `pick0_n554_lq27` for a policy pick and `cand0_n554_lq27` under
  `--all` or `--model-limited`; two rows with the same label would be merged by
  `decide`, so the plan checks they are distinct.

```
# Measurement plan from picks.json
# level STD128, target -64.0, 1 candidate(s)
#
# 12 keys x 1250 gates each. dse_verify.resolving_keys says 75 keys are
# needed at this sample count to RESOLVE the per-key scatter; ...
# label: pick0_n554_lq27
#   decision distance 1.17 bands: predicted log2Pf -68.5, band 3.8, gate 16505 us, map 128:277,512:277
build/bin/boolean_noise_estimate_script -n 554 -q 2048 -N 1024 -Q 27 -k 32768 -G 128:277,512:277 -b 256 -r 64 -s 3.19 -d 1 -t 2 -I 2 -i 1250 -K 1 -Z
build/bin/boolean_noise_estimate_script -n 554 ... -K 1 -Z      (x 12)
```

Check a generated plan before launching it: `-s` must be the input noise
(3.19), not a predicted total; `-K` must be 1; the map counts must sum to `-n`.
The plan checks all three itself, because a plan that names the wrong
configuration would be measured perfectly and answer a different question. It
verifies the input noise is the input noise and not a predicted total, that the
key count is one per process, and that the map's counts sum to the dimension:
`-s` and would have verified a different configuration.

## Step 4: `run-plan.sh`

```
JOBS=8 bash scripts/run-plan.sh plan.cmds > plan.log
```

Runs each command line, reduces its noise stream to sigma, mean and count, and
emits one `record|` line per key carrying the full parameter vector, the label,
and the binary's own `FAILURES=` count. It ends with `PLAN DONE`, which is
the marker a chain should gate on rather than the absence of a process
([measurement-practice.md](measurement-practice.md#chains-and-markers)).
`JOBS=<n>` runs n keys at once, one OpenMP thread each, within a memory budget
the plan's `# key_mib` hints size (`MEM_GIB` overrides it); records then come
out in completion order, which is why every consumer groups them by label.
Without `JOBS` the plan runs serially.

The parameter vector names **every dimension the search is free to move**
(`set=`, `baseg=`, `baserk=`, `keydist=`, `autokeys=` among them), and its
consumers exclude a row that lacks one rather than substitute a default. The
secret distribution is the example that shows why: the two distributions'
rounding terms differ by 15x, so a Gaussian LMKCDEY row scored against
ternary's `Var(s)/12` reads as 25 to 41 percent model error where the model is
within 3 percent. `dse.py validate --manifest` recovers those two fields for a
log written by a runner that lacked them.

Never measure noise without the failure count. A gate can return a clean,
low-noise encryption of the wrong bit, and no sigma-based metric sees that: a
GINX set with a Gaussian secret hides 202 wrong gates in 400 behind a sigma only
25 percent above normal. Named-set runs (`-p STD128`) carry no geometry flags, so
the record's `set=` field is the only thing that identifies them.

Timing and noise want opposite setups. `run-plan.sh` measures noise (one
single-threaded process per key, `JOBS` of them in parallel); gate time comes from
`boolean_estimate_time` on an otherwise idle machine, which is what the cost
arm does. See [measurement-practice.md](measurement-practice.md).

## Step 5: `decide`

```
dse decide plan.log --target T -q Q [--inputs 2] [--samples S] [--model-band 0]
```

For each label, `decide` computes every key's own log2Pf from that key's sigma
and realised bias, certifies the 90th-percentile key, and returns one of four
verdicts:

- **pass**: even the pessimistic end of the interval clears the target.
- **fail**: even the optimistic end misses it.
- **more keys**: undecided, and measurement is what is blocking it.
- **model-limited**: undecided, and the model band is what is blocking it.
  More keys cannot decide such a candidate; tightening a model term can.

`--model-band` belongs to candidates that are still predictions. Once a
candidate is measured its uncertainty is the measurement's; adding the model's
prediction error on top double-counts and leaves every measured row
model-limited, undecidable by construction. Leave it at 0 for a measured plan. When the run cannot resolve the per-key scatter, the certified quantile
assumes it at the measured bound (1.38 percent of sigma) and the report says
so. The statistics are in [verification.md](verification.md).

Verdicts from twelve keys of 1250 gates on the STD128 front above, at OpenFHE
`238153db`:

| row | map | bKS | gate | keys | predicted | certified (90th-pct key) | verdict |
|---|---|---|---|---|---|---|---|
| n=516 | 2^7:516 | 128 | 16 218 µs | 565 MiB | -64.8 ± 2.6 | -62.35 ± 1.23 | fail |
| n=516 | 2^6:129, 2^7:387 | 128 | 16 642 | 569 | -65.7 ± 2.7 | -63.00 ± 1.72 | more keys (~75) |
| n=554 (pick) | 2^7:277, 2^9:277 | 256 | 16 505 | 1 153 | -68.5 ± 3.8 | -64.92 ± 0.74 | **pass** |
| n=554 | 2^7:347, 2^9:207 | 32 | 16 735 | 254 | -71.2 ± 3.6 | -68.14 ± 1.88 | **pass** |

The last row is the practical recommendation: 4.8 percent faster than the
STD128 the library shipped before the re-selection (17 238 µs measured by the
legacy selector on the same image) at the same key material. The pick is 6.1
percent faster but its key-switching base of 256 buys margin with 4.4 times the
key bytes, which the `search` policy does not weigh except on ties; `table`'s
key cap does.

## Step 6: `validate`

```
dse validate plan.log
```

Scores the noise model against every record in the log and **runs a
known-answer control first**, against sets whose sigma is already recorded in
`dse_measured.py`. If that control fails, the numbers after it are not
trustworthy and it says so instead of reporting them. A parser that has drifted
produces a uniform error across every record, which reads as a physical result
rather than as a bug, and the control is what separates the two.

It then reports in two sections, because a log can hold two kinds of row.
Rows that name a shipped set (from a `-p NAME` run) are scored against that
set's parameters; rows that carry geometry instead (everything a `plan`
measurement produces) are scored as candidates, pooled per label:

```
MEASURED candidates, this run (model against measurement):
  label                  method  N      n      measured  predicted err      fails
  pick0_n554_lq27        GINX    1024   554    13.190    13.642      +3.43% 0

  predicted/measured sigma over 1 scored candidate: median 1.034
```

That row is from four keys of 100 gates, where sampling alone is about 3.5
percent, so read the error against the sample count. Rows at the wrap ceiling
or outside the model's domain are printed with a note and left out of the
summary, for the same reasons the fitter excludes them.

`scripts/paramsestimator/dse_lso.py LOG...` is the companion diagnostic: a
leave-region-out error map over every measured record you give it, by
alignment, digit count, ring dimension, word, method, key-switch share, map
shape and key-switching modulus ([noise-model.md](noise-model.md#validation)).

## Generating a whole table: `dse.py table`

The four-command pipeline is right for deciding one configuration and wrong for
regenerating a table. Six levels by three arities by three methods by two
targets is 108 sets, which is 432 commands driven by hand. `table` is the
search-based flow's equivalent of the selector's `--all`, and it collapses the
same job to three commands:

```
est estimator sage -python scripts/paramsestimator/dse.py table plan \
    --levels all --methods all --inputs all --targets=-64,-128 \
    --security estimator --tolerance 1 --jobs 8 --out-dir table
est estimator env JOBS=8 bash scripts/run-plan.sh table/plan.cmds > table/plan.log
est estimator sage -python scripts/paramsestimator/dse.py table finish \
    --dir table --emit-rows --emit-labels
```

Write `--targets` with an equals sign: a bare `-64,-128` is not a plain negative
number, so argparse reads it as an option.

`plan` searches every cell, selects under the key cap
([methodology.md](methodology.md#key-material-is-free-up-to-a-cap-and-priced-beyond-it)),
reports each one, and writes **one** measurement plan plus a manifest that
records every selection rule in force (the quantile budget, the map refinement,
the cap levels, rate and rule, the key layout). `finish` reads the log back,
certifies each cell on the per-key quantile, and emits a pastable parameter row
and a suggested enum label:

```
set                      predicted certifiable    pooled certified  suggested  verdict  gate us
STD128                       -72.6       -68.1     -68.3     -64.9      2^-64     PASS  16594

{ STD128,  {  27, 2048,  554, 2048,  32768, 256,  128, 64, 10, UNIFORM_TERNARY, 3.19, {{128, 304}, {512, 250}} } },
    STD128,                // STD128 : 2^(-64)
```

`repick` re-selects every cell of an existing table from its stored frontier
under the current model and policy, in about two minutes, and writes a manifest
marked `repicked_from` and a plan; a fresh `plan` remains the ground truth
([methodology.md](methodology.md#refine-the-frontier-dont-enumerate-the-refinement)).

Four things worth knowing:

- **Cells are named the way OpenFHE names them** -- the level, then `_3`/`_4`
  above arity two, then `_LMKCDEY`/`_AP` for a non-GINX method, with `LPF_` for
  a target at or below -100. That reproduces the library's naming exactly (the
  41 names of the pre-re-selection table, and the 105 rows of the current one,
  which this tool produced), which is what makes the output pastable and
  diffable.
- **An empty cell is reported, not omitted**, with the gate that refused it. "No
  configuration reaches this target" is a result a table has to state, and the
  dominant refusal says what to do about it: measure a regime, price a curve, or
  accept that the target is out of reach there.
- **`--jobs` parallelises the searches**, which is safe because they are pure
  arithmetic. It does not parallelise the measurement, which still wants the
  machine to itself.
- **The suggested label is taken from the pessimistic end** of the certified
  interval (`certified + uncertainty`, `--label-from pessimistic`) and rounded
  toward zero, to a whole bit by default, so a label never claims more than the
  measurement supports and a set tuned to a target does not give away five bits
  of it. `--label-from point` labels from the point estimate; `--round 5` keeps
  the library's multiple-of-five style.

## `costfit`, and choosing a regime

```
dse costfit gatecost.log [--word-size 32|64] [--log-q N] [--weight relative|absolute] [--emit]
```

Fits the cost cells from a `gatecost-regime.sh` log and, with `--emit`, prints
a paste-ready block. The procedure, the guards, and what each coefficient means
are in [cost-model.md](cost-model.md).

## What to keep

The measurement logs are the only durable product of a run; searches are
reproducible from the code and the cache. Keep every `plan.log` and every
`gatecost` log with the pin and regime they were taken under, because the next
pin move will want them as a before/after.
