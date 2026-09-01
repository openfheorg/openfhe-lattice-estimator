#!/usr/bin/python

'''Identify the accumulator noise term -- the one term the model has not pinned down.

Everything else is now derived rather than fitted. Key switching is
sqrt(N*d_KS)*sigma*(q/q_KS) and both modulus-switch rounding terms are
Var(s)/12 times a coefficient count, all three confirmed by isolated measurement
to under 1% (dse_isolate). So subtracting them from a measured total leaves the
accumulator alone, with no fitted constant absorbing the difference.

What remains open is the SHAPE. The current form is

    acc_var@Q = k * SUM_j count_j * (digitsG_j - 1) * baseG_j^2

and it does not survive contact with the ladder: holding logQ = 27 and moving only
the gadget split, the implied k is 7322 at base 2^5, 4985 at 2^7 and 7334 at 2^9.
The outlier is the MIDDLE base, which rules out any monotonic story -- neither
over-coverage nor digit count is ordered that way.

    > python3 scripts/paramsestimator/dse_accfit.py --measured
    > python3 scripts/paramsestimator/dse_accfit.py ladder2.log lin.log

Candidate shapes compared
-------------------------
    S1  k * (d-1) * b^2                     the current form
    S2  k * d * b^2                         is the -1 of the approximate
                                            decomposition actually there?
    S3  A * (d-1) * b^2 + B * (Q/b^(d-1))^2 a Q-dependent residual. Kept only as
                                            a foil: the code says it is wrong.
    S4  k_b * (d-1) * b^2                   per-base constants: descriptive only,
                                            but it says how much structure a
                                            single constant is failing to carry.
    S5  A * (d-1) * b^2 + C * b^2           the dropped digit as a flat b^2.
    S6  k * b^2                             no digit dependence at all.
    S9  k * [(d-2)*b^2 + min(b,Q/b^(d-1))^2]  ONLY the positions the code emits.
    S7  k * [(d-1)*b^2 + (Q/b^(d-1))^2]     every digit position accounted for,
                                            ONE constant, nothing free.
    S8  A * [(d-2)*b^2 + (Q/b^(d-1))^2]     as S7 but with the dropped digit's
          + C * b^2                         constant free, because it multiplies
                                            the RGSW message where the others
                                            multiply the RGSW noise.

Where S5 comes from
-------------------
`RingGSWAccumulator::SignedDigitDecompose` (rgsw-acc.cpp:55) runs

    for (d = 0; d < digitsG2; d += 2)
        shift = ((d >> 1) + 1) * gBits

so the shift starts at `gBits`, not 0: digit position 0 -- the LEAST significant
-- is never emitted. The comment says so outright ("the first digit is ignored").
The unrepresented residual is therefore whatever lives in that bottom window,
bounded by baseG/2 in absolute units at Q, with variance about b^2/12 per
coefficient.

That fixes the shape. The residual's size depends on the BASE alone: not on the
digit count, and not on Q. So the second term is C*b^2, and the whole model
collapses to S1 with an offset digit count,

    k * (d - 1 + c) * b^2,      c = C/A

which is why a single k fitted across bases has to fail, and fail
non-monotonically: bases with few digits are the ones where a constant offset
matters most. S2 is this form with c pinned to exactly 1, which is why it already
did better than S1 without being right.

Why a second modulus is what identifies this: at fixed Q the base determines the
digit count, so b and d are perfectly collinear and no fit can separate them.
Moving logQ changes d for some bases and not others, which breaks the tie.
Between logQ 27 and 25 only base 2^5 changes (d = 6 -> 5), so S1 predicts the
per-coefficient variance ratio is the same 16.00 for 2^7 and 2^9 and 12.80 for
2^5. A common factor for all three would falsify the (d-1).
'''

from math import log2, sqrt

import argparse
import sys

import dse_model as m
from dse_sweeplog import runner_records


