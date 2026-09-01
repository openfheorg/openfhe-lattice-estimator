#!/usr/bin/python

'''Phase 5: adaptive verification -- measure the few candidates the model kept, and decide.

The enumerator ranks; this decides. It is the half of "predict-then-verify" that
does the verifying, and without it the pipeline stops at a predicted frontier and
nothing is ever confirmed.

    > python3 scripts/paramsestimator/dse_verify.py plan --target -64 --top 5
    > python3 scripts/paramsestimator/dse_verify.py decide verify.log --target -64

Three things this does differently from "measure eight keys and compare sigma"
--------------------------------------------------------------------------------

1. It certifies on a **per-key quantile**, not on pooled sigma. A deployment runs
   ONE key. Pooling samples across keys estimates the MEAN log2Pf, and a 2^-128
   claim is about the tail of the per-key distribution, not its centre. The pooled
   figure is systematically optimistic for the thing being claimed.

2. It folds each key's **realised bias** into that key's statistic rather than
   averaging it away. sigma is taken about the sample mean, so a per-key offset is
   invisible to it -- and the offsets are real: the rounding bias is
   (1/(2M))*(1 - SUM_i s_i), measured exactly, and the switching key contributes
   its own offset on top. `log2_pf` takes the bias with amplification `inputs`,
   because both gate inputs come from the same switching key and the offsets add
   coherently rather than in quadrature.

3. It **escalates the key count** until the decision is safe in both directions,
   and the stopping rule includes the quantile's own estimation error. This is not
   theoretical caution: the review records a candidate that read -118.7 at 3 keys
   and -110.9 at 8. Accepting on the 3-key number would have shipped a set 17 bits
   short of its target, and a rule that only checks "is the estimate past target"
   accepts exactly that way.

Why the harness runs one key per process
---------------------------------------
`boolean_noise_estimate_script -K k` pools its noise stream, so per-key figures are
not recoverable from it. Running `-K 1` k times gives k separable measurements, and
each process seeds its own PRNG so the keys really are independent. That is the
same reason `binfhe_params_helper.measure_noise` fans out one process per key.
'''

from math import log2, sqrt

import argparse
import re
import sys

import dse_model as m

# Standard normal quantile at 0.9, for the parametric quantile estimate.
Z_90 = 1.2816

# Quantile-estimation inflation. The standard error of an empirical p-quantile is
# about sqrt(p(1-p))/f(x_p) per sqrt(K); at p=0.9 on a roughly normal spread that
# is ~1.7 spreads per sqrt(K). Used rather than the raw order statistic because at
# K=4 the 0.9 order statistic IS the worst of four, which is far too noisy to
# accept on.
QUANTILE_SE_FACTOR = 1.7


def log2pf_sensitivity(sigma, inputs, q):
    """|d log2Pf / d ln sigma|, exactly, by central difference.

    The obvious linearization is 2*|log2Pf|: log2Pf = log2(erfcx(x)) - x^2*log2(e)
    with x proportional to 1/sigma, so the x^2 term contributes exactly 2. But the
    erfcx term works against it, and the true factor is 1.91 to 1.97 over the range
    these candidates occupy.

    A 1.4-4.6% over-statement sounds ignorable, and would be if this were being
    added. It is being SUBTRACTED -- it is the sampling variance removed from the
    observed spread -- so over-stating it drives the residual negative and reports
    "the scatter is not visible" when the arithmetic did the hiding.
    """
    h = sigma * 1e-6
    d = (m.log2_pf(sigma + h, inputs, q) - m.log2_pf(sigma - h, inputs, q)) / (2 * h)
    return abs(d * sigma)


def per_key_log2pf(keys, inputs, q):
    """log2Pf for each key, from that key's own sigma AND its own realised bias.

    `keys` is a list of (sigma, mean). The mean is the key's realised offset: the
    gate error is measured about zero, and sigma is taken about the sample mean, so
    the mean is exactly the part sigma cannot see.

    |bias| deliberately: an offset helps on one side of the decision boundary and
    hurts on the other, and the failure probability is set by the side it hurts.
    """
    return [m.log2_pf(sd, inputs, q, bias=abs(mean)) for sd, mean in keys]


