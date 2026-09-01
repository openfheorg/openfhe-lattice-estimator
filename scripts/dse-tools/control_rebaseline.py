#!/usr/bin/env python3
"""Re-baseline the known-answer control from a control-rebaseline.cmds log.

    control_rebaseline.py LOG [--pin SHA] [--on YYYY-MM-DD] [--write] [--z 3.0] [--tol 6.0]

`dse_measured.CONTROL` stores, for four sets the library ships, the parameters a
measurement was taken on and the sigma it measured. When the library changes one
of those rows, `doctor` prints LIVE TABLE DIFFERS for it and the entry has to be
re-measured on the row the library ships now -- never re-pointed at the new row
with the old sigma, which would turn a known answer into a guess. This tool does
the arithmetic and the edit for that re-measurement, so the entry that lands in
the source is the one the log supports and nothing is transcribed by hand.

INPUT. The `record|` rows of `scripts/run-plan.sh` on
`scripts/dse-arms/plans/control-rebaseline.cmds`: labels `ctl_<SET>_off` and
`ctl_<SET>_on`, the 64-bit and 32-bit key forms of the same named set. A `-p`
row carries no geometry of its own (its fields read `-`), so each set's
parameters come from the LIVE table (`dse_shipfit.shipped_params`), which is the
same source the control's parser half checks against.

THREE CHECKS, each of which refuses the write:
  - every wrong answer is fatal: a row with FAILURES > 0 measured nothing;
  - the two arms of a set must agree, |z| below `--z` with per-key scatter
    included (`gate_compare.z_between`): they are the same key material in two
    widths and the library reports them bit-identical, so a disagreement is a
    broken path, not a baseline;
  - the model must reproduce the pooled sigma within `--tol` percent
    (`dse_shipfit.predict`), because a control the model already misses is not a
    control.

OUTPUT. The pooled sigma is the root mean square of the two arms weighted by
their sample counts, `samples` their sum. With `--write` the CONTROL block,
CONTROL_PIN, CONTROL_MEASURED and the header line naming the measurement are
rewritten in `dse_measured.py` (or `--file`), the module is reloaded and
`dse_shipfit.control()` is run on it: the exit status is that control's. Every
set in CONTROL must be present in the log, since the block carries one pin and
one date for all of its rows.
"""
import argparse
import datetime
import importlib
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_DIR = os.path.normpath(os.path.join(HERE, os.pardir, 'paramsestimator'))
sys.path.insert(0, PARAMS_DIR)
sys.path.insert(0, HERE)

KV = re.compile(r'(\w+)=(\S+)')
LABEL = re.compile(r'^ctl_(.+)_(off|on)$')
METHOD = {'1': 'AP', '2': 'GINX', '3': 'LMKCDEY'}
PARAM_KEYS = ('N', 'n', 'q', 'logQ', 'qks', 'bks', 'brk', 'autokeys', 'keydist', 'sigma', 'gmap')


def records(path):
    """{set: {'off': rec, 'on': rec}} from the log, plus the labels that fit no set."""
    out, stray = {}, []
    for line in open(path):
        if 'record|' not in line:
            continue
        d = dict(KV.findall(line))
        if d.get('sigma') in (None, 'NA'):
            continue
        mm = LABEL.match(d.get('label', ''))
        if not mm:
            stray.append(d.get('label', '?'))
            continue
        out.setdefault(mm.group(1), {})[mm.group(2)] = d
    return out, stray


def installed_pin():
    path = os.environ.get('OPENFHE_REF_FILE', '/opt/openfhe/share/openfhe-src/OPENFHE_REF')
    try:
        with open(path) as f:
            return f.readline().strip()
    except OSError:
        return None


def pooled_sigma(arms):
    """RMS over the arms weighted by samples -> (sigma, total samples)."""
    num = sum(float(a['sigma']) ** 2 * int(a['samples']) for a in arms)
    tot = sum(int(a['samples']) for a in arms)
    return math.sqrt(num / tot), tot


def fmt_params(p):
    gm = ", ".join("%d: %d" % (int(k), int(v)) for k, v in sorted(p['gmap'].items(), key=lambda kv: int(kv[0])))
    return ("dict(N=%d, n=%d, q=%d, logQ=%d, qks=%d, bks=%d, brk=%d, autokeys=%d,\n"
            "                    keydist='%s', sigma=%s, gmap={%s})"
            % (p['N'], p['n'], p['q'], p['logQ'], p['qks'], p['bks'], p['brk'], p['autokeys'],
               p['keydist'], p['sigma'], gm))


def entry(name, method, sigma, samples, p):
    return ('    "%s": dict(method="%s", sigma=%.4f, samples=%d,\n        params=%s),\n'
            % (name, method, sigma, samples, fmt_params(p)))


