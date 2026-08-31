#!/usr/bin/python

'''
Run with `sage -python`, not `python3`: this imports the lattice-estimator,
which needs SageMath. (Plain python3 works only where sagelib is installed into
the system python, as `apt install sagemath` does.)

usage (1): Without arguments then answer the prompts.
    > sage -python scripts/paramsestimator/binfhe_params.py

usage (2): With any of {t, d, p, f, I, i, K, x, k, l, u, n} arguments to bypass
the prompts. Run with -h for the full list.
    > sage -python scripts/paramsestimator/binfhe_params.py -t 2 -p STD128 -I 2 -f -32

usage (3): with the --all flag to sweep every (security level, gate input)
combination for the given -t and -d.
    > sage -python scripts/paramsestimator/binfhe_params.py --all -t 3 -f -30 -i 800 -n 16
'''

from itertools import product
from math import log2, floor, sqrt, ceil

import argparse
import binfhe_params_helper as helperfncs
import paramstable as stdparams
import os
import sys

FORCE_q_eq_2N = False
FORCE_openfhe32 = False
FINAL_SAMPLES_FLOOR = 1000

class SweepResults:
    """Every configuration a sweep completed, so it can name the winner itself.

    The winner today is the fastest configuration whose measured failure
    probability beats the target. Records also carry noise, key sizes and
    keygen time, so a Pareto rule can replace that criterion later without
    re-measuring anything.

    The speed prune takes its bar from the same place. Pruning against a
    separately tracked "best" would be a second definition of the winner, free
    to drift from the one actually reported.
    """
    def __init__(self, target_log2pf, prune_pct = None):
        self.target    = target_log2pf
        self.prune_pct = prune_pct
        self.records   = []
        self.skipped   = []     # (label, reason)
        self.pruned    = 0

    def add(self, **rec):
        self.records.append(rec)
        return rec

    def note_skipped(self, label, reason):
        self.skipped.append((label, reason))

    def qualifying(self):
        return [r for r in self.records if r["log2pf"] <= self.target]

    def winner(self):
        q = self.qualifying()
        return min(q, key=lambda r: r["gate_us"]) if q else None

    # -- speed pruning, measured against the current winner --
    def limit(self):
        w = self.winner()
        if (self.prune_pct is None) or (w is None):
            return None
        return w["gate_us"] * (1.0 + self.prune_pct/100.0)

    def too_slow(self, gate_us):
        lim = self.limit()
        if (lim is not None) and (gate_us > lim):
            self.pruned += 1
            return True
        return False

    def best_gate_us(self):
        w = self.winner()
        return None if (w is None) else w["gate_us"]

    def report(self, header):
        print("")
        print("=" * 78)
        print("sweep summary: " + header)
        print("security model: " + helperfncs.security_model_description())
        print("=" * 78)

        if self.records:
            print("%-8s %10s %12s %11s %11s   %s" % ("d_g", "gate(ms)", "log2Pf", "keys(MiB)", "n", "target"))
            for r in sorted(self.records, key=lambda r: r["gate_us"]):
                print("%-8s %10.1f %12.1f %11.0f %11d   %s"
                      % (r["label"], r["gate_us"]/1000.0, r["log2pf"], r["key_bytes"]/2**20,
                         r["n"], "met" if (r["log2pf"] <= self.target) else "MISSED"))

        for label, reason in self.skipped:
            print("%-8s %s" % (label, reason))

        w = self.winner()
        print("-" * 78)
        if w is None:
            print("no configuration met the target failure rate of 2^%s" % self.target)
            if self.pruned:
                print("%d candidate(s) were pruned on speed; raise -x or drop it to consider them" % self.pruned)
        else:
            print("WINNER (fastest of %d meeting 2^%s): d_g %s at %.1f ms/gate, 2^%.1f"
                  % (len(self.qualifying()), self.target, w["label"], w["gate_us"]/1000.0, w["log2pf"]))
            print("  command args: " + w["command_arg"])
            print("  table entry:  " + w["table_entry"])
        print("=" * 78)

