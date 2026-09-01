#!/usr/bin/env python3
"""Did a key-layout pin move change noise by what the model says?

    keylayout_fit.py OLD.log NEW.log [--flag KSK_ZERO_ROWS_DROPPED]

Reads two `keylayout-cells.sh` logs -- the same designed cells at two pins -- and
per cell prints the measured sigma ratio, the predicted ratio, and the z between
them. Exits nonzero if any cell misses its prediction by more than `--z`.

TWO THINGS IT GETS RIGHT, both learned the hard way at OpenFHE 94229558.

The statistic. `sigma_total_at_q` predicts the POOLED sigma over keys, while a
`-K 1` record's sigma is that key's own, taken about that key's mean: a digit
position of extent r contributes ((r-1)/r)^2 of a Gaussian within a key, and its
own mean is a constant the key is stuck with, ((r-1)/r^2). The two sum to the
pooled (1 - 1/r). The gap is ks_share/b_KS in variance -- 0.2 percent of sigma at
b_KS 256, 13 percent at b_KS 4 -- so it hid behind the measurement floor on every
shipped geometry and appeared at 10 sigma on the first cells designed with a small
base. This compares against the WITHIN-key prediction, which is what the harness
reports.

The error bar. Two runs of one configuration differ by sampling, 1/(2*S*K), and
by the per-key scatter of sigma, KEY_SCATTER_BOUND^2/K, which does not shrink with
samples. Leaving the scatter out over-flags; it is the same mistake, in the other
direction, that once made `certify` assume zero scatter.
"""
import argparse
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "paramsestimator"))

import dse_model as m  # noqa: E402

KV = re.compile(r'(\w+)=(\S+)')
METHOD = {'1': 'AP', '2': 'GINX', '3': 'LMKCDEY'}


def cells(path):
    out = {}
    for line in open(path):
        if 'record|' not in line:
            continue
        d = dict(KV.findall(line))
        if d.get('sigma') in (None, 'NA'):
            continue
        out.setdefault(d['label'], []).append(d)
    return out


def pooled(recs):
    """Root-mean-square of the per-key sigmas, and the run's shape."""
    s = [float(r['sigma']) for r in recs]
    return (sum(v * v for v in s) / len(s)) ** 0.5, len(s), int(recs[0]['samples'])


def within_sigma(d, flag, value):
    """Predicted WITHIN-key sigma for this record's geometry, with `flag` set."""
    gm = {int(x.split(':')[0]): int(x.split(':')[1]) for x in d['gmap'].split(',')}
    N, n, q = int(d['N']), int(d['n']), int(d['q'])
    lq, qks, bks = int(d['logQ']), int(d['qks']), int(d['baseks'])
    kw = dict(key_dist=d.get('keydist', 'UNIFORM_TERNARY'),
              method=METHOD[d['method']],
              autokeys=int(d['autokeys']) if d.get('autokeys', '-').isdigit() else 10)
    if kw['method'] == 'AP':
        kw['base_r'] = int(d['baserk']) if d.get('baserk', '-').isdigit() else None
    saved = getattr(m, flag)
    try:
        setattr(m, flag, value)
        total = m.sigma_total_at_q(N, n, q, lq, qks, bks, 3.19, gm, **kw)
        ks_pooled = m.keyswitch_var_at_q(N, q, qks, bks, 3.19)
        ks_within, _ = m.keyswitch_variance_split(N, q, qks, bks, 3.19)
    finally:
        setattr(m, flag, saved)
    return math.sqrt(total ** 2 - ks_pooled + ks_within)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('old', help="the earlier pin's log")
    ap.add_argument('new', help="the later pin's log")
    ap.add_argument('--flag', default='KSK_ZERO_ROWS_DROPPED',
                    help='the layout flag the pin move flips (default the zero-row one)')
    ap.add_argument('--z', type=float, default=3.0)
    ap.add_argument('--scatter', type=float, default=None,
                    help='per-key sigma scatter (default KEY_SCATTER_BOUND)')
    a = ap.parse_args(argv)
    scatter = a.scatter if a.scatter is not None else m.KEY_SCATTER_BOUND
    if not hasattr(m, a.flag):
        sys.exit("no such layout flag in dse_model: %s" % a.flag)

    old, new = cells(a.old), cells(a.new)
    shared = [l for l in new if l in old]
    if not shared:
        sys.exit("no label appears in both logs")

    print("%-20s %9s %9s %9s %9s %7s  %s"
          % ("cell", "old", "new", "ratio", "predicted", "z", "verdict"))
    bad = []
    for lab in shared:
        pn, Kn, S = pooled(new[lab])
        po, Ko, _ = pooled(old[lab])
        d = new[lab][0]
        pred = within_sigma(d, a.flag, True) / within_sigma(d, a.flag, False)
        err = math.sqrt(1.0 / (2 * S * Kn) + (scatter ** 2) / Kn
                        + 1.0 / (2 * S * Ko) + (scatter ** 2) / Ko)
        z = (pn / po - pred) / err
        if abs(z) >= a.z:
            bad.append(lab)
        print("%-20s %9.4f %9.4f %9.4f %9.4f %+7.1f  %s"
              % (lab, po, pn, pn / po, pred, z,
                 "confirmed" if abs(z) < a.z else "MISSES PREDICTION"))
    print("\n%d cell(s), %d beyond |z| = %.1f: %s"
          % (len(shared), len(bad), a.z, bad or 'none'))
    if bad:
        print("A cell that misses means the layout's noise effect is not what the model")
        print("says. Do not certify a table on the model until that is resolved.")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
