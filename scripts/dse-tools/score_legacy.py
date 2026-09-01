#!/usr/bin/env python3
"""Score the models against every candidate a selector run MEASURED.

    sage -python scripts/dse-tools/score_legacy.py legacy.log

`binfhe_params.py` measures every candidate it considers, so one of its logs is
a set of out-of-sample checks on the noise and cost models -- taken by a
different tool, on configurations the search grid may never visit. This pairs
each `(actual noise, mean, EvalBinGate us)` line with the `noise:` command that
produced it, rebuilds the candidate, and prints measured against predicted
sigma and gate time.

Two columns matter as much as the ratios. `grid` says whether the candidate was
even reachable by the search (an off-grid dimension or key-switching modulus is
a gap in the grid, not a model error), and `prune` says what the gates would
have said about it. Rows measured AT the wrap ceiling are called out: their
sigma is the ceiling rather than a measurement, and the model refuses them by
design.

Needs Sage only for the security lookup that `prune` performs; everything else
is arithmetic.
"""
import argparse
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "paramsestimator"))

import dse_model as m                                    # noqa: E402
import dse_enumerate as e                                # noqa: E402
import dse_constraints as c                              # noqa: E402

RESULT = re.compile(
    r"\(actual noise, mean, EvalBinGate us\) \(([\d.]+), ([-\d.]+), ([\d.]+)\)")


def read(path):
    """[{n, q, N, logQ, qks, bg, bks, sigma, gate}] from a selector log."""
    rows, cmd = [], None
    for line in open(path):
        if line.startswith("noise:"):
            cmd = line
            continue
        hit = RESULT.match(line.strip())
        if hit and cmd:
            flags = dict(re.findall(r"-(\w) (\d+)", cmd))
            try:
                rows.append(dict(
                    n=int(flags["n"]), q=int(flags["q"]), N=int(flags["N"]),
                    logQ=int(flags["Q"]), qks=int(flags["k"]),
                    bg=int(flags["g"]), bks=int(flags["b"]),
                    sigma=float(hit.group(1)), gate=float(hit.group(3))))
            except KeyError:
                pass            # a named-set run carries no geometry flags
            cmd = None
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="a binfhe_params.py run's log")
    ap.add_argument("--method", default="GINX", choices=("GINX", "LMKCDEY"))
    ap.add_argument("--level", default="STD128")
    ap.add_argument("--tolerance", type=int, default=1)
    ap.add_argument("--inputs", type=int, default=2)
    a = ap.parse_args()

    rows = read(a.log)
    if not rows:
        sys.exit("%s: no measured candidates found" % a.log)
    grid = e.DEFAULT_GRID
    cache = e.sec.load()

    print("%-5s %-5s %-5s %-3s %-6s %-4s %-3s %-6s | %-7s %-7s %-5s | %-7s %-7s %-5s | %s"
          % ("n", "q", "N", "lQ", "lqKS", "bKS", "dKS", "bG",
             "meas_s", "pred_s", "ratio", "meas_us", "pred_us", "ratio",
             "grid / prune"))

    clean = []
    for r in rows:
        gmap = {r["bg"]: r["n"]}
        log_qks = int(math.log2(r["qks"]))
        word = 32 if c.hybrid_ns32_ok(r["logQ"], gmap, a.method,
                                      default_base=r["bg"]) else 64
        try:
            pred_sigma = m.sigma_total_at_q(
                r["N"], r["n"], r["q"], r["logQ"], r["qks"], r["bks"], 3.19, gmap,
                key_dist="UNIFORM_TERNARY", method=a.method)
        except Exception:
            pred_sigma = float("nan")
        pred_gate = m.gate_us(gmap, r["logQ"], r["N"], method=a.method,
                              word_size=word, autokeys=10)
        d_ks = m.digits_for_base(log_qks, r["bks"], modulus=r["qks"])

        in_grid = (log_qks in grid["log_q_ks"] and r["bks"] in grid["base_ks"]
                   and r["N"] in grid["N"] and r["q"] in grid["q"])
        cand = dict(N=r["N"], n=r["n"], q=r["q"], log_q_big=r["logQ"], q_ks=r["qks"],
                    base_ks=r["bks"], sigma=3.19, inputs=a.inputs, method=a.method,
                    key_dist="UNIFORM_TERNARY", word_size=word, autokeys=10,
                    gadget_shape_keys=(r["bg"],), split_count=0)
        why = e.prune(cand, level=a.level, secret_dist="ternary",
                      sec_source="estimator", sec_cache=cache,
                      sec_tolerance=a.tolerance, w32_needs_hybrid=True)

        ceiling = m.saturated_sigma(r["q"], a.inputs)
        at_ceiling = r["sigma"] >= 0.9 * ceiling
        usable = (not at_ceiling) and m.within_model_domain(
            r["N"], r["qks"], r["bks"], 3.19)
        if usable:
            clean.append(pred_sigma / r["sigma"])

        print("%-5d %-5d %-5d %-3d %-6d %-4d %-3d 2^%-4d | %7.2f %7.2f %5.2f | "
              "%7.0f %7s %5s | %s%s%s"
              % (r["n"], r["q"], r["N"], r["logQ"], log_qks, r["bks"], d_ks,
                 int(math.log2(r["bg"])), r["sigma"], pred_sigma,
                 pred_sigma / r["sigma"], r["gate"],
                 ("%.0f" % pred_gate) if pred_gate else "-",
                 ("%.2f" % (pred_gate / r["gate"])) if pred_gate else "-",
                 "in grid" if in_grid else "OFF GRID(%s)" % (
                     "qKS" if log_qks not in grid["log_q_ks"] else "bKS/N/q"),
                 (" ; prune: " + why) if why else " ; survives",
                 ("  [MEASURED AT THE WRAP CEILING %.0f: not a sigma]" % ceiling)
                 if at_ceiling else ""))

    if clean:
        clean.sort()
        print("\npredicted/measured sigma over the %d candidates inside the model's "
              "domain and below the wrap ceiling: median %.3f  min %.3f  max %.3f"
              % (len(clean), clean[len(clean) // 2], clean[0], clean[-1]))
        print("(%d others: at the ceiling, or in the key-switch-saturated corner "
              "the model refuses)" % (len(rows) - len(clean)))


if __name__ == "__main__":
    main()
