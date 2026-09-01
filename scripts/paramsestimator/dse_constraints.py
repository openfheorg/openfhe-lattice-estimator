#!/usr/bin/python

'''Closed-form constraints that prune the candidate grid before anything is measured.

All of these are microseconds per candidate and none needs OpenFHE or SageMath,
so they run first and cut the space the expensive stages ever see.

Each constraint here exists because something real went wrong without it, and the
reason is recorded next to the check rather than in a commit message.
'''

from math import log2

from dse_model import digits_for_base, digits_g2

# MAX_MODULUS_SIZE in core/include/math/hal/basicint.h, per NATIVEINT.
MAX_MODULUS_BITS = {32: 28, 64: 60}

# Native word bits available to the gadget decomposition. rgsw-acc-common.h:51
# states the requirement as `digitsG*gBits + 1 <= word bits`; the +1 is the
# excess-H bias, which must not carry out of the top digit window. A 28-bit Q at
# gBits 5 needs d=6, and 6*5 + 1 = 31 <= 32, one bit spare.
#
# This was spelled `digitsG*gBits < {32: 31, 64: 63}` -- one bit STRICTER than the
# library. Nothing in the enumerated grid moved, because every base it wrongly
# refused (base 2^21 at logQ 43-54, d=3) is dominated by a smaller base with the
# same digit count and so discarded anyway. It is corrected regardless: the value
# is now the word width and the comparison is the library's own inequality, so the
# two cannot drift apart the way the comparison criterion did.
GADGET_WORD_BITS = {32: 32, 64: 64}


# GINX/CGGI can only represent a TERNARY secret. RingGSWAccumulatorCGGI::KeyGenAcc
# (rgsw-acc-cggi.cpp:40) stores the LWE secret as an indicator pair,
#
#     ek00[i] = RGSW(s_i ==  1)        ek01[i] = RGSW(s_i == -1)
#
# so every coefficient with |s_i| >= 2 is encoded {0, 0} -- indistinguishable from
# s_i == 0. 63.7% of a stddev-3.19 discrete Gaussian's coefficients are outside
# {-1, 0, 1}, so the bootstrapping key encodes a different secret than the one the
# ciphertext was encrypted under.
#
# This is a CORRECTNESS constraint masquerading as a parameter choice, and it is
# invisible to every noise-based metric. Measured at n=64, q=1024, N=1024,
# logQ=25: GINX + GAUSSIAN gave 202 failures in 400 gates -- a coin flip -- with
# sigma 12.937 against the ternary configuration's 10.314. A search ranking
# candidates on sigma would have preferred neither and rejected neither.
#
# AP (rgsw-acc-dm.cpp:39) and LMKCDEY (rgsw-acc-lmkcdey.cpp) both use the centered
# value and handle any secret. OpenFHE's isMethodCompatible() does not catch the
# pairing because it inspects the BINFHE_PARAMSET enum, not keyDist -- and every
# Gaussian set it ships happens to be LMKCDEY, so nothing exercises it.
TERNARY_ONLY_METHODS = ("GINX",)


# LMKCDEY's automorphism keys are stored in a vector sized by the LWE DIMENSION,
# not by numAutoKeys. RingGSWAccumulatorLMKCDEY::KeyGenAcc
# (rgsw-acc-lmkcdey.cpp:37) allocates
#
#     RingGSWACCKey ek = std::make_shared<RingGSWACCKeyImpl>(1, 2, n);
#
# so (*ek)[0][1] holds exactly n elements, then writes indices 0 .. numAutoKeys:
#
#     (*ek)[0][1][0] = KeyGenAuto(...);
#     for (uint32_t i = 1; i <= numAutoKeys; ++i)
#         (*ek)[0][1][i] = KeyGenAuto(...);
#
# The requirement is numAutoKeys <= n - 1, and nothing enforces it.
# RingGSWCryptoParams only rejects numAutoKeys == 0, and it cannot check this one
# because it does not know n -- the LWE dimension lives in LWECryptoParams. The
# source comment ("allocates (n - w) more memory for pointer") shows the author
# assumed w < n without asserting it.
#
# std::vector::operator[] is unchecked, so exceeding it is an out-of-bounds write
# to a shared_ptr slot -- undefined behaviour that happens to segfault. Measured
# on the pinned build: n=32 works to w=31 and SIGSEGVs at 32; n=64 works to 63 and
# SIGSEGVs at 64. Exactly the allocation bound.
#
# This matters for the search because raising numAutoKeys is a cheap way to buy
# noise margin, so an enumerator has every incentive to push it -- straight into a
# crash at small n.
# The library's own cap on a 32-bit modulus: exact arithmetic on its 32-bit
# kernels stops here, so `LWESwitchingKey32Impl::Fits` refuses above it.
MAX_MODULUS_SIZE32 = 28


