#!/usr/bin/python

'''Phase 3: enumerate the grid, prune it in closed form, predict, and report a frontier.

The whole point of the predict-then-verify design is that this stage costs
microseconds per candidate and OpenFHE is never invoked. Thousands of candidates
per second, so the expensive stages only ever see the few that survive.

    > python3 scripts/paramsestimator/dse_enumerate.py --target -64
    > python3 scripts/paramsestimator/dse_enumerate.py --target -128 --method LMKCDEY
    > python3 scripts/paramsestimator/dse_enumerate.py --target -64 --explain

Four things this does that the current search does not
-----------------------------------------------------
1. It REFUSES rather than guesses. A candidate the model cannot predict is
   reported as unrankable, not scored on a number the model cannot produce. Every
   refusal is counted and attributed, so a search that prunes everything says why.

2. It scores margin against the NEXT DISCRETE STEP, not against zero. Failure
   margin is only spendable where a step exists -- a digitsG tier for the whole
   map OR for one grid-split of the coefficients (a two-base map is exactly a
   partial step), a d_KS tier, the word-size cliff. A candidate with 37 bits of
   surplus and nowhere to put it is not better than one with 3; scoring against
   zero penalises exactly the candidates that have run out of places to spend.

3. It reports the FRONTIER, not a winner. A set that is 5% slower and 4x smaller
   is often the one you want, and a single-axis rule cannot express that. Key
   material varied 264 MiB to 2283 MiB across winners in one sweep -- that is a
   deployment-defining spread that "fastest that beats the target" never surfaces.

4. It requires margin to EXCEED UNCERTAINTY. A 1.7-bit margin at +-0.8 bits of
   model uncertainty is not a pass. The uncertainty is per candidate, because it
   depends on how that candidate divides its variance between terms with different
   bands -- the same accumulator band is +-1.3 bits at 5% share and +-26 bits at
   100%.

What this stage cannot do
------------------------
Security is not priced here. It needs the lattice estimator and therefore Sage,
and it is priced once per distinct (N, logQ, keyDist) tuple rather than per
candidate -- there are far fewer of those than candidates. `security_hook` is
where that plugs in; without one, every candidate is passed through and the report
says so.
'''

from math import log2, sqrt

import argparse
import sys

import dse_model as m
import dse_constraints as c
import dse_security as sec
from paramstable import max_logq


# --------------------------------------------------------------------------
# the grid
# --------------------------------------------------------------------------

# Defaults chosen to span what OpenFHE actually ships rather than to be tidy.
# COUNTED from binfhecontext.cpp, all 45 sets -- and counted CORRECTLY, which took
# two attempts:
#
#     N        {512: 2, 1024: 18, 2048: 25}          all 45 sets
#     q        {512: 2, 1024: 4, 2048: 26, 4096: 13}
#     logQ     25,26,27,28,29,34,36,37,39,50
#     baseKS   {25: 3, 32: 14, 64: 27, 128: 1}
#     log2 qKS 14..18, plus 3 sets at modKS=PRIME (q_KS := Q)
#
# (Those figures are counted, then re-counted after the correction below; the
# first version of this very comment had four of them wrong.)
#
# **The second column of that table is `cyclOrder`, not N.** `binfhecontext.cpp`
# does `auto ringDim = params.cyclOrder >> 1;`, so reading the column as N doubles
# every ring dimension. Doing that produced a confident and completely wrong
# conclusion -- that 25 of 45 sets used N=4096 and the grid was missing 60% of
# them, and downstream that every shipped set carried a 2x oversized ring. The
# ORIGINAL tuple (512, 1024, 2048) was right, and its original comment was right.
#
# The tell was available and ignored: EVERY one of the 45 sets has q in {N, 2N}
# exactly -- 27 at q=2N and 18 at q=N, no exceptions -- which is precisely
# OpenFHE's own `q = arbFunc ? ringDim : 2 * ringDim`. Under the doubled reading
# that structure became "q in {N/2, N}", a relationship the library never states
# anywhere, and I built on it instead of asking why it looked unfamiliar.
#
# logQ DOES run to 51: `numberBits` is its own column and STD128Q_4 ships 50 (at
# cyclOrder 4096, so N=2048). That part of the correction stands.
#
# Two shipped values are DELIBERATELY excluded, stated here so it is a choice and
# not another oversight: q=512 (TOY, TOY_MULTI_BASE) and baseKS=25 (those two plus
# SIGNED_MOD_TEST). All three are toy/test sets. baseKS=25 is also the only
# non-power-of-two base anything ships, and `digits_for_base` handles it -- so if a
# production set ever wants one, the grid is what needs widening, not the model.
DEFAULT_GRID = dict(
    N=(512, 1024, 2048),
    log_q_big=tuple(range(21, 52)),
    q=(1024, 2048, 4096),
    # Extended 2026-09-05 from 14..24 and (32, 64, 128): the STD128 frontier's
    # fastest survivor sat at qKS = 2^14 AND bKS = 128, i.e. on two grid edges at
    # once, which is the one pattern an exhaustive search cannot distinguish from
    # an optimum. candidates() already drops q_ks <= q, so the bottom of this range
    # only reaches candidates with q < 4096; the key-switch saturation gate and the
    # security curves decide the rest. bKS need not be a power of two (shipped
    # STD192/STD256 use 25), but every shipped set uses one and the noise model is
    # calibrated on them.
    log_q_ks=tuple(range(12, 25)),
    base_ks=(32, 64, 128, 256, 512),
    # Dimensions to enumerate IN ADDITION to the n_step grid: the security
    # boundaries and shipped n values the cache has priced for the level being
    # searched (dse_security.extra_dims). run() fills this from the cache; a
    # fixed-step grid straddles the cheapest secure n by construction.
    extra_n=(),
    # LMKCDEY only, and the values its cost model is measured at. GINX and AP have
    # no automorphism keys, so for those a single value is enumerated and ignored.
    # It has to be a grid dimension rather than a constant: it moves accumulator
    # variance as 1/w (a 4.3x swing over w=2..20), it moves gate time by up to 59%
    # depending on N/n, and it moves bootstrapping-key size. Pinning it at 10 --
    # which every previous calibration did -- hides all three.
    autokeys=(2, 5, 10, 20, 40),
    # Interior split points per two-base map. 7 gives eighths; 0 and n are the
    # single-base maps and are enumerated separately.
    splits=7,
    n_step=32,
    sigma=3.19,
    inputs=2,
)


# The secret distributions a candidate may carry, and the estimator curve each
# prices on. GAUSSIAN maps to `error` because that is the estimator's name for a
# secret drawn from the error distribution, which is what OpenFHE's GAUSSIAN
# keyDist means (stdDev 3.19).
KEY_DISTS = ("UNIFORM_TERNARY", "GAUSSIAN")
CURVE_FOR_DIST = {"UNIFORM_TERNARY": "ternary", "GAUSSIAN": "error"}


def n_range(q, N, step=32):
    """Plausible LWE dimensions for a given (q, N).

    Bounded above by N because key switching maps N -> n; bounded below by
    security, which this stage does not price, so the floor is deliberately
    permissive and the security hook is what tightens it.
    """
    return range(step, N + 1, step)


