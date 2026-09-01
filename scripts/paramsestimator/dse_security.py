#!/usr/bin/python

'''Certified security boundaries from the lattice estimator, cached for the enumerator.

Two phases on purpose, because the two halves have incompatible requirements.

The ENUMERATOR is pure Python and must stay that way: it evaluates millions of
candidates and importing Sage into that loop is both unnecessary and, per the
Dockerfile's own warning, hazardous if the environment is not exactly right. So
the read path here has no Sage dependency at all -- it loads a JSON cache.

PRICING needs Sage, and it is priced ONCE PER DISTINCT INSTANCE rather than per
candidate, which is what makes it affordable: the grid has millions of candidates
but only a few hundred distinct (dimension, level, secret distribution) triples.

    # phase 1, needs Sage:
    > sage -python scripts/paramsestimator/dse_security.py price --level STD128
    # phase 2, pure Python:
    > python3 scripts/paramsestimator/dse_enumerate.py --target -64 --security estimator

What is cached, and why it is a boundary rather than a bit count
---------------------------------------------------------------
For each (dim, level, secret_dist, model, quantum) the cache stores the largest
INTEGER log2(q) whose security still meets the level. That is the only thing the
enumerator needs -- it asks "is this (dim, q) secure?" and the answer is
"log2(q) <= boundary" -- and it collapses a two-dimensional lookup to one number
per dimension. Security falls monotonically in q at fixed dim, which is what makes
the boundary well defined and lets bisection find it in ~6 calls.

Why this exists at all: the closed-form table the enumerator prunes on today
(paramstable.openfhe_std_tables) disagrees with the estimator in BOTH directions.
Measured 2026-09-01 at the table's own boundary moduli:

    dim   level    dist      table logq   estimator bits   target   gap
    556   STD128   ternary      14.66          137.0        128     +9.0
    601   STD128   ternary      15.85          138.0        128    +10.0
    821   STD192   ternary      15.23          191.0        192     -1.0
    1024  STD192   ternary      19.00          188.0        192     -4.0
    1299  STD256   ternary      18.03          258.0        256     +2.0
    716   STD192   error        14.68          208.0        192    +16.0

Conservative entries cost the search candidates it could have used -- and 9-10
bits of q_KS is not marginal, since key switching is the dominant noise term at
the 128 family. The STD192/n=1024 entry is OPTIMISTIC by 4 bits, i.e. the table
permits a modulus the estimator says is short of the level. Both directions are
reasons not to prune on the table alone.

The cache is keyed on the SECURITY MODEL
----------------------------------------
`standard` and `conservative` differ by 4 bits at the STD128 winner (124 vs 128),
because the latter prices dual_hybrid and MATZOV. A cache built under one model is
not valid under the other, so the model is part of every key and a lookup under a
different model misses rather than silently answering.
'''

from math import log2

import argparse
import json
import os
import sys

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dse_security_cache.json")

# The lattice-estimator these boundaries were priced against, from the image's
# LATTICE_ESTIMATOR_REF. Read from the environment when set so the container can
# assert its own version rather than trusting this literal.
ESTIMATOR_REF = os.environ.get("LATTICE_ESTIMATOR_REF",
                               "53da5982597709ba0fdf94ea37a84d822310fd84")

# The estimator prices q as a power of two here; the search only ever proposes
# q_KS = 2^k and Q = LastPrime(k, ...), so an integer boundary loses nothing.
LOGQ_MIN, LOGQ_MAX = 8, 60


def active_model():
    """The security model in force, read WITHOUT importing Sage.

    Mirrors binfhe_params_helper.SECURITY_MODEL exactly. Duplicated deliberately:
    the whole point of this module's read path is that it works in a plain Python
    process, and that file imports the estimator at module scope.
    """
    return os.environ.get("ESTIMATOR_SECURITY_MODEL") or "standard"


