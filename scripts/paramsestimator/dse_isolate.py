#!/usr/bin/python

'''Parse and check isolated post-accumulator measurements (stage 4).

`boolean_keyswitch_isolate` measures the three terms that live AFTER the
accumulator, each on its own, against a ciphertext whose error is exactly zero.
None of those three has a free constant in the model, so this is a TEST rather
than a calibration -- which is what makes it able to shrink the keyswitch band
from the +-10% carried through the sweep to something sampling-limited.

    > python3 scripts/paramsestimator/dse_isolate.py stage4.log
    > python3 scripts/paramsestimator/dse_isolate.py stage4.log --stage round2

Why it is worth the trouble: at STD128 key switching is 61% of noise variance and
the accumulator 23%, so the keyswitch term's band costs 7.5 bits of log2Pf
against the accumulator's 5.7. Pinning it is worth about 3.1 bits per candidate.

Predictions, all derived, none fitted
-------------------------------------
    round2  Var(s)/12 * (1 - 1/M^2) * n  +  (1 - 1/M^2)/12          at q,   M = q_KS/q
    round1  Var(s)/12 * N               +  1/12                     at q_KS
    ksonly  N * d_KS * sigma^2                                      at q_KS
    tail    ks + round2 + round1 * (q/q_KS)^2                        at q

The `+ 1/12` and `+ (1-1/M^2)/12` tails are the b component's own rounding error,
which carries no secret factor. They are under 0.3% of the total everywhere here
and are included only so the prediction has no hand-waved omission.

Three predictions beyond the totals
-----------------------------------
1. round2's per-key MEAN is (1/(2M)) * (1 - SUM_i s_i), computable from the key
   rather than fitted. Round-half-up on an M-point grid biases every element by
   +1/(2M); the phase inherits it with the secret's sign.
2. ksonly's variance splits. A digit position selects one of baseKS stored rows
   per sample, so WITHIN a key the variance is (1 - 1/baseKS) of the total and
   the remaining 1/baseKS shows up as a fixed per-key offset. A deployment runs
   one key and sees that as bias, not noise.
3. round1 is invisible in a gate measurement -- (q/q_KS)^2 scales it to about
   0.1% of variance -- which is why fitting its coefficient returned zero. It is
   measurable here because it is read at q_KS before the second switch.
'''

from math import log2, sqrt

import argparse
import re
import sys

import dse_model as m

_KV = re.compile(r'(\w+)=(\S+)')


def records(path):
    """One dict per (stage, set), carrying the param line, per-key rows and the pool.

    Param-line fields land at the top level as strings; `key_rows` and `pool`
    hold floats. The two `keys=` fields on different lines mean different things
    (the requested count and the delivered count) -- only the pooled one is kept.
    """
    out, cur = [], None
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('stage='):
                cur = dict(stage=line.split('=', 1)[1], key_rows=[], inputs=[])
            elif cur is None:
                continue
            elif line.startswith('param '):
                # samples= and keys= appear on both the param line and the pooled
                # line; the pooled one is authoritative, and `keys` would collide
                # with the per-key row list, so both are dropped here.
                p = {k: v for k, v in _KV.findall(line)}
                p.pop('keys', None)
                p.pop('samples', None)
                cur.update(p)
            elif line.startswith('input '):
                d = {k: v for k, v in _KV.findall(line)}
                cur['inputs'].append(float(d['phase']))
            elif line.startswith('key '):
                cur['key_rows'].append({k: float(v) for k, v in _KV.findall(line)})
            elif line.startswith('pooled '):
                cur['pool'] = {k: float(v) for k, v in _KV.findall(line)}
                out.append(cur)
                cur = None
    return out


def _geom(r):
    """The numbers the predictions need, as floats, from the param line."""
    return dict(N=int(r['N']), n=int(r['n']), q=float(r['q']), Q=float(r['Q']),
                qks=float(r['qKS']), bks=int(r['baseKS']), dks=int(r['dks_lib']),
                sigma=float(r['sigma']), keydist=r['keydist'], p=int(r['p']))


