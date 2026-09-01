# The cost model

Gate time is the one thing in this repo that is a property of a machine and a
build rather than of the mathematics. The noise model carries over between
pins and between computers; the cost cells do not. Everything in this page
follows from that.

## The form

```
gate_us = c0 + c1 * n + c2 * gate_work   [+ c3 * N / w      for LMKCDEY]
```

per **cell**, where a cell is one `(method, N, word_size)` and the coefficients
are microseconds:

- `c1 * n` is the per-coefficient work that does not depend on the gadget:
  chiefly key switching, which scales with the LWE dimension.
- `gate_work = SUM_j count_j * digitsG2_j` is the accumulator work in RGSW
  row-operations, with `digitsG2 = 2 * (digitsG - 1)` the rows the evaluation
  key is allocated at. Gate cost measured flat in logQ at a fixed digit count,
  so logQ enters only through the digit count.
- **The fourth coefficient means different things per method**, which is safe
  because a cell is keyed by method:
  - **LMKCDEY**: `c3 * N / w`, its automorphism work, which scales with the ring
    and falls with the automorphism key count. Without it LMKCDEY residuals run
    25 to 47 percent; with it 9 to 12. It saturates by `w` of 10 to 20.
  - **AP**: `c3 * n * products`, the fixed cost of one external product, against
    a count of the products a gate actually does. GINX and LMKCDEY do a constant
    number per coefficient, so for them that cost is proportional to `n` and
    `c1` absorbs it invisibly. AP is the first method whose product count moves
    independently of the gadget, and fitting it without this term leaves a
    residual running monotonically from -45 percent at two gadget digits to +20
    percent at twenty-seven. With it, the medians are 0.8 to 4.5 percent.
    Caveat: within one refresh base the product count is proportional to `n`, so
    `c1` and this term separate only across bases, and the arm sweeps three.
  - **GINX**: `None`. It has no fourth term.
- `c0` is whatever does not scale with any of those.
- **Two-base LMKCDEY maps carry one more term in a threaded regime**:
  `share * c2 * (n * d_max - gate_work)`, the regime's `map_width_share` (0.40
  in multi/libomp). Every accumulator region of a context runs at ONE OpenMP
  team width, the widest base's (OpenFHE `41709fbc`), so an index at the
  narrower base has idle threads instead of a smaller team. Measured on 90
  two-base rows: the per-index model under-prices them by 2.3 percent at N=1024
  and 0.7 at N=2048 at the median, the pure widest-width model over-prices them
  by 1.8 and 3.5, and the residual follows `(n*d_max - gate_work)` at 0.36 to
  0.49 x `c2`; the GINX control on the same 90 maps reads -0.10 and -0.05 x `c2`
  with no trend in the coarse fraction, so the cost is LMKCDEY's. Zero in the
  single-thread regimes, where nothing idles; `None` (priced as zero, and the
  regime flagged stale) where a regime has not measured it.

The form is affine, not proportional, and the same three (or four) terms
describe every method at both word sizes. Median residuals per cell: GINX 1.8
to 2.8 percent, LMKCDEY 2.3 to 5.5, AP 1.6 to 4.5; out of sample, 0.93 to 1.05
on the 32 legacy-control candidates and 0.7 to 3.2 percent on the
pre-re-selection sets.

## Cells

Twelve cells per regime for GINX and LMKCDEY (N in {512, 1024, 2048}, word 32
and 64), plus six for AP in every regime whose arm has run with `METHODS=1`.
A regime's cells can rest on more than one pin, by method, because the arm is
run per method after a pin move (`pins_by_method`; the doctor and
`cost_provenance()` say which). The default regime, 8 threads, clang-18 with
libomp, on a single-socket i7-9700: GINX at `238153db` (2026-09-05; verified
unchanged at `41709fbc` by the 90-row GINX maps control, +0.4 to +0.8 percent),
AP at `238153db` (2026-09-07), LMKCDEY at `41709fbc` (2026-09-09):

