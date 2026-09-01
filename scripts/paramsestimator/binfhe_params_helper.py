#!/usr/bin/python

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from estimator import *
from math import log2, floor, sqrt, ceil, erfc, e
from scipy.special import erfcinv, erfcx
from statistics import fmean, stdev

import io
import os
import paramstable as stdparams
import shlex
import subprocess
import sys

# how many modulus doublings to try before declaring a candidate unpriceable
MAX_ESTIMATOR_RETRIES = 10

# Per-key noise scatter is ~2% and does not shrink with sample count, so a
# single key leaves several bits of irreducible error in log2Pf. Eight keys is
# the floor at which a failure-probability figure means anything.
DEFAULT_NUM_KEYS = 8

@contextmanager
def suppressed_stdout():
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = saved

# Single definition, in the model library: it is pure arithmetic with no Sage or
# OpenFHE dependency, and the DSE model needs it without importing this module.
# Re-exported so existing callers keep using helperfncs.digits_for_base.
from dse_model import digits_for_base

# starting point for the estimator: the largest modulus OpenFHE's own security
# tables consider safe at this dimension (see paramstable.max_logq)
def get_mod(dim, exp_sec_level, secret_dist = "ternary"):
    return ceil(stdparams.max_logq(dim, exp_sec_level, secret_dist))

# Which attacks to price, and at what reduction cost. This is a security
# policy, not an implementation detail: the estimator's own defaults have
# shifted between versions, so the choice is pinned here, selectable, and
# reported alongside results rather than inherited silently.
#
# Measured 2026-08-30 at (n=558, q=2^15, uniform ternary) -- the STD128
# winner's keyswitch pair -- on lattice-estimator 53da5982:
#
#   standard      128 bits   usvp 132, bdd 128, dual 136
#   conservative  124 bits   + dual_hybrid 124, the strongest attack of all
#
# "standard" reproduces the era the shipped OpenFHE tables were derived in and
# is the default so results stay comparable with them. "conservative" reflects
# current best-known attacks and is 4 bits lower.
SECURITY_MODELS = {
    "standard": {
        "deny":      ["bkw", "bdd_hybrid", "bdd_mitm_hybrid", "dual_hybrid", "dual_mitm_hybrid", "arora-gb"],
        "classical": "BDGL16",
        "quantum":   "LaaMosPol14",
    },
    "conservative": {
        "deny":      ["bkw", "arora-gb"],
        "classical": "MATZOV",
        "quantum":   "LaaMosPol14",
    },
}

SECURITY_MODEL = os.environ.get("ESTIMATOR_SECURITY_MODEL") or "standard"
if SECURITY_MODEL not in SECURITY_MODELS:
    raise ValueError("ESTIMATOR_SECURITY_MODEL must be one of %s, got %r"
                     % (sorted(SECURITY_MODELS), SECURITY_MODEL))

def security_model_description():
    m = SECURITY_MODELS[SECURITY_MODEL]
    return "%s (classical %s, quantum %s; denied: %s)" % (
        SECURITY_MODEL, m["classical"], m["quantum"], ", ".join(m["deny"]))

class UnsupportedSecretDist(ValueError):
    """An unimplemented secret distribution name.

    Kept distinct from the estimator failures that optimize_params_security
    retries: this one is a misconfiguration, and swallowing it turns the retry
    loop into a silent infinite loop.
    """

# (dim, mod, secret_dist, is_quantum) -> security bits.
_estimator_cache = {}

def estimator_cache_stats():
    return {"entries": len(_estimator_cache)}

