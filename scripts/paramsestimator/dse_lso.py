#!/usr/bin/python
'''Leave-region-out error map for the noise model.

    > python3 scripts/paramsestimator/dse_lso.py LOG [LOG ...]

Reads every measured record it is given (legacy/sweep logs and runner `record|`
logs), predicts each with the FULL model (dse_model.sigma_total_at_q, any method,
any gadget map), and reports predicted/measured sigma per REGION of the design
space -- alignment, digit count, ring dimension, word, method, key-switch share,
map shape -- two ways:

  in-sample   the current constants, as the search uses them;
  held-out    the accumulator constant refit on every OTHER region, then applied
              to this one. A region whose held-out error is much worse than its
              in-sample error is one the constants are quietly tuned to; a region
              whose own best k differs from the global k wants a term, not a
              sample.

Only the accumulator constant is refit: key switching and both rounding terms
are derived, not fitted (dse_model), so there is nothing to move there.

Why regions and not random folds: the question is not "how well does the model
interpolate" but "WHERE is it weak", and a random fold averages exactly the
structure that answers it. Records at the wrap ceiling and in the key-switch-
saturated corner are excluded up front, as dse_validate excludes them: those are
not model errors and sampling them more would not make them so.
'''
import argparse
import math
import statistics as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dse_model as m
import dse_constraints as c
import dse_sweeplog as sl

METHOD = {1: 'AP', 2: 'GINX', 3: 'LMKCDEY'}
DIST = {0: 'GAUSSIAN', 1: 'UNIFORM_TERNARY'}


def load_records(paths):
    """Normalise both log formats to one record shape."""
    out = []
    shipped = None
    for p in paths:
        # legacy / sweep format
        for r in sl.parse(p):
            if r.get('method', 2) == 1:
                continue                                   # AP is refused by the model
            out.append(dict(src=os.path.basename(p), N=r['N'], n=r['n'], q=r['q'], logQ=r['logQ'],
                            q_ks=r['q_ks'], base_ks=r['base_ks'], gmap={r['base_g']: r['n']},
                            method=METHOD.get(r.get('method', 2), 'GINX'),
                            key_dist=DIST.get(r.get('secret_dist', 1), 'UNIFORM_TERNARY'),
                            autokeys=r.get('autokeys'),
                            inputs=r.get('inputs', 2), meas=r['sigma_measured'],
                            samples=(r.get('samples') or 0) * (r.get('keys') or 1)))
        # runner format
        for r in sl.runner_records(p):
            if r.get('method') == 1:
                continue
            if r.get('N') is None:                          # a named set: fill from the table
                if shipped is None:
                    import dse_shipfit as sf
                    shipped = sf.shipped_params()
                s = shipped.get(r.get('set'))
                if not s:
                    continue
                N, n, q, lq, qks, bks, gm = s['N'], s['n'], s['q'], s['logQ'], s['qks'], s['bks'], dict(s['gmap'])
            else:
                N, n, q, lq, qks, bks, gm = r['N'], r['n'], r['q'], r['logQ'], r['qks'], r['baseks'], r['gmap']
            # Both from the record. Hard-coding ternary here was the same defect
            # dse.py validate had: the two Var(s)/12 rounding terms differ 15x
            # between ternary and Gaussian, so a Gaussian LMKCDEY row scored as
            # ternary looks like a 40% model failure. Records written before
            # run-plan.sh named these fields fall back, which is why the
            # fallback is spelled out rather than left to the model's default.
            out.append(dict(src=os.path.basename(p), N=N, n=n, q=q, logQ=lq, q_ks=qks, base_ks=bks, gmap=gm,
                            method=METHOD.get(r.get('method', 2), 'GINX'),
                            key_dist=r.get('keydist') or 'UNIFORM_TERNARY',
                            autokeys=r.get('autokeys'),
                            inputs=r.get('inputs') or 2, meas=r['sigma_measured'], samples=r.get('samples') or 0))
    return out