```
("AP",       512, 32): (-262.3, 2.147, 0.9273, 11.878),
("AP",       512, 64): (-60.8, 3.043, 2.8425, 16.045),
("AP",      1024, 32): (-229.1, 1.234, 1.5641, 17.558),
("AP",      1024, 64): (-123.2, 1.893, 5.3152, 28.125),
("AP",      2048, 32): (-434.1, 0.000, 2.7880, 28.901),
("AP",      2048, 64): (-353.1, 1.947, 10.2781, 53.973),
("GINX",     512, 32): (-65.8, 14.617, 0.9207, None),
("GINX",     512, 64): (-53.6, 22.103, 2.8076, None),
("GINX",    1024, 32): (-34.2, 21.638, 1.6432, None),
("GINX",    1024, 64): (-10.9, 36.433, 5.3839, None),
("GINX",    2048, 32): (109.8, 35.249, 3.0881, None),
("GINX",    2048, 64): (146.0, 66.206, 10.5874, None),
("LMKCDEY",  512, 32): (188.6, 16.959, 1.3362, 6.510),
("LMKCDEY",  512, 64): (1187.5, 22.134, 4.0104, 0.000),
("LMKCDEY", 1024, 32): (1981.9, 24.667, 2.3407, 0.000),
("LMKCDEY", 1024, 64): (3102.1, 36.361, 7.7425, 13.021),
("LMKCDEY", 2048, 32): (3015.4, 37.379, 4.7696, 20.345),
("LMKCDEY", 2048, 64): (5750.6, 58.154, 16.5418, 58.187),
```

