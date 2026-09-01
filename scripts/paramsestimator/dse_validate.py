#!/usr/bin/python

'''Check the closed-form noise model against measured sweep results, and refit its constants.

This is the gate on the model library: the search may only prune candidates it
has not measured if the model can reproduce the ones it has. Run it against any
sweep log; a model change that improves one machine's numbers and degrades
another's is exactly the correlated error the plan warns about.

    > python3 scripts/paramsestimator/dse_validate.py sweep.log
    > python3 scripts/paramsestimator/dse_validate.py sweep.log --fit

Needs numpy/scipy but NOT OpenFHE or SageMath, so it runs on a laptop.

What NOT to validate against
----------------------------
The `2^(-135)` style figures in OpenFHE's BINFHE_PARAMSET comments are not ground
truth. They were produced by an earlier version of this repo, before multi-key
measurement existed and probably without enough samples to bound the number. A
disagreement with them is an OUTPUT of this work, not a defect in it -- so never
gate a model change on reproducing one.

The model's validation is against freshly measured sigma. As of 2026-08-31,
STD128: model 14.13 vs measured 13.94, +1.4%. The same measurement implies
log2Pf = -125.7 where the shipped comment says -135, and the pooled sigma used
there still flatters a deployment that runs one key -- a per-key quantile makes
the gap wider, not narrower.
'''

from math import log2, sqrt

import argparse
import statistics as st
import sys

import dse_model as m
from dse_sweeplog import records


def _terms(r):
    """The three variance contributions for one record, each expressed at q."""
    Q  = 2.0 ** r['logQ']
    return {
        # per (N * coefficient) accumulator basis, with k_acc factored out.
        # Two things were missing here as well as in the model. The N: the sweep
        # varied it over {512, 1024, 2048}, but the accumulator is under 5% of
        # variance in 251 of its 284 in-domain records, so an N-shaped error had
        # nothing to show up in. And the shape: (dg - 1) * base^2 is not what the
        # decomposition does -- see gadget_shape().
        'acc':   r['N'] * r['n'] * m.gadget_shape(r['logQ'], r['base_g']) * (float(r['q']) / Q) ** 2,
        # fully determined, no free constant
        'ks':    m.sigma_keyswitch(r['N'], r['q'], r['q_ks'], r['base_ks'], r['sigma']) ** 2,
        # per-coefficient rounding basis, with ms_coeff factored out
        'round': float(r['n']),
    }


def predict(r, k_acc, ms_coeff):
    t = _terms(r)
    return sqrt(k_acc * t['acc'] + t['ks'] + ms_coeff * t['round'])


def in_domain(r):
    """Two independent exclusions, and conflating them hid a real result.

    within_model_domain  a property of the CANDIDATE: the key-switching key's own
                         noise approaches q_KS, so the model overpredicts without
                         bound.
    measurement_usable   a property of the MEASUREMENT: sigma is at or near the
                         q/(p*sqrt(12)) wrap ceiling, so the number reports the
                         ceiling rather than the parameters.

    Only the first was applied before. That left 8 ceiling-pinned records in the
    scored set, all of them d=2, which read as a "d=2 anomaly" and bought a guard
    that refused a perfectly well-modelled regime.
    """
    return (m.within_model_domain(r['N'], r['q_ks'], r['base_ks'], r['sigma'])
            and m.measurement_usable_for_fit(r['sigma_measured'], r['q'], r['inputs']))


def refit(recs):
    """Least squares on RELATIVE sigma error.

    Fitting on variance instead lets the largest candidates dominate and returns
    a model that is 80% low on the median one -- the errors here are
    multiplicative and span orders of magnitude, so the objective must be too.
    """
    import numpy as np
    from scipy.optimize import least_squares

    A = np.array([[_terms(r)['acc'], _terms(r)['round']] for r in recs])
    ks = np.array([_terms(r)['ks'] for r in recs])
    meas = np.array([r['sigma_measured'] for r in recs])

    def resid(p):
        return np.sqrt(A @ np.abs(p) + ks) / meas - 1.0

    sol = least_squares(resid, x0=[m.K_ACC, m.MODSWITCH_VAR_PER_COEFF])
    return float(abs(sol.x[0])), float(abs(sol.x[1]))


def report(recs, k_acc, ms_coeff):
    kept = [r for r in recs if in_domain(r)]
    n_dom = sum(1 for r in recs
                if not m.within_model_domain(r['N'], r['q_ks'], r['base_ks'], r['sigma']))
    n_sat = sum(1 for r in recs
                if m.within_model_domain(r['N'], r['q_ks'], r['base_ks'], r['sigma'])
                and not m.measurement_usable_for_fit(r['sigma_measured'], r['q'], r['inputs']))
    print("records: %d parsed, %d scored; excluded %d for keyswitch saturation "
          "(candidate) and %d for ceiling compression (measurement)"
          % (len(recs), len(kept), n_dom, n_sat))
    print("constants: k_acc=%.6g  modswitch=%.4g" % (k_acc, ms_coeff))
    if not kept:
        return 1

    err = [(predict(r, k_acc, ms_coeff) / r['sigma_measured'] - 1.0, r) for r in kept]
    e = sorted(x for x, _ in err)
    print("\npredicted/measured sigma - 1:")
    for lbl, i in (("min", 0), ("10%", len(e)//10), ("median", len(e)//2),
                   ("90%", 9*len(e)//10), ("max", -1)):
        print("  %-7s %+8.1f%%" % (lbl, 100 * e[i]))
    for tol in (0.05, 0.10, 0.25):
        n = sum(1 for x in e if abs(x) < tol)
        print("  within +-%2d%%: %3d/%3d  (%.0f%%)" % (100*tol, n, len(e), 100.0*n/len(e)))

    print("\nresidual structure -- a group that stands apart means a missing term:")
    # q_ks and base_ks added after six frontier candidates all came out with the
    # model over-predicting sigma (+0.34% to +1.73%, 6 of 6 the same sign, p=1/64
    # if unbiased). They span q_KS 2^14..2^17, and keyswitch is the dominant term
    # there -- so if that term carries a small residual error it shows up here.
    for key in ('N', 'logQ', 'q', 'q_ks', 'base_ks', 'inputs', 'word_size', 'method'):
        groups = {}
        for x, r in err:
            groups.setdefault(r[key], []).append(x)
        print("  %-10s %s" % (key, "   ".join(
            "%s:%+.0f%%(n=%d)" % (g, 100*st.median(v), len(v)) for g, v in sorted(groups.items()))))

    # what the sweep did NOT cover is as important as what it did
    print("\ncoverage gaps (constants are NOT calibrated for these):")
    print("  methods present: %s   (1=AP 2=GINX 3=LMKCDEY)" % sorted({r['method'] for r in kept}))
    print("  secret_dist present: %s   (0=gaussian 1=ternary)" % sorted({r['secret_dist'] for r in kept}))
    print("  gadget maps: single-base only -- no multi-base record exists to calibrate against")
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_validate')
    ap.add_argument('log', help='sweep log to validate against')
    ap.add_argument('--fit', action='store_true',
                    help='refit k_acc and the modulus-switch coefficient on this log')
    a = ap.parse_args()

    recs = records(a.log)
    k_acc, ms_coeff = m.K_ACC, m.MODSWITCH_VAR_PER_COEFF
    if a.fit:
        kept = [r for r in recs if in_domain(r)]
        k_acc, ms_coeff = refit(kept)
        print("refitted on %d in-domain records -> k_acc=%.6g  modswitch=%.4g\n"
              % (len(kept), k_acc, ms_coeff))
    sys.exit(report(recs, k_acc, ms_coeff))
