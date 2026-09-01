#!/usr/bin/env python3
"""dse -- one entry point for the design-space exploration.

The pieces (dse_model, dse_enumerate, dse_security, dse_verify, ...) are each
usable on their own, and each has its own --help. What was missing until now was
the DOOR: how they compose, and which steps need the container. This is that.

THE PIPELINE, and which half of it is arithmetic and which is measurement:

    1. dse.py doctor                       is this checkout usable at all?
    2. dse.py search  --level L --target T  arithmetic only, seconds to minutes.
                                            Ranks candidates and saves them.
    3. dse.py plan    --picks P             emits a runner-ready command file.
    4. <run it in the container>            THE MEASUREMENT. Needs an OpenFHE
                                            built WITH_NOISE_DEBUG=ON, i.e. the
                                            image this repo's Dockerfile builds.
    5. dse.py decide  --log L --target T    pass / fail / more-keys / model-limited
    6. dse.py validate --log L              model against measurement, with a
                                            known-answer control

Steps 2, 3, 5 and 6 are pure Python and run anywhere SciPy is available. Step 4
is the only one that needs the image, and it is the only one that produces
evidence: everything before it is a prediction and everything after it is
bookkeeping on a measurement.

INTERPRETER. `dse_model` imports `scipy.special.erfcx`, and the container's bare
`python3` does not have SciPy -- SageMath's does, along with the lattice-estimator
that `dse_security price` needs. So inside the image:

    sage -python scripts/paramsestimator/dse.py <subcommand>

Outside it, any python3 with scipy works for everything except `dse_security
price`. `dse.py doctor` reports this rather than crashing on it, which it used to
do -- a checkout doctor that dies on a broken checkout is worse than none.

WHY THE ROUND TRIP IS NOT AUTOMATED. `plan` emits commands instead of running
them, because the measurement takes minutes to hours per candidate, wants an idle
machine for timing, and is usually run on a different box from the search. Hiding
that behind one command would hide the part that costs real time and the part that
can silently produce numbers from the wrong build.
"""

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _installed_openfhe_ref():
    """(sha, commit subject) of the OpenFHE this image installed, or (None, None).

    Written by the Dockerfile's fetch stage and carried into the image; absent on
    a native install and on any image built before it existed, which is reported
    rather than treated as a failure.
    """
    path = os.environ.get('OPENFHE_REF_FILE') or os.path.join(
        os.environ.get('OPENFHE_INSTALL_DIR', '/opt/openfhe'),
        'share', 'openfhe-src', 'OPENFHE_REF')
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return None, None
    sha = lines[0].strip() if lines else ''
    subject = lines[1].strip() if len(lines) > 1 else ''
    # A truncated or hand-edited file is worse than none: say nothing rather than
    # print something that looks like provenance.
    if len(sha) != 40 or not all(c in '0123456789abcdef' for c in sha.lower()):
        return None, None
    return sha, (subject or None)


