# Getting started

A cold start: nothing cached, nothing installed, no OpenFHE on the host. This is
everything between `git clone` and a ranking you can trust, with the exact
commands, including the one step most people skip (phase 6). Times are from an
8-core i7-9700 with 62 GB of RAM; the outputs shown are what that machine
prints at OpenFHE pin `12858277`.

## What is already in the repo, and what you must produce

You get these for free:

- **Security boundaries.** `scripts/paramsestimator/dse_security_cache.json`
  holds 2 037 priced dimensions over 26 curves, plus 169 priced security
  boundaries on the 13 curves that have them, computed against
  lattice-estimator `53da5982`. Hours of Sage work, already done, and the
  legacy selector reads the same file. See [security.md](security.md).
- **The noise model.** Pure arithmetic, validated against every production
  set. Hardware-independent. See [noise-model.md](noise-model.md).
- **Cost cells** for four (thread mode, OpenMP runtime) regimes in
  `dse_model.py`, measured on one specific machine. Read phase 6 before using
  them.
- **No submodules.** The clone is complete; the image fetches OpenFHE and the
  lattice-estimator itself.

These are yours alone:

- **The image.** One build, and the only genuinely slow step.
- **Gate-cost cells for your machine.** The shipped ones describe someone
  else's CPU.
- **Measurements** of whatever candidates the search hands you.

Phases 1 to 4 are setup and happen once. After them you have a choice: the
guided flow below runs phases 5 to 7 from a few questions, and the phases
themselves are the same commands written out, for when you want to see or
change a step.

## Phase 1: host prerequisites

Docker Engine with the Compose v2 plugin, git, and nothing else. No OpenFHE, no
SageMath, no numpy on the host.

```
docker --version          # Engine 24 or newer
docker compose version    # must say v2.x; the old python docker-compose cannot read this file
git --version
nproc; free -g; df -h .   # cores, memory, disk
```

- **Memory.** `MAKE_JOBS` defaults to 4. Some OpenFHE translation units peak
  near 2 GB of RSS, so budget about 2 GB per job: 8 jobs want about 16 GB free.
  An unbounded `-j$(nproc)` is how a many-core machine runs out of memory here.
- **CPU.** `WITH_NATIVEOPT=ON` compiles `-march=native`, so the image only runs
  on a CPU at least as capable as the one that built it (otherwise SIGILL).
  Build on the machine you will measure on, or export `WITH_NATIVEOPT=OFF` for
  a portable, slower image.
- **Disk.** The SageMath base image is about 3 GB to pull and the finished
  image is 5.6 GB; keep about 12 GB free for intermediate layers.
- **Who runs docker.** Your user must be in the `docker` group, or prefix every
  command with `sudo`.
- **Your uid.** The container runs as uid 1000 by default, and with your
  checkout bind-mounted it has to be able to write it. Write your own uid into
  `.env` once, in the repo root, and every `docker compose` command here picks
  it up -- build and run alike, in this shell and every later one:

  ```
  printf 'APP_UID=%s\nAPP_GID=%s\n' "$(id -u)" "$(id -g)" > .env
  ```

  Skipping this is the most common first failure: the entrypoint stops with
  `ERROR: /workspace is not writable by uid 1000`. `.env` is gitignored, since a
  committed uid is one machine's and wrong everywhere else. Exporting the two
  variables works too, but only for the shell that exported them.

## Phase 2: clone, and read the pin

```
git clone https://github.com/openfheorg/openfhe-lattice-estimator.git
cd openfhe-lattice-estimator
grep -n OPENFHE_REF Dockerfile docker-compose.yml
```

```
Dockerfile:161:ARG OPENFHE_REF=128582771b50ce798bfa36d63e43b550b1b17ea0
docker-compose.yml:16:        OPENFHE_REF: ${OPENFHE_REF:-128582771b50ce798bfa36d63e43b550b1b17ea0}
docker-compose.yml:29:    image: openfhe-lattice-estimator:${OPENFHE_REF:-128582771b50ce798bfa36d63e43b550b1b17ea0}
```