# calls lattice-estimator to get the work factor for known attacks
def call_estimator(dim, mod, secret_dist="ternary", num_threads = 1, is_quantum = True):
    mod_key = int(mod) if float(mod).is_integer() else float(mod)
    key = (int(dim), mod_key, secret_dist, bool(is_quantum))
    if key in _estimator_cache:
        return _estimator_cache[key]

    if secret_dist == "error":
        params = LWE.Parameters(n=dim, q=mod, Xs=ND.DiscreteGaussian(3.19), Xe=ND.DiscreteGaussian(3.19))
    elif secret_dist == "ternary":
        params = LWE.Parameters(n=dim, q=mod, Xs=ND.Uniform(-1, 1, dim), Xe=ND.DiscreteGaussian(3.19))
    elif secret_dist == "uniform":
        params = LWE.Parameters(n=dim, q=mod, Xs=ND.UniformMod(mod), Xe=ND.DiscreteGaussian(3.19))
    else:
        raise UnsupportedSecretDist("invalid distribution for secret: " + repr(secret_dist))

    model = SECURITY_MODELS[SECURITY_MODEL]
    cost  = getattr(RC, model["quantum"] if is_quantum else model["classical"])

    with suppressed_stdout():
        estimateval = LWE.estimate(params, red_cost_model=cost,
                                   deny_list=model["deny"], jobs=num_threads)

    # Minimum over EVERY attack the estimator returned, not a hardcoded three.
    costs = [floor(log2(v["rop"])) for v in estimateval.values() if "rop" in v]
    if not costs:
        raise RuntimeError("estimator priced no attacks for n=%s q=%s" % (dim, mod))

    result = min(costs)
    _estimator_cache[key] = result
    return result

# Largest key-switch modulus Qks that still meets the security target at a FIXED
# dimension. Increasing Qks reduces bootstrapped noise, so the largest secure one
# is what the search wants: halve until the target is met, double until it is
# not, then step back one.
#
# Returns (dim, mod). The dimension is handed straight back -- nothing here
# changes it -- so that (0, 0) can serve as the "no answer" sentinel callers test.
def optimize_params_security(expected_sec_level, dim, mod, secret_dist = "ternary", num_threads = 1, is_quantum = True):
    """(dim, largest power-of-two modulus certified at `expected_sec_level` bits), or (0, 0).

    Answered from the DSE security cache when it can be, and PRICED INTO IT when
    it cannot. The cache stores, per (model, level, distribution, classical or
    quantum, tolerance), the largest integer log2(q) the estimator certifies at
    each dimension -- exactly what this function's doubling-and-halving walk used
    to compute from scratch on every call, at ~1 s per estimator run and six or
    so runs per dimension. A repeat run of the selector at the same level now
    spends no estimator time on dimensions it has seen, and every dimension its
    binary search on n visits becomes a priced point the DSE search enumerates
    (dse_security.extra_dims), so the two tools feed each other.

    Only standard levels (128/192/256 bits) map onto a cache curve; anything else
    falls through to the original walk. Tolerance is 0 here, as it always was:
    the selector certifies at the nominal level.
    """
    import dse_security as sec
    level = sec.level_name(expected_sec_level, is_quantum)
    if level is not None:
        cache = sec.load()
        b = sec.max_logq_certified(dim, level, secret_dist, SECURITY_MODEL, is_quantum,
                                   cache=cache, tolerance_bits=0)
        if b is None:
            sec.price_dims([int(dim)], level, secret_dist, is_quantum, num_threads,
                           cache=cache, verbose=False, tolerance_bits=0)
            b = sec.max_logq_certified(dim, level, secret_dist, SECURITY_MODEL, is_quantum,
                                       cache=cache, tolerance_bits=0)
        return (dim, 2 ** b) if b is not None else (0, 0)

    def price(m):
        """Security bits at (dim, m), or None where the estimator cannot price it."""
        try:
            return call_estimator(dim, m, secret_dist, num_threads, is_quantum)
        except UnsupportedSecretDist:
            raise
        except Exception:
            return None

    # Doubling the modulus is how a too-small starting point is escaped
    sec = None
    for _attempt in range(MAX_ESTIMATOR_RETRIES):
        sec = price(mod)
        if sec is not None:
            break
        mod = 2*mod
    if sec is None:
        print("estimator failed for dim %s after %d attempts, giving up on this candidate"
              % (dim, MAX_ESTIMATOR_RETRIES))
        return 0, 0

    mod1 = mod

    # too weak: shrink the modulus until the target is met
    while (sec < expected_sec_level):
        mod1 = mod1/2
        sec  = price(mod1)
        if sec is None:
            return 0, 0

    # then grow it back until it stops meeting the target, and take the last one
    # that did. Security falls monotonically as the modulus grows, so this ends.
    while True:
        prev_sec = sec
        mod1     = 2*mod1
        sec      = price(mod1)
        if sec is None:
            return 0, 0
        if ((prev_sec >= expected_sec_level) and (sec < expected_sec_level)):
            return dim, mod1/2

