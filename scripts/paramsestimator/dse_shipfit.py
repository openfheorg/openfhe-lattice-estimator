"""Shipped-set noise: measured against the model, with a known-answer control.

The control is not optional decoration. Reading binfhecontext's `cyclOrder`
column as N doubled every ring dimension and produced a uniform +30% error that
looked like a physical model failure at large N. A known-answer check -- sets
whose parameters AND measured sigma are recorded together (dse_measured.CONTROL)
-- catches that in one minute, in both halves: the model must predict the
measurement, and the parser must read the live table's row as those same
parameters. So the control runs FIRST and its result gates the rest.
"""
import os
import re
import sys

import dse_model as m
import dse_measured as dm

# Read straight from the library rather than transcribing 45 rows into Python.
# BINFHECONTEXT_SRC names the file. Unset, the image's own pinned copy at
# /opt/openfhe/share/openfhe-src/binfhecontext.cpp is used inside the container,
# and outside it a sibling clone of openfhe-development is searched for.
# `dse.py doctor` reports which file it found, or says none was.
# Candidates, in the order a machine is likely to have them. The default used to
# be one hardcoded path under ~/repos, which is one person's layout: on any other
# machine the fallback silently missed and every shipped-set tool reported the
# table absent. The image is checked first because inside the container it is the
# right answer and it is pinned; a working clone is whatever branch it is on.
_SRC_CANDIDATES = (
    '/opt/openfhe/share/openfhe-src/binfhecontext.cpp',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 os.pardir, os.pardir, os.pardir,
                 'openfhe-development', 'src', 'binfhe', 'lib', 'binfhecontext.cpp'),
    os.path.expanduser('~/repos/openfhe-development/src/binfhe/lib/binfhecontext.cpp'),
    os.path.expanduser('~/openfhe-development/src/binfhe/lib/binfhecontext.cpp'),
)


def _default_src():
    for c in _SRC_CANDIDATES:
        if os.path.exists(c):
            return os.path.normpath(c)
    return os.path.normpath(_SRC_CANDIDATES[0])


SRC = os.environ.get('BINFHECONTEXT_SRC') or _default_src()

# The table the library shipped BEFORE this tool's re-selected sets replaced it
# (238153db, 45 rows, byte-identical to that commit's file). Not used by the
# known-answer control -- that carries its own parameters (dse_measured.CONTROL)
# -- only as the reference for the RELATIVE key cap, `--key-cap-mult`, whose
# meaning is "a multiple of what the library shipped before": a historical
# reference by definition, which is why it is frozen here rather than read from
# whichever clone or image is to hand.
HERE = os.path.dirname(os.path.abspath(__file__))
PRE_RESELECT_SRC = os.path.join(HERE, 'fixtures', 'binfhecontext-238153db.cpp')
PRE_RESELECT_SETS = 45
ROW = re.compile(
    r'\{\s*(\w+)\s*,\s*\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\w+)\s*,'
    r'\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\w+)\s*,\s*([\d.]+)\s*,'
    r'\s*\{(\{.*?\})\}\s*\}\s*\}')

# How many parameter sets the pinned table holds. A parse that comes back far
# short of this is reading a DIFFERENT revision of the file, not a smaller table:
# the row format changed with the per-dimension gadget map, and a sibling clone
# that has moved to another branch parses to zero rows. The image carries its own
# copy at the pinned commit for exactly this reason; a host-side caller pointing
# BINFHECONTEXT_SRC at a working clone is trusting whatever that clone is on.
EXPECTED_SETS = 40