def cmd_doctor(a):
    """Is this checkout usable? Report, do not fix."""
    ok = True
    print("modules")
    mods = ('dse_model', 'dse_constraints', 'dse_enumerate', 'dse_security',
            'dse_verify', 'dse_validate', 'dse_measured', 'dse_shipfit')
    for name in mods:
        try:
            __import__(name)
            print("  ok      %s" % name)
        except Exception as exc:
            ok = False
            print("  FAIL    %s: %s" % (name, exc))
    try:
        import dse_model as m
    except Exception as exc:
        print("\ncost model\n  FAIL    cannot import dse_model: %s" % exc)
        print("\nIf this is a missing scipy/numpy, you are running the wrong")
        print("interpreter: these modules need SageMath's python, which carries")
        print("numpy/scipy and the lattice-estimator. Inside the container use")
        print("`sage -python`, not bare `python3`.")
        return 1

    # Which OpenFHE is actually installed, and does the active regime's cells
    # describe it? The image records the resolved sha because the build log's own
    # "pinned OpenFHE" line is not replayed on a cached rebuild, and because a
    # force-pushed sha keeps resolving -- so a build can quietly keep describing
    # superseded code. See the Dockerfile comment beside .openfhe-ref.
    print("\ninstalled OpenFHE")
    sha, subject = _installed_openfhe_ref()
    if not sha:
        print("  note    not recorded. Either this image predates the OPENFHE_REF")
        print("          file, or you are outside the container. The library's")
        print("          commit cannot be checked against the cells' pin here.")
    else:
        print("  ok      %s  %s" % (sha[:8], subject or ""))
        reg = m.COST_REGIMES[m.ACTIVE_REGIME]
        pin = reg.get("pin")
        by_method = reg.get("pins_by_method") or {}
        if not pin:
            print("  note    the active regime records no pin to compare against")
        elif pin == sha and by_method and set(by_method.values()) != {pin}:
            # The cells describe this library but were TIMED earlier, on commits
            # that do not touch gate time. Print the carry and what it rests on,
            # so "describes" is never mistaken for "measured here".
            print("  ok      the active regime's cells describe THIS library")
            print("          measured at %s" % ", ".join(
                "%s %s" % (k, v[:8]) for k, v in sorted(by_method.items())))
            if reg.get("carried_why"):
                print("          carried: %s" % reg["carried_why"])
        elif by_method and len(set(by_method.values())) > 1:
            # A regime re-measured one method at a time rests on two pins, and
            # neither is the installed commit. Say which, per method.
            here = sorted(k for k, v in by_method.items() if v == sha)
            away = sorted((k, v[:8]) for k, v in by_method.items() if v != sha)
            if here:
                print("  ok      %s cells were measured on THIS library" % "/".join(here))
            for k, v in away:
                print("  note    %s cells were measured at %s, not on this library; the"
                      % (k, v))
                print("          regime's entry says why that is deliberate where it is.")
        elif pin == sha:
            print("  ok      the active regime's cells were measured on THIS library")
        else:
            print("  note    the active regime's cells were measured at %s, not on"
                  % pin[:8])
            print("          this library. Gate times from them describe a different")
            print("          build. Either re-measure (scripts/dse-arms/gatecost-regime.sh,")
            print("          docs/cost-model.md) or check that regime's entry, which")
            print("          records why the difference is deliberate where it is.")

    print("\ncost model")
    need = {(me, N, w) for me in ('GINX', 'LMKCDEY')
            for N in (512, 1024, 2048) for w in (32, 64)}
    have = set(m.GATE_COST)
    print("  %-7s %d of %d (method, N, word) cells present"
          % ("ok" if need <= have else "PARTIAL", len(have & need), len(need)))
    if need - have:
        ok = False
        for k in sorted(need - have, key=str):
            print("          missing %s -- candidates there price as None" % (k,))
    # AP is reported separately: it is a later addition, its cells are optional in
    # the sense that a checkout without them still works for the other two
    # methods, and its fourth coefficient means something different (the
    # per-external-product cost, not an automorphism term).
    ap_need = {('AP', N, w) for N in (512, 1024, 2048) for w in (32, 64)}
    ap_have = ap_need & have
    if ap_have:
        print("  %-7s %d of %d AP cells present (c3 there is the per-product cost)"
              % ("ok" if ap_need <= have else "PARTIAL", len(ap_have), len(ap_need)))
    else:
        print("  note    no AP cells in this regime -- AP candidates refuse. "
              "Measure with METHODS=1 scripts/dse-arms/gatecost-regime.sh")
    nol = [k for k, v in m.GATE_COST.items() if k[0] == 'LMKCDEY' and v[3] is None]
    zer = [k for k, v in m.GATE_COST.items() if k[0] == 'LMKCDEY' and v[3] == 0.0]
    if nol:
        print("  note    %d LMKCDEY cells have no numAutoKeys term; w != 10 is"
              " UNPRICED there" % len(nol))
    if zer:
        print("  note    %d LMKCDEY cells price numAutoKeys at zero gate cost: the term"
              " measured below the 125 us quantum (< 170 us/gate at w=10)" % len(zer))
    print("  %-7s %s" % ("ok", m.cost_provenance()))
    for reg in sorted(m.COST_REGIMES):
        r = m.COST_REGIMES[reg]
        mark = "ok" if r["cells"] else "absent"
        print("  %-7s regime %-6s/%-5s %s"
              % (mark, reg[0], reg[1],
                 # Which METHODS, not just how many cells: a regime holding no
                 # AP cell prices no AP candidate, and "12 cells" does not say so.
                 ("%d cells, %d timings (%s)"
                  % (len(r["cells"]), r["points"],
                     ", ".join(sorted({c[0] for c in r["cells"]}))))
                 if r["cells"] else
                 "not measured -- --threads %s --compiler %s refuses every candidate"
                 % reg))
    for lack in getattr(m, 'COST_PIN_LACKS', ()):
        print("          pending: %s" % lack)
    for un in getattr(m, 'COST_UNREPRESENTED', ()):
        print("          not represented: %s" % un)
    print("\nsecurity boundaries")
    try:
        import dse_security as sec
        c = sec.load()
        ents = c.get('entries', {})
        dims = sum(len(v) for v in ents.values())
        print("  %-7s %d keys, %d priced dimensions" % ("ok" if ents else "EMPTY",
                                                        len(ents), dims))
        ref = c.get('estimator_ref')
        print("  %-7s priced against estimator %s"
              % ("ok" if ref else "note", ref or "UNRECORDED -- provenance unknown"))
        if not ents:
            ok = False
            print("          run: sage -python dse_security.py price --level ... "
                  "(needs the container)")
    except Exception as exc:
        ok = False
        print("  FAIL    %s" % exc)
    print("\nshipped-set table (dse_beats and the shipped-set controls read it from OpenFHE's source)")
    try:
        import dse_shipfit as sf
        if os.path.exists(sf.SRC):
            # Parsing it is the check, not its existence: a clone that has moved
            # to another branch is present and unusable.
            try:
                nsets = len(sf.shipped_params())
                print("  %-7s %s (%d sets)" % ("ok", sf.SRC, nsets))
                # the known-answer control carries its own parameters; the live
                # table is only consulted for the parser half
                try:
                    okc, lines = sf.control(live=sf.shipped_params(strict=False))
                    print("  %-7s known-answer control: %d live sets (parser + model), %d historical (model)%s"
                          % ("ok" if okc else "FAIL", len(sf.dm.CONTROL), len(sf.dm.CONTROL_HISTORICAL),
                             "" if okc else " -- run `dse.py validate` for the rows"))
                except Exception as exc:
                    print("  %-7s known-answer control: %s" % ("FAIL", exc))
                try:
                    npre = len(sf.shipped_params(strict=False, src=sf.PRE_RESELECT_SRC))
                    print("  %-7s pre-re-selection table %s (%d sets; the --key-cap-mult reference)"
                          % ("ok" if npre == sf.PRE_RESELECT_SETS else "FAIL",
                             os.path.relpath(sf.PRE_RESELECT_SRC, HERE), npre))
                except Exception as exc:
                    print("  %-7s pre-re-selection table: %s" % ("FAIL", exc))
            except Exception as exc:
                ok = False
                print("  %-7s %s" % ("FAIL", sf.SRC))
                print("          %s" % exc)
        else:
            ok = False
            print("  %-7s %s" % ("absent", sf.SRC))
            print("          set BINFHECONTEXT_SRC to <openfhe checkout>/src/binfhe/lib/binfhecontext.cpp;"
                  " the image carries a copy and sets it")
    except Exception as exc:
        ok = False
        print("  FAIL    %s" % exc)
    print("\nmeasurement harness (only needed for step 4)")
    for rel in ('build/bin/boolean_noise_estimate_script',
                'build/bin/boolean_estimate_time'):
        p = os.path.join(HERE, '..', '..', rel)
        print("  %-7s %s" % ("ok" if os.path.exists(p) else "absent", rel))
    print("\n%s" % ("checkout looks usable." if ok else
                    "checkout has gaps above; the search will refuse rather than guess."))
    return 0 if ok else 1