def ct_modulus_divides_2n(q, N):
    """True if q | 2N, which BootstrapGateCore requires since 09913224.

    It THROWS otherwise ("Ciphertext modulus must divide 2N"), where before it
    looped forever on a modulus above 2N. Every geometry the search emits happens
    to satisfy it -- q is N or 2N by construction -- so this is a guard against a
    future grid, and the reason to encode it rather than rely on that is that the
    failure it prevents is now an exception in the library rather than a slow run.
    """
    q, N = int(q), int(N)
    return q > 0 and (2 * N) % q == 0


def autokeys_ok(method, autokeys, n):
    """False where numAutoKeys would index past LMKCDEY's automorphism-key vector."""
    if method != "LMKCDEY":
        return True
    return 1 <= autokeys <= n - 1


def ap_base_r_ok(q, base_r):
    """False where AP's refresh-key base cannot describe a usable key.

    baseR < 2 has no digit expansion at all. baseR > q is not illegal but is
    pointless and ruinous: the expansion collapses to one digit whose value is
    a_i itself, so the key holds q-1 refresh keys per coefficient -- gigabytes
    for nothing, since a single digit means no sparsity to reclaim either.
    """
    return 2 <= int(base_r) <= int(q)


def method_has_refresh_base(method):
    """True for methods whose refresh key is indexed by a base of its own.

    AP alone: rgsw-acc-dm.cpp allocates (n, baseR, digitsR), where GINX and
    LMKCDEY index by the gadget only. Whether a method carries baseR decides
    whether baseR is a search dimension, so it is a predicate rather than an
    `if method == "AP"` scattered over the callers.
    """
    return method == "AP"


def ap_undominated_base_r(q, bases=None):
    """The refresh-key bases worth enumerating for AP at this q.

    Raising baseR buys LESS noise and LESS gate time together, because both
    follow the external-product count, and pays in key material: (baseR - 1) *
    digitsR refresh keys per coefficient. So it is a genuine two-axis trade for
    the frontier to resolve.

    MEASURED, no base is dominated. An earlier approximation of the product count
    predicted that a base sharing a digit count with a smaller one would be worse
    on every axis at once -- 32 behind 16, 128 behind 64 -- and the designed arm
    refuted it: counted exactly, the product count is monotone decreasing in
    baseR (dse_model.ap_products_per_coeff). This function is kept because it is
    DERIVED from that count rather than asserting anything about its shape, so it
    prunes if a future q or base grid does produce a dominated base, and returns
    everything when none is.
    """
    import dse_model as m
    bases = tuple(bases) if bases else (2, 4, 8, 16, 32, 64, 128, 256)
    usable = [b for b in bases if ap_base_r_ok(q, b)]
    def cost(b):
        d = m.digits_for_base(int(q).bit_length() - 1, b, modulus=int(q))
        return (m.ap_products_per_coeff(q, b), (b - 1) * d)
    out = []
    for b in usable:
        pb, kb = cost(b)
        dominated = any(
            (lambda po, ko: (po <= pb and ko <= kb) and (po < pb or ko < kb))(*cost(o))
            for o in usable if o != b)
        if not dominated:
            out.append(b)
    return tuple(sorted(out))


def method_keydist_ok(method, key_dist):
    """False for a (method, keyDist) pairing that computes the wrong function."""
    return not (method in TERNARY_ONLY_METHODS and key_dist != "UNIFORM_TERNARY")


