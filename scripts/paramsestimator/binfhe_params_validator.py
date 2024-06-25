#!/usr/bin/python

'''
usage (1): With -p ALL to calculate noise std deviation and probability of failure for named BINFHE_PARAMSET in OpenFHE.
    > python3 scripts/paramsestimator/binfhe_params_validator.py -p ALL

usage (2): With a specific p and any of {t, I, i} arguments.
    > python3 scripts/paramsestimator/binfhe_params_validator.py -p STD128_4_LMKCDEY -t 3 -I 4 -i 1000

usage (3): With no p argument and the output of the binfhe_params.py script.
    > python3 scripts/paramsestimator/binfhe_params_validator.py -n 518 -N 2048 -q 2048 -Q 54 -k 16384 -g 134217728 -r 32 -b 32 -s 3.19 -t 1 -d 1 -I 2 -i 200
'''

from math import log2, sqrt, erfc
from statistics import stdev

import argparse
import binfhe_params_helper as h
import os
import random
import sys

PARAM_SETS = [ "TOY", "MEDIUM", "STD128_AP",
               "STD128", "STD128_3", "STD128_4", "STD128Q", "STD128Q_3", "STD128Q_4",
               "STD192", "STD192_3", "STD192_4", "STD192Q", "STD192Q_3", "STD192Q_4",
               "STD256", "STD256_3", "STD256_4", "STD256Q", "STD256Q_3", "STD256Q_4",
               "STD128_LMKCDEY", "STD128_3_LMKCDEY", "STD128_4_LMKCDEY",
               "STD128Q_LMKCDEY", "STD128Q_3_LMKCDEY", "STD128Q_4_LMKCDEY",
               "STD192_LMKCDEY", "STD192_3_LMKCDEY", "STD192_4_LMKCDEY",
               "STD192Q_LMKCDEY", "STD192Q_3_LMKCDEY", "STD192Q_4_LMKCDEY",
               "STD256_LMKCDEY", "STD256_3_LMKCDEY", "STD256_4_LMKCDEY",
               "STD256Q_LMKCDEY", "STD256Q_3_LMKCDEY", "STD256Q_4_LMKCDEY",
               "LPF_STD128", "LPF_STD128Q", "LPF_STD128_LMKCDEY", "LPF_STD128Q_LMKCDEY",
               "SIGNED_MOD_TEST" ]
BOOT_TECHS = { 1 : "AP", 2 : "GINX", 3 : "LMKCDEY" }

def validator2(param_set, boot_tech, num_input, num_iters):
    filenamerandom = str(random.randrange(500))
    bashCommand = ' '.join([ "scripts/run_script2.sh", param_set, str(boot_tech), str(num_input), str(num_iters),
                             "build", "> out_file_" + filenamerandom, "2> noise_file_" + filenamerandom ])
    os.system(bashCommand)

    with open("noise_file_" + filenamerandom) as file:
        noise_stdev = stdev([float(line.rstrip()) for line in file])

    with open("out_file_" + filenamerandom) as file:
        ofdict = {d[0]: d[1] for d in [line.split(' ', 1) for line in file] if len(d) == 2}

    ctmodq = int(ofdict["ctmodq:"].strip().split(' ')[0])
    num = ctmodq/(4*num_input)
    denom = sqrt(2*num_input)*noise_stdev
    val = erfc(num/denom)
    failures = 0 if (val == 0) else log2(val)
    gtime = int(ofdict["EvalBinGateTime:"].strip().split(' ')[0])

    print((param_set, BOOT_TECHS[boot_tech], num_input, num_iters), (noise_stdev, failures, str(gtime) + 'ms'))

def validator(dim_n, mod_q, dim_N, mod_logQ, mod_Qks, B_g, B_ks, B_rk, sigma, num_iters, secret_dist, boot_tech, num_input):
    filenamerandom = str(random.randrange(500))
    bashCommand = ' '.join([ "scripts/run_script.sh", str(dim_n), str(mod_q), str(dim_N), str(mod_logQ), str(mod_Qks), str(B_g),
                             str(B_ks), str(B_rk), str(sigma), str(num_iters), str(secret_dist), str(boot_tech), str(num_input),
                             "build", "> out_file_" + filenamerandom, "2> noise_file_" + filenamerandom ])
    os.system(bashCommand)

    with open("noise_file_" + filenamerandom) as file:
        noise_stdev = stdev([float(line.rstrip()) for line in file])

    with open("out_file_" + filenamerandom) as file:
        ofdict = {d[0]: d[1] for d in [line.split(' ', 1) for line in file] if len(d) == 2}

    ctmodq = int(ofdict["ctmodq:"].strip().split(' ')[0])
    num = ctmodq/(4*num_input)
    denom = sqrt(2*num_input)*noise_stdev
    val = erfc(num/denom)
    failures = 0 if (val == 0) else log2(val)
    gtime = int(ofdict["EvalBinGateTime:"].strip().split(' ')[0])

    print(bashCommand, (noise_stdev, failures, str(gtime) + 'ms'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='binfhe_params_validator',
                 description='calculates noise std deviation and probability of failure for input parameter set.')

    parser.add_argument('-p', '--param_set', action='store', choices=PARAM_SETS + ["ALL"], default=None)
    parser.add_argument('-t', '--boot_tech', action='store', choices=(1, 2, 3), default=2, type=int)
    parser.add_argument('-I', '--num_input', action='store', choices=(2, 3, 4), default=2, type=int)
    parser.add_argument('-i', '--num_iters', action='store', default=500, type=int)
    parser.add_argument('-n', '--dim_n', action='store', type=int)
    parser.add_argument('-N', '--dim_N', action='store', type=int)
    parser.add_argument('-q', '--mod_q', action='store', type=int)
    parser.add_argument('-Q', '--mod_logQ', action='store', type=int)
    parser.add_argument('-k', '--mod_Qks', action='store', type=int)
    parser.add_argument('-g', '--B_g', action='store', type=int)
    parser.add_argument('-b', '--B_ks', action='store', type=int)
    parser.add_argument('-r', '--B_rk', action='store', default=64, type=int)
    parser.add_argument('-s', '--sigma', action='store', default=3.19, type=float)
    parser.add_argument('-d', '--secret_dist', choices=(0, 1), action='store', default=1, type=int)

    a = parser.parse_args()

    if (a.param_set):
        print(("PARAM_SET", "BOOT_TECH", "NUM_INPUTS", "NUM_ITERS"), ("noise_stdev", "failure_rate", "EvalBinGateTime"))
        if (a.param_set in PARAM_SETS):
            validator2(a.param_set, a.boot_tech, a.num_input, a.num_iters)
        elif (a.param_set == "ALL"):
            for param_set in PARAM_SETS:
                p = param_set.split('_')
                boot_tech = 1 if (p[-1] == "AP") else 3 if (p[-1] == "LMKCDEY") else 2
                num_input = a.num_input
                if (len(p) >= 2) and (p[1] in ('3', '4')):
                    num_input = int(p[1])
                validator2(param_set, boot_tech, num_input, a.num_iters)
        else:
            print("Invalid Args")
            print(sys.argv)
    else:
        validator(a.dim_n, a.mod_q, a.dim_N, a.mod_logQ, a.mod_Qks, a.B_g, a.B_ks, a.B_rk, a.sigma, a.num_iters, a.secret_dist, a.boot_tech, a.num_input)

    h.rm_out_files("out_file_")
    h.rm_out_files("noise_file_")