def candidates(grid=None, method="GINX", key_dist=None,
               word_size=None, multi_base=False, autokeys_grid=False,
               dropped_ks=(0,)):
    """Yield every grid point, before any pruning.

    word_size=None means "try both", which is the honest default: the 32-bit path
    is roughly 2x faster on gate time and caps the modulus at 28 bits, and which
    side of that trade wins is a search result rather than an input.

    key_dist=None likewise means "every distribution this method can represent",
    and for the same reason: it is a TRADE, not a preference. A Gaussian secret
    multiplies both modulus-switch rounding terms by 15 (Var(s)/12 goes from 2/3
    to 3.19^2), but it also prices on the estimator's `error` curve, which admits
    a larger modulus at the same dimension -- so it buys security headroom with
    noise. Two shipped sets take that trade (STD192_LMKCDEY and STD192Q_LMKCDEY,
    at n = 716 and 778 where the ternary sets need 821 and 890), which is exactly
    the sort of thing a search should be able to find rather than be told.

    GINX is the one method with no choice here: it stores the secret as an
    indicator pair over {-1,0,+1}, so a Gaussian secret makes it compute the wrong
    function (dse_constraints.method_keydist_ok).
    """
    g = dict(DEFAULT_GRID, **(grid or {}))
    words = (32, 64) if word_size is None else (word_size,)
    dists = ((key_dist,) if key_dist else
             tuple(d for d in KEY_DISTS if c.method_keydist_ok(method, d)))
    for N in g['N']:
        for lqb in g['log_q_big']:
            for w in words:
                if not c.modulus_fits_word(lqb, w):
                    continue
                # .values(): undominated_bases returns {digits: base}. Iterating it
                # directly yields DIGIT COUNTS, which as bases give a plausible-
                # looking but entirely wrong grid -- it silently dropped 2^7 and
                # 2^9, i.e. every base the shipped sets actually use, and left the
                # frontier's fast end to bases of 4 and 8.
                bases = sorted(c.undominated_bases(lqb, w).values())
                maps = [{b: None} for b in bases]
                # Two-base maps are NOT enumerated here by default. They multiply
                # the gadget-map space by 28.2x -- a 5-hour table search becomes
                # 146 hours -- and they buy nothing a grid sweep is needed for: at
                # fixed everything-else, 0 of 252 admissible two-base maps were
                # both faster and better on log2Pf than the single base, in each of
                # two cells checked. OpenFHE's own two-base sets are interpolation
                # points too, sitting between their single-base endpoints on both
                # axes. So the map is a LOCAL move along a straight trade, and
                # `refine_maps` makes that move from every frontier point instead,
                # for seconds rather than days. multi_base=True restores the
                # exhaustive sweep, which is how you check that staging loses
                # nothing.
                if multi_base:
                    maps += [dict.fromkeys(pair) for pair in c.map_candidates(lqb, w)]
                for q in g['q']:
                    if q > 2 * N:
                        continue                      # q | 2N is required by the embedding
                    for lqks in g['log_q_ks']:
                        q_ks = 1 << lqks
                        if q_ks <= q:
                            continue
                        # numAutoKeys is LMKCDEY's alone: GINX and AP have no
                        # automorphism keys, so enumerating more than one value for
                        # them would multiply the grid for nothing.
                        # ONE value, not the grid. numAutoKeys improves noise
                        # AND gate time together and costs only key material, so
                        # its optimum is the largest priced value rather than an
                        # interior point -- see m.max_priced_autokeys. Enumerating
                        # five values multiplied LMKCDEY's grid by 5 to rediscover
                        # the ceiling every time: all 106 picks of the 2026-09-08
                        # table came back at 40, the grid maximum.
                        aks = (g['autokeys'] if (autokeys_grid and method == "LMKCDEY")
                               else (m.max_priced_autokeys(N, w, method, g['autokeys']),))
                        # AP's refresh key is indexed by a base of its own, and
                        # only it has one. Enumerate the UNDOMINATED bases at
                        # this q: digitsR is a step function of baseR, so a base
                        # sharing a digit count with a smaller one is worse on
                        # noise, time and key size at once (dse_constraints).
                        # The set is q-dependent, hence inside this loop.
                        brs = (c.ap_undominated_base_r(q, g.get('base_r'))
                               if c.method_has_refresh_base(method) else (None,))
                        for bks in g['base_ks']:
                            for gm_shape in maps:
                                for ak, br, kd in ((a, b, d) for a in aks
                                                   for b in brs for d in dists):
                                    for n in sorted(set(n_range(q, N, g['n_step']))
                                                    | {x for x in g.get('extra_n', ()) if g['n_step'] <= x <= N}):
                                        splits = ([0] if len(gm_shape) == 1
                                                  else split_counts(n, g['splits']))
                                        # An approximate key-switching
                                        # decomposition (droppedDigitsKS) is held
                                        # at 0 unless asked for: it trades key
                                        # material for key-switch noise through a
                                        # term no measurement has confirmed, and
                                        # `b_KS` already trades the same two axes
                                        # with a measured term.
                                        d_ks = m.digits_for_base(m.log2(q_ks), bks, modulus=q_ks)
                                        for sc in splits:
                                            for dks in (x for x in dropped_ks if 0 <= x < d_ks):
                                                yield dict(N=N, n=n, q=q, log_q_big=lqb,
                                                           q_ks=q_ks, base_ks=bks,
                                                           sigma=g['sigma'],
                                                           inputs=g['inputs'], word_size=w,
                                                           method=method, key_dist=kd,
                                                           autokeys=ak, split_count=sc,
                                                           base_r=br,
                                                           dropped_digits_ks=dks,
                                                           gadget_shape_keys=tuple(gm_shape))


def split_counts(n, n_splits):
    """Coarse-base coefficient counts to try, for a two-base gadget map.

    The split is a genuine search dimension, not a detail. Holding Q, n, q_KS and
    base_KS fixed and moving ONLY the split, the calibration ladder measured

        rung 2^5/2^7   sigma 8.22 -> 10.76   (1.3x)
        rung 2^7/2^9   sigma 11.00 -> 31.61  (2.9x)

    -- the largest single lever in the whole grid. Enumerating a map at one
    arbitrary split (an even one, as this did) evaluates that dimension at a point
    and calls it searched.

    Interior points only: 0 and n are the single-base maps, which the grid already
    enumerates on their own. Counts are integers because OpenFHE requires the map
    to cover the LWE dimension exactly.
    """
    out = []
    for i in range(1, n_splits + 1):
        c = (n * i) // (n_splits + 1)
        if 0 < c < n and c not in out:
            out.append(c)
    return out


def fill_map(cand):
    """{base: count} for this candidate, summing to n.

    For a two-base map `split_count` is the number of coefficients at the COARSER
    base -- the noisy, cheap one. Raising it moves the candidate along a straight
    line: noise variance, gate work and bootstrapping-key size are all LINEAR in
    it, the first rising and the other two falling. That linearity is why the split
    is a clean trade rather than something with an interior optimum, and it is
    measured rather than assumed -- the ladder is linear in the split to 1.2%.
    """
    bases = sorted(cand['gadget_shape_keys'])
    if len(bases) == 1:
        return {bases[0]: cand['n']}
    if len(bases) != 2:
        raise ValueError("only single- and two-base maps are enumerated, got %r" % (bases,))
    coarse = cand['split_count']
    fine = cand['n'] - coarse
    gm = {}
    if fine:
        gm[bases[0]] = fine
    if coarse:
        gm[bases[1]] = coarse
    return gm


# --------------------------------------------------------------------------
# pruning
# --------------------------------------------------------------------------

# Reasons are counted and reported. A search that prunes 99.99% of its grid is
# either working perfectly or broken, and the only way to tell is the breakdown.
PRUNE_REASONS = (
    'method/keyDist',       # computes the wrong function outright
    'autokeys >= n',        # out-of-bounds write in KeyGenAcc: SIGSEGV
    'gadget word width',
    'carry precondition',
    'keyswitch saturated',
    'outside measured (N, logQ)',
    'accumulator unidentified',
    'predicted saturated',
    'cost uncalibrated',
    'security (table)',
    'security (estimator)',
    'security (unpriced)',
)