def measure_candidate(params, num_of_samples, num_of_inputs, num_of_keys, results):
    """(stddev, mean, gate us, perf), or None if pruned on speed.

    Speed is cheap to measure (one keygen and a few gates); noise is not (a
    thousand gates on each of several keys). Once some configuration meets the
    target, a candidate far slower than it can never be the one you ship, so
    measuring its noise is wasted work.
    """
    gate_us, perf = helperfncs.speed_of(params, num_of_inputs)

    if results.too_slow(gate_us):
        print("pruned n=%s: %.0f us/gate is %.1f%% slower than the best solution so far (%.0f us)"
              % (params.n, gate_us, 100.0*(gate_us/results.best_gate_us() - 1.0), results.best_gate_us()))
        return None

    return helperfncs.measure(params, num_of_samples, num_of_inputs, num_of_keys, gate_us, perf)

def parameter_selector(bootstrapping_tech, secret_dist, exp_sec_level, exp_decryption_failure, num_of_inputs, num_of_samples, d_ks, lower, upper, num_threads, num_of_keys = helperfncs.DEFAULT_NUM_KEYS, prune_pct = None):
    # processing parameters based on the inputs
    is_quantum = (exp_sec_level[-1] == "Q")
    secret_dist_des = ("error", "ternary")[secret_dist]

    print("input parameters")
    print("bootstrapping_tech: ",bootstrapping_tech)
    print("dist_type: ",secret_dist_des)
    print("sec_level: ", exp_sec_level)
    print("expected decryption failure rate: ", exp_decryption_failure)
    print("num_of_inputs: ", num_of_inputs)
    print("num_of_samples: ", num_of_samples)
    print("num_of_keys: ", num_of_keys)
    print("prune candidates slower than (%): ", prune_pct)
    print("d_ks: ", d_ks)
    print("d_g lower bound: ", lower)
    print("d_g upper bound: ", upper)
    print("num_of_threads: ", num_threads)
    print("security model: ", helperfncs.security_model_description())

    command_arg = ' '.join(a for a in [ '-t ' + str(bootstrapping_tech),
                             '-d ' + str(secret_dist),
                             '-p ' + str(exp_sec_level),
                             '-f ' + str(exp_decryption_failure),
                             '-I ' + str(num_of_inputs),
                             '-i ' + str(num_of_samples),
                             '-K ' + str(num_of_keys),
                             ('-x ' + str(prune_pct)) if (prune_pct is not None) else '',
                             '-k ' + str(d_ks),
                             '-l ' + str(lower),
                             '-u ' + str(upper),
                             '-n ' + str(num_threads)
                           ] if a)
    print("command args: ", command_arg)

    ########################################################
    # set ptmod based on num of inputs
    ptmod = 2*num_of_inputs
    results = SweepResults(exp_decryption_failure, prune_pct)

    sigma = 3.19
    d_ks_input = d_ks

    # Search points already visited in this sweep, keyed by the parameters that
    # actually reach OpenFHE. Distinct -d_g requests collapse onto the same B_g
    # once it is quantised to a power of two, and an identical configuration has
    # identical noise -- so re-searching it is pure wall-clock, and reporting it
    # twice would show one measurement as two independent data points.
    searched = {}

    for d_g in range(lower, upper + 1):
        # Set ringsize n, Qks, N, Q based on the security level
        print("\nd_g loop: ", d_g)
        pruned_at_entry = results.pruned
        duplicate_of = None
        d_g_label    = str(d_g)
        # STD256Q starts at N=2048 to dodge a hard failure, not as a preference:
        # OpenFHE derives Q as LastPrime(numberBits, 2N), which THROWS rather
        # than returning a shorter prime when none of the right width exists.
        # Measured against the pinned build, LastPrime(nBits, 2048) throws for
        # nBits <= 13, and STD256Q/ternary at N=1024 asks for exactly 13. Every
        # other (level, distribution, N) the search reaches clears it, but
        # STD256/ternary at N=1024 sits one bit above at 14 -- so do not lower
        # this without re-checking against LastPrime.
        ringsize_N = 2048 if exp_sec_level in ('STD256Q',) else 1024
        opt_n = 0
        optlogmodQks = 0
        optB_ks = 0
        while (ringsize_N <= (1024 if FORCE_openfhe32 else 2048)):
            modulus_q = 2*ringsize_N if FORCE_q_eq_2N else ringsize_N
            loopq2N = False
            while (modulus_q <= 2*ringsize_N):
                print("(q, N): (" + str(modulus_q) + ", " + str(ringsize_N) + ")")

                d_ks = d_ks_input
                B_rk = 32 if (modulus_q == 1024) else 64

                # for stdnum security, could set to ringsize_N/2
                # start with this value and binary search on n to find optimal parameter set
                lattice_n = 100

                # find analytical estimate for starting point of Qks
                logmodQksu = helperfncs.get_mod(lattice_n, exp_sec_level, secret_dist_des)
                logmodQu = helperfncs.get_mod(ringsize_N, exp_sec_level, secret_dist_des)

                # check security by running the estimator and adjust modulus if needed
                dimn, modulus_Qks = helperfncs.optimize_params_security(stdparams.security_bits[exp_sec_level], lattice_n, 2**logmodQksu, secret_dist_des, num_threads, is_quantum)
                dimN, modulus_Q = helperfncs.optimize_params_security(stdparams.security_bits[exp_sec_level], ringsize_N, 2**logmodQu, secret_dist_des, num_threads, is_quantum)

                while ((dimn == 0) or (modulus_Qks == 0)):
                    print("lattice dimension " + str(lattice_n) + " too small to run estimator for this security level, increasing value")
                    lattice_n = lattice_n + 25
                    logmodQksu = helperfncs.get_mod(lattice_n, exp_sec_level, secret_dist_des)
                    dimn, modulus_Qks = helperfncs.optimize_params_security(stdparams.security_bits[exp_sec_level], lattice_n, 2**logmodQksu, secret_dist_des, num_threads, is_quantum)

                if ((dimn > dimN) or (dimn == 0) or (modulus_Qks == 0)):
                    print("lattice dimension is 0 or greater than large N")
                    break

                # dimN is the ring dimension: optimize_params_security hands the
                # dimension straight back, so it is ringsize_N.
                logmodQ, logmodQks = helperfncs.clamp_moduli(log2(modulus_Q), log2(modulus_Qks), dimN)

                # modulus_Q is deliberately not refreshed from the clamped
                # exponent: nothing below reads it, and logmodQ is what the
                # parameter set carries.
                modulus_Qks = 2**logmodQks
                B_g = 2**ceil(logmodQ/d_g)
                B_ks = 2**ceil(logmodQks/d_ks)

                while (B_ks >= 128):
                    d_ks += 1
                    B_ks = 2**ceil(logmodQks/d_ks)

                # d_g is a REQUEST, not the outcome: B_g is quantised to a power
                # of two, so OpenFHE may span Q in fewer digits than asked. At
                # logQ=28, -u 9 asks for 7, 8 and 9 and gets 7 every time.
                eff_d_g   = helperfncs.digits_for_base(int(logmodQ), B_g)
                d_g_label = str(eff_d_g) if (eff_d_g == d_g) else "%d->%d" % (d_g, eff_d_g)

                # create paramset object
                param_set_opt = stdparams.paramsetvars(lattice_n, modulus_q, ringsize_N, logmodQ, modulus_Qks, B_g, B_ks, B_rk, sigma, secret_dist, bootstrapping_tech)

                # optimize n, Qks to reduce the noise
                # compute target noise level for the expected decryption failure rate
                target_noise_level = helperfncs.get_target_noise(exp_decryption_failure, ptmod, modulus_q, num_of_inputs)
                print("target noise for this iteration: ", target_noise_level)

                key   = (ringsize_N, modulus_q, int(logmodQ), B_g, B_ks)
                prior = searched.get(key)
                if prior is not None:
                    # Same (N, q, Q, B_g, B_ks) as an earlier d_g, so the search
                    # would retrace it exactly. Adopt its outcome: on success
                    # this d_g is that d_g, on failure let the (q, N) escalation
                    # continue as it would have.
                    prior_label, (opt_n, optlogmodQks, optB_ks) = prior
                    print("d_g %d at (q, N) = (%d, %d) is d_g %s again: B_g %d, %d digits -- reusing that search"
                          % (d_g, modulus_q, ringsize_N, prior_label, B_g, eff_d_g))
                    if ((opt_n != 0) and (optlogmodQks != 0) and (optB_ks != 0)):
                        duplicate_of = prior_label
                        break
                else:
                    opt_n, optlogmodQks, optB_ks = binary_search_n(lattice_n, ringsize_N, target_noise_level + 1, exp_sec_level, target_noise_level, num_of_samples, d_ks, param_set_opt, secret_dist_des, is_quantum, num_threads, num_of_inputs, num_of_keys, results)
                    searched[key] = (d_g_label, (opt_n, optlogmodQks, optB_ks))

                if ((opt_n != 0) and (optlogmodQks != 0) and (optB_ks != 0)):
                    break
                if (((opt_n == 0) or (optlogmodQks == 0) or (optB_ks == 0)) and loopq2N):
                    break

                modulus_q = 2*modulus_q
                print("increasing q to " + str(modulus_q))
                loopq2N = True

            if ((opt_n != 0) and (optlogmodQks != 0) and (optB_ks != 0)):
                break

            ringsize_N *= 2
            print("increasing N to " + str(ringsize_N))

        if (duplicate_of is not None):
            reason = ("same configuration as d_g %s -- B_g %d spans Q in %d digits either way; not measured again"
                      % (duplicate_of, B_g, eff_d_g))
            print("d_g %d: %s" % (d_g, reason))
            results.note_skipped(d_g_label, reason)
        elif ((opt_n == 0) or (optlogmodQks == 0) or (optB_ks == 0)):
            if (results.pruned > pruned_at_entry):
                # not the same thing as infeasible: these were skipped on speed
                reason = ("all %d candidates measured were slower than the best so far (%.0f us/gate); "
                          "raise -x or drop it to see them"
                          % (results.pruned - pruned_at_entry, results.best_gate_us()))
            else:
                reason = "no parameters found meeting the target"
            print("d_g %d: %s" % (d_g, reason))
            results.note_skipped(d_g_label, reason)
        else:
            optQks = 2**optlogmodQks
            optd_ks = helperfncs.digits_for_base(optlogmodQks, optB_ks)
            B_g = 2**ceil(logmodQ/d_g)

            param_set_final = stdparams.paramsetvars(opt_n, modulus_q, ringsize_N, logmodQ, optQks, B_g, optB_ks, B_rk, sigma, secret_dist, bootstrapping_tech)
            # The winner is confirmed at least as precisely as the candidates it
            # beat, so a larger -i raises this too rather than being ignored.
            final_samples = max(num_of_samples, FINAL_SAMPLES_FLOOR)
            finalnoise, finalmean, final_gate_us, perf = helperfncs.measure(param_set_final, final_samples, num_of_inputs, num_of_keys)
            final_dec_fail_rate = helperfncs.get_decryption_failure(finalnoise, ptmod, modulus_q, num_of_inputs)

            print("final parameters")
            print("dist_type: ",secret_dist_des)
            print("bootstrapping_tech: ",bootstrapping_tech)
            print("sec_level: ", exp_sec_level)
            print("expected decryption failure rate: ", exp_decryption_failure)
            print("actual decryption failure rate: ", final_dec_fail_rate)
            print("num_of_inputs: ", num_of_inputs)
            print("num_of_samples: ", num_of_samples)
            print("num_of_keys: ", num_of_keys)
            print("lattice dimension n: ", opt_n)
            print("ringsize N: ", ringsize_N)
            print("lattice modulus n: ", modulus_q)
            print("size of ring modulus Q: ", logmodQ)
            print("optimal key switching modulus  Qks: ", optQks)
            print("gadget digit base B_g: ", B_g)
            print("key switching digit base B_ks: ", optB_ks)
            print("key switching digit size B_ks: ", optd_ks)
            print("gadget digit count d_g (requested, effective): ", d_g, eff_d_g)
            for k, v in perf.items():
                print(': '.join((k, v.split()[0])))

            command_arg = ' '.join([ "-n " + str(opt_n),
                                     "-N " + str(ringsize_N),
                                     "-q " + str(modulus_q),
                                     "-Q " + str(int(logmodQ)),
                                     "-k " + str(int(optQks)),
                                     "-g " + str(B_g),
                                     "-r " + str(B_rk),
                                     "-b " + str(optB_ks),
                                     "-s " + str(sigma),
                                     "-t " + str(bootstrapping_tech),
                                     "-d " + str(secret_dist),
                                     "-I " + str(num_of_inputs),
                                     "-i " + str(final_samples),
                                     "-K " + str(num_of_keys),
                                   ])
            print("command args: ", command_arg)

            # trailing field is gadgetBaseMap: this search only ever evaluates a
            # single gadget base, so all latticeParam coefficients get B_g
            table_entry = '{ ' + ', '.join(( str(int(logmodQ)), str(2*ringsize_N), str(opt_n), str(modulus_q), str(int(optQks)), str(optB_ks),
                str(B_g), str(B_rk), str(10), ('GAUSSIAN', 'UNIFORM_TERNARY')[secret_dist], str(sigma),
                '{{' + str(B_g) + ', ' + str(opt_n) + '}}')) + ' }'
            print("table entry: ", table_entry)

            # Remembering the whole candidate, not just its speed, is what lets
            # the sweep name a winner at the end -- and lets a later Pareto rule
            # weigh key size or noise margin without re-measuring anything.
            results.add(label       = d_g_label,
                        n           = opt_n,
                        gate_us     = final_gate_us,
                        log2pf      = final_dec_fail_rate,
                        noise_stdev = finalnoise,
                        noise_mean  = finalmean,
                        key_bytes   = helperfncs._key_bytes(perf) or 0,
                        perf        = perf,
                        command_arg = command_arg,
                        table_entry = table_entry)

    results.report("%s, %s, %d-input, %s, target 2^%s"
                   % (exp_sec_level, secret_dist_des, num_of_inputs,
                      {1: "AP", 2: "GINX", 3: "LMKCDEY"}.get(bootstrapping_tech, "?"),
                      exp_decryption_failure))

