#!/usr/bin/python

'''
usage (1): Without arguments then answer the prompts.
    > python3 scripts/paramsestimator/binfhe_params.py

usage (2): With any of {t, d, p, f, I, i, k, n} arguments to bypass the prompts. Argument explanations at the bottom of this file.
    > python3 scripts/paramsestimator/binfhe_params.py -t 2 -p STD128 -I 2 -f -32

usage (3): with the --all flag to iterate through all valid combinations of {p, I, d} arguments for a given t argument.
    > python3 scripts/paramsestimator/binfhe_params.py --all -t 3 -f -30 -i 800 -n 16
'''

from itertools import product
from math import log2, floor, sqrt, ceil

import argparse
import binfhe_params_helper as helperfncs
import paramstable as stdparams
import os
import sys

FORCE_q_eq_2N = True
FORCE_openfhe32 = False

def parameter_selector(bootstrapping_tech, secret_dist, exp_sec_level, exp_decryption_failure, num_of_inputs, num_of_samples, d_ks, lower, upper, num_threads):
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
    print("d_ks: ", d_ks)
    print("d_g lower bound: ", lower)
    print("d_g upper bound: ", upper)
    print("num_of_threads: ", num_threads)

    command_arg = ' '.join([ '-t ' + str(bootstrapping_tech),
                             '-d ' + str(secret_dist),
                             '-p ' + str(exp_sec_level),
                             '-f ' + str(exp_decryption_failure),
                             '-I ' + str(num_of_inputs),
                             '-i ' + str(num_of_samples),
                             '-k ' + str(d_ks),
                             '-l ' + str(lower),
                             '-u ' + str(upper),
                             '-n ' + str(num_threads)
                           ])
    print("command args: ", command_arg)

    ########################################################
    # set ptmod based on num of inputs
    ptmod = 2*num_of_inputs

    sigma = 3.19
    d_ks_input = d_ks

    for d_g in range(lower, upper + 1):
        # Set ringsize n, Qks, N, Q based on the security level
        print("\nd_g loop: ", d_g)
        ringsize_N = 1024
        opt_n = 0
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
                logmodQksu = helperfncs.get_mod(lattice_n, exp_sec_level)
                logmodQu = helperfncs.get_mod(ringsize_N, exp_sec_level)

                # check security by running the estimator and adjust modulus if needed
                dimn, modulus_Qks = helperfncs.optimize_params_security(stdparams.paramlinear[exp_sec_level][0], lattice_n, 2**logmodQksu, secret_dist_des, num_threads, False, True, False, is_quantum)
                dimN, modulus_Q = helperfncs.optimize_params_security(stdparams.paramlinear[exp_sec_level][0], ringsize_N, 2**logmodQu, secret_dist_des, num_threads, False, True, False, is_quantum)

                while ((dimn == 0) or (modulus_Qks == 0)):
                    print("lattice dimension " + str(lattice_n) + " too small to run estimator for this security level, increasing value")
                    lattice_n = lattice_n + 25
                    logmodQksu = helperfncs.get_mod(lattice_n, exp_sec_level)
                    dimn, modulus_Qks = helperfncs.optimize_params_security(stdparams.paramlinear[exp_sec_level][0], lattice_n, 2**logmodQksu, secret_dist_des, num_threads, False, True, False, is_quantum)

                if ((dimn > dimN) or (dimn == 0) or (modulus_Qks == 0)):
                    print("lattice dimension is 0 or greater than large N")
                    break

                logmodQks = log2(modulus_Qks)
                logmodQ = log2(modulus_Q)

                # set logQ upperbound to 28 for lattice dimension 1024
                if ((dimN <= 1024) and (logmodQ > 28)):
                    logmodQ = 28

                # this is added since Qks is declared as usint in openfhe
                if (logmodQks >= 32):
                    logmodQks = 30
                while(logmodQks > logmodQ):
                    logmodQks -= 1

                modulus_Qks = 2**logmodQks
                B_g = 2**ceil(logmodQ/d_g)
                B_ks = 2**ceil(logmodQks/d_ks)

                while (B_ks >= 128):
                    d_ks += 1
                    B_ks = 2**ceil(logmodQks/d_ks)

                # create paramset object
                param_set_opt = stdparams.paramsetvars(lattice_n, modulus_q, ringsize_N, logmodQ, modulus_Qks, B_g, B_ks, B_rk, sigma, secret_dist, bootstrapping_tech)

                # optimize n, Qks to reduce the noise
                # compute target noise level for the expected decryption failure rate
                target_noise_level = helperfncs.get_target_noise(exp_decryption_failure, ptmod, modulus_q, num_of_inputs)
                print("target noise for this iteration: ", target_noise_level)

                opt_n, optlogmodQks, optB_ks = binary_search_n(lattice_n, ringsize_N, target_noise_level + 1, exp_sec_level, target_noise_level, num_of_samples, d_ks, param_set_opt, secret_dist_des, is_quantum, num_threads, num_of_inputs)

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

        if ((opt_n == 0) or (optlogmodQks == 0) or (optB_ks == 0)):
            print("cannot find parameters for d_g: ", d_g)
        else:
            optQks = 2**optlogmodQks
            optd_ks = ceil(optlogmodQks/log2(optB_ks))
            B_g = 2**ceil(logmodQ/d_g)

            param_set_final = stdparams.paramsetvars(opt_n, modulus_q, ringsize_N, logmodQ, optQks, B_g, optB_ks, B_rk, sigma, secret_dist, bootstrapping_tech)
            finalnoise, perf = helperfncs.get_noise_from_cpp_code(param_set_final, 1000, num_of_inputs, True)
            final_dec_fail_rate = helperfncs.get_decryption_failure(finalnoise, ptmod, modulus_q, num_of_inputs)

            print("final parameters")
            print("dist_type: ",secret_dist_des)
            print("bootstrapping_tech: ",bootstrapping_tech)
            print("sec_level: ", exp_sec_level)
            print("expected decryption failure rate: ", exp_decryption_failure)
            print("actual decryption failure rate: ", final_dec_fail_rate)
            print("num_of_inputs: ", num_of_inputs)
            print("num_of_samples: ", num_of_samples)
            print("lattice dimension n: ", opt_n)
            print("ringsize N: ", ringsize_N)
            print("lattice modulus n: ", modulus_q)
            print("size of ring modulus Q: ", logmodQ)
            print("optimal key switching modulus  Qks: ", optQks)
            print("gadget digit base B_g: ", B_g)
            print("key switching digit base B_ks: ", optB_ks)
            print("key switching digit size B_ks: ", optd_ks)
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
                                     "-i 1000",
                                   ])
            print("command args: ", command_arg)


            print("table entry: ", '{ ' + ', '.join(( str(int(logmodQ)), str(2*ringsize_N), str(opt_n), str(modulus_q), str(int(optQks)), str(sigma),
                  str(optB_ks), str(B_g), str(B_rk), str(10), ('GAUSSIAN', 'UNIFORM_TERNARY')[secret_dist])) + ' }')