def remedy(reason, level=None, method=None, secret_dist='ternary', tolerance=0):
    """What to run so this refusal stops happening, or None if nothing can.

    Four of the gates refuse because the model does not know something, and they
    are not equal: three are gaps a measurement closes and one is a domain the
    noise model has never been calibrated over. A refusal that does not say which
    leaves the reader to guess whether they are blocked or merely un-measured.
    """
    quantum = bool(level and level.endswith('Q'))
    if reason == 'cost uncalibrated':
        return ("no gate-cost cells for this (method, ring dimension, word size) "
                "in the active regime. Measure them:\n"
                "      THREADS=<n> METHODS=%s bash scripts/dse-arms/gatecost-regime.sh > cells.log\n"
                "      dse.py costfit cells.log --emit    (then scripts/dse-tools/apply_cells.py --merge)"
                % {'AP': 1, 'GINX': 2, 'LMKCDEY': 3}.get(method, '1 2 3'))
    if reason == 'security (unpriced)':
        return ("this dimension was never priced against the lattice estimator. "
                "Price it (needs Sage):\n"
                "      sage -python scripts/paramsestimator/dse_security.py price "
                "--level %s --dist %s%s --tolerance %d\n"
                "      sage -python scripts/paramsestimator/dse_security.py boundary "
                "--level %s --dist %s%s --tolerance %d"
                % (level or '<LEVEL>', secret_dist, ' --quantum' if quantum else '',
                   tolerance, level or '<LEVEL>', secret_dist,
                   ' --quantum' if quantum else '', tolerance))
    if reason in ('outside measured (N, logQ)', 'accumulator unidentified'):
        return ("the NOISE model has no calibration here, which a cost measurement "
                "cannot fix. Designing and running the cells that would:\n"
                "      sage -python scripts/paramsestimator/dse_calibrate.py plan "
                "--emit-commands\n"
                "    Until then this region is outside what the model will predict.")
    if reason in ('security (table)', 'security (estimator)'):
        return ("these candidates are genuinely insecure at this level and "
                "tolerance -- not a gap. --tolerance 1 admits what OpenFHE ships.")
    return None


# Table-based security prefilter. `paramstable.max_logq` interpolates the
# HomomorphicEncryption.org rows OpenFHE ships, and TWO instances have to hold:
#
#   RLWE  log2(Q)     <= max_logq(N, level)     the ring instance
#   LWE   log2(q_KS)  <= max_logq(n, level)     the LWE secret is used at q_KS,
#                                               which is where the switching key
#                                               encrypts it -- not at q
#
# q_KS is the right modulus for the LWE side and it is easy to get wrong: using q
# would pass dimensions the switching key exposes at a much larger modulus.
#
# This is a PREFILTER, not the answer. paramstable's own header says the estimator
# disagrees with these rows -- at STD192 it certifies 188 bits for the table's
# n=1024/logq=19 entry -- so survivors still need an estimator run. But it is
# closed-form and it removes the bulk, which is what this stage is for.
#
# Without it the frontier fills with candidates like n=32 at logQ=28 reporting
# log2Pf of -12000: arithmetically fine, cryptographically meaningless.
def security_ok(cand, level="STD128", secret_dist="ternary", source="table",
                cache=None, model="standard", tolerance_bits=0):
    """None if the candidate is secure, else why not.

    TWO instances have to hold, and using the wrong modulus on the LWE side is easy:

        RLWE   log2(Q)    <= boundary(N)     the ring instance
        LWE    log2(q_KS) <= boundary(n)     the LWE secret is encrypted by the
                                            switching key AT q_KS, not at q

    source="table" uses paramstable's interpolated rows; source="estimator" uses
    the cached lattice-estimator boundary. The two disagree by -4 to +16 bits (see
    dse_security), so which one is in force is a result-changing choice and is
    reported rather than assumed.

    'security (unpriced)' is returned, distinctly from 'security (estimator)',
    where the estimator has no entry for that dimension. Collapsing the two would
    let a coverage gap read as a security finding.

    `tolerance_bits` is how far below nominal a level may certify, and it only
    applies to source="estimator" -- the table has no notion of it. It matters more
    than it looks: **at tolerance 0 the certified boundary rejects 31 of the 41
    production sets OpenFHE ships, STD128 included**, because those sets sit
    exactly at or one bit past today's boundary. So a search left at 0 cannot
    propose anything shaped like a shipped set, and comparing its frontier against
    one is meaningless. It stays 0 by DEFAULT anyway: whether 127 bits satisfies
    "STD128" is a policy question, and dse_security says the same.
    """
    if source == "table":
        if log2(cand['q_ks']) > max_logq(cand['n'], level, secret_dist):
            return 'security (table)'
        if cand['log_q_big'] > max_logq(cand['N'], level, secret_dist):
            return 'security (table)'
        return None

    # Levels ending in Q are the quantum rows, and select the estimator's quantum
    # cost model.
    quantum = level.endswith('Q')
    for dim, lq in ((cand['n'], log2(cand['q_ks'])), (cand['N'], cand['log_q_big'])):
        b = sec.max_logq_certified(dim, level, secret_dist, model, quantum, cache=cache,
                                   tolerance_bits=tolerance_bits)
        if b is None:
            return 'security (unpriced)'
        if lq > b:
            return 'security (estimator)'
    return None


def prune(cand, q_big=None, level="STD128", secret_dist="ternary",
          sec_source="table", sec_cache=None, sec_model="standard",
          sec_tolerance=0, w32_needs_hybrid=False, extrapolate_n=None):
    """None if the candidate survives, else the reason it did not.

    w32_needs_hybrid models a DEFAULT (NS64) build, where the 32-bit accumulator
    is the runtime hybrid: binfhe-base-scheme.cpp:66,81 narrow each key on
    `internal32 && Fits(...)`, so a w32 candidate that Fits() would refuse is not
    merely mispriced, it is unreachable. Since OpenFHE 9e8045db `internal32`
    defaults to TRUE, so this is no longer an opt-in path but what every default
    caller gets at Q <= 2^28 -- the predicate is unchanged, only its meaning went
    from "reachable with the flag" to "what happens with no flag". False prices a
    genuine NATIVE_SIZE=32 build, which the image no longer carries; every caller
    should now pass True.
    """
    gm = fill_map(cand)

    if not c.method_keydist_ok(cand['method'], cand['key_dist']):
        return 'method/keyDist'
    # Before anything else that could rank it: this one crashes OpenFHE rather
    # than merely mispredicting, so a candidate that reaches verification with it
    # takes the process down.
    if not c.autokeys_ok(cand['method'], cand['autokeys'], cand['n']):
        return 'autokeys >= n'
    # AP only. Enumeration already offers just the undominated bases, so this is
    # the guard for a hand-built candidate or a caller-supplied grid.
    if c.method_has_refresh_base(cand['method']):
        if cand.get('base_r') is None:
            return 'AP without baseR'
        if not c.ap_base_r_ok(cand['q'], cand['base_r']):
            return 'AP baseR out of range'
    for b in gm:
        if not c.gadget_width_ok(cand['log_q_big'], b, cand['word_size']):
            return 'gadget word width'
    if w32_needs_hybrid and cand['word_size'] == 32:
        # The candidate carries no default baseG separate from its map, so the
        # default is taken to be one of the map's own bases -- all of which this
        # predicate already requires to pass, so the LMKCDEY default-base arm is
        # satisfied without inventing a base the candidate does not have.
        #
        # This branch is DEFENSIVE, not discriminating: inside logQ <= 28, Fits
        # reduces to the method check plus the width rule already applied above.
        # Its d2 overflow bound has 4.7x headroom at the worst point in the domain
        # (d2 = 54 against a limit of 256, at logQ 28 base 2), and the LMKCDEY
        # default-base arm is redundant when the default is drawn from the map. It
        # earns its place only if MAX_MODULUS_SIZE32 or the base grid moves.
        # The default base is the SMALLEST in the map: that is what the harness
        # sets (gadgetBaseMap.begin()->first), what the library's re-selected
        # table sets (min(map), LIB-62), and what btkey_bytes prices. Fits()
        # checks it for LMKCDEY's automorphism keys, so passing max(gm) here
        # tested the wrong base -- the lenient direction, since the largest base
        # has the fewest digits. No effect on the 105 current rows (all Fits),
        # but a two-base LMKCDEY candidate could have slipped past the check.
        if not c.hybrid_ns32_ok(cand['log_q_big'], gm, cand['method'],
                                default_base=min(gm)):
            return 'hybrid cannot narrow'
    # The carry precondition needs the REAL LastPrime value, not 2^bits: the
    # margin is often a single bit. Without it, skip the check rather than run it
    # on a value that would give a wrong answer either way.
    if q_big is not None:
        for b in gm:
            if not c.carry_precondition_ok(q_big, b):
                return 'carry precondition'
    if not m.within_model_domain(cand['N'], cand['q_ks'], cand['base_ks'], cand['sigma']):
        return 'keyswitch saturated'
    if not m.within_calibrated_envelope(cand['N'], cand['log_q_big'],
                                       extrapolate_n=extrapolate_n):
        return 'outside measured (N, logQ)'
    if not m.accumulator_identified(gm, cand['log_q_big'], method=cand['method']):
        return 'accumulator unidentified'
    if level:
        why = security_ok(cand, level, secret_dist, sec_source, sec_cache, sec_model,
                          tolerance_bits=sec_tolerance)
        if why:
            return why
    return None