def level_name(bits, quantum=False):
    """'STD128' / 'STD192Q' ... from a bit count, or None for a non-standard one.
    The legacy selector works in bits (paramstable.security_bits); the cache is
    keyed by level name, and this is the one place the two meet."""
    names = {128: "STD128", 192: "STD192", 256: "STD256"}
    base = names.get(int(bits))
    return None if base is None else base + ("Q" if quantum else "")


def _key(level, secret_dist, model, quantum, tolerance_bits=0):
    """Cache key. Tolerance is part of it: a boundary at tol=1 is a different
    number from one at tol=0, and silently reusing one for the other would be a
    security claim the estimator never made. tol=0 keeps the original key format
    so existing entries stay valid."""
    base = "%s|%s|%s|%s" % (model, level, secret_dist, "quantum" if quantum else "classical")
    return base if not tolerance_bits else "%s|tol%d" % (base, tolerance_bits)


def load(path=None):
    p = path or CACHE_PATH
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def save(cache, path=None):
    """Union whatever is on disk into `cache`, IN PLACE, then write it.

    Two bugs live here, both hit:

    A plain rewrite loses entries when two pricing processes overlap -- one key's
    count went from 12 dims back to 7 when a second pricer that had loaded the file
    earlier saved over it. Hence the union.

    The first union was worse than the bug it fixed. It built a fresh merged dict
    and REBOUND cache["entries"] to it, which orphaned the inner-dict alias the
    caller holds (`ents = c["entries"].setdefault(key, {})` in price_dims). Every
    write after the first save went into a dict no longer reachable from `cache`,
    so exactly one dimension per section survived: STD192/ternary priced 51 and
    stored 1. Merging must therefore mutate the existing containers, never replace
    them.

    setdefault, not update, on the disk side: entries already in `cache` are this
    process's own fresher work.
    """
    p = path or CACHE_PATH
    disk = load(p)
    cache.setdefault("entries", {})
    cache.setdefault("meta", {})
    # Stamp the estimator this cache was priced with. A boundary is a statement
    # ABOUT an estimator version, and these entries cost hours of Sage -- so they
    # get committed and travel. Without the stamp, a reader with a different
    # lattice-estimator silently inherits answers computed under another one,
    # which is the same class of error as reusing one security model's numbers
    # under another. Recorded, not enforced: the caller decides what to do about
    # a mismatch, but it can no longer fail to notice.
    cache["estimator_ref"] = ESTIMATOR_REF
    for k, ents in disk.get("entries", {}).items():
        tgt = cache["entries"].setdefault(k, {})
        for d, v in ents.items():
            tgt.setdefault(d, v)
    for k, v in disk.get("meta", {}).items():
        cache["meta"].setdefault(k, v)
    cache.setdefault("boundaries", {})
    for k, bnd in disk.get("boundaries", {}).items():
        tgt = cache["boundaries"].setdefault(k, {})
        for L, n in bnd.items():
            tgt.setdefault(L, n)
    with open(p, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)


def max_logq_certified(dim, level, secret_dist="ternary", model="standard",
                       quantum=False, cache=None, tolerance_bits=0):
    """Largest integer log2(q) certified at this dimension, or None if unpriced.

    None is not an error and must not be treated as a failure: it means nobody has
    run the estimator here yet. The caller decides whether that disqualifies a
    candidate (it should, for a search that claims to be security-priced) but the
    distinction between "unpriced" and "insecure" has to survive to the report, or
    a coverage gap reads as a security result.
    """
    c = cache if cache is not None else load()
    entries = c.get("entries", {}).get(
        _key(level, secret_dist, model, quantum, tolerance_bits), {})
    v = entries.get(str(int(dim)))
    return None if v is None else int(v)


