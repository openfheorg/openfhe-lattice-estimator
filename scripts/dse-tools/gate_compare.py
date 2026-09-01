#!/usr/bin/env python3
"""Correctness-gate verdict: a new pin's 20-row noise log against the previous pin's.

    gate_compare.py BASELINE.log NEW.log [--z 3.0] [--scatter 0.0138]
                    [--expect LABEL=RATIO ...] [--expect-file FILE]

Pure standard library on purpose: it runs on the compute box, which has no scipy.

Two kinds of row, two rules. A row with `set=-` carries its geometry, so the same
configuration was measured at both pins and its sigma is compared to the baseline
as a ratio. A row with `set=NAME` is a `-p` run of a named library set, and since
f3944448 those names carry DIFFERENT parameters from the previous pin (STD128
went from n=556 {128:556} to n=554 {128:304, 512:250}), so its baseline is not a
baseline; it is checked WITHIN the pin -- the `_off`/`_on` pair is the A/B of the
64-bit against the 32-bit key forms -- and against the model on the live table's
geometry by `dse.py validate`, which this tool does not do.

EXPECTED RATIO. A geometry row is compared against the ratio the pin move should
produce, not against 1.0. Most pins leave noise alone and 1.0 is right; a pin that
changes a noise term does not, and holding it to 1.0 asks the wrong question. Pass
the predictions with `--expect label=ratio` or a file of `label ratio` lines
(`#` comments allowed); anything unnamed keeps 1.0.

RESOLVING POWER, reported and never used to excuse a deviation. Each row prints
the 1-sigma of its own ratio, and a row whose expected shift is smaller than that
also prints `cannot confirm`: it is too coarse to tell the prediction from no
change at all. That is a statement about what the row can CONFIRM, not about what
it can DETECT -- a 400-gate, 2-key row resolves nothing below about 5 percent
and still detects a 10 percent move perfectly well. So `cannot confirm` never
suppresses a failure. Any row that sits `--z` sigma from its expectation fails
the gate, and the summary adds how many exceedances chance alone would give at
this row count, because one row of twelve at 3 sigma is a 1-in-30 draw and one of
twelve at 6 sigma is not. Raise `-i`/`-K` in the plan for a row whose verdict has
to mean something.

The statistic. Each sigma is pooled over K keys and S gates in total (the
record's `samples`), and two runs of one configuration differ by two things:
sampling, relative variance 1/(2*S), and the per-key scatter of sigma,
KEY_SCATTER_BOUND = 1.38 percent per key, variance scatter^2 / K. The first version of this check used sampling alone and flagged
ab_STD256Q at z = -3.8 for a 3.2 percent off/on difference between two single-key
runs; with the scatter term it is inside noise, and both arms sit within 1.6
percent of the model. Leaving the scatter out is the same mistake, in the other
direction, that made `certify` assume zero scatter once (docs/verification.md).
"""
import argparse
import math
import re
import sys

KV = re.compile(r'(\w+)=(\S+)')


def records(path):
    out = {}
    for line in open(path):
        if 'record|' not in line:
            continue
        d = dict(KV.findall(line))
        if d.get('sigma') in (None, 'NA'):
            continue
        out[d['label']] = d
    return out


def rel_var(d, scatter):
    """Relative variance of one record's sigma: sampling plus per-key scatter.

    `samples` is the TOTAL gate count run-plan.sh recorded (-i x -K: a `-i 200
    -K 2` row says samples=400), so the sampling term is 1/(2*S) with S as
    recorded, not 1/(2*S*K). Multiplying by K again counted every gate K times
    and shrank each row's 1-sigma by sqrt(K): a two-key row printed 3.8 percent
    where it resolves 5.2, a six-key row 1.2 where it resolves 2.2, and z-scores
    ran up to 2.4x too large -- which is how a 6.8 percent off/on difference at
    six keys read as 5.9 sigma when it is 3.1. The scatter term is the variance
    of a mean over K per-key sigmas, so it does divide by K.
    """
    S = int(d.get('samples', 0) or 0)
    K = int(d.get('keys', 1) or 1)
    if S <= 0:
        return float('inf')
    return 1.0 / (2.0 * S) + (scatter ** 2) / K


def z_between(a, b, scatter, expect=1.0):
    """(ratio, z against `expect`, 1-sigma of the ratio)."""
    r = float(b['sigma']) / float(a['sigma'])
    sd = math.sqrt(rel_var(a, scatter) + rel_var(b, scatter))
    return r, (r - expect) / sd, sd