That commit is the OpenFHE the image will build. It appears three times on
purpose: the Dockerfile default only applies to a plain `docker build`, compose
always passes its own, and the third use makes the image tag track the ref.
They move together or the tag lies. [image-and-pins.md](image-and-pins.md)
explains why it is a `dev` commit and not a release tag.

## Phase 3: build the image

The OpenFHE compile is about a minute at `MAKE_JOBS=8` (measured 62.5s on the
machine above, 1m26s for the whole build), plus the 3 GB base-image pull and the
apt layer the first time, which together dominate a genuinely first build.

```
MAKE_JOBS=8 docker compose build --progress=plain 2>&1 | tee build.log
```

`--progress=plain` matters: BuildKit's default view collapses each step's
output once it succeeds, and the lines you most want to keep are inside steps.

A clean build logs no CMake warnings. If you see this one:

```
CMake Warning:
  Manually-specified variables were not used by the project:
    CMAKE_C_COMPILER
```

the image predates the fix for it and a rebuild clears it. It is harmless either
way, and it means what it says: the harness declares no C language and has no C
source, so a `-DCMAKE_C_COMPILER` is never consulted. Neither the harness build
in the image nor the entrypoint's reconfigure passes it; the OpenFHE stages do,
because OpenFHE's build compiles C and uses it.
Check them when it finishes:

```
$ grep -E "compiler identification|NATIVE_SIZE:|pinned OpenFHE" build.log
-- The CXX compiler identification is Clang 18.1.3
-- NATIVE_SIZE:            64
pinned OpenFHE: 128582771b50ce798bfa36d63e43b550b1b17ea0  (grafted, HEAD) Adds Fourier-Extension Functional Bootstrapping (#1276)
$ docker images openfhe-lattice-estimator
REPOSITORY                  TAG                                        SIZE
openfhe-lattice-estimator   128582771b50ce798bfa36d63e43b550b1b17ea0   6.86GB
```

Those three lines are printed by the layers that produce them, so a **rebuild
that hits the cache does not replay them** and only the `pinned OpenFHE` grep
pattern inside the RUN instruction will match. Two checks work regardless: the
image tag tracks the ref, and the build writes the resolved commit into the
image, where `dse.py doctor` reads it back (phase 6). Prefer those to the log.

One command, four stages, each cached independently afterwards:

1. Pull `sagemath/sagemath:10.9`, about 3 GB. On a first build this normally
   takes longer than all the compilation.
2. `openfhe-base` installs clang-18, gcc-14 and cmake, then runs
   `git fetch --depth 1 origin <OPENFHE_REF>`: a shallow fetch of one exact
   commit, plus submodules.
3. `openfhe-ns64` configures OpenFHE with `NATIVE_SIZE=64`,
   `WITH_NOISE_DEBUG=ON` and the chosen compiler, builds, installs to
   `/opt/openfhe`, and deletes its build tree.
4. `estimator` copies that install onto a clean Sage base, clones the
   lattice-estimator at `53da5982` and strips its `.git`, runs `ldconfig`, and
   builds the harness (`build/`) with the same compiler. It also carries one
   source file from the OpenFHE checkout, `binfhecontext.cpp`: the library's
   parameter table is read from it rather than transcribed, so the tools that
   compare against it work on a machine with no OpenFHE checkout.

It fails here if the pinned commit is not fetchable (`upload-pack: not our
ref`, a layer deep; a force-pushed commit usually still resolves, so this fires
for one that was never pushed at all), if Docker Hub answers
`TLS handshake timeout` while fetching the BuildKit frontend (transient: rerun
the same command), or if the compiler is killed at a high `MAKE_JOBS` (that is
memory; lower it).

## Phase 4: first run, and where your files live

**Two shells, and the difference matters throughout.** `docker compose` runs on
the **host**, in your checkout. It starts a container whose shell sits in
`/workspace`. Mixing them up is the first thing that goes wrong: a `docker`
command typed inside the container answers `bash: docker: command not found`,
and a `sage -python` typed on the host has no Sage. The rule for this page, and
for every other: **each command here runs on the host**, and the ones that need
the container reach into it through `docker compose` or the `est` alias below.
The only commands you type at a container prompt are the ones you get to by
opening a shell there yourself, and those are written without a prefix.