def shipped_params(strict=True, src=None):
    """{name: params} from a binfhecontext.cpp. `src` defaults to SRC (the current
    library table); pass PRE_RESELECT_SRC for the table the library shipped before f3944448."""
    src = src or SRC
    out = {}
    for g in ROW.finditer(open(src).read()):
        gm = {int(a): int(b) for a, b in
              re.findall(r'\{\s*(\d+)\s*,\s*(\d+)\s*\}', g.group(13))}
        logQ = int(g.group(2))
        # cyclOrder >> 1. NOT the column value. See the docstring.
        N = int(g.group(3)) >> 1
        modks = g.group(6)
        out[g.group(1)] = dict(
            logQ=logQ, N=N, cyc=int(g.group(3)), n=int(g.group(4)), q=int(g.group(5)),
            # modKS == PRIME (0) means q_KS is the intermediate prime Q itself.
            qks=(1 << logQ) if modks == 'PRIME' else int(modks),
            bks=int(g.group(7)), brk=int(g.group(9)), autokeys=int(g.group(10)),
            keydist=g.group(11), sigma=float(g.group(12)), gmap=gm)
    if strict and len(out) < EXPECTED_SETS:
        raise ValueError(
            "parsed only %d parameter sets from %s, expected at least %d. That "
            "file is almost certainly a different revision of OpenFHE -- the row "
            "format changed with the per-dimension gadget base map. Point "
            "BINFHECONTEXT_SRC at the pinned copy (the image carries one at "
            "/opt/openfhe/share/openfhe-src/binfhecontext.cpp) rather than at a "
            "clone that may have moved." % (len(out), src, EXPECTED_SETS))
    return out

def designed_arity(name):
    """Gate arity a shipped set exists for, from its name: _3 -> 3, _4 -> 4, else 2.

    This matters because `log2_pf` uses p = 2*inputs, so the decision window is
    q/4, q/6 or q/8 -- and evaluating a `_3` or `_4` set at 2 inputs overstates its
    margin by 150 to 800 bits. Every measurement in this work was taken at -I 2,
    and for a long time its log2Pf was reported at 2 inputs as well, which made
    STD256Q_4 look like -428 when its operating point is -56.

    The MEASURED sigma transfers, so nothing needs re-measuring -- verified rather
    than assumed. Three sets measured at -I 2 and at their designed arity, against
    a run-to-run spread of 1.0% established by three identical repeats:

        STD128_3   +2.39%  (z = +1.2)
        STD128_4   +1.03%  (z = +0.5)
        STD256Q_3  +0.98%  (z = +0.5)     mean z = +0.72, 1.2 sigma

    All inside sampling. The refreshed ciphertext carries bootstrapping noise
    regardless of how many inputs were summed beforehand, and inputs arriving at
    (q, n) trigger no entry key switch -- the k-key-switch path needs
    extended=true, which nothing here passes.

    Note the harness is RANDOM, not seeded (the three repeats above differ), so any
    difference must be read against sampling. An earlier arity pair agreed to five
    significant figures, which looked like proof that -I 3 changed nothing; it was a
    coincidence and did not reproduce.
    """
    if name.endswith('_3') or '_3_' in name:
        return 3
    if name.endswith('_4') or '_4_' in name:
        return 4
    return 2


def predict(p, method):
    # AP prices its accumulator from the refresh base, so that column has to come
    # out of the table too -- it is field 8, and was parsed but discarded while AP
    # was unmodelled.
    return m.sigma_total_at_q(p['N'], p['n'], p['q'], p['logQ'], p['qks'], p['bks'],
                              p['sigma'], p['gmap'], key_dist=p['keydist'],
                              method=method, autokeys=p['autokeys'],
                              base_r=p['brk'] if method == 'AP' else None)

def method_of(name, code=None):
    if code is not None:
        return {'2': 'GINX', '3': 'LMKCDEY', '1': 'AP'}.get(str(code), 'GINX')
    return 'LMKCDEY' if 'LMKCDEY' in name else ('AP' if '_AP' in name else 'GINX')

def _same_params(a, b):
    return all(a[k] == b[k] for k in ('N', 'n', 'q', 'logQ', 'qks', 'bks', 'gmap', 'autokeys', 'keydist'))