# Where the compiled binaries live.
#
# Defaults match both the documented native build (cmake -B build) and the
# Dockerfile, resolved against the repository root rather than the working
# directory so the scripts do not have to be run from one particular place.
# Override for an out-of-tree build:
#
#   ESTIMATOR_BUILD_DIR=/path/to/build
#
# There is ONE build. It used to be two, one per OpenFHE native word size, with
# a per-candidate dispatch to the 32-bit one when the ring modulus fit in 28
# bits. Since OpenFHE 9e8045db the 64-bit build narrows each bootstrapping key
# to its 32-bit internal form by default whenever that key's moduli fit, so the
# dispatch happens inside the library -- and the genuine NATIVE_SIZE=32 build
# was the worse measurement (no HAVE_INT128, so no lazy inner product; it loses
# past six gadget digits). ESTIMATOR_BUILD32_DIR is no longer read.
REPO_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BINARY_REL  = os.path.join("bin", "boolean_noise_estimate_script")

def _build_path(env_var, default_dir):
    root = os.environ.get(env_var) or os.path.join(REPO_ROOT, default_dir)
    return os.path.join(root, BINARY_REL)

BINARY = _build_path("ESTIMATOR_BUILD_DIR", "build")

# MAX_MODULUS_SIZE for NATIVEINT==32 (basicint.h). A parameter set whose ring
# modulus fits in this many bits gets the 32-bit internal accumulator inside the
# 64-bit build (roughly twice the gate speed); anything larger runs 64-bit.
NS32_MAX_LOGQ = 28

# OpenFHE declares BinFHEContextParams::modKS as uint32_t, so Qks must fit in
# 32 bits -- log2(Qks) <= 31. The search used to cap this at 30, discarding a bit
# of Qks for no representable reason; since LWE key switching is 68% of noise
# variance at STD128 and 88% at STD192, and sigma_ks scales with q/qKS, that bit
# is one of the most expensive in the parameter set.
MAX_LOG_QKS = 31

def clamp_moduli(logQ, logQks, ring_dim):
    """Clamp (log2 Q, log2 Qks) to what OpenFHE can represent. Returns ints.

    Safe to apply after the estimator has certified a candidate, and safe to
    apply twice: every clamp here only moves a modulus DOWN, and for LWE a
    smaller modulus at a fixed dimension is MORE secure, so a set certified
    before clamping is still certified after it.

    Three constraints, and the order matters:

    1. At ring dimension 1024, hold Q inside NS32_MAX_LOGQ so the candidate's
       refresh key qualifies for the 32-bit internal accumulator, which is ~2x
       faster per gate. Costs at most a bit of Q, and only for the distributions
       whose tables allow 29.
    2. Qks must fit in modKS (uint32_t).
    3. Qks must not exceed Q. This runs LAST on purpose: it is what keeps the
       N=1024 path inside the 28-bit cap once (1) has clamped Q.
    """
    logQ = int(logQ)
    if (ring_dim <= 1024):
        logQ = min(logQ, NS32_MAX_LOGQ)

    logQks = min(int(logQks), MAX_LOG_QKS, logQ)
    return logQ, logQks

def binary_for(logQ):
    """The one binary. Kept as a function so call sites read as they did when
    the word size was a dispatch decision; the library makes it now."""
    return BINARY

# Gates measured for the speed probe. The point is a stable per-gate time, not
# statistics, so this stays small.
SPEED_PROBE_GATES = 5