# --------------------------------------------------------------------------
# prediction
# --------------------------------------------------------------------------

def evaluate(cand, keys=8, samples_per_key=200):
    """Attach predicted noise, failure probability, uncertainty and cost.

    Returns None where the prediction itself is meaningless -- a sigma at or above
    the wrap ceiling q/(p*sqrt(12)) is not a noise figure, it is the measurement
    saturating, and the model overpredicts without bound past that point.
    """
    gm = fill_map(cand)
    a = (cand['N'], cand['n'], cand['q'], cand['log_q_big'], cand['q_ks'],
         cand['base_ks'], cand['sigma'], gm)
    kw = dict(key_dist=cand['key_dist'], method=cand['method'],
              autokeys=cand['autokeys'],
              dropped_digits_ks=cand.get('dropped_digits_ks', 0) or 0)
    # AP prices its accumulator, its gate work and its key from baseR; the model
    # raises rather than defaulting it, which is what keeps a missing baseR from
    # silently becoming a different refresh key.
    if c.method_has_refresh_base(cand['method']):
        kw['base_r'] = cand['base_r']
    sigma = m.sigma_total_at_q(*a, **kw)
    if sigma >= 0.5 * m.saturated_sigma(cand['q'], cand['inputs']):
        return None

    shares = m.variance_shares(*a, **kw)
    log2pf = m.log2_pf(sigma, cand['inputs'], cand['q'])
    band   = m.log2pf_band(log2pf, shares, keys=keys, samples_per_key=samples_per_key)
    # The statistic certification will actually decide on. `log2pf` is the MEAN
    # key; a deployment runs one key and `certify` accepts on the 0.9 quantile,
    # which is worse by m.quantile_penalty(). Selecting on the mean and being
    # judged on the quantile is what put 7 of the 2026-09-08 table's cells 1.7 to
    # 5.4 bits short of their target while every one of them had a predicted
    # log2Pf that cleared it. Note the sign: log2Pf is negative and worse means
    # LARGER, so the penalty is added.
    penalty = m.quantile_penalty(sigma, cand['inputs'], cand['q'])
    log2pf_cert = log2pf + penalty

    # Refuse rather than extrapolate. GATE_COST covers only the (method, N,
    # word_size) combinations that have been timed, and a cost model that guessed
    # across ring dimension or method would silently reorder the frontier -- which
    # is the only thing the number is for. This prune is loud in the report
    # precisely because it currently removes most of the grid.
    gus = m.gate_us(gm, cand['log_q_big'], cand['N'], method=cand['method'],
                    word_size=cand['word_size'], autokeys=cand['autokeys'],
                    base_r=cand.get('base_r'), q=cand['q'])
    if gus is None:
        return 'cost uncalibrated'

    # The refresh key and the switching key narrow independently, so they are
    # priced at their OWN widths. One shared width overstated every w64
    # candidate's key material by up to 1.75x, the switching key being the larger
    # of the two at those dimensions (LIB-61).
    refresh_bytes, switch_bytes = m.key_word_bytes(
        cand['N'], cand['log_q_big'], cand['q_ks'], cand['base_ks'], gm,
        cand['method'], cand['word_size'])
    word_bytes = refresh_bytes
    out = dict(cand)
    out.update(
        gadget_map=gm,
        # sigma_total, NOT sigma: `sigma` is the candidate's INPUT -- the LWE error
        # stddev, 3.19 -- and overwriting it with the prediction silently corrupts
        # anything that regenerates a command line from the candidate. It did:
        # dse_verify emitted `-s 19.72` (a predicted total) where the binary wants
        # the error stddev, which would have measured a different configuration
        # and reported it as a verification of this one.
        sigma_total=sigma,
        log2pf=log2pf,
        log2pf_cert=log2pf_cert,
        quantile_penalty=penalty,
        band=band,
        shares=shares,
        gate_us=gus,
        ksk_bytes=m.ksk_bytes(cand['N'], cand['n'], cand['q_ks'], cand['base_ks'],
                              switch_bytes,
                              dropped_digits=cand.get('dropped_digits_ks', 0) or 0),
        btkey_bytes=m.btkey_bytes(cand['N'], gm, cand['log_q_big'], word_bytes,
                                  method=cand['method'], autokeys=cand['autokeys'],
                                  base_r=cand.get('base_r'), q=cand['q']),
        ks_aligned=m.keyswitch_aligned(cand['base_ks'], cand['q_ks']),
        g_aligned=all(m.gadget_aligned(b, cand['log_q_big']) for b in gm),
    )
    out['key_bytes'] = out['ksk_bytes'] + out['btkey_bytes']
    # An approximate key-switching decomposition changes the noise through a term
    # nothing in the harness has measured (m.ks_round_var_at_q), so such a row is
    # priced and shown but withheld from selection until it is. The policies read
    # this flag; `spendable_margin` and the frontier do not, because the row
    # belongs on the front as soon as it is priced.
    if kw['dropped_digits_ks']:
        out['ks_noise_unmeasured'] = True
    return out


# --------------------------------------------------------------------------
# margin, scored against the next available step
# --------------------------------------------------------------------------