def extra_dims(level, secret_dist="ternary", model="standard", quantum=False,
               tolerance_bits=0, cache=None, step=32):
    """Priced dimensions OFF the search's n grid for this curve: the shipped
    sets' n values and the security boundaries priced by `boundary`.

    Why the search enumerates these as well as its 32-step grid. The legacy
    selector found n=558 for STD128 at q_KS 2^15 -- the smallest n the
    estimator admits there -- by binary-searching n with a live estimator call.
    A 32-step grid has 544 (fails security at 2^15, so falls back to 2^14 and
    worse noise) and 576 (passes, with 18 wasted coefficients, ~2% of the
    gate). The boundary is where the cheapest secure candidate lives, and it is
    the one point a fixed-step grid is guaranteed to straddle. Pricing it once
    per (curve, q_KS) puts the legacy's up-front estimator work into the cache,
    where it is paid once and read millions of times.
    """
    c = cache if cache is not None else load()
    k = _key(level, secret_dist, model, quantum, tolerance_bits)
    dims = {int(d) for d in c.get("entries", {}).get(k, {})}
    dims |= {int(n) for n in c.get("boundaries", {}).get(k, {}).values()}
    return sorted(d for d in dims if d % step)


def _bisect_n(secure, lo, hi):
    """Smallest n in (lo, hi] with secure(n) True, given secure(lo) False and
    secure(hi) True. Security rises monotonically with n at fixed modulus, so
    bisection is valid. Pure, so it can be tested without an estimator."""
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if secure(mid):
            hi = mid
        else:
            lo = mid
    return hi


def boundary_dims(level, secret_dist="ternary", quantum=False, threads=4,
                  logq_values=range(12, 25), cache=None, path=None, verbose=True,
                  tolerance_bits=None):
    """For each log2(q_KS) in `logq_values`, find the smallest n the estimator
    admits and price that n fully into the cache. Requires Sage.

    The straddle comes from what is already priced: n_hi is the smallest priced n
    whose boundary reaches L, n_lo the largest priced n below it that does not.
    Bisecting between them costs ~5 estimator calls at the single modulus 2^L
    (secure / not secure), and the boundary n then gets the usual 6-call full
    pricing so it sits in `entries` like every other dimension. Recorded under
    cache['boundaries'][curve][L] as well, so the enumeration can find it.
    """
    if tolerance_bits is None:
        tolerance_bits = DEFAULT_TOLERANCE_BITS      # defined further down the module
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import binfhe_params_helper as h
    model = h.SECURITY_MODEL
    target = int(level.replace("STD", "").replace("Q", "")) - tolerance_bits
    c = cache if cache is not None else load(path)
    c.setdefault("entries", {}); c.setdefault("boundaries", {})
    k = _key(level, secret_dist, model, quantum, tolerance_bits)
    ents = c["entries"].get(k, {})
    bnd = c["boundaries"].setdefault(k, {})
    if not ents:
        print("no priced dimensions for %s: run `price` first" % k, flush=True)
        return c

    def secure_at(n, L):
        try:
            b = h.call_estimator(n, 1 << L, secret_dist, threads, quantum)
        except h.UnsupportedSecretDist:
            raise
        except OverflowError:
            return True
        except Exception:
            return False
        return b is not None and b >= target

    if verbose:
        print("boundary n per log2(q_KS) for %s (target %d bits)" % (k, target), flush=True)
        print("  %-7s %-6s %-6s %-6s %s" % ("logqKS", "n_lo", "n_hi", "n_b", "note"), flush=True)
    for L in logq_values:
        L = int(L)
        if str(L) in bnd:
            continue
        above = sorted(int(d) for d, b in ents.items() if int(b) >= L)
        if not above:
            if verbose: print("  %-7d %-6s %-6s %-6s no priced n admits 2^%d" % (L, "-", "-", "-", L), flush=True)
            continue
        n_hi = above[0]
        below = sorted(int(d) for d, b in ents.items() if int(d) < n_hi and int(b) < L)
        if not below:
            n_b = n_hi                    # the smallest priced n already admits L
            note = "smallest priced n already admits it"
        elif n_hi - below[-1] <= 1:
            n_b = n_hi
            note = "already exact"
        else:
            n_lo = below[-1]
            n_b = _bisect_n(lambda n: secure_at(n, L), n_lo, n_hi)
            note = "bisected in %d calls" % max(1, (n_hi - n_lo).bit_length() - 1)
        bnd[str(L)] = n_b
        if str(n_b) not in ents:
            price_dims([n_b], level, secret_dist, quantum, threads, cache=c, path=path,
                       verbose=False, tolerance_bits=tolerance_bits)
        save(c, path)
        if verbose:
            print("  %-7d %-6s %-6d %-6d %s (boundary %s)" % (L, below[-1] if below else "-", n_hi, n_b, note,
                                                             ents.get(str(n_b), "?")), flush=True)
    return c


