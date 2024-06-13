#!/usr/bin/python

from estimator import *
from math import log2, floor, sqrt, ceil, erfc
from scipy.special import erfcinv
from statistics import stdev

import io
import os
import paramstable as stdparams
import random
import sys

def restore_print():
    # restore stdout
    sys.stdout = sys.__stdout__
    #text_trap.getvalue()

def block_print():
    text_trap = io.StringIO()
    sys.stdout = text_trap

# find analytical estimate for starting point of modulus for the estimator
def get_mod(dim, exp_sec_level):
    # get linear relation coefficients for log(modulus) and dimension for the input security level
    a = stdparams.paramlinear[exp_sec_level][1]
    b = stdparams.paramlinear[exp_sec_level][2]

    modapp = a*dim + b
    mod = ceil(modapp)
    return mod

# calls lattice-estimator to get the work factor for known attacks
# TODO: add other secret distributions
def call_estimator(dim, mod, secret_dist="ternary", num_threads = 1, is_quantum = True):
    if secret_dist == "error":
        params = LWE.Parameters(n=dim, q=mod, Xs=ND.DiscreteGaussian(3.19), Xe=ND.DiscreteGaussian(3.19))
    elif secret_dist == "ternary":
        params = LWE.Parameters(n=dim, q=mod, Xs=ND.Uniform(-1, 1, dim), Xe=ND.DiscreteGaussian(3.19))
    else:
        print("Invalid distribution for secret")

    block_print()
    estimateval = LWE.estimate(params, red_cost_model=(RC.LaaMosPol14 if is_quantum else RC.BDGL16),
                               deny_list=["bkw", "bdd_hybrid", "bdd_mitm_hybrid", "dual_hybrid", "dual_mitm_hybrid", "arora-gb"], jobs=num_threads)
    restore_print()

    usvprop = floor(log2(estimateval['usvp']['rop']))
    dualrop = floor(log2(estimateval['dual']['rop']))
    decrop = floor(log2(estimateval['bdd']['rop']))

    return min(usvprop, dualrop, decrop)

# optimize dim, mod for an expected security level - this is specifically for the dimension n, and key switch modulus Qks in FHEW
# Increasing Qks helps reduce the bootstrapped noise
def optimize_params_security(expected_sec_level, dim, mod, secret_dist = "ternary", num_threads = 1, optimize_dim = False, optimize_mod = True, is_dim_pow2 = True, is_quantum = True):
    dim1 = dim
    dimlog = log2(dim)
    done = False

    while done is False:
        try:
            sec_level_from_estimator = call_estimator(dim, mod, secret_dist, num_threads, is_quantum)
            done = True
        except:
            mod = 2*mod
    done = False
    mod1 = mod

    modifieddim = False
    modifiedmod = False
    # loop to adjust modulus if given dim, modulus provide less security than target
    while (sec_level_from_estimator < expected_sec_level or done):
        if (optimize_dim and (not optimize_mod)):
            modifieddim = True
            dim1 = dim1 + 15
            if ((dim1 >= 2*dim) and (not is_dim_pow2)):
                done = True
            elif is_dim_pow2:
                dim1 = 2*dim
            sec_level_from_estimator = call_estimator(dim1, mod, secret_dist, num_threads, is_quantum)
        elif ((not optimize_dim) and optimize_mod):
            mod1 = mod1/2
            try:
                sec_level_from_estimator = call_estimator(dim, mod1, secret_dist, num_threads, is_quantum)
                modifiedmod = True
            except:
                return 0, 0

    # also need to check if starting from a lower than possible mod - when the above condition is satisfied but not optimal
    # loop to adjust modulus if given dim, modulus provide more security than target
    prev_sec_estimator = sec_level_from_estimator
    sec_level_from_estimator_new = sec_level_from_estimator
    prev_mod = mod1
    modifieddimmore = False
    modifiedmodmore = False
    while True:
        prev_sec_estimator = sec_level_from_estimator_new
        if (optimize_dim and (not optimize_mod)):
            modifieddimmore = True
            dim1 = dim1 - 15
            if ((dim1 <= 500) and (not is_dim_pow2)):
                done = True
            elif is_dim_pow2:
                dim1 = dim/2
            sec_level_from_estimator_new = call_estimator(dim1, mod, secret_dist, num_threads, is_quantum)
        elif ((not optimize_dim) and optimize_mod):
            mod1 = 2*mod1
            try:
                sec_level_from_estimator_new = call_estimator(dim, mod1, secret_dist, num_threads, is_quantum)
                modifiedmodmore = True
            except:
                return 0, 0
        if (((prev_sec_estimator >= expected_sec_level) and (sec_level_from_estimator_new < expected_sec_level)) or done):
            break

    if (modifiedmodmore and prev_sec_estimator >= expected_sec_level):
        mod1 = mod1/2

    if ((modifieddim or modifiedmod) and sec_level_from_estimator < expected_sec_level):
        dim1 = 0
        mod1 = 0
        print("cannot find optimal params")

    return dim1, mod1