def binary_search_n(start_n, end_N, prev_noise, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, secret_dist_des, is_quantum, num_threads, num_of_inputs):
    n = 0
    retlogmodQks = 0
    retBks = 0
    found = False
    d_ks_reset_loop = d_ks

    early_exit_tst = True

    while(start_n <= end_N):
        d_ks = d_ks_reset_loop
        new_n = end_N if early_exit_tst else floor((start_n + end_N)/2)

        logmodQks = helperfncs.get_mod(new_n, exp_sec_level)
        new_n, modQks = helperfncs.optimize_params_security(stdparams.paramlinear[exp_sec_level][0], new_n, 2**logmodQks, secret_dist_des, num_threads, False, True, False, is_quantum)
        if (modQks > 0):
            logmodQks = log2(modQks)

        if (logmodQks >= 32):
            logmodQks = 30

        while(logmodQks > params.logQ):
            logmodQks -= 1

        params.n = new_n
        params.Qks = 2**logmodQks
        B_ks = 2**ceil(logmodQks/d_ks)
        while (B_ks >= 128):
            d_ks += 1
            B_ks = 2**ceil(logmodQks/d_ks)

        params.B_ks = B_ks
        new_noise, perf = helperfncs.get_noise_from_cpp_code(params, num_of_samples, num_of_inputs, True)
        print("(actual noise, EvalBinGate time) (" +  str(new_noise) + ", " + perf['EvalBinGateTime'].split()[0] + ")")

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
        params.Bks = retBks
        n, retlogmodQks, retBks = find_opt_n(new_n, prev_n, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, secret_dist_des, is_quantum, num_threads, num_of_inputs)

    return n, retlogmodQks, retBks

