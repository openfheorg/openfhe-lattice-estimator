#!/usr/bin/python

'''Design and fit the calibration measurements the noise model cannot get from a sweep.

Why a designed set is needed at all
-----------------------------------
A parameter sweep drives n down and q_KS up, which is exactly the region where
the accumulator term vanishes -- in the 8h45m sweep it is under 5% of variance in
251 of 284 candidates. Worse, at a fixed Q the gadget base DETERMINES the digit
count (d = ceil(logQ / log2 b)), so every accumulator-dominated record lies on a
one-dimensional curve through the (b, d) plane. The two exponents are perfectly
collinear and no amount of sweeping separates them: regressing them on that data
returns b^-0.34 and (d-1)^-3.6, i.e. coarser bases producing LESS noise.

The fix is to choose logQ = log2(base) * digits. Then base and digits are set
independently, and every cell is exactly gadget-aligned (base^digits == Q), which
removes alignment bias as a confound from the same grid.

A separate misaligned arm re-introduces it deliberately, holding base and digits
fixed and moving only Q, so the bias term is measured by difference rather than
inferred.

These configurations are chosen to identify noise physics, NOT to be deployable.
Most are far below any security level. Nothing here should be read as a
candidate parameter set.

    > python3 scripts/paramsestimator/dse_calibrate.py plan
    > python3 scripts/paramsestimator/dse_calibrate.py plan --emit-commands
    > python3 scripts/paramsestimator/dse_calibrate.py fit calibration.log
'''

from math import log2, sqrt

import argparse
import sys

import dse_model as m

# LastPrime(nBits, cyclOrder) THROWS rather than returning a shorter prime, so
# the grid must avoid the widths it cannot serve. Measured against the pinned
# build over nBits 10..60 (see the notes in binfhe_params.py): for cyclOrder
# 2048 it fails at nBits <= 13, for 4096 at <= 13 and at 15, for 8192 at <= 15.
_LASTPRIME_FAILS = {2048: set(range(10, 14)),
                    4096: set(range(10, 14)) | {15},
                    8192: set(range(10, 16))}

NS32_MAX_LOGQ = m.NS32_MAX_LOGQ if hasattr(m, 'NS32_MAX_LOGQ') else 28
NS64_MAX_LOGQ = 60


def _feasible(N, log_q_big):
    if log_q_big in _LASTPRIME_FAILS.get(2 * N, set()):
        return False
    return 10 <= log_q_big <= NS64_MAX_LOGQ


# Predicted sigma must land well under the decision threshold q/(2p) or the gate
# output is pure noise, the phase error wraps, and the measurement reports
# saturation instead of the accumulator. It must also stay meaningfully above the
# rounding floor sqrt(MODSWITCH*n) or there is no accumulator signal to fit.
SIGMA_TARGET_FRACTION = 0.15      # of the decision threshold
N_MIN, N_MAX = 32, 2048


def _threshold(q, inputs=2):
    return float(q) / (2 * (2 * inputs))


def _size_n(N, q, log_q_big, q_ks, base_g, base_ks, sigma, k_acc):
    """Smallest-error n that puts predicted sigma near the target band.

    sigma scales as sqrt(n) for BOTH the accumulator and the rounding floor, so n
    moves the whole prediction without disturbing the ratio being measured -- it
    is the one lever that buys headroom against saturation for free.
    """
    target = SIGMA_TARGET_FRACTION * _threshold(q)
    best = None
    for n in (32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048):
        s = m.sigma_total_at_q(N, n, q, log_q_big, q_ks, base_ks, sigma,
                               {base_g: n}, k_acc=k_acc)
        d = abs(log2(s / target)) if s > 0 else 99
        if (best is None) or (d < best[0]):
            best = (d, n, s)
    return best[1], best[2]