def cmd_search(a):
    import dse_enumerate as e
    grid = {}
    if a.inputs:
        grid['inputs'] = a.inputs
    total, counts, kept = e.run(
        a.target, method=a.method, key_dist=a.key_dist,
        grid=grid or None, word_size=a.word_size, multi_base=a.multi_base,
        limit=a.limit, level=a.level, sec_source=a.security,
        sec_tolerance=a.tolerance)
    e.report(a.target, total, counts, kept, explain=a.explain,
             level=a.level, method=a.method, tolerance=a.tolerance, limit=a.limit)
    if not kept:
        return 1
    front = e.pareto(kept)
    pick = e.default_policy(front)
    out = dict(level=a.level, target=a.target, method=a.method,
               tolerance=a.tolerance, security=a.security,
               frontier=[_jsonable(c) for c in front],
               pick=_jsonable(pick) if pick else None)
    if a.save:
        with open(a.save, 'w') as f:
            json.dump(out, f, indent=1, default=str)
        print("\nsaved %d frontier candidates to %s  (feed it to `dse.py plan`)"
              % (len(front), a.save))
    return 0


def _jsonable(c):
    return {k: (list(v.items()) if isinstance(v, dict) else v)
            for k, v in c.items() if not k.startswith('_')}


def cmd_plan(a):
    import dse_verify as v
    with open(a.picks) as f:
        saved = json.load(f)
    cands = saved['frontier'] if (a.all or a.model_limited) else (
        [saved['pick']] if saved.get('pick') else [])
    # Decision distance: how many of its own bands a candidate's margin is from
    # the target. Below 1 the MODEL cannot decide it and one measurement can;
    # far above 1 a measurement only confirms what the model already says. This
    # is the adaptive-sampling rule: spend the expensive step where it changes a
    # verdict, not where the prediction is already certain.
    def distance(c):
        b = c.get('band') or 0.0
        return abs(c.get('net_margin', 0.0) + b) / b if b else float('inf')   # margin before the band, in bands
    if a.model_limited:
        cands = [c for c in cands if distance(c) < 1.0]
        if not cands:
            sys.exit("no model-limited candidates in %s: every frontier row is decided by the model alone" % a.picks)
    if a.order == 'decision':
        cands = sorted(cands, key=distance)
    if not cands:
        sys.exit("no candidates in %s" % a.picks)
    need = v.resolving_keys(a.samples)
    lines = ["# Measurement plan from %s" % a.picks,
             "# level %s, target %s, %d candidate(s)"
             % (saved['level'], saved['target'], len(cands)),
             "#",
             "# %d keys x %d gates each. dse_verify.resolving_keys says %s keys are"
             % (a.keys, a.samples, need),
             "# needed at this sample count to RESOLVE the per-key scatter; fewer",
             "# still bounds the pooled sigma, it just cannot separate scatter from",
             "# sampling. One key per process on purpose: -K k pools its stream and",
             "# per-key figures are then unrecoverable.",
             "#",
             "# Run under scripts/run-plan.sh, which records the binary's own",
             "# Failures: count as well as sigma. A gate can return a clean,",
             "# low-noise encryption of the WRONG bit; no sigma sees that."]
    for i, c in enumerate(cands):
        c = dict(c)
        if isinstance(c.get('gadget_map'), list):
            c['gadget_map'] = {int(k): int(n) for k, n in c['gadget_map']}
        # 'pick' when the plan is the policy pick alone, 'cand' for a frontier
        # list: two plans for one level are routinely concatenated into one
        # runner file, and the same (n, logQ) under the same label would merge
        # two different configurations into one measured group.
        lines.append("# label: %s%d_n%d_lq%d" % ("cand" if (a.all or a.model_limited) else "pick", i, c['n'], c['log_q_big']))
        if c.get('key_bytes'):
            lines.append("# key_mib: %.0f" % (c['key_bytes'] / 2 ** 20))
        lines.append("#   decision distance %.2f bands: predicted log2Pf %.1f, band %.1f, gate %.0f us, map %s"
                     % (distance(c), c.get('log2pf', 0.0), c.get('band') or 0, c.get('gate_us') or 0,
                        ",".join("%d:%d" % kv for kv in sorted(c['gadget_map'].items()))))
        for _ in range(a.keys):
            lines.append(v.command(c, a.samples))
    text = "\n".join(lines) + "\n"
    if a.out:
        open(a.out, 'w').write(text)
        print("wrote %d commands to %s" % (len(cands) * a.keys, a.out))
        print("then: docker compose run --rm estimator env JOBS=$(nproc) bash scripts/run-plan.sh %s" % a.out)
    else:
        print(text)
    return 0


