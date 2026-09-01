"""Pareto front of configurations that beat a shipped set, on (gate time, key size).

Reporting only the FASTEST winner hides the trade: STD192Q's fastest is 1.30x
faster at 1.90x the key material, and a reader may well prefer 1.15x faster at
1.0x keys. The frontier is the honest object.

Tolerance is per level, set to the minimum at which the SHIPPED set itself
certifies -- holding a candidate to a stricter bar than the incumbent is not a
comparison. STD256Q needs 2 (it delivers 254 bits against a 256 nominal); STD128
and STD192Q need 1.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from math import log2
import dse_model as m, dse_enumerate as e, dse_security as sec, dse_shipfit as sf, dse_constraints as c

def fmt_map(gm):
    return "{" + ",".join("2^%d:%d" % (int(log2(b)), c) for b, c in sorted(gm.items())) + "}"

def run(lvl, tol):
    if not m.regime_is_measured():
        print(m.cost_provenance())
        print("   -> refusing to rank: an empty frontier here would be "
              "indistinguishable from\n      'nothing beats it'.\n")
        return
    P = sf.shipped_params(); cache = sec.load(); p = P[lvl]
    # The shipped set runs whatever a default 64-bit build gives it, and since
    # OpenFHE 9e8045db that is the 32-bit accumulator whenever its refresh key
    # Fits(): Q <= 2^28 and digitsG*gBits + 1 <= 32 for every base in the map.
    word = 32 if c.hybrid_ns32_ok(p['logQ'], p['gmap'], 'GINX') else 64
    ship = e.evaluate(dict(N=p['N'], n=p['n'], q=p['q'], log_q_big=p['logQ'],
                           q_ks=p['qks'], base_ks=p['bks'], sigma=3.19, inputs=2,
                           method='GINX', key_dist='UNIFORM_TERNARY', word_size=word,
                           autokeys=10, gadget_shape_keys=tuple(sorted(p['gmap'])),
                           split_count=0))
    print("%s shipped: gate %.0f us, keys %.0f MiB, log2Pf %.1f   (tol=%d, w%d)"
          % (lvl, ship['gate_us'], ship['key_bytes']/2**20, ship['log2pf'], tol, word))
    # The shipped set's own log2Pf carries the SAME model error the candidate's
    # band exists to absorb, so the candidate has to clear the baseline's
    # pessimistic end and not its point prediction. Comparing a banded candidate
    # against an unbanded baseline accepted a STD256 candidate whose measured
    # log2Pf came out 0.5 bits WORSE than shipped -- shipped predicted 1.8% high,
    # candidate 2.5% low, and one-sided banding covers neither.
    want = ship['log2pf'] - ship['band']
    grid = dict(e.DEFAULT_GRID, autokeys=(10, 40))
    # the priced security boundaries and shipped n values for this level, so the
    # frontier can land on the smallest secure n rather than the next grid step
    extra = sec.extra_dims(lvl, 'ternary', 'standard', lvl.endswith('Q'), tol, cache=cache)
    if extra:
        grid['extra_n'] = tuple(extra)
    win = []
    for meth in ('GINX', 'LMKCDEY'):
        # word_size=None searches BOTH sides. Pinning it to the shipped set's
        # side was the reason a separate tool had to exist for hybrid candidates:
        # a set with logQ 37 was never offered a 32-bit accumulator even though
        # dropping logQ to 28 to reach one is a legitimate move for the search to
        # make. w32_needs_hybrid holds those candidates to Fits(), since on a
        # default NS64 build the only route to a 32-bit accumulator is the opt-in
        # hybrid.
        for cand in e.candidates(grid=grid, method=meth, word_size=None, multi_base=True):
            if e.prune(cand, level=lvl, secret_dist='ternary', sec_source='estimator',
                       sec_cache=cache, sec_tolerance=tol, w32_needs_hybrid=True):
                continue
            out = e.evaluate(cand)
            if not isinstance(out, dict):
                continue
            if not m.beats_at_no_worse_pf(out['log2pf'], out['band'],
                                          ship['log2pf'], ship['band']) \
                    or out['gate_us'] >= ship['gate_us']:
                continue
            out['_meth'] = meth
            win.append(out)
        print("   %-8s cumulative winners: %d" % (meth, len(win)))
        sys.stdout.flush()
    if not win:
        print("   -> NOTHING beats %s\n" % lvl); return
    # Pareto front on (gate_us, key_bytes), both minimised
    win.sort(key=lambda o: (o['gate_us'], o['key_bytes']))
    front, best_k = [], float('inf')
    for o in win:
        if o['key_bytes'] < best_k:
            front.append(o); best_k = o['key_bytes']
    print("   -> %d beat it; Pareto front on (time, keys) has %d points:"
          % (len(win), len(front)))
    print("      %-8s %-4s %-5s %-42s %-11s %-9s %-9s %s"
          % ("method", "w", "word", "configuration", "gate us", "speedup",
             "keys", "log2Pf"))
    for o in front:
        print("      %-8s %-4s %-5s %-42s %-11.0f %-9.3fx %-9.2fx %.1f +-%.1f"
              % (o['_meth'], o['autokeys'] if o['_meth'] == 'LMKCDEY' else '-',
                 ('w32*' if o['word_size'] == 32 and word == 64
                  else 'w%d' % o['word_size']),
                 "n=%d q=%d lQ=%d %s qKS=2^%d bKS=%d" %
                 (o['n'], o['q'], o['log_q_big'], fmt_map(o['gadget_map']),
                  int(log2(o['q_ks'])), o['base_ks']),
                 o['gate_us'], ship['gate_us']/o['gate_us'],
                 o['key_bytes']/ship['key_bytes'], o['log2pf'], o['band']))
    # The reader is comparing the SHIPPED set's method against the front's, so
    # caveat on that pairing. Cross-method fronts get the loud version, because
    # the ordering between two accumulators is a property of the build: this
    # exact comparison inverted once when CGGI was parallelised and LMKCDEY
    # was not.
    if any(o['word_size'] == 32 for o in front) and word == 64:
        print("      w32* = runs the 32-bit accumulator on a DEFAULT 64-bit build. Since")
        print("             OpenFHE 9e8045db BTKeyGen's internal32 defaults to true, so a")
        print("             candidate whose refresh key Fits() (Q <= 2^28 and digitsG*gBits")
        print("             + 1 <= 32) takes this path with no flag and no rebuild; the")
        print("             shipped set above does not fit and stays 64-bit. GATE TIME was")
        print("             measured on exactly this path (the w32 cells are timed on the")
        print("             hybrid; a genuine NS32 build agrees within 3.3%). The KEY figure")
        print("             is RESIDENT memory: serialization widens a 32-bit key back to")
        print("             64-bit (binfhecontext.h:161), and measured, the serialized key")
        print("             is BYTE-IDENTICAL either way -- 109,825,677 both ways, where a")
        print("             genuine NS32 build gives 55,115,261. Sizing storage or transfer")
        print("             by this column would be wrong; a genuine NS32 build is what")
        print("             halves it.")
    ship_meth = 'LMKCDEY' if 'LMKCDEY' in lvl else 'GINX'
    for line in m.comparison_caveat([ship_meth] + [o['_meth'] for o in front]):
        print("      " + line)
    print()

def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog='dse_beats', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('levels', nargs='+', metavar='LEVEL[:TOL]',
                    help='e.g. STD128 or STD256Q:2. TOL is the bits below '
                         'nominal the level may certify; it defaults to the '
                         'MINIMUM at which the shipped set itself certifies, '
                         'because holding a candidate to a stricter bar than the '
                         'incumbent is not a comparison.')
    a = ap.parse_args(argv)
    for spec in a.levels:
        if ':' in spec:
            lvl, tol = spec.split(':', 1)
            run(lvl, int(tol))
        else:
            run(spec, _min_tolerance(spec))


def _min_tolerance(level):
    """Lowest tolerance at which the shipped set of this level certifies."""
    import dse_security as sec, dse_shipfit as sf
    P = sf.shipped_params(); cache = sec.load()
    p = P.get(level)
    if p is None:
        return 0
    cand = dict(N=p['N'], n=p['n'], q=p['q'], log_q_big=p['logQ'],
                q_ks=p['qks'], base_ks=p['bks'])
    dist = 'error' if p['keydist'] == 'GAUSSIAN' else 'ternary'
    for tol in range(0, 4):
        if e.security_ok(cand, level, dist, source='estimator', cache=cache,
                         tolerance_bits=tol) is None:
            return tol
    return 0


if __name__ == '__main__':
    sys.exit(_cli())