def predict(r):
    """(pooled, within-key, between-key) predicted sigma. None where no claim is made.

    The three are not independent: a pooled stddev taken about the GLOBAL mean
    picks up both the within-key spread and the scatter of the per-key means, so
    the pooled prediction is their quadrature sum. Reporting only a pooled figure
    against a within-key prediction charges the model for a term it got right --
    which is what made the first read of these rows look 1.5% high.

    Where the log carries the secrets' second moments the prediction is made PER
    KEY and then aggregated, because the rounding variance is Var(d) * SUM_i s_i^2
    and that sum scatters across keys by more than this program's sampling floor.
    Using n*Var(s) instead is a second, avoidable source of disagreement.
    """
    g    = _geom(r)
    vs   = m.SECRET_VARIANCE[g['keydist']]
    M    = g['qks'] / g['q']
    grid = 1.0 - 1.0 / (M * M) if M >= 1.0 else 1.0
    rows = r['key_rows']
    exact = bool(rows) and ('sumsq_s' in rows[0])

    def aggregate(var_k, mean_k):
        """Match how the program pools: within = rms of per-key sd, between = sd of means."""
        w = sqrt(sum(var_k) / len(var_k))
        if len(mean_k) > 1:
            mm = sum(mean_k) / len(mean_k)
            b = sqrt(sum((x - mm) ** 2 for x in mean_k) / (len(mean_k) - 1))
        else:
            b = 0.0
        return sqrt(w * w + b * b), w, b

    def statistical(w, b):
        return sqrt(w * w + b * b), w, b

    if r['stage'] == 'round2':
        vd = grid / 12.0
        bias = (1.0 / (2.0 * M)) if M >= 1.0 else 0.0
        if exact:
            return aggregate([vd * k['sumsq_s'] + vd for k in rows],
                             [bias * (1.0 - k['sum_s']) for k in rows])
        return statistical(sqrt(vd * g['n'] * vs + vd), bias * sqrt(g['n'] * vs))

    if r['stage'] == 'round1':
        # read at q_KS. M1 = Q/q_KS is not an integer, so neither the finite-grid
        # variance correction nor the +1/(2M) bias applies: the error is
        # continuous to measurement precision (mean -1.3e-6 over 400k samples).
        vd = 1.0 / 12.0
        if exact:
            return aggregate([vd * k['sumsq_sN'] + vd for k in rows], [0.0] * len(rows))
        return statistical(sqrt(vd * g['N'] * vs + vd), 0.0)

    if r['stage'] == 'ksonly':
        v = g['N'] * g['dks'] * g['sigma'] ** 2
        # Delegate the split to the model rather than restating it here. This used
        # to hard-code the UNIFORM form (v/baseKS for the offset), which is only
        # right when q_KS is an exact power of baseKS; keyswitch_variance_split
        # accounts for a misaligned q_KS confining the TOP digit to fewer values.
        # Restating a formula the model already owns is how the two drifted apart.
        w_at_q, b_at_q = m.keyswitch_variance_split(g['N'], g['q'], g['qks'],
                                                    g['bks'], g['sigma'])
        # the model works at q; ksonly reads at q_KS
        sc = (g['qks'] / g['q']) ** 2
        return sqrt(v), sqrt(w_at_q * sc), sqrt(b_at_q * sc)

    if r['stage'] == 'tail':
        v_ks = m.sigma_keyswitch(g['N'], g['q'], g['qks'], g['bks'], g['sigma']) ** 2
        vd2  = grid / 12.0
        vd1  = (1.0 / 12.0) * (g['q'] / g['qks']) ** 2
        bias = (1.0 / (2.0 * M)) if M >= 1.0 else 0.0
        if exact:
            var_k = [v_ks * (1.0 - 1.0 / g['bks']) + vd2 * (k['sumsq_s'] + 1.0)
                     + vd1 * (k['sumsq_sN'] + 1.0) for k in rows]
            mean_k = [bias * (1.0 - k['sum_s']) for k in rows]
            pooled, w, b = aggregate(var_k, mean_k)
            # the switching key's own offset adds to the scatter of the means
            b = sqrt(b * b + v_ks / g['bks'])
            return sqrt(w * w + b * b), w, b
        w = sqrt(v_ks * (1.0 - 1.0 / g['bks']) + vd2 * (g['n'] * vs + 1.0)
                 + vd1 * (g['N'] * vs + 1.0))
        b = sqrt(v_ks / g['bks'] + (bias ** 2) * g['n'] * vs)
        return statistical(w, b)

    raise ValueError('unknown stage %r' % r['stage'])


def bias_prediction(r, key_row):
    """round2/tail per-key mean: (1/(2M)) * (1 - SUM_i s_i), exact given the key."""
    g = _geom(r)
    M = g['qks'] / g['q']
    if r['stage'] not in ('round2', 'tail') or M < 1.0:
        return None
    return (1.0 / (2.0 * M)) * (1.0 - key_row['sum_s'])


