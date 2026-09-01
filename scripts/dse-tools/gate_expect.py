#!/usr/bin/env python3
"""Predicted sigma ratio per gate row across a pin move, for `gate_compare --expect-file`.

    gate_expect.py NEW.log [--from-flags FLAG=VAL ...] [--out FILE]

The correctness gate compares each geometry row's sigma at the new pin against
the same row at the previous one. Most pin moves leave noise alone, so a ratio of
1.0 is the right expectation and `gate_compare` assumes it. A pin that changes a
noise term breaks that assumption: at OpenFHE 94229558 the switching key stops
holding a row for digit value zero, so every row's key-switch variance loses a
factor of (1 - 1/r) per digit position and the gate is asking the wrong question
if it holds the ratio to 1.0.

This reads the geometry off the new log's `record|` lines -- the same parser the
rest of the tooling uses -- and prints, per label, the ratio the model predicts
between the two layouts:

    label   sigma(new layout) / sigma(old layout)

`--from-flags` names the layout flags to move, as `FLAG=old,new`; the default
`KSK_ZERO_ROWS_DROPPED=0,1` is the 94229558 move. Flags that do not change noise
(the two top-position compactions) affect key size only and belong nowhere here.

Read the output as a prediction, and check the RESOLVING POWER before reading
anything into a row that disagrees: `gate_compare` prints each row's own 1-sigma,
and the gate's stock plan (200 samples over 2 keys) puts that near 4 percent,
which cannot test a 1 percent shift. Raise `-i`/`-K` in the plan for a row whose
verdict has to mean something.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "paramsestimator"))

import dse_model as m  # noqa: E402

KV = re.compile(r'(\w+)=(\S+)')
METHOD = {'1': 'AP', '2': 'GINX', '3': 'LMKCDEY'}


def geometry_rows(path):
    """The rows that carry their own geometry, which are the ones comparable across pins."""
    out = []
    for line in open(path):
        if 'record|' not in line:
            continue
        d = dict(KV.findall(line))
        if d.get('set', '-') != '-' or d.get('sigma') in (None, 'NA'):
            continue
        out.append(d)
    return out


def sigma_for(d):
    gm = {int(x.split(':')[0]): int(x.split(':')[1]) for x in d['gmap'].split(',')}
    args = (int(d['N']), int(d['n']), int(d['q']), int(d['logQ']),
            int(d['qks']), int(d['baseks']), float(d.get('sigma_in', 3.19)), gm)
    kw = dict(key_dist=d.get('keydist', 'UNIFORM_TERNARY'),
              method=METHOD[d['method']],
              autokeys=int(d['autokeys']) if d.get('autokeys', '-').isdigit() else 10)
    if kw['method'] == 'AP':
        kw['base_r'] = int(d['baserk']) if d.get('baserk', '-').isdigit() else None
    return m.sigma_total_at_q(*args, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log', help="the NEW pin's gate log, for the geometries")
    ap.add_argument('--from-flags', action='append', metavar='FLAG=OLD,NEW',
                    default=None,
                    help='layout flag to move (default KSK_ZERO_ROWS_DROPPED=0,1)')
    ap.add_argument('--out', help='write here instead of stdout')
    a = ap.parse_args(argv)

    moves = []
    for spec in (a.from_flags or ['KSK_ZERO_ROWS_DROPPED=0,1']):
        flag, _, vals = spec.partition('=')
        old, _, new = vals.partition(',')
        if not hasattr(m, flag):
            sys.exit("no such layout flag in dse_model: %s" % flag)
        moves.append((flag, old == '1', new == '1'))

    lines = ["# predicted sigma ratio per geometry row, for gate_compare --expect-file",
             "# layout move: " + ", ".join("%s %s -> %s" % (f, int(o), int(n))
                                           for f, o, n in moves),
             "# a ratio of 1.0 means the move does not touch that row's noise"]
    saved = {f: getattr(m, f) for f, _, _ in moves}
    try:
        for d in geometry_rows(a.log):
            for f, o, _ in moves:
                setattr(m, f, o)
            old = sigma_for(d)
            for f, _, n in moves:
                setattr(m, f, n)
            new = sigma_for(d)
            lines.append("%s %.6f" % (d['label'], new / old))
    finally:
        for f, v in saved.items():
            setattr(m, f, v)

    text = "\n".join(lines) + "\n"
    if a.out:
        open(a.out, 'w').write(text)
        print("wrote %d row(s) to %s" % (len(lines) - 3, a.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
