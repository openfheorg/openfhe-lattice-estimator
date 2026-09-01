#!/usr/bin/env python3
"""Current cost cells against a fresh fit, read as microseconds rather than coefficients.

    compare_cells.py FIT.txt --regime multi/libomp

FIT.txt is `dse.py costfit <log> --emit` output. For every cell this prints the
old and new (c1, c2, c3) side by side AND the predicted gate time at two
reference configurations -- n=1024, w=10, at 3 and at 6 gadget digits -- with the
ratio. A pin move or a new machine is then a percentage of a gate, which is the
unit the decision is actually made in; a change in c0 alone can look alarming
and mean nothing.

Run it BEFORE apply_cells.py, while dse_model.py still holds the old numbers.
A cell that appears in one side only is called out rather than skipped.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "paramsestimator"))

import dse_model as m                                    # noqa: E402

REFERENCE_N = 1024
REFERENCE_W = 10


def read_emitted(path):
    text = open(path).read()
    block = re.search(r"GATE_COST = \{\n(.*?)\n\}", text, re.S)
    if not block:
        sys.exit("%s: no `GATE_COST = {...}` block. Did you pass --emit?" % path)
    cells = {}
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        mm = re.match(r"\s*\('(\w+)', (\d+), (\d+)\): \((.*)\),", line)
        if not mm:
            sys.exit("%s: unparsed emit line: %r" % (path, line))
        method, N, word, coef = mm.groups()
        vals = [None if v.strip() == "None" else float(v) for v in coef.split(",")]
        cells[(method, int(N), int(word))] = vals
    return cells


def gate(coef, method, N, word, digits):
    """Predicted gate us at the reference n and w, for `digits` gadget digits."""
    c0, c1, c2, c3 = coef
    log_q = 27 if word == 32 else 37
    base = 1 << -(-log_q // digits)
    us = c0 + c1 * REFERENCE_N + c2 * m.gate_work({base: REFERENCE_N}, log_q)
    if method == "LMKCDEY" and c3 is not None:
        us += c3 * N / float(REFERENCE_W)
    return us


def fmt(coef):
    return "%7.1f %6.2f %6s" % (
        coef[1], coef[2], "-" if coef[3] is None else "%.1f" % coef[3])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fit", help="output of `dse.py costfit <log> --emit`")
    ap.add_argument("--regime", default="/".join(m.DEFAULT_REGIME),
                    choices=["/".join(k) for k in sorted(m.COST_REGIMES)],
                    help="which regime's current cells to compare against")
    a = ap.parse_args()

    threads, runtime = a.regime.split("/")
    entry = m.COST_REGIMES[(threads, runtime)]
    old, new = entry["cells"], read_emitted(a.fit)

    print("regime %s: cells at %s (measured %s) -> %s"
          % (a.regime, (entry["pin"] or "?")[:8], entry["measured"],
             os.path.basename(a.fit)))
    print("  gate us at n=%d, w=%d, on %d cells" % (REFERENCE_N, REFERENCE_W, len(new)))
    print("  %-18s | %-22s | %-22s | %-24s | %s"
          % ("cell", "old  c1 c2 c3", "new  c1 c2 c3",
             "gate d=3  old->new    x", "gate d=6  old->new    x"))

    for key in sorted(set(old) | set(new), key=lambda k: (k[0], k[1], k[2])):
        method, N, word = key
        label = "%s,%d,%d" % key
        o, n = old.get(key), new.get(key)
        if o is None or n is None:
            print("  %-18s | %s" % (label, "NEW cell (absent from the current table)"
                                    if o is None else "DROPPED (absent from the fit)"))
            continue
        g3o, g3n = gate(o, method, N, word, 3), gate(n, method, N, word, 3)
        g6o, g6n = gate(o, method, N, word, 6), gate(n, method, N, word, 6)
        print("  %-18s | %s | %s | %7.0f->%7.0f %5.2f | %7.0f->%7.0f %5.2f"
              % (label, fmt(o), fmt(n), g3o, g3n, g3n / g3o, g6o, g6n, g6n / g6o))


if __name__ == "__main__":
    main()