def find_opt_n(start_n, end_n, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, secret_dist_des, is_quantum, num_threads, num_of_inputs):
    opt_n = end_n
    optlogmodQks = log2(params.Qks)
    optBks = params.Bks
    d_ks_reset_loop = d_ks
    while (start_n <= end_n):
        d_ks = d_ks_reset_loop
        newopt_n = floor((start_n + end_n)/2)

        logmodQks = helperfncs.get_mod(newopt_n, exp_sec_level)
        newopt_n, modQks = helperfncs.optimize_params_security(stdparams.paramlinear[exp_sec_level][0], newopt_n, 2**logmodQks, secret_dist_des, num_threads, False, True, False, is_quantum)
        logmodQks = log2(modQks)

        if (logmodQks >= 32):
            logmodQks = 30

        while(logmodQks > params.logQ):
            logmodQks -= 1

        params.n = newopt_n
        params.Qks = 2**logmodQks

        B_ks = 2**ceil(logmodQks/d_ks)
        while (B_ks >= 128):
            d_ks += 1
            B_ks = 2**ceil(logmodQks/d_ks)

        params.B_ks = B_ks
        new_noise, perf = helperfncs.get_noise_from_cpp_code(params, num_of_samples, num_of_inputs, True)
        print("(actual noise, EvalBinGate time) (" +  str(new_noise) + ", " + perf['EvalBinGateTime'].split()[0] + ")")

        if (new_noise < target_noise_level):
            opt_n = newopt_n
            optlogmodQks = logmodQks
            optBks = B_ks
            end_n = newopt_n - 1
        else:
            start_n = newopt_n + 1


    return opt_n, optlogmodQks, optBks