def _need_log(path):
    """A missing log is a typo, not a crash. Say so and name the likely cause.

    The commands below run INSIDE the container, where only the mounted
    checkout is visible -- passing a host path outside it is the easy mistake,
    and a bare FileNotFoundError traceback does not suggest that.
    """
    if not os.path.exists(path):
        sys.exit("no such log: %s\n"
                 "  If you are inside the container, the path must be under the\n"
                 "  mounted checkout (/workspace); a host path outside it is not\n"
                 "  visible there." % path)


def cmd_decide(a):
    import dse_verify as v
    _need_log(a.log)
    groups = v.read_keys(a.log)
    if not groups:
        sys.exit("no records parsed from %s" % a.log)
    return v.report(groups, a.inputs, a.ct_modulus, a.target,
                    samples_per_key=a.samples, model_band=a.model_band, log=a.log)


def cmd_validate(a):
    import dse_shipfit as sf
    _need_log(a.log)
    # dse_shipfit runs the KNOWN-ANSWER CONTROL first and scores the named
    # shipped sets in the log. If the control fails it returns non-zero and
    # nothing after it is trustworthy, so stop here.
    rc = sf.main(a.log)
    if rc:
        return rc
    return _validate_candidates(a.log, getattr(a, 'manifest', None))


def _manifest_dims(path):
    """{label: (key_dist, autokeys)} from a `dse.py table` manifest, or {}.

    A log written before the runner recorded those two fields cannot be scored
    on its own, and the parameter table's log is exactly such a log. The manifest
    beside it holds every dimension the search chose, so where it is available
    the row can be scored properly instead of excluded. Nothing is inferred: a
    label absent from the manifest stays excluded.
    """
    try:
        with open(path) as f:
            man = json.load(f)
        return {c['name']: (c['pick'].get('key_dist'), c['pick'].get('autokeys'))
                for c in man.get('cells', []) if c.get('pick')}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("  note    cannot read dimensions from %s: %s" % (path, exc))
        return {}


