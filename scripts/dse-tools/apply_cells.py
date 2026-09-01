#!/usr/bin/env python3
"""Paste a `dse.py costfit --emit` block into one _CELLS_* table of dse_model.py.

    apply_cells.py FIT.txt [--cells _CELLS_MULTI_CLANG] [--comment NOTE.txt]

FIT.txt is the output of `dse.py costfit <log> --emit` (the whole file is fine;
only its `GATE_COST = {...}` block is read). The named table's rows are replaced
in place, and the block that was there is printed first -- so the numbers a pin
move overwrote are in the terminal transcript, not only in git.

`--cells` defaults to the table of the regime named by --regime, or to
_CELLS_MULTI_CLANG. `--comment` inserts a file's text between the opening brace
and the first row, which is where each table records the pin, thread count,
runtime, box, date and residuals it was measured under. Write that comment: a
cell block with no provenance is a cell block nobody can retire.

Nothing else in dse_model.py is touched, so the regime's `pin` and `measured`
fields still have to be updated by hand -- see docs/cost-model.md.
"""
import argparse
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, os.pardir, "paramsestimator", "dse_model.py")

REGIME_TABLE = {
    "multi/libomp": "_CELLS_MULTI_CLANG",
    "multi/libgomp": "_CELLS_MULTI_GCC",
    "single/libomp": "_CELLS_SINGLE_CLANG",
    "single/libgomp": "_CELLS_SINGLE_GCC",
}


REGIME_KEYS = {k: '    ("%s", "%s"): dict(' % tuple(k.split("/"))
               for k in REGIME_TABLE}


def probe_pin():
    """The commit of the installed OpenFHE, as the build recorded it."""
    path = os.environ.get("OPENFHE_REF_FILE",
                          "/opt/openfhe/share/openfhe-src/OPENFHE_REF")
    try:
        with open(path) as f:
            return f.read().split()[0]
    except (OSError, IndexError):
        return None


def probe_box():
    """The machine, from lscpu. It knows this better than the person at it."""
    import subprocess
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    f = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            f[k.strip()] = v.strip()
    if "Model name" not in f:
        return None
    return "%s, %s socket(s) x %s core(s) x %s thread(s), %s NUMA node(s)" % (
        f["Model name"], f.get("Socket(s)", "?"), f.get("Core(s) per socket", "?"),
        f.get("Thread(s) per core", "?"), f.get("NUMA node(s)", "?"))


def fit_points(path):
    """{method: timings} from the `pts` column of a costfit table.

    The count is the fit's own, not a guess: a regime's `points` is then the sum
    of its per-method records and nobody adds up timings by hand.
    """
    pts = {}
    for line in open(path):
        w = line.split()
        if len(w) > 4 and w[0] in ("GINX", "LMKCDEY", "AP") and w[-1].endswith("%"):
            try:
                pts[w[0]] = pts.get(w[0], 0) + int(w[3])
            except ValueError:
                continue
    return pts


def patch_regime(src, regime, records):
    """Rewrite one COST_REGIMES entry's `by_method` rows, and its `pin`.

    `records` is {method: (pin, on, points, box)}. Methods absent from it keep
    their row VERBATIM -- symbols and all -- because those rows are provenance
    for cells this fit did not touch.
    """
    key = REGIME_KEYS[regime]
    if key not in src:
        raise SystemExit("no COST_REGIMES entry %s in the model" % regime)
    i = src.index(key)
    ends = [x for x in (src.find('\n    ("', i + len(key)), src.find("\n}\n", i))
            if x != -1]
    entry = src[i:min(ends)]
    if "by_method={" not in entry:
        raise SystemExit("%s has no by_method block to update" % regime)
    b0 = entry.index("by_method={")
    b1 = entry.index("},", b0) + 2
    rows = dict(re.findall(r'"(\w+)":\s*dict\(([^)]*)\)', entry[b0:b1]))
    for meth, (pin, on, points, box) in records.items():
        rows[meth] = 'pin="%s", on="%s", points=%d, box="%s"' % (pin, on, points, box)
    order = [m for m in ("AP", "GINX", "LMKCDEY") if m in rows]
    order += [m for m in sorted(rows) if m not in order]
    out = []
    for meth in order:
        line = '            %-10s dict(%s),' % ('"%s":' % meth, rows[meth])
        if len(line) > 92 and ", box=" in rows[meth]:
            # A probed box string is long; wrap rather than run to 190 columns.
            head, box = rows[meth].split(", box=", 1)
            line = ('            %-10s dict(%s,\n%sbox=%s),'
                    % ('"%s":' % meth, head, " " * 28, box))
        out.append(line + "\n")
    block = "by_method={\n" + "".join(out) + "        },"
    new_entry = entry[:b0] + block + entry[b1:]
    # The regime's own pin is what the cells DESCRIBE. A fit measured at a newer
    # commit moves it, and the methods not in this fit become carried to it --
    # which is a claim about them that only `carried_why` can justify, so this
    # says so rather than quietly rewriting the prose.
    pins = {r[0] for r in records.values()}
    moved = None
    if len(pins) == 1:
        pin = pins.pop()
        cut = new_entry.index("by_method={")
        head, tail = new_entry[:cut], new_entry[cut:]
        mm = re.search(r'pin=(\w+|"[0-9a-f]{40}")', head)
        if mm and _resolve_pin(src, mm.group(1)) != pin:
            head = head[:mm.start(1)] + '"%s"' % pin + head[mm.end(1):]
            moved = (_resolve_pin(src, mm.group(1)), pin)
        new_entry = head + tail
    return src[:i] + new_entry + src[min(ends):], moved


