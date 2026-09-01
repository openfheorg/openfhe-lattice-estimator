#!/usr/bin/env python3
"""Did gate time move across a pin, on this machine?

    gatetime_ab.py AB.log [--tol 3.0]

Reads `gatetime-ab.sh` records -- the same named sets timed at two pins, several
repetitions each -- and prints the per-set ratio on the MIN of each run, which is
the statistic a comparison wants: the first gate of a run is cold (measured 30252
us against ~16350 for the other seven on STD128) so the mean carries a tail the
floor does not.

It reports the key sizes alongside, because they are the independent check that
the pin move landed at all: a layout commit that shrinks the switching key by a
quarter shows up there exactly, and a set whose q_KS is an exact power of its base
shows up unchanged. If the ratios move but the key sizes do not, the two images
are the same build and the script is comparing noise to itself.

Exits nonzero if any set moved by more than `--tol` percent, which is a prompt to
re-measure the cost cells rather than a verdict on its own: one min per pin per
set is a single draw, and `REPS` in the arm is there to be raised.
"""
import argparse
import collections
import re
import sys

KV = re.compile(r'(\w+)=(\S+)')


def records(path):
    """(records, header): the `gatetime|` rows and the arm's `gatetime-ab|` header."""
    out, header = [], {}
    for line in open(path):
        if 'gatetime-ab|' in line:
            d = dict(KV.findall(line))
            if 'new' in d and 'old' in d:
                header = d
            continue
        if 'gatetime|' not in line:
            continue
        out.append(dict(KV.findall(line)))
    return out, header


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log')
    ap.add_argument('--tol', type=float, default=3.0,
                    help='percent of gate time that counts as a move (default 3)')
    ap.add_argument('--new', help='the newer pin (prefix), when the log has no gatetime-ab| header')
    a = ap.parse_args(argv)

    recs, header = records(a.log)
    if not recs:
        sys.exit("%s holds no `gatetime|` records" % a.log)
    pins = sorted({r['pin'] for r in recs})
    if len(pins) != 2:
        sys.exit("expected two pins in the log, found %s" % (pins,))
    # Which pin is the newer one comes from the arm's header (or --new), not from
    # sorting: shas do not sort by age, and 12858277 sorts before 94229558.
    newer = (a.new or header.get('new') or '')[:8]
    if newer and newer in pins:
        pins = [p for p in pins if p != newer] + [newer]
    elif newer:
        sys.exit("--new %s names neither pin in the log (%s)" % (newer, pins))
    else:
        print("WARNING: no gatetime-ab| header and no --new; taking the lexically larger sha "
              "as the newer pin, which is a guess. Pass --new.")
    if header:
        print("arm: reps=%s geometry=%s order=%s" % (header.get('reps', '?'),
              header.get('geometry', 'named'), header.get('order', 'pin-major')))
    best = collections.defaultdict(dict)      # set -> pin -> (min gate, key bytes)
    for r in recs:
        s, p = r['set'], r['pin']
        v = int(r['gate_us_min'])
        cur = best[s].get(p)
        if cur is None or v < cur[0]:
            best[s][p] = (v, int(r['ksk_b']) if r['ksk_b'] != 'NA' else 0)

    new, old = pins[1], pins[0]
    print("gate time, min over %d repetition(s), %s -> %s" % (
        len(recs) // max(len(best) * 2, 1), old, new))
    print("  %-18s %10s %10s %8s   %13s %13s %8s"
          % ("set", "old us", "new us", "ratio", "old ksk_b", "new ksk_b", "ratio"))
    moved, layout_moved = [], False
    for s in sorted(best):
        if len(best[s]) != 2:
            print("  %-18s only measured at %s" % (s, sorted(best[s])))
            continue
        (go, ko), (gn, kn) = best[s][old], best[s][new]
        r = gn / go
        kr = (kn / ko) if ko else float('nan')
        if ko and abs(kr - 1.0) > 0.001:
            layout_moved = True
        if abs(r - 1.0) * 100 > a.tol:
            moved.append((s, r))
        print("  %-18s %10d %10d %8.4f   %13d %13d %8.4f"
              % (s, go, gn, r, ko, kn, kr))
    print()
    if not layout_moved and header.get('geometry') == 'explicit':
        print("NOTE: identical key sizes at both pins, as explicit geometry should give when")
        print("no layout commit is in the span; the images are told apart by their")
        print("installed-ref file, not by this column.")
    elif not layout_moved:
        print("NOTE: no key size changed between the two pins. Either the commits do not")
        print("touch key layout, or both images are the same build -- check before reading")
        print("the ratios as a pin effect.")
    if moved:
        print("%d set(s) moved by more than %.1f%%: %s"
              % (len(moved), a.tol, ", ".join("%s %+.1f%%" % (s, 100 * (r - 1))
                                              for s, r in moved)))
        print("Raise REPS in the arm before concluding; if it holds, the cost cells need")
        print("re-measuring at this pin and any table picked on the old ones is suspect.")
        return 1
    print("no set moved by more than %.1f%%: the cells carry to this pin." % a.tol)
    return 0


if __name__ == '__main__':
    sys.exit(main())