def _validate_candidates(log, manifest=None):
    """Score the CANDIDATE rows of a runner log against the noise model.

    dse_shipfit scores rows that name a shipped set (`-p NAME` runs) and skips
    the rest BY DESIGN. A plan log has no named sets -- its rows carry geometry
    instead -- so step 6 of the pipeline, run on exactly such a log, used to
    print a header and no rows at all. This is that half.

    Two exclusions, and they are the same two dse_validate and dse_lso apply,
    for the same reasons: a measurement at the wrap ceiling is not a sigma, and
    a candidate outside the model's domain is not a model error.
    """
    import statistics
    import dse_model as m
    import dse_sweeplog as sl

    METHODS = {2: 'GINX', 3: 'LMKCDEY'}
    dims = _manifest_dims(manifest) if manifest else {}
    rows = [r for r in sl.runner_records(log)
            if r.get('N') is not None and r.get('method') != 1]
    if not rows:
        print("\nNo candidate rows in this log. `validate` scores measured rows "
              "against the\nmodel: named sets from a `-p NAME` run (above) and "
              "geometry-carrying rows\nfrom a `dse.py plan` measurement. This log "
              "has neither.")
        return 0

    groups = {}
    for r in rows:
        groups.setdefault(r.get('label') or '-', []).append(r)

    print("\nMEASURED candidates, this run (model against measurement):")
    print("  %-22s %-7s %-4s %-6s %-6s %-9s %-9s %-8s %s"
          % ("label", "method", "sk", "N", "n", "measured", "predicted",
             "err", "fails"))
    ratios = []
    excluded = []
    for label, rs in sorted(groups.items()):
        r = rs[0]
        # Pooled over the keys of one label, which is what a per-key run is for:
        # variances average, so sigma is the root of the mean square.
        meas = math.sqrt(sum(x['sigma_measured'] ** 2 for x in rs) / len(rs))
        fails = sum(x.get('FAILURES') or 0 for x in rs)
        meth = METHODS.get(r.get('method', 2), 'GINX')
        # The distribution and the automorphism-key count come from the RECORD.
        # Both are search dimensions, and guessing either is how this function
        # reported -25% to -41% "model error" on the parameter table's four
        # Gaussian LMKCDEY picks: ternary Var(s) = 2/3 against Gaussian 3.19^2
        # is a 15x difference in the two Var(s)/12 rounding terms, so the
        # assumption -- not the model -- was the whole discrepancy.
        #
        # A log predating those fields can still be scored where the missing
        # value has only one correct setting: GINX is only correct under a
        # ternary secret (a Gaussian one decrypts wrong rather than noisily, and
        # announces itself in the failure count this row already prints), and it
        # has no automorphism keys. LMKCDEY has a free choice of both, so a
        # record that names neither identifies no configuration, and the row is
        # printed on the fallback but kept OUT of the summary ratio.
        assumed = []
        # A manifest, where one was passed, stands in for fields the runner of
        # the day did not record -- it is the search's own account of the same
        # configuration, not a default.
        man_kd, man_w = dims.get(label, (None, None))
        kd = r.get('keydist') or man_kd
        if kd is None:
            kd = 'UNIFORM_TERNARY'
            if meth != 'GINX':
                assumed.append('keydist')
        w = r.get('autokeys') or man_w
        if w is None and meth == 'LMKCDEY':
            w = 10
            assumed.append('autokeys')
        try:
            pred = m.sigma_total_at_q(r['N'], r['n'], r['q'], r['logQ'], r['qks'],
                                      r['baseks'], 3.19, r['gmap'],
                                      key_dist=kd, method=meth,
                                      **({'autokeys': w} if w else {}))
        except Exception as exc:
            print("  %-22s %-7s %-4s %-6d %-6d %-9.3f %s"
                  % (label, meth, 'G' if kd == 'GAUSSIAN' else 'T',
                     r['N'], r['n'], meas, "model refuses: %s" % exc))
            continue
        note = ""
        if not m.measurement_usable_for_fit(meas, r['q'], r.get('inputs') or 2):
            note = "  AT THE WRAP CEILING: not a sigma"
            excluded.append(label)
        elif not m.within_model_domain(r['N'], r['qks'], r['baseks'], 3.19):
            note = "  outside the model's domain (key-switch saturated)"
            excluded.append(label)
        elif assumed:
            note = "  %s not recorded: predicted on a DEFAULT, not this run" \
                % " and ".join(assumed)
            excluded.append(label)
        else:
            ratios.append(pred / meas)
        # The secret distribution is shown because it is now a searched
        # dimension: two rows of identical geometry and different sk are
        # different configurations, and the column is what makes that visible.
        print("  %-22s %-7s %-4s %-6d %-6d %-9.3f %-9.3f %+7.2f%% %d%s"
              % (label, meth, 'G' if kd == 'GAUSSIAN' else 'T',
                 r['N'], r['n'], meas, pred,
                 100 * (pred - meas) / meas, fails, note))
        if fails:
            print("       ^ FAILURES > 0: this configuration decrypts incorrectly. "
                  "No sigma sees that.")

    if ratios:
        print("\n  predicted/measured sigma over %d scored candidate%s: "
              "median %.3f  min %.3f  max %.3f"
              % (len(ratios), "" if len(ratios) == 1 else "s",
                 statistics.median(ratios), min(ratios), max(ratios)))
    if excluded:
        print("  excluded from the summary: %s" % ", ".join(excluded))
    return 0


