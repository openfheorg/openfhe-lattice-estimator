#!/usr/bin/python

'''Approach for generating standard security tables for fhe
1) Pick security distribution
2) Pick security level
3) Set number of threads for the lattice-estimator
4) (optional) specific lattice dimension
'''

import paramstable as stdparams
import binfhe_params_helper as helperfncs
from math import log2, floor, sqrt, ceil

def parameter_selector():
    print("Generate standard parameter tables for different security levels")

    # NOTE: this numbering is local to this script and is NOT binfhe_params.py's
    # -d, where 0 = error and 1 = ternary. Here all three estimator distributions
    # are reachable, because the point is to tabulate them.
    secret_dist = int(input("Enter secret key distribution (0 = uniform, 1 = error, 2 = ternary): "))
    helperfncs.test_range(secret_dist, 0, 2)

    exp_sec_level = input("Enter Security level (STD128, STD128Q, STD192, STD192Q, STD256, STD256Q): ")

    ring_dim = int(input("Enter ring dimension: "))

    num_threads = int(input("Enter number of threads that can be used to run the lattice-estimator: "))

    #processing parameters based on the inputs
    if (exp_sec_level[-1] == "Q"):
        is_quantum = True
    else:
        is_quantum = False

    secret_dist_des = ""
    if secret_dist == 0:
        secret_dist_des = "uniform"
    elif secret_dist == 1:
        secret_dist_des = "error"
    elif secret_dist == 2:
        secret_dist_des = "ternary"

    # ring_dim 0 means "the whole table": every row is reported, where the loop
    # used to overwrite dim/mod each pass and print only the last one.
    dims = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536] if (ring_dim == 0) else [ring_dim]

    print("%-10s %14s %16s" % ("dimension", "modulus Q bits", "security model"))
    for i in dims:
        dim, mod = generate_dim_mod(exp_sec_level, i, secret_dist_des, num_threads, is_quantum)

        if ((dim == 0) or (mod == 0)):
            print("%-10s %14s   %s" % (i, "-",
                  "too small to price at this security level, raise the dimension"))
        else:
            print("%-10s %14.1f   %s" % (dim, log2(mod), helperfncs.security_model_description()))

def generate_dim_mod(exp_sec_level, ringdim, secret_dist, num_threads, is_quantum):
    logmod = helperfncs.get_mod(ringdim, exp_sec_level, secret_dist) #find analytical estimate for starting point of Qks

    #check security by running the estimator and adjust modulus if needed
    dim, mod = helperfncs.optimize_params_security(stdparams.security_bits[exp_sec_level], ringdim, 2**logmod, secret_dist, num_threads, is_quantum)

    return dim, mod

if __name__ == '__main__':
    parameter_selector()