On the host:

```
docker compose run --rm estimator
```

gives a bash prompt in `/workspace`, the repo root inside the container, and
that container is where every measurement runs. Four things happen before the
prompt appears:

- **Noise-debug check.** The entrypoint greps the installed
  `OpenFHEConfig.cmake` for `set(OpenFHE_NOISEDEBUG "ON")` and refuses without
  it. That flag is what makes OpenFHE emit the noise values these scripts
  parse; without the check you get a `statistics.stdev()` traceback on an empty
  file, hundreds of lines from the cause.
- **Writability check.** The container's uid must be able to write
  `/workspace`, because a bind mount owned by another uid otherwise fails as an
  opaque CMake "pkgRedirects" error.
- **Configure** if, and only if, the cmake cache is absent.
- **Always build.** Incremental, so it costs a second when nothing changed, and
  it is the only thing that picks up an edited source file; a build guarded on
  the binary already existing would run a stale binary against new sources.

**Files written in a plain run vanish.** `/workspace` there is the image's own
copy of the repo, and `--rm` discards it with the container, so a `picks.json`
saved by `search` is gone before `plan` can read it. Anything multi-step wants
your checkout mounted instead, which is what `docker-compose.dev.yml` does.

Leave that container first -- `exit`, or Ctrl-D -- because the next two lines are
**host** commands. The alias is a host alias; run it inside the container and you
get `bash: docker: command not found`:

```
exit                       # back to the host, if the container shell is still open
alias est='docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm'
est estimator              # a container shell again, but /workspace IS your checkout now
```

`est` takes a command as well as giving you a shell, and that is how the rest of
this page uses it: `est estimator sage -python ...` runs one thing and exits.
Both forms are host commands. **If you are already at a container prompt, drop
the `est estimator` prefix and run the command by itself** -- `sage -python
scripts/paramsestimator/dse.py doctor` rather than `est estimator sage -python
...`.

The first start with the mount recompiles the two harness binaries (a few
seconds): the mount shadows the image's prebuilt `build/`, and this is the
"always build" above earning its place.

If this stops with `ERROR: /workspace is not writable`, read the two lines under
it: they name the uid the container runs as and the uid that owns the mount, and
the fix depends on which is wrong.

- **The owner is your uid and the container is something else.** The `.env` from
  phase 1 is missing or was written somewhere other than the repo root. Write it
  there and re-run.
- **The owner is uid 0.** The checkout is root-owned, so no ordinary uid can
  write it and matching `APP_UID` to your own changes nothing. On the host,
  either `sudo chown -R $(id -u):$(id -g) .` or run the container as root with
  `APP_UID=0 APP_GID=0`.
- **The owner is some third uid.** The checkout belongs to another account and
  wants chown-ing before anything else will work.

The image also bakes a user at `APP_UID`, so a `.env` written before phase 3
keeps the built image and the runtime uid consistent. Writing it afterwards
still clears the check, and a rebuild is only needed if something later
complains about a home directory.

### Use `sage -python`, never `python3`

Inside the container every script runs under `sage -python`. `dse_model`
imports `scipy.special.erfcx`, which the container's bare `python3` lacks, and
`binfhe_params_helper` does `from estimator import *`, which needs SageMath.
Do not work around that by putting Sage's venv on `PATH`: importing `sage.all`
from a python without Sage's environment retries its interface subprocesses
without bound, a fork storm rather than a slow import. Sage's `bin` is kept off
`PATH` deliberately, and the container sets a `pids_limit` as a second net.

## The guided flow: phases 5 to 7 from a few questions

```
est estimator sage -python scripts/paramsestimator/dse_wizard.py
```

It asks which bootstrapping method, which security level, how many gate inputs,
what failure probability, how much key material you can hold, whether to
recalibrate the gate-cost cells for this machine, which thread regime, and how
many keys to measure. Each question comes with one line on what the answer
changes, and every default is the one the phases below use. Then it runs, in
order and printing each command first:

```
dse.py doctor                        phase 5: is this checkout usable, and for this method
recalibrate.sh + apply_cells.py      phase 6, only if you said yes -- or if the method has
                                     no cost cells in this regime, when it is not optional
dse_table.py plan                    phase 7: search that one cell, select under the key cap
run-plan.sh                          phase 7: measure -- the only step that runs the library
dse.py table finish                  phase 7: certify, and print the row and label to paste
```

The compiler is not asked. The image decides it, and the wizard reads which
OpenMP runtime the harness linked rather than trusting anyone to remember;
a ranking for the other runtime needs the other image (phase 6).

Three things make it usable unattended. Every answer is a flag, and `--yes`
takes the default for whatever is not given, so a shell script can drive it.
`--dry-run` prints every command it would run and runs nothing, which is also
the fastest way to learn the manual flow. And it resumes: answers go to
`wizard.json` in the output directory, and a second run there skips the search
if the plan exists and the measurement if the log has records, so a run
interrupted after the expensive step does not repeat it.

What it does not do is decide for you when the measurement is inconclusive. A
`MORE KEYS` verdict means the label is limited by measurement rather than by the
parameters; re-run with a larger `--keys` in the same directory after deleting
`plan.log`, exactly as the manual flow would.

## Phase 5: `dse.py doctor`, before trusting anything

```
$ est estimator sage -python scripts/paramsestimator/dse.py doctor
modules
  ok      dse_model
  ok      dse_constraints
  ok      dse_enumerate
  ok      dse_security
  ok      dse_verify
  ok      dse_validate
  ok      dse_measured
  ok      dse_shipfit

installed OpenFHE
  ok      12858277  Adds Fourier-Extension Functional Bootstrapping (#1276)
  ok      the active regime's cells describe THIS library
          measured at AP 238153db, GINX 238153db, LMKCDEY 41709fbc
          carried: c4180d75 and 94229558 change key layout only, gate times measured unchanged at 1 and 8 threads; 94229558 -> 12858277 leaves the accumulator and key-switch loops unchanged (two per-gate guards, a keygen fold, serialization, the table itself), gate-time A/B on this box in COST_PIN_CONTAINS

cost model
  ok      12 of 12 (method, N, word) cells present
  ok      6 of 6 AP cells present (c3 there is the per-product cost)
  note    2 LMKCDEY cells price numAutoKeys at zero gate cost: the term measured below the 125 us quantum (< 170 us/gate at w=10)
  ok      GATE_COST: 1423 timings describing OpenFHE 12858277, clang-18 (libomp) (8 threads), multi, single-socket i7-9700, 8 cores, 1 NUMA node (2026-09-09)
  measured at: AP 238153db (563 timings, 2026-09-07), GINX 238153db (330 timings, 2026-09-05), LMKCDEY 41709fbc (530 timings, 2026-09-09)
  carried to 12858277: c4180d75 and 94229558 change key layout only, gate times measured unchanged at 1 and 8 threads; 94229558 -> 12858277 leaves the accumulator and key-switch loops unchanged (two per-gate guards, a keygen fold, serialization, the table itself), gate-time A/B on this box in COST_PIN_CONTAINS
  ok      regime multi /libgomp 12 cells, 660 timings (GINX, LMKCDEY)
  ok      regime multi /libomp 24 cells, 1423 timings (AP, GINX, LMKCDEY)
  ok      regime single/libgomp 12 cells, 660 timings (GINX, LMKCDEY)
  ok      regime single/libomp 12 cells, 660 timings (GINX, LMKCDEY)
          not represented: many-core deployments ...
          not represented: n = 64 for LMKCDEY ...
          not represented: AP's tail, and its N=2048/w32 degeneracy ...
          not represented: single slow rows ...

security boundaries
  ok      26 keys, 2039 priced dimensions
  ok      priced against estimator 53da5982597709ba0fdf94ea37a84d822310fd84

shipped-set table (dse_beats and the shipped-set controls read it from OpenFHE's source)
  ok      /opt/openfhe/share/openfhe-src/binfhecontext.cpp (112 sets)
  ok      known-answer control: 4 live sets (parser + model), 5 historical (model)
  ok      pre-re-selection table fixtures/binfhecontext-238153db.cpp (45 sets; the --key-cap-mult reference)

measurement harness (only needed for step 4)
  ok      build/bin/boolean_noise_estimate_script
  ok      build/bin/boolean_estimate_time

checkout looks usable.
```