def cmd_table(a):
    # The table driver is its own module because it composes the pipeline rather
    # than adding to it: one search per cell, ONE plan for all of them, and one
    # pass that certifies the measurements and emits both the table rows and the
    # enum labels. See dse_table.py's own --help.
    import dse_table as t
    return t.main(a.rest)


def cmd_costfit(a):
    # dse_gatefit is the canonical fitter: it produced the shipped cells, and it
    # separates LMKCDEY's c3 on the dedicated w-arm rather than fitting all four
    # jointly, which is rank-deficient wherever w is held at 10.
    import dse_gatefit as gf
    argv = [a.log] + (['--emit'] if a.emit else [])
    if a.word_size:
        argv += ['--word-size', str(a.word_size)]
    if a.log_q:
        argv += ['--log-q', str(a.log_q)]
    # Forwarded rather than left to the fitter's default: this front end used to
    # drop it, so `dse.py costfit` silently fitted a different objective from the
    # one that produced the committed cells.
    argv += ['--weight', a.weight]
    return gf.main(argv)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='dse', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # Which build the cost cells should describe. Cells are only valid for the
    # thread count and compiler they were measured under: clang has measured
    # ahead single-threaded and gcc ahead multi-threaded, so this REORDERS
    # builds rather than scaling them. An unmeasured regime holds no cells and
    # every candidate is refused, which is the honest failure.
    ap.add_argument('--threads', choices=('single', 'multi'), default=None,
                    metavar='MODE',
                    help='thread regime the ranking should optimise for '
                         '(default multi; env DSE_COST_THREADS)')
    ap.add_argument('--compiler', choices=('clang', 'gcc'), default=None,
                    help='compiler the ranking should optimise for '
                         '(default clang; env DSE_COST_COMPILER)')

    sub = ap.add_subparsers(dest='cmd', required=True)

    d = sub.add_parser('doctor', help='check this checkout is usable')
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser('search', help='rank candidates (arithmetic only)')
    s.add_argument('--target', type=float, required=True,
                   help='log2 failure probability, e.g. -64')
    s.add_argument('--level', default='STD128')
    s.add_argument('--method', default='GINX', choices=('GINX', 'AP', 'LMKCDEY'))
    s.add_argument('--key-dist', default=None, dest='key_dist',
                   choices=('UNIFORM_TERNARY', 'GAUSSIAN'),
                   help='pin the secret distribution; default enumerates every '
                        'one the method can represent and lets the front choose')
    s.add_argument('--word-size', type=int, choices=(32, 64), dest='word_size')
    s.add_argument('--inputs', type=int, choices=(2, 3, 4))
    s.add_argument('--multi-base', action='store_true', dest='multi_base')
    s.add_argument('--security', default='table', choices=('table', 'estimator'))
    s.add_argument('--tolerance', type=int, default=0,
                   help='bits below nominal a level may certify (estimator only)')
    s.add_argument('--limit', type=int,
                   help='stop after this many GRID POINTS. It truncates the enumeration, not the printed rows: a small value makes the search refuse everything for lack of candidates, which reads exactly like a real refusal')
    s.add_argument('--explain', action='store_true')
    s.add_argument('--save', help='write the frontier to JSON for `plan`')
    s.set_defaults(fn=cmd_search)

    p = sub.add_parser('plan', help='emit measurement commands for saved picks')
    p.add_argument('--picks', required=True, help='JSON from `search --save`')
    p.add_argument('--keys', type=int, default=12)
    p.add_argument('--samples', type=int, default=1250, help='gates per key')
    p.add_argument('--order', choices=('decision', 'frontier'), default='decision',
                   help="'decision' (default) measures the rows whose verdict the model cannot "
                        "settle first -- margin within its own band; 'frontier' keeps the saved order")
    p.add_argument('--model-limited', action='store_true',
                   help='only the frontier rows the model cannot decide (margin inside its band)')
    p.add_argument('--all', action='store_true',
                   help='every frontier point, not just the policy pick')
    p.add_argument('--out')
    p.set_defaults(fn=cmd_plan)

    c = sub.add_parser('decide', help='pass/fail from a measurement log')
    c.add_argument('log')
    c.add_argument('--target', type=float, required=True)
    c.add_argument('--inputs', type=int, default=2)
    c.add_argument('-q', '--ct-modulus', type=int, required=True, dest='ct_modulus')
    c.add_argument('--samples', type=int)
    c.add_argument('--model-band', type=float, default=0.0)
    c.set_defaults(fn=cmd_decide)

    val = sub.add_parser('validate', help='model vs measurement, with a control')
    # For a log written before run-plan.sh recorded keydist and autokeys. Those
    # are searched dimensions, so scoring an LMKCDEY row without them means
    # scoring a different configuration; with the manifest the row is scored, and
    # without it the row is excluded rather than guessed.
    val.add_argument('--manifest', help='a dse.py table manifest.json, to supply '
                                        'dimensions the log does not record')
    val.add_argument('log')
    val.set_defaults(fn=cmd_validate)

    tb = sub.add_parser('table',
                        help='generate a whole parameter table (cross product of '
                             'level, arity, method and target) in one command')
    tb.add_argument('rest', nargs=argparse.REMAINDER,
                    help="passed to dse_table.py; start with 'plan' or 'finish'")
    tb.set_defaults(fn=cmd_table)

    cf = sub.add_parser('costfit',
                        help='fit GATE_COST cells from a gate-timing log')
    cf.add_argument('log')
    cf.add_argument('--word-size', type=int, default=None, choices=(32, 64),
                    help='word size for rows with no word or build field, e.g. '
                         'hybcost logs where every row is the hybrid')
    cf.add_argument('--log-q', type=int, default=None,
                    help='logQ for logs that do not record it '
                         '(gatecost-hybrid.sh holds it at 27)')
    cf.add_argument('--weight', choices=('absolute', 'relative'),
                    default='relative',
                    help='least-squares objective (default relative, which is how '
                         'every shipped cell was fitted); use ONE objective for '
                         'every cell in a table')
    cf.add_argument('--emit', action='store_true',
                    help='print a paste-ready GATE_COST block')
    cf.set_defaults(fn=cmd_costfit)

    a = ap.parse_args(argv)
    if a.threads or a.compiler:
        import dse_model as m
        m.set_cost_regime(a.threads, a.compiler)
        if not m.regime_is_measured():
            print("WARNING: %s\n" % m.cost_provenance())
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