def next_step_cost(cand, splits=None):
    """Bits of log2Pf the cheapest available speedup would cost.

    Margin is only spendable where a discrete step exists. The steps, cheapest
    first: moving ONE grid-split of the coefficients (n / (splits + 1), an eighth
    by default) to the next coarser gadget base, moving the whole map there, a
    smaller q_KS (fewer keyswitch digits), and the word-size cliff. Returns None
    when no step is available -- and a candidate with nowhere to spend margin
    should not be penalised for having it.

    The partial step is the one that matters in practice. At STD128 the ladder at
    logQ 27 runs 2^7 -> 2^9 (2^8 fails the word-width rule), and moving the whole
    map costs far more than the 60-odd bits of surplus, so every row read
    "margin-unspendable" -- while a two-base map moving an eighth of the
    coefficients spends a few bits for a third of the accumulator work on that
    eighth. The library takes such maps (-G), the grid enumerates them under
    --multi-base, and the step is real whether or not this search enumerated it.

    For a single-base map the partial step opens a second base; for a two-base
    map it moves one more split from the fine base to the coarse one, or replaces
    the coarse base with the next coarser one (still two bases -- the grid never
    goes to three).
    """
    if splits is None:
        splits = DEFAULT_GRID['splits']
    gm = fill_map(cand)
    bases = sorted(gm)
    n = cand['n']
    chunk = max(1, n // (splits + 1))
    trials = []

    def coarser_than(b):
        """Next undominated base above b, i.e. the next one that reduces the
        digit count AND fits the word. Walked over undominated_bases rather than
        by doubling: at logQ 27 on 32-bit words 2^8 fails the width rule while
        2^9 passes it, and a doubling walk that stopped at the first failure
        never reached 2^9 -- which, with the sign error above, is why no step was
        ever found."""
        d = m.digits_for_base(cand['log_q_big'], b)
        for cb in sorted(c.undominated_bases(cand['log_q_big'], cand['word_size']).values()):
            if cb > b and m.digits_for_base(cand['log_q_big'], cb) < d:
                return cb
        return None

    if len(bases) == 1:
        b = bases[0]
        cb = coarser_than(b)
        if cb is not None:
            trials.append(dict(cand, gadget_shape_keys=(cb,), split_count=0))        # whole map
            if chunk < n:
                trials.append(dict(cand, gadget_shape_keys=(b, cb), split_count=chunk))  # one split
    else:
        fine, coarse = bases
        at_coarse = gm.get(coarse, 0)
        nxt = min(n, at_coarse + chunk)
        if nxt == n:
            trials.append(dict(cand, gadget_shape_keys=(coarse,), split_count=0))
        else:
            trials.append(dict(cand, gadget_shape_keys=(fine, coarse), split_count=nxt))
        cc = coarser_than(coarse)
        if cc is not None:
            trials.append(dict(cand, gadget_shape_keys=(fine, cc), split_count=at_coarse))

    best = None
    for trial in trials:
        ev = evaluate(trial)
        if not isinstance(ev, dict):
            continue
        # log2Pf is NEGATIVE and a worse Pf is the LESS negative number, so the
        # bits a step costs are trial minus candidate. The previous form had them
        # the other way round, which made every cost negative, every step
        # "unavailable", and every frontier row "margin-unspendable" -- a wrong
        # flag that read as a finding about the parameter space.
        cost = ev['log2pf'] - cand['log2pf']       # positive = worse PF
        if cost > 0 and (best is None or cost < best):
            best = cost
    return best


def spendable_margin(cand, target):
    """(margin in bits, whether it can actually be spent).

    Margin net of the model's own uncertainty: a 1.7-bit margin at +-0.8 bits is
    not a pass. `spendable` says whether a discrete step exists to convert the
    surplus into speed -- surplus with nowhere to go is not an advantage, and
    scoring it as one is what makes a policy prefer candidates that have simply
    run out of room.
    """
    # Against the CERTIFIABLE statistic where one is present, so that the margin
    # reported on the frontier is the margin the verdict will use. Scoring this
    # against the mean-key log2Pf while the gate used the quantile made the
    # frontier claim margin a measurement would not find.
    margin = target - cand.get('log2pf_cert', cand['log2pf'])
    net = margin - cand['band']
    step = next_step_cost(cand)
    return net, (step is not None and step <= net)


# --------------------------------------------------------------------------
# frontier and policy
# --------------------------------------------------------------------------
# Axes, all minimised, all already per candidate. Failure margin enters NEGATED so
# that "more margin is better" reads as "smaller is better" like the rest.
AXES = ('gate_us', 'key_bytes', 'neg_margin')

# Two candidates whose predicted gate times differ by less than this are the
# same speed for any practical purpose, so a margin difference between them is
# free. Set from the cost model's OWN error rather than picked: per-cell median
# residuals run 1.6-4.7% and the out-of-sample check against the legacy
# selector's measured candidates spans 0.93-1.05, so a 2% difference in
# predicted gate time is not a difference anyone can measure.
#
# It matters that this is not tighter. At the STD128 front the policy's pick sits
# at 16505 us with 0.7 bits of margin, and 1.4% slower -- inside the model's own
# error -- there is a row with 11.9 bits at the SAME key material. A 1% window
# hid it; nothing about "fastest" says to prefer the riskier of two
# indistinguishable configurations.
SAME_SPEED_TOLERANCE = 0.02


def pareto(rows, axes=AXES):
    """The non-dominated set, in O(n * |frontier|) after a sort.

    Sorting by the first axis means a point can only ever be dominated by one
    already seen, so each candidate is compared against the surviving frontier
    rather than against every other candidate. That is exact, not approximate:
    with rows sorted ascending on axes[0], any s preceding r has
    s[axes[0]] <= r[axes[0]], so domination reduces to the remaining axes, and r
    cannot dominate s.

    The naive O(n^2) form this replaces was tolerable only while the cost model
    covered 3 of 12 (method, N, word_size) cells and pruned most of the grid for
    want of a gate time. Calibrating the other nine multiplied the survivors and
    the quadratic scan stopped returning -- 600 seconds without printing a line.
    A performance bug that only appears once an unrelated gap is closed.
    """
    if not rows:
        return []
    first, rest = axes[0], axes[1:]
    front = []
    for r in sorted(rows, key=lambda r: tuple(r[a] for a in axes)):
        dominated = False
        for s in front:
            if all(s[a] <= r[a] for a in rest) and (
                    s[first] < r[first] or any(s[a] < r[a] for a in rest)):
                dominated = True
                break
        if not dominated:
            front.append(r)
    return front


def front_insert(front, r, axes=AXES):
    """Insert r into a running non-dominated set, in place. O(|front|).

    The search used to keep EVERY surviving candidate and take the frontier at
    the end. Survivors are what a wide grid produces most of: the STD128
    multi-base search held tens of millions of candidate dicts and was killed
    by the OOM killer on a 62 GB box. The frontier itself is a few hundred rows,
    so it is maintained as the survivors stream past: a candidate dominated by
    any member is dropped; otherwise it evicts the members it dominates and
    joins. Exact -- the final set equals pareto(all survivors) -- and memory
    is bounded by the frontier, not the grid.
    """
    for s_ in front:
        if all(s_[a] <= r[a] for a in axes) and any(s_[a] < r[a] for a in axes):
            return False                            # dominated: not a frontier point
    front[:] = [s_ for s_ in front
                if not (all(r[a] <= s_[a] for a in axes) and any(r[a] < s_[a] for a in axes))]
    front.append(r)
    return True


def adjacent_pairs(log_q_big, word_size, base):
    """Undominated-base pairs containing `base` and one of its NEIGHBOURS.

    Hong and Lee (ePrint 2025/1892, Theorems 4.1 and 4.2) prove the structure of
    the optimal heterogeneous gadget map: the knapsack's LP relaxation has at most
    two non-zero base counts, and they are adjacent -- because cost grows
    linearly in the digit count while noise falls geometrically, so the
    cost-per-noise ratio (c_j - c_i)/(a_j - a_i) is smallest at the minimal
    j > i, i.e. the NEXT AVAILABLE item. Their statement reads "d and d+1"
    because they assume every digit length is on offer. Ours are not: with
    power-of-two bases the achievable digit counts at logQ 26 are 26, 13, 9, 7,
    6, 5, 4, 3, 2, so bases 2, 4 and 8 have no d+1 neighbour at all. Read
    literally, the theorem refined nothing at those bases and LOST a cell
    (LPF_STD256Q_4_LMKCDEY at base 4) that the next-available base could rescue.
    So "adjacent" here means adjacent in the sorted undominated list, which is
    what the proof actually uses.

    Every two-base map that has won here obeys it: {32:165, 64:1152} and
    {16:348, 32:1043} at logQ 26, {32:158, 64:472} and {128:347, 512:207} at
    logQ 27, and OpenFHE's own shipped {128:547, 512:9}. Of the 36 undominated
    pairs at logQ 26 only 8 are neighbours, and for a frontier member at one base
    only the two that contain it -- this is the theorem, not a heuristic cut.
    """
    bases = sorted(c.undominated_bases(log_q_big, word_size).values())
    if base not in bases:
        return []
    i = bases.index(base)
    out = []
    if i > 0:
        out.append((bases[i - 1], base))
    if i + 1 < len(bases):
        out.append((base, bases[i + 1]))
    return out


def _map_candidate(template, pair, coarse_count):
    """The template re-shaped to a two-base map, evaluated fields stripped."""
    cand = dict(template, gadget_shape_keys=tuple(sorted(pair)),
                split_count=coarse_count)
    for k in ('gadget_map', 'log2pf', 'log2pf_cert', 'quantile_penalty',
              'band', 'shares', 'gate_us', 'ksk_bytes', 'btkey_bytes',
              'key_bytes', 'net_margin', 'neg_margin', 'spendable',
              'sigma_total', 'ks_aligned', 'g_aligned',
              # repick's bookkeeping on a STORED row; a refinement built from it
              # is a new row and must not claim the template's stored price
              # (it did, and made every refined GINX pick look "repriced").
              'stored_gate_us', 'repriced'):
        cand.pop(k, None)
    return cand


def boundary_split(template, pair, evaluate_fn, admit):
    """The largest coarse-base count whose evaluation `admit` accepts, or None.

    Hong and Lee's Theorem 4.3: the relax-and-round solution of the map knapsack
    is exactly optimal, so the optimum split is the BOUNDARY of admissibility,
    not the nearest of a handful of samples. Raising the coarse count raises
    noise and lowers cost monotonically (Theorem 3.1 -- all three are linear in
    it), so admissibility is a step function of the count and bisection finds the
    step in about log2(n) evaluations. A 7-point ladder can sit up to n/8
    coefficients short of it, which at five digits is about 2.5 percent of gate
    work left on the table.
    """
    n = template['n']

    def ok(x):
        ev = evaluate_fn(_map_candidate(template, pair, x))
        return ev if isinstance(ev, dict) and admit(ev) else None

    lo_ev = ok(1)
    if lo_ev is None:
        return None
    lo, hi = 1, n - 1
    hi_ev = ok(hi)
    if hi_ev is not None:
        return hi_ev
    best = lo_ev
    while hi - lo > 1:
        mid = (lo + hi) // 2
        ev = ok(mid)
        if ev is not None:
            lo, best = mid, ev
        else:
            hi = mid
    return best


def refine_maps(front, evaluate_fn, target, quantile_budget=True,
                splits=None, security_hook=None, boundary=True):
    """Expand a single-base frontier with two-base gadget maps.

    THIS IS WHERE MULTI-BASE BELONGS. As a grid dimension it costs 28.2x for
    nothing a sweep is needed for: the split is a straight trade, with noise
    variance rising and gate work and key size falling LINEARLY in the coarse
    count, so there is no interior optimum to discover -- only a segment between
    two single-base endpoints. As a local move from each frontier point it costs
    seconds and finds the same segment.

    It is applied to EVERY front member rather than to the chosen pick, because
    refining one point cannot discover that a split makes a different
    (n, logQ, baseKS) win, and the front is hundreds of points rather than
    billions.

    It is not decoration. Of the three cells pinned at STD256Q's ring-security
    ceiling -- where logQ cannot rise past 26, so there is no 64-bit option at all
    -- a two-base split is the ONLY lever that reaches the target for
    STD256Q_4_LMKCDEY, at +2.6 percent gate time and no extra key material, where
    a larger baseKS cannot get there at any price.

    Two results from Hong and Lee (ePrint 2025/1892) shape the move. Only the
    pairs whose digit counts are ADJACENT to the member's own base are tried
    (`adjacent_pairs`, their Theorems 4.1-4.2), and for each pair the split is
    taken at the exact BOUNDARY of admissibility by bisection
    (`boundary_split`, their Theorem 4.3) as well as at the ladder points that
    give the frontier its shape. Checked against the exhaustive map-in-the-grid
    sweep on four cells before the boundary step existed: identical picks, 25 to
    33 times faster. With it the staged pick can only be at least as fast.
    """
    if not front:
        return front
    splits = splits or DEFAULT_GRID['splits']

    def stat(ev):
        return ev['log2pf_cert'] if quantile_budget else ev['log2pf']

    def in_front(ev):
        return stat(ev) <= target and (
            security_hook is None or security_hook(ev))

    def pickable(ev):
        return in_front(ev) and (target - stat(ev) - ev['band']) > 0

    added = []
    for r in front:
        base = r.get('gadget_shape_keys')
        if not base or len(base) != 1:
            continue                      # already a map, or nothing to anchor on
        for pair in adjacent_pairs(r['log_q_big'], r['word_size'], base[0]):
            # the ladder, for the frontier's shape
            for sc in split_counts(r['n'], splits):
                ev = evaluate_fn(_map_candidate(r, pair, sc))
                if isinstance(ev, dict) and in_front(ev):
                    added.append(ev)
            # the boundary, for the pick. `boundary=False` leaves the ladder
            # alone, which is how the theorem's own contribution is measured.
            if boundary:
                ev = boundary_split(r, pair, evaluate_fn, pickable)
                if ev is not None:
                    added.append(ev)
    if not added:
        return front
    out = list(front)
    for ev in added:
        ev['net_margin'] = target - stat(ev) - ev['band']
        ev['neg_margin'] = -ev['net_margin']
        front_insert(out, ev)
    return out


KEY_CAP_LAMBDA = 2.0


def key_cap_policy(frontier, cap_bytes, lam=KEY_CAP_LAMBDA, hard=False):
    """Key material is free up to a cap and priced at a stated exchange rate beyond it.

    Carlo's framing (2026-09-09): up to a certain level key size does not matter
    because the keys are manageable, and past it the key-size/gate-time trade-off
    is what needs scrutiny. Every admissible row is scored

        gate_us * (1 + lam * max(0, key_bytes / cap_bytes - 1))

    so a row within the cap scores its own gate time -- among themselves the
    under-cap rows are ranked exactly as default_policy ranks them -- and a row
    beyond it wins only when its speed advantage beats lam times its excess:
    exceeding the cap by a factor of two costs the same as running lam times
    slower. lam = 0 is gate-first everywhere, lam = inf is "never exceed the cap
    while anything fits", and the default lam = 2 (Carlo, 2026-09-09 evening).

    Why SOFT and why 2. The first form was two-stage: the fastest row that fit,
    and the score only when nothing did. That put a cliff at the level: three
    STD256/STD256Q AP cells took 5.5-7.8 GiB rows at 558-587 ms while a 9 GiB
    row ran 312 ms, because the 9 GiB row was never looked at. Under the soft
    score at lam = 2 those three flip (they would up to lam = 6-18) along with
    six AP cells that gain 24-28 percent for keys 8.1-8.9 GiB; lam = 1 would
    move fifteen cells and lam = 0.5 twenty-four, so 2 is the setting that
    removes the cliff without re-opening the trade the level was set to close.
    `hard=True` keeps the two-stage rule for comparison. (The version before
    either took the least-key row beyond the cap, which sent eight AP cells to
    rows 88 to 240 percent slower for a few GiB.)

    Returns (pick, over_cap); over_cap is True when the pick exceeds the cap.
    """
    eligible = [r for r in frontier
                if r['net_margin'] > 0 and not r.get('ks_noise_unmeasured')]
    if not eligible:
        return None, False
    under = [r for r in eligible if r['key_bytes'] <= cap_bytes]
    if hard and under:
        return min(under, key=lambda r: (r['gate_us'], r['key_bytes'])), False
    if lam == float('inf'):
        pool = under or eligible
        return (min(pool, key=lambda r: (r['gate_us'], r['key_bytes'])) if under
                else min(pool, key=lambda r: (r['key_bytes'], r['gate_us']))), not under

    def score(r):
        return r['gate_us'] * (1.0 + lam * max(0.0, r['key_bytes'] / float(cap_bytes) - 1.0))
    pick = min(eligible, key=lambda r: (score(r), r['key_bytes']))
    return pick, pick['key_bytes'] > cap_bytes


def default_policy(frontier):
    """Fastest whose margin exceeds its own uncertainty; ties broken on key size.

    A single replaceable function on purpose. This one encodes a deployment that
    cares about gate time first -- swap it for one that weights key material and
    the answer changes, which is the point of reporting the frontier whole.
    """
    eligible = [r for r in frontier
                if r['net_margin'] > 0 and not r.get('ks_noise_unmeasured')]
    if not eligible:
        return None
    return min(eligible, key=lambda r: (r['gate_us'], r['key_bytes']))


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(target, method="GINX", key_dist=None, grid=None,
        word_size=None, multi_base=False, security_hook=None, limit=None,
        level="STD128", sec_source="table", sec_tolerance=0,
        quantile_budget=True, refine=True, autokeys_grid=False, boundary=True,
        dropped_ks=(0,), extrapolate_n=None):
    sec_cache = sec.load() if sec_source == "estimator" else None
    sec_model = sec.active_model()
    counts = dict.fromkeys(PRUNE_REASONS, 0)
    counts['below target'] = 0
    counts['security'] = 0
    total = 0
    front, n_survived = [], 0

    if level and sec_source == 'estimator':
        # the cache's off-grid dimensions for this level's curve: shipped n values
        # and the priced security boundaries. Where the cache has none the grid
        # is unchanged, so this never removes a candidate.
        # One curve per secret distribution, so with the distribution enumerated
        # the off-grid dimensions are the UNION. A superset is safe: a dimension
        # belonging to the other curve is refused by the security gate, not
        # silently accepted.
        dists = ((key_dist,) if key_dist else
                 tuple(d for d in KEY_DISTS if c.method_keydist_ok(method, d)))
        step = (grid or {}).get('n_step', DEFAULT_GRID['n_step'])
        extra = set()
        for kd in dists:
            extra |= set(sec.extra_dims(level, CURVE_FOR_DIST[kd], sec_model,
                                        level.endswith('Q'), sec_tolerance,
                                        cache=sec_cache, step=step))
        if extra:
            grid = dict(grid or {}, extra_n=tuple(sorted(extra)))
    if extrapolate_n is not None:
        # Opt-in, and additive: the measured dimensions stay in the grid, so a
        # candidate at N=4096 competes against the ones the model has measured
        # rather than replacing them.
        g_n = tuple(sorted(set((grid or {}).get('N', DEFAULT_GRID['N']))
                           | {int(extrapolate_n)}))
        # And the ciphertext modulus it unlocks. q is capped at 2N by the
        # embedding (candidates() drops the rest), so the grid's largest q was
        # 4096 only because its largest N was 2048 -- leaving every candidate at
        # N=4096 stuck at HALF the modulus that dimension allows. That cap, not
        # the ring dimension, is what holds the failure probability up: q is the
        # window the accumulated noise has to stay inside.
        g_q = tuple(sorted(set((grid or {}).get('q', DEFAULT_GRID['q']))
                           | {2 * int(extrapolate_n)}))
        grid = dict(grid or {}, N=g_n, q=g_q)
    for cand in candidates(grid, method, key_dist, word_size, multi_base,
                           autokeys_grid=autokeys_grid, dropped_ks=dropped_ks):
        total += 1
        if limit and total > limit:
            break
        sd = CURVE_FOR_DIST[cand['key_dist']]
        # w32_needs_hybrid=True: there is one build, and its 32-bit accumulator
        # is the runtime hybrid (the library default since 9e8045db), so a w32
        # candidate that Fits() refuses is unreachable, not merely mispriced.
        # This driver used to leave it False because the image also carried a
        # genuine NATIVE_SIZE=32 build that w32 candidates were dispatched to;
        # that build is gone (no HAVE_INT128, so it was the slower path past six
        # digits and not what any caller runs). Inside logQ <= 28 the predicate
        # reduces to the digit-width rule already applied above, so today this
        # changes nothing numerically; it would if MAX_MODULUS_SIZE32 moved.
        why = prune(cand, level=level, secret_dist=sd, sec_source=sec_source,
                    sec_tolerance=sec_tolerance, extrapolate_n=extrapolate_n,
                    sec_cache=sec_cache, sec_model=sec_model, w32_needs_hybrid=True)
        if why:
            counts[why] += 1
            continue
        ev = evaluate(cand)
        if isinstance(ev, str):
            counts[ev] += 1
            continue
        if ev is None:
            counts['predicted saturated'] += 1
            continue
        # Carried on the row itself, so a pick that rests on an extrapolated ring
        # dimension says so wherever it is read -- the manifest, the plan, the
        # artifact -- rather than only in the invocation that produced it.
        if m.n_is_extrapolated(ev['N']):
            ev['n_extrapolated'] = True
        # Gated on the CERTIFIABLE statistic, not the mean-key one. A candidate
        # whose mean key reaches the target but whose 0.9-quantile key does not
        # is a candidate that will be measured and then rejected, so refusing it
        # here spends the measurement somewhere it can succeed.
        stat = ev['log2pf_cert'] if quantile_budget else ev['log2pf']
        if stat > target:                          # both negative: > means worse
            counts['below target'] += 1
            continue
        if security_hook is not None and not security_hook(ev):
            counts['security'] += 1
            continue
        # Margin net of the model's own band is an AXIS, so every survivor needs
        # it; whether it is SPENDABLE costs up to three extra evaluations and is
        # only ever read off the frontier, so it is attached to the frontier
        # members after the sweep rather than to every survivor during it.
        # Two different allowances, kept separate because they are different
        # kinds of thing: `band` is the MODEL's two-sided uncertainty about its
        # own prediction, and the quantile penalty already inside `stat` is a
        # systematic offset certification will always apply. Adding the penalty
        # into the statistic rather than into the band is what keeps
        # `--model-band 0` correct for a measured candidate.
        net = target - stat - ev['band']
        ev['net_margin'] = net
        ev['neg_margin'] = -net
        n_survived += 1
        front_insert(front, ev)

    # The two-base refinement, over the WHOLE front. Kept out of the grid (28.2x)
    # and done here as a local move along a straight trade; see refine_maps.
    if refine and not multi_base:
        def _eval(cand):
            sd2 = CURVE_FOR_DIST[cand['key_dist']]
            why2 = prune(cand, level=level, secret_dist=sd2, sec_source=sec_source,
                         sec_tolerance=sec_tolerance, sec_cache=sec_cache,
                         extrapolate_n=extrapolate_n,
                         sec_model=sec_model, w32_needs_hybrid=True)
            if why2:
                return why2
            ev2 = evaluate(cand)
            if not isinstance(ev2, str) and m.n_is_extrapolated(cand['N']):
                ev2['n_extrapolated'] = True
            return ev2
        before = len(front)
        front = refine_maps(front, _eval, target, quantile_budget=quantile_budget,
                            splits=(grid or {}).get('splits'),
                            security_hook=security_hook, boundary=boundary)
        counts['map refinements added'] = len(front) - before

    for r in front:
        r['spendable'] = spendable_margin(r, target)[1]
    counts['SURVIVED'] = n_survived
    # `kept` is the FRONTIER now (pareto() of it is itself), and counts['SURVIVED']
    # carries the survivor total the frontier was drawn from.
    return total, counts, front


def _cost_note(methods):
    """Print which build the gate_us figures above came from.

    A ranking is a conclusion, and this one is priced by cells measured on one
    specific OpenFHE build. See dse_model.comparison_caveat for why cross-method
    rankings get a louder warning than same-method ones.
    """
    for line in m.comparison_caveat(methods):
        print("  " + line)


def _fmt_bytes(b):
    return "%.0f MiB" % (b / 1048576.0)


def report(target, total, counts, kept, explain=False, level=None, method=None,
           tolerance=0, limit=None):
    print("grid: %d candidates enumerated%s"
          % (total, "  (TRUNCATED at --limit %d)" % limit if limit else ""))
    print("pruned in closed form, by reason:")
    for k in list(PRUNE_REASONS) + ['below target', 'security']:
        if counts.get(k):
            print("  %-28s %8d" % (k, counts[k]))
    print("  %-28s %8d" % ("SURVIVED", counts.get('SURVIVED', len(kept))))
    # Any refusal that a command would fix says so, with the command. The three
    # that are gaps and the one that is a domain limit are distinguished, because
    # "run this" and "this region is not modelled" are different answers.
    shown = []
    for k in ('cost uncalibrated', 'security (unpriced)',
              'outside measured (N, logQ)', 'accumulator unidentified'):
        if counts.get(k):
            fix = remedy(k, level=level, method=method, tolerance=tolerance)
            if fix:
                shown.append((k, counts[k], fix))
    if shown:
        print("\nrefusals a command would fix:")
        for k, n, fix in shown:
            print("  %s (%d candidates)\n      %s" % (k, n, fix))

    if not kept:
        if limit:
            # The reassuring message below is actively wrong when the enumeration
            # was cut short: --limit stops the grid walk, so a small value refuses
            # everything for lack of candidates and the breakdown names whatever
            # constraint the first few happened to hit.
            print("\nNothing survived, but the grid was TRUNCATED at --limit %d of a"
                  % limit)
            print("space that runs to millions of points, so the breakdown above")
            print("describes only the first %d. Re-run without --limit before" % limit)
            print("reading this as a refusal.")
            return 1
        print("\nNothing survived. That is a result, not a bug -- the breakdown above")
        print("says which constraint is binding, and 'below target' vs a structural")
        print("refusal are very different problems.")
        return 1

    front = pareto(kept)
    print("\npredicted Pareto frontier over (gate time, key material, margin): "
          "%d of %d" % (len(front), counts.get('SURVIVED', len(kept))))
    print("  %-9s %-6s %-5s %-6s %-5s %-5s %-6s %-4s %-9s %-9s %7s %7s  %s"
          % ("gate us", "keyMiB", "N", "n", "q", "logQ", "logqKS", "bKS", "gadget",
             "log2Pf", "band", "margin", "flags"))
    for r in sorted(front, key=lambda r: r['gate_us'])[:25]:
        gm = ",".join("2^%d:%d" % (b.bit_length() - 1, cnt)
                      for b, cnt in sorted(r['gadget_map'].items()))
        if r['method'] == 'LMKCDEY':
            gm += " w=%d" % r['autokeys']
        flags = []
        if not r['ks_aligned']: flags.append("ks-misaligned")
        if not r['g_aligned']:  flags.append("g-misaligned")
        if not r['spendable']:  flags.append("margin-unspendable")
        if r.get('n_extrapolated'): flags.append("N-EXTRAPOLATED")
        print("  %9.0f %6s %5d %6d %5d %5d %6d %4d %-9s %9.1f %7.1f %7.1f  %s"
              % (r['gate_us'], _fmt_bytes(r['key_bytes']).replace(" MiB", ""), r['N'],
                 r['n'], r['q'], r['log_q_big'], int(log2(r['q_ks'])), r['base_ks'], gm,
                 r['log2pf'], r['band'], r['net_margin'], " ".join(flags)))

    pick = default_policy(front)
    print("\npolicy (fastest with margin exceeding its own uncertainty, "
          "ties on key size):")
    if pick is None:
        print("  NOTHING QUALIFIES. Every frontier candidate's margin is inside its own")
        print("  model uncertainty, so none can be claimed to beat the target. Tightening")
        print("  a term's band, or measuring more keys, is what moves this -- not a")
        print("  different candidate.")
    else:
        print("  N=%d n=%d q=%d logQ=%d q_KS=2^%d baseKS=%d gadget=%s word=%d"
              % (pick['N'], pick['n'], pick['q'], pick['log_q_big'],
                 int(log2(pick['q_ks'])), pick['base_ks'],
                 ",".join("2^%d:%d" % (b.bit_length() - 1, cnt)
                          for b, cnt in sorted(pick['gadget_map'].items())),
                 pick['word_size']))
        # Both figures, because they answer different questions: log2Pf is what
        # a pooled measurement will read back, and certifiable is what `decide`
        # will accept or reject on. Printing only the first is what made seven
        # short cells look like model failures.
        print("  predicted sigma %.3f  log2Pf %.1f +-%.1f  certifiable %.1f "
              "(quantile penalty %.1f)"
              % (pick['sigma_total'], pick['log2pf'], pick['band'],
                 pick.get('log2pf_cert', pick['log2pf']),
                 pick.get('quantile_penalty', 0.0)))
        print("  margin %.1f bits over target, net of the model band"
              % pick['net_margin'])
        print("  gate %.0f us   keys %s   variance shares %s"
              % (pick['gate_us'], _fmt_bytes(pick['key_bytes']),
                 " ".join("%s %.0f%%" % (k, 100 * v) for k, v in pick['shares'].items())))
        _cost_note([c.get('method', 'GINX') for c in kept])
        # SAME SPEED, BETTER MARGIN. The policy takes the fastest row whose margin
        # clears its band, and gate time is quantised by the cost model, so a row
        # costing the same to a fraction of a percent may sit many bits safer.
        # Nothing about "fastest" says to take the riskier of two equal-speed
        # configurations, and the front already holds both.
        near = [r for r in front
                if r['gate_us'] <= pick['gate_us'] * (1.0 + SAME_SPEED_TOLERANCE)
                and r['net_margin'] > pick['net_margin'] + 1.0]
        if near:
            best = max(near, key=lambda r: r['net_margin'])
            print("\n  same speed, better failure probability (within %.1f%% of the pick's"
                  " gate time):" % (100 * SAME_SPEED_TOLERANCE))
            print("    log2Pf %.1f against the pick's %.1f (%.1f bits more margin), "
                  "gate %+.2f%%, keys %s (%+.0f%%)"
                  % (best['log2pf'], pick['log2pf'],
                     best['net_margin'] - pick['net_margin'],
                     100 * (best['gate_us'] / pick['gate_us'] - 1),
                     _fmt_bytes(best['key_bytes']),
                     100 * (best['key_bytes'] / pick['key_bytes'] - 1)))
            print("    %s" % ("n=%d q=%d logQ=%d q_KS=2^%d baseKS=%d gadget=%s"
                              % (best['n'], best['q'], best['log_q_big'],
                                 int(log2(best['q_ks'])), best['base_ks'],
                                 ",".join("2^%d:%d" % (b.bit_length() - 1, cnt)
                                          for b, cnt in sorted(best['gadget_map'].items())))))

    if explain:
        print("\nwhat the frontier costs on each axis, so the trade-off is visible:")
        for axis, label in (('gate_us', 'fastest'), ('key_bytes', 'smallest keys'),
                            ('neg_margin', 'most margin')):
            b = min(front, key=lambda r: r[axis])
            print("  %-14s gate %8.0f us   keys %-9s   margin %5.1f bits"
                  % (label, b['gate_us'], _fmt_bytes(b['key_bytes']), b['net_margin']))
        print("\n  The point: if 'fastest' and 'smallest keys' are different rows, a")
        print("  single-axis rule is choosing for you without saying so.")
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_enumerate')
    ap.add_argument('--target', type=float, required=True,
                    help='log2 failure probability target, e.g. -64')
    ap.add_argument('--method', default='GINX', choices=('GINX', 'AP', 'LMKCDEY'))
    ap.add_argument('--key-dist', default=None,
                    choices=('UNIFORM_TERNARY', 'GAUSSIAN'),
                    help='default: every distribution the method can represent, '
                         'because it is a trade (15x on rounding against a looser '
                         'security curve) rather than a preference')
    ap.add_argument('--word-size', type=int, choices=(32, 64),
                    help='default: try both, because which side of the cliff wins is a result')
    # Two-base maps are reached by refining the frontier, which is on by
    # default and costs seconds. This flag is the EXHAUSTIVE alternative: maps in
    # the grid, 28.2x the enumeration, for validating that staging loses nothing.
    ap.add_argument('--multi-base', action='store_true',
                    help='enumerate per-dimension gadget maps in the GRID (28x '
                         'slower; the frontier refinement already reaches them)')
    ap.add_argument('--no-refine', action='store_false', dest='refine',
                    help='skip the two-base frontier refinement, leaving a '
                         'single-base-only result')
    ap.add_argument('--autokeys-grid', action='store_true',
                    help='enumerate numAutoKeys instead of taking the largest '
                         'priced value (5x slower for LMKCDEY; the value is '
                         'monotone, so this only ever rediscovers the ceiling)')
    ap.add_argument('--inputs', type=int, default=2, choices=(2, 3, 4))
    ap.add_argument('--limit', type=int, help='stop after this many grid points')
    ap.add_argument('--level', default='STD128',
                    choices=('STD128', 'STD128Q', 'STD192', 'STD192Q', 'STD256', 'STD256Q'),
                    help='security level for the table prefilter')
    ap.add_argument('--security', default='table', choices=('table', 'estimator'),
                    dest='sec_source',
                    help='table: paramstable rows (fast, disagrees with the estimator by '
                         '-4 to +16 bits). estimator: cached lattice-estimator boundaries, '
                         'filled by `dse_security.py price`.')
    ap.add_argument('--tolerance', type=int, default=0, dest='sec_tolerance',
                    help='bits below nominal the level may certify (estimator '
                         'source only). 0 rejects 31 of the 41 shipped sets, so '
                         'comparing a frontier against one needs 1.')
    ap.add_argument('--explain', action='store_true')
    # Off only to reproduce a pre-2026-09-08 search. With the budget off, a pick
    # is selected on the mean key and certified on the 0.9 quantile, which is
    # the defect this flag exists to let you re-create, not a mode to run in.
    ap.add_argument('--no-quantile-budget', action='store_false',
                    dest='quantile_budget',
                    help='select on the mean-key log2Pf instead of the '
                         'certifiable quantile (reproduces pre-2026-09-08 picks)')
    a = ap.parse_args()

    total, counts, kept = run(a.target, a.method, a.key_dist,
                              grid={'inputs': a.inputs},
                              word_size=a.word_size, multi_base=a.multi_base,
                              limit=a.limit, level=a.level,
                              sec_source=a.sec_source,
                              sec_tolerance=a.sec_tolerance,
                              quantile_budget=a.quantile_budget,
                              refine=a.refine, autokeys_grid=a.autokeys_grid)
    sys.exit(report(a.target, total, counts, kept, a.explain))
