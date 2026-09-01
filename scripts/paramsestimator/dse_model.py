#!/usr/bin/python

'''Closed-form noise, cost and failure-probability models for the parameter search.

Pure functions with no OpenFHE and no SageMath dependency, so they can be
developed, unit-tested and calibrated without a container. Nothing here measures
anything; `dse_sweeplog` supplies measured records and `dse_validate` compares.

The point of these models is to let the search RANK candidates without running
OpenFHE on each one, and only then verify the few that survive. A model that is
wrong in a *correlated* way will silently prune good candidates, so every
constant below is either derived, or calibrated and carried with an error band --
none are guessed.

Conventions
-----------
A gadget map is {base: coefficient_count}, matching OpenFHE's
BinFHEContextParams::gadgetBaseMap. A single-base set is just {base: n}. Counts
must sum to n or OpenFHE rejects the set.

"@q" means a noise figure expressed at the LWE ciphertext modulus q, which is
where decryption sees it.
'''

from math import log2, sqrt, e

# scipy only for erfcx: erfc(x) underflows to 0 above x ~= 27, which reported an
# astronomically safe set as 2^0. erfcx(x) = exp(x^2)*erfc(x) does not underflow.
from scipy.special import erfcx


# --------------------------------------------------------------------------
# digit counts
# --------------------------------------------------------------------------

