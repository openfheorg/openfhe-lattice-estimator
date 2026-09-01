#!/usr/bin/env python3
"""Ask a few questions, then run the search-based flow end to end.

    sage -python scripts/paramsestimator/dse_wizard.py            # asks
    sage -python scripts/paramsestimator/dse_wizard.py --yes      # takes every default
    sage -python scripts/paramsestimator/dse_wizard.py --dry-run  # prints the commands, runs nothing

It asks which bootstrapping method, which security level, how many gate inputs,
what failure probability, how much key material you can hold, whether to
recalibrate the gate-cost model for THIS machine first, how many threads the
deployment runs, and how many keys to measure -- then it runs, in order:

    dse.py doctor                      is this checkout usable, and for which method
    recalibrate.sh + apply_cells.py    only if asked, or if the method has no cost cells
    dse_table.py plan                  search one cell, select under the key cap, write the plan
    run-plan.sh                        MEASURE: the only step that runs the library (JOBS keys at once)
    dse_table.py finish                certify, and print the BinFHEContextParams row and label

Every command is printed before it runs, in the form docs/getting-started.md
shows, so this is also a worked example of the manual flow. Every answer has a
flag; `--yes` fills in the rest with defaults, which is how a script drives it.
Answers are written to <out>/wizard.json, and a second run in the same directory
skips the steps whose outputs already exist.

WHY ONE CELL OF THE TABLE FLOW rather than search/plan/decide. The single-set
path ranks gate-first with key material as a tiebreak; the table path selects
under the key cap, labels from the unfavourable end of the certified value, reads
the wrong-answer count, and emits the row the library actually consumes. Those
are the behaviours a shipped parameter set needs, so the wizard runs a table of
one cell instead of re-implementing them.
"""
import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, HERE)

import dse_model as m           # noqa: E402
import dse_table as t           # noqa: E402

METHODS = ("GINX", "AP", "LMKCDEY")
LEVELS = t.ALL_LEVELS
HARNESS = os.path.join(ROOT, "build", "bin", "boolean_noise_estimate_script")
TIMER = os.path.join(ROOT, "build", "bin", "boolean_estimate_time")


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if os.path.isfile(os.path.join(d, name)) and os.access(os.path.join(d, name), os.X_OK):
            return True
    return False


# The interpreter the sub-steps run under. Inside the image that is sage's
# python (the estimator and OpenFHE bindings live there); on a bare host with
# numpy and scipy, plain python3 runs every step except the measurement.
PY = "sage -python" if (os.environ.get("SAGE_ROOT") or _which("sage")) else "python3"


# ---------------------------------------------------------------------------
# asking
# ---------------------------------------------------------------------------

class Answers(object):
    """One place for every answer, whichever way it arrived."""

    def __init__(self, args):
        self.args = args
        self.given = {k: v for k, v in vars(args).items() if v is not None}
        self.values = {}

    def ask(self, key, question, default, choices=None, cast=str, why=None):
        """The flag wins; then --yes takes the default; then the terminal is asked.

        `why` is one line on what the answer changes. It is printed before the
        prompt, because an average user asked "key cap lambda?" cannot answer
        without it, and a wizard that assumes the reader already knows the flow
        is the manual flow with extra steps.
        """
        if key in self.given:
            val = self.given[key]
        elif self.args.yes or not sys.stdin.isatty():
            val = default
        else:
            if why:
                print("  %s" % why)
            opts = " (%s)" % "/".join(str(c) for c in choices) if choices else ""
            raw = input("  %s%s [%s]: " % (question, opts, default)).strip()
            val = raw if raw else default
        try:
            val = cast(val)
        except (TypeError, ValueError):
            sys.exit("wizard: %r is not a valid answer for %s" % (val, key))
        if choices is not None and val not in choices:
            sys.exit("wizard: %r is not one of %s for %s" % (val, list(choices), key))
        self.values[key] = val
        return val


def _upper(s):
    return str(s).strip().upper()


