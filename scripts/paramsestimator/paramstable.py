# Security tables and helpers, transcribed from OpenFHE so the two agree.
#
# Source: StandardLatticeParmSets in
#   openfhe-development/src/core/lib/lattice/stdlatticeparms.cpp
# which encodes the HomomorphicEncryption.org security standard tables.
# "Q" levels are the quantum rows, the others classical.
#
# These are only a STARTING POINT for the search: optimize_params_security
# prices every candidate with the lattice-estimator and corrects it. Note the
# estimator does not always agree with these rows -- at STD192 it certifies 188
# bits for the table's own n=1024/logq=19 entry -- which is why nothing here is
# trusted without an estimator run.

security_bits = {
    "STD128": 128, "STD128Q": 128,
    "STD192": 192, "STD192Q": 192,
    "STD256": 256, "STD256Q": 256,
}

# (secret distribution, level) -> {dimension: max log2(q)}
openfhe_std_tables = {
    ("uniform", "STD128"): {1024: 29, 2048: 56, 4096: 111, 8192: 220, 16384: 440, 32768: 880},
    ("uniform", "STD128Q"): {1024: 27, 2048: 53, 4096: 103, 8192: 206, 16384: 413, 32768: 829},
    ("uniform", "STD192"): {1024: 21, 2048: 39, 4096: 77, 8192: 154, 16384: 307, 32768: 612},
    ("uniform", "STD192Q"): {1024: 19, 2048: 37, 4096: 72, 8192: 143, 16384: 286, 32768: 573},
    ("uniform", "STD256"): {1024: 16, 2048: 31, 4096: 60, 8192: 120, 16384: 239, 32768: 478},
    ("uniform", "STD256Q"): {1024: 15, 2048: 29, 4096: 56, 8192: 111, 16384: 222, 32768: 445},
    ("error", "STD128"): {1024: 29, 2048: 56, 4096: 111, 8192: 220, 16384: 440, 32768: 883, 65536: 1749, 131072: 3525},
    ("error", "STD128Q"): {1024: 27, 2048: 53, 4096: 103, 8192: 206, 16384: 413, 32768: 829, 65536: 1665, 131072: 3351},
    ("error", "STD192"): {1024: 21, 2048: 39, 4096: 77, 8192: 154, 16384: 307, 32768: 613, 65536: 1201, 131072: 2413},
    ("error", "STD192Q"): {1024: 19, 2048: 37, 4096: 72, 8192: 143, 16384: 286, 32768: 573, 65536: 1147, 131072: 2304},
    ("error", "STD256"): {1024: 16, 2048: 31, 4096: 60, 8192: 120, 16384: 239, 32768: 478, 65536: 931, 131072: 1868},
    ("error", "STD256Q"): {1024: 15, 2048: 29, 4096: 56, 8192: 111, 16384: 222, 32768: 445, 65536: 890, 131072: 1786},
    ("ternary", "STD128"): {1024: 27, 2048: 54, 4096: 109, 8192: 218, 16384: 438, 32768: 881, 65536: 1747, 131072: 3523},
    ("ternary", "STD128Q"): {1024: 25, 2048: 51, 4096: 101, 8192: 202, 16384: 411, 32768: 827, 65536: 1663, 131072: 3348},
    ("ternary", "STD192"): {1024: 19, 2048: 37, 4096: 75, 8192: 152, 16384: 305, 32768: 611, 65536: 1199, 131072: 2411},
    ("ternary", "STD192Q"): {1024: 17, 2048: 35, 4096: 70, 8192: 141, 16384: 284, 32768: 571, 65536: 1145, 131072: 2301},
    ("ternary", "STD256"): {1024: 14, 2048: 29, 4096: 58, 8192: 118, 16384: 237, 32768: 476, 65536: 929, 131072: 1866},
    ("ternary", "STD256Q"): {1024: 13, 2048: 27, 4096: 54, 8192: 109, 16384: 220, 32768: 443, 65536: 888, 131072: 1784},
}

def max_logq(dim, exp_sec_level, secret_dist = "ternary"):
    """Largest log2(q) considered secure at this dimension.

    Linear interpolation between the table rows, exact at every dimension
    OpenFHE tabulates. Below the first row the segment is anchored at the
    origin, since a secure modulus has to shrink with the dimension -- the
    search spends most of its time there, at LWE dimensions of a few hundred.
    """
    table = openfhe_std_tables[(secret_dist, exp_sec_level)]
    dims  = sorted(table)

    if dim <= dims[0]:
        return table[dims[0]] * float(dim) / dims[0]

    for lo, hi in zip(dims, dims[1:]):
        if dim <= hi:
            return table[lo] + (table[hi] - table[lo]) * float(dim - lo) / (hi - lo)

    # past the last row: extrapolate along the final segment
    lo, hi = dims[-2], dims[-1]
    return table[hi] + (table[hi] - table[lo]) * float(dim - hi) / (hi - lo)

class paramsetvars:
    def __init__(self, n, q, N, logQ, Qks, B_g, B_ks, B_rk, sigma, secret_dist, bootstrapping_tech):
        self.n = n #n
        self.q = q #mod_q
        self.logQ = logQ  #mod_Q numberBits
        self.N = N  # cyclOrder/2
        self.Qks = Qks #Qks modKS
        self.B_g = B_g #gadgetBase
        self.B_ks = B_ks #baseKS
        self.B_rk = B_rk #baseRK
        self.sigma = sigma #sigma stddev
        self.secret_dist = secret_dist #secret distribution used
        self.bootstrapping_tech = bootstrapping_tech #bootstrapping technique used
