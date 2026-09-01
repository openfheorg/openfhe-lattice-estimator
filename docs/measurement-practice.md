# Measurement practice

Everything in this repo that is evidence comes from a container running the
harness binaries. These are the rules for making those runs mean something,
with the measurement behind each.

## Timing wants an idle machine; noise does not care

Anything measuring gate time measures wall clock, and another job on the box
makes the numbers wrong rather than noisy: a contaminated box once spread
repeated timings of one setting by 15 to 359 percent against under 0.7 percent
clean. So:

- **Timing arms** (`gatecost-regime.sh`, `oos.sh`, any `boolean_estimate_time`
  run) are the sole job on the machine, and on anything larger than a
  single-socket desktop they are **pinned**. Idle is not enough: eight OpenMP
  threads left to the scheduler on a 72-core box land somewhere different every
  run -- one socket, split over two, scattered across four sub-NUMA domains -- and
  the bootstrapping key is faulted in by whichever thread touches it first, so a
  placement that separates the threads from that memory pays remote access on
  every gate. The result is several values rather than scatter around one:
  measured, GINX intra-run skews of 163 to 385 percent under the scheduler on a
  72-core box and none under a cpuset, and cost-fit residuals of 3 to 11 percent
  unpinned where a pinned single-socket box gives 2 to 3, with `c3` terms failing
  to resolve because they come from matched pairs.

  `recalibrate.sh` pins for you and records the binding;
  `scripts/dse-tools/pick_cpus.py N` prints the list it uses, which is N physical
  cores inside one NUMA node. **One CPU per physical core** is the other half of
  it: taking the first N entries of a node's CPU list takes hyperthread siblings
  wherever the enumeration interleaves them, so a request for 8 becomes 4 cores
  at 2:1 oversubscription, and that reads as a large regression that is entirely
  an artefact of the binding. State the thread count and the binding with any
  many-thread figure.