# Runtime-NS32 hybrid qualification (reviewer Addendum 28, 2026-09-02).
#
# A BinFHE bootstrapping key can be held internally at 32 bits inside a
# NATIVE_SIZE=64 build: RGSW eval keys and monomials as NativePoly32, the whole
# accumulator at 32-bit width with a lazy inner product, narrowed/widened at the
# gate boundary. Selected by BTKeyGen(sk, mode, internal32) -- opt-in when this
# was written, the DEFAULT since OpenFHE 9e8045db -- bit-identical to the 64-bit
# path, and each key falls back to its 64-bit form when its own moduli do not
# qualify.
#
# WHY THIS CHANGES THE SEARCH: word size stops being a BUILD decision and becomes
# a PER-CANDIDATE one. The whole "NS32-only, and on a default build the same change
# is a regression" framing (this repo's Addenda 19 and 24) inverts for qualifying
# candidates -- they get the 32-bit gate on the build everyone already has.
# Measured 1.78-2.04x at 8 threads on qualifying shipped sets, against this repo's
# independently refitted 1.864x. The two agree.
#
# Encoded verbatim rather than approximated, because the reviewer asked for that
# and because an approximation here silently changes which candidates collect the
# prize:
#
#   MSB(Q) <= 28                        and method is GINX
#   digitsG * gBits + 1 <= 32           excess-H headroom, per gadget base
#   digitsG2 * (Q-1)^2  < 2^64          lazy-accumulator headroom
#
# The third never binds at Q <= 2^28 (it would need digitsG2 > 256) but is encoded
# anyway so the predicate stays correct if the Q bound ever moves.
#
# CAVEAT recorded with it: as of this writing the library's own Fits() checks only
# the first two conditions and the rest THROW out of KeyGen rather than falling
# back (reviewer workplan item 29). So this predicate is the authority, not the
# code, until that lands -- which is the opposite of the usual rule here and is
# why it is written out in full.
# MAX_MODULUS_SIZE32 in core/include/math/hal/basicint.h:38.
HYBRID_MAX_Q_BITS = 28
# GINX, AP and LMKCDEY as of 1b8648e9 -- an earlier revision of this had GINX only,
# which was correct for f56c301b and wrong one commit later. AP and LMKCDEY gained
# the 32-bit path in Track A items 5+6 and roughly DOUBLE on it (LMKCDEY 1.99x,
# AP 2.12x), which matters because LMKCDEY gained only 1.06-1.11x from the
# accumulator parallelisation -- so excluding it here silently reopened the same
# method-comparison error twice.
HYBRID_METHODS = ("GINX", "AP", "LMKCDEY")


def hybrid_ns32_ok(log_q_big, gadget_map, method, default_base=None):
    """True if this candidate can run the 32-bit accumulator inside an NS64 build.

    Transcribed from `RingGSWACCKey32Impl::Fits` (rgsw-acckey32.cpp:43), which is
    the authority. Deliberately mirrors its structure rather than paraphrasing it,
    including the integer form of the headroom test -- the library writes
    `d2 <= UINT64_MAX / ((Q-1)*(Q-1))` rather than `d2*(Q-1)^2 < 2^64` to avoid the
    overflow the test is checking for.

    `default_base` is the paramset's own baseG. It matters ONLY for LMKCDEY, whose
    automorphism key switch uses the default base regardless of the per-dimension
    map -- so a multi-base LMKCDEY candidate whose map all qualifies can still be
    refused on a default base that does not. Omitting it for LMKCDEY leaves that
    check unmade, so it is required there rather than silently skipped.
    """
    if method not in HYBRID_METHODS:
        return False
    if int(log_q_big) > HYBRID_MAX_Q_BITS:
        return False
    q_big = 1 << int(log_q_big)

    def ok(base):
        # The excess-H test is the same inequality the general path needs, so it
        # is CALLED here rather than restated -- against 32 because the hybrid
        # narrows the accumulator to a 32-bit word inside an NS64 build. Two
        # spellings of one rule is exactly how the comparison criterion came to
        # differ between its two callers.
        if not gadget_width_ok(log_q_big, base, 32):
            return False
        d2 = digits_g2(digits_for_base(log_q_big, base))
        return d2 <= ((1 << 64) - 1) // ((q_big - 1) * (q_big - 1))

    # The library falls back to the default base params when the per-index map is
    # empty; a map is always populated here, so that branch is the `not gadget_map`
    # case rather than dead code.
    if not gadget_map:
        if default_base is None:
            raise ValueError("hybrid_ns32_ok: empty gadget map needs default_base")
        return ok(default_base)
    for base in gadget_map:
        if not ok(base):
            return False
    if method == "LMKCDEY":
        if default_base is None:
            raise ValueError(
                "hybrid_ns32_ok: LMKCDEY needs default_base -- its automorphism "
                "key switch uses the default base regardless of the per-index map")
        if not ok(default_base):
            return False
    return True