def plan(N=1024, q=2048, base_ks=32, sigma=3.19, betas=(4, 5, 6, 7), digits=(3, 4, 5)):
    """The calibration grid, each cell sized so the measurement is meaningful.

    Aligned arm: logQ = beta*d, so base and digit count move independently and
    every cell is exactly gadget-aligned. Misaligned arm: same (base, digits),
    logQ reduced, isolating alignment bias by difference.
    """
    thresh = _threshold(q)
    cells = []

    def add(arm, beta, d, log_q_big):
        if not _feasible(N, log_q_big):
            return
        base_g = 1 << beta
        if m.digits_for_base(log_q_big, base_g) != d:
            return
        q_ks = 1 << log_q_big
        if not m.within_model_domain(N, q_ks, base_ks, sigma):
            return
        # size with the LARGER candidate constant so a cell cannot saturate if
        # that one is right, then check it still carries signal if it is not
        n, s_hi = _size_n(N, q, log_q_big, q_ks, base_g, base_ks, sigma, m.K_ACC)
        if s_hi > 0.5 * thresh:
            return                                  # unreachable: saturates at every n
        floor = sqrt(m.MODSWITCH_VAR_PER_COEFF * n)
        if s_hi < 1.15 * floor:
            return                                  # no accumulator signal above rounding
        cells.append(dict(arm=arm, N=N, n=n, q=q, logQ=log_q_big, q_ks=q_ks,
                          base_g=base_g, base_ks=base_ks, sigma=sigma,
                          digits=d, beta=beta, pred_sigma=s_hi, thresh=thresh))

    for beta in betas:
        for d in digits:
            add('aligned', beta, d, beta * d)
    for beta in betas:
        for d in digits:
            for drop in (1, 2):
                lq = beta * d - drop
                if lq > beta * (d - 1):
                    add('misaligned-%db' % drop, beta, d, lq)
    return cells


# ---------------------------------------------------------------------------
# The base ladder -- the experiment that actually identifies the accumulator.
#
# Hold Q, n, q_KS and base_KS fixed and move ONLY the gadget split between two
# bases. Key switching and rounding are then identical in every row and cancel in
# differences, while acc_var is linear in the split with both digit counts fixed
# and known. Nothing is left to be collinear with.
#
# Two hard lessons are built into this design:
#
#  1. The constant CANNOT be co-fitted. counts sum to n identically, so
#     [c_a, c_b, 1] has rank 2 and a free three-parameter fit returns an
#     arbitrary minimum-norm answer -- it drove the constant to 0 where it had to
#     be 56.7 and reported the shape as 668% wrong. The rung measures a RATIO;
#     the constant must come from the independently derived rounding term.
#  2. Adjacent bases only. A 2^5/2^9 pair spans 102x, which sounds ideal but
#     leaves the light end at 4.4% of variance -- sitting on the rounding floor,
#     where its coefficient is barely determined. ~10x rungs keep both ends
#     measurable, and overlapping bases let consecutive rungs check each other.
# ---------------------------------------------------------------------------
LADDER_SPLITS = (0.0, 0.25, 0.5, 0.75, 1.0)


# Which rungs are worth running at a given logQ. The 2^9/2^11 and 2^11/2^13
# rungs SATURATED at logQ=27 at every n tried (256 and 16), pinning sigma at the
# q/(p*sqrt(12)) ceiling independently of n -- which is the proof it is a ceiling
# and not a measurement. They are excluded by default rather than re-run: the
# heavy-over-coverage regime they probe (k ~ 146000) is a different mechanism
# from the one the ladder identifies, and it cannot be measured while it wraps.
LADDER_RUNGS_DEFAULT = (((1 << 5), (1 << 7), 1024),
                        ((1 << 7), (1 << 9), 1024))
LADDER_RUNGS_ALL = LADDER_RUNGS_DEFAULT + (((1 << 9), (1 << 11), 512),
                                           ((1 << 11), (1 << 13), 48))


def ladder(N=1024, q=2048, log_q_big=27, base_ks=32, sigma=3.19, rungs=LADDER_RUNGS_ALL):
    """Rungs of adjacent base pairs, each sized so neither end saturates.

    n falls as the bases coarsen because acc_var grows as b^2: at 2^13 with
    n=1024 the prediction is ~463 against a decision threshold of 256, i.e. pure
    wrap-around. Overlaps (2^7, 2^9, 2^11 each appear twice) are deliberate --
    a base measured at two different n also tests the model's linearity in n.

    Running the SAME rung at a second logQ is what tests the (d-1) shape, and it
    is nearly free because logQ moves two things with known factors:

      - (q/Q)^2 scales every coefficient identically, so it cancels in a ratio;
      - d = ceil(logQ / log2 base) changes for SOME bases and not others.

    Between logQ 27 and 25 only base 2^5 changes digit count (6 -> 5). So the
    model predicts the per-coefficient variance ratio 27->25 is exactly 4.00 for
    2^7 and 2^9 (pure modulus scaling) and 4.00 * 4/5 = 3.20 for 2^5. One
    coefficient deviating from the common factor, by the predicted amount, is a
    direct test of (d-1) that no single-Q ladder can perform.
    """
    q_ks = 1 << log_q_big
    out = []
    for lo, hi, n in rungs:
        for f in LADDER_SPLITS:
            c_lo = int(round(f * n))
            c_hi = n - c_lo
            gmap = {}
            if c_lo: gmap[lo] = c_lo
            if c_hi: gmap[hi] = c_hi
            out.append(dict(arm='ladder', rung='2^%d/2^%d' % (lo.bit_length()-1, hi.bit_length()-1),
                            N=N, n=n, q=q, logQ=log_q_big, q_ks=q_ks, base_ks=base_ks,
                            sigma=sigma, gadget_map=gmap, f=f,
                            pred_sigma=m.sigma_total_at_q(N, n, q, log_q_big, q_ks,
                                                          base_ks, sigma, gmap)))
    return out