# --------------------------------------------------------------------------
# turning a measured total into an accumulator-only per-coefficient variance
# --------------------------------------------------------------------------

def acc_per_coeff(r, key_dist="UNIFORM_TERNARY"):
    """Measured accumulator variance at q, per gadget coefficient AND per N.

    Subtracts the two derived terms. Returns None where the subtraction leaves
    less than a fifth of the total, because then the answer is dominated by the
    1-2% uncertainty on terms that are much larger than what is left.
    """
    N, n, q, logQ = r['N'], r['n'], float(r['q']), r['logQ']
    q_ks, base_ks = float(r['q_ks'] if 'q_ks' in r else r['qks']), r['baseks']
    sigma = r.get('sigma_in', 3.19)

    var_tot   = r['sigma_measured'] ** 2
    var_ks    = m.sigma_keyswitch(N, q, q_ks, base_ks, sigma) ** 2
    var_round = m.modswitch_var_per_coeff(q_ks, q, key_dist) * n
    var_first = m.modswitch_var_first_switch(N, q, q_ks, key_dist)
    var_acc   = var_tot - var_ks - var_round - var_first

    share = var_acc / var_tot if var_tot > 0 else 0.0
    if share < 0.20:
        return None, share
    # Divided by N because the accumulator is linear in N (measured N^0.985), and
    # the data now spans N = 512..2048. Leaving N in would let it masquerade as
    # part of the shape.
    return var_acc / N, share


def basis(gadget_map, logQ, shape):
    """SUM_j count_j * f(base_j, d_j) for the chosen shape, as a dict of terms.

    Returned per shape-parameter so a linear solve can use it directly.
    """
    if shape == 'S1':
        return {'k': sum(c * (m.digits_for_base(logQ, b) - 1) * float(b) ** 2
                         for b, c in gadget_map.items())}
    if shape == 'S2':
        return {'k': sum(c * m.digits_for_base(logQ, b) * float(b) ** 2
                         for b, c in gadget_map.items())}
    if shape == 'S3':
        # term B is written at Q here, like term A; the caller scales both by
        # (q/Q)^2, after which B's Q-dependence cancels exactly.
        Q = 2.0 ** logQ
        return {'A': sum(c * (m.digits_for_base(logQ, b) - 1) * float(b) ** 2
                         for b, c in gadget_map.items()),
                'B': sum(c * (Q / float(b) ** (m.digits_for_base(logQ, b) - 1)) ** 2
                         for b, c in gadget_map.items())}
    if shape == 'S6':
        return {'k': sum(c * float(b) ** 2 for b, c in gadget_map.items())}
    if shape == 'S4':
        return {('k', b): c * (m.digits_for_base(logQ, b) - 1) * float(b) ** 2
                for b, c in gadget_map.items()}
    if shape == 'S5':
        return {'A': sum(c * (m.digits_for_base(logQ, b) - 1) * float(b) ** 2
                         for b, c in gadget_map.items()),
                'C': sum(c * float(b) ** 2 for b, c in gadget_map.items())}
    if shape == 'S9':
        # ONLY the digit positions the implementation actually emits: 1..d-1.
        # Positions 1..d-2 see a full window (variance b^2); position d-1 is the
        # top one and a misaligned modulus confines it to Q/b^(d-1) values. Position
        # 0 is dropped and contributes NOTHING -- which is not an assumption, it is
        # what the fit says: freeing its coefficient (S8) drives it to 0.005 against
        # 6.617 for the emitted positions.
        total = 0.0
        for b, c in gadget_map.items():
            d = m.digits_for_base(logQ, b)
            top = min(float(b), (2.0 ** logQ) / float(b) ** (d - 1))
            total += c * ((d - 2) * float(b) ** 2 + top ** 2)
        return {'k': total}
    if shape in ('S7', 'S8'):
        # Per-digit-position variance, read off SignedDigitDecompose.
        #
        #   position 0        DROPPED. Its value is the representation error, and
        #                     it multiplies the RGSW MESSAGE, not the RGSW noise.
        #                     Full window, so variance b^2/12.
        #   positions 1..d-2  full window, variance b^2/12 each, each multiplied
        #                     by one RGSW row's noise.
        #   position d-1      TOP. The excess-H window is w = x + H with x
        #                     centered in (-Q/2, Q/2], so w/b^(d-1) spans an
        #                     interval of width Q/b^(d-1) -- fewer than b values
        #                     whenever b^d > Q. Variance (Q/b^(d-1))^2/12, and
        #                     Q <= b^d makes this never exceed b^2/12.
        #
        # So over-coverage is not a correction bolted onto the model: it IS the
        # top digit's range, and it is why the aligned case (b^d == Q) is the one
        # where the top digit behaves like every other.
        noise_side, msg_side = 0.0, 0.0
        for b, c in gadget_map.items():
            d = m.digits_for_base(logQ, b)
            top = (2.0 ** logQ) / float(b) ** (d - 1)
            noise_side += c * ((d - 2) * float(b) ** 2 + min(float(b), top) ** 2)
            msg_side   += c * float(b) ** 2
        if shape == 'S8':
            return {'A': noise_side, 'C': msg_side}
        # S7 ties them: one constant, no freedom left anywhere.
        return {'k': noise_side + msg_side}
    raise ValueError(shape)