def _yesno(s):
    if isinstance(s, bool):
        return s
    s = str(s).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    raise ValueError(s)


# ---------------------------------------------------------------------------
# what the machine tells us, so we do not ask
# ---------------------------------------------------------------------------

def detect_runtime():
    """'clang' or 'gcc' from which OpenMP runtime the harness LINKED, or None.

    Not asked, because the image decides it: OpenFHE and the harness are built
    with one compiler, and a ranking for the other runtime needs the other image
    (docs/cost-model.md#regimes). Detected from the binary rather than assumed.
    """
    if not os.path.exists(TIMER):
        return None
    try:
        out = subprocess.run(["ldd", TIMER], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "libgomp" in out:
        return "gcc"
    if "libomp" in out:
        return "clang"
    return None


def method_has_cells(method, threads, compiler):
    """Whether the cost model can price this method in this regime at all.

    Without a cell the search refuses every candidate as uncalibrated, so this
    is the difference between "recalibration is optional" and "recalibration
    is the only way this run can produce anything".
    """
    runtime = m.COMPILER_ALIASES.get(compiler, compiler)
    cells = m.COST_REGIMES.get((threads, runtime), {}).get("cells") or {}
    return any(k[0] == method for k in cells)


def installed_ref():
    path = os.environ.get("OPENFHE_REF_FILE") or "/opt/openfhe/share/openfhe-src/OPENFHE_REF"
    try:
        with open(path) as f:
            return f.read().split()[0]
    except (OSError, IndexError):
        return None


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

class Runner(object):
    def __init__(self, dry, log_dir):
        self.dry = dry
        self.log_dir = log_dir
        self.steps = []

    def run(self, argv, stdout=None, stderr=None, env=None, cwd=ROOT, check=True):
        """Print the command the way the docs write it, then run it."""
        shown = " ".join(shlex.quote(a) for a in argv)
        if env:
            shown = " ".join("%s=%s" % kv for kv in sorted(env.items())) + " " + shown
        if stdout:
            shown += " > %s" % os.path.relpath(stdout, ROOT)
        print("\n$ %s" % shown)
        self.steps.append(shown)
        if self.dry:
            return 0
        full_env = dict(os.environ)
        full_env.update(env or {})
        out = open(stdout, "w") if stdout else None
        err = open(stderr, "w") if stderr else None
        try:
            rc = subprocess.call(argv, stdout=out, stderr=err, env=full_env, cwd=cwd)
        finally:
            if out:
                out.close()
            if err:
                err.close()
        if check and rc != 0:
            sys.exit("wizard: step failed (exit %d): %s" % (rc, shown))
        return rc


def _rel(path):
    """Paths are shown and passed relative to the repo root, because that is how
    docs/getting-started.md writes every command and the Runner's cwd is ROOT."""
    return os.path.relpath(path, ROOT)


def _py(*parts):
    return PY.split() + [_rel(parts[0])] + list(parts[1:])


def estimate_measurement_minutes(manifest_path, keys, samples, jobs=1):
    """From the pick's own predicted gate time: keys x samples x gate, plus keygen.

    A number from the pick rather than a rule of thumb, so a slow AP set is told
    as slow before the hours are spent. Keygen is the part the model does not
    predict; it is booked at a flat share observed on the 105-cell table. The
    gate time is the 8-thread cell's; a single-threaded key process runs the
    gate about twice as slowly but JOBS of them run side by side, and the two
    together measured 4.0x on 8 cores at 1250 gates per key, so the fan-out is
    booked at 0.5 per process.
    """
    try:
        man = json.load(open(manifest_path))
        p = next(c["pick"] for c in man["cells"] if c.get("pick"))
    except (OSError, ValueError, StopIteration, KeyError):
        return None
    gates = keys * samples * p["gate_us"] / 1e6
    minutes = (gates * 1.35) / 60.0     # keygen + startup ran ~35% on top of gates, 105 cells
    if jobs > 1:
        minutes /= max(1.0, 0.5 * min(jobs, keys))
    return minutes


# ---------------------------------------------------------------------------
# the flow
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="dse_wizard",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", "-y", action="store_true",
                    help="take the default for every question not given as a flag")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="print every command the flow would run and run nothing")
    ap.add_argument("--method", choices=METHODS, type=_upper)
    ap.add_argument("--level", choices=LEVELS, type=_upper)
    ap.add_argument("--inputs", type=int, choices=(2, 3, 4))
    ap.add_argument("--target", type=float, help="log2 failure probability, e.g. -64")
    ap.add_argument("--key-cap-gib", dest="key_cap_gib",
                    help='key material budget: "4", "4,AP=8", or "none"')
    ap.add_argument("--key-cap-lambda", dest="key_cap_lambda", type=float)
    ap.add_argument("--recalibrate", dest="recalibrate", type=_yesno,
                    help="measure this machine's gate-cost cells first (yes/no)")
    ap.add_argument("--threads", choices=("multi", "single"))
    ap.add_argument("--mt-threads", dest="mt_threads", type=int,
                    help="thread count of the multi regime (default 8)")
    ap.add_argument("--keys", type=int)
    ap.add_argument("--samples", type=int)
    ap.add_argument("--out-dir", dest="out_dir")
    ap.add_argument("--security", choices=("table", "estimator"), default=None)
    ap.add_argument("--tolerance", type=int, default=None)
    a = ap.parse_args(argv)

    print("BinFHE parameter selection -- the search-based flow, one set at a time.")
    print("Every command below is printed before it runs; docs/getting-started.md")
    print("explains each one.\n")

    ans = Answers(a)

    # --- what we can read off the machine -----------------------------------
    compiler = detect_runtime()
    in_container = os.path.exists(HARNESS)
    ref = installed_ref()
    if compiler:
        print("  runtime:  %s (%s), detected from the harness" %
              (compiler, "libgomp" if compiler == "gcc" else "libomp"))
    else:
        compiler = "clang"
        print("  runtime:  not detected (no harness here); assuming clang/libomp")
    print("  OpenFHE:  %s" % (ref[:8] if ref else "unknown -- outside the container?"))
    print("  box:      %s  <- what the shipped cost cells describe" % m.COST_BOX)
    print()

    # --- the questions -------------------------------------------------------
    method = ans.ask("method", "Bootstrapping method", "GINX", METHODS, _upper,
                     why="GINX is what dse.py search defaults to and what most shipped sets "
                         "use; AP and LMKCDEY are the other two accumulators.")
    level = ans.ask("level", "Security level", "STD128", LEVELS, _upper,
                    why="The number is bits of attack cost; a Q suffix means a quantum attacker.")
    inputs = ans.ask("inputs", "Gate inputs", 2, (2, 3, 4), int,
                     why="Arity of the boolean gate. 3 and 4 are strictly harder to keep correct.")
    target = ans.ask("target", "Failure probability, log2", -64.0, None, float,
                     why="-64 means one wrong gate in 2^64; -128 gives the set the LPF_ prefix.")
    cap = ans.ask("key_cap_gib", "Key material budget, GiB", t.KEY_CAP_DEFAULT, None, str,
                  why='Beyond this the search trades gate time for key material. "none" is '
                      'gate-first with no limit; the default is 4 GiB, 8 for AP.')
    lam = ans.ask("key_cap_lambda", "Exchange rate beyond the budget", 2.0, None, float,
                  why="How many times the gate time one doubling of key over the budget costs. "
                      "0 ignores key material; larger values pull picks under the cap.")
    threads = ans.ask("threads", "Thread regime", "multi", ("multi", "single"), str,
                      why="Gate times are ranked per regime. multi is the OpenMP build at a "
                          "thread count; single is one thread.")
    mt = ans.ask("mt_threads", "Threads in the multi regime", 8, None, int,
                 why="The cost cells are measured at one thread count; 8 is what ships.") \
        if threads == "multi" else 1

    has_cells = method_has_cells(method, threads, compiler)
    if not has_cells:
        print("\n  NOTE: the %s/%s regime holds NO cost cells for %s. Without them the search"
              % (threads, compiler, method))
        print("  refuses every candidate as uncalibrated, so recalibration is not optional here.")
    recal = ans.ask("recalibrate", "Measure this machine's gate-cost cells first",
                    not has_cells, (True, False), _yesno,
                    why="The shipped cells describe an i7-9700. On another machine every gate "
                        "time and ranking is someone else's until you recalibrate. Budget about "
                        "15 minutes for one method at 8 threads, an hour with the single-thread "
                        "stage; the box must be idle.")
    if not has_cells and not recal:
        sys.exit("wizard: %s has no cost cells in %s/%s and recalibration was declined; "
                 "nothing can be ranked. Re-run and answer yes, or pick a method with cells."
                 % (method, threads, compiler))

    keys = ans.ask("keys", "Independent keys to measure", 12, None, int,
                   why="The verdict is the 90th-percentile key, so it needs several. 8 is the "
                       "table's minimum, 12 the flow's default, 16-32 settle a thin margin.")
    samples = ans.ask("samples", "Gates per key", 1250, None, int,
                      why="1250 resolves the per-key spread of every shipped set.")
    jobs = ans.ask("jobs", "Keys measured at once", min(os.cpu_count() or 1, 8), None, int,
                   why="One single-threaded process per key, this many side by side: keys "
                       "parallelise where OpenMP inside a gate does not (4x on 8 cores at 1250 "
                       "gates per key). run-plan.sh keeps them inside a memory budget from the "
                       "plan's key sizes.")
    name = t.set_name(level, inputs, method, target)
    default_out = "wizard-%s-%s" % (name, datetime.date.today().isoformat())
    out = ans.ask("out_dir", "Output directory", default_out, None, str,
                  why="Everything the run produces lands here; re-running in it resumes.")
    out = os.path.join(ROOT, out) if not os.path.isabs(out) else out
    security = a.security or "estimator"
    tolerance = a.tolerance if a.tolerance is not None else 1

    print("\n  set name:  %s" % name)
    print("  regime:    %s/%s%s" % (threads, compiler, " at %d threads" % mt if threads == "multi" else ""))
    print("  output:    %s" % os.path.relpath(out, ROOT))

    # A dry run with a named --out-dir still records its answers there: the file
    # is the resolved form of every question, which is what a reader wants to
    # inspect before spending the hours. A dry run with the DEFAULT directory
    # writes nothing, so looking costs no directory in the checkout.
    if not a.dry_run or "out_dir" in ans.given:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "wizard.json"), "w") as f:
            json.dump(dict(ans.values, compiler=compiler, set_name=name,
                           openfhe=ref, when=datetime.datetime.now().isoformat(timespec="seconds")),
                      f, indent=1)

    if not in_container and not a.dry_run:
        sys.exit("\nwizard: build/bin/boolean_noise_estimate_script is missing, so nothing here can\n"
                 "measure. Run inside the container (est estimator sage -python %s),\n"
                 "or pass --dry-run to see the commands." % os.path.relpath(__file__, ROOT))

    R = Runner(a.dry_run, out)
    regime_flags = ["--threads", threads, "--compiler", compiler]

    # --- 0. doctor -----------------------------------------------------------
    print("\n== 1/5  doctor: is this checkout usable ==")
    R.run(_py(os.path.join(HERE, "dse.py"), *regime_flags, "doctor"), check=False)

    # --- 1. recalibrate ------------------------------------------------------
    if recal:
        print("\n== 2/5  recalibrate: gate-cost cells for THIS machine (%s) ==" % method)
        print("  The box must be idle. recalibrate.sh pins the run and records the binding.")
        recal_dir = os.path.join(out, "recal")
        stages = "multi maps-ginx" if method == "GINX" else ("multi maps maps-ginx" if method == "LMKCDEY" else "multi")
        if threads == "single":
            stages = "single"
        env = dict(METHODS=method, STAGES=stages)
        if threads == "multi":
            env["MT_THREADS"] = str(mt)
        R.run(["bash", "scripts/dse-arms/recalibrate.sh", _rel(recal_dir)], env=env)
        stage = "single" if threads == "single" else "multi"
        runtime = "libgomp" if compiler == "gcc" else "libomp"
        fit = os.path.join(recal_dir, "%s.fit.txt" % stage)
        if a.dry_run or os.path.exists(fit):
            R.run(["python3", "scripts/dse-tools/apply_cells.py", _rel(fit),
                   "--regime", "%s/%s" % (stage, runtime),
                   "--comment", _rel(os.path.join(recal_dir, "provenance.txt")),
                   "--merge", "--update-regime"])
            R.run(_py(os.path.join(ROOT, "tests", "test_dse.py")), check=False)
        else:
            sys.exit("wizard: recalibration produced no %s; see %s" % (fit, recal_dir))
    else:
        print("\n== 2/5  recalibrate: skipped (using the cells this checkout carries) ==")

    # --- 2. search + select + plan ------------------------------------------
    print("\n== 3/5  search one cell and write its measurement plan ==")
    manifest = os.path.join(out, "manifest.json")
    plan = os.path.join(out, "plan.cmds")
    if os.path.exists(plan) and not a.dry_run:
        print("  found %s -- skipping the search (delete it to redo)" % os.path.relpath(plan, ROOT))
    else:
        R.run(_py(os.path.join(HERE, "dse_table.py"), "plan",
                  "--levels", level, "--methods", method, "--inputs", str(inputs),
                  "--targets=%g" % target, "--security", security, "--tolerance", str(tolerance),
                  "--key-cap-gib", cap, "--key-cap-lambda", "%g" % lam,
                  "--threads", threads, "--compiler", compiler,
                  "--keys", str(keys), "--samples", str(samples),
                  "--out-dir", os.path.relpath(out, ROOT)))
        if not a.dry_run and not os.path.exists(plan):
            sys.exit("wizard: the search produced no pick for %s. The table output above says\n"
                     "which constraint bound; 'below target' means no configuration reaches it at\n"
                     "this level and arity, 'cost uncalibrated' means recalibrate first." % name)

    # --- 3. measure ------------------------------------------------------------
    print("\n== 4/5  measure: the only step that runs the library ==")
    log = os.path.join(out, "plan.log")
    est = estimate_measurement_minutes(manifest, keys, samples, jobs)
    if est:
        print("  about %.0f minutes for %d keys x %d gates at %d at once, from the pick's own predicted gate time"
              % (est, keys, samples, jobs))
    if os.path.exists(log) and not a.dry_run and \
            any(l.startswith("record|") for l in open(log)):
        print("  found %s with records -- skipping the measurement (delete it to redo)"
              % os.path.relpath(log, ROOT))
    else:
        R.run(["bash", "scripts/run-plan.sh", _rel(plan)], env={"JOBS": str(jobs)},
              stdout=log, stderr=os.path.join(out, "plan.err"))

    # --- 4. certify ------------------------------------------------------------
    print("\n== 5/5  certify, and the row to paste ==")
    R.run(_py(os.path.join(HERE, "dse.py"), "table", "finish", "--dir", os.path.relpath(out, ROOT),
              "--emit-rows", "--emit-labels"), check=False)

    if a.dry_run:
        print("\n(dry run: %d commands, nothing executed)" % len(R.steps))
        return 0
    print("\nThe verdict column says whether the measurement certifies the set at its target.")
    print("PASS: paste the BinFHEContextParams row and the enum comment above into OpenFHE.")
    print("MORE KEYS: the label is limited by measurement, not by the parameters -- re-run")
    print("with a larger --keys in the same directory after deleting plan.log.")
    print("Everything is in %s" % os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