def digits_for_base(modulus_bits, base, modulus=None):
    """Digits OpenFHE will actually use for `base` at a modulus of `modulus_bits` bits.

    Exact integer ceil(log_base(x)) over the EXCLUSIVE bound x. Two identities are
    what let a bit COUNT stand in for the modulus itself, for a power-of-two base:

      - Q is LastPrime(numberBits, cyclOrder), so bitlen(Q-1) == numberBits.
        GUARANTEED, not assumed: LastPrime ends with
            if (qNew.GetMSB() != nBits) OPENFHE_THROW(...)
        so a prime of any other width is an exception, never a silent return.
        (Its doc comment in nbtheory.h says "at most nBits bits" -- that is
        stale; the implementation enforces exactly.) Q is an odd prime, so it
        cannot equal 2^(numberBits-1), hence Q-1 >= 2^(numberBits-1) and the bit
        length carries over to Q-1.
      - Qks is set to exactly 2^logQks, so bitlen(Qks-1) == logQks.

    Non-power-of-two bases need the modulus itself, not just its width, because
    powers of such a base do not line up with bit boundaries. Two shipped sets
    need this -- TOY and SIGNED_MOD_TEST both use baseKS = 25 -- and without it
    every model call on them raised, so the validator could not cover the sets it
    is explicitly supposed to cover. Pass `modulus` where it is known; absent it,
    2^ceil(modulus_bits) is used, which is an upper bound and therefore may
    overstate the count by one for a modulus sitting just above a power of `base`.

    Integer arithmetic on purpose, and as of the 0a1f7e95 pin OpenFHE agrees.
    `GetDigitCount` (nbtheory.h:208) is exact integer arithmetic with a
    power-of-two fast path, and it is now used at all three sites that need a
    digit count -- rgsw-cryptoparameters.h for the gadget, lwe-pke.cpp
    KeySwitchGen and KeySwitch for the keyswitch. **Zero `std::log` calls remain
    in binfhe.**

    An earlier revision of this docstring said no exact GetDigitCount existed.
    That was true of the OLD pin (252b2b1f) and false of upstream: it landed
    2026-08-28. Which is itself an argument for pinning -- a claim about "this
    tree" silently expires when the tree moves.

    Verified against the C++ exhaustively: power-of-two bases 2^1..2^20 over
    logQ 8..60, plus the non-power-of-two bases anything ships (baseKS=25 in TOY
    and SIGNED_MOD_TEST) -- exact agreement everywhere. `boolean_keyswitch_isolate`
    still prints both (`dks_lib` and `dks_exact`) on every run, so any future
    divergence shows up as data rather than a silent one-digit error in d_KS.
    """
    base = int(base)
    if base < 2:
        raise ValueError("digit base must be at least 2, got %r" % base)
    if (base & (base - 1)) == 0:
        return -(-int(modulus_bits) // (base.bit_length() - 1))

    mod = int(modulus) if modulus is not None else 2 ** int(-(-float(modulus_bits) // 1))
    d, v = 0, 1
    while v < mod:
        v *= base
        d += 1
    return d


def digits_g2(digits_g):
    """Rows the RGSW evaluation key is allocated at: 2*(digitsG - 1).

    The approximate decomposition drops the first digit and the allocation
    reflects it (rgsw-acc-cggi.cpp:82,85), so there are no dead rows. This is
    both the accumulator's work measure and the width of its OpenMP region.
    """
    return 2 * (digits_g - 1)


# --------------------------------------------------------------------------
# noise
# --------------------------------------------------------------------------

def sigma_keyswitch(N, q, q_ks, base_ks, sigma):
    """LWE key-switching noise, expressed at q.

        sigma_ks@q = sqrt(N * d_KS) * sigma_KS * (q / q_KS)

    MEASURED, by feeding KeySwitch an exactly noiseless dimension-N ciphertext at
    q_KS and reading the phase error -- no bootstrapping key, no accumulator, and
    no free constant in the prediction, so this is a test rather than a fit.
    96000 samples over 32 switching keys per set:

        STD128    pooled +0.66%    within-key +0.09%
        STD128_3  pooled +0.04%    within-key -0.86%
        STD128_4  pooled -0.00%    within-key +0.02%
        MEDIUM    pooled +0.37%    within-key +0.33%
        STD256    pooled -0.06%    within-key -0.14%

    All five inside 0.7%, against a 0.23% sampling floor. That is what justifies
    TERM_VAR_BAND['ks'] below; before this it was carried at +-10%, which cost 7.5
    bits of log2Pf at STD128 -- more than the accumulator's own band.

    The pooled and within-key figures differ on purpose, and the split is itself a
    prediction: see keyswitch_variance_split().

    This term is 61% of total noise variance at STD128 and 88% at STD192, but only
    ~1% at STD256 -- an asymmetry that is itself a search heuristic: for the
    128/192 families no gadget change matters until q_KS moves.
    """
    return sqrt(keyswitch_var_at_q(N, q, q_ks, base_ks, sigma))


def keyswitch_var_at_q(N, q, q_ks, base_ks, sigma, dropped_digits=0,
                       zero_dropped=None, compact=None):
    """Pooled key-switching variance at q, as the pinned library switches.

        var = N * sigma^2 * (q/q_KS)^2 * SUM over stored positions of f(extent)

    with f(r) = 1 - 1/r where the library keeps no row for digit value 0, and
    f(r) = 1 otherwise. A digit that is zero selects no row and so adds nothing,
    and a position of extent r is zero with probability 1/r: that is the whole of
    the change at 94229558, worth about 0.4 bits of log2Pf at b_KS 32 and 64 and
    under 0.1 at 256 and above.

    `dropped_digits` is the approximate decomposition's delta: positions below it
    are rounded away rather than switched, so they contribute no Gaussian here.
    What they contribute instead is `ks_round_var_at_q`, which is far larger --
    the two together are the whole cost of the knob.
    """
    if zero_dropped is None:
        zero_dropped = KSK_ZERO_ROWS_DROPPED
    d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    delta = int(dropped_digits or 0)
    ext = ks_digit_extents(q_ks, base_ks, d_ks, compact)[delta:]
    positions = sum((1.0 - 1.0 / r) if zero_dropped else 1.0 for r in ext)
    return N * (sigma ** 2) * positions * (float(q) / q_ks) ** 2


def ks_round_var_at_q(N, q, q_ks, base_ks, dropped_digits,
                      key_dist="UNIFORM_TERNARY"):
    """Rounding variance of an approximate key-switching decomposition, at q.

        var = N * (base_KS^(2*delta) - 1)/12 * Var(z) * (q/q_KS)^2

    With delta > 0 the key switch rounds each coefficient of the ring ciphertext
    to the nearest multiple of base_KS^delta before decomposing, and the discarded
    remainder r_i, uniform on [-base_KS^delta/2, base_KS^delta/2), stays in the
    phase multiplied by the ring secret coefficient z_i. Var(z) is the ring
    secret's per-coefficient variance, the same one the Q -> q_KS switch carries.

    The term is DERIVED, not fitted, and it is UNMEASURED: nothing in the harness
    isolates it, so a candidate that uses delta > 0 must be measured before it is
    picked (`dse_enumerate` marks such rows and the default grid holds delta at 0).

    Its size is why delta is a small-base knob. Against the positions it removes,
    at q_KS and Var(z) = 2/3, base 2^8 / d 2 costs about 180x what it saves,
    2^6 / d 3 about 20x, and 2^5 / d 4 about 1.4x.
    """
    delta = int(dropped_digits or 0)
    if delta <= 0:
        return 0.0
    if key_dist not in SECRET_VARIANCE:
        raise ValueError("no secret variance for keyDist %r" % key_dist)
    scale2 = float(base_ks) ** (2 * delta)
    return (N * (scale2 - 1.0) / 12.0 * SECRET_VARIANCE[key_dist]
            * (float(q) / q_ks) ** 2)


def keyswitch_variance_split(N, q, q_ks, base_ks, sigma):
    """(within-key, between-key) keyswitch variance at q. They sum to the total.

    Key switching draws its noise ONCE per switching key: KeySwitchGen stores
    baseKS rows per (coefficient, digit position), and each sample selects one of
    them by a digit of the ciphertext. So over samples a position contributes the
    empirical variance of its stored rows, and its empirical MEAN is a constant
    the key is stuck with.

    For a position whose digit takes r distinct values,

        within  = N * sigma^2 * (1 - 1/r)        between = N * sigma^2 * (1/r)

    and the two sum to N*sigma^2 exactly, which is why a pooled sigma over many
    keys matches the plain sqrt(N*d_KS)*sigma while a single key does not.

    This matters beyond bookkeeping: a DEPLOYMENT runs one key. It sees the
    between-key part as a fixed bias, not as noise -- so a per-key quantile
    certification and a pooled sigma are answering different questions, and the
    pooled one flatters the deployment.

    r is baseKS for every position except the TOP one, which a misaligned q_KS
    confines to ceil(q_KS / baseKS^(d_KS-1)) values. Measured at K=32 the two
    candidate models for the top digit disagreed in opposite directions on the two
    misaligned sets (STD128_3 favoured the confined form by +5% against uniform's
    +49%; STD128_4 favoured uniform), which 12% error bars cannot separate --
    hence the higher-K measurement. Until that lands the confined form is used,
    because it is the one the code implies.
    """
    d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    per  = N * (sigma ** 2) * (float(q) / q_ks) ** 2
    top_range = max(1.0, float(q_ks) / (float(base_ks) ** (d_ks - 1)))
    ranges = [float(base_ks)] * (d_ks - 1) + [min(float(base_ks), top_range)]
    if KSK_ZERO_ROWS_DROPPED:
        # A zero digit selects no row, so a position of extent r draws one of
        # r-1 stored rows with probability (r-1)/r and nothing otherwise: the
        # two parts become (r-1)^2/r^2 and (r-1)/r^2 of one Gaussian, and they
        # sum to the (1 - 1/r) this position now contributes.
        between = sum(per * (r - 1.0) / (r * r) for r in ranges)
        within  = sum(per * ((r - 1.0) ** 2) / (r * r) for r in ranges)
        return within, between
    between = sum(per / r for r in ranges)
    within  = sum(per * (1.0 - 1.0 / r) for r in ranges)
    return within, between


def accumulator_var_at_Q(gadget_map, log_q_big, k_acc, N, method="GINX", autokeys=10,
                         base_r=None, q=None):
    """Accumulator noise variance at the ring modulus Q.

        acc_var ~= k * N * SUM_j count_j * gadget_shape(logQ, baseG_j)

    Refit PER COEFFICIENT, and without an intercept. The published fit
    `29 + 0.0082*(digitsG-1)*baseG^2` cannot be used as written:

      - 0.0082 silently contains n. The accumulator does one external product per
        LWE coefficient, so variance is linear in coefficient count -- and n is a
        search dimension. Per-coefficient is also what generalises to a gadget
        map, hence the sum. Linearity in n is now measured, not assumed: at
        logQ=25, base 2^7, over n = 64..1024, sigma^2/n was flat at 0.9176 with a
        2.6% spread against a 2.5% sampling error, and the low-n and high-n halves
        gave 0.9178 and 0.9174.
      - The 29 intercept is NOT accumulator noise. It is everything else at that
        configuration, keyswitch included, because the fit was against total
        measured noise. Feeding it into a search that also models keyswitch
        explicitly double-counts the dominant term.

    N IS A REQUIRED ARGUMENT, and it used to be absent. Every external product is
    a ring operation over N coefficients, so the variance carries N -- but the
    ladder that calibrated k held N = 1024 throughout, so the omission was
    invisible and the constant silently meant "at N = 1024". Measured: holding
    everything else fixed and moving only N,

        base 2^7, n=256   N=512 -> 27.58   N=1024 -> 57.86   N=2048 -> 108.08
        base 2^9, n=64    N=512 -> 57.74   N=1024 -> 112.57

    which is N^0.985, i.e. linear to 1.5%. N is a primary search dimension, so
    without this the accumulator term is wrong by the N ratio -- a factor of 2 to
    4 across the range a search would explore. k_acc is correspondingly now
    per (N * coefficient); K_ACC below was divided by 1024 so that predictions at
    N = 1024 are unchanged.
    """
    total = 0.0
    for base, count in gadget_map.items():
        total += count * gadget_shape(log_q_big, base)
    if method == "LMKCDEY":
        # LMKCDEY is an automorphism-based blind rotation, and its noise has a
        # second part that does NOT scale with n: the automorphism keys' own
        # contribution, which falls as 1/numAutoKeys because that is the window
        # width. So the per-coefficient form alone cannot describe it, which is
        # why the GINX constant read +33% at the one shipped LMKCDEY set where
        # the accumulator carries weight.
        #
        #     acc_var = N * SUM_j count_j * shape_j  ->  replaced by
        #     acc_var = N * shape_per_coeff * (K_ACC_LMKCDEY_EXT * n
        #                                      + K_ACC_LMKCDEY_AUTO / autokeys)
        #
        # Measured over three arms at logQ=25, base 2^9, N=1024 -- a top-digit
        # scan, a numAutoKeys scan and an n scan:
        #
        #   numAutoKeys 2 / 5 / 10 / 20   var_acc 541.19 / 261.61 / 170.63 / 123.77
        #   n 32 / 64 / 128 (at w=10)     var_acc 170.63 / 242.57 / 397.92
        #
        # An n-linear-only fit cannot hold both: var_acc is affine in n with a
        # large intercept (C1 = 98.7, C2 = 2.25 at w=10), and that intercept goes
        # as 1/w (C1*w = 939 / 948 / 987 / 1038 across the four w). Two constants,
        # six points, +-4.5%.
        #
        # gadget_shape() itself carries over UNCHANGED, which it should: both
        # accumulators call the same SignedDigitDecompose. The top-digit scan gives
        # implied k of 5.019 / 5.019 / 4.874 / 4.755 -- the same +-2.7% flatness
        # GINX shows at 6.8.
        if autokeys < 1:
            raise ValueError("LMKCDEY needs numAutoKeys >= 1, got %r" % autokeys)
        per_coeff = total / sum(gadget_map.values())
        return N * per_coeff * (K_ACC_LMKCDEY_EXT * sum(gadget_map.values())
                                + K_ACC_LMKCDEY_AUTO / float(autokeys))
    if method == "AP":
        # AP does ap_products_per_coeff(q, baseR) external products per
        # coefficient instead of GINX's two, and that count is the only thing
        # baseR changes. Everything else -- gadget_shape, the N factor, linearity
        # in the coefficient count -- is the shared primitive.
        #
        # baseR and q are REQUIRED rather than defaulted: a default would silently
        # price a different refresh key from the one the candidate carries, and
        # baseR moves this term by 2.8x across the grid.
        if base_r is None or q is None:
            raise ValueError(
                "AP needs base_r and q: its external-product count is "
                "digitsR*(1-1/baseR) with digitsR = GetDigitCount(q, baseR), so "
                "neither can be assumed")
        return K_ACC_AP * N * ap_products_per_coeff(q, base_r) * total
    return k_acc * N * total


def gadget_shape(log_q_big, base):
    """The per-coefficient gadget factor: exactly the digit positions the code emits.

    `RingGSWAccumulator::SignedDigitDecompose` (rgsw-acc.cpp:55) loops

        for (d = 0; d < digitsG2; d += 2)
            shift = ((d >> 1) + 1) * gBits

    so the shift starts at gBits: it emits positions 1 .. digitsG-1 and position 0
    is dropped ("approximate gadget decomposition is used; the first digit is
    ignored"). Two consequences, and both are measured rather than assumed:

      - Positions 1 .. d-2 see a full window, variance b^2/12 each.
      - Position d-1 is the TOP one. The excess-H window is w = x + H with x
        centered in (-Q/2, Q/2], so w/b^(d-1) spans only Q/b^(d-1) values -- fewer
        than b whenever b^d > Q. Its variance is reduced in proportion.
      - Position 0 is dropped, and contributes NOTHING. Not an assumption: freeing
        its coefficient drives it to -0.01 of the others' (dse_accfit shape S8).

    So over-coverage is not a correction bolted on to the model, it IS the top
    digit's range -- which is why the aligned case (b^d == Q) is the one where the
    top digit behaves like every other, and why a single constant over (d-1)*b^2
    had to fail non-monotonically.

    Measured. Designed measurements over bases 2^5/2^7/2^9, d = 3..5, logQ 21..27,
    N 512..2048, n 32..1024, single- and multi-base maps -- seven independent
    cells, implied k:

        6.621  6.667  6.697  6.710  6.770  6.831  6.991      +-2.7%

    against the same cells under (d-1)*b^2 spanning 3.36 .. 6.99, i.e. +-35%. The
    sharpest single test holds base AND d fixed and moves only logQ, so only the
    top digit's range changes: at base 2^9, d=3, k*T rose x2.08 over logQ 24->27
    where (d-1)*b^2 predicts no change at all. This form predicts x1.97.

    Confirmed OUT OF SAMPLE on the 8h45m sweep, which it was not fitted on: of its
    352 records, the 84 with the accumulator above 20% of variance give median
    implied k of 6.87 (d=3), 6.83 (d=4) and 6.67 (d=5) -- flat across digit count,
    where (d-1)*b^2 gives 6.81 / 5.08 / 5.67.

    d = 2 needs no special treatment. It briefly looked as though it did -- 8 sweep
    records implied k ~ 0.46 against 6.8 -- but all 8 sit at 0.98 to 1.02 of the
    wrap ceiling, and an implied k computed from a sigma pinned at the ceiling is
    an artefact of the arithmetic. The 45 d=2 records below HALF the ceiling
    predict to min -4.1%, median -0.1%, max +3.8%.
    """
    d   = digits_for_base(log_q_big, base)
    b   = float(base)
    top = min(b, (2.0 ** log_q_big) / b ** (d - 1))
    return max(d - 2, 0) * b * b + top * top


# ---------------------------------------------------------------------------
# CALIBRATION STATUS -- read before trusting a predicted sigma
#
# Two of the three noise terms are validated against the 8h45m target-2^-64
# sweep (284 GINX / uniform-ternary candidates inside the domain below):
# keyswitch and modulus-switch rounding together predict measured sigma to a
# median of +0% with 267/284 inside +-10%. In 251 of those 284 the accumulator
# contributes under 5% of variance, so that is what those numbers test.
#
# The accumulator term is NOT identified, and cannot be from that sweep.
# At fixed Q the gadget base DETERMINES the digit count (d = ceil(logQ/log2 b)),
# so every accumulator-dominated record lies on a one-dimensional curve through
# the (b, d) plane and the two exponents are perfectly collinear. Regressing them
# returns b^-0.34 and (d-1)^-3.6 -- coarser base giving LESS noise, which is
# physically impossible and is the signature of that collinearity.
#
# The consequence is concrete, not theoretical. Fitting k on records with d >= 3
# gives 5446 and predicts them to within -15%..+7%; fitting on d == 2 gives 133.
# Neither works for the other: k=5446 overpredicts the d==2 records by +295% to
# +699%, k=133 underpredicts d >= 3 by up to 78%. A single (d-1)*b^2 shape cannot
# describe both, so the shape itself is wrong -- not merely its constant.
#
# K_ACC below is the d >= 3 fit because that is the regime the search operates in
# (the sweep's own winners all sit there). `accumulator_identified()` marks where
# it must not be trusted. Breaking the collinearity needs measurements that vary
# the base at FIXED digit count -- achievable by varying logQ at a fixed base, or
# by a multi-base map -- which is Phase 2's job.
# ---------------------------------------------------------------------------
# Per (N * gadget coefficient), for the gadget_shape() form. Seven designed cells
# give 6.62 .. 6.99 (+-2.7%); the sweep's 84 accumulator-dominated records give
# 6.87 / 6.83 / 6.67 at d = 3 / 4 / 5 out of sample. 6.8 is the joint value.
#
# It is NOT comparable to the 7330 this used to be: that was per coefficient with
# N absorbed and a different shape.
K_ACC = 6.8

# LMKCDEY's two constants, in the same per-(N * coefficient) units as K_ACC.
# _EXT is the per-coefficient external product -- 2.12 against GINX's 6.8, and
# GINX does two external products per coefficient (an indicator pair over
# {-1,0,+1}) where LMKCDEY does one, so a factor of 2-3 is the expected shape of
# that difference. _AUTO is the automorphism keys' contribution, which is
# independent of n and falls as 1/numAutoKeys.
#
# Fitted on six designed points, +-4.5%. Validated against the one shipped set
# where the accumulator carries real weight: STD128_LMKCDEY, accumulator 40% of
# variance, predicts -0.5% against a measured 10.2552 -- where the GINX constant
# read +33%.
K_ACC_LMKCDEY_EXT = 2.116
K_ACC_LMKCDEY_AUTO = 929.0

# AP, in the same per-(N * external product) units. AP's products come from
# ap_products_per_coeff(), so this constant multiplies
#
#     N * products_per_coeff * SUM_j count_j * gadget_shape(logQ, b_j)
#
# PROVENANCE: fitted on the designed baseR arm (2026-09-07, seven bases at fixed
# n, N, q, logQ and gadget base, plus n and gadget-base arms), and anchored
# independently by the one shipped AP set. STD128_AP has its accumulator at 61%
# of variance -- unusually identifiable, where most GINX sets sit under 5% -- and
# three independent 2400-sample runs of it (sigma 19.696 / 19.720 / 19.733) imply
# 1.706 / 1.713 / 1.716. That agreement to 0.6% is what the arm had to reproduce,
# and the arm is what says the SHAPE holds; the anchor alone could have been one
# constant absorbing a wrong count.
#
# Per external product the three methods run AP 1.71 < LMKCDEY 2.12 < GINX 3.40.
# They are not required to agree: the message each product carries differs (an
# indicator pair for GINX, a rotated secret digit for AP), so only the count is
# shared structure.
K_ACC_AP = 1.762

# Where that number comes from, and how far to trust it. Measured by the base
# ladder (dse_calibrate.ladder): hold Q, n, q_KS and base_KS fixed, move only the
# gadget split, so keyswitch and rounding cancel in differences.
#
#   base 2^5 (d=6, misaligned)  k = 7322
#   base 2^9 (d=3, ALIGNED)     k = 7334     <- agree to 0.2%
#   base 2^7 (d=4, misaligned)  k = 4985     <- 32% low, reproducible, unexplained
#
# for comparison: 5446 from the sweep's d>=3 records, 6710 from a 2^5/2^9 pilot.
# So K_ACC carries roughly a +-20% band, NOT the +-10% the other two terms have.
#
# The method itself is validated independently of the constant: base 2^7 measured
# in two different rungs, against different partners at different noise levels,
# agreed to 1.9%. Linearity in n also holds -- base 2^9 at n=1024 vs n=512 gave a
# sigma ratio of 1.501 against sqrt(2)=1.414.
#
# STILL OPEN: the two rungs bracket the model rather than agreeing with it (ratio
# measured 6.47 vs 9.60 predicted on one, 15.54 vs 10.67 on the other), so
# (d-1)*b^2 is missing structure that appears at d=4. Alignment does not explain
# it: misalignment runs 3 bits / 1 bit / 0 bits across 2^5 / 2^7 / 2^9 while k
# runs 7322 / 4985 / 7334.
#
# COARSE BASES ARE OUTSIDE THE MODEL ENTIRELY. At logQ=27 the ladder tried 2^11
# and 2^13 and both failed completely at every n attempted (256 and 16 alike,
# sigma pinned at the 147.8 ceiling). Where 2^11 was measurable (n=16, all-2^11,
# sigma 67.5) the implied per-coefficient contribution is ~318x that of 2^9,
# where (d-1)*b^2 predicts 16x -- so the form is short by a factor of ~20, and
# short in the DANGEROUS direction: it rates catastrophic configurations as good.
# `accumulator_identified()` gates on digit count, which happens to exclude these
# (2^11 and 2^13 both give d=3 at logQ=27... so it does NOT exclude them). Until
# the coarse-base behaviour is understood, candidates whose base exceeds roughly
# 2^9 at a 27-bit Q must be measured, never ranked on prediction.

# Two empirical bounds on where the accumulator term has been identified, both
# measured by the calibration ladder at logQ=27 and neither derived:
#
#   digits >= 3      d == 1 has no active digit; d == 2 has exactly one and is
#                    where the fitted shape disagrees by ~40x.
#   over-coverage    d*log2(b) - logQ, i.e. how many bits the decomposition
#     <= 3 bits      spans beyond Q. Measured: 2^5 (3 bits over) k=7322,
#                    2^7 (1 over) k=4985, 2^9 (0, aligned) k=7334 -- all usable;
#                    2^11 (6 over) implies k~146000 and 2^13 (12 over) saturates
#                    outright. A digits-only gate does NOT catch these, since
#                    2^11 and 2^13 both give d=3 at logQ=27.
# d = 2 WAS refused here, on the grounds that 8 sweep records implied k ~ 0.46
# against 6.8. That was wrong, and the error was mine rather than the model's:
# all 8 of those records sit at 0.98 to 1.02 of the wrap ceiling q/(p*sqrt(12)),
# i.e. pinned to it. Dividing a sigma that is stuck at the ceiling by a large
# predicted basis yields a small implied k mechanically, and says nothing about
# the physics.
#
# The 45 d=2 sweep records that sit below HALF the ceiling -- unambiguously
# unsaturated -- are predicted to min -4.1%, median -0.1%, max +3.8%. That is the
# same quality the model achieves everywhere else, so there is nothing special
# about d=2 and no reason to refuse it.
#
# What the episode actually shows is that the guard was in the wrong place: the
# real condition is SATURATION, not digit count, and it is a property of the
# measurement rather than of the candidate. See measurement_usable_for_fit().
ACC_IDENTIFIED_MIN_DIGITS = 2


# The (N, logQ) region the noise model has been measured over.
#
# This used to be a LIST of five exact (N, logQ) pairs, and it existed for one
# reason: at STD256's (N=2048, logQ=29) the model predicted sigma 7.97 against a
# measured 21.456 -- short by 2.7x in sigma and 7.2x in variance -- and the other
# two guards passed it. A pair list was the only thing that caught it.
#
# THAT DEFECT IS FIXED. It was the accumulator's shape, not the (N, logQ) pair:
# the term was missing its N factor and used (d-1)*base^2 where the decomposition
# emits positions 1..d-1 with the top one confined. With gadget_shape() and the
# measured N factor, STD256 predicts -4.4% and STD192 -- which the old model
# overpredicted by 91% -- predicts +1.9%.
#
# So the pair list has become a source of FALSE refusals: it rejects shipped sets
# the model now handles to a few percent. Replaced by the honest statement, which
# is a range rather than a list, because the shape is now derived and the constant
# is flat across everything measured:
#
#   N     512 .. 2048     measured, and the N dependence is linear (N^0.985)
#   logQ   21 .. 54       measured; the sweep covers 25/27/50/54, tonight's
#                         designed runs add 21..27, and shipped-set validation
#                         adds 26/28/29/36/37/39
#
# Outside those ranges it still refuses, because nothing has been measured there.
CALIBRATED_N_RANGE = (512, 2048)
CALIBRATED_LOGQ_RANGE = (21, 54)

# Kept so the older, tighter guard can still be applied deliberately, and so the
# five pairs the original calibration actually used stay on the record.
CALIBRATED_N_LOGQ = {(512, 27), (1024, 25), (1024, 27), (2048, 50), (2048, 54)}
CALIBRATED_LOGQ_TOLERANCE = 2


# One doubling past the measured range, and no further. The noise model's error
# is FLAT in N over the range it was measured on -- across the 105 cells of the
# 94229558 table the predicted pooled sigma runs 1.004 of measured at N=1024 and
# 1.005 at N=2048 -- so the N dependence carrying one more doubling is an
# argument from measured shape (N^0.985) plus a residual that does not drift.
# It is still an extrapolation, so it is opt-in per call, every candidate that
# uses it is marked, and the measurement decides as it does everywhere else.
N_EXTRAPOLATION_LIMIT = 2 * CALIBRATED_N_RANGE[1]


def within_calibrated_envelope(N, log_q_big, strict=False, extrapolate_n=None):
    """False outside the (N, logQ) region the noise model has been measured over.

    `extrapolate_n=K` additionally admits ring dimension K above the measured
    range, up to N_EXTRAPOLATION_LIMIT. Nothing else widens: logQ still has to be
    inside its measured range, and a caller that does not ask gets the refusal.

    `strict=True` applies the original five-pair list instead. That list was a
    workaround for the accumulator shape defect described above; it is retained
    only so a caller can reproduce the older, far more restrictive behaviour.
    """
    if strict:
        return any(N == n and abs(int(log_q_big) - q) <= CALIBRATED_LOGQ_TOLERANCE
                   for n, q in CALIBRATED_N_LOGQ)
    if not (CALIBRATED_LOGQ_RANGE[0] <= int(log_q_big) <= CALIBRATED_LOGQ_RANGE[1]):
        return False
    if CALIBRATED_N_RANGE[0] <= N <= CALIBRATED_N_RANGE[1]:
        return True
    return (extrapolate_n is not None and N == int(extrapolate_n)
            and CALIBRATED_N_RANGE[1] < N <= N_EXTRAPOLATION_LIMIT)


def n_is_extrapolated(N):
    """True where this ring dimension is outside what the noise model measured."""
    return not (CALIBRATED_N_RANGE[0] <= N <= CALIBRATED_N_RANGE[1])


# The accumulator term is derived from and measured on GINX/CGGI only. LMKCDEY is
# a different blind rotation -- automorphism-based, with numAutoKeys automorphism
# keys contributing noise of their own -- so the per-LWE-coefficient form has no
# reason to transfer, and it does not.
#
# Measured. Of the three shipped LMKCDEY sets, two have the accumulator at 0% and
# 9% of variance and agree with the model to 1.05% and 0.14% -- but that is the
# other terms being right, not this one. STD128_LMKCDEY has it at 66%, and there
# the model reads 13.65 against a measured 10.26: +33%.
#
# Over-prediction, so the direction is conservative, but a search must not RANK
# LMKCDEY candidates on it.
# AP was refused until 2026-09-07, on two grounds, and both have been answered
# rather than waived. The grounds were: no AP noise measurement beyond a
# method-compatibility probe, and a dimension the model did not represent --
# baseRK, which indexes AP's refresh key and therefore drives its noise, its key
# size and its gate time, none of which the formulas could see.
#
# WHAT MAKES IT MODELLABLE is that baseRK enters through exactly one quantity,
# read off the schedule rather than fitted. `DMAccSchedule`
# (rgsw-acc-common.h:437) walks each LWE coefficient's base-baseR digits and
# SKIPS a zero digit:
#
#     for k in 0 .. digitsR-1:  a0 = (a_i / baseR^k) mod baseR
#                               if a0: external product with ek[i][a0][k]
#
# so the number of external products per coefficient is not digitsR but
#
#     ap_products_per_coeff(q, baseR) = digitsR * (1 - 1/baseR)
#
# with digitsR = GetDigitCount(q, baseR) -- the digit count of the LWE modulus q,
# not of Q (rgsw-cryptoparameters.cpp:54). One factor drives noise AND time,
# because a skipped digit costs neither. Each product is the same
# `AddToAccNoMonomial` primitive GINX and LMKCDEY use, so gadget_shape() and the
# linearity in N and n carry over untouched; only the count changes and only one
# constant is fitted.
#
# WHAT THE MEASUREMENT CHANGED. The first version of this count assumed every
# digit position is uniform over baseR values, giving digitsR*(1 - 1/baseR), and
# predicted a non-monotonicity: baseR 32 worse than 16, 128 no better than 64.
# The designed arm refuted it. The top position is TRUNCATED -- a < q leaves it
# fewer than baseR values -- and once that is counted exactly the product count
# is monotone decreasing in baseR:
#
#     baseR     2      4      8      16     32     64     128
#     digitsR   11     6      4      3      3      2      2
#     products  5.500  4.250  3.375  2.750  2.438  1.953  1.930
#
# Measured against the approximation, the exact count moves the baseR 2:4
# variance ratio from 1.222 to 1.294 where the measurement says 1.290, and the
# 64:128 ratio from 0.992 to 1.012 where the measurement says 1.012. The implied
# constant tightens from +-2.8% to +-0.79% across the sweep.
#
# So for AP a coarser refresh base is better on BOTH noise and gate time, and the
# only thing it costs is key material -- (baseR - 1) * digitsR refresh keys per
# coefficient. That is a clean two-axis trade for the frontier to resolve, and no
# base is dominated. The 2-vs-4 ratio remains the test that separates a count of
# work done (1.29) from a count of digit positions (11/6 = 1.83).
ACCUMULATOR_METHODS = ("GINX", "LMKCDEY", "AP")


def ap_products_per_gate(n, q, base_r):
    """External products in a whole gate: n * ap_products_per_coeff.

    A feature in its own right for AP, and the reason is structural. Every
    external product carries a FIXED cost besides its digitsG2 row-operations --
    the accumulator decomposition, the loop, the schedule's own division. For
    GINX and LMKCDEY the product count per coefficient is a constant, so that
    fixed cost is proportional to n and c1 absorbs it invisibly. AP is the first
    method where the count varies independently of the gadget (baseR moves it 2.8x
    at fixed digitsG2), so the two separate and the cost model needs both.

    Measured: fitting AP on (1, n, gate_work) alone leaves a residual that runs
    monotonically from -45% at two gadget digits to +20% at twenty-seven -- the
    signature of a work feature that grows too fast with the digit count because
    it is carrying a term that does not.
    """
    return int(n) * ap_products_per_coeff(q, base_r)


def ap_products_per_coeff(q, base_r):
    """External products per LWE coefficient for AP, in expectation. EXACT.

        SUM over digit positions k of  P(digit_k != 0),   a uniform on [0, q)

    A zero digit is skipped outright (DMAccSchedule), so this counts work done
    rather than digit positions. It is the ONLY route by which baseR enters the
    model, and it enters noise and gate time identically.

    THE TOP POSITION IS TRUNCATED, and getting that wrong is measurable. The
    approximation digitsR*(1 - 1/baseR) assumes every position is uniform over
    baseR values, but a < q means the highest position spans only
    ceil(q/baseR^(digitsR-1)) of them. At q = 2048, baseR = 4 the top digit is 0
    or 1, so it is nonzero half the time and not three quarters, and the count is
    4.25 rather than 4.50.
    
    Measured on the designed baseR arm (2026-09-07, five bases, 10000 gates each,
    the accumulator at 88-95% of variance), the difference is not academic:

        baseR 2 : 4    variance ratio measured 1.290, exact 1.294, approx 1.222
        baseR 64 : 128 variance ratio measured 1.012, exact 1.012, approx 0.992

    and the implied constant tightens from +-2.8% to +-0.79% across the sweep.
    The second row also flips a conclusion: exactly counted, baseR 128 does FEWER
    products than 64, so it is better on noise and time at twice the key material
    -- a real trade, where the approximation made it strictly dominated.

    Exact for any integer base and modulus, not just powers of two: within each
    full cycle of baseR^(k+1) exactly baseR^k values have digit k zero, and the
    partial cycle at the top contributes min(remainder, baseR^k). TOY ships
    baseRK = 23, so the general case is reachable.
    """
    q = int(q)
    base_r = int(base_r)
    if base_r < 2:
        raise ValueError("AP needs baseR >= 2, got %r" % base_r)
    digits_r = digits_for_base(q.bit_length() - 1, base_r, modulus=q)
    total = 0.0
    for k in range(digits_r):
        block = base_r ** k
        cycle = block * base_r
        zeros = (q // cycle) * block + min(q % cycle, block)
        total += 1.0 - float(zeros) / q
    return total


def accumulator_identified(gadget_map, log_q_big, method="GINX"):
    """False when a predicted sigma would rest on the unidentified part of the model.

    The enumerator must not RANK on a prediction this rejects. Verifying such a
    candidate by measurement is fine; that is how the term gets identified.

    THE FAILURE DIRECTION HAS REVERSED. The old shape UNDERstated noise at coarse
    bases by ~20x, rating catastrophic configurations as good -- the one direction
    a prune cannot afford. With gadget_shape() there is no record the model gets
    wrong by more than 4.6% once ceiling-compressed MEASUREMENTS are excluded
    (see measurement_usable_for_fit), so this guard no longer excludes anything on
    the sweep. It is kept as a floor -- d = 1 has no emitted digit at all -- rather
    than as a statement that some digit count is untrustworthy.
    """
    if method not in ACCUMULATOR_METHODS:
        return False
    for b in gadget_map:
        d = digits_for_base(log_q_big, b)
        if d < ACC_IDENTIFIED_MIN_DIGITS:
            return False
    return True

# Rounding variance per coefficient in the final q_KS -> q modulus switch.
#
# DERIVED, not fitted. ModSwitch applies RoundqQ to every element of a and to b
# (lwe-pke.cpp:241-248), so writing a'_i = a_i*q/Q + d_i with d_i the rounding
# error, the phase after switching is
#
#     b' - <a',s>  =  (q/Q)*(b - <a,s>)  +  d_b - SUM_i d_i * s_i
#
# giving added variance 1/12 + n*Var(s)/12. The coefficient is Var(s)/12 -- a
# property of the SECRET DISTRIBUTION, not a free parameter.
#
#   UNIFORM_TERNARY   Var(s) = 2/3      -> 1/18   = 0.055556
#   GAUSSIAN          Var(s) = 3.19^2   -> 0.8480              15.3x LARGER
#
# The 15x swing is across a dimension this search proposes to explore, and two
# shipped sets already use GAUSSIAN (STD192_LMKCDEY, STD192Q_LMKCDEY). The free
# fit returned 0.055380 against the ternary derivation's 0.055556 -- but the sweep
# contains ONLY ternary records, so that agreement says nothing about the Gaussian
# branch. It is derived here rather than fitted for exactly that reason.
#
# Grid correction: RoundqQ is floor(0.5 + x), round-half-up, and where q_KS and q
# are both powers of two M = q_KS/q is an exact integer, so the error lives on an
# M-point grid rather than a continuum: mean +1/(2M), variance (1 - 1/M^2)/12.
# Measured effect at the M values the sweep visited (minimum M = 8) is under 0.4%
# on the coefficient and undetectable in the residual -- but at M = 2 it is 25%,
# and q_KS is a search dimension. Applied by derivation so it stays correct there.
SECRET_VARIANCE = {"UNIFORM_TERNARY": 2.0/3.0, "GAUSSIAN": 3.19**2}


def modswitch_var_per_coeff(q_ks, q, key_dist="UNIFORM_TERNARY"):
    """Var(s)/12, with the finite-grid correction (1 - 1/M^2), M = q_KS/q."""
    if key_dist not in SECRET_VARIANCE:
        raise ValueError("no secret variance for keyDist %r" % key_dist)
    base = SECRET_VARIANCE[key_dist] / 12.0
    M = float(q_ks) / q
    return base * (1.0 - 1.0 / (M * M)) if M >= 1.0 else base


# Kept for the ternary/large-M case the sweep calibrated, so existing callers and
# the validation numbers in dse_validate stay reproducible.
MODSWITCH_VAR_PER_COEFF = 0.05538

# The Q -> q_KS switch. Fitting this returned ~2e-7 and it was read as "genuinely
# zero" -- but it is not zero, it is scaled by (q/q_KS)^2, about 1/256 at STD128.
# Its coefficient is the same Var(s_N)/12 over the RING secret, so pin it by
# derivation rather than fit: a weakly-identified small term fitted to zero bakes
# in the assumption that q/q_KS stays small, and q_KS is a dimension the search
# exists to move.
def modswitch_var_first_switch(N, q, q_ks, key_dist="UNIFORM_TERNARY"):
    """Rounding variance of the Q -> q_KS switch, expressed at q."""
    return (SECRET_VARIANCE[key_dist] / 12.0) * N * (float(q) / q_ks) ** 2


# The rounding is BIASED, and this is a second bias source independent of gadget
# alignment. Round-half-up on an M-point grid gives mean +1/(2M) per element, so
# the phase gains (1/(2M))*(1 - SUM_i s_i). With SUM_i s_i ~ +-sqrt(2n/3) that is
# about +-0.6 at STD128, coherent across both gate inputs, so +-1.2 against a
# threshold of 256 -- roughly a bit of log2Pf.
#
# IT IS CAPTURED AUTOMATICALLY IF b_k IS MEASURED PER KEY. It is NOT captured if
# b_k is derived from the alignment test, because it has nothing to do with
# alignment. That is the argument for measuring the offset rather than inferring it.
def modswitch_bias_scale(q_ks, q):
    """The +1/(2M) per-element rounding bias; multiply by (1 - SUM_i s_i)."""
    M = float(q_ks) / q
    return 1.0 / (2.0 * M) if M >= 1.0 else 0.0


# The model assumes noise is small against the modulus carrying it. Once the
# key-switching key's own noise approaches q_KS the ciphertext saturates, the
# phase error wraps, and the model overpredicts without bound -- by up to 38x in
# the sweep. Measured boundary: every record the model got impossibly wrong had
# sqrt(N*d_KS)*sigma / q_KS >= 0.0153, and none below 0.015 was wrong that way.
# The populations DO overlap above that (good records reach 0.043), so this is a
# conservative gate, not a separator.
KS_SATURATION_LIMIT = 0.015


def within_model_domain(N, q_ks, base_ks, sigma):
    """False where the closed-form noise model is not to be trusted.

    The enumerator must apply this: a search that proposes candidates outside it
    is ranking on numbers the model cannot produce, and will do so silently.
    """
    d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    return (sqrt(N * d_ks) * sigma / q_ks) < KS_SATURATION_LIMIT


def sigma_total_at_q(N, n, q, log_q_big, q_ks, base_ks, sigma, gadget_map,
                     k_acc=K_ACC, ms_coeff=None, key_dist="UNIFORM_TERNARY",
                     method="GINX", autokeys=10, base_r=None,
                     dropped_digits_ks=0):
    """Predicted total noise stddev at q.

    Four contributions, each expressed at q:

      - the accumulator adds its noise at Q, and the chain Q -> q_KS -> q scales
        it by (q/Q);
      - key switching adds its noise at q_KS, scaled by (q/q_KS);
      - the final q_KS -> q modulus switch adds rounding error at q, which does
        NOT scale down and therefore dominates wherever the other two have been
        scaled into insignificance. Leaving it out underpredicted the sweep by a
        median of 82%; adding it moved the median to +2%;
      - the first Q -> q_KS switch adds rounding error at q_KS, scaled by
        (q/q_KS)^2 to about 0.1% of variance. Included because q_KS is a search
        dimension: fitting this term returned ~2e-7 and reading that as "zero"
        bakes in the assumption that q/q_KS stays small.

    ms_coeff defaults to the DERIVED per-coefficient rounding variance for this
    candidate, Var(s)/12 * (1 - 1/M^2), not to a constant fitted over the sweep.
    Isolated measurement (dse_isolate, 800k samples per point) puts the derived
    value within 0.5% at every shipped set, across n = 422..1299 and M = 8..128,
    for BOTH key distributions -- so there is nothing left for a fitted constant
    to improve on, and the fitted one was wrong by 15x on the Gaussian sets,
    where this term is about a third of total variance. Pass ms_coeff explicitly
    only to reproduce an older number.
    """
    Q = 2.0 ** log_q_big
    if ms_coeff is None:
        ms_coeff = modswitch_var_per_coeff(q_ks, q, key_dist)
    var_acc   = accumulator_var_at_Q(gadget_map, log_q_big, k_acc, N,
                                     method=method, autokeys=autokeys,
                                     base_r=base_r, q=q) * (float(q) / Q) ** 2
    var_ks    = keyswitch_var_at_q(N, q, q_ks, base_ks, sigma,
                                   dropped_digits=dropped_digits_ks)
    var_ksr   = ks_round_var_at_q(N, q, q_ks, base_ks, dropped_digits_ks, key_dist)
    var_round = ms_coeff * n
    var_first = modswitch_var_first_switch(N, q, q_ks, key_dist)
    return sqrt(var_acc + var_ks + var_ksr + var_round + var_first)


# --------------------------------------------------------------------------
# alignment bias -- a term a variance-only model cannot see
# --------------------------------------------------------------------------

def keyswitch_aligned(base_ks, q_ks):
    """True when base_KS^d_KS == q_KS exactly, so no digit position is short.

    A digit whose range the modulus does not fill re-uses ONE fixed key row on
    every operation, contributing a per-key OFFSET rather than N further
    Gaussians. Every power-of-two-aligned pair is clean, so the shipped STD128
    keyswitch (32^3 == 2^15) carries no bias.
    """
    d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    return base_ks ** d_ks == int(q_ks)


def gadget_aligned(base_g, log_q_big):
    """True when baseG^digitsG == 2^logQ, i.e. the top gadget digit is full.

    This is where the bias bites a set that ships: at 25-bit Q, base 16 needs
    d=7 and 16^7 = 2^28, eight times over Q, so the top digit spans an eighth of
    its range. That is LPF_STD128Q's primary base.
    """
    d_g = digits_for_base(log_q_big, base_g)
    return (base_g ** d_g) == (1 << int(log_q_big))


def bias_amplification(inputs):
    """Both gate inputs come from the same switching key, so offsets add
    COHERENTLY: inputs*b, not b*sqrt(inputs). That distinction is the whole
    reason the bias must be modelled as an offset rather than folded into sigma.
    """
    return inputs


# --------------------------------------------------------------------------
# failure probability -- per key, then a quantile
# --------------------------------------------------------------------------

def log2_pf(sigma_k, inputs, q, bias=0.0):
    """log2 failure probability for ONE key, given that key's realised offset.

        x = (thresh - inputs*b) / (sqrt(2*inputs) * sigma)
        log2Pf = log2(erfcx(x)) - x^2*log2(e)

    thresh = q/(2p) with p = 2*inputs, the distance to the decision boundary.
    """
    p = 2 * inputs
    thresh = float(q) / (2 * p)
    x = (thresh - bias_amplification(inputs) * bias) / (sqrt(2.0 * inputs) * sigma_k)
    if x <= 0:
        return 0.0                       # noise already past the boundary
    return log2(erfcx(x)) - x * x * log2(e)


def saturated_sigma(q, inputs=2):
    """The sigma a FAILED measurement reports, in closed form.

    Once noise swamps the modulus the decryption error is uniform over one
    plaintext cell of width q/p, whose standard deviation is q/(p*sqrt(12)). The
    measurement then carries no information about the parameters and must not be
    fitted. For q=2048, p=4 this is 147.8 -- and the calibration ladder measured
    146.8 to 148.5 across every failed cell, at n=256 and n=16 alike.

    Independence from n is what proves it is a ceiling rather than noise: real
    noise scales as sqrt(n), so a 16x change in n would have moved sigma 4x.
    """
    p_mod = 2 * inputs
    return float(q) / (p_mod * sqrt(12.0))


def measurement_saturated(sigma_measured, q, inputs=2, tol=0.10):
    """True when a measured sigma is AT the failure ceiling and carries no information."""
    return sigma_measured >= (1.0 - tol) * saturated_sigma(q, inputs)


# Compression starts long before the ceiling is reached, so a measurement can be
# distorted without being "saturated". Measured: at base 2^14, n=64, two logQ
# values whose predicted sigma is IDENTICAL by construction (for d=2 the shape
# reduces to (Q/b)^2 and (Q/b)^2*(q/Q)^2 = (q/b)^2, independent of Q) read 105.87
# and 84.29 -- a 26% spread -- while sitting at 0.72 and 0.57 of the ceiling.
# Below half the ceiling the same comparison is clean.
#
# So: 0.9 for "this number is the ceiling", 0.5 for "this number may be fitted".
MEASUREMENT_FIT_LIMIT = 0.5


def measurement_usable_for_fit(sigma_measured, q, inputs=2):
    """False where ceiling compression may have distorted a measurement.

    Stricter than measurement_saturated() on purpose. This is the one to apply
    before FITTING or before scoring a model against a record; the other is for
    deciding whether a single measurement means anything at all.
    """
    return sigma_measured < MEASUREMENT_FIT_LIMIT * saturated_sigma(q, inputs)


def certify_quantile(log2pf_per_key, quantile=0.9):
    """Certify on a QUANTILE of the per-key failure probabilities, not on pooled sigma.

    A deployment runs ONE key, not the mean of eight. Pooling samples across keys
    gives the mean log2Pf; a 2^-128 claim needs the tail of the per-key
    distribution. `quantile` is the fraction of keys that must be at least this
    good, so it returns the WORST log2Pf among the best `quantile` of keys.
    """
    if not log2pf_per_key:
        raise ValueError("no per-key failure probabilities to certify from")
    ordered = sorted(log2pf_per_key)            # most negative (best) first
    idx = min(len(ordered) - 1, max(0, int(round(quantile * (len(ordered) - 1)))))
    return ordered[idx]


# Per-term relative uncertainty on VARIANCE, from calibration. These do not
# collapse to a single scalar "model error band" -- the plan asks for one number
# and calibration produced three, whose consequence depends on how the candidate's
# variance is divided between them.
# Fractional 1-sigma band on each VARIANCE term. Two of the three are now set by
# isolated measurement against a prediction with no free constant, rather than by
# how well a fit happened to land.
#
#   ks     five sets, 96000 samples over 32 switching keys each, pooled sigma
#          within 0.7% and within-key sigma within 0.33% of the derived formula.
#          2% on the variance is 3x the worst of those, kept as headroom for the
#          per-key offset structure that K=32 could not resolve. Was 0.10.
#   round  twelve sets x 800000 samples, n = 422..1299, M = 8..128, BOTH key
#          distributions: the final switch within 0.5% and the first switch within
#          0.52%, both against Var(s)/12 with the finite-grid correction. 2% on
#          the variance again. Was 0.10 -- and the constant it was carried with was
#          wrong by 15x on the Gaussian sets.
#   acc    Was 20%, on a shape that was wrong. With gadget_shape() the seven
#          designed cells agree on k to +-2.7% and the sweep agrees out of sample
#          to 1.5% in the median -- but individual sweep records scatter over
#          [6.16, 7.77] at 10-90%, which is per-record measurement noise at 200
#          samples x 8 keys rather than model error. 6% carries the systematic
#          part with room for the rest.
TERM_VAR_BAND = {
    'ks':    0.02,
    'round': 0.02,
    'acc':   0.06,
    # The approximate decomposition's rounding term is derived the same way the
    # modulus-switch rounding is, and carries the same band -- but unlike those
    # it has never been measured, which is a separate matter from its width and
    # is why a delta > 0 candidate is held back from selection rather than
    # merely banded (dse_enumerate.evaluate).
    'ks_round': 0.02,
}


def log2pf_band(log2pf, var_shares, keys=None, samples_per_key=None,
                term_bands=None):
    """Uncertainty in log2Pf for ONE candidate, propagated through its variance shares.

        |dlog2Pf| ~= 2 * |log2Pf| * dsigma/sigma
        dsigma/sigma ~= sqrt( SUM_i (0.5 * share_i * band_i)^2  +  sampling^2 )

    Why this cannot be a scalar: the same +-20% on the accumulator term is
    negligible or decisive depending on its share.

        accumulator at   5% of variance -> +-0.5% sigma -> ~ +-1.3 bits at -128
        accumulator at 100% of variance -> +-10%  sigma -> ~ +-26  bits at -128

    26 bits is larger than most decisions a frontier makes and 1.3 is noise, so a
    single band either wastes verification on safe candidates or accepts unsafe
    ones. Feeding shares in also tells stage 5 where to spend its budget: measure
    where the propagated band is WIDE, not merely where log2Pf sits near target.

    `var_shares` is {term: fraction of predicted variance}, summing to ~1.
    """
    bands = term_bands or TERM_VAR_BAND
    acc = 0.0
    for term, share in var_shares.items():
        b = bands.get(term)
        if b is None:
            raise ValueError("no calibrated band for variance term %r" % term)
        acc += (0.5 * share * b) ** 2
    if (keys is not None) and (samples_per_key is not None):
        acc += sigma_relative_uncertainty(keys, samples_per_key) ** 2
    return 2.0 * abs(log2pf) * sqrt(acc)


def variance_shares(N, n, q, log_q_big, q_ks, base_ks, sigma, gadget_map,
                    k_acc=None, key_dist="UNIFORM_TERNARY",
                    method="GINX", autokeys=10, base_r=None,
                    dropped_digits_ks=0):
    """How a candidate's predicted variance divides between the three terms."""
    k_acc = K_ACC if k_acc is None else k_acc
    Q = 2.0 ** log_q_big
    v = {
        'acc':   accumulator_var_at_Q(gadget_map, log_q_big, k_acc, N,
                                    method=method, autokeys=autokeys,
                                    base_r=base_r, q=q) * (float(q) / Q) ** 2,
        'ks':    keyswitch_var_at_q(N, q, q_ks, base_ks, sigma,
                                     dropped_digits=dropped_digits_ks),
        'round': modswitch_var_per_coeff(q_ks, q, key_dist) * n,
    }
    # Only present when the approximate decomposition is in use, so a candidate
    # at delta = 0 carries exactly the three shares it always did.
    if dropped_digits_ks:
        v['ks_round'] = ks_round_var_at_q(N, q, q_ks, base_ks,
                                          dropped_digits_ks, key_dist)
    tot = sum(v.values())
    return {k: val / tot for k, val in v.items()}


# Per-key sigma scatter -- MEASURED 2026-09-01, was assumed at 2.00%.
#
# Twelve candidate-runs over two independent verification runs (156 dof),
# deconvolved in SIGMA space, where the sampling error of a stddev is exactly
# 1/sqrt(2S) and so needs no linearization:
#
#     observed_CV^2 = true_CV^2 + 1/(2S)
#
# Pooled point estimate 0.89%, 1-sigma upper bound 1.38%. The assumed 2.00% is
# excluded. Per-candidate structure is NOT resolvable from this data: each
# candidate-run carries about 1 sigma, the two runs of the same candidate agree
# to 0.1-1.1 sigma, and chi2 against one shared value is 15.4 on 11 dof. A
# scatter derived from the variance shares and the count of key-derived noise
# contributions predicts a pooled 0.74% -- consistent, but the per-candidate
# ratios run 0.23 to 7.28, so the agreement is not evidence for the structure.
# Hence ONE constant, not a formula.
#
# Two names because the two uses want OPPOSITE conservatism:
#   - widening a decision band: overstating is safe (it withholds a PASS)
#     -> KEY_SCATTER_BOUND
#   - sizing a run to resolve the scatter: overstating is UNSAFE, because it
#     asks for fewer keys than the true scatter needs
#     -> KEY_SCATTER_POINT
KEY_SCATTER_BOUND = 0.0138
KEY_SCATTER_POINT = 0.0089


# Effective per-key scatter IN LOG2PF TERMS -- a DIFFERENT quantity from
# KEY_SCATTER_BOUND above, and the reason the two constants are separate.
#
# KEY_SCATTER_BOUND is the scatter of SIGMA across keys, 1.38%. But `certify`
# does not work on sigma: it works on per-key log2Pf, which also carries each
# key's realised OFFSET, amplified by `inputs` because both gate inputs come
# through the same switching key. Measured over the 97 certified cells of the
# 2026-09-08 parameter table (8 keys x 1250 gates each), the deconvolved per-key
# log2Pf spread expressed as a fraction of the sensitivity runs:
#
#     arity 2   median 0.0138 (at the floor, unresolved)  p90 0.0287  max 0.0368
#     arity 3   median 0.0138                             p90 0.0250  max 0.0464
#     arity 4   median 0.0138                             p90 0.0254  max 0.0299
#
# Recompute those spreads with the per-key bias forced to zero and the median
# effective scatter falls to 0.0000 -- i.e. essentially ALL of the resolvable
# per-key spread is the offset, not sigma variation. That is why the sigma-space
# constant is the wrong one to budget with, and why the seven short cells of that
# table were all at arity 3 or 4, where the offset is amplified 3x and 4x.
#
# The value is the p90 of that measured distribution. It also covers every one of
# the seven shortfalls observed: the largest penalty any of them needed was 7.14
# bits, which is scatter 0.0202.
#
# WHY THE P90 AND NOT THE MEDIAN. This constant is used to SELECT, where the two
# errors are not symmetric: over-budgeting picks a slower set that certifies,
# under-budgeting picks a faster set that fails measurement and costs the whole
# measurement to find out. The same asymmetry argument, in the opposite
# direction, is why KEY_SCATTER_BOUND and KEY_SCATTER_POINT are two names.
#
# NOT used by `certify`, deliberately. There the observed spread usually replaces
# any assumption, and the floor's job is to be the honest minimum rather than a
# conservative budget. Raising certify's floor to this would re-decide every
# verdict already recorded.
PER_KEY_LOG2PF_SCATTER = 0.025

# Standard normal quantile at 0.9. Duplicated from dse_verify deliberately --
# the enumerator must not import the verifier -- and asserted equal by the tests,
# because the whole point of the function below is that prediction and
# certification use the SAME number.
Z_90 = 1.2816


def log2pf_sensitivity(sigma_k, inputs, q):
    """|d log2Pf / d ln sigma|, exactly, by central difference.

    The obvious linearization is 2*|log2Pf|, but the erfcx term works against the
    x^2 term and the true factor is 1.91 to 1.97 over the range these candidates
    occupy. Kept exact because it is used both to SUBTRACT sampling variance (where
    over-stating hides a real scatter) and to ADD a quantile penalty (where
    under-stating ships an optimistic set).
    """
    h = sigma_k * 1e-6
    d = (log2_pf(sigma_k + h, inputs, q) - log2_pf(sigma_k - h, inputs, q)) / (2 * h)
    return abs(d * sigma_k)


def max_priced_autokeys(N, word_size, method, values, default=10):
    """The largest numAutoKeys in `values` this regime can actually price.

    numAutoKeys is NOT a trade-off dimension, and that is why it does not belong
    in the search grid. Both of its effects improve together:

        noise    K_ACC_LMKCDEY_AUTO / w    falls with w
        gate     c3 * N / w                falls with w   (c3 >= 0 in every regime)

    (c3 can also be ZERO -- N=1024/w32 at 41709fbc, below the timing quantum --
    and then the gate is flat in w while the noise still falls, so the ceiling
    is still the answer.)

    so the only thing it costs is key material, `2*N*word*w*(digits-1)`. The
    optimum is therefore always the largest value available, not an interior
    point -- which is a closed form, not something to enumerate. Measured on
    STD256Q_3_LMKCDEY, the most autokeys-sensitive pick in the 2026-09-08 table
    (accumulator at 63 percent of variance): w = 2 -> 40 buys 6.1 bits AND 18
    percent of gate time, and w = 40 -> 512 buys 0.3 bits and 1 percent. The
    curve saturates, which is why every pick in that table sat at the grid's
    ceiling of 40.

    The direction never reverses with thread count. c3 is non-negative in all
    four regimes, and LARGER single-threaded (N=2048/w64: 103.76 single against
    51.68 multi under libomp) -- the automorphism work is partly hidden by
    parallelism when threaded and paid in full when serial. dse_gatefit refuses
    a wrong-sign majority outright rather than fitting it, so a cell priced at
    c3 = 0.0 means "below the 125 us measurement quantum", not "negative".

    Carrying 5 values in the grid instead multiplies LMKCDEY's enumeration by 5,
    and LMKCDEY is 52.7 percent of a full table search.
    """
    if method != "LMKCDEY":
        return default
    best = None
    for w in sorted(values):
        if w < 1:
            continue
        try:
            if gate_us({2: N}, 28, N, method=method, word_size=word_size,
                       autokeys=w) is not None:
                best = w
        except ValueError:
            continue
    return best if best is not None else default


def quantile_penalty(sigma_k, inputs, q, scatter=PER_KEY_LOG2PF_SCATTER,
                     quantile_z=Z_90):
    """Bits by which the 0.9-quantile key is worse than the mean key.

    THIS IS THE PENALTY CERTIFICATION ALWAYS APPLIES, and it is the half of the
    decision the search used to leave out. `certify` sets

        certified = mean_per_key_log2pf + Z_90 * spread

    with `spread` floored at `sensitivity * KEY_SCATTER_BOUND` whenever the
    measurement cannot resolve the scatter -- which is the usual case at the 8-to-12
    keys a table run affords. So a candidate selected because its PREDICTED
    (mean-key) log2Pf reaches the target is selected on a statistic that is better,
    by exactly this many bits, than the statistic that will judge it.

    Calibrated against the 97 certified cells of the 2026-09-08 parameter table.
    At `scatter = KEY_SCATTER_BOUND` -- the sigma-space constant, which is the
    obvious thing to reach for -- the budget tracks the MEDIAN penalty
    certification applied almost exactly (4.65 / 4.49 / 2.39 bits predicted at
    arity 2 / 3 / 4, against 4.58 / 4.49 / 2.86 measured) and still admitted 3 of
    the 7 picks that went on to fail. At PER_KEY_LOG2PF_SCATTER it admits none of
    them, at a cost of 16 of the 63 passing cells being re-searched to a slower
    pick. See that constant for why the two differ.

    This is a budget, not a guarantee: a run that resolves a spread above the
    assumed scatter gets the larger penalty it measured.

    Why linear rather than in quadrature with the model band: this is a
    SYSTEMATIC offset, not an uncertainty. Certification does not sometimes apply
    it. The band is a two-sided uncertainty and stays separate.
    """
    return quantile_z * log2pf_sensitivity(sigma_k, inputs, q) * scatter


def sigma_relative_uncertainty(keys, samples_per_key,
                               scatter=KEY_SCATTER_BOUND):
    """Relative 1-sigma uncertainty on a pooled sigma estimate.

        sqrt(scatter^2 / K + 1 / (2*n*K))

    The first term is the per-key scatter, which shrinks only with KEYS. The
    second is the sampling error of a stddev, which shrinks with total samples.
    Omitting the sampling term makes an accept/reject band accept too eagerly.

    Defaults to the measured UPPER BOUND, not the point estimate: this widens a
    band used to withhold a PASS.
    """
    return sqrt((scatter ** 2) / keys + 1.0 / (2.0 * samples_per_key * keys))


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Key layout: what the library at the pin actually stores
# --------------------------------------------------------------------------
# A coefficient below q_KS reaches only floor((q_KS - 1) / base_KS^(d - 1)) + 1
# values at the TOP digit position, so a switching key allocated base_KS rows at
# every position holds rows no key switch can index (OpenFHE #1302, first half,
# LIB-64). The library on top of 354510e9 stores only the reachable rows there;
# 41709fbc, this tool's pin, still stores them all. DM's refresh key has the same
# dead slice at its top position over q in base_R (LIB-64 SS3; proposed, not yet
# in the branch). The model prices what the PINNED library holds, so both flags
# are False until the pin includes the change; the environment overrides are for
# what-if pricing ("what would the table look like once #1302 lands"), and every
# manifest records the layout it was priced under. Flip the defaults WITH the pin
# move, never before: the VmHWM control (LIB-62, median 1.009) measures the
# library that is installed.
import os as _os
KSK_TOP_COMPACT = _os.environ.get("DSE_KSK_TOP_COMPACT", "1") == "1"
RK_TOP_COMPACT = _os.environ.get("DSE_RK_TOP_COMPACT", "1") == "1"
# The switching key holds no row for digit value 0 and the key switch skips a
# zero digit, so a position of extent r contributes (1 - 1/r) of a Gaussian
# rather than one, and the key loses one row per stored position (94229558).
# This one changes NOISE as well as size: about 0.4 bits of log2Pf at b_KS 32
# and 64, which is why it flips with the pin and not before -- a measurement
# taken at an earlier pin carries the zero rows' noise.
KSK_ZERO_ROWS_DROPPED = _os.environ.get("DSE_KSK_ZERO_ROWS_DROPPED", "1") == "1"


def top_digit_extent(modulus, base, digits):
    """Values the top digit position can take: floor((modulus - 1) / base^(digits - 1)) + 1.

    Equal to `base` exactly when base^digits == modulus (an aligned pair), smaller
    otherwise -- 128 of 256 for q_KS 2^15 at base 256, 2 of 32 for q 2048 at
    base_R 32 over three digits.
    """
    return (int(modulus) - 1) // int(base) ** (int(digits) - 1) + 1


def ks_digit_extents(q_ks, base_ks, d_ks=None, compact=None):
    """Values each digit position can take, low position first.

    Every position spans the whole base except the top one, which a q_KS that is
    not an exact power of the base confines to `top_digit_extent`. With the top
    position uncompacted the library allocates the full base there too, and the
    rows beyond the extent are simply never indexed.
    """
    if d_ks is None:
        d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    if compact is None:
        compact = KSK_TOP_COMPACT
    top = top_digit_extent(q_ks, base_ks, d_ks) if compact else int(base_ks)
    return [int(base_ks)] * (d_ks - 1) + [top]


def ksk_rows(q_ks, base_ks, d_ks=None, compact=None, zero_dropped=None,
             dropped_digits=0):
    """Rows of the switching key per (N, n+1) slot, as the pinned library holds it.

    A stored position of extent r holds r rows, or r - 1 where the library keeps
    no row for digit value 0. The positions below `dropped_digits` are rounded
    away by an approximate decomposition and stored at all. So

        rows = SUM over stored positions of (extent - 1 if zero_dropped else extent)

    which reduces to base*d with nothing enabled, (d-1)*base + top with the top
    compacted, and (d-1-delta)*(base-1) + (top-1) with all three -- the count
    `LWESwitchingKey32Impl` allocates.
    """
    if d_ks is None:
        d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    if zero_dropped is None:
        zero_dropped = KSK_ZERO_ROWS_DROPPED
    delta = int(dropped_digits or 0)
    if delta >= d_ks:
        raise ValueError("droppedDigitsKS %d leaves no digit of q_KS in base %d"
                         % (delta, base_ks))
    ext = ks_digit_extents(q_ks, base_ks, d_ks, compact)[delta:]
    return sum((r - 1) if zero_dropped else r for r in ext)


def ksk_bytes(N, n, q_ks, base_ks, word_bytes, compact=None,
              zero_dropped=None, dropped_digits=0):
    """Serialized key-switching key size. Dominates key material in absolute terms.

    N * rows * (n + 1) * word, with rows from ksk_rows: base_KS * d_KS as every
    library up to 41709fbc stores it, (d_KS - 1) * base_KS + top once #1302's
    first half is in the pin (-25% on a 2^15 / 256 key, 0 on an aligned pair).
    """
    d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
    rows = ksk_rows(q_ks, base_ks, d_ks, compact, zero_dropped, dropped_digits)
    return N * rows * (n + 1) * word_bytes


def ap_refresh_keys_per_coeff(q, base_r, compact=None):
    """RGSW refresh keys DM stores per LWE coefficient: (base_R - 1) * digits_R, or
    (digits_R - 1) * (base_R - 1) + (top - 1) once the dead top slice is dropped."""
    digits_r = digits_for_base(int(q).bit_length() - 1, int(base_r), modulus=int(q))
    if compact is None:
        compact = RK_TOP_COMPACT
    if not compact:
        return (int(base_r) - 1) * digits_r
    return (digits_r - 1) * (int(base_r) - 1) + (top_digit_extent(q, base_r, digits_r) - 1)


# RGSW ciphertexts per LWE coefficient, times 2 polynomials each. GINX needs 4
# ciphertexts (it decomposes the ternary secret into +-1 parts), LMKCDEY needs 2.
# MEASURED by backing the constant out of shipped key sizes: GINX 8.02-8.07 over
# 2 sets, LMKCDEY 4.01-4.04 over 6 sets including both multi-base LPF ones. The
# ~0.5% excess over the integer is serialization headers.
#
# The plan used 8 for both and added the automorphism term on top, which
# overstates LMKCDEY BTkey by ~2x -- and LMKCDEY is precisely the method where
# the gadget map is supposed to pay, so the map's key-size benefit there was
# being doubled.
# AP's 4 is per REFRESH KEY, of which each coefficient holds
# (baseR - 1) * digitsR rather than GINX's two -- applied in btkey_bytes, since
# unlike the other two it is not a constant. Validated at -0.86% against the one
# shipped AP set (2 307 981 312 predicted against 2 327 998 053 measured), the
# same accuracy the GINX and LMKCDEY rows get.
BTKEY_CIPHERTEXTS = {"GINX": 8, "LMKCDEY": 4, "AP": 4}


def btkey_bytes(N, gadget_map, log_q_big, word_bytes, method="GINX", autokeys=0,
                base_r=None, q=None, compact_rk=None):
    """Serialized bootstrapping key size.

        C * N * wordsize * SUM_j count_j * (digitsG_j - 1)        C per method above
        + 2 * N * wordsize * autokeys * (digitsG_default - 1)     LMKCDEY only

    Validated to under 1% against every shipped GINX and LMKCDEY set, multi-base
    included. AP is NOT covered: its refreshing key is indexed by baseRK rather
    than the gadget, and measured 2.33 GB against 37 MB from this formula -- a
    42x miss. It raises rather than returning a number nobody should trust.

    LMKCDEY's automorphism keys are allocated at (digitsG - 1) rows UNDOUBLED
    (rgsw-acc-lmkcdey.cpp:209-210), hence the different multiplier, and they use
    the DEFAULT gadget base -- which is why a multi-base map is fine here, so long
    as the automorphism term is computed from the default base rather than the map.
    """
    if method not in BTKEY_CIPHERTEXTS:
        raise ValueError("no BTkey model for method %r" % method)
    rows = sum(c * (digits_for_base(log_q_big, b) - 1) for b, c in gadget_map.items())
    total = BTKEY_CIPHERTEXTS[method] * N * word_bytes * rows
    if method == "AP":
        # Each coefficient holds a refresh key per (nonzero digit value, digit
        # position): KeyGenAcc allocates (n, baseR, digitsR) and fills j = 1 ..
        # baseR-1 over every k (rgsw-acc-dm.cpp:20-31). Note this uses the FULL
        # digitsR, not the sparsity-corrected product count -- every digit
        # position is generated; the skipping happens at evaluation time. Getting
        # those two counts the wrong way round is the easy mistake, and it would
        # understate the key by a third at baseR = 2.
        if base_r is None or q is None:
            raise ValueError("AP needs base_r and q for its refresh-key count")
        # ... unless the library at the pin drops the values the top position
        # cannot reach (ap_refresh_keys_per_coeff, LIB-64 SS3): 30-44% of the
        # refresh key on 27 of the 35 AP picks once it lands.
        total *= ap_refresh_keys_per_coeff(q, base_r, compact_rk)
    if method == "LMKCDEY":
        if autokeys <= 0:
            raise ValueError("LMKCDEY needs autokeys > 0 for its automorphism-key term")
        # the default base is the smallest in the map, matching OpenFHE's gadgetBase
        base_default = min(gadget_map)
        total += 2 * N * word_bytes * autokeys * (digits_for_base(log_q_big, base_default) - 1)
    return total


# Gate time is AFFINE in accumulator work, not proportional:
#
#     gate_us ~= fixed(method, N, word) + slope(method, N, word) * gate_work
#
# Measured on the pinned build, one machine, 8 shared cores:
#
#   GINX    NS32 N=1024   fixed 10371 us   slope  6.00   worst +7.9%   (work range 4.3x)
#   LMKCDEY NS32 N=1024   fixed 20628 us   slope  5.11   worst -4.6%   (work range 3.0x)
#   GINX    NS64 N=2048   fixed  1210 us   slope 59.31   worst +0.2%   (work range 1.6x)
#
# The fixed part is what the plan's proportional model omitted, and it is not
# small: 27% of a typical GINX gate at N=1024 and 50% of an LMKCDEY one. That
# qualifies the claim that reducing digitsG is the main speed lever -- at
# LMKCDEY/N=1024, halving gadget work buys at most 25% of the gate.
#
# The NS64 intercept is NOT trustworthy: its three sets span only 1.6x in work,
# so extrapolating to work=0 is unreliable. Determining an intercept needs a WIDE
# work range at fixed (method, N, word), which the shipped sets do not provide --
# a calibration requirement, not something to read off a parameter table.
#
# These are local constants. The plan already warns the word-size factor is
# 2.0-2.7x, machine and toolchain dependent; nothing here is portable, and the
# NS32-vs-NS64 rows above are confounded with N and must not be differenced to
# infer a word-size factor.
# Per-gate microseconds, fitted per (method, N, word_size) as
#
#     gate_us = c0 + c1*n + c2*gate_work
#
# The n term is not redundant with gate_work even though gate_work contains n.
# gate_work counts RGSW row-operations; n additionally counts the per-coefficient
# loop overhead around them, and the two separate only if n is VARIED. The first
# calibration held n = 256 in every cell on the assumption that it was redundant,
# and the resulting work-only model missed by up to 13.5 per cent with obvious
# structure -- the same collinearity trap the accumulator term had, in a term
# nobody was watching. This was previously a two-constant table covering 3 of the
# 12 (method, N, word_size) cells, which meant the enumerator could only ever
# place candidates from those three on a frontier.
#
# 416 designed timings, every configuration measured TWICE, n = 64..960, four
# gadget bases (d = 3, 4, 6, 7), logQ = 27, machine idle -- plus the shipped-set
# timings from SHIPPED_COST. The per-configuration estimator is the MINIMUM of
# each duplicate pair: a timing outlier can only be slow, never spuriously fast.
# Duplicate spread was 0.34 per cent median, 1.71 at the 90th percentile, with 8
# of 208 configurations over 5 -- one of them 45, which on its own had taken a
# cell from ~5 per cent residual to 28.7 when it was fitted rather than rejected.
#
# The constants below are fitted on everything. The evidence that the FORM is
# right is the out-of-sample test that preceded it -- fit on designed timings
# only, scored against SHIPPED_COST:
#
#     GINX     12 sets   median 3.0 per cent   worst 5.6
#     LMKCDEY   6 sets   median 4.6 per cent   worst 11.7
#
# Two honest caveats.
#
# LMKCDEY IS THE WEAK HALF, and its within-cell residuals (10-20 per cent) say so
# before the shipped sets do. gate_work is derived from GINX's structure -- one
# external product per LWE coefficient -- whereas LMKCDEY does external products
# AND automorphisms, with the count set by numAutoKeys. Every timing here holds
# numAutoKeys = 10, so that dimension is unmeasured and unidentifiable from this
# data, exactly as it was for LMKCDEY's NOISE before the numAutoKeys arm ran. A
# cost arm varying it is the fix.
#
# GINX/2048/NS32 carried 13.3 per cent under the OLD pin because STD256Q sits at
# n=1242 and work=9936, beyond the designed range in both. It is the only NS32
# N=2048 shipped set, so there was nothing to average it against.
#
# ===========================================================================
# RE-CALIBRATED 2026-09-02 against pin b72753b5 -- parallel CGGI accumulator AND
# parallel key switch. 810 timings, 8 threads, idle single-socket i7-9700 (8
# cores, ONE NUMA node, so no binding subtlety). All 12 cells, ordinary least
# squares on ABSOLUTE microseconds (the clock error is absolute, so uniform
# weights are the correct choice, not a shortcut).
#
# Supersedes two earlier fits: the original serial-accumulator one, and a
# da4d1f48 one that had a parallel accumulator but a serial key switch.
#
# COVERAGE, which is the part that kept going wrong. Earlier arms spanned d=2..5
# while shipped sets reach d=7, and the out-of-sample residual correlated with d
# at r = -0.78 -- pure extrapolation, not a missing term. Chasing it produced two
# refuted hypotheses (OpenMP round quantisation, an n x d interaction) before the
# obvious fix: this arm spans **d = 2..13**, and with d inside the fit the
# correlation collapses to about +-0.2. Cover the range you intend to predict.
#
# WHAT THE RESIDUALS MEAN, per cell type:
#   N=1024/2048  median 1.5-3.6%, max 7-26%.  These are the cells that matter --
#                every shipped set and every search winner lives here.
#   N=512        median 0.9-4.5% but max ~62%. NOT a model failure: those gates
#                run 9-27 ms for the harness's fixed 8 gates, and it reports
#                INTEGER milliseconds, so gate_us carries a +-125 us floor which
#                is 4-11% there. 97 of 810 points are resolution-limited, all at
#                N=512 with n <= 256. No shipped set uses N=512.
#
# THE KEY-SWITCH TERM WAS TESTED AND REJECTED. q_KS was swept 2^15/2^18/2^21 at
# baseKS=32 to move d_KS through 3/4/5 (an earlier attempt swept baseKS 32 vs 128,
# which gives ceil(15/5)=3 and ceil(15/7)=3 -- the SAME digit count, so d_KS never
# moved and the sweep measured nothing). With d_KS genuinely varying, adding
# c*N*d_KS buys 0.1-0.2 percentage points, makes one cell worse, and returns a
# NEGATIVE coefficient in another. At 8 threads the parallel key switch is ~460 us
# against a ~20 ms gate, i.e. under the fit's noise floor. Addendum 23's concern
# was right for 36 threads and does not bite here.
#
# TWO CELLS CARRY A NEGATIVE c0 (GINX 1024/64 and 2048/32) because their data
# starts at n=512. It does not produce negative predictions -- c1*n + c2*gw
# dominates, giving 676 us at n=32 -- but those cells ARE extrapolated below
# n=512, and the grid proposes n from 32. Treat small-n predictions there as
# indicative.
#
# c3 is None for GINX, which has no automorphism keys at all. For LMKCDEY it is
# MEASURED at this pin: 120 timings with w crossed against two n and two gadget
# bases INSIDE each cell (w must vary within a cell or c3 is unidentifiable, which
# is how it was None for a while). Residuals fall from 2.26-15.96% to 0.83-3.61%,
# all six coefficients positive, and the w-spread grows with N exactly as the
# mechanism predicts -- 11% at N=512, 63% at N=2048 -- because automorphism work
# scales with the ring while external products scale with n.
#
# So all five autokeys values the grid proposes are now priced. They were not:
# gate_us returned None for w in {2,5,20,40}, silently removing four fifths of
# that axis for LMKCDEY.
#
# Incidental confirmation of f9694c39: c3 roughly halved at N=1024 and 2048 NS32
# against the da4d1f48 fit (19.7->13.5, 50.2->36.9), which is the automorphism-map
# caching that commit adds.
# ===========================================================================
# ---------------------------------------------------------------------------
# Provenance of the cost cells below.
#
# A method-vs-method conclusion is only as good as the build BOTH methods were
# timed on, and this is not a hypothetical. On 2026-09-02 the conclusion
# "LMKCDEY beats GINX at STD192Q and STD256Q" was retracted: f9694c39
# parallelised the CGGI accumulator and gave LMKCDEY only automorphism-map
# caching, so GINX's accumulator coefficient improved 2.58x geomean against
# LMKCDEY's 0.95x, and candidates that had won by 1.12x lost by 0.55x. The
# numbers were right; the build had moved under them.
#
# That cost a full refit to discover, and it would have been visible immediately
# if the comparison had printed the pin it was measured under. Hence this block,
# and `cost_provenance()` / `comparison_caveat()` below -- the reviewer's ask
# (Addendum 28 item: "tag any method-comparison conclusion with the code state it
# was measured under").
#
# UPDATE THIS WHENEVER GATE_COST IS REFITTED. A stale pin here is worse than none,
# because it invites exactly the false confidence it exists to prevent.
# The w32 cells are re-measured at 88710e3b (330 timings, gatecost-regime.sh at
# 8 threads, clang-18), which is the PUBLISHED tip of issue1269. The w64 cells
# still rest on the earlier 930 timings.
#
# On the previous pin, and what it was worth. These cells were measured at
# 678ea50c, which was force-pushed off issue1269 and replaced by the amended
# 88710e3b -- siblings off the same parent, whose trees differ by 468 lines in
# math/hal/intnat/transformnat-impl.h, the NTT inner loop. That sounded
# disqualifying and I said so. Measured across eight realistic candidate shapes,
# the old cells differ from these by a median of +1.3% and at worst +3.4%, which
# is INSIDE the model's own out-of-sample error of 4.0% median / 5.4% worst. So
# no ranking drawn on them changed, and the alarm was mine, not the data's.
#
# Why so small: the commit makes the 32-bit NTT inner loops auto-vectorizable,
# and clang was already vectorizing them. The large movement is on the gcc path,
# which had been emitting no vector code at all for the 32-bit butterflies (gcc
# w32 cells 1.87-2.04x faster at native-opt, per dse-plan-review Addendum 30).
# Nothing here was ever priced under gcc, so nothing here was exposed to it --
# which is the argument for keeping the compiler in the regime key rather than in
# a footnote.
#
# STILL PENDING, and this one is material. bf3c9ca5 ("Reuse scratch and monomial
# Shoup constants in the blind rotation") moves 8-THREAD cells by +13-21% at w32
# and +5-10% at w64. That differential is the problem, not the magnitude: the
# unified search compares w32 candidates against w64 baselines, so an uneven
# shift between the widths moves exactly the comparison the w32* results rest on.
# It is not fetchable yet -- `upload-pack: not our ref` -- so it cannot be
# measured until it is pushed.
# The image pin moved to 41709fbc on 2026-09-09 (LIB-63): one OpenMP team width and one
# scratch size across gadget bases. That removes a cost the 238153db cells CONTAIN for
# two-base LMKCDEY maps -- team-width churn as LMKCDEY visits indices in automorphism
# order -- worth 6-9% under libomp and 21-37% under libgomp on high-minority maps.
# GINX, AP and single-base LMKCDEY are unchanged. multi/libomp's LMKCDEY cells were
# re-measured at 41709fbc the same day (530 timings, 90 of them two-base maps) and its
# GINX cells checked there against 90 two-base GINX rows (+0.4..+0.8% median, no
# trend in the coarse fraction), so that regime is current; multi/libgomp's LMKCDEY
# cells are still the 238153db ones and its `stale` flag says so.
COST_STALE = ("two-base LMKCDEY gate times in this regime: its LMKCDEY cells are at 238153db "
              "and include team-width churn that 41709fbc removed (21-37% under libgomp); "
              "re-measure with METHODS=3 MAPS=1 under gcc")
# Cells are carried to the image's pin where the commits between are not scheduling:
# multi/libomp holds LMKCDEY at 41709fbc and GINX/AP at 238153db, multi/libgomp is at
# 238153db, and the single regimes are at a0c3f2cd BY DESIGN (no OpenMP region runs at
# one thread). pins_by_method records where each was measured.
COST_PIN = "128582771b50ce798bfa36d63e43b550b1b17ea0"
# Regimes are re-measured one at a time after a pin move, so a regime carries
# its OWN pin until all four agree -- and, since 2026-09-09, its cells can rest
# on two pins by METHOD (pins_by_method), because an arm is run per method.
# COST_PIN above is the image's pin, i.e. what the newest cells describe.
PIN_A0C3 = "a0c3f2cdbf1d48861e3b06705b01ee98832506da"
MEASURED_A0C3 = "2026-09-05"
PIN_2381 = "238153db950b186eff5ce7b17235d74e3aab5c98"
MEASURED_2381 = "2026-09-05"
PIN_4170 = "41709fbc52fe40db1f9c65c84655fd7647748804"
MEASURED_4170 = "2026-09-09"
# The image's pin 2026-09-09 to 2026-09-15. Two commits past 41709fbc (which it
# contains, rebased as 354510e9), both of them key LAYOUT and neither of them
# scheduling: gate times were measured unchanged across both at 1 and 8 threads,
# so the cells measured at the commits above describe this library and are
# carried rather than re-run.
PIN_9422 = "94229558d45111bffe0d26577529a282924f9f59"
# The image's pin since 2026-09-15: the head of openfhe-development dev, i.e. the
# squash-merge of the branch 94229558 sat on (#1295) plus two CKKS-only PRs
# (#1308, #1276) that touch nothing under src/binfhe or src/core. Between
# 94229558 and the merge the accumulator and key-switch inner loops are
# unchanged; 09913224 adds two per-gate guards and the 28-bit Fits cap,
# e5d64a4c folds key GENERATION into shared templates and moves the LMKCDEY
# schedule into a shared function, and the rest is serialization and the table
# itself. No key-layout commit is in the span, so the three layout flags stay
# on; the cells are carried on the strength of the gate-time A/B recorded in
# COST_PIN_CONTAINS, not on inspection alone.
PIN_1285 = "128582771b50ce798bfa36d63e43b550b1b17ea0"
# What a two-base LMKCDEY map pays for running every index at the WIDEST base's
# team width (41709fbc's one-team-width rule), as a fraction of c2 on the digits
# the narrow-base indices do not have: gate_us += share * c2 * (n*d_max - gate_work).
# Fitted on the 90 two-base rows of the 2026-09-09 arm: 0.49 x c2 at N=1024 and
# 0.36 at N=2048 (0.45 / 0.43 against the 238153db baseline with an intercept);
# the GINX control on the same 90 maps reads -0.10 / -0.05, i.e. zero. It is a
# REGIME property (idle threads in a fixed-width team), so it lives in the regime
# dict: 0.40 where measured, 0 in the single-thread regimes by mechanism, None
# where unmeasured (priced as 0 and flagged stale).
LMKCDEY_MAP_WIDTH_SHARE = 0.40
COST_MEASURED = "2026-09-05"
COST_THREADS = 8
COST_BOX = "single-socket i7-9700, 8 cores, 1 NUMA node"
# The w32 cells were re-measured ON the hybrid at the pin above (330 timings,
# gatecost-hybrid.sh); the w64 cells still rest on the earlier 930. Mixing two
# pins in one table is stated rather than hidden: c1 moved under 8% and c2 by
# 10-21% between them, so a w32-vs-w64 comparison carries that much slack.
COST_POINTS = 660

# What the pin above DOES contain, in the order it matters for cost:
COST_PIN_CONTAINS = (
    "12858277 (the image's pin since 2026-09-15): the head of openfhe-development dev, "
    "the squash-merge of the branch 94229558 sat on plus #1308 and #1276, which touch no "
    "file under src/binfhe or src/core. Between 94229558 and the merge the accumulator and key-switch inner loops are unchanged: 09913224 adds two per-gate guards (the ciphertext modulus must divide 2N, and must not exceed q_KS) and the 28-bit MAX_MODULUS_SIZE32 cap the model already prices; e5d64a4c folds the three 32-bit key-GENERATION twins out of the accumulator .cpp files into shared templates (RGSWEncrypt, RGSWEncryptAutomorphism, MonomialOf in rgsw-acc-common.h) and moves the LMKCDEY schedule into a shared function; then serialization for the 32-bit keys and RingGSWBTKey, and the 112-row parameter table this tool produced. LIB-69 timed the two code commits at 1.001-1.003x on icelake. The 108 "
    "table rows this tool certified are in it with geometry and label identical. "
    "Gate time across the move on the cost box (gatetime-ab.sh, the same explicit geometry at both pins, pins interleaved, REPS=6; 12858277 / 94229558 on min-of-8, best of 6): STD128 1.010, STD128_3 1.000, STD192_4 1.014, STD128_LMKCDEY 1.000, STD128_AP 1.013, STD128_3_AP 1.034. Within the cells' own residuals (medians 1.6-4.5%), and within a method uniform to 0.4% across N, so no within-cell ranking moves and the cells are carried; AP reads 2.4-2.7% slower (REPS=12 on the two AP sets, the new pin's twelve repetitions above the old pin's almost without overlap) and that is what to expect when its cells are next re-measured. Two pin-major passes before that read +-3% with the sign following which pin ran FIRST, and one named set (STD192_4) had changed row with the table, which is why the arm interleaves the pins and times geometry rather than names.",
    "f9694c39 parallel CGGI accumulator (GINX c2 improved 2.58x geomean; "
    "LMKCDEY 0.95x -- it only gained automorphism-map caching)",
    "b72753b5 parallel key switch (tested and NOT worth an explicit term at "
    "8 threads: ~460us against a ~20ms gate)",
    "0c75c2ec fused LMKCDEY/DM accumulator regions: LMKCDEY's accumulator is "
    "parallel for the first time. Against 1c3e83ec its 8-thread c2 fell to "
    "0.59-0.60x (w64) and 0.67-0.70x (w32) while GINX moved < 3%. Every "
    "method-vs-method conclusion drawn before this pin was re-run on it.",
    "a0c3f2cd the OpenMP saturation fix: caps THAT region at max(digitsG2/2, 4) "
    "threads, shares its digit decomposition across the region at digitsG2 >= 8, "
    "blocks the 32-bit inner product across the region. The libgomp "
    "one-chunk-per-thread cliff (4.8x, bimodal) is gone: libgomp/libomp at 8 "
    "threads is median 1.00-1.14, p95 <= 1.22, and multi/libgomp LMKCDEY is "
    "priced (tail p05 -5.4..-8.2%). CGGI's own region (rgsw-acc-cggi.cpp:168,322) "
    "keeps the old digitsG2 cap; measured, GINX never had the min-cliff (libgomp/"
    "libomp 0.93-0.95 at 8 threads) but both runtimes show 163-385% intra-run skew "
    "at 6-8 threads there, which the 8-gate MEAN these cells rest on carries.",
    "9e8045db the 32-bit key forms are the library default (BTKeyGen/BTKeyLoad "
    "internal32 = true). w32 cells are timed on exactly that path; w64 cells at "
    "logQ 37 carry the 32-bit SWITCHING key a default caller now gets, which "
    "moved c1 by < 3%.",
    "LIB-57 (2026-09-05) closed the CGGI question by measurement: capping CGGI's "
    "region costs 4-12% at 36 threads and ~1% at 8 where there is no penalty to "
    "relieve, so CGGI stays uncapped on evidence. The 163-385% GINX intra-run skews "
    "seen on llserver at 6-8 threads did not reproduce on the 72-core box under a "
    "cpuset; they stay noted here as this box's property.",
    "94229558 (the image's pin 2026-09-09 to 2026-09-15): two key-layout commits on top of "
    "41709fbc, rebased there as 354510e9. c4180d75 stores only the reachable rows at the top "
    "digit position of the switching key and of DM's refresh key; 94229558 drops the rows for "
    "digit value zero and skips a zero digit in both key switches. Both shrink keys and key "
    "generation and NEITHER moves a gate: measured unchanged at 1 and 8 threads on both "
    "runtimes, which is why the cells above are carried here rather than re-measured. The "
    "second one changes NOISE -- a position of extent r contributes (1 - 1/r) of a Gaussian "
    "instead of one, worth about 0.4 bits of log2Pf at b_KS 32 and 64 -- so the model's three "
    "layout flags flip with this pin and a measurement taken before it carries the zero rows' "
    "noise.",
    "41709fbc (the image's pin 2026-09-09, carried into 94229558 as 354510e9): every accumulator region of a context "
    "runs at ONE OpenMP team width and one scratch size across gadget bases (LIB-63), "
    "which removed the team-width churn a two-base LMKCDEY map paid at 238153db "
    "(6-9% libomp, 21-37% libgomp on high-minority maps). multi/libomp LMKCDEY "
    "re-measured here: c1/c2 within 5%/6% at N >= 1024, c3 at N=1024/w32 below the "
    "quantum (priced 0, was 5.7). What remains on two-base LMKCDEY is 0.7-2.3% at "
    "the median, follows (gw_max - gw) at 0.36-0.49 x c2 with a GINX control at "
    "zero, and is priced by LMKCDEY_MAP_WIDTH_SHARE. GINX cells verified unchanged "
    "here (+0.4..+0.8% on 90 two-base rows); AP not re-measured (its region is not "
    "the one the commit touched).",
    "238153db (the image's pin 2026-09-05 to 2026-09-09): the half cap is replaced "
    "by EQUALIZED team sizes. LIB-58's 2x2 showed the libgomp disease was the "
    "runtime rebuilding its thread team between consecutive regions that asked for "
    "different counts (accumulator digitsG2, automorphism key switch digitsG2/2), "
    "not single-chunk workers -- which co-varied with it in every earlier table. "
    "Equalizing at FULL width keeps the cure and gives back the parallelism the cap "
    "took: 6-11% faster on w >= 6 LMKCDEY sets at 36 threads, within 0.95-1.05 of "
    "the cap at <= 8. So the LMKCDEY multi-thread cells move a little at 8 threads "
    "and thread-sweep columns no longer flatten at digitsG2/2. GINX/DM untouched; "
    "1-thread cells unaffected. e39eee84 (same push) is keygen-only.",
)

# What it does NOT contain, each of which is expected to move a method comparison.
# Anything listed here is a scheduled invalidation, not a vague caveat.
COST_PIN_LACKS = (
    # Nothing scheduled upstream as of 2026-09-05 evening. The LMKCDEY cap question
    # (closed: equalize, 238153db), the CGGI cap question (closed: measured and
    # rejected, LIB-57) and the automorphism-region question (closed: it is the
    # other half of the equalization) are all in COST_PIN_CONTAINS now. A pke-side
    # team-size campaign is queued on the library side; it does not touch BinFHE.
)

# Regimes the cells do NOT represent, as distinct from things they get wrong.
COST_UNREPRESENTED = (
    "many-core deployments. These cells stop at 8 threads on an 8-core box. At "
    "238153db the LMKCDEY regions run at full digitsG2 width with equal team "
    "sizes, and LIB-59 measured them 6-11% faster than the capped pin at 36 "
    "threads with rows still improving to the core count -- gains an 8-thread "
    "cell cannot see. A ranking for a many-core deployment needs cells measured "
    "there (the arm takes THREADS=<n>).",
    "n = 64 for LMKCDEY. Every cell's data now spans n = 64..N (gatecost-regime.sh, "
    "relative fit), but the linear form still cannot reach the bottom row: the "
    "c3*N/w automorphism term does not scale with n, so at n=64 and high digit "
    "counts the fit over-predicts by 16-33% (both runtimes, both thread modes) "
    "against medians of 2.5-6% at n >= 256. GINX has no such term and its n=64 "
    "rows sit within 8%. Nothing shipped or on any front has n < 256.",
    "AP's tail, and its N=2048/w32 degeneracy. Two to four rows per AP cell sit "
    "16-73% off the fit, scattered in n, digit count and refresh base rather than "
    "structured -- the same single-slow-row spread the other methods show, but "
    "wider because an AP gate runs hundreds of milliseconds so an 8-gate mean is "
    "more exposed to one hiccup. Medians are 1.6-4.5%, in line with LMKCDEY. "
    "Separately, at N=2048/w32 the bounded fit pins c1 to 0.0 and lets the "
    "per-product term carry the whole n dependence: that reproduces the measured "
    "grid but must not be extrapolated to product counts the three refresh bases "
    "did not visit.",
    "single slow rows. Three (libomp) to five (libgomp) of 660 timings per regime "
    "are one 8-gate MEAN 18-46% above the fit, scattered in n and d -- libgomp's "
    "residual run-to-run spread after the cap, not a pattern. The n >= 256 tails "
    "quoted per regime (p05 -4..-8%) include them.",
)


def key_word_bytes(N, log_q_big, q_ks, base_ks, gadget_map, method, word_size):
    """(refresh key, switching key) bytes per word, as a DEFAULT build holds them.

    Two numbers, not one, because the two keys narrow independently and on
    different predicates: the refresh key on Q and the gadget widths, the
    switching key on q_KS and its row count. Since OpenFHE 9e8045db both narrow
    by default wherever they fit, so a logQ 37 set holds a 64-bit accumulator key
    AND a 32-bit switching key -- and the switching key is the larger of the two
    at those dimensions.

    RESIDENT bytes. Serialization widens a narrowed key back to 64 bits, so a
    serialized figure is 8 for both regardless; measured, the serialized key is
    byte-identical with and without the 32-bit form.
    """
    import dse_constraints as c
    refresh32 = (int(word_size) == 32
                 and c.hybrid_ns32_ok(log_q_big, gadget_map, method,
                                      default_base=min(gadget_map)))
    switch32 = c.hybrid_switch32_ok(N, q_ks, base_ks)
    return (4 if refresh32 else 8), (4 if switch32 else 8)


def beats_at_no_worse_pf(cand_log2pf, cand_band, ship_log2pf, ship_band):
    """True if `cand` is no worse on failure probability than `ship`, conservatively.

        cand_log2pf + cand_band  <=  ship_log2pf - ship_band

    BOTH sides are predictions and both carry model error, so the candidate has to
    clear the baseline's PESSIMISTIC end, not its point value. Comparing a banded
    candidate against an unbanded baseline is not merely optimistic, it is wrong in
    a way that fires: it accepted a STD256 candidate at predicted -64.2 +-2.8
    against a shipped point prediction of -59.6, and the measured pair came out
    -61.2 against -61.7 -- the candidate 0.5 bits WORSE. Shipped had predicted 1.8%
    high and the candidate 2.5% low; one-sided banding covers neither direction.

    Lives here rather than in the comparison tools because they each had their own
    copy of the test, which is how they came to disagree.
    """
    return cand_log2pf + cand_band <= ship_log2pf - ship_band


def cost_provenance():
    """One line naming the build AND regime these cost cells were measured on."""
    r = COST_REGIMES[ACTIVE_REGIME]
    if not r["cells"]:
        return ("GATE_COST: regime %s/%s is NOT MEASURED -- no cells, so every "
                "candidate is refused. Measure it with "
                "scripts/dse-arms/gatecost-regime.sh, or pick one of: %s"
                % (ACTIVE_REGIME[0], ACTIVE_REGIME[1],
                   ", ".join("%s/%s" % k for k in measured_regimes())))
    line = ("GATE_COST: %d timings describing OpenFHE %s, %s (%d thread%s), %s, %s (%s)"
            % (r["points"], (r["pin"] or "?")[:8], r["compiler"], r["threads"],
               "" if r["threads"] == 1 else "s", ACTIVE_REGIME[0], r["box"],
               r["measured"]))
    by_method = r.get("pins_by_method") or {}
    bm = r.get("by_method") or {}
    if by_method and set(by_method.values()) != {r.get("pin")}:
        line += "\n  measured at: " + ", ".join(
            "%s %s%s" % (k, v[:8],
                         " (%d timings, %s)" % (bm[k]["points"], bm[k]["on"])
                         if k in bm else "")
            for k, v in sorted(by_method.items()))
        if r.get("carried_why"):
            line += "\n  carried to %s: %s" % ((r["pin"] or "?")[:8], r["carried_why"])
    # A table whose rows come from two machines says so here, because the summary
    # line above can only name one box and the difference is what makes a
    # method-against-method reading of these cells wrong.
    if len({v["box"] for v in bm.values()}) > 1:
        line += "\n  boxes: " + "; ".join(
            "%s on %s" % (k, bm[k]["box"]) for k in sorted(bm))
    if COST_STALE and r.get("stale", True):
        line += "\n  STALE: %s" % COST_STALE
    return line


def map_width_us(gadget_map, log_q_big, c2, share):
    """Extra microseconds a two-base LMKCDEY map pays for the widest team width.

    Since 41709fbc every accumulator region of a context runs at ONE team width,
    the widest base's, so an index at the narrower base has idle threads instead
    of a smaller team. Priced as `share * c2` per digit an index does not have:

        share * c2 * (n * d_max - gate_work)

    where gate_work is the per-index sum. Zero for a single-base map (d_max is
    every index's own count) and zero where share is 0 or None.
    """
    if not share or len(gadget_map) < 2:
        return 0.0
    n = sum(gadget_map.values())
    d_max = max(digits_g2(digits_for_base(log_q_big, b)) for b in gadget_map)
    work = gate_work(gadget_map, log_q_big, method="LMKCDEY")
    return share * c2 * (n * d_max - work)


def comparison_caveat(methods):
    """Caveat lines for a conclusion comparing `methods`, or [] if none applies.

    Cross-method comparisons get a loud one because they are the fragile case:
    parallelism has not arrived uniformly across accumulators, so the ordering
    between two methods is a property of the build and not of the mathematics.
    """
    out = [cost_provenance()]
    if len(set(methods)) > 1:
        out.append("  CROSS-METHOD comparison -- the ordering here is a property "
                   "of this build, not of the algorithms.")
        for lack in COST_PIN_LACKS:
            out.append("  pending: %s" % lack)
    return out


_CELLS_SINGLE_CLANG = {
    # single/libomp at a0c3f2cd, measured 2026-09-05 as the sole job on the box
    # (THREADS=1, clang-18, 660 timings, relative fit). Against 1c3e83ec: GINX
    # within 1% everywhere (its serial path did not change); LMKCDEY c2 within 1%
    # but c1 down 1.1-3.0 us per n, so gates 0.95-1.00x -- 0c75c2ec skips the
    # fused region entirely when serial, which removes a per-step region cost that
    # c1*n was carrying. The 32-bit switching key a default caller now gets at
    # logQ 37 (9e8045db) is inside that c1 movement and not separable from it.
    # c3 below the quantum at N=512 (48%/61% sign agreement) and N=1024 w32 (69%),
    # priced as ZERO there with |c3| < 3.3 / 9.8 / 6.5; resolved at N=1024 w64 and
    # N=2048. Median residuals: GINX 0.4-2.6%, LMKCDEY 3.2-5.9%,
    # with LMKCDEY slightly over-predicted at d >= 5 (n >= 256: +2.6..+5.6% against
    # -1.6..+0.7% at d <= 4) -- a mild sub-linearity in digits at 1 thread, not a
    # kink. Worst rows are n=64 (the documented unrepresented corner).
    ("GINX",     512, 32): (-107.9, 9.383, 3.3934, None),
    ("GINX",     512, 64): (-17.8, 18.795, 11.6636, None),
    ("GINX",    1024, 32): (4.3, 19.031, 6.4850, None),
    ("GINX",    1024, 64): (11.4, 40.799, 23.4264, None),
    ("GINX",    2048, 32): (221.1, 40.129, 12.7717, None),
    ("GINX",    2048, 64): (188.9, 87.361, 47.4894, None),
    ("LMKCDEY",  512, 32): (558.3, 6.490, 3.9655, 0.000),
    ("LMKCDEY",  512, 64): (1347.8, 9.984, 11.2039, 0.000),
    ("LMKCDEY", 1024, 32): (2292.0, 12.153, 8.1780, 0.000),
    ("LMKCDEY", 1024, 64): (3776.1, 18.604, 23.7656, 16.276),
    ("LMKCDEY", 2048, 32): (3079.8, 18.187, 18.2502, 29.297),
    ("LMKCDEY", 2048, 64): (1221.5, 21.093, 53.4367, 103.760),
}

_CELLS_MULTI_GCC = {
    # multi/libgomp at 238153db, measured 2026-09-05 evening as the sole job on the
    # box (THREADS=8, gcc-14, 660 timings, relative fit). All twelve cells, LMKCDEY
    # included, within 3% of the a0c3f2cd cells (0.97-1.01x at n=1024) -- the
    # equalization is invisible at 8 threads on this box, as LIB-59 measured.
    # LMKCDEY tails (n >= 256, w=10) p05 -4.2% .. -10.5%, medians within +2%: the
    # accepted band (GINX here: -4 .. -6%), a shade wider than at a0c3f2cd. No
    # cliff: libgomp/libomp on identical configs is median 1.00 (GINX w32), 1.12
    # (GINX w64), 1.06 / 1.14 (LMKCDEY w32 / w64), p95 <= 1.22, max 1.32.
    # c3 resolved at N=1024 w64 (13.8) and N=2048 (25.6 / 54.5); below the quantum
    # at N=512 and N=1024 w32 (61-69% sign agreement), priced as ZERO with
    # |c3| < 6.5 / 9.8 / 7.3. Large residuals are n=64 and single slow 8-gate
    # means, as in every other table.
    ("GINX",     512, 32): (36.5, 12.551, 0.9282, None),
    ("GINX",     512, 64): (156.1, 20.223, 3.1615, None),
    ("GINX",    1024, 32): (60.8, 20.018, 1.7469, None),
    ("GINX",    1024, 64): (97.2, 37.675, 6.1494, None),
    ("GINX",    2048, 32): (188.0, 35.563, 3.3133, None),
    ("GINX",    2048, 64): (464.3, 71.149, 12.4225, None),
    ("LMKCDEY",  512, 32): (709.9, 13.668, 1.4903, 0.000),
    ("LMKCDEY",  512, 64): (1455.1, 21.543, 4.3886, 0.000),
    ("LMKCDEY", 1024, 32): (2405.7, 20.453, 2.8679, 0.000),
    ("LMKCDEY", 1024, 64): (4072.4, 39.034, 8.8069, 13.835),
    ("LMKCDEY", 2048, 32): (4005.7, 33.325, 5.9460, 25.635),
    ("LMKCDEY", 2048, 64): (10136.3, 67.515, 19.0958, 54.525),
}

_CELLS_SINGLE_GCC = {
    # single/libgomp at a0c3f2cd, measured 2026-09-05 as the sole job on the box
    # (THREADS=1, gcc-14, 660 timings, relative fit). Against 1c3e83ec: GINX within
    # 1% in every cell, LMKCDEY 0.97-1.00x with c2 unchanged and c1 down 0.4-4.0
    # us per n (0c75c2ec skips the fused region when serial). The 1-thread
    # libgomp/libomp ratio is the runtime/codegen term, 1.16-1.26x, unchanged by
    # a0c3f2cd as it had to be. c3 below the quantum at N=512 (52%/70% sign
    # agreement), priced as ZERO there with |c3| < 3.3 / 13.0; resolved from N=1024
    # up. Medians GINX 0.1-2.2%, LMKCDEY 3.6-6.2%; LMKCDEY
    # slightly over-predicted at d >= 5 (n >= 256: +3.6..+5.1% against -1.5..+0.7%
    # at d <= 4), the same mild 1-thread sub-linearity as single/libomp.
    ("GINX",     512, 32): (-31.4, 10.345, 4.0152, None),
    ("GINX",     512, 64): (-48.6, 18.645, 14.3611, None),
    ("GINX",    1024, 32): (68.7, 21.528, 7.7305, None),
    ("GINX",    1024, 64): (117.6, 39.285, 29.2550, None),
    ("GINX",    2048, 32): (311.2, 45.454, 15.4062, None),
    ("GINX",    2048, 64): (347.0, 83.961, 59.9737, None),
    ("LMKCDEY",  512, 32): (795.7, 7.112, 5.0981, 0.000),
    ("LMKCDEY",  512, 64): (1680.3, 10.643, 14.6841, 0.000),
    ("LMKCDEY", 1024, 32): (1614.0, 12.478, 10.7445, 14.648),
    ("LMKCDEY", 1024, 64): (3420.9, 19.604, 31.4262, 33.366),
    ("LMKCDEY", 2048, 32): (4642.7, 17.096, 24.0588, 35.807),
    ("LMKCDEY", 2048, 64): (1227.6, 15.522, 71.0500, 133.870),
}

_CELLS_MULTI_CLANG = {
    # AP at 238153db, measured 2026-09-07 as the sole job on the box (THREADS=8,
    # clang-18, 563 timings over 6 cells, relative fit, refresh bases 2/8/64).
    # The FOURTH coefficient here is NOT LMKCDEY's automorphism term: for AP it is
    # the fixed cost of one external product, against a feature of n*products
    # (dse_model.ap_products_per_gate). GINX and LMKCDEY do a constant number of
    # products per coefficient so that cost is proportional to n and c1 absorbs
    # it; AP is the first method where the count moves independently of the gadget
    # and the two separate. Fitting without it left a residual running -45% at
    # digitsG 2 to +20% at 27; with it the medians are 1.6-4.5%.
    #
    # BOUNDED at zero on c1, c2 and c3. Within one refresh base n*products is
    # proportional to n, so c1 and c3 separate only ACROSS bases and three is
    # thin: unconstrained, N=2048/w32 returned c1 = -0.84, a gate time falling
    # with the lattice dimension. At that cell the bound pins c1 to 0.0 and c3
    # carries the whole n dependence, which reproduces the cell's own 90 timings
    # to a 3.8% median but should not be extrapolated to product counts the arm
    # did not visit. More bases would separate them.
    #
    # Measured 2026-09-12, sole job on the box, relative fit.
    # Box:       Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz
    #            1 socket(s) x 8 core(s) x 1 thread(s), 1 NUMA node(s), 8 online
    # OpenFHE:   94229558d45111bffe0d26577529a282924f9f59
    # Runtime:   Ubuntu clang version 18.1.3 (1ubuntu1) (libomp)
    # Threads:   8 (multi regime), 1 (single regime)
    # Binding:   cpus 0-7, one per physical core, single NUMA node
    # Methods:   AP, GINX and LMKCDEY
    # Rows:      441 of 446 timings (5 OOMed at n=2048 with a coarse base) over the stages in recal-n4096/
    # Residuals, multi:  GINX median 1.7-2.3%, worst 7.4% over 2 cell(s); AP median 2.2-2.9%, worst 17.3% over 2 cell(s); LMKCDEY median 6.1-6.3%, worst 64.7% over 2 cell(s)
    # Residuals, single: stage not run
    # N=4096 ONLY -- these six cells extend the table upward; every other cell
    # here is N <= 2048. LMKCDEY's median residual is 6.1-6.3% against 1.7-2.9%
    # for GINX and AP, and it is STRUCTURE, not scatter: the n x d interaction
    # runs +12% at (n=64, d=2) to -45% at (n=64, d=27). It is confined to the
    # n=64 corner this grid has always under-represented; at n >= 512 the rows
    # sit within +-6%, and at the n ~ 1300 the STD256Q arity-4 cells want,
    # within +-4%. Do not read these cells at small n.
    ("AP",       512, 32): (-262.3, 2.147, 0.9273, 11.878),
    ("AP",       512, 64): (-60.8, 3.043, 2.8425, 16.045),
    ("AP",      1024, 32): (-229.1, 1.234, 1.5641, 17.558),
    ("AP",      1024, 64): (-123.2, 1.893, 5.3152, 28.125),
    ("AP",      2048, 32): (-434.1, 0.000, 2.7880, 28.901),
    ("AP",      2048, 64): (-353.1, 1.947, 10.2781, 53.973),
    ("AP",      4096, 32): (-258.7, 0.000, 4.3156, 53.769),
    ("AP",      4096, 64): (-648.1, 0.000, 19.9036, 108.239),
    ("GINX",     512, 32): (-65.8, 14.617, 0.9207, None),
    ("GINX",     512, 64): (-53.6, 22.103, 2.8076, None),
    ("GINX",    1024, 32): (-34.2, 21.638, 1.6432, None),
    ("GINX",    1024, 64): (-10.9, 36.433, 5.3839, None),
    ("GINX",    2048, 32): (109.8, 35.249, 3.0881, None),
    ("GINX",    2048, 64): (146.0, 66.206, 10.5874, None),
    ("GINX",    4096, 32): (157.3, 64.458, 5.8073, None),
    ("GINX",    4096, 64): (353.6, 126.529, 20.8921, None),
    ("LMKCDEY",  512, 32): (188.6, 16.959, 1.3362, 6.510),
    ("LMKCDEY",  512, 64): (1187.5, 22.134, 4.0104, 0.000),
    ("LMKCDEY", 1024, 32): (1981.9, 24.667, 2.3407, 0.000),
    ("LMKCDEY", 1024, 64): (3102.1, 36.361, 7.7425, 13.021),
    ("LMKCDEY", 2048, 32): (3015.4, 37.379, 4.7696, 20.345),
    ("LMKCDEY", 2048, 64): (5750.6, 58.154, 16.5418, 58.187),
    ("LMKCDEY", 4096, 32): (9960.0, 63.338, 8.4873, 47.607),
    ("LMKCDEY", 4096, 64): (18632.5, 108.487, 33.5770, 130.615),
}


# --------------------------------------------------------------------------
# Cost regimes: (thread mode, compiler)
# --------------------------------------------------------------------------
# A cell is valid only for the thread count and compiler it was measured under,
# and these are NOT scale factors that can be divided out. Reported A/Bs have
# clang ahead single-threaded and gcc ahead multi-threaded, which REORDERS
# builds rather than shifting them uniformly -- the same shape of hazard as
# f9694c39 parallelising CGGI but not LMKCDEY, which invalidated every
# method-vs-method conclusion drawn on the serial cells.
#
# So the regime is part of the key, not a footnote, and an unmeasured regime
# holds NO cells: gate_us returns None for every candidate there, which the
# searches already surface as "uncalibrated" rather than ranking on a guess.
# Populate one by measuring it (scripts/dse-arms/gatecost-regime.sh) and pasting
# the fitted cells in, exactly as the multi/clang table was built.
# ONE RECORD PER METHOD, in `by_method`, and it is the authority. A cell is
# measured per method, so after a one-method recalibration a table holds one
# machine's GINX beside another's AP -- which is the normal case, not the odd one,
# since `recalibrate.sh` measures GINX by default. Each record carries the pin it
# was measured at, the date, its timing count and the box; `points`, `measured`,
# `box` and `pins_by_method` are DERIVED from them by _rollup_regime below.
#
# Derived rather than written twice because both halves of that were bugs waiting
# to happen: a `points` literal is a hand-added total that goes stale the moment
# one method is re-measured, and a single `box` string over rows from two machines
# is a claim nobody can check. Nothing here is a judgement call, so nothing here
# is left to a person: apply_cells.py --update-regime writes these records.
COST_REGIMES = {
    ("multi", "libomp"): dict(
        threads=8, compiler="clang-18 (libomp)", pin=PIN_1285,
        # The cells are MEASURED at the commits in by_method and CARRIED to
        # `pin`: everything between is key layout, key generation, guards,
        # serialization or outside BinFHE, and gate time was measured across
        # each move on this box. A carry is stated rather than assumed, because
        # the alternative -- a regime whose pin silently means "measured here"
        # -- is how a scheduling commit gets carried by accident.
        carried_why="c4180d75 and 94229558 change key layout only, gate times "
                    "measured unchanged at 1 and 8 threads; 94229558 -> 12858277 "
                    "leaves the accumulator and key-switch loops unchanged (two "
                    "per-gate guards, a keygen fold, serialization, the table "
                    "itself), gate-time A/B on this box in COST_PIN_CONTAINS",
        # GINX verified at 41709fbc by the 90-row GINX maps control (+0.4..+0.8%);
        # LMKCDEY's 530 include 90 two-base map rows.
        by_method={
            "GINX":    dict(pin=PIN_2381, on=MEASURED_2381, points=330, box=COST_BOX),
            "AP":      dict(pin=PIN_2381, on="2026-09-07",  points=563, box=COST_BOX),
            "LMKCDEY": dict(pin=PIN_4170, on=MEASURED_4170, points=530, box=COST_BOX),
        },
        cells=_CELLS_MULTI_CLANG,
        map_width_share=LMKCDEY_MAP_WIDTH_SHARE,
        stale=False),
    ("single", "libomp"): dict(
        threads=1, compiler="clang-18 (libomp)", pin=PIN_A0C3,
        by_method={
            "GINX":    dict(pin=PIN_A0C3, on=MEASURED_A0C3, points=220, box=COST_BOX),
            "LMKCDEY": dict(pin=PIN_A0C3, on=MEASURED_A0C3, points=440, box=COST_BOX),
        },
        cells=_CELLS_SINGLE_CLANG,
        map_width_share=0.0,      # one thread: no team, nothing idles
        stale=False),
    ("multi", "libgomp"): dict(
        threads=8, compiler="gcc-14 (libgomp)", pin=PIN_2381,
        by_method={
            "GINX":    dict(pin=PIN_2381, on=MEASURED_2381, points=220, box=COST_BOX),
            "LMKCDEY": dict(pin=PIN_2381, on=MEASURED_2381, points=440, box=COST_BOX),
        },
        cells=_CELLS_MULTI_GCC,
        map_width_share=None,     # not measured under gcc; priced 0 and flagged
        stale=True),
    ("single", "libgomp"): dict(
        threads=1, compiler="gcc-14 (libgomp)", pin=PIN_A0C3,
        by_method={
            "GINX":    dict(pin=PIN_A0C3, on=MEASURED_A0C3, points=220, box=COST_BOX),
            "LMKCDEY": dict(pin=PIN_A0C3, on=MEASURED_A0C3, points=440, box=COST_BOX),
        },
        cells=_CELLS_SINGLE_GCC,
        map_width_share=0.0,
        stale=False),
}


def _rollup_regime(r):
    """Derive a regime's display fields from its per-method records.

    `points` is the sum of the per-method timing counts, `measured` the newest
    date among them, `pins_by_method` the pin of each, and `box` the single box
    where they agree. Where they do not, `box` names the box the most timings
    came from and says the rest are elsewhere, rather than picking one and
    implying the others.
    """
    bm = r.get("by_method") or {}
    if not bm:
        r.setdefault("points", 0)
        r.setdefault("box", COST_BOX)
        r.setdefault("measured", "?")
        return r
    r["points"] = sum(v.get("points", 0) for v in bm.values())
    r["measured"] = max(v["on"] for v in bm.values())
    r["pins_by_method"] = {k: v["pin"] for k, v in bm.items()}
    boxes = {v["box"] for v in bm.values()}
    if len(boxes) == 1:
        r["box"] = boxes.pop()
    else:
        # Deliberately NOT the box with the most timings. Naming one machine for
        # a table holding two reads as a fact about all the rows, and the method
        # a default search prices is not always the one with the most timings.
        r["box"] = "two or more machines (see `boxes:` below)"
    return r


for _r in COST_REGIMES.values():
    _rollup_regime(_r)
DEFAULT_REGIME = ("multi", "libomp")
THREAD_MODES = ("single", "multi")
OMP_RUNTIMES = ("libomp", "libgomp")
# Back-compat: the old labels were compiler names, and they map 1:1 onto the
# runtime a default build links. Accepted as aliases so existing invocations
# and saved commands keep working.
COMPILER_ALIASES = {"clang": "libomp", "gcc": "libgomp"}
COMPILERS = OMP_RUNTIMES


def _regime_from_env():
    """Regime from DSE_COST_THREADS / DSE_COST_COMPILER, else the default."""
    import os
    tm = os.environ.get("DSE_COST_THREADS", DEFAULT_REGIME[0]).lower()
    cc = os.environ.get("DSE_COST_COMPILER", DEFAULT_REGIME[1]).lower()
    cc = COMPILER_ALIASES.get(cc, cc)
    if tm not in THREAD_MODES or cc not in OMP_RUNTIMES:
        return DEFAULT_REGIME
    return (tm, cc)


ACTIVE_REGIME = _regime_from_env()
# The active cells. Rebound by set_cost_regime; every consumer reads it through
# the module (m.GATE_COST) rather than importing the name, so rebinding reaches
# all of them.
GATE_COST = COST_REGIMES[ACTIVE_REGIME]["cells"]


def set_cost_regime(threads_mode=None, compiler=None):
    """Select the (thread mode, compiler) the cost cells should describe.

    Returns the active regime. Raises on an unknown one rather than silently
    falling back, because a ranking priced on the wrong regime looks entirely
    normal.
    """
    global ACTIVE_REGIME, GATE_COST
    tm = (threads_mode or ACTIVE_REGIME[0]).lower()
    cc = (compiler or ACTIVE_REGIME[1]).lower()
    cc = COMPILER_ALIASES.get(cc, cc)
    if (tm, cc) not in COST_REGIMES:
        raise ValueError("unknown cost regime %r; have %s"
                         % ((tm, cc), sorted(COST_REGIMES)))
    ACTIVE_REGIME = (tm, cc)
    GATE_COST = COST_REGIMES[ACTIVE_REGIME]["cells"]
    return ACTIVE_REGIME


def normalise_regime(regime):
    """Accept the old compiler labels anywhere a regime is named.

    An alias that only works in the setter is worse than no alias: it makes the
    other entry points raise KeyError on input the setter accepted.
    """
    tm, rt = regime
    return (tm.lower(), COMPILER_ALIASES.get(rt.lower(), rt.lower()))


def regime_is_measured(regime=None):
    key = normalise_regime(regime) if regime else ACTIVE_REGIME
    if key not in COST_REGIMES:
        raise ValueError("unknown cost regime %r; have %s"
                         % (key, sorted(COST_REGIMES)))
    return bool(COST_REGIMES[key]["cells"])


def measured_regimes():
    return sorted(k for k, v in COST_REGIMES.items() if v["cells"])


def gate_us(gadget_map, log_q_big, N, method="GINX", word_size=32, autokeys=10,
            base_r=None, q=None):
    """Predicted per-gate microseconds, or None where uncalibrated.

        gate_us = c0 + c1*n + c2*gate_work  [+ c3*N/autokeys for LMKCDEY]

    n comes from the gadget map, whose counts sum to it by construction.

    The fourth term is LMKCDEY's automorphism work, and it is not optional. Its
    size scales with N while the external products scale with n, so its SHARE --
    and hence the sensitivity to numAutoKeys -- grows with N/n. Measured, gate time
    at w=40 relative to w=2:

        N/n = 1.45  ->  0.887        N/n = 2.9  ->  0.678
        N/n = 4.0   ->  0.638        N/n = 8.0  ->  0.407

    Without the term, LMKCDEY's within-cell residual is 25-47%; with it, 9-12%.

    c3 is None where the dependence is not measured (N=512, where every timing
    holds w=10) or structurally absent (GINX has no automorphism keys at all). For
    LMKCDEY with c3 None this returns None for any w != 10 rather than pretending
    the fit extends there.

    Returns None rather than guessing anywhere else too: a cost model that
    extrapolated across method or ring dimension would silently reorder the
    frontier, and ranking is the only thing this number is for.

    AP takes base_r and q, because its accumulator work carries the same
    digitsR*(1-1/baseR) factor its noise does. A regime whose arm has not
    measured AP holds no AP cells, so this returns None there -- the honest
    answer, and the reason adding a method does not silently reprice one.
    """
    key = (method, N, word_size)
    if key not in GATE_COST:
        return None
    c0, c1, c2, c3 = GATE_COST[key]
    n = sum(gadget_map.values())
    us = c0 + c1 * n + c2 * gate_work(gadget_map, log_q_big, method=method,
                                      base_r=base_r, q=q)
    if method == "AP":
        # For AP the fourth coefficient is the per-external-product fixed cost,
        # against a feature of n * products. (For LMKCDEY it is the automorphism
        # term, c3*N/w. The two never collide because a cell is keyed by method.)
        if c3 is None:
            return None
        return us + c3 * ap_products_per_gate(n, q, base_r)
    if method != "LMKCDEY":
        return us
    if c3 is None:
        return us if autokeys == 10 else None
    if autokeys < 1:
        raise ValueError("LMKCDEY needs numAutoKeys >= 1, got %r" % autokeys)
    us += c3 * float(N) / autokeys
    # Two-base maps pay for the widest team width in a threaded regime
    # (map_width_us); the share is the regime's, measured or 0/None.
    us += map_width_us(gadget_map, log_q_big, c2,
                       COST_REGIMES[ACTIVE_REGIME].get("map_width_share"))
    return us


def gate_work(gadget_map, log_q_big, method="GINX", base_r=None, q=None):
    """Accumulator work per gate, in RGSW row-operations: SUM_j count_j * digitsG2_j.

    Gate time is c(N, method, threads) * this * a word-size factor. `c` is left
    to calibration and is single-threaded unless calibrated per thread regime:
    BinFHE gates scale badly with cores (icelake 1 vs 36 threads: 93.9 -> 77.3 ms,
    only 1.21x).

    ON THE PARALLEL WIDTH, checked against the source at a0c3f2cd rather than
    assumed, and it has moved twice since the cells' pin:

      GINX     `AddToAccCGGI` / `AddToAccCGGI32` (rgsw-acc-cggi.cpp:343,186): one
               `omp parallel` of width min(threads, max(digitsG2, 4)) holding an
               `omp barrier`, an `omp single` digit decomposition, and `omp for`
               loops of width 2, digitsG2 and 4. Unchanged by a0c3f2cd.
      LMKCDEY  had NO accumulator region at 1c3e83ec (its gate parallelism was
               the key switch, lwe-pke.cpp:369,544). 0c75c2ec fused one:
               `AddToAccNoMonomial` (rgsw-acc-common.h:141,365), same shape as
               CGGI's but with a width-2 product loop, plus `AutomorphismKeySwitch`
               (:261), width max(digitsG, 4). a0c3f2cd then capped the first at
               max(digitsG2/2, 4), made its decomposition parallel when digitsG2
               >= 8 and 4+ threads share the region, and blocked the 32-bit inner
               product across the region instead of across the two columns. The
               automorphism region kept its cap.

    None of that changes the FORM here -- work is still linear in digitsG2 per
    row-operation and the coefficients absorb the scheduling -- but it does mean
    the per-digit cost under the cap can kink at digitsG2 == 8 for LMKCDEY in a
    multi-thread regime, which is a residual pattern to look for in the fit, not
    something to model until it is seen. The coupling that matters for THIS
    search: reducing digitsG is its main speed lever and also narrows every one
    of those regions, so a 1-thread calibration systematically overstates the
    many-core gain of coarse-gadget candidates.
    """
    work = sum(c * digits_g2(digits_for_base(log_q_big, b)) for b, c in gadget_map.items())
    if method == "AP":
        # One row-operation per external product, and AP does
        # digitsR*(1-1/baseR) of them per coefficient where GINX does a fixed
        # number the constant absorbs. A skipped zero digit costs no time and no
        # noise, so this is the SAME factor accumulator_var_at_Q applies -- which
        # is what makes baseR a coherent search dimension rather than two
        # independent guesses.
        if base_r is None or q is None:
            raise ValueError("AP gate work needs base_r and q")
        work *= ap_products_per_coeff(q, base_r)
    return work