def solve(rows, shape):
    """Least squares on RELATIVE variance error, returning (params, per-row error).

    Relative, not absolute: the rows span two orders of magnitude in variance and
    an absolute fit is decided entirely by the largest. That is the same mistake
    the first sigma fit made, where fitting on variance returned a model 80% low
    on the median candidate.
    """
    import numpy as np

    names = []
    for var_acc, r in rows:
        for k in basis(r['gmap'], r['logQ'], shape):
            if k not in names:
                names.append(k)
    A, y = [], []
    for var_acc, r in rows:
        Q = 2.0 ** r['logQ']
        scale = (float(r['q']) / Q) ** 2
        b = basis(r['gmap'], r['logQ'], shape)
        # divide through by the measured value: that is what makes it relative
        A.append([b.get(nm, 0.0) * scale / var_acc for nm in names])
        y.append(1.0)
    sol, *_ = np.linalg.lstsq(np.array(A), np.array(y), rcond=None)
    params = dict(zip(names, sol))

    errs = []
    for var_acc, r in rows:
        Q = 2.0 ** r['logQ']
        scale = (float(r['q']) / Q) ** 2
        b = basis(r['gmap'], r['logQ'], shape)
        pred = sum(params.get(nm, 0.0) * b.get(nm, 0.0) for nm in b) * scale
        errs.append((pred / var_acc - 1.0, r))
    return params, errs


def measured_rows():
    """The logQ=27 ladder, from the checked-in measurements."""
    from dse_measured import LADDER
    out = []
    for rung, rows in LADDER.items():
        if 'SATURATED' in rung:
            continue
        for f, n, gmap, sd in rows:
            out.append(dict(N=1024, n=n, q=2048, logQ=27, qks=1 << 27, baseks=32,
                            gmap=gmap, sigma_measured=sd, rung=rung))
    return out


