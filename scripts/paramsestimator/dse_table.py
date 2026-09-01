#!/usr/bin/env python3
"""Generate a whole parameter table in one command: the cross product of security
level, gate arity, bootstrapping method and failure target.

    sage -python scripts/paramsestimator/dse_table.py plan \
        --levels all --methods all --inputs all --targets=-64,-128 \
        --security estimator --tolerance 1 --jobs 8 --out-dir table

    <measure the one plan file that produces>

    sage -python scripts/paramsestimator/dse_table.py finish \
        --dir table --log table/plan.log

WHY THIS EXISTS. The per-candidate pipeline is four commands, which is right when
you are deciding one configuration and wrong when you are regenerating a table:
six levels x three arities x three methods x two targets is 108 sets, and driving
that by hand is 432 commands and a fortnight of care. The parameter selector has
had `--all` for exactly this reason; this is the search-based flow's equivalent,
and it collapses the same job to three commands.

WHAT IT DOES NOT DO. It does not measure. `plan` emits ONE runner file covering
every cell, and `finish` reads the log back; the measurement in between is still
an explicit step on an idle machine, for the same reasons it always was. Nor does
it hide an empty cell: a level/arity/method/target combination with no survivor
is reported as such, with the gate that refused it, because "no configuration
exists at this target" is a result a table needs to state rather than omit.

NAMING. Cells are named the way OpenFHE names them, so the output is pastable:
the level, then `_3`/`_4` for arity above two, then `_LMKCDEY`/`_AP` for a method
other than GINX, with `LPF_` in front for the low-failure target. That
reproduces every shipped name exactly (STD128, STD128_4_LMKCDEY, LPF_STD128Q,
STD128_AP), which is what lets `finish` emit a row you can paste and a label you
can diff.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ALL_LEVELS = ("STD128", "STD128Q", "STD192", "STD192Q", "STD256", "STD256Q")
ALL_METHODS = ("AP", "GINX", "LMKCDEY")
ALL_INPUTS = (2, 3, 4)

# A target at or below this counts as the low-failure family, which is what the
# LPF_ prefix means in OpenFHE: the same design at a lower failure target, not a
# different kind of set.
LPF_TARGET = -100.0


def set_name(level, inputs, method, target):
    """The name OpenFHE would give this cell.

    Verified against the shipped table: STD128, STD128_3, STD128_4,
    STD128_LMKCDEY, STD128_3_LMKCDEY, STD128_AP, LPF_STD128Q_LMKCDEY.
    """
    name = level
    if inputs != 2:
        name += "_%d" % inputs
    if method != "GINX":
        name += "_%s" % method
    if target <= LPF_TARGET:
        name = "LPF_" + name
    return name


def cells(levels, methods, inputs, targets):
    return [dict(level=l, method=m, inputs=i, target=t,
                 name=set_name(l, i, m, t))
            for t in targets for l in levels for m in methods for i in inputs]


_KEY_REFS = None


def shipped_key_refs():
    """{shipped set name: model-priced key bytes} from the PRE-RE-SELECTION table.

    The reference for a per-cell key cap is the key material the library shipped
    for that set before this tool touched it (Carlo, LIB-62 postscript). Priced by
    the same model the candidates are priced by, so cap and candidate are in the
    same units; the model matched icelake's VmHWM on 105 rows to a median 1.009.
    """
    global _KEY_REFS
    if _KEY_REFS is not None:
        return _KEY_REFS
    import dse_model as m
    import dse_shipfit as sf
    P = sf.shipped_params(strict=False, src=sf.PRE_RESELECT_SRC)
    refs = {}
    for name, p in P.items():
        if not (name.startswith('STD') or name.startswith('LPF_STD')):
            continue
        meth = sf.method_of(name)
        gm = dict(p['gmap'])
        word = 32 if p['logQ'] <= 28 else 64
        try:
            rb, sb = m.key_word_bytes(p['N'], p['logQ'], p['qks'], p['bks'], gm, meth, word)
            refs[name] = (m.ksk_bytes(p['N'], p['n'], p['qks'], p['bks'], sb)
                          + m.btkey_bytes(p['N'], gm, p['logQ'], rb, method=meth,
                                          autokeys=p['autokeys'],
                                          base_r=(p['brk'] if meth == 'AP' else None), q=p['q']))
        except Exception:
            continue
    _KEY_REFS = refs
    return refs


def key_cap_for(name, refs, mult):
    """(cap_bytes, reference set name) for a cell, or (None, None) with no reference.

    The shipped table has 41 STD/LPF rows against 108 cells, so most cells have no
    row of their own name. The chain is: the same name; the name without its
    method suffix (STD128_3_AP -> STD128_3, the GINX row of the same level and
    arity); then without the LPF_ prefix (LPF_STD192_3 -> STD192_3, since the
    library shipped LPF rows only at STD128). A cell that resolves to nothing is
    left to the global --max-key-mib, and the manifest says so.
    """
    if not mult:
        return None, None
    cands = [name]
    base = name
    for suf in ('_AP', '_LMKCDEY'):
        if base.endswith(suf):
            base = base[:-len(suf)]
    if base != name:
        cands.append(base)
    if base.startswith('LPF_'):
        cands.append(base[4:])
    for c in cands:
        if c in refs:
            return refs[c] * float(mult), c
    return None, None


# The key-cap level a fresh run applies unless told otherwise (Carlo, 2026-09-09):
# 4 GiB for GINX and LMKCDEY, 8 GiB for AP, whose keys run 5-10x GINX's at the
# same gate time. Up to the level keys are simply manageable and the pick is
# gate-first; beyond it the key/gate trade is scored at --key-cap-lambda.
KEY_CAP_DEFAULT = "4,AP=8"


def parse_key_cap(spec):
    """A --key-cap-gib spec -> {method or '*': GiB}, or None for no cap.

        "4"            every method at 4 GiB
        "4,AP=8"       4 GiB by default, AP at 8
        "GINX=4,LMKCDEY=4,AP=8"
        "none" / "0" / ""   no absolute cap

    Also accepts a number (the pre-2026-09-09 form) and a dict already parsed
    (a manifest round-trip). Unknown method names and two default levels are
    errors rather than silently ignored -- a misspelt method would otherwise
    fall back to the default level and look like a policy choice.
    """
    if spec is None:
        return None
    if isinstance(spec, dict):
        return {k: float(v) for k, v in spec.items()} or None
    if isinstance(spec, (int, float)):
        return {'*': float(spec)} if spec else None
    text = str(spec).strip()
    if text.lower() in ('', 'none', 'off', '0'):
        return None
    caps = {}
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '=' in part:
            k, v = part.split('=', 1)
            k = k.strip().upper()
            if k not in ALL_METHODS:
                raise ValueError("--key-cap-gib: unknown method %r in %r (have %s)"
                                 % (k, spec, ", ".join(ALL_METHODS)))
            caps[k] = float(v)
        else:
            if '*' in caps:
                raise ValueError("--key-cap-gib: two default levels in %r" % spec)
            caps['*'] = float(part)
    if '*' not in caps and set(caps) != set(ALL_METHODS):
        raise ValueError("--key-cap-gib: %r names no default level and not every "
                         "method; add a bare number or name all of %s"
                         % (spec, ", ".join(ALL_METHODS)))
    return caps


def key_cap_gib_for(method, caps):
    """The cap level (GiB) for one method under a parsed spec, or None."""
    if not caps:
        return None
    gib = caps.get(method, caps.get('*'))
    return float(gib) if gib else None


def _method_of(name):
    for suf, meth in (('_AP', 'AP'), ('_LMKCDEY', 'LMKCDEY')):
        if name.endswith(suf):
            return meth
    return 'GINX'


def key_cap_spec_text(caps):
    """The inverse of parse_key_cap, for manifests and reports."""
    if not caps:
        return "none"
    parts = ([("%g" % caps['*'])] if '*' in caps else []) + \
            ["%s=%g" % (k, v) for k, v in sorted(caps.items()) if k != '*']
    return ",".join(parts)


def _select(front, name, max_key_mib, key_cap_mult, key_cap_gib=None, key_cap_lambda=None,
            method=None, key_cap_hard=False):
    """Apply the global ceiling, then the key-cap policy. -> (pick, info)

    Two ways to set the cap. `key_cap_gib` is an ABSOLUTE threshold -- the level
    up to which keys are simply manageable -- set PER METHOD (parse_key_cap; the
    default is KEY_CAP_DEFAULT), which matters: AP's keys run 5-10x GINX's at the
    same gate time, so one level for all three either starves AP or frees GINX.
    `key_cap_mult` is the relative form (a multiple of the shipped row's key,
    resolved through key_cap_for), kept for comparison against the table the
    library shipped. Absolute wins where both are given.
    """
    import dse_enumerate as e
    cap_g = max_key_mib * (1 << 20) if max_key_mib else 0
    pickable = [f for f in front if not cap_g or f['key_bytes'] <= cap_g]
    if key_cap_lambda is None:
        key_cap_lambda = e.KEY_CAP_LAMBDA
    info = dict(key_cap_mib=None, key_cap_ref=None, over_cap=False, key_cap_lambda=None,
                key_cap_rule=None)
    if not pickable:
        return None, info
    cap, ref = None, None
    gib = key_cap_gib_for(method or _method_of(name), parse_key_cap(key_cap_gib))
    if gib:
        cap, ref = gib * (1 << 30), 'absolute'
    elif key_cap_mult:
        cap, ref = key_cap_for(name, shipped_key_refs(), key_cap_mult)
    if cap:
        pick, over = e.key_cap_policy(pickable, cap, key_cap_lambda, hard=key_cap_hard)
        info.update(key_cap_mib=cap / 2 ** 20, key_cap_ref=ref, over_cap=over,
                    key_cap_lambda=key_cap_lambda,
                    key_cap_rule='hard' if key_cap_hard else 'soft')
        if pick is not None:
            pick['over_cap'] = over
        return pick, info
    return e.default_policy(pickable), info


def search_cell(cell, security="estimator", tolerance=1, multi_base=False,
                word_size=None, key_dist=None, limit=None, threads=None,
                compiler=None, max_key_mib=0, ap_bases=None,
                refine=True, autokeys_grid=False, boundary=True, key_cap_mult=None,
                key_cap_gib=None, key_cap_lambda=None, key_cap_hard=False,
                dropped_ks=(0,), extrapolate_n=None):
    """Search one cell. Returns a result dict; never raises for an empty cell.

    The secret distribution is left unpinned by default, so each method offers
    every distribution it can represent and the front decides -- which is the
    whole point of a table: STD192's shipped LMKCDEY rows take a Gaussian secret
    to reach n=716 where the ternary ones need 821, and that should be a search
    result rather than a per-cell input.
    """
    import dse_enumerate as e
    import dse_model as m
    if threads or compiler:
        m.set_cost_regime(threads, compiler)
    grid = {'inputs': cell['inputs']}
    # AP's refresh base multiplies its grid by however many values are offered,
    # and the exact product count is monotone in it, so a COARSE sample spans the
    # trade at a quarter of the search cost: 2, 8, 32, 128 give 5.50, 3.38, 2.44
    # and 1.93 products per coefficient at q = 2048, where the full list only
    # interpolates between them at intermediate key sizes. A table sweep wants
    # the span; a single-set search can pass the full list.
    if ap_bases:
        grid['base_r'] = tuple(ap_bases)
    try:
        total, counts, kept = e.run(
            cell['target'], method=cell['method'], key_dist=key_dist,
            grid=grid, word_size=word_size, multi_base=multi_base, limit=limit,
            level=cell['level'], sec_source=security, sec_tolerance=tolerance,
            refine=refine, autokeys_grid=autokeys_grid, boundary=boundary,
            dropped_ks=dropped_ks, extrapolate_n=extrapolate_n)
    except Exception as exc:                      # a cell must not kill the table
        return dict(cell, **cap_info, error="%s: %s" % (type(exc).__name__, exc),
                    enumerated=0, survived=0, front=[], pick=None)
    front = e.pareto(kept) if kept else []
    # A key-material ceiling, because the default policy weighs key size only to
    # break ties and AP can offer a front row whose refresh key runs to hundreds
    # of GiB -- (baseR-1)*digitsR keys PER COEFFICIENT. Measuring one of those
    # unattended is an OOM, not a result, so the pick is chosen from the rows that
    # actually fit. The front is kept whole either way, so nothing is hidden.
    pickable = ([f for f in front if f['key_bytes'] <= max_key_mib * (1 << 20)]
                if max_key_mib else front)
    pick, cap_info = _select(front, cell['name'], max_key_mib, key_cap_mult,
                             key_cap_gib, key_cap_lambda, method=cell['method'],
                             key_cap_hard=key_cap_hard)
    # The dominant refusal is the actionable part of an empty cell: "cost
    # uncalibrated" means measure a regime, "security (unpriced)" means price a
    # curve, and "below target" means no configuration reaches it.
    worst = max(counts.items(), key=lambda kv: kv[1])[0] if counts else None
    return dict(cell, enumerated=total, survived=len(kept), front=front,
                pick=pick, counts=counts, dominant=worst, error=None,
                over_key_cap=len(front) - len(pickable))


def _jsonable(c):
    return {k: (list(v.items()) if isinstance(v, dict) else v)
            for k, v in c.items() if not k.startswith('_')}


def _worker(args):
    cell, kw = args
    r = search_cell(cell, **kw)
    r['front'] = [_jsonable(x) for x in r['front']]
    r['pick'] = _jsonable(r['pick']) if r['pick'] else None
    return r


def plan(a):
    import dse_verify as v
    levels = ALL_LEVELS if a.levels == 'all' else tuple(a.levels.split(','))
    methods = ALL_METHODS if a.methods == 'all' else tuple(a.methods.split(','))
    ins = ALL_INPUTS if a.inputs == 'all' else tuple(int(x) for x in a.inputs.split(','))
    targets = tuple(float(x) for x in a.targets.split(','))
    todo = cells(levels, methods, ins, targets)
    os.makedirs(a.out_dir, exist_ok=True)

    kw = dict(security=a.security, tolerance=a.tolerance, multi_base=a.multi_base,
              refine=a.refine, autokeys_grid=a.autokeys_grid, boundary=a.boundary,
              key_cap_mult=a.key_cap_mult, key_cap_gib=a.key_cap_gib,
              key_cap_lambda=a.key_cap_lambda, key_cap_hard=a.key_cap_hard,
              dropped_ks=tuple(int(x) for x in a.dropped_ks.split(',')),
              word_size=a.word_size, key_dist=a.key_dist, limit=a.limit,
              threads=a.threads, compiler=a.compiler, max_key_mib=a.max_key_mib,
              ap_bases=(tuple(int(x) for x in a.ap_bases.split(',')) if a.ap_bases else None),
              extrapolate_n=getattr(a, 'extrapolate_n', None))
    print("%d cells: %d level(s) x %d aritie(s) x %d method(s) x %d target(s)"
          % (len(todo), len(levels), len(ins), len(methods), len(targets)))
    print("searching%s ...\n" % ("" if a.jobs == 1 else " with %d workers" % a.jobs))

    if a.jobs > 1:
        import multiprocessing as mp
        with mp.Pool(a.jobs) as pool:
            results = pool.map(_worker, [(c, kw) for c in todo])
    else:
        results = [_worker((c, kw)) for c in todo]

    print("%-24s %-8s %10s %10s %10s %9s  %s"
          % ("set", "method", "enumerated", "survived", "gate us", "log2Pf", "outcome"))
    ok, empties = [], []
    for r in results:
        if r['error']:
            outcome = "ERROR %s" % r['error']
        elif r['pick']:
            outcome = "pick"
            ok.append(r)
        elif r['front']:
            outcome = "front but no pick (no row clears its own band)"
        else:
            outcome = "EMPTY -- dominant refusal: %s" % r.get('dominant')
            empties.append(r)
        p = r['pick']
        print("%-24s %-8s %10d %10d %10s %9s  %s"
              % (r['name'], r['method'], r['enumerated'], r['survived'],
                 ("%.0f" % p['gate_us']) if p else "-",
                 ("%.1f" % p['log2pf']) if p else "-", outcome))

    # An empty cell that a command would fix says so once, grouped, rather than
    # leaving the reader to map a refusal name onto an arm.
    if empties:
        import dse_enumerate as _e
        seen = {}
        for r in empties:
            fix = _e.remedy(r.get('dominant'), level=r['level'], method=r['method'],
                            tolerance=a.tolerance)
            if fix:
                seen.setdefault((r.get('dominant'), fix), []).append(r['name'])
        if seen:
            print("\nempty cells a command would fix:")
            for (reason, fix), names in seen.items():
                print("  %s -- %d cell(s): %s" % (reason, len(names), ", ".join(names[:4])
                                                  + (" ..." if len(names) > 4 else "")))
                print("      %s" % fix)

    # The SELECTION POLICY is recorded, not just the picks. A manifest from
    # before the quantile budget and one from after contain the same fields with
    # different meanings -- the earlier picks were selected on the mean-key
    # log2Pf and the later ones on the certifiable quantile -- and a table whose
    # rows cannot say which rule chose them cannot be compared with one that can.
    import dse_model as _m
    manifest = dict(regime_label="%s/%s" % (a.threads or 'multi', a.compiler or 'clang'),
                    levels=levels, methods=methods, inputs=ins, targets=targets,
                    security=a.security, tolerance=a.tolerance,
                    multi_base=a.multi_base, keys=a.keys, samples=a.samples,
                    quantile_budget=True,
                    per_key_log2pf_scatter=_m.PER_KEY_LOG2PF_SCATTER,
                    quantile_z=_m.Z_90,
                    # How the gadget map and numAutoKeys were reached, because
                    # the same `pick` field means different searches: a table
                    # from before 2026-09-08 enumerated 5 numAutoKeys values and
                    # no two-base maps at all.
                    map_refinement=bool(a.refine and not a.multi_base),
                    map_boundary=bool(a.refine and not a.multi_base and a.boundary),
                    key_cap_mult=a.key_cap_mult, key_cap_gib=parse_key_cap(a.key_cap_gib),
                    key_cap_lambda=a.key_cap_lambda,
                    key_cap_rule='hard' if a.key_cap_hard else 'soft',
                    dropped_ks=a.dropped_ks,
                    # Recorded because a table containing an extrapolated ring
                    # dimension is a different claim from one that does not, and
                    # the manifest is what a later reader has.
                    extrapolate_n=getattr(a, 'extrapolate_n', None),
                    key_layout=dict(ksk_top_compact=_m.KSK_TOP_COMPACT,
                                    rk_top_compact=_m.RK_TOP_COMPACT,
                                    ksk_zero_rows_dropped=_m.KSK_ZERO_ROWS_DROPPED),
                    map_grid=bool(a.multi_base),
                    autokeys_grid=bool(a.autokeys_grid),
                    cells=results)
    mpath = os.path.join(a.out_dir, 'manifest.json')
    with open(mpath, 'w') as f:
        json.dump(manifest, f, indent=1, default=str)

    # ONE plan for every cell that has a pick. The label carries the set name, so
    # `finish` can put a measurement back with the cell that asked for it without
    # matching on parameters (three rows once shared a predicted sigma to within
    # 1%, which is how label matching came to be the rule).
    # Order the plan by how likely a row is to be wanted: the primary target
    # before the low-failure one, two inputs before three and four, GINX before
    # LMKCDEY before AP. run-plan walks the file in order, so a run that is
    # interrupted -- or simply reviewed in the morning -- has measured the rows
    # that matter rather than an arbitrary third of them.
    order = {'GINX': 0, 'LMKCDEY': 1, 'AP': 2}
    ok.sort(key=lambda r: (0 if r['target'] > LPF_TARGET else 1,
                           r['inputs'], order.get(r['method'], 9), r['level']))
    ppath = os.path.join(a.out_dir, 'plan.cmds')
    _write_plan(ppath, ok, len(results), a.keys, a.samples)

    print("\n%d of %d cells have a pick." % (len(ok), len(results)))
    print("plan:     %s   (%d runs)" % (ppath, len(ok) * a.keys))
    print("manifest: %s" % mpath)
    print("\nnext:  JOBS=$(nproc) bash scripts/run-plan.sh %s > %s/plan.log" % (ppath, a.out_dir))
    print("then:  dse_table.py finish --dir %s" % a.out_dir)
    return 0


def finish(a):
    import dse_model as m
    import dse_verify as v
    with open(os.path.join(a.dir, 'manifest.json')) as f:
        man = json.load(f)
    log = a.log or os.path.join(a.dir, 'plan.log')
    if not os.path.exists(log):
        sys.exit("no measurement log at %s -- run the plan first" % log)
    groups = v.read_keys(log)
    # A label is a CLAIM about failure probability that ships in OpenFHE's enum
    # comments. Sigma cannot see a gate that decrypted incorrectly, so a cell
    # with wrong answers is excluded from what this emits rather than labelled
    # from its noise distribution alone.
    fails_by = v.read_failures(log)
    by_name = {c['name']: c for c in man['cells']}
    # Say which selection rule produced these picks. Without this line a reader
    # comparing "predicted" against "certified" on an old table has no way to
    # know the two columns are different statistics, which is exactly the
    # confusion that let seven short cells look like model failures.
    if not man.get('quantile_budget'):
        print("NOTE: these picks were selected on the MEAN-key log2Pf, before the")
        print("      quantile budget existed. Read the `certifiable` column, not")
        print("      `predicted`, against `certified` -- and expect cells whose")
        print("      predicted value clears the target while the verdict does not.")
        print()

    # Two predicted columns, because the verdict compares against the SECOND
    # one. "predicted" is the mean key, which `pooled` is the measurement of;
    # "certifiable" adds the quantile penalty and is what `certified` should be
    # read against. A single predicted column invited exactly the wrong
    # comparison: seven cells of the 2026-09-08 table had a predicted log2Pf
    # comfortably past target and certified short of it, which looks like model
    # error and is entirely the missing penalty.
    print("%-24s %9s %11s %9s %9s %7s %10s %8s  %s"
          % ("set", "predicted", "certifiable", "pooled", "certified",
             "+-unc", "suggested", "verdict", "gate us"))
    rows = []
    for name, keys in sorted(groups.items()):
        c = by_name.get(name)
        if not c or not c['pick']:
            print("%-24s  (measured but not in the manifest)" % name)
            continue
        p = c['pick']
        gm = {int(b): int(n) for b, n in p['gadget_map']}
        pooled = (sum(s * s for s, _ in keys) / len(keys)) ** 0.5
        pooled_pf = m.log2_pf(pooled, p['inputs'], p['q'])
        cert, unc, det = v.certify(keys, p['inputs'], p['q'],
                                   samples_per_key=man['samples'])
        verdict, _ = v.decide(keys, p['inputs'], p['q'], c['target'],
                              samples_per_key=man['samples'], model_band=0.0)
        wrong, counted, uncounted = fails_by.get(name, (0, 0, 0))
        if wrong:
            verdict = 'fail'
        import math
        # Toward zero, so the label never claims more than was measured. A whole
        # bit by default rather than the existing multiple-of-5 style, which
        # would give away up to five bits of a set that was tuned to a target:
        # a certified -64.9 deserves 2^-64, not 2^-60. --round 5 keeps the old
        # style where matching it matters more than the margin.
        step = max(1, a.round)
        # FROM THE PESSIMISTIC END of the measurement, not the point estimate.
        # The label is a CLAIM about failure probability that ships in OpenFHE's
        # enum comments, so it has to be one the measurement supports even at the
        # unfavourable end of its own uncertainty. Taken from `cert` alone,
        # rounding to the nearest bit leaves about half a bit of room against an
        # uncertainty of 0.7 to 2.5 bits, so 105 of the 2026-09-08 table's 106
        # labels could overstate; taken from `cert + unc` all 106 are honest at
        # the 8 keys already measured. It costs a median of 3 bits.
        #
        # `--label-from point` restores the old behaviour for comparing against
        # an earlier table, and is not a mode to emit a shipping label in.
        edge = cert if a.label_from == 'point' else cert + unc
        suggested = -int(math.floor(abs(edge) / step) * step)
        rows.append(dict(name=name, cell=c, pick=p, gmap=gm, pooled=pooled,
                         pooled_pf=pooled_pf, cert=cert, unc=unc,
                         verdict=verdict, suggested=suggested,
                         wrong=wrong, unchecked=bool(uncounted and not counted)))
        # A manifest written before the penalty existed has no log2pf_cert, so it
        # is computed here from the pick's own predicted sigma rather than left
        # blank -- the whole point is to be able to read an old table correctly.
        cert_pred = p.get('log2pf_cert')
        if cert_pred is None:
            cert_pred = p['log2pf'] + m.quantile_penalty(
                p['sigma_total'], p['inputs'], p['q'])
        # The uncertainty travels WITH the certified value. A certified -134.7 on
        # its own is half a statement, and the artifact the library's generator
        # parses prints them as one field ("-134.70 +- 0.90").
        print("%-24s %9.1f %11.1f %9.1f %9.1f %7.2f %10s %8s  %.0f%s"
              % (name, p['log2pf'], cert_pred, pooled_pf, cert, unc,
                 "2^%d" % suggested, verdict.upper(), p['gate_us'],
                 "   WRONG ANSWERS: %d" % wrong if wrong else
                 "   (no FAILURES field)" if uncounted and not counted else ""))

    bad = [r for r in rows if r['wrong']]
    emitted = [r for r in rows if not r['wrong']]
    if a.emit_rows:
        print("\n// BinFHEContextParams rows: numberBits, cyclOrder, latticeParam, mod,")
        print("// modKS, baseKS, gadgetBase, baseRK, numAutoKeys, keyDist, stdDev, gadgetBaseMap")
        for r in emitted:
            p, gm = r['pick'], r['gmap']
            mp = ", ".join("{%d, %d}" % (b, n) for b, n in sorted(gm.items()))
            print("{ %-22s {  %3d, %5d, %5d, %5d, %7d, %3d, %8d, %3d, %3d, %s, %.2f, {%s} } },"
                  % (r['name'] + ",", p['log_q_big'], 2 * p['N'], p['n'], p['q'],
                     p['q_ks'], p['base_ks'], min(gm),
                     p.get('base_r') or 64, p['autokeys'], p['key_dist'],
                     p['sigma'], mp))

    if a.emit_labels:
        print("\n// enum comments: the 90th-percentile key, rounded toward zero")
        for r in emitted:
            print("    %-22s // %s : 2^(%d)"
                  % (r['name'] + ",", r['cell']['level'], r['suggested']))

    unchecked = [r['name'] for r in rows if r['unchecked']]
    if bad:
        print("\n%d cell(s) DECRYPT INCORRECTLY and are excluded from everything"
              " emitted above:" % len(bad))
        for r in bad:
            print("    %-24s %d wrong answer(s)" % (r['name'], r['wrong']))
        print("  No sigma sees that, and a label is a claim about failure")
        print("  probability, not about the noise distribution alone.")
    if unchecked:
        print("\n%d cell(s) carry no FAILURES field, so no wrong-answer check was"
              " made for them: %s" % (len(unchecked), ", ".join(unchecked)))
    if rows and not bad and not unchecked:
        print("\ncorrectness: 0 wrong answers over %d cell(s)" % len(rows))

    missing = [c['name'] for c in man['cells'] if c['pick'] and c['name'] not in groups]
    if missing:
        print("\n%d cell(s) planned but not measured: %s"
              % (len(missing), ", ".join(missing)))
    empty = [c['name'] for c in man['cells'] if not c['pick']]
    if empty:
        print("%d cell(s) had no pick and were never planned: %s"
              % (len(empty), ", ".join(empty)))
    return 1 if bad else 0


def compare(a):
    """Two table runs side by side, each pick priced in BOTH regimes.

    The question this answers is not "which run is faster" -- they are priced on
    different cost cells, so that comparison is meaningless -- but "does the
    optimum MOVE, and what does taking the other regime's pick cost you here".
    So every pick is priced under both regimes and both numbers are shown.

    Gate time is the only thing that differs. Noise is identical: it does not
    depend on the thread count at all, so a pick that appears in both runs needs
    measuring once, and a new pick can be measured with every core available even
    though it was chosen for one.
    """
    import dse_model as m
    dirs = a.dirs.split(',')
    if len(dirs) != 2:
        sys.exit("--dirs takes exactly two table directories")
    mans = []
    for d in dirs:
        with open(os.path.join(d, 'manifest.json')) as f:
            mans.append(json.load(f))
    labels = [x.get('regime_label') or os.path.basename(os.path.normpath(d))
              for x, d in zip(mans, dirs)]
    by = [{c['name']: c for c in mm['cells']} for mm in mans]

    def price(pick, threads, compiler):
        if not pick:
            return None
        m.set_cost_regime(threads, compiler)
        gm = {int(b): int(n) for b, n in pick['gadget_map']}
        return m.gate_us(gm, pick['log_q_big'], pick['N'], method=pick['method'],
                         word_size=pick['word_size'], autokeys=pick['autokeys'],
                         base_r=pick.get('base_r'), q=pick['q'])

    regs = [(a.threads_a, a.compiler_a), (a.threads_b, a.compiler_b)]
    print("%-24s %-34s %-34s %s"
          % ("set", "%s pick" % labels[0], "%s pick" % labels[1], "same?"))
    moved = same = 0
    for name in sorted(set(by[0]) | set(by[1])):
        ps = [by[i].get(name, {}).get('pick') for i in (0, 1)]
        def desc(p):
            # EVERY searched dimension has to appear here. An earlier version
            # omitted numAutoKeys and the secret distribution, and reported two
            # LMKCDEY picks as identical when they differed in the automorphism
            # count -- which moves both noise (1/w) and gate time (c3*N/w). The
            # tell was their predicted failure probabilities disagreeing while the
            # display claimed the configurations were the same.
            if not p:
                return "(no pick)"
            gm = {int(b): int(n) for b, n in p['gadget_map']}
            bits = ["n=%d" % p['n'], "lQ=%d" % p['log_q_big'],
                    ",".join("2^%d:%d" % (b.bit_length() - 1, c)
                             for b, c in sorted(gm.items())),
                    "bKS=%d" % p['base_ks']]
            if p.get('method') == 'LMKCDEY':
                bits.append("w=%d" % p['autokeys'])
            if p.get('base_r'):
                bits.append("bR=%d" % p['base_r'])
            if p.get('key_dist') != 'UNIFORM_TERNARY':
                bits.append("GAUSSIAN")
            return " ".join(bits)
        d0, d1 = desc(ps[0]), desc(ps[1])
        identical = (d0 == d1 and d0 != "(no pick)")
        same += identical
        moved += (not identical and "(no pick)" not in (d0, d1))
        print("%-24s %-34s %-34s %s" % (name, d0, d1, "yes" if identical else "MOVED"))
        if a.verbose and not identical:
            for i, p in enumerate(ps):
                if not p:
                    continue
                own = price(p, *regs[i])
                other = price(p, *regs[1 - i])
                print("      %s's pick: %s us in its own regime, %s in the other"
                      % (labels[i], ("%.0f" % own) if own else "unpriced",
                         ("%.0f" % other) if other else "UNPRICED there"))
    print("\n%d cells pick the same configuration, %d move." % (same, moved))
    m.set_cost_regime('multi', 'libomp')
    return 0


LPF_TARGET_ORDER = {'GINX': 0, 'LMKCDEY': 1, 'AP': 2}


def _write_plan(ppath, ok, n_cells, keys, samples):
    """ONE plan for every cell that has a pick, in the order a reader wants it.

    The label carries the set name, so `finish` can put a measurement back with
    the cell that asked for it without matching on parameters (three rows once
    shared a predicted sigma to within 1%, which is how label matching came to
    be the rule). Ordered primary target before low-failure, two inputs before
    three and four, GINX before LMKCDEY before AP: run-plan walks the file in
    order, so an interrupted run has measured the rows that matter first.
    """
    import dse_verify as v
    ok = sorted(ok, key=lambda r: (0 if r['target'] > LPF_TARGET else 1,
                                   r['inputs'], LPF_TARGET_ORDER.get(r['method'], 9),
                                   r['level']))
    with open(ppath, 'w') as f:
        f.write("# Parameter-table measurement plan, %d cells with a pick of %d\n"
                % (len(ok), n_cells))
        f.write("# %d keys x %d gates each, one key per process.\n#\n" % (keys, samples))
        f.write("# Run under scripts/run-plan.sh, which records the binary's own\n"
                "# Failures: count as well as sigma. JOBS=<n> runs n keys at once, one\n"
                "# OpenMP thread each, within a memory budget sized from the key_mib lines.\n")
        for r in ok:
            p = dict(r['pick'])
            if isinstance(p['gadget_map'], list):
                p['gadget_map'] = {int(b): int(n) for b, n in p['gadget_map']}
            f.write("# label: %s\n" % r['name'])
            # what one process of this block holds, for run-plan.sh's memory budget
            f.write("# key_mib: %.0f\n" % (p['key_bytes'] / 2 ** 20))
            f.write("#   %s target %.1f  predicted sigma %.2f  log2Pf %.1f +-%.1f"
                    "  gate %.0f us  keys %.0f MiB\n"
                    % (r['name'], r['target'], p['sigma_total'], p['log2pf'],
                       p['band'], p['gate_us'], p['key_bytes'] / 2 ** 20))
            for _ in range(keys):
                f.write(v.command(p, samples) + "\n")


def _revive(row):
    """A JSON-round-tripped frontier row back into the dict evaluate() emits."""
    r = dict(row)
    if isinstance(r.get('gadget_map'), list):
        r['gadget_map'] = {int(b): int(n) for b, n in r['gadget_map']}
    if isinstance(r.get('shares'), list):
        r['shares'] = dict(r['shares'])
    if isinstance(r.get('gadget_shape_keys'), list):
        r['gadget_shape_keys'] = tuple(r['gadget_shape_keys'])
    return r


def _reprice(front):
    """Stored frontier rows -> the same rows re-evaluated under the CURRENT model.

    Returns (rows, moved) where moved lists gate_us(new)/gate_us(stored) - 1 for
    every row that could be re-evaluated. A row evaluate() now refuses (its cost
    cell withdrawn, say) is kept as stored and marked repriced=False rather than
    dropped: the frontier is the record of what the search found, and a pick
    from an unpriced row is what the OVER-CAP / uncalibrated reporting exists
    to surface.
    """
    import dse_enumerate as e
    out, moved = [], []
    for r in front:
        try:
            ev = e.evaluate(r)
        except Exception:
            ev = None
        if isinstance(ev, dict):
            ev['stored_gate_us'] = r.get('gate_us')
            ev['repriced'] = True
            if r.get('gate_us'):
                moved.append(ev['gate_us'] / float(r['gate_us']) - 1.0)
            out.append(ev)
        else:
            r = dict(r)
            r['repriced'] = False
            r.setdefault('log2pf_cert', r['log2pf'] + m_quantile(r))
            out.append(r)
    return out, moved


def m_quantile(r):
    import dse_model as m
    return m.quantile_penalty(r['sigma_total'], r['inputs'], r['q'])


def _same_pick(a, b):
    if (a is None) != (b is None):
        return False
    if a is None:
        return True
    ka = (a['n'], a['N'], a['log_q_big'], a['q_ks'], a['base_ks'], a.get('autokeys'),
          sorted(_revive(a)['gadget_map'].items()), a.get('key_dist'), a.get('base_r'))
    kb = (b['n'], b['N'], b['log_q_big'], b['q_ks'], b['base_ks'], b.get('autokeys'),
          sorted(_revive(b)['gadget_map'].items()), b.get('key_dist'), b.get('base_r'))
    return ka == kb


def repick(a):
    """Re-select every cell from its STORED frontier under the current policy.

    A search costs hours; a selection costs seconds, and the manifest keeps each
    cell's whole Pareto front. So when the selection rule changes -- the quantile
    budget, the two-base refinement, the boundary split -- the table can be
    brought up to date without enumerating the grid again: re-score the stored
    rows, refine them, pick. The 2026-09-08 table's 106 picks were re-selected
    this way in under two minutes.

    Every stored row is re-run through evaluate() first (_reprice), so its gate
    time, key bytes, sigma, log2Pf and certifiable statistic all come from the
    CURRENT noise model and cost cells. Before 2026-09-09 only the quantile
    statistic was recomputed and gate_us was carried over from the search; a
    change of cost cells -- the LMKCDEY re-measure at 41709fbc -- would then have
    ranked the stored rows on stale prices while pricing the refinements on
    fresh ones. The repricing is reported (how many rows moved, by how much).

    What it cannot do: a stored front is the non-dominated set under the margin
    axis of the search that produced it. The quantile penalty is close to a
    monotone transform of that axis (about 5 percent of |log2Pf|), so the two
    fronts nearly coincide, but a row dominated under the old axis and not the
    new is not here to be found. A fresh `plan` is the ground truth; this is the
    fast approximation, and the manifest says so (`repicked_from`).
    """
    import dse_enumerate as e
    import dse_model as m
    import dse_security as sec
    with open(os.path.join(a.dir, 'manifest.json')) as f:
        man = json.load(f)
    sec_source = man.get('security', 'estimator')
    tol = man.get('tolerance', 1)
    sec_cache = sec.load() if sec_source == 'estimator' else None
    sec_model = sec.active_model()
    cap = a.max_key_mib * (1 << 20) if a.max_key_mib else 0
    os.makedirs(a.out_dir, exist_ok=True)

    results, rows = [], []
    moved_all, n_stored = [], 0
    for cell in man['cells']:
        tgt, level = cell['target'], cell['level']
        front = [_revive(x) for x in cell.get('front') or []]
        n_stored += len(front)
        # Re-PRICE under the current model: evaluate() recomputes gate_us from
        # the active cost cells and the noise figures from the noise model, so
        # a stored row and a fresh refinement are priced by the same rules.
        front, moved = _reprice(front)
        moved_all.extend(moved)
        for r in front:
            r['net_margin'] = tgt - r['log2pf_cert'] - r['band']
            r['neg_margin'] = -r['net_margin']
        front = e.pareto([r for r in front if r['log2pf_cert'] <= tgt])

        def _eval(cand, level=level):
            sd = e.CURVE_FOR_DIST[cand['key_dist']]
            why = e.prune(cand, level=level, secret_dist=sd, sec_source=sec_source,
                          sec_tolerance=tol, sec_cache=sec_cache, sec_model=sec_model,
                          w32_needs_hybrid=True)
            return why if why else e.evaluate(cand)

        n0 = len(front)
        if a.refine:
            front = e.refine_maps(front, _eval, tgt, quantile_budget=True,
                                  boundary=a.boundary)
        for r in front:
            try:
                r['spendable'] = e.spendable_margin(r, tgt)[1]
            except Exception:
                r['spendable'] = False
        pick, cap_info = _select(front, cell['name'], a.max_key_mib, a.key_cap_mult,
                                 a.key_cap_gib, a.key_cap_lambda, method=cell['method'],
                                 key_cap_hard=a.key_cap_hard)

        res = dict(cell)
        res['front'] = [_jsonable(x) for x in front]
        res['pick'] = _jsonable(pick) if pick else None
        res['old_pick'] = cell.get('pick')
        res['refinements_added'] = len(front) - n0
        res.update(cap_info)
        results.append(res)
        rows.append((cell['name'], cell.get('pick'), pick, tgt))

    if moved_all:
        ma = sorted(moved_all)
        nz = [x for x in ma if abs(x) > 1e-9]
        print("re-priced %d stored rows under %s\n  gate_us moved on %d of them: median %+.1f%%, range %+.1f%% .. %+.1f%%\n"
              % (n_stored, m.cost_provenance().splitlines()[0].replace("GATE_COST: ", ""),
                 len(nz), 100 * (sorted(nz)[len(nz) // 2] if nz else 0.0),
                 100 * ma[0], 100 * ma[-1]))
    # the comparison, cell by cell. "old us" is the stored pick's gate time AT
    # THE CELLS THAT CHOSE IT; "new us" is at the current cells, so on a cell
    # whose pick is unchanged the % column is the repricing alone.
    print("%-24s %-9s %-9s %-7s %-8s %-8s %-10s %s"
          % ("set", "old us", "new us", "gate", "old MiB", "new MiB", "certifiable", "change"))
    n_same = n_faster = n_slower = n_new = n_lost = 0
    for name, old, new, tgt in rows:
        if old is None and new is None:
            print("%-24s %-9s %-9s %-7s %-8s %-8s %-10s no pick either way" % (name, "-", "-", "", "-", "-", "-")); continue
        if new is None:
            n_lost += 1
            print("%-24s %-9.0f %-9s %-7s %-8.0f %-8s %-10s LOST" % (name, old['gate_us'], "-", "", old['key_bytes']/2**20, "-", "-")); continue
        ncert = new.get('log2pf_cert', new['log2pf'])
        if old is None:
            n_new += 1; tag = "NEW PICK"
            print("%-24s %-9s %-9.0f %-7s %-8s %-8.0f %-10.1f %s"
                  % (name, "-", new['gate_us'], "", "-", new['key_bytes']/2**20, ncert, tag)); continue
        ratio = new['gate_us'] / old['gate_us']
        gm_new = _revive(new)['gadget_map']; gm_old = _revive(old)['gadget_map']
        if _same_pick(old, new):
            n_same += 1; tag = "same"
        else:
            if ratio < 0.9999: n_faster += 1
            elif ratio > 1.0001: n_slower += 1
            else: n_same += 0
            bits = []
            if gm_new != gm_old: bits.append("map %s->%s" % (
                ",".join("%d:%d" % kv for kv in sorted(gm_old.items())),
                ",".join("%d:%d" % kv for kv in sorted(gm_new.items()))))
            if new['base_ks'] != old['base_ks']: bits.append("bKS %d->%d" % (old['base_ks'], new['base_ks']))
            if new['n'] != old['n']: bits.append("n %d->%d" % (old['n'], new['n']))
            if new['log_q_big'] != old['log_q_big']: bits.append("logQ %d->%d" % (old['log_q_big'], new['log_q_big']))
            tag = "; ".join(bits) or "re-selected"
        if new.get('over_cap'):
            tag += "  [OVER CAP]"
        print("%-24s %-9.0f %-9.0f %+6.1f%% %-8.0f %-8.0f %-10.1f %s"
              % (name, old['gate_us'], new['gate_us'], 100*(ratio-1),
                 old['key_bytes']/2**20, new['key_bytes']/2**20, ncert, tag))
    print("\nre-selected %d cells from stored frontiers: %d unchanged, %d faster, %d slower, "
          "%d newly picked, %d lost" % (len(rows), n_same, n_faster, n_slower, n_new, n_lost))

    out = dict(man)
    out.update(cells=results, repicked_from=os.path.abspath(a.dir),
               quantile_budget=True, per_key_log2pf_scatter=m.PER_KEY_LOG2PF_SCATTER,
               quantile_z=m.Z_90, map_refinement=bool(a.refine), map_grid=False,
               map_boundary=bool(a.refine and a.boundary), autokeys_grid=False,
               key_cap_mult=a.key_cap_mult, key_cap_gib=parse_key_cap(a.key_cap_gib),
               key_cap_lambda=a.key_cap_lambda,
               key_cap_rule='hard' if a.key_cap_hard else 'soft', repriced=True,
               dropped_ks=man.get('dropped_ks', '0'),
               key_layout=dict(ksk_top_compact=m.KSK_TOP_COMPACT,
                               rk_top_compact=m.RK_TOP_COMPACT,
                               ksk_zero_rows_dropped=m.KSK_ZERO_ROWS_DROPPED),
               cost_cells=m.cost_provenance().splitlines()[0])
    mpath = os.path.join(a.out_dir, 'manifest.json')
    with open(mpath, 'w') as f:
        json.dump(out, f, indent=1, default=str)
    ok = [r for r in results if r['pick']]
    ppath = os.path.join(a.out_dir, 'plan.cmds')
    _write_plan(ppath, ok, len(results), man.get('keys', 8), man.get('samples', 1250))
    print("manifest: %s\nplan:     %s   (%d runs)" % (mpath, ppath, len(ok) * man.get('keys', 8)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='dse_table', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('plan', help='search every cell and emit one measurement plan')
    p.add_argument('--levels', default='all')
    p.add_argument('--methods', default='all')
    p.add_argument('--inputs', default='all')
    p.add_argument('--targets', default='-64',
                   help='comma-separated. Write it with an equals sign -- '
                        '--targets=-64,-128 -- because a bare -64,-128 is not a '
                        'plain negative number and argparse reads it as an '
                        'option. A target <= %d takes the LPF_ prefix.' % LPF_TARGET)
    p.add_argument('--security', default='estimator', choices=('table', 'estimator'))
    p.add_argument('--tolerance', type=int, default=1)
    # Two-base maps are reached by refining the frontier, on by default, in
    # seconds. --multi-base is the 28x exhaustive alternative, kept for checking
    # that staging is lossless: on four cells it gave identical picks 25-33x
    # faster, including two cells whose winner IS a two-base map.
    p.add_argument('--multi-base', action='store_true', dest='multi_base',
                   help='enumerate two-base gadget maps in the GRID instead of '
                        'refining the frontier (28x slower)')
    p.add_argument('--no-refine', action='store_false', dest='refine',
                   help='skip the two-base frontier refinement, leaving a '
                        'single-base-only table')
    p.add_argument('--no-boundary', action='store_false', dest='boundary',
                   help='refine at the ladder points only, without bisecting to '
                        'the admissibility boundary')
    p.add_argument('--key-cap-gib', type=str, default=KEY_CAP_DEFAULT, dest='key_cap_gib',
                   help='ABSOLUTE key-material cap in GiB, per method: "4" for all, '
                        '"4,AP=8" (the default) for 4 GiB with AP at 8, "none" for no cap. '
                        'Up to it keys are simply manageable and the pick is gate-first; '
                        'beyond it rows are scored gate*(1+lambda*(key/cap-1)). Wins over '
                        '--key-cap-mult')
    p.add_argument('--key-cap-lambda', type=float, default=2.0, dest='key_cap_lambda',
                   help='exchange rate beyond the cap: doubling key over the cap costs '
                        'this many times the gate time (0 = gate-first everywhere, '
                        'inf = never exceed the cap while a row fits). Default 2.0')
    p.add_argument('--key-cap-hard', action='store_true', dest='key_cap_hard',
                   help='the two-stage rule instead of the soft score: the fastest row '
                        'that fits the cap, and the score only when nothing fits '
                        '(kept for comparison; it put a cliff at the level)')
    p.add_argument('--key-cap-mult', type=float, default=None, dest='key_cap_mult',
                   help='per-cell key-material cap as a multiple of the key the '
                        'library shipped for that set (frozen 238153db table; '
                        'name, then name without method suffix, then without '
                        'LPF_); gate-first within it, least-key beyond it')
    p.add_argument('--extrapolate-n', type=int, dest='extrapolate_n',
                   help='ALSO enumerate this ring dimension, above the range the '
                        'noise model was measured over (512..2048) and at most one '
                        'doubling past it. Every candidate that uses it is marked '
                        'n_extrapolated and the measurement still decides; without '
                        'it such a candidate is refused, which is the default for '
                        'a reason')
    p.add_argument('--dropped-ks', default='0', dest='dropped_ks',
                   help='comma-separated droppedDigitsKS values to enumerate '
                        '(OpenFHE\'s approximate key-switching decomposition). '
                        'Default 0. A row with delta > 0 is priced and shown but '
                        'NOT selected: its key-switch rounding term is derived and '
                        'unmeasured, and b_KS already trades the same two axes')
    p.add_argument('--autokeys-grid', action='store_true', dest='autokeys_grid',
                   help='enumerate numAutoKeys rather than taking the largest '
                        'priced value (5x slower for LMKCDEY, and the value is '
                        'monotone so it only rediscovers the ceiling)')
    p.add_argument('--word-size', type=int, choices=(32, 64), dest='word_size')
    p.add_argument('--key-dist', default=None, dest='key_dist',
                   choices=('UNIFORM_TERNARY', 'GAUSSIAN'),
                   help='default: every distribution each method can represent')
    p.add_argument('--keys', type=int, default=12)
    p.add_argument('--samples', type=int, default=1250)
    p.add_argument('--limit', type=int)
    p.add_argument('--ap-bases', default='2,8,32,128', dest='ap_bases',
                   help="AP refresh bases to enumerate (default 2,8,32,128, which "
                        "spans the product-count range at a quarter of the search "
                        "cost; empty string uses the full derived list)")
    p.add_argument('--max-key-mib', type=int, default=16384, dest='max_key_mib',
                   help='the pick must fit this much key material (default 16 GiB; '
                        '0 disables). AP can front a row whose refresh key is '
                        'hundreds of GiB, and measuring it unattended is an OOM '
                        'rather than a result.')
    p.add_argument('--jobs', type=int, default=1,
                   help='search cells in parallel; the searches are pure '
                        'arithmetic, so this is safe on a shared box in a way a '
                        'timing run is not')
    p.add_argument('--threads', choices=('single', 'multi'))
    p.add_argument('--compiler', choices=('clang', 'gcc'))
    p.add_argument('--out-dir', default='table', dest='out_dir')
    p.set_defaults(fn=plan)

    f = sub.add_parser('finish', help='certify the measurements and emit the table')
    f.add_argument('--dir', default='table')
    f.add_argument('--log')
    f.add_argument('--round', type=int, default=1,
                   help='granularity of the suggested label in bits (default 1; '
                        'pass 5 for the existing multiple-of-5 style). Always '
                        'rounds TOWARD ZERO, so the label never overclaims.')
    # A label is a claim that ships. Default it to the end of the interval the
    # claim has to survive, not to the middle of it.
    f.add_argument('--label-from', choices=('pessimistic', 'point'),
                   default='pessimistic',
                   help='which end of the certified interval the suggested label '
                        'comes from. pessimistic (default) uses cert+unc, so the '
                        'label holds even at the unfavourable end of the '
                        "measurement's own uncertainty; point uses cert alone and "
                        'can overstate by up to that uncertainty.')
    f.add_argument('--emit-rows', action='store_true', dest='emit_rows')
    f.add_argument('--emit-labels', action='store_true', dest='emit_labels')
    f.set_defaults(fn=finish)

    rp = sub.add_parser('repick', help='re-select every cell from its STORED frontier '
                                        'under the current policy and refinement, '
                                        'without searching again')
    rp.add_argument('--dir', default='table', help='a table directory with manifest.json')
    rp.add_argument('--out-dir', required=True, dest='out_dir')
    rp.add_argument('--max-key-mib', type=int, default=16384, dest='max_key_mib')
    rp.add_argument('--no-refine', action='store_false', dest='refine')
    rp.add_argument('--key-cap-gib', type=str, default=KEY_CAP_DEFAULT, dest='key_cap_gib',
                   help='ABSOLUTE key-material cap in GiB, per method: "4" for all, '
                        '"4,AP=8" (the default), "none" for no cap; see `plan`')
    rp.add_argument('--key-cap-lambda', type=float, default=2.0, dest='key_cap_lambda',
                   help='exchange rate beyond the cap (default 2.0; see `plan`)')
    rp.add_argument('--key-cap-hard', action='store_true', dest='key_cap_hard',
                    help='the two-stage rule instead of the soft score (see `plan`)')
    rp.add_argument('--key-cap-mult', type=float, default=None, dest='key_cap_mult',
                    help='per-cell key cap as a multiple of the shipped row (see plan)')
    rp.add_argument('--no-boundary', action='store_false', dest='boundary',
                    help='ladder points only, no bisection to the admissibility '
                         'boundary (isolates Hong & Lee Thm 4.3 in an A/B)')
    rp.set_defaults(fn=repick)

    c = sub.add_parser('compare', help='two table runs side by side: does the '
                                       'optimum move between cost regimes?')
    c.add_argument('--dirs', required=True, help='two table directories, comma-separated')
    c.add_argument('--threads-a', default='multi', dest='threads_a')
    c.add_argument('--compiler-a', default='clang', dest='compiler_a')
    c.add_argument('--threads-b', default='single', dest='threads_b')
    c.add_argument('--compiler-b', default='clang', dest='compiler_b')
    c.add_argument('-v', '--verbose', action='store_true',
                   help='for each moved cell, price both picks under both regimes')
    c.set_defaults(fn=compare)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