def report(recs, only=None):
    bad = 0
    for r in recs:
        if only and r['stage'] != only:
            continue
        g = _geom(r)
        pool = r['pool']
        pred, pred_w, pred_b = predict(r)
        n_samp = pool['samples']
        # relative error on a stddev from n samples; the honest floor on any claim below
        floor = 100.0 / sqrt(2.0 * n_samp)

        # the input is noiseless by construction, so a nonzero phase means the
        # arithmetic is wrong and the rest of the row is meaningless
        if any(abs(x) > 0 for x in r['inputs']):
            print("!! %s %s: input phase nonzero (%s) -- measurement invalid"
                  % (r['stage'], r.get('set'), r['inputs'][:3]))
            bad += 1
            continue

        err = 100.0 * (pool['sd'] / pred - 1.0)
        # The tolerance is 0.5% or 3x the sampling floor, whichever is larger.
        # At 800k samples the floor is 0.08%, tighter than effects that are real
        # and not modelled here -- discrete-Gaussian variance is not exactly
        # sigma^2, and the per-key mean is estimated from a finite sample.
        tol = max(0.5, 3 * floor)
        flag = '' if abs(err) < tol else '  <<<'
        print("%-7s %-20s n=%-5d N=%-5d M=%-5g dKS=%d %-16s"
              % (r['stage'], r.get('set', '-'), g['n'], g['N'], g['qks'] / g['q'],
                 g['dks'], g['keydist']))
        print("        pooled sigma  measured %10.4f  predicted %10.4f  %+7.2f%%  (sampling floor +-%.2f%%)%s"
              % (pool['sd'], pred, err, floor, flag))
        if abs(err) >= tol:
            bad += 1

        if pred_w is not None:
            ew = 100.0 * (pool['within_sd'] / pred_w - 1.0)
            print("        within  key   measured %10.4f  predicted %10.4f  %+7.2f%%"
                  % (pool['within_sd'], pred_w, ew))
        if pred_b:
            eb = 100.0 * (pool['between_sd'] / pred_b - 1.0)
            # K keys give only 1/sqrt(2K) on a stddev of means: 25% at K=8
            kfloor = 100.0 / sqrt(2.0 * pool['keys'])
            print("        between key   measured %10.4f  predicted %10.4f  %+7.2f%%  (+-%.0f%% at K=%d)"
                  % (pool['between_sd'], pred_b, eb, kfloor, int(pool['keys'])))

        # per-key VARIANCE, checked key by key where the second moment is logged
        rows_all = r['key_rows']
        if rows_all and ('sumsq_s' in rows_all[0]):
            vs_  = m.SECRET_VARIANCE[g['keydist']]
            Mv   = g['qks'] / g['q']
            gridv = 1.0 - 1.0 / (Mv * Mv) if Mv >= 1.0 else 1.0
            if r['stage'] in ('round2', 'round1'):
                if r['stage'] == 'round2':
                    pv = [sqrt((gridv / 12.0) * (k['sumsq_s'] + 1.0)) for k in rows_all]
                else:
                    pv = [sqrt((1.0 / 12.0) * (k['sumsq_sN'] + 1.0)) for k in rows_all]
                e = [100.0 * (k['sd'] / p - 1.0) for k, p in zip(rows_all, pv)]
                kf = 100.0 / sqrt(2.0 * (pool['samples'] / pool['keys']))
                print("        per-key sigma  worst %+.2f%%  rms %.2f%%   (per-key sampling floor +-%.2f%%)"
                      % (max(e, key=abs), sqrt(sum(x * x for x in e) / len(e)), kf))

        # the per-key bias, checked key by key against a value computed from the key
        rows = [k for k in r['key_rows'] if bias_prediction(r, k) is not None]
        if rows:
            worst, tot = 0.0, 0.0
            for k in rows:
                d = k['mean'] - bias_prediction(r, k)
                worst = max(worst, abs(d))
                tot += d * d
            # sampling error on one key's mean
            se = pool['sd'] / sqrt(pool['samples'] / pool['keys'])
            # For `tail` this is deliberately incomplete: the per-key mean there
            # carries the switching key's own offset as well as the rounding bias,
            # and which key draws which offset is not predictable -- only its
            # DISTRIBUTION is, which is the between-key line above. So a large rms
            # miss on `tail` is the keyswitch offset, not a failed bias prediction.
            label = ("per-key bias" if r['stage'] != 'tail'
                     else "rounding bias only (tail also carries the ksk offset)")
            print("        %-14s rms miss %.4f  worst %.4f   (per-key sampling error %.4f)"
                  % (label, sqrt(tot / len(rows)), worst, se))
    return bad


if __name__ == '__main__':
    ap = argparse.ArgumentParser(prog='dse_isolate')
    ap.add_argument('log')
    ap.add_argument('--stage', choices=('round1', 'round2', 'ksonly', 'tail'))
    a = ap.parse_args()
    recs = records(a.log)
    print("%d isolated measurements parsed from %s\n" % (len(recs), a.log))
    bad = report(recs, a.stage)
    print("\n%d row(s) outside 3x the sampling floor" % bad)
    sys.exit(0)
