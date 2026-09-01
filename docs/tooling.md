# Every script, in one place

Run everything under `sage -python` inside the container. The pure-Python
modules also run on any host with numpy and scipy; anything that imports the
lattice-estimator or OpenFHE does not
([getting-started.md](getting-started.md#use-sage--python-never-python3)).

## Entry points

| script | what it does |
|---|---|
| `scripts/paramsestimator/dse_wizard.py` | the guided flow: asks method, level, arity, failure target, key budget, whether to recalibrate for this machine, thread regime and key count, then runs `doctor`, optionally `recalibrate.sh` + `apply_cells.py --update-regime`, `dse_table.py plan` for that one cell, `run-plan.sh`, and `table finish --emit-rows --emit-labels`. Detects the OpenMP runtime from the harness rather than asking. Every answer is a flag, `--yes` takes defaults, `--dry-run` prints the commands; re-running in the same directory resumes |
| `scripts/paramsestimator/dse.py` | the door to the search-based flow: `doctor`, `search`, `plan`, `decide`, `validate`, `table`, `costfit`, plus `--threads` / `--compiler` to pick a cost regime. [dse-flow.md](dse-flow.md) |
| `scripts/paramsestimator/binfhe_params.py` | the parameter selector: bisects `n` on measured noise per digit count and names a winner. [legacy-flow.md](legacy-flow.md) |
| `scripts/paramsestimator/binfhe_params_validator.py` | measures sigma and failure probability for a named set, for `ALL` of them, or for a `command args:` line |
| `scripts/paramsestimator/dse_table.py` | generates a whole table: the cross product of level, arity, method and target, in one `plan`, one `finish`, and `repick` to re-select from stored frontiers. `dse.py table` is its front end. [dse-flow.md](dse-flow.md#generating-a-whole-table-dsepy-table) |
| `scripts/paramsestimator/dse_beats.py` | Pareto front of configurations that beat a shipped set at no worse failure probability. `LEVEL[:TOL] ...` |
| `scripts/paramsestimator/generate_std_tables.py` | recomputes a security-table cell from the estimator; the tool for a table update. Interactive only |

## Models and their support

| module | contents |
|---|---|
| `dse_model.py` | the noise, failure-probability, key-size and cost models, every calibrated constant with its provenance, the guards, and `COST_REGIMES`. [noise-model.md](noise-model.md), [cost-model.md](cost-model.md) |
| `dse_constraints.py` | the closed-form correctness constraints the gates call: method/keyDist, automorphism bound, gadget width, the 32-bit eligibility predicate, carry precondition, undominated bases, alignment |
| `dse_enumerate.py` | the grid, the eleven gates, prediction, the streaming Pareto front, the two-base map refinement (`refine_maps`, `boundary_split`), `next_step_cost`, and the selection policies (`default_policy`, `key_cap_policy`) |
| `dse_security.py` | certified boundaries: `price`, `boundary`, `coverage`, `compare`, and the pure-Python read path the enumerator uses. [security.md](security.md) |
| `dse_security_cache.json` | 2 037 priced dimensions over 26 curves, plus 169 boundary dimensions on 13 of them, against lattice-estimator `53da5982` |
| `dse_verify.py` | per-key certification, the four verdicts, key sizing, and the measurement plan. `plan` / `decide`. [verification.md](verification.md) |
| `paramstable.py` | OpenFHE's own `StandardLatticeParmSets`, transcribed, with `max_logq` interpolation |
| `binfhe_params_helper.py` | the selector's measurement, fan-out sizing and security pricing (which reads and fills the shared cache) |

## Fitting, parsing and diagnostics

| module | contents |
|---|---|
| `dse_gatefit.py` | fits the cost cells from a gate-timing log; `dse.py costfit` is its front end. Guards, and the `c3`-from-matched-pairs rule. [cost-model.md](cost-model.md#the-fitter-dsepy-costfit) |
| `dse_validate.py` | scores the noise model against a measurement log after a known-answer control; `--fit` refits |
| `dse_shipfit.py` | every shipped set against the model, reading the table from OpenFHE's `binfhecontext.cpp`, control first |
| `dse_lso.py` | leave-region-out error map of the noise model over any set of measured logs. [methodology.md](methodology.md#directed-sampling) |
| `dse_sweeplog.py` | parses selector sweep logs and `record\|` runner logs into measured records |
| `dse_measured.py` | the measured data the model is calibrated and validated against, and the known-answer control (`CONTROL`: parameters and measured sigma of four sets at the pin; `CONTROL_HISTORICAL`: five earlier sets). Checked in because regenerating costs about two hours |
| `dse_calibrate.py` | designs the calibration cells a sweep cannot produce: `plan`, `ladder`, `fit`. [noise-model.md](noise-model.md#key-sizes-and-the-calibration-arms) |
| `dse_isolate.py` | parses the isolated post-accumulator measurements (`round1`, `round2`, `ksonly`, `tail`) |
| `dse_accfit.py` | compares candidate accumulator shapes against the ladder; the analysis that identified `gadget_shape` |

## Harness (C++)

| binary | what it does |
|---|---|
| `build/bin/boolean_noise_estimate_script` | the noise measurement. `-i` gates per key, `-K` keys, `-G` per-dimension gadget map, `-p` a named set, `-Z` to skip size reporting, `-6` to force the 64-bit key forms. Emits noise values on stderr (needs `WITH_NOISE_DEBUG=ON`) and its own `Failures:` count on stdout |
| `build/bin/boolean_estimate_time` | one context, keygen time, key sizes, and a handful of gates. Separates keygen from gate time, which is what the cost arm needs. Also the way to sanity-check a parameter set by hand |
| `build/bin/boolean_keyswitch_isolate` | the post-accumulator tail, each stage on its own, against a zero-error ciphertext |

`-h` lists every option on each. The two estimator binaries default to the
32-bit key forms where they fit, mirroring the library
([image-and-pins.md](image-and-pins.md#one-library-and-the-hybrid-default)).

## Measurement runners and arms

| script | what it does |
|---|---|
| `JOBS=<n> MEM_GIB=<g> scripts/run-plan.sh PLAN` | runs a plan, `JOBS` keys at once with one OpenMP thread each inside a memory budget sized from the plan's `# key_mib` hints (serial without `JOBS`), reduces each command to one `record\|` line with sigma, mean, sample count, `FAILURES=` and every searched dimension (including `keydist=` and `autokeys=`), grouped by `# label:`. Ends `PLAN DONE` |
| `dse.py table repick --dir T --out-dir T2` | re-prices every stored frontier row under the current noise model and cost cells (`evaluate()` again), then re-selects each cell under the current policy and map refinement, without searching; reports how many rows moved and by how much; writes a manifest marked `repicked_from` and a plan. Seconds, not hours; a fresh `plan` is the ground truth |
| `dse.py table plan --dropped-ks 0,1` | enumerates OpenFHE's approximate key-switching decomposition (`droppedDigitsKS`) alongside the exact one. A `delta > 0` row is priced and shown but never selected: its key-switch rounding term is derived and unmeasured, and `b_KS` already trades the same two axes with a measured term ([noise-model.md](noise-model.md#key-sizes-and-the-calibration-arms)) |
| `dse.py table plan\|repick --key-cap-gib SPEC [--key-cap-lambda L]` | absolute key-material cap **per method**: `"4"` for every method, `"4,AP=8"` (the default: 4 GiB for GINX and LMKCDEY, 8 for AP), `"none"` for no cap. Every admissible row scored `gate*(1+L*max(0,key/cap-1))` (default L = 2): under the level a row scores its gate, beyond it its excess is priced; `over_cap`, the cap and the rule recorded per cell. `--key-cap-hard` is the two-stage rule (fastest row that fits, score only when nothing fits). `--key-cap-mult M` is the relative form (M times the shipped row, frozen `238153db` table) |
| `scripts/dse-arms/recalibrate.sh [OUTDIR]` | the whole recalibration of one method in one command: the single-base sweep, the map rows that method has, the single-thread regime, then the fits. `METHODS` takes names, numbers or `all` and defaults to GINX, which is also `search`'s default; the stages follow from it. Pins the run and records the binding. Resumable (a stage whose log ends `GATECOST DONE` is skipped, unless it covers other methods, which it refuses), reports rows and rate per stage, and edits nothing -- it prints the `apply_cells.py` commands and a provenance stub naming the methods measured. `DRY=1` prints the plan only. Ends `RECALIBRATE DONE` |
| `scripts/dse-arms/gatecost-regime.sh` | one stage of that: the cost cells of one regime, both word sizes, one commit. Its own `METHODS` default is all three, so a plain run covers the single-base grid (`recalibrate.sh` passes the methods it was asked for instead); `MAPS_ONLY=1` emits only the two-base map rows, `MAPS=1` adds them to a sweep; `THREADS=n`, `NS`, `WORDS` restrict it. Ends `GATECOST DONE` |
| `scripts/dse-arms/pinmove-gate.sh [OLD.log]` | the correctness gate at a new pin: runs the 20 rows of `plans/pinmove-gate.cmds`, stops on any wrong answer, and with the previous pin's log also predicts the per-row ratios and compares. Exits nonzero on a fail, so a chain can gate on it. **The first thing to run after a pin move** |
| `scripts/run-plan.sh scripts/dse-arms/plans/control-rebaseline.cmds` | re-measures the four known-answer control sets in both key forms at `-i 2400 -K 6` (28800 pooled gates per set), the precision `dse_measured.CONTROL` records. Run it when `doctor` prints `LIVE TABLE DIFFERS` on a control set, i.e. the library's table does not hold the row the stored measurement was taken on; the entry is rewritten with the new parameters **and** the new sigma, never re-pointed. About two hours on the 8-core box |
| `scripts/dse-tools/control_rebaseline.py LOG [--pin SHA] [--write]` | turns that plan's log into the new `dse_measured.CONTROL`: parameters from the live table, sigma pooled over the two arms, and three refusals (a wrong answer, arms that disagree beyond 3 sigma with per-key scatter, a model error over the control's 6 percent). With `--write` it rewrites the block, its pin and date, reloads and runs the control; its exit status is the control's |
| `scripts/dse-arms/keylayout-cells.sh` | designed cells that make a key-layout noise change visible: b_KS 4 to 16 turns a 0.2 percent shift into 3 to 7 percent, plus a b_KS 512 control. Run at both pins, compare with `scripts/dse-tools/keylayout_fit.py OLD.log NEW.log` |
| `scripts/dse-arms/gatetime-ab.sh NEW_REF OLD_REF` | gate time for the same sets at two pins **on this machine**, since cost cells are per machine and "the library says nothing moved" was measured on another CPU. Runs on the host (it drives two images), takes `REPS`, times explicit geometry resolved once at the newer pin's table (`GEOM=named` times `-p NAME` as each pin ships it instead), interleaves the pins with alternating order so the box's drift cancels, reports key sizes as the check that the pin move landed, and refuses to print ratios if any run produced no timing. Read with `scripts/dse-tools/gatetime_ab.py`, which takes the newer pin from the arm's header |
| `scripts/dse-arms/oos.sh` | times the shipped sets; the out-of-sample check on the cells' form, and the first thing to run after a pin move. Ends `OOS DONE` |

Both arms are the sole job on the machine while they run
([measurement-practice.md](measurement-practice.md)).

## Helpers for a recalibration

Small tools that exist because a pin move needs them; each takes `-h`.

| script | what it does |
|---|---|
| `scripts/dse-tools/apply_cells.py FIT --regime R [--comment NOTE] [--merge] [--update-regime]` | pastes a `costfit --emit` block into one `_CELLS_*` table of `dse_model.py` and prints the block it replaced, so the old numbers land in the transcript. `--merge` keeps the cells the fit does not mention, for an arm that measured one method, and appends to the comment which methods it describes. `--update-regime` also writes that regime's per-method provenance record -- pin, date, timing count from the fit's own `pts` column, box from `lscpu` -- so `points`, `measured`, `box` and `pins_by_method` follow the measurement instead of being kept by hand; it reports a pin move rather than rewriting `carried_why` |
| `scripts/dse-tools/maps_fit.py LOG [--baseline model]` | does a two-base map cost its per-index digits or the widest width? Fits each cell on its single-base rows (or takes the stored cell with `--baseline model`, for a `MAPS_ONLY` log) and prints the residual of the per-index, widest-width and mixed models on the map rows, with the fitted width share `c4/c2` |
| `scripts/dse-tools/compare_cells.py FIT --regime multi/libomp` | the current cells against a fresh fit, as predicted microseconds at reference configurations and a ratio, so a pin move reads as time rather than coefficients |
| `scripts/dse-tools/pick_cpus.py N` | the `taskset -c` list for a timing run: N physical cores inside one NUMA node, one per core so hyperthread siblings cannot halve the core count behind your back. Refuses rather than spanning nodes. `recalibrate.sh` calls it; `--sysfs-root` lets a test drive a recorded topology |
| `scripts/dse-tools/resid_table.py LOG [METHOD]` | residual percent by `(n, digit count)` per cell, so a worst case can be located instead of read as one number |
| `scripts/dse-tools/gate_expect.py NEW.log --out FILE` | the model's predicted sigma ratio per gate row across a pin's key-layout flags, for `gate_compare --expect-file`. Needed only when a pin changes a noise term ([measurement-practice.md](measurement-practice.md#correctness-gate-before-calibration)) |
| `scripts/dse-tools/gate_compare.py OLD.log NEW.log [--expect-file F]` | the correctness-gate verdict after a pin move: geometry rows as a ratio to the previous pin with a z-score that includes sampling AND per-key scatter; named-set rows as within-pin `_off`/`_on` A/B (their parameters may have changed); exit 0 on PASS. Standard library only, so it runs on the compute box |
| `scripts/dse-tools/score_legacy.py LOG` | scores the model against every candidate a selector run measured, flagging rows at the wrap ceiling and rows the gates would refuse. [legacy-flow.md](legacy-flow.md#as-a-control-for-the-search-based-flow) |

## Tests

```
sage -python tests/test_dse.py   # inside the container
python3 tests/test_dse.py        # on a host that has numpy and scipy
```

Prints `all checks passed`. The suite reaches `dse_model` and so needs SciPy,
which in the image lives in Sage's python and not in the bare one.

About two hundred checks over the model, the constraints, the gates, the
fitter, the regimes, the security cache, the verifier, the key-cap policy and
the known-answer control. They are assertions about
recorded evidence, not unit tests of arithmetic: several fail if a pin stops
looking like a full commit sha, if a regime's cells go missing, if the
two-sided comparison is weakened, if the streaming frontier disagrees with the
batch one, or if the certified quantile stops charging its penalty. No numpy
beyond what the models need, no container.