def coverage(level=None, cache=None):
    """(key -> sorted dimensions priced), so a gap is visible rather than inferred."""
    c = cache if cache is not None else load()
    out = {}
    for k, ents in c.get("entries", {}).items():
        if level and ("|%s|" % level) not in k:
            continue
        out[k] = sorted(int(d) for d in ents)
    return out


# --------------------------------------------------------------------------
# pricing -- needs Sage, imported lazily so the read path above does not
# --------------------------------------------------------------------------

# Tolerance in bits, subtracted from the target when deciding the boundary.
#
# It must be explicit, because a strict `bits >= target` test on the estimator's
# INTEGER output rejects most of what OpenFHE ships -- by exactly one bit. Measured
# at the shipped parameters, model `standard`, classical:
#
#     STD128   n=556  q_KS=2^15   127.0 against 128      STD128   n=559  128.0
#     STD128_3 n=595  q_KS=2^16   127.0 against 128      STD128_4 n=635  128.0
#     STD192   n=821  q_KS=2^15   191.0 against 192      STD192_LMKCDEY  192.0
#     STD192_3 n=876  q_KS=2^16   191.0 against 192      STD256   n=1299 258.0
#     STD256   N=2048 logQ=29     255.0 against 256      STD256_3 n=1241 261.0
#
# So the shipped sets straddle their nominal levels by +/-1 bit, which is what
# parameters tuned to the boundary look like. At tolerance 0 the filter prunes
# STD128 itself, which makes it useless as a filter; at tolerance 1 it admits
# exactly what ships and still rejects anything genuinely weaker.
#
# Default 0 anyway. Whether 127 bits satisfies "STD128" is a policy question and
# not one this module should answer silently -- so strict is the default and the
# report says what strict costs.
DEFAULT_TOLERANCE_BITS = 0