def certify(keys, inputs, q, quantile=0.9, samples_per_key=None):
    """Certified log2Pf and its uncertainty, from per-key measurements.

    Returns (certified, uncertainty_bits, detail). `certified` is the quantile of
    the per-key distribution, estimated parametrically from the mean and spread
    rather than as an order statistic so it degrades gracefully at small K.

    THE OBSERVED SPREAD IS NOT THE PER-KEY SCATTER, and treating it as one made
    this stage measure its own noise. Each key's sigma carries a sampling error of
    1/sqrt(2*S), which propagates to log2Pf as

        d(log2Pf) = 2 * |log2Pf| * dsigma/sigma  ->  2*|log2Pf|/sqrt(2*S) bits

    At the first real run -- 16 keys, 400 gates each, |log2Pf| ~ 63.5 -- that is
    4.49 bits against an OBSERVED spread of 4.46. The whole spread was sampling;
    there was no room left for true scatter at all, and the certified quantile came
    out 5.7 bits below the mean key purely as measurement noise. A candidate would
    have been rejected for the harness's sample count.

    So the sampling variance is subtracted. Variances add, so

        true_var = observed_var - sampling_var

    and where that is negative the data cannot see the scatter -- reported as such
    rather than clamped and presented as a measurement. `samples_per_key` is
    therefore REQUIRED for a deconvolved estimate; without it the raw spread is
    used and flagged.
    """
    if len(keys) < 2:
        raise ValueError("need at least 2 keys to estimate a spread, got %d" % len(keys))
    pf = per_key_log2pf(keys, inputs, q)
    K = len(pf)
    mean_pf = sum(pf) / K
    obs = sqrt(sum((x - mean_pf) ** 2 for x in pf) / (K - 1))

    if samples_per_key:
        mean_sd = sum(sd for sd, _ in keys) / K
        samp = log2pf_sensitivity(mean_sd, inputs, q) / sqrt(2.0 * samples_per_key)
    else:
        samp = 0.0
    resid = obs * obs - samp * samp
    deconvolved = resid > 0
    # When the data cannot see the per-key scatter, ASSUME IT AT ITS MEASURED
    # BOUND rather than at zero. Zero was what this did, and it made a
    # sampling-limited run certify the MEAN key as the 90th-percentile key: the
    # adaptive test's four STD128 rows all came back with spread 0.00 and a
    # certified value equal to their mean, which is optimistic by the whole
    # quantile penalty (about 2.3 bits at -64 for the 1.38% bound). The only
    # reason those verdicts were not wrong was that the model band, applied
    # after measurement where it does not belong, happened to be the same size.
    # With the floor in place the band can come off measured candidates and
    # the measurement decides them.
    if deconvolved:
        spread = sqrt(resid)
        scatter_assumed = False
    else:
        spread = log2pf_sensitivity(mean_sd if samples_per_key else sum(sd for sd, _ in keys) / K,
                                    inputs, q) * m.KEY_SCATTER_BOUND
        scatter_assumed = True

    certified = mean_pf + Z_90 * spread

    # Uncertainty on the quantile. Two parts:
    #  - the mean itself, whose standard error is obs/sqrt(K);
    #  - the spread, whose own error is sqrt(2/(K-1))*obs_var in variance terms.
    # The second is deliberately computed from the OBSERVED variance, not the
    # deconvolved one: when sampling dominates, the deconvolution is barely
    # determined and the band has to say so instead of collapsing to zero.
    se_mean = obs / sqrt(K)
    se_var = sqrt(2.0 / (K - 1)) * (obs * obs)
    se_spread = se_var / (2.0 * spread) if spread > 1e-9 else se_var / (2.0 * max(obs, 1e-9))
    q_se = sqrt(se_mean ** 2 + (Z_90 * se_spread) ** 2)

    detail = dict(keys=K, mean_log2pf=mean_pf, spread=spread, observed_spread=obs,
                  sampling_spread=samp, deconvolved=deconvolved, scatter_assumed=scatter_assumed,
                  sampling_dominates=(samp >= obs),
                  worst=max(pf), best=min(pf), quantile_se=q_se,
                  samples_per_key=samples_per_key, per_key=pf)
    return certified, q_se, detail


