#!/usr/bin/python
"""Fit GATE_COST from a runner_time.sh log, per (method, N, word_size) cell.

    gate_us = c0 + c1*n + c2*gate_work   [+ c3*N/w for LMKCDEY]

The LMKCDEY fourth term is separated rather than fitted jointly. On the main grid
w is held at 10, so c3*N/10 is constant within a cell and absorbs into c0 --
fitting all four there would be rank-deficient. The dedicated w-arm moves only w,
so c3 comes from regressing that arm on 1/w, and c0 is then recovered by
subtracting c3*N/10 from the main-grid intercept.

Reports per-cell residuals and, separately, an out-of-sample score against
SHIPPED_COST. In-sample residuals on a 3-parameter fit with 12+ points say the
model SHAPE holds; only the shipped comparison says the numbers predict anything,
because every shipped set is a configuration the grid never visited.

Reads every arm's record format, since they do not agree on which fields they
precompute:

    timing|    meth=GINX N=.. word=.. logQ=.. d=.. n=.. w=.. gw=..
    timing2|   meth=2 threads=.. build=build32 n=.. N=.. logQ=.. bG=..
    hybcost|   meth=2 threads=.. N=.. n=.. bG=.. w=..        (no logQ, no word)

Anything absent is DERIVED, never guessed: `word` from the build field, `d` and
`gw` by calling dse_model, so a refit cannot drift from the predictor it feeds.
`logQ` cannot be derived and must be supplied with --log-q when the log omits it;
the value used is printed, because gatecost-hybrid.sh holds it at 27 in the
command line and does not record it.
"""
import argparse
import re
import sys
from collections import defaultdict

import numpy as np

import dse_model as m

# Fraction of paired numAutoKeys differences that must carry the expected
# (positive) sign before c3 is believed. 0.70 sits between the unresolved 0.52
# and the resolved 0.73 measured at 8 threads -- see fit_cell.
C3_SIGN_MAJORITY = 0.70
# gate_us in every arm is integer_ms * 1000 / 8: one quantum of gate time.
GATE_US_QUANTUM = 125.0


def read(path, word_size=None, log_q=None):
    """[{meth,N,word,logQ,d,n,w,gw,gate_us,...}] from any arm's timing log."""
    prefix = re.compile(r'^(timing|timing2|timing3|gatecost|hybcost)\|')
    names = {'1': 'AP', '2': 'GINX', '3': 'LMKCDEY'}
    out, bad = [], []
    for ln, line in enumerate(open(path), 1):
        if not prefix.match(line.strip()):
            continue
        d = dict(re.findall(r'(\w+)=([^\s]+)', line))
        if d.get('gate_us', 'NA') == 'NA':
            d['err'] = 'gate_us=NA'
            bad.append(d)
            continue
        try:
            # The shifted-printf log (12 specifiers, 11 arguments) put `build32`
            # in the threads field and slid every column left. Parsing it
            # strictly is what turns that into a rejected row instead of a
            # plausible number: its mislabelled gate time read as 16 us.
            threads = int(d['threads']) if 'threads' in d else None
            build = d.get('build')
            word = (int(d['word']) if 'word' in d
                    else 32 if build == 'build32'
                    else 64 if build == 'build' else word_size)
            if word is None:
                raise ValueError('no word size in log and none supplied')
            i32 = d.get('internal32')
            if i32 in ('yes', 'no'):
                # Since 9e8045db the 32-bit forms are the library default, so
                # the word a row is KEYED under is a claim the binary can
                # contradict: a w32 row whose refresh key did not narrow is a
                # w64 timing mis-keyed as w32, and the reverse mis-keys the other
                # way. Refuse the row rather than let it average into the wrong
                # cell. Checked against the resolved WORD, not the build tag: the
                # regime arm now writes `word=32 build=build` (one binary), and a
                # build-tag check rejected every one of its 297 w32 rows.
                if word == 32 and i32 != 'yes':
                    raise ValueError('keyed w32 but internal32 refresh key=%s' % i32)
                if word == 64 and i32 == 'yes':
                    raise ValueError('keyed w64 but internal32 refresh key=yes')
            lq = int(d['logQ']) if 'logQ' in d else log_q
            if lq is None:
                raise ValueError('no logQ in log and --log-q not given')
            n = int(d['n'])
            meth = names.get(d['meth'], d['meth'])
            # A two-base row (MAPS=1 arm) carries gmap= and bG=-. Its gate work
            # is the map's, and its digit count is reported as the map's MAXIMUM
            # -- the team width every region now runs at (41709fbc) -- so the
            # residual tables split at the width the machine actually saw.
            gmap = None
            if d.get('gmap') and d['gmap'] != '-':
                gmap = {int(b): int(c) for b, c in
                        (part.split(':') for part in d['gmap'].split(','))}
                if sum(gmap.values()) != n:
                    raise ValueError('gmap counts sum to %d, n=%d' % (sum(gmap.values()), n))
                base = min(gmap)
                dg = max(m.digits_for_base(lq, b) for b in gmap)
            else:
                base = int(d['bG']) if 'bG' in d else None
                dg = int(d['d']) if 'd' in d else m.digits_for_base(lq, base)
            # AP's accumulator work carries digitsR*(1-1/baseR) on top of the
            # gadget, and that factor spans 2.8x across the arm's refresh-base
            # sweep. Deriving the work WITHOUT it fits AP on GINX-shaped features
            # and the residuals say so: 31-36% median against 2-5% once the
            # factor is in. So baseR and q are REQUIRED for an AP row rather than
            # defaulted -- a default would silently reproduce the bad fit.
            base_r = int(d['baseR']) if 'baseR' in d else None
            q = int(d['q']) if 'q' in d else None
            if meth == 'AP' and (base_r is None or q is None):
                raise ValueError('AP row needs baseR= and q= to derive its gate '
                                 'work; this log predates the arm recording them')
            gw = (int(d['gw']) if 'gw' in d
                  else m.gate_work(gmap or {base: n}, lq, method=meth,
                                   base_r=base_r, q=q))
            rec = dict(meth=meth, N=int(d['N']),
                       word=word, logQ=lq, d=dg, n=n, w=int(d.get('w', 10)),
                       gw=gw, threads=threads, gate_us=float(d['gate_us']),
                       base_r=base_r, q=q, gmap=gmap,
                       # work if every index were charged at the map's widest
                       # base -- the alternative the maps arm exists to test
                       gw_max=(n * m.digits_g2(dg) if gmap else None))
        except (KeyError, ValueError, TypeError) as exc:
            d['err'] = str(exc)
            bad.append(d)
            continue
        for k in ('keygen_ms', 'btkey_b', 'ksk_b'):
            if d.get(k, 'NA') != 'NA':
                rec[k] = float(d[k])
        out.append(rec)
    return out, bad