def control(live=None, tol=6.0):
    """The known-answer control. -> (ok, report lines)

    Two halves. dse_measured.CONTROL carries parameters AND measured sigma for
    sets the library ships now, so each entry checks the model (predict within
    `tol` percent) and, where `live` -- the parsed live table -- has the name,
    the parser (same parameters as inline). CONTROL_HISTORICAL checks the model
    alone on the geometry of 238153db, which the library no longer ships. Either
    half failing means the pipeline's numbers are not trustworthy.
    """
    lines, bad = [], 0
    lines.append("CONTROL -- known answers with parameters inline, measured %s at %s:"
                 % (dm.CONTROL_MEASURED, dm.CONTROL_PIN))
    for name, c in sorted(dm.CONTROL.items()):
        p = c['params']
        e = 100 * (predict(p, c['method']) - c['sigma']) / c['sigma']
        flags = []
        if abs(e) > tol:
            flags.append("OUT OF BAND"); bad += 1
        if live is not None:
            lp = live.get(name)
            if lp is None:
                flags.append("not in the live table: parser half skipped")
            elif not _same_params(lp, p):
                diff = [k for k in ('N', 'n', 'q', 'logQ', 'qks', 'bks', 'gmap', 'autokeys', 'keydist')
                        if lp[k] != p[k]]
                flags.append("LIVE TABLE DIFFERS on %s -- parser bug, or the row changed again; re-baseline"
                             % ",".join(diff)); bad += 1
            else:
                flags.append("parser ok")
        lines.append("   %-16s %-8s N=%-5d %7.3f meas  %+6.2f%%   %s"
                     % (name, c['method'], p['N'], c['sigma'], e, "; ".join(flags)))
    lines.append("   historical (geometry of %s inline, model only):" % dm.CONTROL_HISTORICAL_PIN)
    for name, c in sorted(dm.CONTROL_HISTORICAL.items()):
        p = c['params']
        e = 100 * (predict(p, c['method']) - c['sigma']) / c['sigma']
        flag = "" if abs(e) <= tol else "   <-- OUT OF BAND"
        if abs(e) > tol:
            bad += 1
        lines.append("   %-16s %-8s N=%-5d %7.3f meas  %+6.2f%%%s" % (name, c['method'], p['N'], c['sigma'], e, flag))
    if bad:
        lines.append("\n   CONTROL FAILED on %d row(s). The parser or the model is wrong; nothing below is trustworthy." % bad)
    else:
        lines.append("   control passes (%d live sets: parser + model; %d historical: model; all within %.0f%%)\n"
                     % (len(dm.CONTROL), len(dm.CONTROL_HISTORICAL), tol))
    return bad == 0, lines


def main(log):
    P = shipped_params()
    ok, lines = control(live=P)
    print("\n".join(lines))
    if not ok:
        return 1

    print("MEASURED shipped sets, this run:")
    print("  %-24s %-7s %-6s %-6s %-9s %-9s %-8s %s"
          % ("set", "method", "N", "n", "measured", "predicted", "err", "fails"))
    errs = []
    for line in open(log):
        if 'record|' not in line:
            continue
        d = dict(re.findall(r'(\w+)=([^\s]+)', line))
        name = d.get('set', '-')
        if name == '-':
            continue
        p = P.get(name)
        if not p:
            print("  %-24s not in binfhecontext" % name); continue
        meth = method_of(name, d.get('method'))
        meas = float(d['sigma'])
        pred = predict(p, meth)
        e = 100 * (pred - meas) / meas
        errs.append((name, e))
        print("  %-24s %-7s %-6d %-6d %-9.3f %-9.3f %+-8.2f%% %s"
              % (name, meth, p['N'], p['n'], meas, pred, e, d.get('FAILURES', '-')))
    if errs:
        a = sorted(abs(e) for _, e in errs)
        w = max(errs, key=lambda t: abs(t[1]))
        print("\n  %d modelled sets: median |err| %.2f%%, worst %.2f%% (%s)"
              % (len(errs), a[len(a) // 2], abs(w[1]), w[0]))

    else:
        # A plan log has no named sets, so the header above would otherwise sit
        # over nothing and read as a failure. Say which kind of log this is.
        print("  (none -- no rows in this log name a shipped set, so there is"
              " nothing here to score.\n   Rows from a `-p NAME` run land here;"
              " rows from a `dse.py plan` measurement\n   carry geometry instead,"
              " and `dse.py validate` scores those separately.)")
    return 0

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("usage: dse_shipfit.py <runner-log>   "
                 "(records as emitted by scripts/run-plan.sh)")
    if not os.path.exists(SRC):
        sys.exit("cannot read %s -- set BINFHECONTEXT_SRC" % SRC)
    sys.exit(main(sys.argv[1]))