def get_noise_from_cpp_code(param_set, num_of_samples, num_of_inputs, perfNumbers = False):
    filenamerandom = str(random.randrange(500))

    # TODO: change build folder based on word size
    bashCommand = ' '.join([ "scripts/run_script.sh",
                             str(param_set.n),
                             str(param_set.q),
                             str(param_set.N),
                             str(param_set.logQ),
                             str(param_set.Qks),
                             str(param_set.B_g),
                             str(param_set.B_ks),
                             str(param_set.B_rk),
                             str(param_set.sigma),
                             str(num_of_samples),
                             str(param_set.secret_dist),
                             str(param_set.bootstrapping_tech),
                             str(num_of_inputs),
                             "build",
                             "> out_file_" + filenamerandom,
                             "2> noise_file_" + filenamerandom
                          ])

    print(bashCommand)
    os.system(bashCommand)

    # parse noise values and compute stddev
    with open("noise_file_" + filenamerandom) as file:
        noise = [float(line.rstrip()) for line in file]

    if perfNumbers:
        return stdev(noise), get_performance("out_file_" + filenamerandom)
    else:
        return stdev(noise)

def get_performance(filename):
    # stdparams.performanceNumbers(bootstrapKeySize, keyswitchKeySize, ciphertextSize, bootstrapKeygenTime, evalbingateTime)
    perf = {}
    with open(filename) as file:
        for line in file:
            s1 = line.split(":")
            ### need python 3.10 or higher to use match instead of ifelse
            if (s1[0] == "BootstrappingKeySize"):
                perf.update({"BootstrappingKeySize": s1[1] + " bytes"})
            elif(s1[0] == "KeySwitchingKeySize"):
                perf.update({"KeySwitchingKeySize": s1[1] + " bytes"})
            elif(s1[0] == "CiphertextSize"):
                perf.update({"CiphertextSize": s1[1] + " bytes"})
            elif(s1[0] == "BootstrapKeyGenTime"):
                perf.update({"BootstrapKeyGenTime": s1[1]})
            elif(s1[0] == "EvalBinGateTime"):
                perf.update({"EvalBinGateTime": s1[1]})

    return perf

def get_decryption_failure(noise_stddev, ptmod, ctmod, comp):
    num = ctmod/(2*ptmod)
    denom = sqrt(2*comp)*noise_stddev
    val = erfc(num/denom)
    if (val == 0):
        retval = 0
    else:
    	retval = log2(val)
    return retval

def get_target_noise(decryption_failure, ptmod, ctmod, comp):
    num = ctmod/(2*ptmod)
    denom = sqrt(2*comp)

    val = erfcinv(2**decryption_failure)
    target_noise = num/(denom*val)
    return target_noise

def isPowerOfTwo(n):
    if (n == 0):
        return False
    return (ceil(log2(n)) == floor(log2(n)))

def get_dim_mod(paramset):
    dim = paramset.n
    mod = 2**(paramset.Qks)

    return dim, mod

def test_range(val, low, hi):
    if val in range(low, hi+1):
        return
    else:
        msg = f"input not in valid range ({low} - {hi})"
        raise Exception(msg)

def rm_out_files(prefix):
    try:
        for filename in os.listdir(os.getcwd()):
            if filename.startswith(prefix):
                file_path = os.path.join(os.getcwd(), filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
        print(prefix + " Cleanup completed.")
    except OSError as e:
        print(f"Error: {e}")