The **installed OpenFHE** block is the library you are actually linked against,
read from a file the build wrote, and the lines under it compare that commit
against the pin the active regime's cells describe. Where the cells were timed
at an earlier commit and carried forward, it names the commits they were
measured at and what the carry rests on; where they describe another build
entirely, it says that instead:

```
  note    the active regime's cells were measured at a0c3f2cd, not on
          this library. Gate times from them describe a different
          build. ...
```

which is the expected answer for the two single-thread regimes, and their table
entry records why that is deliberate.

Read the cost-model block twice. The provenance line names the machine and the
OpenFHE commit the timing constants were measured on. On the machine this page
was written from they match the build; on yours they will not. A regime whose
cells are knowingly behind the pin prints a `STALE` line saying what is stale
(multi/libgomp's two-base LMKCDEY rows). Either way the message is the same:
**the timing constants are not yours yet.** Security and noise carry over,
because they are arithmetic.

## Phase 6: recalibrate for your hardware (the skipped step)

Three commands, one of them long, on an otherwise idle box. Not optional
for rankings.

The shipped cost cells were measured on a single-socket i7-9700 with 8 cores
and one NUMA node, at one OpenFHE commit, with one compiler, at one thread
count. On your machine they describe someone else's CPU, and every gate time,
speedup and frontier position depends on them. Nothing else may run on the
machine while these do: a busy box once spread repeated timings of one setting
by 15 to 359 percent.

**Three commands.** The first is the long one; steps 2 and 3 are printed for
you, with your paths, pin and box already filled in, when it finishes:

```
# 1. measure and fit
est estimator bash scripts/dse-arms/recalibrate.sh recal-mine

# 2. adopt -- one per regime measured, cells + provenance + per-method record
est estimator python3 scripts/dse-tools/apply_cells.py recal-mine/multi.fit.txt \
    --regime multi/libomp --comment recal-mine/provenance.txt --merge \
    --update-regime --pin <commit> --box "<machine>"

# 3. check
est estimator sage -python tests/test_dse.py
est estimator sage -python scripts/paramsestimator/dse.py doctor
```

The rest of this phase is what those do, and the knobs that change them.

**Step 1 measures GINX**, the method `dse.py search` selects by default: its
single-base sweep over both word sizes, the GINX control on two-base gadget
maps, the single-thread regime, and a fit per regime. It is one command because
the order and the environment of those stages are not judgement calls, and
getting them wrong costs hours of machine time.

**One method, because calibration is per method.** A cost cell is keyed by
(method, N, word size) and nothing extrapolates across methods, so a GINX run
gives you correct GINX gate times whatever else the table holds -- and says
nothing about the other methods, whose cells go on describing the box named in
their provenance comment. Measure the method you intend to search:

```
METHODS=LMKCDEY      est estimator bash scripts/dse-arms/recalibrate.sh recal-lmk
METHODS=GINX,LMKCDEY est estimator bash scripts/dse-arms/recalibrate.sh recal-two
METHODS=all          est estimator bash scripts/dse-arms/recalibrate.sh recal-all
```

Names or the arm's numbers (1 AP, 2 GINX, 3 LMKCDEY). Give each method set its
own OUTDIR, as above: a stage that already ended in `GATECOST DONE` is skipped on
a re-run, and the script refuses rather than resume a directory whose finished
log covers other methods.

`all` is what this project runs before shipping a parameter table, because a
table prices every method against every other -- and it is far the most
expensive, since AP is in it. For your own parameters, one method is usually the
honest answer: `search` defaults to `--method GINX`, so a GINX calibration
matches the default flow exactly, while `search --method LMKCDEY` or any `table`
run reads cells that measurement never touched. The script says which methods it
covered when it finishes, and writes that into the provenance stub. The stages
follow from the methods: LMKCDEY brings its own two-base map rows along, AP has
none of its own.

`DRY=1` prints the stages, methods and CPU binding a run would use and measures
nothing -- a second well spent before committing the box for an evening.

**Budget hours for all three methods**, and expect AP to dominate. GINX and
LMKCDEY measure at about 2.4 seconds a row at 8 threads; an AP gate runs hundreds
of milliseconds and its key generation tens of seconds on the large rows. Of the
1423 timings behind the shipped 8-thread cells, 330 are GINX, 530 LMKCDEY and 563
AP. The script prints rows, elapsed time and seconds per row for each stage and
appends them to `timing.log`, so the number for your hardware is recorded rather
than guessed. Interrupting is safe: re-running skips any stage whose log already
ends in `GATECOST DONE`, and `STAGES`, `NS` and `WORDS` cut it down further.

**It pins the run, and letting it is the point.** On anything larger than a
single-socket desktop, eight OpenMP threads left to the scheduler land somewhere
different every run -- one socket, split across two, scattered over four sub-NUMA
domains -- and the bootstrapping key is faulted in by whichever thread touches it
first, so a placement that separates the threads from that memory pays remote
access on every gate. That is several values rather than scatter around one:
unpinned on a 72-core box the cost-fit residuals run 3 to 11 percent where a
pinned box gives 2 to 3, and some `c3` terms stop resolving because they come
from matched pairs. The script takes one physical core per thread inside one NUMA
node, prints the binding and records it in `timing.log`. `PIN_CPUS=0-7` sets it
by hand and `PIN=0` turns it off, which is right only on a single-node box with
nothing else on it.

The arm sweeps both word sizes at one commit, so the 32-bit and 64-bit cells
cannot drift apart. The fitter fits exactly the model the search evaluates,
refuses a log with mixed thread counts, and leaves a coefficient `None` rather
than fitting one the measurement never resolved.

**What step 2 writes**: the cells, the provenance comment, and the regime's
per-method record -- pin, date, timing count and box -- for exactly the methods
in the fit.
`--merge` keeps the cells this run did not measure. `provenance.txt` arrives
filled in -- box from `lscpu`, OpenMP runtime from `ldd` on the harness, pin from
the installed library, residuals parsed out of the fit -- so read it rather than
complete it; a probe that cannot answer says so in the file and on the terminal.

Then `sage -python tests/test_dse.py && dse.py doctor`, and the regime lines show
your box, your date and your timing count. The suite computes its expectations
from the cells themselves, so a recalibration does not break it.

**One judgement call is left to you, deliberately.** If your fit moved the
regime's pin, the methods you did not measure are now *carried* to it, and
`apply_cells.py` says so instead of editing the justification: only
`carried_why` can state what a carry rests on, and a carry is a claim about a
measurement nobody made.
[cost-model.md](cost-model.md#per-method-provenance) has the data model.

The runtime axis needs its own image, because OpenFHE and the harness both
have to be built with the other compiler. Not through compose, which tags by
`OPENFHE_REF` alone and would overwrite the clang image:

```
docker build -t openfhe-lattice-estimator:128582771b50ce798bfa36d63e43b550b1b17ea0-gcc \
    --build-arg OPENFHE_REF=128582771b50ce798bfa36d63e43b550b1b17ea0 --build-arg MAKE_JOBS=8 \
    --build-arg CC_BIN=gcc-14 --build-arg CXX_BIN=g++-14 .
rm -rf build               # see below
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD":/workspace -w /workspace \
    openfhe-lattice-estimator:128582771b50ce798bfa36d63e43b550b1b17ea0-gcc \
    bash scripts/dse-arms/recalibrate.sh recal-gcc
```

The same one command, and `METHODS` works the same way inside it; its fits paste
into the `libgomp` tables (`--regime multi/libgomp`, `single/libgomp`).

The `rm -rf build` is not optional when one checkout is mounted into two
images: the entrypoint only reconfigures when the cmake cache is absent, and
both images install to the same path, so a harness linked under the clang
image would otherwise be reused, unchanged, under the gcc one. Pick a regime at
search time with `dse.py --threads single --compiler gcc search ...`; a regime
nobody measured refuses every candidate rather than guessing.

## Phase 7: the pipeline

Search is minutes; measurement is the expensive part. The search below took
3m30s on the machine in phase 1, and the twelve-key measurement is the step
that costs real time. `JOBS=8` runs eight keys at once, one OpenMP thread each,
inside a memory budget the plan carries; on the 8-core box that is four times
faster than the serial run on 1250-gate keys, and the records it writes are the
same.

```
est estimator sage -python scripts/paramsestimator/dse.py search \
    --level STD128 --target -64 --security estimator --tolerance 1 --save picks.json
est estimator sage -python scripts/paramsestimator/dse.py plan \
    --picks picks.json --keys 12 --samples 1250 --out plan.cmds
est estimator env JOBS=8 bash scripts/run-plan.sh plan.cmds > plan.log   # the only step that measures
est estimator sage -python scripts/paramsestimator/dse.py decide plan.log \
    --target -64 -q 2048 --samples 1250
est estimator sage -python scripts/paramsestimator/dse.py validate plan.log
```

Only `run-plan.sh` needs the compiled library; everything else is arithmetic
and runs on any python3 with SciPy. What each step prints, and how to read it,
is in [dse-flow.md](dse-flow.md). The legacy selector, which answers a
different question, is in [legacy-flow.md](legacy-flow.md).

## Editing the source

`docker-compose.dev.yml` mounts your working copy over the image's baked-in
copy, so changes to the Python scripts and to `src/*.cpp` take effect without a
rebuild; the entrypoint's incremental `cmake --build` recompiles an edited
`.cpp` before anything measures with it. It also discards a `build/` that was
configured against a different OpenFHE (a native build left in the working
copy, say) rather than linking the wrong library.

The pure-Python parts run on the host too. `python3 tests/test_dse.py` needs
only numpy and scipy and prints `all checks passed`; every `dse.py` subcommand
except `dse_security price`/`boundary` works the same way. Anything that
imports the lattice-estimator or OpenFHE needs the container.

## Native install (without Docker)

The image exists so that none of this is necessary, but the scripts do not
require it.

1. `sudo apt install python3 sagemath` (tested with python 3.8.10 and SageMath
   9.0); `pip3 install numpy scipy`.
2. Clone [lattice-estimator](https://github.com/malb/lattice-estimator) and
   add it to `PYTHONPATH`. The security cache in this repo was priced against
   commit `53da5982`; a different estimator revision can move a boundary by a
   bit, so re-price before trusting the cache with another one.
3. Clone [openfhe-development](https://github.com/openfheorg/openfhe-development),
   check out the commit named in `OPENFHE_REF`, and build and install it with
   `cmake -DWITH_NOISE_DEBUG=ON ..`. Without that flag no noise values are
   emitted and the scripts fail far from the cause.
4. In this repo, `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build`.
   This produces `build/bin/boolean_noise_estimate_script`,
   `build/bin/boolean_estimate_time` and `build/bin/boolean_keyswitch_isolate`.
   Build directories are found relative to the repository root, so the scripts
   need not be run from it; set `ESTIMATOR_BUILD_DIR` for an out-of-tree
   layout.
5. `dse_beats` and `dse_shipfit` read the shipped parameter table from
   OpenFHE's `src/binfhe/lib/binfhecontext.cpp`. A native checkout looks for a
   sibling `openfhe-development` clone, or takes `BINFHECONTEXT_SRC`.
6. For single-core timings set `OMP_NUM_THREADS=1`.

Where the container instructions say `sage -python`, a Debian or Ubuntu host
where `apt install sagemath` put sagelib into the system python can use plain
`python3`.