def _regime_entry(src, regime):
    """The source text of one COST_REGIMES entry."""
    key = REGIME_KEYS[regime]
    i = src.index(key)
    ends = [x for x in (src.find('\n    ("', i + len(key)), src.find("\n}\n", i))
            if x != -1]
    return src[i:min(ends)]


def REGIME_METHODS(src, regime):
    """The methods that entry holds records for."""
    return re.findall(r'"(\w+)":\s*dict\(pin=', _regime_entry(src, regime))


def _resolve_pin(src, token):
    """`PIN_9422` -> the commit it names, a quoted literal -> itself."""
    if token.startswith('"'):
        return token.strip('"')
    mm = re.search(r'^%s = "([0-9a-f]+)"' % re.escape(token), src, re.M)
    return mm.group(1) if mm else token


def _and(names):
    """'GINX', 'AP and GINX', 'AP, GINX and LMKCDEY' -- it goes into a source
    comment a person reads, not into a machine-parsed field."""
    names = list(names)
    if len(names) < 2:
        return "".join(names)
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def read_emitted(path):
    """[(method, N, word, coefficient text)] from a costfit --emit file."""
    text = open(path).read()
    block = re.search(r"GATE_COST = \{\n(.*?)\n\}", text, re.S)
    if not block:
        sys.exit("%s: no `GATE_COST = {...}` block. Did you pass --emit?" % path)
    rows = []
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        m = re.match(r"\s*\('(\w+)', (\d+), (\d+)\): \((.*)\),", line)
        if not m:
            sys.exit("%s: unparsed emit line: %r" % (path, line))
        method, N, word, coef = m.groups()
        rows.append((method, int(N), int(word), coef))
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fit", help="output of `dse.py costfit <log> --emit`")
    ap.add_argument("--cells", help="the _CELLS_* table to replace")
    ap.add_argument("--regime", choices=sorted(REGIME_TABLE),
                    help="name the table by regime instead of by variable name")
    ap.add_argument("--comment", help="file whose text becomes the table's leading comment")
    ap.add_argument("--model", default=MODEL, help="path to dse_model.py")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="print what would change and write nothing")
    ap.add_argument("--update-regime", action="store_true", dest="update_regime",
                    help="also write the regime's per-method provenance record in "
                         "COST_REGIMES: the pin, date, timing count and box of "
                         "every method in this fit. Requires --regime. Without "
                         "it, `points`, `measured`, `box` and `pins_by_method` go "
                         "on describing the previous measurement.")
    ap.add_argument("--pin", help="commit the fit was measured at "
                                  "(default: the installed OpenFHE's own record)")
    ap.add_argument("--box", help="the machine, one line "
                                  "(default: probed with lscpu)")
    ap.add_argument("--on", help="measurement date, ISO (default: today)")
    ap.add_argument("--merge", action="store_true",
                    help="keep cells the fit does not mention, instead of "
                         "replacing the whole table. This is what you want after "
                         "measuring a SUBSET of cells (gatecost-regime.sh takes "
                         "METHODS/NS/WORDS): without it, pasting six AP cells "
                         "would delete the twelve GINX and LMKCDEY cells the log "
                         "never covered.")
    a = ap.parse_args()

    name = a.cells or (REGIME_TABLE[a.regime] if a.regime else "_CELLS_MULTI_CLANG")
    rows = read_emitted(a.fit)
    comment = open(a.comment).read() if a.comment else ""
    if comment and not comment.endswith("\n"):
        comment += "\n"

    src = open(a.model).read()
    anchor = "\n%s = {\n" % name
    if a.merge:
        # Read the cells currently in that table and keep any the fit is silent
        # about. Order is (method, N, word) so a merged table reads the same way
        # a wholly re-measured one does.
        import re as _re
        cur = _re.search(_re.escape(name) + r" = \{\n(.*?)\n\}\n", src, _re.S)
        existing = {}
        if cur:
            for line in cur.group(1).splitlines():
                mm = _re.match(r'\s*\("(\w+)",\s*(\d+),\s*(\d+)\):\s*\((.*)\),', line)
                if mm:
                    me, N, w, coef = mm.groups()
                    existing[(me, int(N), int(w))] = coef
        fitted = {(me, N, w): coef for me, N, w, coef in rows}
        kept = {k: v for k, v in existing.items() if k not in fitted}
        rows = sorted([(k[0], k[1], k[2], v) for k, v in
                       list(kept.items()) + list(fitted.items())],
                      key=lambda r: (r[0], r[1], r[2]))
        print("--- merging: %d cell(s) from the fit, %d kept from the table ---"
              % (len(fitted), len(kept)))
        # A merge that keeps rows makes the leading comment describe only PART of
        # the table, and that comment is the provenance: box, pin, date. Recording
        # a GINX run's box above another machine's AP cells is exactly the claim
        # nobody could later retire, so the mismatch is written into the comment
        # rather than left to the person pasting.
        kept_methods = sorted({k[0] for k in kept})
        fit_methods = sorted({k[0] for k in fitted})
        outside = [me for me in kept_methods if me not in fit_methods]
        # A merge that KEEPS rows must keep their provenance too. The comment is
        # replaced wholesale by --comment, so extending a table -- same methods,
        # new (N, word) cells -- silently deletes the note describing every row
        # that stayed. Prepend what was there instead; a reader can then see both
        # measurements, which is the whole point of keeping the rows.
        if kept and comment:
            prev = _re.search(_re.escape(name) + r" = \{\n((?:    #.*\n)+)", src)
            if prev and prev.group(1).strip() and prev.group(1) not in comment:
                comment = prev.group(1) + "    #\n" + comment
                print("--- kept the previous comment above the new one "
                      "(%d lines) ---" % prev.group(1).count("\n"))
        if outside:
            print("--- kept cells for %s, which this fit does not cover ---"
                  % ", ".join(outside))
            if comment:
                comment += ("    # The comment above describes the %s rows. The %s rows below\n"
                            "    # were measured separately, on whatever box and pin the comment\n"
                            "    # history of this table names.\n"
                            % (_and(fit_methods), _and(outside)))
    if anchor not in src:
        sys.exit("%s: no table called %s (have: %s)"
                 % (a.model, name, ", ".join(sorted(set(REGIME_TABLE.values())))))
    start = src.index(anchor) + 1
    end = src.index("\n}\n", start) + 3

    print("--- replaced in %s ---" % name)
    print(src[start:end], end="")

    body = "\n".join('    (%-11s%4d, %s): (%s),' % ('"%s",' % m, N, w, c)
                     for m, N, w, c in rows)
    new = "%s = {\n%s%s\n}\n" % (name, comment, body)
    if a.dry_run:
        print("--- would write %d rows ---" % len(rows))
        print(new, end="")
        return
    src = src[:start] + new + src[end:]
    print("--- wrote %d rows into %s ---" % (len(rows), name))

    if not a.update_regime:
        open(a.model, "w").write(src)
        print("Now update that regime's per-method record in COST_REGIMES "
              "(--update-regime does it), then run tests/test_dse.py and "
              "dse.py doctor.")
        return

    if not a.regime:
        sys.exit("--update-regime needs --regime, to know which entry to write")
    pin = a.pin or probe_pin()
    if not pin:
        sys.exit("--update-regime: no --pin given and no installed OPENFHE_REF to "
                 "read. Pass --pin <commit>.")
    box = a.box or probe_box()
    if not box:
        sys.exit("--update-regime: no --box given and lscpu could not answer. "
                 "Pass --box '<model>, <sockets> x <cores> ...'.")
    on = a.on or datetime.date.today().isoformat()
    pts = fit_points(a.fit)
    fitted_methods = sorted({m for m, _, _, _ in read_emitted(a.fit)})
    missing = [m for m in fitted_methods if m not in pts]
    if missing:
        sys.exit("--update-regime: %s has no `pts` column for %s, so the timing "
                 "count would be invented. Pass the whole costfit output, not "
                 "just its GATE_COST block." % (a.fit, ", ".join(missing)))
    records = {m: (pin, on, pts[m], box) for m in fitted_methods}
    src, moved = patch_regime(src, a.regime, records)
    open(a.model, "w").write(src)
    print("--- regime %s: wrote provenance for %s ---"
          % (a.regime, _and(fitted_methods)))
    for m in fitted_methods:
        print("      %-8s %s  %s  %d timings" % (m, pin[:8], on, pts[m]))
    print("      box: %s" % box)
    if moved:
        carried = [me for me in sorted(set(REGIME_METHODS(src, a.regime)) - set(fitted_methods))]
        print("--- the regime's pin moved %s -> %s ---" % (moved[0][:8], moved[1][:8]))
        if carried:
            one = len(carried) == 1
            print("    %s %s now CARRIED to it: %s cells were measured at the older"
                  % (_and(carried), "is" if one else "are", "its" if one else "their"))
            print("    pin, and this entry now claims they describe the newer one.")
        if "carried_why=" in _regime_entry(src, a.regime):
            print("    Check `carried_why` (and `stale`) still cover that span.")
        else:
            print("    This entry has NO carried_why, so the claim stands unjustified:")
            print("    add one saying what the span between those pins does and what")
            print("    was measured across it. Only a person can say that.")
    print("Now: sage -python tests/test_dse.py && dse.py doctor")


if __name__ == "__main__":
    main()