def decide(keys, inputs, q, target, quantile=0.9, samples_per_key=None,
           model_band=0.0):
    """'pass', 'fail' or 'more keys', with the numbers behind it.

    The rule is two-sided on purpose:

        pass          certified + uncertainty <= target   even the pessimistic end clears
        fail          certified - uncertainty >  target   even the optimistic end misses
        more keys     otherwise, and MEASUREMENT is what is blocking it
        model-limited otherwise, and the MODEL BAND is what is blocking it

    The last two are both "undecided", split because they call for opposite
    actions and naming them the same thing sent c0 off to buy keys that could
    not have decided it.

    A one-sided rule ("is the estimate past target") is what accepts a 3-key
    reading that eight keys would have contradicted. Requiring the whole interval
    to land on one side is what makes the escalation terminate honestly rather than
    at the first favourable draw.
    """
    certified, q_se, detail = certify(keys, inputs, q, quantile, samples_per_key)
    # `model_band` is for a candidate whose log2Pf is still a PREDICTION. A
    # measured candidate's uncertainty is the measurement's (q_se, which now
    # includes the scatter floor above); adding the model's prediction error on
    # top double-counts, and it left every measured row in the adaptive test
    # "model-limited" -- undecidable by construction. Pass 0 for measured rows.
    unc = sqrt(q_se ** 2 + model_band ** 2)
    detail.update(certified=certified, uncertainty=unc, model_band=model_band,
                  target=target, margin=target - certified)
    if certified + unc <= target:
        return 'pass', detail
    if certified - unc > target:
        return 'fail', detail
    # Undecided -- but WHICH term is blocking it is the actionable part. More
    # keys shrink quantile_se and do precisely nothing to the model band.
    if model_band >= q_se:
        return 'model-limited', detail
    return 'more keys', detail


# --------------------------------------------------------------------------
# measurement plan
# --------------------------------------------------------------------------
# Doubling, because the uncertainty falls as 1/sqrt(K): each step buys a factor
# 1.41, and a finer schedule spends measurements to shrink a band that is already
# dominated by the model term once K is past ~16.
KEY_SCHEDULE = (4, 8, 16, 32, 64)


# Per-key sigma scatter, now MEASURED -- see dse_model for the derivation and
# why there are two of them. Sizing uses the POINT estimate: assuming a bigger
# scatter than the truth asks for too few keys.
ASSUMED_KEY_SCATTER = m.KEY_SCATTER_POINT


def resolving_keys(samples_per_key, scatter=ASSUMED_KEY_SCATTER):
    """Keys needed for the deconvolution to resolve the per-key scatter.

    In bits, the true spread is 2*|log2Pf|*scatter and the sampling spread is
    2*|log2Pf|/sqrt(2S), so their variance ratio is

        r = 1 / (2*S*scatter^2)          -- 6310/S at the measured 0.89%

    and |log2Pf| cancels, which is why one sizing rule covers every candidate. The
    variance estimate from K keys carries relative error sqrt(2/(K-1)), so the
    difference is resolved when sqrt(2/(K-1))*(1+r) < 1:

        K > 1 + 2*(1 + r)^2

    Total gates K*S is flattest near r = 1, i.e. S = 1/(2*scatter^2) and K ~ 10.
    At the measured 0.89% that is S = 6310, K = 10 -- 63k gates per candidate,
    against the 15k that run 2 spent (S=1250, K=12) and could not resolve it.
    Resolving a SMALLER scatter costs more, so measuring it down from 2% to
    0.89% raised this bill 4x rather than lowering it.
    """
    if not samples_per_key:
        return None
    r = 1.0 / (2.0 * samples_per_key * scatter ** 2)
    return int(1 + 2 * (1 + r) ** 2) + 1