def map_arg(gmap):
    return ",".join("%d:%d" % (b, c) for b, c in sorted(gmap.items()))


def _shares(c, k_acc):
    """Predicted variance split, so the design can be checked before it is run."""
    Q = 2.0 ** c['logQ']
    va = m.accumulator_var_at_Q({c['base_g']: c['n']}, c['logQ'], k_acc, c['N']) * (float(c['q']) / Q) ** 2
    vk = m.sigma_keyswitch(c['N'], c['q'], c['q_ks'], c['base_ks'], c['sigma']) ** 2
    vr = m.MODSWITCH_VAR_PER_COEFF * c['n']
    tot = va + vk + vr
    return va / tot, vk / tot, vr / tot, sqrt(tot)


def command(c, samples, keys):
    """The measurement command for one cell."""
    build = "build"   # the 64-bit build narrows to 32-bit key forms itself where they fit
    return ("%s/bin/boolean_noise_estimate_script -n %d -q %d -N %d -Q %d -k %d "
            "-g %d -b %d -r 64 -s %.2f -d 1 -t 2 -I 2 -i %d -K %d"
            % (build, c['n'], c['q'], c['N'], c['logQ'], c['q_ks'],
               c['base_g'], c['base_ks'], c['sigma'], samples, keys))


def show_plan(cells, samples, keys, emit):
    print("calibration grid: %d cells  (%d aligned, %d misaligned)"
          % (len(cells), sum(c['arm'] == 'aligned' for c in cells),
             sum(c['arm'] != 'aligned' for c in cells)))
    print("accumulator share is shown under BOTH candidate constants, because the")
    print("design must isolate the term whichever one turns out to be right.\n")
    print("  arm            base  d  logQ   n     acc%@k=133  acc%@k=5446  pred sigma")
    print("  " + "-" * 76)
    for c in sorted(cells, key=lambda c: (c['arm'], c['beta'], c['digits'], c['n'])):
        a_lo, _, _, _ = _shares(c, 133.0)
        a_hi, _, _, s_hi = _shares(c, m.K_ACC)
        print("  %-14s 2^%-3d %-2d %-6d %-5d %9.0f%% %11.0f%% %11.1f"
              % (c['arm'], c['beta'], c['digits'], c['logQ'], c['n'],
                 100 * a_lo, 100 * a_hi, s_hi))

    # the whole point of the grid: are base and digits actually decoupled?
    print("\nidentifiability check -- each digit count must appear at several bases:")
    for d in sorted({c['digits'] for c in cells if c['arm'] == 'aligned'}):
        bs = sorted({c['beta'] for c in cells if c['digits'] == d and c['arm'] == 'aligned'})
        print("  digits=%d  bases 2^%s" % (d, ", 2^".join(str(b) for b in bs)))
    for b in sorted({c['beta'] for c in cells if c['arm'] == 'aligned'}):
        ds = sorted({c['digits'] for c in cells if c['beta'] == b and c['arm'] == 'aligned'})
        print("  base=2^%-2d  digits %s" % (b, ", ".join(str(d) for d in ds)))

    if emit:
        print("\n# --- measurement commands (%d samples/key, %d keys) ---" % (samples, keys))
        for c in sorted(cells, key=lambda c: (c['arm'], c['beta'], c['digits'], c['n'])):
            print(command(c, samples, keys))


def ladder_command(c, samples, keys):
    """The measurement command for one ladder rung. -G carries the split."""
    build = "build"   # the 64-bit build narrows to 32-bit key forms itself where they fit
    return ("%s/bin/boolean_noise_estimate_script -n %d -q %d -N %d -Q %d -k %d "
            "-G %s -b %d -r 64 -s %.2f -d 1 -t 2 -I 2 -i %d -K %d -Z"
            % (build, c['n'], c['q'], c['N'], c['logQ'], c['q_ks'],
               map_arg(c['gadget_map']), c['base_ks'], c['sigma'], samples, keys))


