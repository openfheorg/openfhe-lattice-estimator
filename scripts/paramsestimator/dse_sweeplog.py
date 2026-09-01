#!/usr/bin/python

'''Parse a sweep log into measured records, for calibrating and checking the DSE model.

A sweep prints, for every candidate it measures, a `speed:` line carrying the full
parameter vector and the word size it ran on, a `noise:` line carrying the key
count and samples per key, and an `(actual noise, mean, EvalBinGate us)` line
carrying the result. This turns those triples back into records.

Deliberately free of any OpenFHE or SageMath dependency, so the model it feeds
can be developed and checked without a container.

    > python3 scripts/paramsestimator/dse_sweeplog.py overnight-final.log
'''

from math import log2

import re
import sys

# `speed:`/`noise:` print the command they ran, so the parameters are recoverable
# verbatim rather than reconstructed from the search's own bookkeeping.
_FLAG_RE  = re.compile(r'-([a-zA-Z]) +([0-9.]+)')
# Two trailing forms: the two-build era printed "(32-bit words)" / "(64-bit
# words)"; since the genuine 32-bit build was retired the harness prints
# "(32-bit key forms where they fit)" and the word size follows from logQ.
_SPEED_RE = re.compile(r'^speed: .*?boolean_noise_estimate_script (.*?) \((?:(32|64)-bit words|32-bit key forms where they fit)\)')
_NOISE_RE = re.compile(r'^noise: (\d+) keys,.*?boolean_noise_estimate_script (.*?) \((?:(?:32|64)-bit words|32-bit key forms where they fit)\)')
_RESULT_RE = re.compile(r'^\(actual noise, mean, EvalBinGate us\) \(([-0-9.e+]+), ([-0-9.e+]+), ([-0-9.e+]+)\)')

# flag -> field name, as the binary defines them
_FIELDS = {'n': 'n', 'q': 'q', 'N': 'N', 'Q': 'logQ', 'k': 'q_ks', 'g': 'base_g',
           'b': 'base_ks', 'r': 'base_rk', 's': 'sigma', 'd': 'secret_dist',
           't': 'method', 'I': 'inputs', 'i': 'samples', 'K': 'keys'}

_INT_FIELDS = ('n', 'q', 'N', 'logQ', 'q_ks', 'base_g', 'base_ks', 'base_rk',
               'secret_dist', 'method', 'inputs', 'samples', 'keys')


def _parse_flags(argstr):
    rec = {}
    for flag, value in _FLAG_RE.findall(argstr):
        name = _FIELDS.get(flag)
        if name is None:
            continue
        rec[name] = float(value) if name == 'sigma' else int(value)
    return rec


def parse(path):
    """Yield one dict per measured candidate.

    A `speed:` line with no result before the next one was pruned on speed (-x)
    and is skipped: it has a gate time but never had its noise measured.
    """
    pending = None
    with open(path) as f:
        for line in f:
            line = line.rstrip('\n')

            m = _SPEED_RE.match(line)
            if m:
                pending = _parse_flags(m.group(1))
                # the retired two-build suffix named the word; the current one
                # does not, and the accumulator word follows from the modulus
                pending['word_size'] = (int(m.group(2)) if m.group(2)
                                        else (32 if pending.get('logQ', 64) <= 28 else 64))
                # the probe's own -i/-K describe the probe, not the noise run
                pending.pop('samples', None)
                pending.pop('keys', None)
                continue

            m = _NOISE_RE.match(line)
            if m and (pending is not None):
                pending['keys'] = int(m.group(1))
                pending['samples'] = _parse_flags(m.group(2)).get('samples')
                continue

            m = _RESULT_RE.match(line)
            if m and (pending is not None):
                rec = dict(pending)
                rec['sigma_measured'] = float(m.group(1))
                rec['mean_measured']  = float(m.group(2))
                rec['gate_us']        = float(m.group(3))
                pending = None
                yield rec


# `record|` is found ANYWHERE in the line, not anchored at the start: compose
# writes container-management chatter to the same stream the runner writes
# records to, and one interleaved "Error response from daemon: Conflict..."
# prefixed a record and silently cost a measured cell. The record itself was
# intact; only the anchor rejected it.
_RUNNER_RE = re.compile(r'record\|\s+(.*)$')
_KV_RE     = re.compile(r'(\w+)=(\S+)')


def runner_records(path):
    """Parse the `record|` lines the designed-measurement runner emits.

    A second format because the runner drives configurations the sweep cannot
    express -- a per-dimension gadget map, above all -- and because it computes
    sigma itself rather than going through the Python harness. Fields absent from
    a given run are written "-" and come back as None, so a named-set run and a
    hand-built one parse with the same code.
    """
    out = []
    with open(path) as f:
        for line in f:
            m = _RUNNER_RE.search(line)
            if not m:
                continue
            raw = dict(_KV_RE.findall(m.group(1)))
            if raw.get('sigma') in (None, 'NA'):
                continue
            r = {}
            for k, v in raw.items():
                if v == '-':
                    r[k] = None
                elif k == 'gmap':
                    r[k] = {int(b): int(c) for b, c in
                            (part.split(':') for part in v.split(','))}
                elif k in ('sigma', 'mean'):
                    r[k] = float(v)
                elif k in ('set', 'label', 'keydist'):
                    r[k] = v
                else:
                    r[k] = int(v)
            r['sigma_measured'] = r.pop('sigma')
            # A single-base run passes -g, not -G, so gmap is absent. Rebuild it
            # from baseg rather than leaving consumers to guess: every downstream
            # model call needs a map, and a record that carries the base but no
            # map would otherwise be silently unusable.
            if r.get('gmap') is None and r.get('baseg') and r.get('n'):
                r['gmap'] = {int(r['baseg']): int(r['n'])}
            out.append(r)
    return out


def records(path):
    return list(parse(path))


def _summarize(recs):
    missing = [k for k in ('n', 'q', 'N', 'logQ', 'q_ks', 'base_g', 'base_ks')
               if any(k not in r for r in recs)]
    print("records parsed: %d" % len(recs))
    print("incomplete fields: %s" % (', '.join(missing) if missing else "none"))
    if not recs:
        return
    print("keys per record: %s   samples per key: %s"
          % (sorted({r.get('keys') for r in recs}), sorted({r.get('samples') for r in recs})))
    print("word sizes: %s   methods: %s   inputs: %s   secret_dist: %s"
          % (sorted({r['word_size'] for r in recs}), sorted({r['method'] for r in recs}),
             sorted({r['inputs'] for r in recs}), sorted({r['secret_dist'] for r in recs})))
    print("distinct (N, logQ): %s" % sorted({(r['N'], r['logQ']) for r in recs}))
    print("n range: %d-%d    sigma range: %.2f-%.2f    gate ms: %.1f-%.1f"
          % (min(r['n'] for r in recs), max(r['n'] for r in recs),
             min(r['sigma_measured'] for r in recs), max(r['sigma_measured'] for r in recs),
             min(r['gate_us'] for r in recs)/1000.0, max(r['gate_us'] for r in recs)/1000.0))
    print("log2(q_ks) values: %s"
          % sorted({int(log2(r['q_ks'])) for r in recs if r['q_ks'] > 0}))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    _summarize(records(sys.argv[1]))