def binary_search_n(start_n, end_N, prev_noise, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, secret_dist_des, is_quantum, num_threads, num_of_inputs, num_of_keys, results):
    n = 0
    retlogmodQks = 0
    retBks = 0
    found = False
    d_ks_reset_loop = d_ks

    early_exit_tst = True

    while(start_n <= end_N):
        d_ks = d_ks_reset_loop
        new_n = end_N if early_exit_tst else floor((start_n + end_N)/2)

        cand_n = new_n
        logmodQks = helperfncs.get_mod(cand_n, exp_sec_level, secret_dist_des)
        sec_n, modQks = helperfncs.optimize_params_security(stdparams.security_bits[exp_sec_level], cand_n, 2**logmodQks, secret_dist_des, num_threads, is_quantum)

        # treat the candidate as infeasible and move on.
        if ((sec_n == 0) or (modQks == 0)):
            print("estimator could not price n = " + str(cand_n) + ", skipping it")
            if (early_exit_tst):
                break
            start_n = cand_n + 1
            continue

        new_n = sec_n
        # params.logQ was clamped when the parameter set was built, so this only
        # moves Qks; feeding logQ back through is a no-op and keeps one rule.
        params.logQ, logmodQks = helperfncs.clamp_moduli(params.logQ, log2(modQks), params.N)

        params.n = new_n
        params.Qks = 2**logmodQks
        B_ks = 2**ceil(logmodQks/d_ks)
        while (B_ks >= 128):
            d_ks += 1
            B_ks = 2**ceil(logmodQks/d_ks)

        params.B_ks = B_ks

        measured = measure_candidate(params, num_of_samples, num_of_inputs, num_of_keys, results)
        if measured is None:
            # Too slow to beat what we already have: skip the noise run and keep
            # shrinking n, the direction that gets faster. Every larger n is
            # slower still, so the upper bound moves here.
            early_exit_tst = False
            end_N = cand_n - 1
            continue
        new_noise, new_mean, gate_us, perf = measured
        print("(actual noise, mean, EvalBinGate us) (" + str(new_noise) + ", " + str(new_mean) + ", " + str(gate_us) + ")")

        if (early_exit_tst):
            if ((new_noise - target_noise_level) > 8):
                break
            early_exit_tst = False
            continue

        if (new_noise > target_noise_level and prev_noise <= target_noise_level):
            found = True
            n = prev_n
            retlogmodQks = prevlogmodQks
            retBks = prevBks
            break
        if (new_noise < target_noise_level):
            n = new_n
            retlogmodQks = logmodQks
            retBks = B_ks
            end_N = new_n - 1
        else:
            start_n = new_n + 1

        prev_noise = new_noise
        prev_n = new_n
        prevlogmodQks = logmodQks
        prevBks = B_ks

    # add code to check if any n value lesser than the obtained n could result in the same or lower noise level
    if ((found) and (new_n < prev_n)):
        params.Qks = 2**retlogmodQks
        params.B_ks = retBks
        n, retlogmodQks, retBks = find_opt_n(new_n, prev_n, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, secret_dist_des, is_quantum, num_threads, num_of_inputs, num_of_keys, results)

    return n, retlogmodQks, retBks

