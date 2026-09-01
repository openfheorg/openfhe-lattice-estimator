#!/usr/bin/env python3
"""Does a two-base gadget map cost its per-index digits, or the map's widest width?

    maps_fit.py ARM.log [--method LMKCDEY] [--weight relative]

LIB-63 measured that, after the library's team-width and scratch fixes, a two-base
LMKCDEY pick still runs 2-3 points slower than the sum-of-digits work model says,
and one N=2048 pick 14 points; GINX and AP two-base picks do not. Since 41709fbc
every accumulator region of a context runs at the WIDEST base's team width, so a
narrower-base index has idle threads rather than a smaller team -- which suggests
the cost per index follows the map's maximum digit count, not its own.

This fits each (method, N, word) cell on its single-base rows exactly as costfit
does, then predicts the two-base rows (gmap= rows from `MAPS=1`) three ways:

    lin    gate = c0 + c1 n + c2 gw       (+ c3 N/w)    per-index digits (the model today)
    max    gate = c0 + c1 n + c2 gw_max   (+ c3 N/w)    every index at the widest base
    mixed  gate = lin + c4 (gw_max - gw)                c4 fitted on the map rows alone

and prints the residuals of each, by cell and by coarse fraction, plus c4 as a
fraction of c2. A c4 near 0 says the map is priced right already; near c2 says
width is what costs; the GINX control should read near 0 either way.
"""
import argparse
import collections
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "paramsestimator"))

import dse_gatefit as g   # noqa: E402
import dse_model as m     # noqa: E402


def predict(c, r, N, work):
    c0, c1, c2, c3 = c
    return c0 + c1 * r['n'] + c2 * work + ((c3 or 0.0) * N / r['w'] if r['meth'] == 'LMKCDEY' else 0.0)


def summarise(label, res):
    if not res:
        return "%-6s (no rows)" % label
    a = sorted(abs(x) for x in res)
    return "%-6s median %+6.2f%%  |med| %5.2f  worst %+6.2f%%" % (
        label, sorted(res)[len(res) // 2], a[len(a) // 2], max(res, key=abs))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--method", help="only this method")
    ap.add_argument("--weight", choices=("relative", "absolute"), default="relative")
    ap.add_argument("--baseline", choices=("log", "model"), default="log",
                    help="where the single-base cell comes from: fitted on this log's "
                         "own single-base rows (default), or taken from the model's "
                         "GATE_COST for the active regime. `model` is for a MAPS_ONLY "
                         "control log that carries no single-base rows; since the "
                         "stored cell may sit at another pin, the mixed fit then "
                         "carries an intercept so a uniform offset cannot pose as c4")
    a = ap.parse_args(argv)

    rows, bad = g.read(a.log)
    if bad:
        print("%d rows did not parse and are excluded" % len(bad))
    cells = collections.defaultdict(lambda: dict(single=[], maps=[]))
    for r in rows:
        if a.method and r['meth'] != a.method:
            continue
        cells[(r['meth'], r['N'], r['word'])]['maps' if r.get('gmap') else 'single'].append(r)

    any_maps = False
    for key in sorted(cells):
        meth, N, word = key
        single, maps = cells[key]['single'], cells[key]['maps']
        if not maps:
            continue
        any_maps = True
        from_model = a.baseline == "model"
        if not from_model and len([r for r in single if r['w'] == 10]) < 4:
            print("\n%s N=%d w%d: %d map rows but too few single-base rows to fit the cell "
                  "(pass --baseline model to use the stored GATE_COST cell)" % (meth, N, word, len(maps)))
            continue
        if from_model:
            if (meth, N, word) not in m.GATE_COST:
                print("\n%s N=%d w%d: no GATE_COST cell in the active regime" % (meth, N, word))
                continue
            c = tuple(m.GATE_COST[(meth, N, word)])
        else:
            out = g.fit_cell(single, N, weight=a.weight)
            c = (out['c0'], out['c1'], out['c2'], out['c3'])
        lin = [100 * (predict(c, r, N, r['gw']) / r['gate_us'] - 1) for r in maps]
        mx = [100 * (predict(c, r, N, r['gw_max']) / r['gate_us'] - 1) for r in maps]
        # mixed: fit c4 on the residual of lin against (gw_max - gw), weighted like costfit
        X = np.array([r['gw_max'] - r['gw'] for r in maps], dtype=float)
        Y = np.array([r['gate_us'] - predict(c, r, N, r['gw']) for r in maps], dtype=float)
        Wt = 1.0 / np.array([r['gate_us'] for r in maps]) if a.weight == 'relative' else np.ones(len(maps))
        a0 = 0.0
        if from_model:
            # weighted least squares with an intercept: Y = a0 + c4 X
            A = np.vstack([np.ones_like(X), X]).T * np.sqrt(Wt)[:, None]
            sol, *_ = np.linalg.lstsq(A, Y * np.sqrt(Wt), rcond=None)
            a0, c4 = (float(sol[0]), float(sol[1])) if np.any(X) else (float(np.average(Y, weights=Wt)), 0.0)
        else:
            c4 = float(np.sum(Wt * X * Y) / np.sum(Wt * X * X)) if np.any(X) else 0.0
        mixed = [100 * ((predict(c, r, N, r['gw']) + a0 + c4 * (r['gw_max'] - r['gw'])) / r['gate_us'] - 1) for r in maps]
        src = ("stored GATE_COST cell (regime %s/%s)" % m.ACTIVE_REGIME) if from_model else "single-base fit"
        print("\n%s N=%d w%d   %s c0=%.1f c1=%.3f c2=%.4f c3=%s   (%d single, %d map rows)"
              % (meth, N, word, src, c[0], c[1], c[2], c[3], len(single), len(maps)))
        print("   " + summarise("lin", lin))
        print("   " + summarise("max", mx))
        print("   " + summarise("mixed", mixed) + "   c4 = %.4f = %.2f x c2%s"
              % (c4, c4 / c[2] if c[2] else float('nan'),
                 ("   intercept %+.0f us (%+.1f%% of the median gate)" % (a0, 100 * a0 / float(np.median([r['gate_us'] for r in maps]))) if from_model else "")))
        # by coarse fraction, the direction that separates a per-index from a per-width cost
        byf = collections.defaultdict(list)
        for r, e in zip(maps, lin):
            gm = r['gmap']; coarse = max(gm); frac = gm[coarse] / float(r['n'])
            byf[round(frac, 2)].append(e)
        print("   lin residual by coarse fraction: " + "  ".join(
            "%.2f:%+.1f%%" % (f, sorted(v)[len(v) // 2]) for f, v in sorted(byf.items())))
    if not any_maps:
        print("no two-base rows in this log yet (run the arm with MAPS=1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