# This function is no longer used -- was initially an attempt to search
# through optimal Qks for every n, very slow to do it while also optimizing for n
def binary_search_n_Qks(start_n, end_N, prev_noise, exp_sec_level, target_noise_level, num_of_samples, d_ks, params, num_of_inputs):
    n = 0

    retlogmodQks = 0
    retBks = 0

    intlogmodQks = 0
    intBks = 0
    int_noise = 0
    initiallogQks = log2(params.Qks)
    while(start_n <= end_N):
        new_n = floor((start_n + end_N)/2)
        print("new n: ", new_n)
        params.n = new_n
        logmodQks = helperfncs.get_mod(new_n, exp_sec_level)

        while(logmodQks > params.logQ):
            logmodQks -= 1

        if (logmodQks >= 32):
            logmodQks = 30

        startlogQks = initiallogQks

        endlogQks = logmodQks

        newlogmodQks = startlogQks
        # newlogmodQks = log2(startQks)

        found = False
        while(startlogQks <= endlogQks):
            params.Qks = 2**newlogmodQks
            B_ks = 2**ceil(newlogmodQks/d_ks)
            while (B_ks >= 128):
                B_ks = B_ks/2     # display in the final parameters if the d_ks value is different from input

            params.B_ks = B_ks
            new_noise, perf = helperfncs.get_noise_from_cpp_code(params, num_of_samples, num_of_inputs, True)
            print("(actual noise, EvalBinGate time) (" +  str(new_noise) + ", " + perf['EvalBinGateTime'].split()[0] + ")")

            # if (new_noise > target_noise_level and prev_noise <= target_noise_level):
            if (new_noise <= target_noise_level):
                print("in qks search break if")
                found = True
                n = new_n
                intlogmodQks = prevlogmodQks
                intBks = prevBks
                int_noise = prev_noise
            prev_found = found

            if found:
                break

            if (new_noise <= target_noise_level):
                print("in qks search new noise < target noise")
                endlogQks = newlogmodQks - 1
            else:
                print("in qks search new noise > target noise")
                startlogQks = newlogmodQks + 1

            prevlogmodQks = newlogmodQks
            prevBks = B_ks
            prev_noise = new_noise

            newlogmodQks = ceil((startlogQks + endlogQks)/2)
            print("newlogmodQks: ", newlogmodQks)
            print("startlogQks: ", startlogQks)
            print("endlogQks: ", endlogQks)

        print("int_noise: ", int_noise)
        print("new_noise: ", new_noise)

        print("prev_found: ", prev_found)
        print("found: ", found)

        if (prev_found and (not found) and (new_noise <= int_noise)):
            end_N = new_n - 1
        elif (prev_found and (not found) and (new_noise > int_noise)):
            n = new_n
            retlogmodQks = intlogmodQks
            retBks = intBks
            break
        else:
            if (new_noise > target_noise_level):
                start_n = new_n + 1
            else:
                end_N = new_n - 1

        if (prev_found and (not found)):
            retlogmodQks = intlogmodQks
            retBks = intBks


    # add code to check if any n value lesser than the obtained n could result in the same or lower noise level
    # if (new_noise > target_noise_level and prev_noise <= target_noise_level):

    return n, retlogmodQks, retBks


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

        num_of_samples_in = input("Enter expected number of samples to estimate noise [default = 200]: ")
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
        parameter_selector(bootstrapping_tech, secret_dist, exp_sec_level, exp_decryption_failure, num_of_inputs, num_of_samples, d_ks, lower, upper, num_threads)
    else:
        sec_levels  = ('STD128', 'STD128Q', 'STD192', 'STD192Q', 'STD256', 'STD256Q')
        boot_techs  = { 1 : "AP", 2 : "GINX", 3 : "LMKCDEY" }
        gate_inputs = (2, 3, 4)

        parser = argparse.ArgumentParser(prog='binfhe_params')
        parser.add_argument('-t', '--bootstrapping_tech', action='store', choices=boot_techs.keys(), default=2, type=int)
        parser.add_argument('-d', '--secret_dist', choices=(0, 1), action='store', default=1, type=int)
        parser.add_argument('-p', '--exp_sec_level', action='store', choices=sec_levels, default='STD128')
        parser.add_argument('-f', '--exp_decryption_failure', action='store', default=-40, type=int)
        parser.add_argument('-I', '--num_of_inputs', action='store', choices=gate_inputs, default=2, type=int)
        parser.add_argument('-i', '--num_of_samples', action='store', default=200, type=int)
        parser.add_argument('-k', '--d_ks', action='store', choices=(2, 3, 4), default=3, type=int)
        parser.add_argument('-l', '--lower', action='store', default=2, type=int)
        parser.add_argument('-u', '--upper', action='store', default=4, type=int)
        parser.add_argument('-n', '--num_threads', action='store', default=1, type=int)
        parser.add_argument('-a', '--all', action='store_true')
        a = parser.parse_args()

        if a.all:
#            for sl, gi, bt in product(sec_levels, gate_inputs, boot_techs.keys()):
            for sl, gi, bt in product(sec_levels, gate_inputs, [a.bootstrapping_tech,]):
                print('_'.join((sl, str(gi), boot_techs[bt])), '##########################################################################################\n')

                secret_dist = 0 if (bt == 3) else 1
                parameter_selector(bt, secret_dist, sl, a.exp_decryption_failure, gi, a.num_of_samples, a.d_ks, a.lower, a.upper, a.num_threads)

                helperfncs.rm_out_files("out_file_")
                helperfncs.rm_out_files("noise_file_")

                print('_'.join((sl, str(gi), boot_techs[bt])), '##########################################################################################\n')
        else:
            parameter_selector(a.bootstrapping_tech, a.secret_dist, a.exp_sec_level, a.exp_decryption_failure, a.num_of_inputs, a.num_of_samples, a.d_ks, a.lower, a.upper, a.num_threads)

    helperfncs.rm_out_files("out_file_")
    helperfncs.rm_out_files("noise_file_")