def hybrid_switch32_ok(N, q_ks, base_ks):
    """True if the LWE SWITCHING key narrows to 32 bits inside an NS64 build.

    Transcribed from `LWESwitchingKey32Impl::Fits` (lwe-keyswitchkey32.h:63). It
    is a DIFFERENT predicate from the refresh key's: it constrains q_KS and the
    stored row count and knows nothing about Q or the gadget at all.

    THE TWO KEYS NARROW INDEPENDENTLY (binfhe-base-scheme.cpp:66 and :81 are
    separate `if`s), so a set whose accumulator stays 64-bit can still hold a
    32-bit switching key -- and every shipped set at logQ > 28 does, because q_KS
    is 2^14..2^18 whatever Q is. Pricing both keys at one word width therefore
    overstates such a set's key material by up to 1.75x, since the switching key
    is the larger of the two at these dimensions (616 of 822 MiB at STD192).
    That error is what LIB-61 caught in DSE-59's key column.
    """
    q_ks = int(q_ks)
    # MAX_MODULUS_SIZE32 (basicint.h), not the 32 of the storage word. Generation
    # runs on 32-bit kernels that are exact only to 28 bits, so 09913224 capped
    # the predicate there; the library's own test pins the boundary at
    # 2^28 - 57 fitting and 2^28 not. A model that still said 32 would price a
    # 2^28..2^32 switching key at half its bytes, and since the key cap now
    # SELECTS on key material that is a pick-changing error, not a cosmetic one.
    if q_ks.bit_length() > MAX_MODULUS_SIZE32:
        return False
    import dse_model as m
    rows = int(N) * m.digits_for_base(q_ks.bit_length() - 1, int(base_ks), modulus=q_ks)
    return rows <= ((1 << 64) - 1) // q_ks


def modulus_fits_word(log_q_big, word_size):
    return int(log_q_big) <= MAX_MODULUS_BITS[word_size]


def gadget_width_ok(log_q_big, base_g, word_size):
    """digitsG*gBits + 1 <= word bits, per rgsw-acc-common.h:51.

    The single spelling of the excess-H headroom rule: the general path calls it
    with the build's word size, and hybrid_ns32_ok calls it with 32 because the
    hybrid narrows the accumulator to a 32-bit word whatever the build is.
    """
    gbits = int(base_g).bit_length() - 1
    return (digits_for_base(log_q_big, base_g) * gbits + 1
            <= GADGET_WORD_BITS[word_size])


def carry_precondition_ok(q_big, base_g, digits=None):
    """Q/2 + H < baseG^digitsG, with H the signed-decomposition bias.

        H = (baseG/2) * (baseG^digitsG - 1) / (baseG - 1)

    This is NOT implied by digitsG = ceil(log_baseG Q), which is exactly why it
    has to be asserted separately. When it fails, SignedDigitDecompose masks the
    top window and discards a carry, and the decomposition reconstructs short by
    baseG^digitsG mod Q. The pinned build carries the fix, but a search that
    varies Q and baseG freely will generate violating pairs, and against an
    unfixed library they cost up to 74 bits of log2Pf while looking healthy.

    Takes the actual modulus Q, not its bit count: the margin here is often a
    single bit, so rounding Q to 2^bits would change the answer.
    """
    b = int(base_g)
    d = digits if digits is not None else digits_for_base(int(q_big).bit_length(), b)
    bd = b ** d
    H = (b // 2) * (bd - 1) // (b - 1)
    return (int(q_big) // 2 + H) < bd


def undominated_bases(log_q_big, word_size, max_bits=None):
    """The only gadget bases worth enumerating at this Q.

    A base yielding the same digit count as a smaller one is strictly worse: same
    work, same key rows, larger noise. So for each achievable digit count keep the
    SMALLEST base achieving it. The rule is coefficient-wise, which is what lets
    it hold inside a gadget map too.

    Returns {digits: base}.
    """
    limit = max_bits if max_bits is not None else GADGET_WORD_BITS[word_size]
    best = {}
    for gbits in range(1, limit):
        base = 1 << gbits
        if not gadget_width_ok(log_q_big, base, word_size):
            continue
        d = digits_for_base(log_q_big, base)
        if d < 2:
            continue
        if (d not in best) or (base < best[d]):
            best[d] = base
    return best


def map_candidates(log_q_big, word_size):
    """Pairs of undominated bases a two-base gadget map could actually use.

    The pre-filter the plan asks for: if no two undominated bases with a genuine
    digit-count gap exist at this Q, the whole map combinatorial explosion
    collapses to nothing for that (N, Q) and can be skipped in microseconds.
    """
    bases = undominated_bases(log_q_big, word_size)
    ds = sorted(bases)
    return [(bases[a], bases[b]) for i, a in enumerate(ds) for b in ds[i + 1:]]


def alignment(log_q_big, base_g, q_ks=None, base_ks=None):
    """Which cleanliness tests a candidate passes, so bias is applied not omitted.

    A digit position whose range the modulus does not fill re-uses one fixed key
    row every time, contributing a per-key OFFSET rather than more Gaussians.
    """
    out = {}
    d = digits_for_base(log_q_big, base_g)
    out['gadget_clean'] = (base_g ** d) == (1 << int(log_q_big))
    if (q_ks is not None) and (base_ks is not None):
        d_ks = digits_for_base(log2(q_ks), base_ks, modulus=q_ks)
        out['keyswitch_clean'] = (base_ks ** d_ks) == int(q_ks)
    return out