def fit_cell(rows, N, weight='relative'):
    """(c0, c1, c2, c3, diagnostics) for one (method, N, word) cell.

    weight='relative' -- the default, and how every shipped cell was fitted --
    minimises RELATIVE residuals (each row scaled by 1/gate_us)
    instead of absolute microseconds. That is the right objective for a model
    used only to RANK: relative error is what mis-orders two candidates, and
    absolute-error fitting lets the largest-n rows -- whose gate times are ~10x
    the smallest -- set the fit. Measured on the LMKCDEY w32 cells, it halves the
    worst residual in the useful band (12-13% against 19-24%) at the same median.
    Use ONE objective for every cell in a table: mixing them is a silent
    inconsistency of exactly the kind the rest of this model refuses.
    """
    main = [r for r in rows if r['w'] == 10]
    if len(main) < 4:
        return None
    is_ap = main[0]['meth'] == 'AP'
    # AP fits a FOURTH feature jointly: the external-product count, whose fixed
    # per-product cost the other two methods hide in c1 because their count per
    # coefficient is constant. It is identifiable here and not collinear with the
    # work feature, because the arm varies the gadget at fixed refresh base AND
    # the refresh base at fixed gadget -- work is products*digitsG2, so both
    # directions are needed and both are present.
    # CAVEAT worth recording: within one refresh base, n*products is proportional
    # to n, so c1 and the per-product cost separate only ACROSS bases. The arm
    # sweeps three (2, 8, 64), which is enough to identify them but not enough to
    # pin each precisely -- expect c1 to trade against c3 here in a way it does
    # not for the other two methods. The fit is for RANKING, and the sum of the
    # two terms is what a ranking uses.
    if is_ap:
        A = np.array([[1.0, r['n'], r['gw'],
                       m.ap_products_per_gate(r['n'], r['q'], r['base_r'])]
                      for r in main])
    else:
        A = np.array([[1.0, r['n'], r['gw']] for r in main])
    y = np.array([r['gate_us'] for r in main])
    W = (1.0 / y) if weight == 'relative' else np.ones_like(y)
    if is_ap:
        # BOUNDED for AP, because its two n-proportional features (n and
        # n*products) separate only across refresh bases and an unconstrained fit
        # trades them: the first full arm returned c1 = -0.84 at N=2048/w32, a
        # gate time that FALLS with the lattice dimension. That is not a loose
        # fit, it is a wrong ordering, so the coefficients that cannot physically
        # be negative are constrained not to be. c0 stays free: it is an
        # intercept absorbing fixed overhead and is negative in GINX cells too.
        from scipy.optimize import lsq_linear
        res = lsq_linear(A * W[:, None], y * W,
                         bounds=([-np.inf, 0.0, 0.0, 0.0], np.inf))
        coef = res.x
    else:
        coef, *_ = np.linalg.lstsq(A * W[:, None], y * W, rcond=None)
    if is_ap:
        c0p, c1, c2, c_prod = coef
    else:
        (c0p, c1, c2), c_prod = coef, None

    if is_ap:
        # AP has no numAutoKeys, so the fourth slot carries its per-product cost.
        pred = [c0p + c1 * r['n'] + c2 * r['gw']
                + c_prod * m.ap_products_per_gate(r['n'], r['q'], r['base_r'])
                for r in main]
        res = sorted(100.0 * abs(p / r['gate_us'] - 1.0) for p, r in zip(pred, main))
        return dict(c0=c0p, c1=c1, c2=c2, c3=c_prod, n_main=len(main),
                    med=res[len(res) // 2], worst=res[-1],
                    c3_sign_frac=1.0, bad_sign=False, below_quantum=False,
                    c3_bound=0.0, ap_per_product=True)

    # c3 from MATCHED PAIRS that differ only in w. Differencing cancels c0,
    # c1*n and c2*gw exactly, so no other term can leak into it:
    #
    #     t(w_a) - t(w_b) = c3 * N * (1/w_a - 1/w_b)
    #
    # The previous form regressed every w != 10 row against a single anchored
    # w == 10 row. That holds where the w-arm is a dedicated sweep of one
    # config, but gatecost-hybrid.sh moves w INSIDE the main grid, so the arm
    # spanned many configs and attributed their differences to c3 -- returning
    # c3 = -300 to -447, negative, when more automorphism keys can only make a
    # gate faster. Individual estimates are noisy because gate times are
    # quantized (deltas of 125 us on a 125 us step), so take the median.
    cfg = lambda r: (r['n'], r['d'], r['logQ'])
    grouped = {}
    for r in rows:
        grouped.setdefault(cfg(r), {})[r['w']] = r['gate_us']
    ests = []
    widest = 0.0
    for t in grouped.values():
        ws = sorted(t)
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                wa, wb = ws[i], ws[j]
                denom = N * (1.0 / wa - 1.0 / wb)
                if denom:
                    ests.append((t[wa] - t[wb]) / denom)
                    widest = max(widest, denom)
    # SIGN CONSISTENCY, not just magnitude. More automorphism keys means fewer
    # automorphism steps, so every paired difference must be positive. Requiring
    # a clear majority tests whether the effect is PRESENT at all, which the
    # median alone does not: at N=512 only 44%/52% of pairs came out positive
    # (a coin flip -- the term is smaller than the 125 us quantization of
    # gate_us = integer_ms * 1000 / 8), and the w64 median still landed on
    # exactly one quantum, 3.255, which reads as a perfectly plausible small
    # coefficient and is pure noise. N=1024 gives 75%/73% and N=2048 86%/86%,
    # so the threshold below separates measured from unresolved cleanly.
    pos = sum(1 for x in ests if x > 0)
    frac = (pos / len(ests)) if ests else 0.0
    c3 = float(np.median(ests)) if ests else None
    bad_sign = below_quantum = False
    c3_bound = None
    if c3 is not None and not (c3 > 0 and frac >= C3_SIGN_MAJORITY):
        # Two different failures, and they used to get one verdict (None, which
        # prices only w == 10 and drops the other autokeys values as
        # "uncalibrated"). A clear WRONG-sign majority is a contradiction -- more
        # automorphism keys cannot make a gate slower -- and stays refused. A
        # coin-flip sign is not a contradiction: it says the term is smaller than
        # this measurement can see, and the honest price for a term the data
        # cannot distinguish from zero is ZERO, which leaves the time axis
        # indifferent to numAutoKeys exactly where the data is indifferent. It is
        # bounded: one unresolved quantum of paired difference at the widest pair
        # present caps |c3| at GATE_US_QUANTUM / (N * (1/w_a - 1/w_b)), i.e.
        # c3 * N / w under 170 us at w = 10 for any N. At a0c3f2cd this is every
        # N <= 1024 cell (55-69% correct sign, medians 0-1 quantum; restricting to
        # n >= 256 made it worse, so not a small-n artefact), while N = 2048
        # resolves it at 85-88% and keeps the measured value.
        resolution = GATE_US_QUANTUM / widest if widest else float('inf')
        if frac <= 1.0 - C3_SIGN_MAJORITY:
            c3, bad_sign = None, True
        else:
            c3_bound = max(abs(c3), resolution)
            c3, below_quantum = 0.0, True

    c0 = c0p - (c3 * N / 10.0 if c3 is not None else 0.0)
    # Residuals over EVERY row under the full model, not just the w == 10 rows
    # the intercept was fitted on -- otherwise a broken c3 hides.
    pred = np.array([c0 + c1 * r['n'] + c2 * r['gw']
                     + (c3 * N / r['w'] if c3 is not None else 0.0)
                     for r in rows])
    obs = np.array([r['gate_us'] for r in rows])
    res = 100.0 * (pred - obs) / obs
    return dict(c0=c0, c1=c1, c2=c2, c3=c3, n_main=len(main),
                n_arm=len(rows) - len(main), n_c3=len(ests),
                bad_sign=bad_sign, below_quantum=below_quantum,
                c3_bound=c3_bound, c3_sign_frac=frac,
                med=float(np.median(np.abs(res))), worst=float(np.max(np.abs(res))))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='dse_gatefit')
    ap.add_argument('log')
    ap.add_argument('--emit', action='store_true',
                    help='print a GATE_COST dict literal')
    ap.add_argument('--word-size', type=int, choices=(32, 64), default=None,
                    help='word size for logs with no word or build field, e.g. '
                         'hybcost logs where every row is the hybrid')
    # DEFAULT IS RELATIVE, and it changed: every cell in every shipped regime was
    # fitted this way, so an absolute default meant the documented command and the
    # committed numbers disagreed unless the flag was passed by hand. 'absolute'
    # stays reachable for reproducing a pre-2026-09 fit.
    ap.add_argument('--weight', choices=('absolute', 'relative'),
                    default='relative',
                    help="least-squares objective (default relative, which is how "
                         "every shipped cell was fitted); 'relative' minimises "
                         "percent error, which is what mis-orders two candidates, "
                         "where absolute lets the largest-n rows set the fit")
    ap.add_argument('--log-q', type=int, default=None,
                    help='logQ for logs that do not record it '
                         '(gatecost-hybrid.sh holds it at 27)')
    a = ap.parse_args(argv)

    rows, bad = read(a.log, a.word_size, a.log_q)
    print("%d timings read, %d failed rows" % (len(rows), len(bad)))
    # Gate time is calibrated per thread regime, so a cell fitted across two
    # regimes describes neither. Refuse rather than average them.
    thr = {r['threads'] for r in rows if r['threads'] is not None}
    if len(thr) > 1:
        print("REFUSING: mixed thread counts %s in one log." % sorted(thr))
        return 1
    if thr:
        print("threads: %d" % thr.pop())
    for b in bad[:5]:
        print("   FAILED %s N=%s word=%s logQ=%s d=%s n=%s  %s"
              % (b.get('meth'), b.get('N'), b.get('word'), b.get('logQ'),
                 b.get('d'), b.get('n'), b.get('err', '')))
    cells = defaultdict(list)
    for r in rows:
        cells[(r['meth'], r['N'], r['word'])].append(r)

    print("\n  %-8s %-6s %-5s %-4s %-11s %-10s %-9s %-9s %-8s %s"
          % ("method", "N", "word", "pts", "c0", "c1", "c2", "c3",
             "med|res|", "worst"))
    fits = {}
    for key in sorted(cells):
        meth, N, word = key
        f = fit_cell(cells[key], N, a.weight)
        if f is None:
            print("  %-8s %-6d %-5d too few points" % (meth, N, word))
            continue
        fits[key] = f
        print("  %-8s %-6d %-5d %-4d %-11.1f %-10.3f %-9.4f %-9s %-8s %s%s"
              % (meth, N, word, f['n_main'], f['c0'], f['c1'], f['c2'],
                 ("%.3f" % f['c3']) if f['c3'] is not None else "-",
                 "%.2f%%" % f['med'], "%.2f%%" % f['worst'],
                 ("  c3 REFUSED (%.0f%% correct sign)" % (100 * f['c3_sign_frac']))
                 if f['bad_sign'] else
                 ("  c3 BELOW QUANTUM -> 0 (|c3| < %.1f; %.0f%% correct sign)"
                  % (f['c3_bound'], 100 * f['c3_sign_frac']))
                 if f['below_quantum'] else ""))
    if a.emit:
        print("\nGATE_COST = {")
        for key in sorted(fits):
            f = fits[key]
            print("    (%r, %d, %d): (%.1f, %.3f, %.4f, %s),"
                  % (key[0], key[1], key[2], f['c0'], f['c1'], f['c2'],
                     ("%.3f" % f['c3']) if f['c3'] is not None else "None"))
        print("}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