def sizing_advice(samples_per_key, keys):
    """One line saying what to change, given where the run actually is."""
    need = resolving_keys(samples_per_key)
    if need is None:
        return "pass --samples-per-key to get a deconvolved estimate at all."
    best_s = int(1.0 / (2.0 * ASSUMED_KEY_SCATTER ** 2))
    best_k = resolving_keys(best_s)
    if keys >= need:
        return ("K=%d is already enough at %d gates/key; the scatter is genuinely "
                "smaller than the assumed %.0f%%." % (keys, samples_per_key,
                                                      100 * ASSUMED_KEY_SCATTER))
    return ("at %d gates/key this needs K=%d (has %d). Cheaper in total gates: "
            "%d gates/key with K=%d (%d vs %d gates)."
            % (samples_per_key, need, keys, best_s, best_k,
               best_s * best_k, samples_per_key * need))


def command(cand, samples, build=None):
    """One-key measurement command for a candidate dict (as dse_enumerate emits).

    -K 1: per-key figures are the whole point, and -K k pools them.
    """
    gm = cand['gadget_map']
    # One build. A logQ <= 28 candidate gets the 32-bit internal key forms from
    # the library's own default (9e8045db); the genuine NATIVE_SIZE=32 build
    # this used to dispatch to is gone, and was the wrong path to measure anyway.
    b = build or "build"
    extra = " -a %d" % cand['autokeys'] if cand.get('method') == 'LMKCDEY' else ""
    # -r is AP's REFRESH BASE, and for AP it must come from the candidate: it sets
    # the external-product count, so it drives the noise, the gate time and the
    # key size all three. This was hardcoded at 64, which measured a different
    # configuration from the one searched for every AP row whose base was not 64 --
    # silently, because the run succeeds and reports a plausible sigma. 64 remains
    # the default for the methods that do not use it, matching every shipped set.
    base_r = cand.get('base_r') or 64
    return ("%s/bin/boolean_noise_estimate_script -n %d -q %d -N %d -Q %d -k %d "
            "-G %s -b %d -r %d -s %.2f -d %d -t %d%s -I %d -i %d -K 1 -Z"
            % (b, cand['n'], cand['q'], cand['N'], cand['log_q_big'], cand['q_ks'],
               ",".join("%d:%d" % kv for kv in sorted(gm.items())),
               cand['base_ks'], base_r, cand['sigma'],
               0 if cand.get('key_dist') == 'GAUSSIAN' else 1,
               {'AP': 1, 'GINX': 2, 'LMKCDEY': 3}[cand.get('method', 'GINX')],
               extra, cand['inputs'], samples))


_KV = re.compile(r'(\w+)=(\S+)')


def read_keys(path, label=None):
    """Per-key (sigma, mean) from a verification log.

    Accepts the runner's `record|` lines, one per key. `label` selects one
    candidate's rows where a log holds several.
    """
    out = {}
    for line in open(path):
        if not line.startswith('record|'):
            continue
        d = dict(_KV.findall(line))
        if d.get('sigma') in (None, 'NA'):
            continue
        k = d.get('label') or d.get('set') or 'candidate'
        out.setdefault(k, []).append((float(d['sigma']), float(d.get('mean', 0.0))))
    if label:
        return out.get(label, [])
    return out


def read_failures(path):
    """{label: (wrong answers, rows carrying the count, rows without it)}.

    Sigma is a statement about the noise distribution, not about whether the
    gate answered correctly, and the two come apart: a GINX context handed a
    Gaussian secret returned 202 wrong answers in 400 gates with a sigma that
    looked entirely normal. So a verdict that never reads this field is a
    verdict about the wrong question, and a log that does not carry the field
    cannot answer it at all -- which is reported, not assumed to be zero.
    """
    out = {}
    for line in open(path):
        if not line.startswith('record|'):
            continue
        d = dict(_KV.findall(line))
        if d.get('sigma') in (None, 'NA'):
            continue
        k = d.get('label') or d.get('set') or 'candidate'
        tot, have, miss = out.get(k, (0, 0, 0))
        raw = d.get('FAILURES')
        if raw is None or not raw.lstrip('-').isdigit():
            out[k] = (tot, have, miss + 1)
        else:
            out[k] = (tot + int(raw), have + 1, miss)
    return out