def _bisect_boundary(price, target, lo=LOGQ_MIN, hi=LOGQ_MAX):
    """Largest integer L in [lo, hi] with price(L) >= target, or None.

    Bisection is valid because security falls monotonically as the modulus grows
    at fixed dimension.

    THE LOWER BOUND CANNOT DEPEND ON ONE CALL SUCCEEDING, and getting that wrong
    silently discarded 25 of 64 dimensions on the first run. At large dimension
    and small modulus the estimator raises

        OverflowError: cannot convert float infinity to integer

    because the work factor overflows -- the instance is so secure the number
    does not fit. Treating any exception as "cannot certify" therefore inverted
    the meaning of the most extreme security the estimator can express: `ok(lo)`
    came back False for every dim >= 1280 and the whole dimension was dropped.
    Calling that "conservative" was wrong; it was backwards.

    Two defences now. `price` maps that overflow to +inf, so it reads as secure.
    And the lower bound scans upward for the first modulus the estimator can price
    at all, so any other failure mode at the low end costs a step rather than the
    dimension.
    """
    def ok(L):
        b = price(L)
        return (b is not None) and (b >= target)

    # find a lower bound that is BOTH priceable and secure
    while lo < hi and not ok(lo):
        b = price(lo)
        if b is not None:
            return None          # priced and insecure: genuinely no boundary here
        lo += 1                  # unpriceable: try a larger modulus
    if lo >= hi:
        return None
    if ok(hi):
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def price_dims(dims, level, secret_dist="ternary", quantum=False, threads=4,
               cache=None, path=None, verbose=True,
               tolerance_bits=DEFAULT_TOLERANCE_BITS):
    """Fill the cache for these dimensions. Requires Sage.

    `tolerance_bits` is part of the cache key, because a boundary computed at one
    tolerance is not the boundary at another -- see DEFAULT_TOLERANCE_BITS.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import binfhe_params_helper as h          # pulls in Sage's estimator

    model = h.SECURITY_MODEL
    target = int(level.replace("STD", "").replace("Q", "")) - tolerance_bits
    c = cache if cache is not None else load(path)
    c.setdefault("entries", {})
    c.setdefault("meta", {})
    k = _key(level, secret_dist, model, quantum, tolerance_bits)
    c["meta"][k] = {
        "model_description": h.security_model_description(),
        "target_bits": target,
        "tolerance_bits": tolerance_bits,
    }
    ents = c["entries"].setdefault(k, {})

    if verbose:
        # flush=True throughout: `sage -python` block-buffers when stdout is not a
        # tty, so a long run under `docker logs` looked hung for minutes while it
        # was in fact 28 dimensions in. The incremental cache write is the other
        # progress signal.
        print("security model: %s" % h.security_model_description(), flush=True)
        print("level %s, target %d bits, %s secret, %s"
              % (level, target, secret_dist, "quantum" if quantum else "classical"), flush=True)
        print("  %-7s %-11s %-11s %s" % ("dim", "certified", "table", "delta"), flush=True)

    from paramstable import max_logq as table_logq
    for dim in dims:
        if str(dim) in ents:
            continue                              # already priced; cache is the point

        def price(L):
            """Security bits, +inf where the work factor overflows, None on failure.

            The overflow is not a failure: it is the estimator saying the instance
            is beyond what it can represent. Mapping it to None -- as this did --
            made maximum security indistinguishable from no answer.
            """
            try:
                return h.call_estimator(dim, 1 << L, secret_dist, threads, quantum)
            except h.UnsupportedSecretDist:
                raise
            except OverflowError:
                return float('inf')
            except Exception:
                return None

        b = _bisect_boundary(price, target)
        if b is None:
            if verbose:
                print("  %-7d %-11s %-11.2f %s"
                      % (dim, "NONE", table_logq(dim, level, secret_dist),
                         "not certifiable even at 2^%d" % LOGQ_MIN), flush=True)
            continue
        ents[str(dim)] = b
        save(c, path)                             # incremental: a long run is resumable
        if verbose:
            t = table_logq(dim, level, secret_dist)
            print("  %-7d %-11d %-11.2f %+.2f" % (dim, b, t, b - t), flush=True)
    return c


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_security')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p1 = sub.add_parser('price', help='fill the cache with estimator boundaries (needs Sage)')
    p1.add_argument('--level', required=True,
                    choices=('STD128', 'STD128Q', 'STD192', 'STD192Q', 'STD256', 'STD256Q'))
    p1.add_argument('--dist', default='ternary', choices=('ternary', 'error', 'uniform'))
    p1.add_argument('--quantum', action='store_true')
    p1.add_argument('--dims', help='comma-separated, or lo:hi:step (default 32:2048:32)')
    p1.add_argument('--threads', type=int, default=4)
    p1.add_argument('--tolerance', type=int, default=DEFAULT_TOLERANCE_BITS,
                    help='bits of slack against the level. 0 (default) is strict and '
                         'rejects most shipped sets by exactly 1 bit; see '
                         'DEFAULT_TOLERANCE_BITS.')

    pb = sub.add_parser('boundary', help='price the smallest secure n per q_KS into the cache (needs Sage)')
    pb.add_argument('--level', required=True)
    pb.add_argument('--dist', default='ternary', choices=('ternary', 'error', 'uniform'))
    pb.add_argument('--quantum', action='store_true')
    pb.add_argument('--logqks', default='12:24', help='lo:hi inclusive, default 12:24 (the search grid)')
    pb.add_argument('--threads', type=int, default=4)
    pb.add_argument('--tolerance', type=int, default=DEFAULT_TOLERANCE_BITS)

    p2 = sub.add_parser('coverage', help='which dimensions are priced')
    p2.add_argument('--level')

    p3 = sub.add_parser('compare', help='certified boundary against the closed-form table')
    p3.add_argument('--level', required=True)
    p3.add_argument('--dist', default='ternary')
    p3.add_argument('--quantum', action='store_true')

    a = ap.parse_args()
    if a.cmd == 'boundary':
        lo, hi = (int(x) for x in a.logqks.split(':'))
        boundary_dims(a.level, a.dist, a.quantum, a.threads, range(lo, hi + 1),
                      tolerance_bits=a.tolerance)
        sys.exit(0)

    if a.cmd == 'coverage':
        cov = coverage(a.level)
        if not cov:
            print("nothing priced yet (cache %s)" % CACHE_PATH)
        for k, dims in sorted(cov.items()):
            print("%-44s %4d dims  %d..%d" % (k, len(dims), dims[0], dims[-1]))
        sys.exit(0)

    if a.cmd == 'compare':
        from math import floor
        from paramstable import max_logq as table_logq
        model = active_model()
        c = load()
        ents = c.get("entries", {}).get(_key(a.level, a.dist, model, a.quantum), {})
        if not ents:
            sys.exit("nothing priced for %s" % _key(a.level, a.dist, model, a.quantum))
        print("certified vs table, %s, %s secret, %s, model %s"
              % (a.level, a.dist, "quantum" if a.quantum else "classical", model))
        print()
        print("Compared as INTEGERS. The table interpolates to a fractional log2(q),")
        print("but the search only ever proposes q_KS = 2^k and Q = LastPrime(k), so")
        print("what the table actually admits is floor() of its own row. Comparing the")
        print("certified integer against the raw fraction manufactures a half-step of")
        print("disagreement at almost every dimension and inverts the sign of the")
        print("summary -- it read '22 of 31 optimistic' before this was fixed.")
        print()
        print("  delta > 0: table CONSERVATIVE, the search is losing usable modulus")
        print("  delta < 0: table OPTIMISTIC, it admits what the estimator will not\n")
        print("  %-7s %-11s %-9s %-9s %s" % ("dim", "certified", "table", "admits", "delta"))
        deltas = []
        for d in sorted(int(x) for x in ents):
            t = table_logq(d, a.level, a.dist)
            adm = int(floor(t))
            b = ents[str(d)]
            deltas.append(b - adm)
            flag = "" if b == adm else ("  <<< OPTIMISTIC" if b < adm else "")
            print("  %-7d %-11d %-9.2f %-9d %+d%s" % (d, b, t, adm, b - adm, flag))
        deltas.sort()
        neg = [x for x in deltas if x < 0]
        pos = [x for x in deltas if x > 0]
        print("\n  %d dims: median %+d, range %+d .. %+d"
              % (len(deltas), deltas[len(deltas)//2], deltas[0], deltas[-1]))
        print("  table optimistic (would admit an uncertified modulus): %d dims" % len(neg))
        print("  table conservative (costs the search a modulus step):  %d dims" % len(pos))
        sys.exit(0)

    if a.dims and ':' in a.dims:
        lo, hi, step = (int(x) for x in a.dims.split(':'))
        dims = list(range(lo, hi + 1, step))
    elif a.dims:
        dims = [int(x) for x in a.dims.split(',')]
    else:
        dims = list(range(32, 2049, 32))
    price_dims(dims, a.level, a.dist, a.quantum, a.threads,
               tolerance_bits=a.tolerance)