def find_opt_n(start_n, end_n, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, secret_dist_des, is_quantum, num_threads, num_of_inputs, num_of_keys, results):
    opt_n = end_n
    optlogmodQks = log2(params.Qks)
    optBks = params.B_ks
    d_ks_reset_loop = d_ks
    while (start_n <= end_n):
        d_ks = d_ks_reset_loop
        newopt_n = floor((start_n + end_n)/2)

        cand_n = newopt_n
        logmodQks = helperfncs.get_mod(cand_n, exp_sec_level, secret_dist_des)
        sec_n, modQks = helperfncs.optimize_params_security(stdparams.security_bits[exp_sec_level], cand_n, 2**logmodQks, secret_dist_des, num_threads, is_quantum)

        # as in binary_search_n: unpriceable candidate, not a candidate of size 0
        if ((sec_n == 0) or (modQks == 0)):
            print("estimator could not price n = " + str(cand_n) + ", skipping it")
            start_n = cand_n + 1
            continue

        newopt_n = sec_n
        # params.logQ was clamped when the parameter set was built, so this only
        # moves Qks; feeding logQ back through is a no-op and keeps one rule.
        params.logQ, logmodQks = helperfncs.clamp_moduli(params.logQ, log2(modQks), params.N)

        params.n = newopt_n
        params.Qks = 2**logmodQks

        B_ks = 2**ceil(logmodQks/d_ks)
        while (B_ks >= 128):
            d_ks += 1
            B_ks = 2**ceil(logmodQks/d_ks)

        params.B_ks = B_ks

        measured = measure_candidate(params, num_of_samples, num_of_inputs, num_of_keys, results)
        if measured is None:
            end_n = cand_n - 1
            continue
        new_noise, new_mean, gate_us, perf = measured
        print("(actual noise, mean, EvalBinGate us) (" + str(new_noise) + ", " + str(new_mean) + ", " + str(gate_us) + ")")

        if (new_noise < target_noise_level):
            opt_n = newopt_n
            optlogmodQks = logmodQks
            optBks = B_ks
            end_n = newopt_n - 1
        else:
            start_n = newopt_n + 1


    return opt_n, optlogmodQks, optBks

