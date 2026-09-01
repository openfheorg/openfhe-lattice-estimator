#!/usr/bin/env python3
"""Cost-fit residuals by (n, gadget digits), per cell, so a worst case has a location.

    resid_table.py gatecost.log [--method LMKCDEY]

`dse.py costfit` reports one median and one worst residual per cell. A worst
residual of 60 percent means something different if it is one row than if it is
a corner, and the fix differs too. This refits each cell exactly as costfit does
and prints the residual at every (n, digits) it measured, plus the medians split
at digitsG2 = 8, where the accumulator's shared decomposition switches on.

Residual is `predicted / measured - 1` at w = 10, so a positive number is the
model over-predicting (calling the configuration slower than it is).

Where the corners have come from before: n = 64 for LMKCDEY, where the
automorphism term does not scale with n, and a handful of single 8-gate means
that ran slow. Neither is a shape error, and this table is how you tell.
"""
import argparse
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "paramsestimator"))

import dse_gatefit as g                                  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="a gatecost-regime.sh log")
    ap.add_argument("--method", help="only this method (GINX, LMKCDEY)")
    ap.add_argument("--weight", choices=("relative", "absolute"), default="relative",
                    help="fit objective, matching what you pass to costfit")
    a = ap.parse_args()

    rows, bad = g.read(a.log)
    if bad:
        print("%d rows did not parse and are excluded" % len(bad))

    cells = collections.defaultdict(list)
    for r in rows:
        cells[(r["meth"], r["N"], r["word"])].append(r)

    for key in sorted(cells):
        if a.method and key[0] != a.method:
            continue
        method, N, word = key
        out = g.fit_cell(cells[key], N, weight=a.weight)
        c0, c1, c2, c3 = out["c0"], out["c1"], out["c2"], out["c3"]

        main_rows = [r for r in cells[key] if r["w"] == 10]
        if not main_rows:
            continue
        digits = sorted({r["d"] for r in main_rows})
        dims = sorted({r["n"] for r in main_rows})

        def predict(r):
            # The fourth coefficient means different things per method, because a
            # cell is keyed by method: LMKCDEY's automorphism term c3*N/w, and
            # AP's per-external-product fixed cost against n*products.
            if method == "AP":
                import dse_model as _m
                return (c0 + c1 * r["n"] + c2 * r["gw"]
                        + (c3 or 0.0) * _m.ap_products_per_gate(
                            r["n"], r["q"], r["base_r"]))
            return c0 + c1 * r["n"] + c2 * r["gw"] + (c3 or 0.0) * N / 10.0

        def residual(r):
            return 100.0 * (predict(r) / r["gate_us"] - 1.0)

        print("\n%s N=%d w%d   c0=%.1f c1=%.3f c2=%.4f c3=%s"
              % (method, N, word, c0, c1, c2, c3))
        print("   residual % = predicted/measured - 1, at w = 10")
        print("   n\\d " + "".join("%7d" % d for d in digits))
        for n in dims:
            line = "  %5d " % n
            for d in digits:
                match = [r for r in main_rows if r["n"] == n and r["d"] == d]
                line += ("%+6.0f%%" % residual(match[0])) if match else "      ."
            print(line)

        def median(rs):
            vals = sorted(residual(r) for r in rs)
            return vals[len(vals) // 2] if vals else float("nan")

        low = [r for r in main_rows if r["d"] <= 4]
        high = [r for r in main_rows if r["d"] >= 5]
        print("   median residual d<=4: %+.1f%%   d>=5: %+.1f%%   "
              "(n>=256 only: %+.1f%% / %+.1f%%)"
              % (median(low), median(high),
                 median([r for r in low if r["n"] >= 256]),
                 median([r for r in high if r["n"] >= 256])))


if __name__ == "__main__":
    main()
