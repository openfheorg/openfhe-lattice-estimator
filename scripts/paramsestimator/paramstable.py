# # given a distribution type and a security level, you can get the maxQ for a
# given ring dimension, and you can get the ring dimension given a maxQ

# The code below is very specific to the layout of the DistributionType and
# SecurityLevel enums IF you change them, go look at and change byRing and
# byLogQ
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

#linear relation of log(modulus) and dimension as [a,b] for each standard security level - log(modulus) = a*dimension + b
paramlinear = {
    'STD128': [128, 0.026243550051145488, -0.19332645282074845],
    'STD128Q': [128, 0.024334365322949414, 0.026487788095649],
    'STD192': [192, 0.01843137255110034, -0.6666666695778614],
    'STD192Q': [192, 0.017254901960954656, -0.9019607843827292],
    'STD256': [256, 0.014352941174320843, -1.0014705882400903],
    'STD256Q': [256, 0.01339285714070515, -1.083333333337455]
}