def main(paths, use_measured):
    rows = []
    if use_measured:
        rows += measured_rows()
    for p in paths:
        for r in runner_records(p):
            if r.get('gmap') and r.get('logQ'):
                rows.append(r)

    usable, skipped = [], 0
    for r in rows:
        r.setdefault('qks', r.get('q_ks'))
        va, share = acc_per_coeff(r)
        if va is None:
            skipped += 1
            continue
        usable.append((va, r))

    print("%d measured configurations, %d usable (accumulator >= 20%% of variance)"
          % (len(rows), len(usable)))
    if skipped:
        print("%d skipped: the accumulator is too small a part of the total for the"
              % skipped)
        print("   subtraction to say anything about it.")
    if not usable:
        return 1

    print("\nshape value k*T, by (logQ, base) -- comparable across q, Q, N and n:")
    print("  var_acc / (N * count * (q/Q)^2). Normalising by (q/Q)^2 as well as by N")
    print("  is what lets a q=1024 run and a q=2048 run sit in the same row; without")
    print("  it they differ by 4x and averaging them together says nothing.")
    print("  A shape is right when its k column is CONSTANT down the rows.\n")
    single = {}
    for va, r in usable:
        if len(r['gmap']) == 1:
            b, c = next(iter(r['gmap'].items()))
            scale = (float(r['q']) / 2.0 ** r['logQ']) ** 2
            single.setdefault((r['logQ'], b), []).append(va / (c * scale))

    def T9(lq, b):
        d = m.digits_for_base(lq, b)
        return (d - 2) * float(b) ** 2 + min(float(b), (2.0 ** lq) / float(b) ** (d - 1)) ** 2

    for (lq, b), vals in sorted(single.items()):
        d  = m.digits_for_base(lq, b)
        kT = sum(vals) / len(vals)
        spread = (max(vals) / min(vals) - 1.0) * 100 if len(vals) > 1 else 0.0
        print("  logQ=%-3d base=2^%-3d d=%d  k*T %12.6g   k via S9 %7.3f   k via S1 %7.3f  (%d run%s%s)"
              % (lq, b.bit_length() - 1, d, kT, kT / T9(lq, b),
                 kT / ((d - 1) * float(b) ** 2), len(vals),
                 "" if len(vals) == 1 else "s",
                 "" if spread == 0 else ", spread %.1f%%" % spread))

    print("\nwithin-base logQ scan -- d is FIXED, so S1 and S6 predict a flat k*T:")
    for b in sorted({bb for (_, bb) in single}):
        lqs = sorted(lq for (lq, bb) in single if bb == b)
        if len(lqs) < 2:
            continue
        d = m.digits_for_base(lqs[0], b)
        if any(m.digits_for_base(lq, b) != d for lq in lqs):
            continue
        ref  = sum(single[(lqs[0], b)]) / len(single[(lqs[0], b)])
        ref9 = T9(lqs[0], b)
        parts = ["logQ%d x%.2f (S9 x%.2f)"
                 % (lq, (sum(single[(lq, b)]) / len(single[(lq, b)])) / ref, T9(lq, b) / ref9)
                 for lq in lqs]
        print("  base 2^%-3d d=%d   %s" % (b.bit_length() - 1, d, "   ".join(parts)))

    print("\nshape comparison (least squares on relative variance error):")
    for shape in ('S1', 'S2', 'S6', 'S3', 'S4', 'S5', 'S7', 'S8', 'S9'):
        try:
            params, errs = solve(usable, shape)
        except Exception as exc:                       # rank-deficient, typically
            print("  %-3s  not solvable on this data: %s" % (shape, exc))
            continue
        e = sorted(abs(x) for x, _ in errs)
        pstr = "  ".join("%s=%.4g" % (k if isinstance(k, str) else "k[2^%d]" % (k[1].bit_length() - 1), v)
                         for k, v in sorted(params.items(), key=str))
        extra = ""
        if shape in ('S5', 'S8') and params.get('A'):
            extra = "   -> C/A = %.2f" % (params['C'] / params['A'])
        print("  %-3s  median |err| %5.1f%%   worst %5.1f%%   %s%s"
              % (shape, 100 * e[len(e) // 2], 100 * e[-1], pstr, extra))

    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_accfit')
    ap.add_argument('logs', nargs='*', help='runner logs carrying `record|` lines')
    ap.add_argument('--measured', action='store_true',
                    help='include the checked-in logQ=27 ladder from dse_measured')
    a = ap.parse_args()
    sys.exit(main(a.logs, a.measured))