- **Noise runs** are one single-threaded process per key, several in parallel
  ([verification.md](verification.md#one-key-per-process)). Keys parallelise
  fully where OpenMP inside a gate does not: on the 8-core box a STD128 key of
  1250 gates takes 23 s in one 8-thread process and 44 s single-threaded, and
  eight single-threaded processes side by side finish eight keys in 46 s
  against 185 s serially, 4.0x (2.3x on 100-gate keys, where key generation is
  most of the run). The noise does not depend on what else is running.
  `JOBS=<n> bash scripts/run-plan.sh plan.cmds` runs n keys at once with one
  OpenMP thread each, inside a memory budget (`MEM_GIB`, default three quarters
  of what is available) sized from the `# key_mib` hint each plan block
  carries; `JOBS=1`, the default, is the serial path. Pass `-Z` to skip the
  key-size report: serializing a 64-bit key to measure it is a transient the
  size of the key itself.
- Never measure noise without the failure count. `run-plan.sh` records
  `FAILURES=` from the binary's own `Failures:` line, which is on stdout while
  the noise values are on stderr; a runner has to keep both streams.

Speed and noise are therefore two measurements per candidate, in opposite
setups, and the `speed:` and `noise:` lines of the legacy selector, or the cost
arm and `run-plan.sh` in the search-based flow, are where each comes from.

## Min-of-N versus the mean

`boolean_estimate_time` times 8 gates and the cost cells rest on their mean. At
saturating thread widths both OpenMP runtimes show intra-run skew of 148 to
292 percent in a few cells (one slow gate in eight, the minimum untouched), and
on a 36-core machine 620 to 1170 percent in every leg. For a thread-sweep or a
runtime comparison the **min** is the right statistic, and the harness reports
per-gate min, mean and max so a tail effect is distinguishable from a moved
floor. Never measure a threaded regime only at threads equal to cores; sweep
it. The libgomp cliff hid on every box with more cores than the thread count.

## The security cache is measured data living in the source tree

`scripts/paramsestimator/dse_security_cache.json` is git-tracked, and pricing a
dimension writes into it. That makes `rsync scripts/ llserver:.../scripts/` a
destructive operation for it: the sync carries the local copy over whatever the
remote has just measured, and the loss is silent -- the next search simply reports
`security (unpriced)` again and looks like the original problem.

Measured: dimension 4096 was priced on llserver, the scripts directory was synced
before the next run, and the search reproduced its pre-pricing numbers exactly
(GINX 0 survivors, dominant refusal `security (unpriced)`), which cost a 35-minute
search and read as a modelling result rather than a clobbered file.

So pricing has three steps, not one:

```
price on the machine with Sage
scp the cache back to the working tree
commit it
```

Only then is a sync safe, because the local copy is the one holding the new rows.
The same applies to anything else generated into `scripts/` -- the cache is the
only such file today.

## Memory binds before CPU

A key-switching key runs to hundreds of MB and past 30 GB at the largest
dimensions, and each parallel noise process holds its own. `run-plan.sh`'s
fan-out books each block at its `# key_mib` hint plus a quarter and half a GB,
against `MEM_GIB` (default three quarters of `MemAvailable`, or of the cgroup
limit when that is smaller), and a block too large for the budget on its own
runs alone. The legacy selector's fan-out is sized from the key material the
speed probe reported and from `MemAvailable`; `ESTIMATOR_MAX_PARALLEL`
overrides it. The container has
no memory limit by default, so it may use all of host RAM, and without a limit
an over-eager fan-out is a host-wide OOM rather than a container-level one; on
a shared machine set `mem_limit:` in `docker-compose.yml`, which the fan-out
honours rather than fights. The search itself can run out of memory too: a
batch frontier over the 56 million survivors of a STD128 multi-base target
search does not fit, which is why the front is streamed.

## Not on the workstation

Anything longer than about a minute, or that walks the whole grid, runs on a
compute box, not on the machine someone is working at. Several multi-hour
frontier runs and searches launched as local background jobs made a
workstation unusable for over an hour. Pure-Python enumerations are not
harmless: they hold hundreds of thousands of candidate dicts and run for hours.
If the compute box is busy, ask before running locally, and say how long it
will take.

## Rebuild before you run

After syncing a working copy to the compute box, `docker compose build` before
running anything: a plain `docker compose run` otherwise uses the previously
baked image and silently tests stale code. The `docker-compose.dev.yml` path
bind-mounts the working copy and its entrypoint rebuilds the harness on every
start, so an edited `.cpp` is always current there; but a leftover `build/`
configured against a different OpenFHE is discarded rather than linked, so
expect a reconfigure the first time.

Never overwrite a bind-mounted script while a container is executing it: bash
re-reads the file at its old offset and the tail runs corrupted.

## Plans, caches and syncing

Sync the working copy to the compute box with `rsync -a` from an absolute
source path, excluding `.git`, `build`, `__pycache__` and the table output
directories, and **without `--delete`**: the remote tree holds things the local
one does not (the harness build, plan files, table outputs), and a `--delete`
from a checkout that lacks `build/` removes the compiled harness. Never pipe
rsync into `head`: the pipe closing kills the transfer part-way and leaves a
tree that is half one version and half another. Check with `rsync -n` when in
doubt. Never sync over a tree a container is running from
([Rebuild before you run](#rebuild-before-you-run)).

Keep measurement plan files either outside the synced tree, mounted read-only
(`-v ~/plans:/plans:ro`), or in a directory the sync excludes; a plan that a
sync removes leaves its consumer reading "0 of 0 rows FAILURES=0", which the
gate refuses, and the chain stops hours later than it should have.

The security cache is the file that lives in the tree and gains entries on the
compute box: every legacy-selector run and every `dse_security.py boundary`
writes into it. **Copy the cache back before syncing over it**, or the pricing
is lost.

## Chains and markers

A multi-stage job is "not running" between every one of its stages, and any
pattern match against process metadata is at the mercy of formatting: `docker
ps` truncates the command column, and a waiter on "no `reg_` container" exited
in the gap between two calibrations and put a noise run inside a timing window.
Absence of a process is not evidence of completion. Gate instead on a
**completion marker the producer writes** and the consumer greps:

| producer | marker |
|---|---|
| `scripts/dse-arms/gatecost-regime.sh` | `GATECOST DONE` |
| `scripts/dse-arms/oos.sh` | `OOS DONE` |
| `scripts/dse-arms/keylayout-cells.sh` | `KEYLAYOUT CELLS DONE` |
| `scripts/dse-arms/gatetime-ab.sh` | `GATETIME AB DONE` |
| `scripts/run-plan.sh` | `PLAN DONE`, with the record count |
| `dse.py search` / `dse_beats` | the `saved ... candidates` or `NOTHING beats` line |

**A marker has to be earned.** `run-plan.sh` checks its plan before the first
run, counts the records against the command lines, and fails when every record
comes back `sigma=NA`, which is what a rejected flag or an OpenFHE without
`WITH_NOISE_DEBUG` looks like from there. A marker printed unconditionally is
worse than no marker: a chain gating on one reads "0 of 0 rows, FAILURES 0" as a
pass and spends the night on nothing.

Hold the arms you write to the same rule, because a stage that reports success
while measuring nothing is the one failure a measurement runner must never
produce. Two ways to get there: pass a flag the binary does not have, and every
run prints a usage message while the loop completes and the log fills with
errors; or name a parameter set the harness cannot resolve, and the context
throws after the loop has already logged that set as started. Both are cheap to
guard against -- count what you measured and compare it to what you asked for.

Pair the waiter with a fail-fast check that also breaks on a fatal log pattern
or on a vanished **named** container (`docker run --name ...`; an unnamed
container orphaned by a killed ssh client competes silently and cannot be
attributed), so a dead job does not spin until morning. Two shell traps that
have each cost a night: `grep -c X` exits 1 on a zero count, so
`$(ssh "... | grep -c X" || echo 1)` turns a real 0 into `0\n1` (use `grep -c
X; true` remotely); and a `kill` loop that matches a substring of its own
command line kills the shell running it (match on the process's argv prefix).

`nohup docker compose run ... &` leaves the container created but never
started, because it wants a TTY it cannot get. Use `docker run -d --name X` and
poll `docker inspect -f '{{.State.Status}}' X`; Python buffers stdout when not
on a TTY, so expect nothing in `docker logs` until it exits.

## Correctness gate before calibration

After every pin move, before spending machine time on cells, run the
correctness gate:

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
    estimator bash scripts/dse-arms/pinmove-gate.sh gate-<previous pin>.log
```

which is the same 20 rows each time (new-path LMKCDEY rows at
digitsG2 10 and 16, the mixed logQ-37 form, named-set A/Bs with `-6` for the
off arms), and stop unless every row reports `FAILURES=0` and every sigma is
within sampling of the previous pin. Noise has survived every pin move and cost
has survived none, so the gate is cheap insurance that the expensive step is
measuring the right thing. At `41709fbc`: 20 of 20 rows, zero failures, every
comparison within |z| 2.7.

The gate's rows are of two kinds, and only one compares across pins. The
explicit-geometry rows (`newpath_*`, `mixed_*`) measure the same configuration
at both pins and are compared as a ratio to the previous pin's log with a
z-score on the difference, `|z| < 3` at the recorded sample count. The `ab_*`
rows are `-p <named set>` runs, and a named set's parameters belong to the
library's table, which can change between pins (at `41709fbc` the table is this
tool's own re-selected output, so `STD128` is `n=554 {128:304, 512:250}` where
the table before it had `n=556 {128:556}`, and its sigma is 18.5 against 13.8
with nothing wrong). Those rows are checked **within** the pin -- the
`_off`/`_on` pair is the A/B of the 64-bit against the 32-bit key forms -- and
against the model on the *live* table's geometry (`dse.py validate` does this).

`scripts/dse-tools/gate_compare.py OLD.log NEW.log` applies both rules and exits
0 on a pass, so a chain can launch the cost arm on it. Its z-score includes the
per-key scatter as well as sampling, because two single-key runs of one
configuration differ by both; sampling alone over-flags a 3 percent off/on
difference that is within the per-key scatter.

Two things the geometry rows need when the pin **changes** a noise term, which
is not the usual case but is what `94229558` does. First, the expectation is the
predicted ratio, not 1.0: `scripts/dse-tools/gate_expect.py NEW.log --out
expect.txt` prints the model's ratio per row across the layout flags, and
`gate_compare --expect-file expect.txt` scores against it. Holding a row to 1.0
across a pin that legitimately moves its sigma asks the wrong question. Second,
the rows have to be able to see the shift. The stock plan runs `-i 200 -K 2`,
400 pooled gates, about 5 percent of 1-sigma on a ratio, which is right for
catching a broken path and cannot resolve the 0.5 to 1.3 percent this pin
predicts: `gate_compare` prints each row's own 1-sigma and marks a row that
cannot confirm its expected shift. That marking never excuses a deviation, since
a coarse row still detects a large move, and the summary prices a lone
exceedance against chance -- one row of twelve at 3 sigma is a 31-to-1 draw. A
row whose verdict has to mean something gets more keys and samples.

The baseline is a measurement of the same power, so it can sit a few percent
high or low as a whole. When every geometry row leans the same way against the
previous pin's log, compare against the log two pins back as well before
reading a shift into a pin that touches no noise term: `12858277` read 4
percent low against `94229558`'s log, which had itself read 3 percent high
against `41709fbc`'s, and sits within 2 percent of `41709fbc`'s with every row
inside 2.1 sigma. The logs are kept for exactly this (`gate-<pin>.log` in the
repo root, written by `pinmove-gate.sh`).

## The harness compiler

The harness is built with the same compiler as OpenFHE, because a regime
labelled "clang" should not be half gcc. The harness compiler does not affect
gate time -- the timed region is eight straight-line library calls across a
shared-library boundary with no harness arithmetic, checked by measurement --
but it would the moment anyone puts hot code in the harness. One checkout mounted
into two images needs `rm -rf build` between them, because the entrypoint only
reconfigures when the cmake cache is absent and both images install to the same
path.

## Same machine, same flags

`WITH_NATIVEOPT=ON` bakes in `-march=native`. Build on the machine you will
measure on, or the images are not comparable, and never carry a cell from one
CPU to another: the shipped cells describe a single-socket i7-9700 and nothing
else ([cost-model.md](cost-model.md#regimes)).

## Two draws are not a trend

The harness has no drift beyond sampling: 28 sets measured twice in different
sessions with different keys differ by a mean |z| of 0.83 against the 0.80
expected for pure sampling noise. That is what licenses comparing a
measurement taken today against one taken days earlier. It also means a
one-sigma hint from a small sample is a hint: a first pass of six sets across
one pin move read +1.74 ± 0.83 percent, and a paired A/B on four sets with
40 000 samples read +0.36 ± 0.79. Raise the sample count before believing a
small difference, and never size an experiment with the model being
calibrated: two calibration rungs saturated because of it.