def annotate(r):
    """Prediction, variance split, and the region labels."""
    kw = dict(key_dist=r['key_dist'], method=r['method'])
    # LMKCDEY's automorphism term is K_ACC_LMKCDEY_AUTO / autokeys, so the
    # model's default of 10 is a real prediction about a run that used 40.
    if r.get('autokeys'):
        kw['autokeys'] = int(r['autokeys'])
    r['pred'] = m.sigma_total_at_q(r['N'], r['n'], r['q'], r['logQ'], r['q_ks'], r['base_ks'], 3.19, r['gmap'], **kw)
    sh = m.variance_shares(r['N'], r['n'], r['q'], r['logQ'], r['q_ks'], r['base_ks'], 3.19, r['gmap'], **kw)
    r['acc_var'] = sh['acc'] * r['pred'] ** 2
    r['rest_var'] = (1.0 - sh['acc']) * r['pred'] ** 2
    ds = [m.digits_for_base(r['logQ'], b) for b in r['gmap']]
    aligned = all(b ** m.digits_for_base(r['logQ'], b) == 2 ** r['logQ'] for b in r['gmap'])
    r['regions'] = {
        'alignment': 'aligned' if aligned else 'misaligned',
        'digits':    'd=%d' % max(ds) if max(ds) < 5 else 'd>=5',
        'N':         'N=%d' % r['N'],
        'word':      'w32' if r['logQ'] <= 28 else 'w64',
        'method':    r['method'],
        'ks_share':  ('ks<1/3' if sh['ks'] < 1/3 else 'ks 1/3-2/3' if sh['ks'] < 2/3 else 'ks>2/3'),
        'map':       'single' if len(r['gmap']) == 1 else 'two-base',
        'qKS':       'qKS<=2^15' if r['q_ks'] <= 2 ** 15 else 'qKS>2^15',
    }
    return r


def usable(r):
    return (m.within_model_domain(r['N'], r['q_ks'], r['base_ks'], 3.19)
            and m.measurement_usable_for_fit(r['meas'], r['q'], r['inputs']))


def fit_k_scale(recs):
    """Scale s on the accumulator variance minimising RELATIVE sigma error."""
    from scipy.optimize import minimize_scalar
    def obj(s):
        return sum((math.sqrt(max(s, 0.0) * r['acc_var'] + r['rest_var']) / r['meas'] - 1.0) ** 2 for r in recs)
    res = minimize_scalar(obj, bounds=(0.05, 20.0), method='bounded')
    return float(res.x)


def err(r, s=1.0):
    return math.sqrt(s * r['acc_var'] + r['rest_var']) / r['meas'] - 1.0


def report(recs):
    print("%d usable records (of %d); in-sample predicted/measured sigma - 1:" % (len(recs), report.total))
    e = sorted(err(r) for r in recs)
    print("  median %+.1f%%   p10 %+.1f%%   p90 %+.1f%%   min %+.1f%%   max %+.1f%%"
          % (100 * st.median(e), 100 * e[len(e) // 10], 100 * e[9 * len(e) // 10], 100 * e[0], 100 * e[-1]))
    s_all = fit_k_scale(recs)
    print("  accumulator constant refit on everything: k_acc x %.3f" % s_all)
    print("\n%-11s %-12s %4s %5s | %-22s | %-22s | %s" % ("axis", "region", "n", "acc%", "in-sample med (p10,p90)", "held-out med (p10,p90)", "k_region/k_all"))
    print("(acc% = median accumulator share of variance in the region: k_region means nothing where it is small)")
    for axis in ('alignment', 'digits', 'N', 'word', 'method', 'ks_share', 'map', 'qKS'):
        groups = {}
        for r in recs:
            groups.setdefault(r['regions'][axis], []).append(r)
        for g in sorted(groups):
            inside = groups[g]; outside = [r for r in recs if r['regions'][axis] != g]
            ins = sorted(err(r) for r in inside)
            if len(outside) >= 5 and len(inside) >= 3:
                s_out = fit_k_scale(outside)
                held = sorted(err(r, s_out / s_all if s_all else 1.0) for r in inside)   # complement k, applied here
                s_in = fit_k_scale(inside)
                hs = "%+5.1f%% (%+.0f,%+.0f)" % (100 * st.median(held), 100 * held[len(held) // 10], 100 * held[9 * len(held) // 10])
                kr = "%.2f" % (s_in / s_all) if s_all else "-"
            else:
                hs, kr = "(too few)", "-"
            acc = st.median(r['acc_var'] / (r['acc_var'] + r['rest_var']) for r in inside)
            print("%-11s %-12s %4d %4.0f%% | %+5.1f%% (%+.0f,%+.0f)%8s | %-22s | %s"
                  % (axis, g, len(inside), 100 * acc, 100 * st.median(ins), 100 * ins[len(ins) // 10], 100 * ins[9 * len(ins) // 10], "", hs, kr))
    print("\nreading it: a held-out median far from the in-sample one means the constants are tuned to")
    print("that region; k_region/k_all far from 1 means the region wants its own term. Both near the")
    print("in-sample figure means more samples there would not move the model.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_lso')
    ap.add_argument('logs', nargs='+')
    a = ap.parse_args()
    recs = load_records(a.logs)
    report.total = len(recs)
    recs = [annotate(r) for r in recs if usable(r)]
    sys.exit(report(recs))