def rewrite(src, entries, order, pin8, on, zmax):
    """The CONTROL block, its pin, its date and its header line, replaced in `src`."""
    start = src.index("CONTROL = {\n")
    end = src.index("\n}\n", start) + len("\n}\n")
    block = "CONTROL = {\n" + "".join(entries[n] for n in order) + "}\n"
    src = src[:start] + block + src[end:]
    src = re.sub(r'^CONTROL_PIN = "[0-9a-f]+"', 'CONTROL_PIN = "%s"' % pin8, src, count=1, flags=re.M)
    src = re.sub(r'^CONTROL_MEASURED = "[0-9-]+"', 'CONTROL_MEASURED = "%s"' % on, src, count=1, flags=re.M)
    src = re.sub(r'^# Measured at [0-9a-f]+, .*?: -p <SET>, -i 2400,$',
                 '# Measured at %s, %s, plans/control-rebaseline.cmds: -p <SET>, -i 2400,' % (pin8, on),
                 src, count=1, flags=re.M)
    src = re.sub(r'\|z\| <= [0-9.]+ including per-key scatter',
                 '|z| <= %.1f including per-key scatter' % zmax, src, count=1)
    return src


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log')
    ap.add_argument('--pin', help='the OpenFHE commit measured (default: the installed OPENFHE_REF)')
    ap.add_argument('--on', help='measurement date (default: today)')
    ap.add_argument('--z', type=float, default=3.0, help='|z| at which the two arms disagree (default 3)')
    ap.add_argument('--tol', type=float, default=6.0, help='model error, percent, the control allows (default 6)')
    ap.add_argument('--file', help='the dse_measured.py to rewrite (default: the one beside dse_model.py)')
    ap.add_argument('--write', action='store_true', help='rewrite the file; without it, print what would change')
    a = ap.parse_args(argv)

    import dse_model as m
    import dse_measured as dm
    import dse_shipfit as sf
    import gate_compare as gc

    pin = a.pin or installed_pin()
    if not pin:
        sys.exit("no --pin and no installed OPENFHE_REF to read; say which commit this log measured")
    pin8 = pin[:8]
    on = a.on or datetime.date.today().isoformat()

    recs, stray = records(a.log)
    if stray:
        print("ignoring %d row(s) that are not ctl_<SET>_off/_on: %s" % (len(stray), sorted(set(stray))))
    live = sf.shipped_params()
    want = list(dm.CONTROL)
    missing = [s for s in want if s not in recs]
    incomplete = [s for s in recs if set(recs[s]) != {'off', 'on'}]
    if missing or incomplete:
        print("control sets missing from the log: %s; incomplete (one arm): %s"
              % (missing or 'none', incomplete or 'none'))
        print("The CONTROL block carries one pin and one date, so every set is re-measured together.")
        return 1

    entries, bad, zmax = {}, [], 0.0
    print("%-16s %-8s %9s %9s %7s %6s   %9s %8s   %s"
          % ("set", "method", "off", "on", "ratio", "z", "pooled", "model", "live row"))
    for s in want:
        off, on_ = recs[s]['off'], recs[s]['on']
        fails = int(off.get('FAILURES', 0) or 0) + int(on_.get('FAILURES', 0) or 0)
        method = METHOD.get(off.get('method'), '?')
        if s not in live:
            print("%-16s not in the live table" % s); bad.append(s); continue
        p = {k: live[s][k] for k in PARAM_KEYS}
        r, z, _sd = gc.z_between(off, on_, m.KEY_SCATTER_BOUND)
        zmax = max(zmax, abs(z))
        sigma, samples = pooled_sigma([off, on_])
        e = 100.0 * (sf.predict(p, method) - sigma) / sigma
        flags = []
        if fails:
            flags.append("WRONG ANSWERS %d" % fails)
        if abs(z) >= a.z:
            flags.append("ARMS DISAGREE")
        if abs(e) > a.tol:
            flags.append("MODEL OFF")
        if flags:
            bad.append(s)
        gm = ",".join("%d:%d" % kv for kv in sorted(p['gmap'].items()))
        print("%-16s %-8s %9.4f %9.4f %7.4f %+6.1f   %9.4f %+7.2f%%   n=%d %s%s"
              % (s, method, float(off['sigma']), float(on_['sigma']), r, z, sigma, e,
                 p['n'], gm, ("   <-- " + "; ".join(flags)) if flags else ""))
        entries[s] = entry(s, method, sigma, samples, p)

    if bad:
        print("\nnot written: %s. A control that fails its own checks is not a baseline." % bad)
        return 1

    path = a.file or os.path.join(PARAMS_DIR, 'dse_measured.py')
    src = open(path).read()
    new = rewrite(src, entries, want, pin8, on, zmax)
    if not a.write:
        print("\nwould write %s (CONTROL_PIN %s, CONTROL_MEASURED %s); pass --write" % (path, pin8, on))
        for s in want:
            print(entries[s], end="")
        return 0
    open(path, 'w').write(new)
    print("\nwrote %s: %d entries, CONTROL_PIN %s, CONTROL_MEASURED %s" % (path, len(entries), pin8, on))
    if os.path.abspath(path) == os.path.abspath(dm.__file__):
        importlib.reload(dm)
        ok, lines = sf.control(live=live)
        print("\n".join(lines))
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
