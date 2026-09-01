#!/usr/bin/env python3
"""Choose CPUs to pin a timing run to: N physical cores inside ONE NUMA node.

    pick_cpus.py N [--sysfs-root DIR] [--node K]

Prints a `taskset -c` list, or exits 1 with the reason on stderr if it cannot
satisfy the request. Standard library only; it runs inside the measurement
container, which has taskset and lscpu but no numactl.

WHY A TIMING RUN MUST BE PINNED ON A BIG BOX. Eight OpenMP threads left to the
scheduler on a 72-core machine land differently every run: all on one socket, or
split across two, or scattered over four sub-NUMA domains. The bootstrapping key
is hundreds of megabytes to gigabytes and is faulted in by whichever thread
touches it first, so a placement that separates the threads from that memory pays
remote-access cost on every gate. The result is not noise around a true value; it
is several distinct values depending on where the run happened to land, which
shows up as a fit residual of 3 to 11 percent where a pinned box gives 2 to 3,
and as `c3` terms that stop resolving because they come from matched pairs.
Measured on a 72-core box: GINX intra-run skews of 163 to 385 percent under the
scheduler, and none under a cpuset.

ONE CPU PER PHYSICAL CORE, which is the other half. Taking the first N entries of
a node's CPU list takes hyperthread siblings wherever the enumeration interleaves
them, so a request for 8 becomes 4 cores at 2:1 oversubscription -- and that reads
as a large regression that is entirely an artefact of the binding. This reads
`thread_siblings_list` and keeps one CPU per core.
"""
import argparse
import os
import sys


def expand(spec):
    """'0-3,8,12-13' -> [0,1,2,3,8,12,13]"""
    out = []
    for part in spec.strip().split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            lo, hi = part.split('-')
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def compress(cpus):
    """[0,1,2,3,8] -> '0-3,8', which is what taskset -c wants."""
    out, run = [], []
    for c in sorted(cpus):
        if run and c == run[-1] + 1:
            run.append(c)
            continue
        if run:
            out.append(run)
        run = [c]
    if run:
        out.append(run)
    return ','.join(str(r[0]) if len(r) == 1 else "%d-%d" % (r[0], r[-1]) for r in out)


def nodes(root):
    """[(node index, [cpu, ...])], low index first. One entry when the machine
    exposes no NUMA topology, so the same code path pins a single-socket box."""
    base = os.path.join(root, 'devices', 'system', 'node')
    found = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if not name.startswith('node') or not name[4:].isdigit():
                continue
            p = os.path.join(base, name, 'cpulist')
            try:
                with open(p) as f:
                    found.append((int(name[4:]), expand(f.read())))
            except OSError:
                continue
    if found:
        return found
    online = os.path.join(root, 'devices', 'system', 'cpu', 'online')
    try:
        with open(online) as f:
            return [(0, expand(f.read()))]
    except OSError:
        return []


def one_per_core(root, cpus):
    """`cpus` thinned to one CPU per physical core, order preserved."""
    seen, out = set(), []
    for c in cpus:
        sib = os.path.join(root, 'devices', 'system', 'cpu', 'cpu%d' % c,
                           'topology', 'thread_siblings_list')
        try:
            with open(sib) as f:
                core = min(expand(f.read()))
        except OSError:
            core = c
        if core in seen:
            continue
        seen.add(core)
        out.append(c)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('want', type=int, help='how many physical cores to pin to')
    ap.add_argument('--sysfs-root', default='/sys',
                    help='for testing against a recorded topology')
    ap.add_argument('--node', type=int, default=None,
                    help='require this NUMA node rather than the first that fits')
    a = ap.parse_args(argv)
    if a.want < 1:
        sys.exit("pick_cpus: want must be >= 1")

    ns = nodes(a.sysfs_root)
    if not ns:
        print("pick_cpus: no CPU topology under %s" % a.sysfs_root, file=sys.stderr)
        return 1
    tried = []
    for idx, cpus in ns:
        if a.node is not None and idx != a.node:
            continue
        cores = one_per_core(a.sysfs_root, cpus)
        tried.append((idx, len(cores)))
        if len(cores) >= a.want:
            print(compress(cores[:a.want]))
            print("pick_cpus: %d core(s) on NUMA node%d (of %d there)"
                  % (a.want, idx, len(cores)), file=sys.stderr)
            return 0
    print("pick_cpus: no single NUMA node has %d physical core(s); nodes offer %s"
          % (a.want, ", ".join("node%d:%d" % t for t in tried)), file=sys.stderr)
    print("pick_cpus: pin by hand (PIN_CPUS) or lower the thread count -- a run"
          " spanning nodes is not comparable to one that does not", file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