if __name__ == '__main__':
    if (len(sys.argv) == 1):
        '''
        Approach for determining parameters for binfhe
        1) Pick bootstrapping method
        2) Pick secret distribution
        3) Pick security level
        4) Set expected decryption failure rate
        5) Specify max number of inputs to a boolean gate
        Measure bootstrap keygen/evalbingate time, and throughput (bootstrap keygen size, keyswitching key size, ciphertext size) and document.
        '''

        print("Parameter selector for FHEW like schemes")

        bootstrapping_tech_in = input("Enter Bootstrapping technique (1 = AP, 2 = GINX, 3 = LMKCDEY) [default = 2]: ")
        if (not bootstrapping_tech_in):
            bootstrapping_tech_in = 2
        bootstrapping_tech = int(bootstrapping_tech_in)
        if ((bootstrapping_tech != 1) and (bootstrapping_tech != 2) and (bootstrapping_tech != 3)):
            bootstrapping_tech = 2

        secret_dist_in = input("Enter Secret distribution (0 = error, 1 = ternary) [default = 1]: ")
        if ( not secret_dist_in):
            secret_dist_in = 1
        secret_dist = int(secret_dist_in)
        if ((secret_dist != 0) and (secret_dist != 1)):
            secret_dist = 1

        exp_sec_level = input("Enter Security level (STD128, STD128Q, STD192, STD192Q, STD256, STD256Q) [default = STD128]: ")
        if (not exp_sec_level):
            exp_sec_level = "STD128"

        exp_decryption_failure_in = input("Enter expected decryption failure rate (for example, enter -32 for 2^-32 failure rate) [default = -40]: ")
        if (not exp_decryption_failure_in):
            exp_decryption_failure_in = -40
        exp_decryption_failure = int(exp_decryption_failure_in)

        num_of_inputs_in = input("Enter expected number of inputs to the boolean gate (2, 3, or 4) [default = 2]: ")
        if (not num_of_inputs_in):
            num_of_inputs_in = 2
        num_of_inputs = int(num_of_inputs_in)

        num_of_samples_in = input("Enter number of noise samples per key [default = 200]: ")
        if (not num_of_samples_in):
            num_of_samples_in = 200
        num_of_samples = int(num_of_samples_in)

        d_ks_in = input("Enter key switching digit size (2, 3, or 4) [default = 3]: ")
        if (not d_ks_in):
            d_ks_in = 3
        d_ks = int(d_ks_in)

        lower_in = input("Enter lower bound for digit decomposition digits [default = 2]: ")
        if (not lower_in):
            lower_in = 2
        lower = int(lower_in)

        upper_in = input("Enter upper bound for digit decomposition digits [default = 4]: ")
        if (not upper_in):
            upper_in = 4
        upper = int(upper_in)

        num_threads_in = input("Enter number of threads that can be used to run the lattice-estimator (only used for the estimator) [default = 1]: ")
        if (not num_threads_in):
            num_threads_in = 1
        num_threads = int(num_threads_in)

        num_of_keys_in = input("Enter number of independent keys to pool noise over [default = %d]: " % helperfncs.DEFAULT_NUM_KEYS)
        if (not num_of_keys_in):
            num_of_keys_in = helperfncs.DEFAULT_NUM_KEYS
        num_of_keys = int(num_of_keys_in)

        prune_pct_in = input("Enter % slower than a working set at which to skip a candidate's noise run [default = none]: ")
        prune_pct = float(prune_pct_in) if prune_pct_in else None

        parameter_selector(bootstrapping_tech, secret_dist, exp_sec_level, exp_decryption_failure, num_of_inputs, num_of_samples, d_ks, lower, upper, num_threads, num_of_keys, prune_pct)
    else:
        sec_levels  = ('STD128', 'STD128Q', 'STD192', 'STD192Q', 'STD256', 'STD256Q')
        boot_techs  = { 1 : "AP", 2 : "GINX", 3 : "LMKCDEY" }
        gate_inputs = (2, 3, 4)

        parser = argparse.ArgumentParser(prog='binfhe_params')
        parser.add_argument('-t', '--bootstrapping_tech', action='store', choices=boot_techs.keys(), default=2, type=int,
                            help='bootstrapping technique: 1 = AP, 2 = GINX, 3 = LMKCDEY')
        parser.add_argument('-d', '--secret_dist', choices=(0, 1), action='store', default=1, type=int,
                            help='LWE secret distribution: 0 = error (GAUSSIAN), 1 = ternary (UNIFORM_TERNARY)')
        parser.add_argument('-p', '--exp_sec_level', action='store', choices=sec_levels, default='STD128',
                            help='target security level; a trailing Q selects the quantum tables')
        parser.add_argument('-f', '--exp_decryption_failure', action='store', default=-40, type=int,
                            help='target log2 failure probability, e.g. -40 for 2^-40')
        parser.add_argument('-I', '--num_of_inputs', action='store', choices=gate_inputs, default=2, type=int,
                            help='inputs to the boolean gate being measured')
        parser.add_argument('-i', '--num_of_samples', action='store', default=200, type=int,
                            help='noise samples PER KEY; total samples = num_of_samples * num_of_keys')
        parser.add_argument('-k', '--d_ks', action='store', choices=(2, 3, 4), default=3, type=int,
                            help='key switching digit count; raised automatically if it would need B_ks >= 128')
        parser.add_argument('-l', '--lower', action='store', default=2, type=int,
                            help='lowest gadget digit count d_g to search')
        parser.add_argument('-u', '--upper', action='store', default=4, type=int,
                            help='highest gadget digit count d_g to search')
        parser.add_argument('-n', '--num_threads', action='store', default=1, type=int,
                            help='threads for the lattice-estimator only; measurement parallelism is sized automatically')
        parser.add_argument('-K', '--num_of_keys', action='store', default=helperfncs.DEFAULT_NUM_KEYS, type=int,
                            help='independent keys to pool noise over, measured in parallel (default 8)')
        parser.add_argument('-x', '--prune_pct', action='store', default=None, type=float,
                            help='skip the noise run for a candidate more than this %% slower than the fastest set already meeting the target')
        parser.add_argument('-a', '--all', action='store_true')
        a = parser.parse_args()

        if a.all:
#            for sl, gi, bt in product(sec_levels, gate_inputs, boot_techs.keys()):
            for sl, gi, bt in product(sec_levels, gate_inputs, [a.bootstrapping_tech,]):
                print('_'.join((sl, str(gi), boot_techs[bt])), '##########################################################################################\n')

                parameter_selector(bt, a.secret_dist, sl, a.exp_decryption_failure, gi, a.num_of_samples, a.d_ks, a.lower, a.upper, a.num_threads, a.num_of_keys, a.prune_pct)

                print('_'.join((sl, str(gi), boot_techs[bt])), '##########################################################################################\n')
        else:
            parameter_selector(a.bootstrapping_tech, a.secret_dist, a.exp_sec_level, a.exp_decryption_failure, a.num_of_inputs, a.num_of_samples, a.d_ks, a.lower, a.upper, a.num_threads, a.num_of_keys, a.prune_pct)