def show_ladder(cells, samples, keys, emit):
    ceiling = m.saturated_sigma(cells[0]['q']) if cells else 0.0
    print("base ladder: %d cells over logQ %s   saturation ceiling sigma=%.1f"
          % (len(cells), sorted({c['logQ'] for c in cells}), ceiling))
    print("a cell whose PREDICTED sigma is already near the ceiling cannot be")
    print("measured -- it wraps, and reports the ceiling whatever n is.\n")
    print("  rung        logQ  n     split  gadget map                  d(lo) d(hi)  pred sigma  risk")
    print("  " + "-" * 92)
    for c in cells:
        lo, hi = min(c['gadget_map']), max(c['gadget_map'])
        risk = "SATURATES" if c['pred_sigma'] > 0.5 * ceiling else ""
        print("  %-11s %-5d %-5d %-6.2f %-27s %-5d %-6d %10.1f  %s"
              % (c['rung'], c['logQ'], c['n'], c['f'], map_arg(c['gadget_map']),
                 m.digits_for_base(c['logQ'], lo), m.digits_for_base(c['logQ'], hi),
                 c['pred_sigma'], risk))

    print("\npredicted per-coefficient variance ratios against logQ=27 (the measured ladder):")
    for lq in sorted({c['logQ'] for c in cells} - {27}):
        parts = []
        for b in sorted({b for c in cells for b in c['gadget_map']}):
            d27, dlq = m.digits_for_base(27, b), m.digits_for_base(lq, b)
            scale = (2.0 ** (27 - lq)) ** 2 * (dlq - 1) / float(d27 - 1)
            parts.append("2^%d: %.2f%s" % (b.bit_length() - 1, scale,
                                           "" if d27 == dlq else " (d %d->%d)" % (d27, dlq)))
        print("  logQ=%d   %s" % (lq, "   ".join(parts)))

    if emit:
        print("\n# --- ladder commands (%d samples/key, %d keys) ---" % (samples, keys))
        for c in cells:
            print(ladder_command(c, samples, keys))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_calibrate')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('plan', help='show the designed measurement set')
    p1.add_argument('--emit-commands', action='store_true')
    p1.add_argument('-i', '--samples', type=int, default=500)
    p1.add_argument('-K', '--keys', type=int, default=8)
    p3 = sub.add_parser('ladder', help='the gadget-split ladder that identifies the accumulator')
    p3.add_argument('--emit-commands', action='store_true')
    p3.add_argument('-Q', '--logQ', type=int, action='append',
                    help='repeatable; defaults to 27 (measured) plus 25 and 23')
    p3.add_argument('-i', '--samples', type=int, default=700)
    p3.add_argument('-K', '--keys', type=int, default=8)
    p3.add_argument('--all-rungs', action='store_true',
                    help='include the 2^9/2^11 and 2^11/2^13 rungs that saturated at logQ=27')
    p3.add_argument('--rung', action='append',
                    help='repeatable, e.g. 5/7 -- restrict to these base pairs')
    p3.add_argument('-n', '--dim-n', type=int,
                    help='override n for every selected rung. Use a MEASURED value: '
                         'sizing a rung with the model being calibrated is what let the '
                         'coarse rungs saturate at logQ=27')
    p2 = sub.add_parser('fit', help='fit constants from a completed calibration log')
    p2.add_argument('log')
    a = ap.parse_args()

    if a.cmd == 'plan':
        show_plan(plan(), a.samples, a.keys, a.emit_commands)
    elif a.cmd == 'ladder':
        rungs = LADDER_RUNGS_ALL if a.all_rungs else LADDER_RUNGS_DEFAULT
        if a.rung:
            want = {tuple(int(x) for x in r.split('/')) for r in a.rung}
            rungs = tuple(r for r in rungs
                          if (r[0].bit_length() - 1, r[1].bit_length() - 1) in want)
            if not rungs:
                sys.exit("no rung matches %s" % a.rung)
        if a.dim_n:
            rungs = tuple((lo, hi, a.dim_n) for lo, hi, _ in rungs)
        cells = []
        for lq in (a.logQ or [27, 25, 23]):
            cells += ladder(log_q_big=lq, rungs=rungs)
        show_ladder(cells, a.samples, a.keys, a.emit_commands)
    else:
        print("fit: run `plan --emit-commands` first and measure; not yet implemented")
        sys.exit(2)
