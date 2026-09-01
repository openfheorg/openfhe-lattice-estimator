# Build, image and pins

## The image

`docker compose build` produces one image, `openfhe-lattice-estimator:<OPENFHE_REF>`,
bundling every prerequisite: SageMath, numpy and scipy, the lattice-estimator,
and an OpenFHE built with `WITH_NOISE_DEBUG=ON`. Nothing has to be installed on
the host. The stages and what to check in the build log are walked through in
[getting-started.md](getting-started.md#phase-3-build-the-image).

| argument | default | meaning |
|---|---|---|
| `OPENFHE_REF` | `128582771b50ce798bfa36d63e43b550b1b17ea0` | the OpenFHE commit to build; a tag, branch or **full** sha |
| `MAKE_JOBS` | 4 | OpenFHE build parallelism; budget about 2 GB of RAM per job |
| `CC_BIN` / `CXX_BIN` | `clang-18` / `clang++-18` | compiler for OpenFHE **and** the harness; `gcc-14` / `g++-14` selects the libgomp runtime |
| `WITH_NATIVEOPT` | `ON` | `-march=native`; the image then runs only on a CPU at least as capable as the builder |
| `APP_UID` / `APP_GID` | 1000 | the uid the container runs as, so bind-mounted files stay writable |
| `LATTICE_ESTIMATOR_REF` | `53da5982597709ba0fdf94ea37a84d822310fd84` | the estimator revision the security cache was priced against |

`MAKE_JOBS=8 docker compose build` wants about 16 GB free; the binding limit is
memory, not cores. With unit tests, examples and benchmarks disabled the
OpenFHE compile is about 2 minutes on 8 cores, and on a first build the 3 GB
SageMath pull usually takes longer.

The image carries one source file from the OpenFHE checkout,
`binfhecontext.cpp`, at `/opt/openfhe/share/openfhe-src/`, and points
`BINFHECONTEXT_SRC` at it: `dse_shipfit` and `dse_beats` read the library's
parameter table from it rather than from a transcription, so they work on a
machine with no OpenFHE checkout. `doctor` reports which file it found.

At the current pin that table is the re-selected table this tool produced (109
rows), so "the shipped sets" and this tool's own output are the same rows -- and
so are the failure probabilities in the `BINFHE_PARAMSET_LIST` comments beside
them, on the 105 rows the search re-selected: those labels are this tool's
measured per-key certifications, taken from the pessimistic end and rounded
toward zero. `TOY`, `TOY_MULTI_BASE`, `MEDIUM` and `SIGNED_MOD_TEST` keep their
earlier labels. Two
consequences. The known-answer control does not depend on the file:
`dse_measured.CONTROL` stores each control set's parameters together with its
measured sigma (four sets measured as the library ships them, at this pin), and
`dse_shipfit.control()` checks the model against the measurement and the parser
against the live table's row for the same name, so a row the library changes
shows up as `LIVE TABLE DIFFERS` rather than passing on stale expectations; five
earlier sets are kept as `CONTROL_HISTORICAL`, geometry inline, model half
only. And the table the library shipped before the re-selection (41 `STD*`/`LPF*`
rows) is kept as `scripts/paramsestimator/fixtures/binfhecontext-238153db.cpp`:
it is the reference for `--key-cap-mult`, and the baseline when a comparison
against "what shipped before" is wanted (`BINFHECONTEXT_SRC` can point at it).

The entrypoint refuses to start if the installed OpenFHE was not built with
`WITH_NOISE_DEBUG=ON`, checks that `/workspace` is writable, and runs an
incremental `cmake --build` of the harness on every start
([getting-started.md](getting-started.md#phase-4-first-run-and-where-your-files-live)).
Sage's `bin` is deliberately kept off `PATH` and the container sets
`pids_limit: 4096`, because `sage.all` imported without Sage's environment
forks without bound. `OMP_NUM_THREADS` is forwarded from the host only when
set, because assigning it an empty string makes libgomp print a warning on
stderr, the stream the scripts read noise values from.

## The pin

The image pins one OpenFHE revision, and it appears in three places that must
move together: `Dockerfile` (`ARG OPENFHE_REF`), and `docker-compose.yml`
twice (the build argument and the `image:` tag). The Dockerfile default only
applies to a plain `docker build`; compose always passes its own, so bumping
only the Dockerfile silently rebuilds the old ref under the new tag.

```
OPENFHE_REF=<full sha> MAKE_JOBS=8 docker compose build
```

**Do not fall back to a release tag.** `v1.5.1` and earlier lack three things
this tool depends on:

- the excess-H lost-carry fix in both `SignedDigitDecompose` overloads. The
  defect silently corrupted measured noise for any (set, base) pair whose base
  overflows, by up to 74 bits of log2Pf, and made noise non-monotonic in the
  gadget base, so a search would have rejected good candidates;
- per-dimension gadget bases, which add a column to the parameter table and are
  the whole reason two-base maps can be searched;
- the `BTKeyGen` fix. Without it the bootstrapping-key cache is keyed by gadget
  base alone, so a second `BTKeyGen` on one context returns the first key
  regardless of secret, and multi-key noise measurement reads about 10x the true
  stddev. The harness builds a fresh context per key regardless, which keeps it
  honest against an older revision.

The build also records the resolved commit inside the image, at
`/opt/openfhe/share/openfhe-src/OPENFHE_REF` (two lines: the sha, then the
commit subject), pointed at by `OPENFHE_REF_FILE`. `dse.py doctor` reads it and
compares it against the pin the active cost regime's cells carry, which turns
two silent hazards into printed lines: a library nobody can identify after the
build log is gone, and cells that describe a different commit from the one
installed. An image without the file, or a native install, reports the commit
as not recorded rather than guessing. A regime whose cells rest on more than one
commit (`pins_by_method`) is reported per method.

Hazards when bumping:

- `git fetch` takes no abbreviated sha: `couldn't find remote ref`. Full 40
  characters.
- A force-pushed sha **keeps resolving**. GitHub serves it directly even though
  it is no longer any branch tip, so the build never breaks; it keeps
  describing superseded code. `git branch -r --contains <sha>` returning
  nothing is the tell.
- A commit named as a repin target may not be pushed yet: `upload-pack: not
  our ref`, a layer deep. Check `git ls-remote origin` before editing.
- The cost cells are per pin and the regime tables say which; the noise model
  and the security cache are not ([cost-model.md](cost-model.md#recalibrating),
  [security.md](security.md#the-cache)). After a bump: correctness gate first,
  then cells, then `doctor`.
- The known-answer control stores the parameters its four sets were measured
  on. A pin that changes one of those rows in the library's table makes
  `doctor` print `LIVE TABLE DIFFERS` for it, which is the control doing its
  job, not a parser bug: re-measure the set on the row the library ships now
  (`scripts/dse-arms/plans/control-rebaseline.cmds`, about two hours) and let
  `scripts/dse-tools/control_rebaseline.py LOG --write` rewrite the entry with
  the new parameters and the new sigma. `12858277` did
  this to three of the four, by one gadget-map position each, when the library
  took the 108-row table.
- Three key-layout flags in `dse_model.py` follow the pin and are flipped with
  it, never before: `KSK_TOP_COMPACT` (the switching key stores only the
  reachable rows at its top digit position), `RK_TOP_COMPACT` (the same for
  DM's refresh key) and `KSK_ZERO_ROWS_DROPPED` (no row for digit value zero,
  and the key switch skips a zero digit). All three are on, since `94229558`. The
  third changes **noise** as well as size, so a measurement taken before it
  carries the zero rows' noise and reads about 0.4 bits pessimistic at `b_KS`
  32 and 64. The model prices what the installed library holds, and every
  manifest records the layout it was priced under (`key_layout`).

## What each pin moved

Newest first. Noise has been measured unchanged across every one of these
moves; the cost cells were re-measured or explicitly carried at each.

| OpenFHE commit | what it changed here |
|---|---|
| `12858277` (current, 2026-09-15) | The head of openfhe-development `dev`: the squash-merge of the branch `94229558` sat on (#1295) plus two CKKS-only PRs (#1308, #1276) that touch no file under `src/binfhe` or `src/core`. Between `94229558` and the merge the accumulator and key-switch inner loops are unchanged: `09913224` adds two per-gate guards (the ciphertext modulus must divide 2N and must not exceed `q_KS`) and the 28-bit `MAX_MODULUS_SIZE32` cap the model already prices; `e5d64a4c` folds the three 32-bit key-generation twins out of the accumulator sources into shared templates and moves the LMKCDEY schedule into a shared function; the 32-bit keys and `RingGSWBTKey` gain serialization; and the 112-row parameter table is this tool's, 108 of its rows the certified picks with geometry and label identical. No key-layout commit is in the span, so the three layout flags stay on. Gate time on the cost box, same explicit geometry at both pins and the pins interleaved over six repetitions: 1.000 to 1.014 on the GINX and LMKCDEY sets, 1.024 and 1.027 on the two AP sets at twelve repetitions -- inside the cells' residuals and uniform within a method, so the cells are carried and the regime records it. Three of the four known-answer control sets moved one gadget-map position with the table and were re-baselined. `GetRefreshKey()` and `GetSwitchKey()` return the 64-bit member as the context holds it, null when the 32-bit form is resident, so serializing them at this pin yields five bytes; the harnesses report key sizes from the resident form instead (`estimator::key_sizes`). `94229558` is on no branch any more (the squash replaced it), so it keeps resolving while describing a tree `dev` has moved past. |
| `94229558` (2026-09-09) | Two key-layout commits on top of `41709fbc`, which it carries rebased as `354510e9`. `c4180d75` stores only the reachable rows at the top digit position of the switching key and of DM's refresh key: the switching key falls 25 percent on the rows whose `q_KS` is not an exact power of `b_KS`, and DM's refresh key 30 to 44 percent on 27 of the 35 AP rows. `94229558` drops the rows for digit value zero and skips a zero digit in both key switches, worth another `1/b_KS` of the switching key **and about 0.4 bits of log2Pf** (1.0 at `b_KS` 32, 0.5 at 64, under 0.1 at 256 and above) -- the one library change so far that improves noise. Both leave gate time alone, measured unchanged at 1 and 8 threads on both runtimes, so the cost cells are carried here rather than re-measured and the regime records the carry. Re-pricing moves 88 of the 105 table picks, 77 of them faster, because the freed key budget lets the cap admit a faster row; AP gains 19 to 28 percent of gate time. |
| `41709fbc` (2026-09-09) | Two commits. `f3944448` replaces the 41 shipped `STD*`/`LPF*` rows with the 105 re-selected sets of this tool (the table is 109 rows and is this tool's own output); the known-answer control carries its own parameters (`dse_measured.CONTROL`) and the earlier table is kept as a fixture. `41709fbc` runs every accumulator region at one OpenMP team width and one scratch size across gadget bases, which removes a team-width churn two-base LMKCDEY maps paid at `238153db` (6 to 9 percent under libomp, 21 to 37 under libgomp). multi/libomp LMKCDEY cells measured here (530 timings, 90 two-base maps); what remains on two-base maps is the width term, `map_width_share` 0.40. GINX cells verified unchanged here (+0.4 to +0.8 percent on 90 two-base rows); AP not re-measured. multi/libgomp's LMKCDEY cells are still the `238153db` ones and the regime is flagged stale for two-base LMKCDEY. |
| `238153db` (2026-09-05) | LMKCDEY accumulator and automorphism regions run at equal team sizes instead of a half cap. Multi-thread cells re-measured: within 2 to 3 percent of `a0c3f2cd` at 8 threads, 6 to 11 percent faster at 36. `c3` resolved again at N=1024. `e39eee84` in the same push is keygen-only. |
| `a0c3f2cd` (2026-09-05) | Capped the fused DM/LMKCDEY region at `max(digitsG2/2, 4)` threads, removing the libgomp cliff; all four cost regimes measured here, and the single-thread regimes still stand here (no OpenMP region executes at one thread). |
| `9e8045db` | `BTKeyGen`/`BTKeyLoad` default `internal32 = true`: the 32-bit key forms became the library default. Changed what a `w32*` row means and how the shipped baseline must be priced. |
| `0c75c2ec` | Fused the DM and LMKCDEY accumulator regions; LMKCDEY's accumulator parallel for the first time, its 8-thread `c2` down to 0.59 to 0.70x. |
| `1c3e83ec` and the `issue1269` churn before it | Cells first measured with the two-width arm (660 timings per regime). `BinFHEContext` became non-copyable (a mutex member guarding the lazy 64-bit widening). |
| `678ea50c` / `1b8648e9` | 32-bit NTT butterflies auto-vectorizable; lazy inner product extended from GINX to AP and LMKCDEY on the 32-bit path (they roughly doubled on it). Measuring one commit earlier would have skewed every method comparison toward GINX. |
| `f56c301b` | The runtime 32-bit hybrid arrived, opt-in: a bootstrapping key held at 32 bits inside a 64-bit build for Q up to 2^28. Word size stopped being a build decision. |
| `b72753b5` | Parallel key switch, the last fully serial block in a gate (18.7 to 26.8x standalone at 36 threads). Its predecessor `0a1f7e95` did not compile (`std::min` on `uint32_t` against `int`). |
| `da4d1f48` / `f9694c39` | Parallel CGGI accumulator, LMKCDEY only map caching: GINX `c2` 2.58x geomean against LMKCDEY 0.95x. Every method-vs-method conclusion drawn on the serial cells retracted. Also a wrong-answer fix in `Bootstrap` and `Reject GINX with non-ternary secret key distributions`. |
| `252b2b1f` (2026-08-30) | Squash-merge of PR #1268: the carry fix, per-dimension gadget bases, the `BTKeyGen` fix. Noise measured identical to the previous pin (70 byte-identical gate outputs). |

## One library, and the hybrid default

The image builds OpenFHE once, at `NATIVE_SIZE=64`, and there is one harness
build (`build/`). BinFHE gates run about 2x faster on 32-bit words (1.98x
median over 24 matched pairs at 8 threads, growing with the digit count), and
the library provides that path itself rather than through a second build:

- **The 32-bit forms are the library default.** `BTKeyGen(sk, mode, bool
  internal32 = true)` (OpenFHE `9e8045db` and later): the 64-bit build narrows
  each bootstrapping key to a 32-bit internal form whenever that key's moduli
  fit. Both narrowing sites are gated on `internal32 && Fits(...)`, per key, so
  a default caller gets a 32-bit **switching** key on every set in the table
  (q_KS is 2^14..2^17 whatever Q is) and a 32-bit **accumulator** only when Q
  is at most 2^28 and `digitsG * gBits + 1 <= 32` for every base in the map:
  the STD128 family, not the rows at logQ 29 and above. GINX, AP and LMKCDEY
  all qualify. `dse_constraints.hybrid_ns32_ok` transcribes the library's
  `Fits`, including the default-base check LMKCDEY's automorphism key switch
  requires, and every search holds 32-bit candidates to it.
- **A genuine `NATIVE_SIZE=32` build is not what anyone runs.** OpenFHE's build
  system forces `HAVE_INT128` off there, so that build cannot use the lazy
  128-bit inner product; it is marginally ahead at four gadget digits, even at
  six, and loses past that, and no default caller reaches it. The w32 cells are
  therefore timed on the hybrid path inside the 64-bit build, which measured
  within 3.3 percent of a genuine 32-bit build on four matched configurations.

The estimator binaries follow the library: no flag measures the default
caller's path; `-3` / `--internal32` asks for the 32-bit forms explicitly (a
no-op, accepted so older plans parse); `-6` / `--internal64` forces the 64-bit
forms, the only way to time that path at Q up to 2^28, and what every "off" arm
of an A/B says. Both binaries print `internal32 refresh key:` and `internal32
switch key:` so a log records which forms a run held, and the cost arm carries
both into its records. The key saving of the 32-bit form is resident memory
only: serialization widens the key back to 64 bits ([dse-flow.md](dse-flow.md#w32)).
Rows labelled `build32` in `dse_measured.SHIPPED_COST` are from a genuine
32-bit build the image no longer contains.

## Two images for two runtimes

Compose tags by `OPENFHE_REF` alone, so the gcc image has to be built with
`docker build` and its own tag:

```
docker build -t openfhe-lattice-estimator:<ref>-gcc \
    --build-arg OPENFHE_REF=<ref> --build-arg MAKE_JOBS=8 \
    --build-arg CC_BIN=gcc-14 --build-arg CXX_BIN=g++-14 .
```

The harness is compiled with the same compiler as OpenFHE, in the image and in
the entrypoint's runtime reconfigure (`CC_BIN`/`CXX_BIN` are carried into the
image for that reconfigure), so a regime labelled "clang" is clang throughout.
The harness compiler does not affect gate time -- the timed region is
straight-line library calls -- but the label should be true. A checkout
mounted into both images needs `rm -rf build` between them
([getting-started.md](getting-started.md#phase-6-recalibrate-for-your-hardware-the-skipped-step)).

Images are about 5.6 GB each; a build box accumulates them quickly, and only
the current pin's matter.

## Native builds

Without Docker, a plain `cmake -B build` against an installed 64-bit OpenFHE
is the whole setup; the scripts find `build/bin` relative to the repository
root, or take `ESTIMATOR_BUILD_DIR`. The OpenFHE must be built with
`WITH_NOISE_DEBUG=ON` and should be the pinned commit for the reasons above.
See [getting-started.md](getting-started.md#native-install-without-docker).
