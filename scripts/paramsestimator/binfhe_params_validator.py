#!/usr/bin/python

'''
Run with `sage -python`, not `python3`: this imports binfhe_params_helper, which
imports the lattice-estimator and so needs SageMath. (Plain python3 works only
where sagelib is installed into the system python, as `apt install sagemath` does.)

usage (1): With -p ALL to calculate noise std deviation and probability of failure for named BINFHE_PARAMSET in OpenFHE.
    > sage -python scripts/paramsestimator/binfhe_params_validator.py -p ALL

usage (2): With a specific p and any of {t, I, i} arguments.
    > sage -python scripts/paramsestimator/binfhe_params_validator.py -p STD128_4_LMKCDEY -t 3 -I 4 -i 1000

usage (3): With no p argument and the output of the binfhe_params.py script.
    > sage -python scripts/paramsestimator/binfhe_params_validator.py -n 518 -N 2048 -q 2048 -Q 54 -k 16384 -g 134217728 -r 32 -b 32 -s 3.19 -t 1 -d 1 -I 2 -i 200
'''

from statistics import fmean, stdev

import argparse
import binfhe_params_helper as h
import sys

PARAM_SETS = [ "TOY", "TOY_MULTI_BASE", "MEDIUM", "STD128_AP",
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

def summarize(perf, noise, num_input, gate_us):
    """(noise stddev, noise mean, log2 failure probability, gate ms).

    The mean is here because sigma alone cannot show a per-key noise bias: it is
    taken about the sample mean. With several keys pooled, a mean well away from
    zero is the tell.
    """
    noise_stdev = stdev(noise)
    ctmodq      = int(perf["ctmodq"].strip().split(' ')[0])

    # same maths as the search itself, kept in one place: ptmod = 2*num_input
    failures = h.get_decryption_failure(noise_stdev, 2*num_input, ctmodq, num_input)

    return noise_stdev, fmean(noise), failures, "%.1fms" % (gate_us/1000.0)

def run(opts, num_input, num_keys):
    """Speed on the whole machine, noise across single-threaded processes."""
    gate_us, perf, binary = h.measure_speed(opts)
    _out, noise = h.measure_noise(opts, num_keys, per_proc_bytes=h._key_bytes(perf), binary=binary)
    return summarize(perf, noise, num_input, gate_us)

def validator2(param_set, boot_tech, num_input, num_iters, num_keys = h.DEFAULT_NUM_KEYS):
    opts = [("-p", param_set), ("-t", boot_tech), ("-I", num_input), ("-i", num_iters)]
    print((param_set, BOOT_TECHS[boot_tech], num_input, num_iters, num_keys), run(opts, num_input, num_keys))

def validator(dim_n, mod_q, dim_N, mod_logQ, mod_Qks, B_g, B_ks, B_rk, sigma, num_iters, secret_dist, boot_tech, num_input, num_keys = h.DEFAULT_NUM_KEYS):
    opts = [("-n", dim_n), ("-q", mod_q), ("-N", dim_N), ("-Q", mod_logQ), ("-k", mod_Qks),
            ("-g", B_g), ("-b", B_ks), ("-r", B_rk), ("-s", sigma), ("-i", num_iters),
            ("-d", secret_dist), ("-t", boot_tech), ("-I", num_input)]
    print(opts, run(opts, num_input, num_keys))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='binfhe_params_validator',
                 description='noise std deviation, mean and probability of failure for a parameter set. Noise is pooled over -K independent keys measured in parallel single-threaded processes; the gate time is measured separately with all cores.')

    parser.add_argument('-p', '--param_set', action='store', choices=PARAM_SETS + ["ALL"], default=None)
    parser.add_argument('-t', '--boot_tech', action='store', choices=(1, 2, 3), default=2, type=int)
    parser.add_argument('-I', '--num_input', action='store', choices=(2, 3, 4), default=2, type=int)
    parser.add_argument('-i', '--num_iters', action='store', default=500, type=int,
                        help='noise samples PER KEY; total = num_iters * num_keys')
    parser.add_argument('-K', '--num_keys', action='store', default=h.DEFAULT_NUM_KEYS, type=int,
                        help='independent keys to pool noise over, measured in parallel (default 8)')
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

    REQUIRED_WITHOUT_P = (("-n", "dim_n"), ("-N", "dim_N"), ("-q", "mod_q"), ("-Q", "mod_logQ"),
                          ("-k", "mod_Qks"), ("-g", "B_g"), ("-b", "B_ks"))

    if (not a.param_set):
        missing = [flag for flag, name in REQUIRED_WITHOUT_P if getattr(a, name) is None]
        if missing:
            parser.error("without -p, these are required: %s" % ' '.join(missing))

    if (a.param_set):
        print(("PARAM_SET", "BOOT_TECH", "NUM_INPUTS", "NUM_ITERS"), ("noise_stdev", "noise_mean", "failure_rate", "EvalBinGateTime"))
        if (a.param_set in PARAM_SETS):
            validator2(a.param_set, a.boot_tech, a.num_input, a.num_iters, a.num_keys)
        elif (a.param_set == "ALL"):
            for param_set in PARAM_SETS:
                p = param_set.split('_')
                boot_tech = 1 if (p[-1] == "AP") else 3 if (p[-1] == "LMKCDEY") else 2
                num_input = a.num_input
                if (len(p) >= 2) and (p[1] in ('3', '4')):
                    num_input = int(p[1])
                validator2(param_set, boot_tech, num_input, a.num_iters, a.num_keys)
        else:
            print("Invalid Args")
            print(sys.argv)
    else:
        validator(a.dim_n, a.mod_q, a.dim_N, a.mod_logQ, a.mod_Qks, a.B_g, a.B_ks, a.B_rk, a.sigma, a.num_iters, a.secret_dist, a.boot_tech, a.num_input, a.num_keys)
