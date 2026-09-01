#!/usr/bin/env python3
"""The explicit-geometry flags of named library sets, for the timing harness.

    sage -python scripts/dse-tools/set_flags.py SET:METHOD [SET:METHOD ...]

Prints one line per set:

    set=STD128 meth=2 flags=-n 554 -N 1024 -q 2048 -Q 27 -k 32768 -b 256 -G 128:303,512:251 -t 2 -d 1 -a 10 -r 64

taken from the LIVE parameter table (`dse_shipfit.shipped_params`, i.e. the
`binfhecontext.cpp` the installed OpenFHE was built from), so that
`boolean_estimate_time` can be handed the same geometry at two pins instead of
`-p NAME`. A named set's parameters belong to the library's table and change
between pins -- at 12858277 STD192_4 moved from b_KS 512 to 64 and read 3 percent
slower for it, which is not a library speed change -- so an A/B across pins has
to time geometry, not names. METHOD is the harness index: 1 AP, 2 GINX, 3
LMKCDEY; the table's own numAutoKeys and baseRK are passed through.

Runs inside the container (it needs the noise model's imports and the image's
table); the host-side arm calls it once at the newer pin and reuses the flags.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, os.pardir, 'paramsestimator')))

KEYDIST = {'UNIFORM_TERNARY': 1, 'GAUSSIAN': 0}


def flags(p, meth):
    gm = ",".join("%d:%d" % (int(b), int(w)) for b, w in sorted(p['gmap'].items(), key=lambda kv: int(kv[0])))
    return ("-n %d -N %d -q %d -Q %d -k %d -b %d -G %s -t %s -d %d -a %d -r %d"
            % (p['n'], p['N'], p['q'], p['logQ'], p['qks'], p['bks'], gm, meth,
               KEYDIST.get(p['keydist'], 1), p['autokeys'], p['brk']))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.exit(__doc__)
    import dse_shipfit as sf
    live = sf.shipped_params()
    rc = 0
    for item in argv:
        name, _, meth = item.partition(':')
        meth = meth or '2'
        if name not in live:
            print("set=%s meth=%s error=not in the live table (%s)" % (name, meth, sf.SRC))
            rc = 1
            continue
        print("set=%s meth=%s flags=%s" % (name, meth, flags(live[name], meth)))
    return rc


if __name__ == '__main__':
    sys.exit(main())