def _env_int(name, default):
    """An exported-but-empty or malformed value means "unset", not a traceback."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print("ignoring %s=%r: not an integer" % (name, raw))
        return default

def _cpu_count():
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1

def _read_int(path):
    try:
        with open(path) as f:
            v = f.read().strip()
        return None if v in ("max", "-1") else int(v)
    except (OSError, ValueError):
        return None

def _available_bytes():
    """Memory we may actually use, honouring a container limit if one is set.

    /proc/meminfo reports the HOST inside a container, so a cgroup-capped
    container would otherwise over-estimate wildly and fan out into an OOM.
    """
    avail = None
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass

    # cgroup v2, then v1
    limit = _read_int("/sys/fs/cgroup/memory.max")
    used  = _read_int("/sys/fs/cgroup/memory.current")
    if limit is None:
        limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        used  = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")

    # a "limit" of essentially all of RAM means unlimited
    if (limit is not None) and (limit < (1 << 62)):
        headroom = limit - (used or 0)
        avail = headroom if avail is None else min(avail, headroom)

    return avail

# What one measurement process holds, as a multiple of its serialized key size.
FOOTPRINT_FACTOR = 2

def _fanout(num_of_keys, per_proc_bytes = None):
    """How many measurement processes to run at once.

    Memory, not CPU, is usually the binding constraint: a key-switching key runs
    to hundreds of MB and into GB at large dimensions, so eight concurrent
    processes can want tens of GB. ESTIMATOR_MAX_PARALLEL overrides.
    """
    cap = _env_int("ESTIMATOR_MAX_PARALLEL", 0)
    if cap > 0:
        return max(1, min(cap, num_of_keys))

    n = _cpu_count()

    avail = _available_bytes()
    if (avail is not None) and per_proc_bytes:
        by_mem = int(avail * 0.7 // (per_proc_bytes * FOOTPRINT_FACTOR))
        if by_mem < n:
            print("limiting to %d concurrent measurements: %.1f GiB per process, %.1f GiB available"
                  % (max(1, by_mem), per_proc_bytes*FOOTPRINT_FACTOR/2**30, avail/2**30))
        n = min(n, by_mem)

    return max(1, min(n, num_of_keys))

def _key_bytes(perf):
    """Serialized key material a single measurement process holds."""
    total = 0
    for k in ("BootstrappingKeySize", "KeySwitchingKeySize"):
        if k in perf:
            try:
                total += int(perf[k].strip().split(' ')[0])
            except ValueError:
                return None
    return total or None

NO_ARG_FLAGS = ("-Z", "-h")

def _opts_to_cmd(opts, binary = None):
    logQ = None
    named = False
    for flag, value in opts:
        if (value is None) and (flag not in NO_ARG_FLAGS):
            raise ValueError("%s needs a value, got None -- a required parameter was not supplied" % flag)
        if flag == "-Q":
            logQ = value
        elif flag == "-p":
            named = True

    if binary is None:
        binary = binary_for(logQ)

    cmd = [binary]
    for flag, value in opts:
        cmd += [flag] if (value is None) else [flag, str(value)]
    return cmd

def _parse_noise(stderr, cmd):
    """Noise values are on stderr (OpenFHE's WITH_NOISE_DEBUG), so anything else
    written there corrupts the measurement. Name the offender rather than dying
    in float('')."""
    noise = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            noise.append(float(line))
        except ValueError:
            raise ValueError("unexpected output on the noise stream from %s: %s" % (cmd[0], repr(line)))
    return noise

def _require_binary(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            "%s not found.\n"
            "Build it with:  cmake -S %s -B <dir> -DCMAKE_BUILD_TYPE=Release && cmake --build <dir>\n"
            "then point ESTIMATOR_BUILD_DIR at <dir> if it is not '%s/build'."
            % (path, REPO_ROOT, REPO_ROOT))

def _run(opts, threads = None, binary = None):
    cmd = _opts_to_cmd(opts, binary)
    _require_binary(cmd[0])
    env = dict(os.environ)
    if threads is not None:
        env["OMP_NUM_THREADS"] = str(threads)

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if proc.returncode != 0:
        raise RuntimeError("%s exited %d:\n%s" % (cmd[0], proc.returncode, proc.stderr.strip()))

    return proc.stdout, _parse_noise(proc.stderr, cmd), cmd[0]

def run_estimate_binary(opts):
    """One measurement, one process, inherited thread count."""
    out, noise, _binary = _run(opts)
    if len(noise) < 2:
        raise ValueError("need at least 2 noise samples for a stddev, got %d" % len(noise))
    return out, noise

def measure_speed(opts, gates = SPEED_PROBE_GATES):
    """Per-gate time with OpenMP left unrestricted.

    Timing and noise want opposite setups. A gate uses every core it is given,
    so the representative time comes from one process with the whole machine;
    measured single-threaded it reads ~30% slower, and measured under the
    contention of a parallel noise run, worse still. So speed is probed on its
    own, before any of that.
    """
    probe = [(f, v) for f, v in opts if f not in ("-i", "-K")]
    probe += [("-i", gates), ("-K", 1)]
    out, _noise, binary = _run(probe)
    print("speed: %s (%s)" % (' '.join(shlex.quote(c) for c in _opts_to_cmd(probe, binary)),
                              "32-bit key forms where they fit"))
    perf = get_performance(out)
    return float(perf["EvalBinGateTimeUs"]), perf, binary

def measure_noise(opts, num_of_keys, max_parallel = None, per_proc_bytes = None, binary = None):
    """Pool noise over independent keys, one process per key.

    Each process gets an equal share of the cores (one apiece at full fan-out,
    more when memory forces a narrower one), so the machine stays busy either way.

    Keys are embarrassingly parallel while OpenMP inside a gate is not (measured
    5.1x on 8 cores), so this is where the wall-clock goes. Each process seeds
    its own PRNG, so the keys really are independent -- verified by comparing
    the streams of concurrent runs.
    """
    if max_parallel is None:
        max_parallel = _fanout(num_of_keys, per_proc_bytes)
    max_parallel = max(1, min(max_parallel, num_of_keys))

    threads_each = max(1, _cpu_count() // max_parallel)

    # -Z: skip size reporting. Serializing the keys to measure them is a
    # transient the size of the keys themselves, and it is pure waste in these
    # processes -- the speed probe already reported the sizes.
    per_key = [(f, v) for f, v in opts if f not in ("-K",)] + [("-K", 1), ("-Z", None)]
    if binary is None:
        binary = _opts_to_cmd(per_key)[0]

    print("noise: %d keys, %d at a time, %d thread(s) each: %s (%s)"
          % (num_of_keys, max_parallel, threads_each,
             ' '.join(shlex.quote(c) for c in _opts_to_cmd(per_key, binary)),
             "32-bit key forms where they fit"))

    noise = []
    out0  = None
    outs  = []
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        for out, part, _b in pool.map(lambda _: _run(per_key, threads=threads_each, binary=binary), range(num_of_keys)):
            if out0 is None:
                out0 = out
            outs.append(out)
            noise += part

    if len(noise) < 2:
        raise ValueError("need at least 2 noise samples for a stddev, got %d" % len(noise))

    _check_observed_failures(outs, opts, stdev(noise))

    return out0, noise


def _check_observed_failures(outs, opts, noise_stddev):
    """Cross-check the gates that actually failed against what sigma predicts.

    Every "failure rate" in this pipeline is computed from sigma. That is a claim
    about the noise DISTRIBUTION, and it is blind to a configuration whose
    bootstrapping key decodes a different secret than the ciphertext was
    encrypted under: the gate then returns a clean, low-noise encryption of the
    WRONG BIT, and no sigma-based number can see it.

    Measured instance, GINX with a GAUSSIAN secret. CGGI's KeyGenAcc
    (rgsw-acc-cggi.cpp:40) encodes the LWE secret as an indicator pair over
    {-1, 0, +1}:

        ek00[i] = RGSW(s == 1)     ek01[i] = RGSW(s == -1)

    so any coefficient with |s| >= 2 is encoded {0, 0}, indistinguishable from
    s == 0 -- and 63.7% of a stddev-3.19 Gaussian's coefficients are outside
    {-1,0,1} (P(s in {-1,0,1}) = 0.3632, computed, not estimated). At n=64, q=1024, N=1024, logQ=25 that produced 202 failures in 400
    gates, a coin flip, with sigma 12.9 against the ternary configuration's 10.3.
    AP and LMKCDEY both use the centered value and are unaffected;
    OpenFHE's isMethodCompatible() does not reject the pairing because it
    inspects the paramset enum, not keyDist.

    The bar is deliberately blunt: any observed failure at all, where the measured
    sigma says to expect essentially none, is a broken configuration rather than
    an unlucky sample.
    """
    flags = dict((f, v) for f, v in opts)
    inputs = int(flags.get("-I", 2))
    per_key_gates = int(flags.get("-i", 0))
    if per_key_gates <= 0:
        return

    observed, gates = 0, 0
    for out in outs:
        perf = get_performance(out)
        if "Failures" not in perf or "ctmodq" not in perf:
            return                                  # nothing to check against
        observed += int(perf["Failures"])
        gates    += per_key_gates
    if observed == 0:
        return

    ctmod    = int(get_performance(outs[0])["ctmodq"])
    log2pf   = get_decryption_failure(noise_stddev, 2 * inputs, ctmod, inputs)
    expected = gates * (2.0 ** log2pf) if log2pf > -400 else 0.0
    if expected >= 0.01:
        return                                      # failures are consistent with the noise

    raise RuntimeError(
        "%d of %d gates returned the wrong answer, but the measured sigma of %.4f "
        "predicts %.3g failures (log2Pf = %.1f).\n"
        "That is not a noise result: the configuration is semantically broken -- the "
        "gate is computing something other than the requested function.\n"
        "First thing to check: GINX (-t 2) requires a UNIFORM_TERNARY secret (-d 1). "
        "It encodes the secret as an indicator pair over {-1,0,+1}, so a GAUSSIAN "
        "secret silently loses every coefficient with |s| >= 2. Use LMKCDEY (-t 3) "
        "or AP (-t 1) for Gaussian secrets."
        % (observed, gates, noise_stddev, expected, log2pf))

def param_opts(param_set, samples_per_key, num_of_inputs):
    return [ ("-n", param_set.n),
             ("-q", param_set.q),
             ("-N", param_set.N),
             ("-Q", int(param_set.logQ)),
             ("-k", int(param_set.Qks)),
             ("-g", param_set.B_g),
             ("-b", param_set.B_ks),
             ("-r", param_set.B_rk),
             ("-s", param_set.sigma),
             ("-i", samples_per_key),
             ("-d", param_set.secret_dist),
             ("-t", param_set.bootstrapping_tech),
             ("-I", num_of_inputs),
           ]

def speed_of(param_set, num_of_inputs):
    """Per-gate microseconds and the key/ciphertext sizes, without a noise run.

    Cheap enough (one keygen plus a handful of gates) to run before deciding
    whether a candidate is worth measuring properly.
    """
    gate_us, perf, _binary = measure_speed(param_opts(param_set, 1, num_of_inputs))
    return gate_us, perf

def measure(param_set, samples_per_key, num_of_inputs, num_of_keys = DEFAULT_NUM_KEYS, gate_us = None, perf = None):
    """(noise stddev, noise mean, per-gate us, performance dict).

    Speed comes from one process with OpenMP unrestricted; noise comes from
    num_of_keys single-threaded processes in parallel. Pass gate_us/perf to
    reuse a probe already taken for pruning.

    The mean is reported because sigma is taken about the sample mean and so
    cannot see a per-key bias -- an always-zero gadget digit, for instance,
    reuses one key row on every switch and shifts the mean without widening the
    spread. That only becomes visible once several keys are pooled.
    """
    opts = param_opts(param_set, samples_per_key, num_of_inputs)

    binary = None
    if (gate_us is None) or (perf is None):
        gate_us, perf, binary = measure_speed(opts)

    _out, noise = measure_noise(opts, num_of_keys, per_proc_bytes=_key_bytes(perf), binary=binary)

    return stdev(noise), fmean(noise), gate_us, perf

def get_performance(text):
    """Pull the labelled figures the binary prints on stdout into a dict."""
    wanted = ("BootstrappingKeySize", "KeySwitchingKeySize", "CiphertextSize",
              "BootstrapKeyGenTime", "EvalBinGateTime", "EvalBinGateTimeUs", "NumKeys",
              "ctmodq", "Failures", "Gate")
    sized  = ("BootstrappingKeySize", "KeySwitchingKeySize", "CiphertextSize")

    perf = {}
    for line in text.splitlines():
        key, sep, val = line.partition(":")
        if sep and (key in wanted):
            perf[key] = val + (" bytes" if key in sized else "")

    return perf

def get_decryption_failure(noise_stddev, ptmod, ctmod, comp):
    x = (ctmod/(2*ptmod))/(sqrt(2*comp)*noise_stddev)
    # erfc(x) underflows to 0 above x ~= 27, and reporting that as 0 read as 2^0 --
    # an astronomically SAFE parameter set scoring as the worst possible one.
    # erfcx(x) = exp(x^2)*erfc(x) does not underflow, so this stays exact:
    #   log2(erfc(x)) = log2(erfcx(x)) - x^2*log2(e)
    return log2(erfcx(x)) - x*x*log2(e)

def get_target_noise(decryption_failure, ptmod, ctmod, comp):
    num = ctmod/(2*ptmod)
    denom = sqrt(2*comp)

    val = erfcinv(2**decryption_failure)
    target_noise = num/(denom*val)
    return target_noise

def test_range(val, low, hi):
    if val in range(low, hi+1):
        return
    else:
        msg = f"input not in valid range ({low} - {hi})"
        raise Exception(msg)