A `None` coefficient means the measurement never resolved that term; the cell
refuses any candidate that needs it. A `0.000` in LMKCDEY's `c3` column is
different and deliberate: the term measured **below the timing quantum** (the
arm times 8 gates in whole milliseconds, so 125 µs per gate) with a coin-flip
sign, and is priced as zero with its bound recorded (under 6.5 and 7.3 µs per
unit of `N/w` in those two cells, under 170 µs a gate at w = 10); refusing it
would drop every automorphism count but 10 at those cells for a cost the data
says is not there. A clear wrong-sign majority (30 percent or fewer pairs
positive) is refused. `doctor` prints a note naming the cells priced this way.
AP's `c3` is its per-product cost and is bounded at zero in the fit, as is its
`c1`; the `0.000` at AP N=2048/w32 is that bound ([AP caveat](#the-form)).

A **w32** cell is timed on the 32-bit key forms inside the 64-bit build, which
is what a default caller gets at Q up to 2^28 since OpenFHE `9e8045db`
([image-and-pins.md](image-and-pins.md#one-library-and-the-hybrid-default)). A
**w64** cell is timed at logQ 37 with default flags, so it carries the 32-bit
*switching* key a default caller still gets there (q_KS fits whatever Q is);
that landed in `c1`, by under 3 percent.

## Regimes

Cells are valid only for the thread count and OpenMP runtime they were
measured under, and neither is a scale factor that could be divided out:

- The runtime **reorders** builds rather than shifting them. gcc-14 (libgomp)
  leads clang-18 (libomp) on GINX gates at 8 threads by 3 to 8 percent while
  clang leads at 1 thread and on LMKCDEY. The effect is the OpenMP runtime, not
  the compiler: the same gcc-built library with only the runtime swapped by
  `LD_PRELOAD` runs 2.35x faster on the min at 8 threads. A gcc build links
  libgomp and a clang build links libomp, so every "compiler" comparison is a
  runtime comparison in a compiler costume. The regime key uses the runtime
  names; `clang`/`gcc` are accepted as aliases.
- Thread count changes which term dominates, and a 1-thread calibration
  systematically overstates the many-core gain of coarse-gadget candidates,
  because parallelism cuts the marginal cost of a digit.

So `COST_REGIMES` is keyed `(thread mode, runtime)`, each entry carrying its
own `pin`, `measured` date, point count and box, and a regime nobody measured
holds no cells: `gate_us` returns `None` for every candidate there and the
search refuses rather than guessing. An empty frontier and "the cells for this
build do not exist" would otherwise be indistinguishable in the output, and the
first reads as a result.

| regime | threads | runtime | methods | pin | measured | why that pin |
|---|---|---|---|---|---|---|
| multi / libomp (default) | 8 | clang-18 | AP, GINX, LMKCDEY | describes `12858277`; measured at `41709fbc` (LMKCDEY) and `238153db` (GINX, AP) | 2026-09-09 | the image's pin. The commits between are key layout, key generation, guards, serialization, the table itself and code outside BinFHE, and gate time was measured across each move on this box, so the cells are **carried** rather than re-run; the regime records the carry and `doctor` prints it |
| multi / libgomp | 8 | gcc-14 | GINX, LMKCDEY | `238153db` | 2026-09-05 | the previous image pin; **stale** for two-base LMKCDEY (21 to 37 percent team-width churn that `41709fbc` removed) until `METHODS=3 MAPS=1` is re-run under gcc |
| single / libomp | 1 | clang-18 | GINX, LMKCDEY | `a0c3f2cd` | 2026-09-05 | no OpenMP region executes at one thread and the only commit between the two pins that is not scheduling (`e39eee84`) is keygen-only |
| single / libgomp | 1 | gcc-14 | GINX, LMKCDEY | `a0c3f2cd` | 2026-09-05 | as above |

**AP is measured in the default regime only**, so its 18 cells against the
others' 12. Anywhere else an AP candidate is *refused* rather than priced:
`gate_us` finds no cell and returns `None`, and the searches report it as
uncalibrated and counted. That is the same rule every gap here follows -- a
missing cell never becomes a guess -- and it is deliberate rather than pending,
because AP costs more machine time per row than the other two methods together
and no shipped set uses it at one thread. To fill it: `THREADS=1 METHODS=AP bash
scripts/dse-arms/gatecost-regime.sh`, then paste with `--update-regime`, which
adds the AP record to that regime.

Select one with `dse.py --threads single|multi --compiler clang|gcc ...` or
`DSE_COST_THREADS` / `DSE_COST_COMPILER`. `cost_provenance()` prints the
**active** regime's pin, and `tests/test_dse.py` fails if any regime's pin
stops looking like a full commit sha. `dse.py doctor` goes further and compares
that pin against the OpenFHE commit actually installed in the image, so cells
measured on a different build say so instead of being inferred; the two
single-thread regimes are the deliberate case, and their table entry records
why. Comparing numbers across regimes carries
whatever changed between their pins as well as the regime difference.

## What the cells do not represent

Listed in `COST_UNREPRESENTED`, and printed by `doctor`, as distinct from
things the cells get wrong:

- **Many-core deployments.** The cells stop at 8 threads on an 8-core box. The
  LMKCDEY regions run at one full-width team, and on a 72-core machine at 36
  threads that form measures 6 to 11 percent faster than a capped one, with
  rows still improving to the core count. A ranking for such a deployment needs
  cells measured there; the arm takes `THREADS=<n>`.
- **n = 64 for LMKCDEY.** The `c3 * N / w` term does not scale with `n`, so at
  n = 64 and high digit counts the fit over-predicts by 16 to 33 percent
  against medians of 2.5 to 6 percent at n of 256 and above. Nothing shipped
  or on any front has n below 256.
- **Single slow rows.** Three (libomp) to five (libgomp) of the GINX and
  LMKCDEY timings per regime are one 8-gate mean 18 to 46 percent above the fit, scattered in n and
  d. The quoted tails include them.

## The arm: `scripts/dse-arms/gatecost-regime.sh`

One arm measures the cells of one regime, **both word sizes**, at one commit.
One command drives it through a whole recalibration of one method, fits included:

```
bash scripts/dse-arms/recalibrate.sh [OUTDIR]              # GINX
METHODS=all bash scripts/dse-arms/recalibrate.sh [OUTDIR]  # every method
```

**A cost cell is keyed by (method, N, word size) and nothing extrapolates across
methods**, which is why the default is one method rather than three: measuring
GINX yields correct GINX gate times whatever the rest of the table holds, and no
statement at all about the others. It defaults to GINX because that is what
`dse.py search` selects by default. `METHODS` takes names or the arm's numbers,
comma or space separated, and `all`; a table run, which prices every method
against every other, needs `all`.

The stages follow from the methods: the single-base sweep and the single-thread
regime always, LMKCDEY's two-base map rows when LMKCDEY is in, and the GINX
control on those same maps whenever GINX or LMKCDEY is (it is the evidence that
the team-width term is LMKCDEY's alone, and therefore that a GINX multi-base gate
pays no width penalty on this box). `DRY=1` prints the resolved plan and measures
nothing. Run the stages by hand only to repeat one:

```
THREADS=8 METHODS=2 bash scripts/dse-arms/gatecost-regime.sh > mt.log       # GINX, both widths
THREADS=8 bash scripts/dse-arms/gatecost-regime.sh > mt.log                 # all three methods, both widths
THREADS=8 METHODS=3 MAPS_ONLY=1 bash scripts/dse-arms/gatecost-regime.sh    # the two-base LMKCDEY map rows
THREADS=8 METHODS=2 MAPS_ONLY=1 bash scripts/dse-arms/gatecost-regime.sh    # the GINX control on the same maps
THREADS=1 METHODS=2 bash scripts/dse-arms/gatecost-regime.sh > st.log       # the single-thread regime
```

The arm sweeps every method it is handed in ONE pass over the grid, so a
three-method sweep is one invocation of the second line; calling it once per
method re-measures the shared grid each time.

**How long: hours for all three methods**, and only part of that is measured
rather than estimated. GINX and LMKCDEY run about 2.4 seconds a row at 8 threads,
measured: 530 rows in 21 minutes, and 90 map-control rows in 3m40. AP is in the
same sweep at a much lower rate, since its gate runs hundreds of milliseconds and
its key generation tens of seconds on the large rows, and its share of the wall
clock is not measured here. Of the 1423 timings behind the multi/libomp cells,
330 are GINX, 530 LMKCDEY and 563 AP, which is the shape of what a method costs.
`recalibrate.sh` prints the row count, elapsed time and rate of every stage and
appends them to `timing.log`, which is how a number for your hardware gets
recorded rather than estimated. `NS`, `WORDS` and `METHODS` cut the grid to the
part you need.

**A partial recalibration leaves a mixed table, and that has to be recorded.**
`apply_cells.py --merge` keeps the cells the fit is silent about, so a GINX run
lands GINX rows beside AP and LMKCDEY rows from another machine; gate times are
then comparable within a method and not across them. The merge prints which
methods it kept and appends that fact to the table's leading comment, and
`recalibrate.sh` fills the measured methods into the provenance stub, because a
comment naming one box above another box's rows is the claim nobody could later
retire.

It sweeps method × N × the undominated gadget bases at that logQ × n in {64,
256, 512, 1024, up to N}, and for LMKCDEY both w = 10 and w = 40 at every
point, because `c3` is taken from matched pairs and needs both values at the
same `(n, base)`. `MAPS=1` adds two-base maps (pairs 128/512, 64/128, 32/64 at
N 1024 and 2048, coarse fractions 6 to 80 percent), which feed
`scripts/dse-tools/maps_fit.py`; `NS` and `WORDS` restrict the sweep. Each
record carries `word=32|64`, the thread count, both `internal32=` fields the
binary reports, and the gate time as the mean of 8 gates from
`boolean_estimate_time` (which separates keygen from gate time). It ends with
`GATECOST DONE`.

Both widths in one arm is not tidiness. The unified search compares a w32
candidate directly against a w64 baseline; when one commit moved 8-thread w32
by 13 to 21 percent and w64 by only 5 to 10, the uneven shift landed entirely
on that verdict. Cells for the two widths must come from the same pin under the
same regime.

The other runtime needs its own image, because OpenFHE and the harness are
both built with the compiler that selects it
([getting-started.md](getting-started.md#phase-6-recalibrate-for-your-hardware-the-skipped-step)).

## The fitter: `dse.py costfit`

```
dse.py costfit mt.log --weight relative --emit
```

```
530 timings read, 0 failed rows
threads: 8

  method   N      word  pts  c0          c1         c2        c3        med|res| worst
  LMKCDEY  512    32    27   188.6       16.959     1.3362    6.510     2.84%    12.39%
  LMKCDEY  512    64    33   1187.5      22.134     4.0104    0.000     3.64%    26.25%  c3 BELOW QUANTUM -> 0 (|c3| < 6.5; 64% correct sign)
  ...
  LMKCDEY  2048   64    44   5750.6      58.154     16.5418   58.187    5.46%    37.12%

GATE_COST = {
    ('LMKCDEY', 512, 32): (188.6, 16.959, 1.3362, 6.510),
    ...
}
```

(an excerpt from the `METHODS=3 MAPS=1` log at `41709fbc`; the worst rows are
the n = 64 corner the cells do not represent)

It fits exactly the model `gate_us` evaluates and derives its features by
**calling** `gate_work`, so a refit cannot drift from the predictor it feeds.
Each guard is a defect it would otherwise reproduce:

- cells are partitioned by word size, because fitting both widths together
  gave GINX a 38 percent residual instead of 2;
- a log with mixed thread counts is refused rather than averaged;
- rows that do not parse are reported rather than dropped, which turned a
  field-shifted log into 474 rejections instead of plausible numbers;
- a row whose `word=` contradicts the key form the binary reported is refused
  (a w32 row must show a narrowed refresh key, a w64 row must not);
- `c3` comes from matched pairs differing only in `w`, never from a joint fit:
  on the main grid `w` is held at 10, so `c3 * N / 10` is constant within a
  cell and absorbs into `c0`; a joint fit there is rank-deficient and returns a
  confident wrong answer;
- `--weight relative` minimises percent error, which is what mis-orders
  candidates; absolute weighting lets the largest-n rows set the fit (on the
  LMKCDEY w32 cells it cut the worst residual from 111 to 44 percent at an
  unchanged median). Use one objective per table.

`--log-q` supplies the modulus for older logs that do not record it;
`--word-size` labels rows that carry neither a `word` nor a `build` field.

## Recalibrating

After a pin move, a new machine, or a new regime:

1. Check whether **noise** moved first. It usually does not, and cost usually
   does: run the correctness gate (the same 20 rows at both pins,
   `scripts/dse-tools/gate_compare.py OLD.log NEW.log`;
   [measurement-practice.md](measurement-practice.md#correctness-gate-before-calibration)).
   If it passes, every noise number stands. `scripts/dse-arms/oos.sh` times the
   shipped sets and is the out-of-sample check on the cells' *form*.
2. Run the arm for each regime and method you need, sole job on the box and
   pinned to one NUMA node
   ([measurement-practice.md](measurement-practice.md)). `recalibrate.sh` does
   both and records the binding; unpinned on a many-core box the residuals run
   3 to 11 percent instead of 2 to 3 and some `c3` terms stop resolving. A pin
   that touches one method's regions needs only that method's arm (`METHODS=`,
   which defaults to GINX); the others keep their cells and the regime records
   both pins.
3. `dse.py costfit LOG --weight relative --emit` per log.
4. Read `provenance.txt`. `recalibrate.sh` writes it from the machine: the box
   from `lscpu`, the OpenMP runtime the harness actually linked from `ldd`, the
   pin from the installed library's own record, plus the binding, thread counts,
   methods, row count and the residual ranges parsed out of the fits. A probe
   that cannot answer says so in the file and on the terminal; nothing else needs
   completing.
5. Paste each block into its `_CELLS_*` table:
   `apply_cells.py FIT.txt --regime multi/libomp --comment provenance.txt --merge
   --update-regime --pin <commit> --box "<machine>"`. It writes the rows (printing
   the old ones, so the previous numbers land in the transcript), the comment at
   the head of the table, and the regime's per-method provenance record. `--merge`
   keeps cells the fit is silent about, which is what a one-method log needs, and
   appends to the comment a line naming which methods it describes and which it
   does not.
6. Compare old against new as microseconds, not coefficients:
   `scripts/dse-tools/compare_cells.py FIT.txt --regime multi/libomp` prints
   each cell's predicted gate at two reference configurations and the ratio.
   `scripts/dse-tools/resid_table.py LOG` shows residual percent by `(n, d)` so
   a worst case can be located rather than read as one number. If the arm ran
   with `MAPS=1`, `scripts/dse-tools/maps_fit.py LOG` says whether
   `map_width_share` still describes the two-base rows.
7. Review what `--update-regime` cannot decide: `carried_why` when the pin
   moved (it says so), the `stale` flag and `COST_STALE` if a method's cells are
   knowingly left at an older pin, and `COST_PIN` once every regime agrees. Move
   anything that landed from `COST_PIN_LACKS` to `COST_PIN_CONTAINS`; the doctor
   and every cross-method report print both.
8. `sage -python tests/test_dse.py`, then `dse.py doctor`. The suite computes its
   expectations from `GATE_COST` rather than from stored microseconds, so a
   recalibration does not break it; a failure there is a real one.
9. Re-run any search whose picks you still hold. They were priced on the cells
   you just replaced, and an uneven shift between the two word sizes lands
   entirely on the w32-against-w64 verdict.

### Per-method provenance

A cost cell is measured per method, so a table normally holds one machine's GINX
beside another's AP -- `recalibrate.sh` measures GINX by default, and `--merge`
keeps the rest. The regime records that shape directly, one record per method in
`by_method`:

```python
by_method={
    "AP":      dict(pin=PIN_2381, on="2026-09-07", points=563, box=COST_BOX),
    "GINX":    dict(pin="94229558...", on="2026-09-10", points=220, box="Xeon Gold 6240, ..."),
    "LMKCDEY": dict(pin=PIN_4170, on=MEASURED_4170, points=530, box=COST_BOX),
}
```

`points`, `measured`, `box` and `pins_by_method` are **derived** from those
records by `_rollup_regime`, and `apply_cells.py --update-regime` writes the
records for exactly the methods in the fit. Nothing is added up by hand, which is
the point: a `points` literal is a hand-kept total that goes stale the moment one
method is re-measured, and a single `box` string over rows from two machines is a
claim nobody can check. Where the boxes disagree the rollup refuses to name one
-- `box` reads "two or more machines" and `cost_provenance()` prints a `boxes:`
line naming each method's -- because the method a default search prices is not
always the one with the most timings.

What that leaves to a person is the one thing measurement cannot settle: when a
fit moves the regime's `pin`, the methods not in that fit become **carried** to
it, and only `carried_why` can justify a carry. `apply_cells.py` prints the pin
move and says so rather than editing the prose.

## What moves a cell

Recorded in `COST_PIN_CONTAINS`, newest last, so that a ranking can say what
its numbers rest on:

- `f9694c39` parallelised the CGGI accumulator and gave LMKCDEY only
  automorphism-map caching: GINX's `c2` improved 2.58x geomean against
  LMKCDEY's 0.95x, and no method-vs-method conclusion drawn on the serial cells
  survives it.
- `b72753b5` parallelised the key switch; tested and not worth an explicit
  term at 8 threads (about 460 µs against a 20 ms gate).
- `0c75c2ec` fused the DM and LMKCDEY accumulator regions, so LMKCDEY's
  accumulator is parallel for the first time: its 8-thread `c2` fell to 0.59
  to 0.70x while GINX moved under 3 percent.
- `a0c3f2cd` capped that fused region at `max(digitsG2/2, 4)` threads, which
  removed the libgomp cliff (a 4.8x, bimodal penalty at threads equal to
  cores) and made multi/libgomp LMKCDEY priceable.
- `9e8045db` made the 32-bit key forms the library default.
- `12858277`, the image's pin, is the head of openfhe-development `dev`: the
  squash-merge of the branch `94229558` sat on plus two CKKS-only PRs that
  touch nothing under `src/binfhe` or `src/core`. The commits between
  `94229558` and the merge leave the accumulator and key-switch loops alone:
  two per-gate guards and the 28-bit `Fits` cap the model already prices, a
  fold of key generation into shared templates, serialization, and the
  parameter table itself. The cells are carried on the strength of
  `gatetime-ab.sh` across the move, not on that inspection. With the same
  explicit geometry at both pins and the pins interleaved (six repetitions,
  min of 8 gates each, best kept), `12858277` over `94229558` reads STD128
  1.010, STD128_3 1.000, STD192_4 1.014, STD128_LMKCDEY 1.000, STD128_AP
  1.013 and STD128_3_AP 1.034: inside the cells' own residuals, uniform
  within a method to 0.4 percent across ring dimensions, so no within-cell
  ranking moves. AP reads about 2.5 percent slower (1.024 and 1.027 at twelve
  repetitions of the two AP sets, the new pin's twelve above the old pin's
  almost without overlap), which is what to expect when its cells are next
  re-measured. Two pin-major passes before that one
  read 3 percent either way with the sign following whichever pin ran first,
  and one named set had changed its row with the table (STD192_4, `b_KS` 512
  to 64), which is why the arm interleaves and times geometry, not names.
- `94229558` adds two key-layout commits (`c4180d75`,
  `94229558`) that shrink the switching key, DM's refresh key and key
  generation. They are in this list because a carry is a claim about cost and
  belongs where the other cost claims are, and because the carry is not clean.
  Timed on this box with `gatetime-ab.sh`, min of 8 runs per set per pin: three
  sets whose switching key shrinks a quarter get 1.3 to 1.9 percent **faster**,
  the aligned-pair control is flat at +0.5, and `STD192_4` -- the only N = 2048
  set in the sample -- is **4.3 percent slower**, reproducibly (it read +5.2 on
  an independent 3-run pass). Whatever the mechanism, it is not "smaller key,
  faster gate". The cells are carried anyway on a measured basis: repricing
  every stored frontier row by those factors, N = 2048 up 4.3 percent and
  N = 1024 down 1.5, moves **no pick in any of the 108 cells**, and 29 of them
  hold rows at more than one ring dimension, so the test had somewhere to move
  to. A within-cell ranking is mostly among rows of one ring dimension, where a
  uniform factor cancels.
- `41709fbc` runs every accumulator region of
  a context at **one team width** and one scratch size across gadget bases. multi/libomp LMKCDEY re-measured: `c1`/`c2` within 5 and 6 percent
  at N >= 1024, `c0` moved -69 to +21 percent, `c3` at N=1024/w32 fell below the
  timing quantum and is priced zero (5.7 at `238153db`). The two-base residual
  that remains (0.7 to 2.3 percent) is the width term above.
- `238153db`, the image's pin from 2026-09-05 to 2026-09-09, replaced the cap with **equalised team sizes**,
  the mechanism behind the cliff being the runtime rebuilding its thread team
  between consecutive regions that ask for different counts. Within 0.95 to 1.05 of
  the cap at 8 threads, 6 to 11 percent faster at 36. At 8 threads every
  multi-regime cell moved under 2 to 3 percent; the one visible change is that
  `c3` resolved again at N = 1024 (20.3 µs on w64, 5.7 on w32).

Two properties of the measurement itself, recorded alongside: the libgomp
penalty against libomp on identical configurations at 8 threads is a median of
1.00 to 1.14 (the 1-thread runtime term, no cliff), and at
saturating widths both runtimes show 148 to 292 percent intra-run skew in a few
cells (one slow gate in eight, the min untouched). The min-of-N statistic is
right for a saturating table; the 8-gate means the cells rest on carry that
skew, which is why the tails are quoted.

`COST_PIN_LACKS` lists scheduled upstream changes expected to move a method
comparison. It is empty at `12858277`; when it is not, every cross-method
report prints each entry as `pending:`. Key-layout changes do not appear there
even when queued: they move key sizes and noise rather than gate times, and the
key-layout flags track them instead
([noise-model.md](noise-model.md#key-sizes-and-the-calibration-arms)).