def read_expect(pairs, path):
    """{label: expected ratio} from --expect and --expect-file."""
    out = {}
    if path:
        for line in open(path):
            line = line.split('#')[0].split()
            if len(line) == 2:
                out[line[0]] = float(line[1])
    for kv in pairs or ():
        k, _, v = kv.partition('=')
        out[k.strip()] = float(v)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('baseline')
    ap.add_argument('new')
    ap.add_argument('--z', type=float, default=3.0, help='|z| at which a row fails (default 3)')
    ap.add_argument('--scatter', type=float, default=0.0138,
                    help='per-key sigma scatter, relative (default KEY_SCATTER_BOUND 0.0138)')
    ap.add_argument('--expect', action='append', metavar='LABEL=RATIO',
                    help='ratio this row SHOULD move by across the pin (default 1.0). '
                         'Repeatable')
    ap.add_argument('--expect-file', metavar='FILE',
                    help='file of `label ratio` lines, same meaning as --expect')
    a = ap.parse_args(argv)
    expect = read_expect(a.expect, a.expect_file)
    old, new = records(a.baseline), records(a.new)
    fails = sum(int(d.get('FAILURES', 0) or 0) for d in new.values())
    missing = [l for l in old if l not in new]
    bad = []

    print("geometry rows: new/baseline sigma against the expected ratio, "
          "z on the difference (sampling + per-key scatter)")
    print("  %-30s %-8s %-8s %-7s %-7s %-6s %-6s %s"
          % ("label", "old", "new", "ratio", "expect", "z", "1sig", "FAILS"))
    unconfirmable = []
    for lab, o in old.items():
        n = new.get(lab)
        if not n or n.get('set', '-') != '-':
            continue
        e = expect.get(lab, 1.0)
        r, z, sd = z_between(o, n, a.scatter, e)
        # Whether the row could CONFIRM its expected shift. Only meaningful when a
        # shift is expected: with e = 1.0 the row asks "did anything change", and
        # there is nothing to confirm. Never a reason to forgive a deviation
        # either way, since this row detects a large move regardless.
        if e != 1.0 and abs(e - 1.0) < sd:
            unconfirmable.append(lab)
        flag = ""
        if abs(z) >= a.z:
            flag = "  <-- |z| >= %.1f" % a.z
            bad.append(lab)
        elif lab in unconfirmable:
            flag = "  (cannot confirm)"
        print("  %-30s %-8.3f %-8.3f %-7.4f %-7.4f %+5.1f %5.1f%% %-5s%s"
              % (lab, float(o['sigma']), float(n['sigma']), r, e, z, 100 * sd,
                 n.get('FAILURES', '?'), flag))

    print("\nnamed-set rows: off/on A/B within the new pin (the name's parameters may differ from the baseline)")
    print("  %-26s %-8s %-8s %-7s %-6s %s" % ("pair", "off", "on", "ratio", "z", "FAILS off/on"))
    for p in sorted({l[:-4] for l in new if l.endswith('_off')}):
        x, y = new.get(p + '_off'), new.get(p + '_on')
        if not (x and y):
            print("  %-26s incomplete" % p)
            bad.append(p)
            continue
        r, z, _sd = z_between(x, y, a.scatter)
        flag = "  <-- |z| >= %.1f" % a.z if abs(z) >= a.z else ""
        if flag:
            bad.append(p)
        print("  %-26s %-8.3f %-8.3f %-7.4f %+5.1f %s/%s%s"
              % (p, float(x['sigma']), float(y['sigma']), r, z,
                 x.get('FAILURES', '?'), y.get('FAILURES', '?'), flag))
    for l in sorted(l for l in new if new[l].get('set', '-') != '-'
                    and not (l.endswith('_off') or l.endswith('_on'))):
        print("  %-26s single named row: sigma %.3f  FAILS %s  (model check: dse.py validate)"
              % (l, float(new[l]['sigma']), new[l].get('FAILURES', '?')))

    ok = (len(new) == len(old) and fails == 0 and not bad and not missing)
    print("\nrows %d/%d  FAILURES %d  |z| >= %.1f: %s  missing: %s  -> %s"
          % (len(new), len(old), fails, a.z, bad or 'none', missing or 'none',
             "GATE PASS" if ok else "GATE FAIL"))
    n_geom = sum(1 for l in old if l in new and new[l].get('set', '-') == '-')
    if unconfirmable:
        print("  %d of %d geometry row(s) are too coarse to CONFIRM their expected shift "
              "(1-sigma exceeds it): %s" % (len(unconfirmable), n_geom, unconfirmable))
        print("  They still detect a large move, and one did not become a pass by being "
              "coarse. Raise -i/-K for a row whose verdict has to mean something.")
    if bad and n_geom:
        # Chance exceedances at this row count, two-sided, so a lone 3-sigma row in a
        # dozen reads as what it is rather than as a finding.
        p1 = math.erfc(a.z / math.sqrt(2.0))
        print("  chance alone gives %.2f exceedances at |z| >= %.1f over %d rows "
              "(one lone exceedance is a %.0f-to-1 draw, not a result)"
              % (n_geom * p1, a.z, n_geom, 1.0 / max(n_geom * p1, 1e-9)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