def report(groups, inputs, q, target, samples_per_key=None, model_band=0.0,
           log=None):
    print("%-22s %-5s %-9s %-9s %-8s %-9s %-8s %s"
          % ("candidate", "keys", "mean", "spread*", "certified", "+-unc", "margin", "verdict"))
    print("(* spread is DECONVOLVED: the harness's own sampling error removed)")
    fails = read_failures(log) if log else {}
    rc = 0
    for name, keys in sorted(groups.items()):
        if len(keys) < 2:
            print("%-22s %-5d (need >= 2 keys)" % (name, len(keys)))
            rc = 1
            continue
        verdict, d = decide(keys, inputs, q, target,
                            samples_per_key=samples_per_key, model_band=model_band)
        wrong, have, miss = fails.get(name, (0, 0, 0))
        if wrong:
            # No margin in bits survives a gate that decrypts incorrectly, so
            # this overrides the verdict rather than annotating it.
            verdict = 'fail'
        print("%-22s %-5d %-9.2f %-9.2f %-8.2f %-9.2f %-8.2f %s"
              % (name, d['keys'], d['mean_log2pf'], d['spread'], d['certified'],
                 d['uncertainty'], d['margin'], verdict.upper()))
        if wrong:
            print("    WRONG ANSWERS: %d over %d key(s). This configuration decrypts"
                  % (wrong, have))
            print("    incorrectly; no sigma sees that, and no margin excuses it.")
        elif miss and not have:
            print("    CORRECTNESS NOT CHECKED: no FAILURES field on any of this"
                  " candidate's")
            print("    %d row(s). The verdict above is about sigma alone." % miss)
        elif miss:
            print("    NOTE: %d of %d row(s) carry no FAILURES field; the other %d report"
                  " none." % (miss, miss + have, have))
        if d['sampling_dominates']:
            print("    SAMPLING-LIMITED: observed spread %.2f bits, of which %.2f is the"
                  % (d['observed_spread'], d['sampling_spread']))
            print("    harness's own sampling error, so the per-key scatter is not visible;")
            print("    the certified quantile assumes it at the measured bound (%.1f%% of sigma, %.2f bits)."
                  % (100 * m.KEY_SCATTER_BOUND, d['spread']))
            # Only worth spending gates on if measurement is what is in the way.
            if verdict != 'model-limited':
                print("    %s" % sizing_advice(d['samples_per_key'], d['keys']))
        if verdict in ('more keys', 'model-limited'):
            # Which term is stopping the decision decides what to do about it, and
            # they call for opposite actions. c0 sat at margin 0.10 with a total
            # uncertainty of 2.82 of which 2.70 was the MODEL band -- escalating
            # keys there buys nothing at all.
            meas_part = d['quantile_se']
            if verdict == 'model-limited':
                print("    MODEL-LIMITED: of +-%.2f bits, %.2f is the model band and only "
                      "%.2f is" % (d['uncertainty'], d['model_band'], meas_part))
                print("    measurement. More keys cannot decide this candidate; tightening a "
                      "model term can.")
            else:
                nxt = next((k for k in KEY_SCHEDULE if k > d['keys']), None)
                print("    MEASUREMENT-LIMITED: %.2f of +-%.2f bits is measurement. %s"
                      % (meas_part, d['uncertainty'],
                         "Escalate to K=%d." % nxt if nxt else "At the top of the schedule."))
        if verdict == 'fail':
            rc = 2
    if log:
        tot = sum(v[0] for v in fails.values())
        have = sum(v[1] for v in fails.values())
        if have:
            print("\n  correctness: %d wrong answer(s) over %d measured key(s)"
                  % (tot, have))
    else:
        print("\n  correctness: not checked -- no log given to read FAILURES from")
    return rc


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_verify')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('decide', help='decide from a verification log')
    p1.add_argument('log')
    p1.add_argument('--target', type=float, required=True)
    p1.add_argument('--inputs', type=int, default=2)
    p1.add_argument('-q', '--ct-modulus', type=int, required=True, dest='q')
    p1.add_argument('--samples-per-key', type=int)
    p1.add_argument('--model-band', type=float, default=0.0,
                    help='the candidate log2pf_band from dse_model, in bits')
    a = ap.parse_args()
    groups = read_keys(a.log)
    if not groups:
        sys.exit("no per-key records in %s" % a.log)
    sys.exit(report(groups, a.inputs, a.q, a.target, a.samples_per_key,
                    a.model_band, log=a.log))
