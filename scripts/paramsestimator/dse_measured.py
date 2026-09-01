#!/usr/bin/python

'''Measured data the DSE model is calibrated and validated against.

Checked in because regenerating it costs about two hours of machine time and the
container logs it came from do not survive. Every number here was measured on the
pinned build (openfhe-development @ 252b2b1f) on llserver, 8 shared cores, with
nothing else running for the timing rows.

Reproduce with:
  scripts/paramsestimator/dse_calibrate.py plan --emit-commands   (ladder)
  the -p <SET> path of boolean_noise_estimate_script               (shipped sets)
'''

# --------------------------------------------------------------------------
# Shipped-set noise, -i 400 -K 6, 2-input OR. Pooled sigma over 6 keys.
# Taken on the table OpenFHE shipped BEFORE this tool re-selected it, so the
# log2Pf in the enum comments beside those rows predates multi-key measurement
# and is not a target. (At the current pin the comments on the 105 re-selected
# rows are this tool's own certifications instead, but these measurements are
# not of those rows.) Recorded here for contrast only.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# THE KNOWN-ANSWER CONTROL, self-contained. Each entry carries the parameters the
# measurement was taken on, so the control depends on no copy of any library
# file: `dse_shipfit.control()` predicts sigma from these parameters and checks
# it against the measurement, and -- for names that exist in the LIVE table --
# also checks that the parser reads the same parameters (the cyclOrder misread
# that doubled every N is what that half is for). A set whose parameters the
# library changes again shows up as LIVE TABLE DIFFERS and the entry must be
# re-baselined, never silently re-pointed.
#
# Measured at 12858277, 2026-09-15, plans/control-rebaseline.cmds: -p <SET>, -i 2400,
# -K 6, two arms per set (64-bit and 32-bit key forms, which agreed within
# |z| <= 2.1 including per-key scatter), sigma pooled over the two arms. These
# four are certified rows of the 108-row table AS THE LIBRARY SHIPS THEM; three
# of them moved one gadget-map position when that table replaced the f3944448
# rows, which is what the previous baseline was measured on and why it was
# re-measured rather than re-pointed (scripts/dse-tools/control_rebaseline.py).
# --------------------------------------------------------------------------
CONTROL_PIN = "12858277"
CONTROL_MEASURED = "2026-09-15"
CONTROL = {
    "STD128": dict(method="GINX", sigma=18.4051, samples=28800,
        params=dict(N=1024, n=554, q=2048, logQ=27, qks=32768, bks=256, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={128: 303, 512: 251})),
    "STD128_LMKCDEY": dict(method="LMKCDEY", sigma=18.3310, samples=28800,
        params=dict(N=1024, n=554, q=2048, logQ=27, qks=32768, bks=256, brk=64, autokeys=40,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={512: 552, 16384: 2})),
    "STD128_AP": dict(method="AP", sigma=18.5418, samples=28800,
        params=dict(N=1024, n=554, q=2048, logQ=27, qks=32768, bks=256, brk=128, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={128: 10, 512: 544})),
    "STD256Q": dict(method="GINX", sigma=37.1257, samples=28800,
        params=dict(N=2048, n=1169, q=4096, logQ=26, qks=32768, bks=256, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={32: 173, 64: 996})),
}

# The five sets the control used before the library's table became this tool's
# output (2026-09-01, -i 400 -K 6, geometry of 238153db, inline). HISTORICAL: the
# library no longer ships these parameters under these names, so there is no
# parser half to check; they remain known answers for the model, which noise has
# reproduced across every pin move so far.
CONTROL_HISTORICAL_PIN = "238153db"
CONTROL_HISTORICAL = {
    "LPF_STD128": dict(method="GINX", sigma=14.0833, samples=2400,
        params=dict(N=1024, n=556, q=2048, logQ=27, qks=32768, bks=32, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={128: 547, 512: 9})),
    "STD128": dict(method="GINX", sigma=13.9369, samples=2400,
        params=dict(N=1024, n=556, q=2048, logQ=27, qks=32768, bks=32, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={128: 556})),
    "STD128Q": dict(method="GINX", sigma=13.2297, samples=2400,
        params=dict(N=1024, n=601, q=2048, logQ=25, qks=32768, bks=32, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={16: 601})),
    "STD192": dict(method="GINX", sigma=16.7278, samples=2400,
        params=dict(N=2048, n=821, q=2048, logQ=37, qks=32768, bks=32, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={8192: 821})),
    "STD256": dict(method="GINX", sigma=21.4559, samples=2400,
        params=dict(N=2048, n=1299, q=2048, logQ=29, qks=262144, bks=64, brk=64, autokeys=10,
                    keydist='UNIFORM_TERNARY', sigma=3.19, gmap={1024: 1299})),
}

SHIPPED_SIGMA = {
    # set:            (measured sigma, log2Pf from it, label in OpenFHE)
    "STD128":         (13.9369, -125.7, -135),
    "STD128Q":        (13.2297, -139.2, -145),
    "STD192":         (16.7278,  -88.2,  -80),   # model REFUSES this one
    "STD256":         (21.4559,  -54.8,  -55),   # envelope hole: model said 7.97
    "LPF_STD128":     (14.0833, -123.2, -128),
}

# --------------------------------------------------------------------------
# Shipped-set cost, -i 50 -K 1, machine idle. gate_us is gate-only. HISTORICAL:
# "build32" rows were timed on the genuine NATIVE_SIZE=32 build, which the image
# no longer carries (the 64-bit build narrows its keys itself since 9e8045db and
# is faster past six digits); "build" rows were 64-bit with 64-bit key forms.
# set: (build, gate_us, keygen_ms, btkey_bytes, ksk_bytes)
# --------------------------------------------------------------------------
SHIPPED_COST = {
    "TOY":                 ("build32",   1724.02,   32,    2133085,   21102629),
    "MEDIUM":              ("build32",  18995.2,   506,   27892605,  448806949),
    "STD128_AP":           ("build32",  36018.3,  6443, 2327998053,  221921317),
    "STD128":              ("build32",  30793.9,   381,   55115261,  220741669),
    "STD128_3":            ("build32",  32895.8,   640,   58981253,  472137765),
    "STD128_4":            ("build32",  49941.5,   793,  104897013,  503595045),
    "STD128Q":             ("build32",  52637.3,   547,  119132717,  238436389),
    "STD192":              ("build",   196380.0,   984,  215732637, 1297121317),
    "STD192Q":             ("build",   211872.0,  1064,  233863629, 1405648933),
    "STD256":              ("build",   309440.0,  2431,  341335741, 4097867813),
    "STD256Q":             ("build32", 153147.0,  2768,  326934237, 1961918501),
    "STD128_LMKCDEY":      ("build32",  31759.1,   288,   19385113,  230572069),
    "STD128Q_LMKCDEY":     ("build32",  39183.0,   349,   31996259,  253771813),
    "STD192_LMKCDEY":      ("build",   140608.0,   655,   47405567, 1131970597),
    "STD256_LMKCDEY":      ("build",   240821.0,  1023,  142490417, 1702920229),
    "LPF_STD128":          ("build32",  30679.4,   374,   54817973,  220741669),
    "LPF_STD128Q":         ("build32",  52322.7,   566,  117811437,  238436389),
    "LPF_STD128_LMKCDEY":  ("build32",  37738.6,   300,   25123923,  220741669),
    "LPF_STD128Q_LMKCDEY": ("build32",  56896.8,   403,   58990881,  238436389),
    "TOY_MULTI_BASE":      ("build32",   2137.74,   31,    2665821,   21102629),
}

# --------------------------------------------------------------------------
# Calibration ladder. N=1024, q=2048, logQ=27, q_KS=2^27, base_KS=32, GINX,
# ternary. Only the gadget split moves, so keyswitch and rounding are identical
# in every row of a rung and cancel in differences.
#
# rung -> [(fraction at the FINE base, n, gadget map, measured sigma)]
# --------------------------------------------------------------------------
LADDER = {
    "2^5/2^7": [(0.00, 1024, {128: 1024},            10.761556),
                (0.25, 1024, {32: 256, 128: 768},    10.018441),
                (0.50, 1024, {32: 512, 128: 512},     9.606710),
                (0.75, 1024, {32: 768, 128: 256},     8.631233),
                (1.00, 1024, {32: 1024},              8.221185)],
    "2^7/2^9": [(0.00, 1024, {512: 1024},            31.610499),
                (0.25, 1024, {128: 256, 512: 768},   27.008332),
                (0.50, 1024, {128: 512, 512: 512},   23.054416),
                (0.75, 1024, {128: 768, 512: 256},   18.485163),
                (1.00, 1024, {128: 1024},            10.996272)],
    # rungs 3 and 4 SATURATED at every n tried (256 and 16): sigma pinned at the
    # 147.8 ceiling = q/(p*sqrt(12)), independent of n, which is the proof it is a
    # ceiling. Kept as evidence of the failure mode, not as fittable data.
    "2^9/2^11 SATURATED":  [(f, 256, None, s) for f, s in
                            ((0.00, 146.980), (0.25, 146.601), (0.50, 138.615),
                             (0.75, 122.590), (1.00, 15.309))],
    "2^11/2^13 SATURATED": [(f, 16, None, s) for f, s in
                            ((0.00, 146.813), (0.25, 148.522), (0.50, 147.964),
                             (0.75, 147.735), (1.00, 67.495))],
}

# Per-coefficient contributions backed out of the two valid rungs, with the
# constant HELD at the derived rounding value (it cannot be co-fitted: counts sum
# to n identically, so the design matrix is rank 2).
#   base 2^5 -> 0.00873    base 2^7 -> 0.05651 / 0.05760    base 2^9 -> 0.89524
# Base 2^7 measured in two independent rungs agreed to 1.9% -- that is the
# validation of the per-coefficient formulation, independent of any constant.
LADDER_IMPLIED_K = {32: 7322, 128: 4985, 512: 7334, 2048: 145770}



# ==========================================================================
# 2026-08-31 overnight run. Nine experiments, pinned build 252b2b1f, llserver.
#
# Raw logs and the driver scripts that produced them are kept at
#   llserver:~/lattice-estimator-docker/results-2026-08-31/
# and everything below was extracted from them programmatically -- never
# transcribed, after a hand-entered gadgetBase once produced two phantom model
# failures of +143% and +99%.
#
# CAVEAT on ACC_LMKCDEY: the runner does not capture -a (numAutoKeys), so the
# rows there are distinguished only by ORDER, matching lmkcdey.cmds:
#   rows 1-4  top-digit scan, logQ 24..27, a=10
#   rows 5-7  numAutoKeys scan, logQ=25, a = 2, 5, 20
#   rows 8-9  n scan, logQ=25, n = 64, 128, a=10
#   row  10   GAUSSIAN secret, logQ=25, n=32, a=10
# Recover them from that file, not from the log alone.
#
# Full write-up: Addendum 4 of
#   ~/repos/openfhe-development/.claude/notes/dse-plan-review.md
# ==========================================================================

# --------------------------------------------------------------------------
# Stage 4: the post-accumulator chain, measured in isolation against an
# exactly noiseless input. No bootstrapping key, and no free constant in any
# of the three predictions, so these are tests rather than calibrations.
#
# A LIST, not a dict: STD128 appears twice under ksonly, at K=32 and K=200,
# and a dict would silently keep only one of them.
# (set, keys, n, N, q, qKS, baseKS, keyDist, samples, pooled_sd, within_sd, between_sd)
# --------------------------------------------------------------------------
STAGE4_ROUND2 = [
    ("MEDIUM",                16,   422,  1024,  1024,    16384,  128, "UNIFORM_TERNARY",  800000,    4.805759,    4.776365,   0.548530),
    ("STD128",                16,   556,  1024,  2048,    32768,   32, "UNIFORM_TERNARY",  800000,    5.581381,    5.536804,   0.727504),
    ("STD128_LMKCDEY",        16,   581,  1024,  1024,    32768,   32, "UNIFORM_TERNARY",  800000,    5.668719,    5.653471,   0.429871),
    ("STD128_3",              16,   595,  1024,  2048,    65536,   64, "UNIFORM_TERNARY",  800000,    5.767872,    5.760052,   0.311164),
    ("STD128_4",              16,   635,  1024,  2048,   131072,   64, "UNIFORM_TERNARY",  800000,    5.970950,    5.968584,   0.175623),
    ("STD192_LMKCDEY",        16,   716,  2048,  4096,    32768,   32, "GAUSSIAN",         800000,   24.817825,   24.471170,   4.270513),
    ("STD192Q_LMKCDEY",       16,   778,  2048,  4096,    32768,   32, "GAUSSIAN",         800000,   26.167669,   25.372176,   6.614054),
    ("STD192",                16,   821,  2048,  2048,    32768,   32, "UNIFORM_TERNARY",  800000,    6.809529,    6.779618,   0.659162),
    ("STD256_LMKCDEY",        16,  1079,  2048,  2048,    32768,   32, "UNIFORM_TERNARY",  800000,    7.793692,    7.749336,   0.858250),
    ("STD256_3",              16,  1241,  2048,  2048,   131072,   64, "UNIFORM_TERNARY",  800000,    8.302562,    8.299742,   0.226536),
    ("STD256Q",               16,  1242,  2048,  2048,    65536,   64, "UNIFORM_TERNARY",  800000,    8.335163,    8.320030,   0.519844),
    ("STD256",                16,  1299,  2048,  2048,   262144,   64, "UNIFORM_TERNARY",  800000,    8.476684,    8.476010,   0.116707),
]

STAGE4_ROUND1 = [
    ("MEDIUM",                16,   422,  1024,  1024,    16384,  128, "UNIFORM_TERNARY",  800000,    7.568332,    7.568317,   0.037373),
    ("STD128",                16,   556,  1024,  2048,    32768,   32, "UNIFORM_TERNARY",  800000,    7.559533,    7.559515,   0.037869),
    ("STD128_LMKCDEY",        16,   581,  1024,  1024,    32768,   32, "UNIFORM_TERNARY",  800000,    7.569071,    7.569031,   0.042398),
    ("STD128_3",              16,   595,  1024,  2048,    65536,   64, "UNIFORM_TERNARY",  800000,    7.534951,    7.534946,   0.034999),
    ("STD128_4",              16,   635,  1024,  2048,   131072,   64, "UNIFORM_TERNARY",  800000,    7.563755,    7.563742,   0.036910),
    ("STD192_LMKCDEY",        16,   716,  2048,  4096,    32768,   32, "GAUSSIAN",         800000,   41.891293,   41.891348,   0.173532),
    ("STD192Q_LMKCDEY",       16,   778,  2048,  4096,    32768,   32, "GAUSSIAN",         800000,   41.462409,   41.462416,   0.183932),
    ("STD192",                16,   821,  2048,  2048,    32768,   32, "UNIFORM_TERNARY",  800000,   10.662318,   10.662342,   0.041399),
    ("STD256_LMKCDEY",        16,  1079,  2048,  2048,    32768,   32, "UNIFORM_TERNARY",  800000,   10.676518,   10.676556,   0.037794),
    ("STD256_3",              16,  1241,  2048,  2048,   131072,   64, "UNIFORM_TERNARY",  800000,   10.693352,   10.693380,   0.040762),
    ("STD256Q",               16,  1242,  2048,  2048,    65536,   64, "UNIFORM_TERNARY",  800000,   10.695956,   10.695937,   0.052159),
    ("STD256",                16,  1299,  2048,  2048,   262144,   64, "UNIFORM_TERNARY",  800000,   10.624521,   10.624570,   0.034110),
]

STAGE4_KSONLY = [
    ("MEDIUM",                32,   422,  1024,  1024,    16384,  128, "UNIFORM_TERNARY",   96000,  144.902400,  144.265401,  14.038414),
    ("STD128",                32,   556,  1024,  2048,    32768,   32, "UNIFORM_TERNARY",   96000,  177.980901,  174.188358,  37.266618),
    ("STD128",               200,   556,  1024,  2048,    32768,   32, "UNIFORM_TERNARY",   60000,  176.528008,  173.704588,  33.081474),
    ("STD128_3",              32,   595,  1024,  2048,    65536,   64, "UNIFORM_TERNARY",   96000,  176.885830,  173.916539,  32.943988),
    ("STD128_3",             200,   595,  1024,  2048,    65536,   64, "UNIFORM_TERNARY",   60000,  176.892725,  173.991214,  33.527632),
    ("STD128_4",              32,   635,  1024,  2048,   131072,   64, "UNIFORM_TERNARY",   96000,  176.805419,  175.452334,  22.411296),
    ("STD192_LMKCDEY",        32,   716,  2048,  4096,    32768,   32, "GAUSSIAN",          96000,  251.496101,  246.108392,  52.797646),
    ("STD256",                32,  1299,  2048,  2048,   262144,   64, "UNIFORM_TERNARY",   96000,  249.883275,  247.730318,  33.561064),
]

STAGE4_TAIL = [
    ("MEDIUM",                32,   422,  1024,  1024,    16384,  128, "UNIFORM_TERNARY",   96000,   10.261335,   10.209240,   1.065627),
    ("STD128",                32,   556,  1024,  2048,    32768,   32, "UNIFORM_TERNARY",   96000,   12.343972,   12.199060,   1.928956),
    ("STD128_3",              32,   595,  1024,  2048,    65536,   64, "UNIFORM_TERNARY",   96000,    8.005656,    7.943228,   1.024103),
    ("STD128_4",              32,   635,  1024,  2048,   131072,   64, "UNIFORM_TERNARY",   96000,    6.588732,    6.580476,   0.355908),
]

# --------------------------------------------------------------------------
# Is accumulator variance linear in n? Everything fixed but n, at logQ=25 with
# base 2^7, where sigma stays at 0.20 of the wrap ceiling even at n=1024, so
# saturation cannot explain a deviation. sigma^2/n came out
# 0.9153 0.9199 0.9110 0.9248 0.8782 0.8986 0.9419 0.9509 -- mean 0.9176, spread
# +-2.6% against a 2.5% sampling error, and the low-n and high-n halves gave
# 0.9178 and 0.9174. Linear, with no trend.
# (logQ, N, n, q, qKS, gadget_map, samples, sigma)
# --------------------------------------------------------------------------
ACC_LINEARITY_N = [
    (25,  1024,    64,  2048,   33554432, {128: 64},                 3200,    7.653700),
    (25,  1024,   128,  2048,   33554432, {128: 128},                3200,   10.850968),
    (25,  1024,   192,  2048,   33554432, {128: 192},                3200,   13.225270),
    (25,  1024,   256,  2048,   33554432, {128: 256},                3200,   15.386521),
    (25,  1024,   384,  2048,   33554432, {128: 384},                3200,   18.363885),
    (25,  1024,   512,  2048,   33554432, {128: 512},                3200,   21.449990),
    (25,  1024,   768,  2048,   33554432, {128: 768},                3200,   26.898406),
    (25,  1024,  1024,  2048,   33554432, {128: 1024},               3200,   31.204782),
]

# --------------------------------------------------------------------------
# Does the accumulator scale with N? accumulator_var_at_Q() had no N in it and
# the ladder that calibrated it held N=1024 throughout, so the dependence was
# untested rather than small. Backing out var_acc: base 2^7 gave 27.58 / 57.86 /
# 108.08 at N = 512 / 1024 / 2048, and base 2^9 gave 57.74 / 112.57 at N = 512 /
# 1024. That is N^0.985 -- linear to 1.5%. N is a primary search dimension, so
# without it the term is wrong by the N ratio.
# (logQ, N, n, q, qKS, gadget_map, samples, sigma)
# --------------------------------------------------------------------------
ACC_N_DEPENDENCE = [
    (25,   512,   256,  1024,   33554432, {128: 256},                4000,    6.465646),
    (25,  1024,   256,  1024,   33554432, {128: 256},                4000,    8.489865),
    (25,  2048,   256,  1024,   33554432, {128: 256},                4000,   11.058945),
    (25,   512,    64,  1024,   33554432, {512: 64},                 4000,    7.829257),
    (25,  1024,    64,  1024,   33554432, {512: 64},                 4000,   10.776237),
    (25,  2048,    64,  1024,   33554432, {512: 64},                 4000,   15.282214),
]

# --------------------------------------------------------------------------
# The decisive shape test: hold base AND digit count fixed, move only logQ, so
# the only thing changing about the gadget is the TOP digit's range Q/b^(d-1).
# (d-1)*b^2 and b^2 both predict the shape factor is CONSTANT across such a run.
# Measured at base 2^9, d=3: it rose x2.08 over logQ 24->27. gadget_shape()
# predicts x1.97.
# (logQ, N, n, q, qKS, gadget_map, samples, sigma)
# --------------------------------------------------------------------------
ACC_TOPDIGIT = [
    (21,  1024,   512,  2048,    2097152, {32: 512},                 8000,  102.183735),
    (22,  1024,   512,  2048,    4194304, {32: 512},                 8000,   51.211885),
    (22,  1024,    64,  2048,    4194304, {128: 64},                 4000,   58.947196),
    (23,  1024,   512,  2048,    8388608, {32: 512},                 8000,   26.737691),
    (23,  1024,    64,  2048,    8388608, {128: 64},                 4000,   29.260346),
    (24,  1024,   512,  2048,   16777216, {32: 512},                 8000,   14.330680),
    (24,  1024,    64,  2048,   16777216, {128: 64},                 4000,   14.617298),
    (24,  1024,    32,  2048,   16777216, {512: 32},                 8000,   29.369277),
    (25,  1024,   512,  2048,   33554432, {32: 512},                 8000,    9.140805),
    (25,  1024,    64,  2048,   33554432, {128: 64},                 4000,    7.567779),
    (25,  1024,    32,  2048,   33554432, {512: 32},                 8000,   15.048220),
    (26,  1024,    32,  2048,   67108864, {512: 32},                 8000,    8.299466),
    (27,  1024,    32,  2048,  134217728, {512: 32},                 8000,    5.315698),
]

# --------------------------------------------------------------------------
# LMKCDEY's accumulator, which the GINX-derived constant does not describe.
# Both accumulators call the SAME SignedDigitDecompose, so gadget_shape() should
# carry over and only the constant differ -- and it does: the top-digit scan gives
# k = 5.019 / 5.017 / 4.873 / 4.754 over logQ 24..27, the same +-2.7% spread GINX
# gives at 6.8. Arms: (1) top-digit scan, (2) numAutoKeys, which GINX has no
# analogue of, (3) linearity in n, (4) a GAUSSIAN secret, legal here unlike GINX.
# (logQ, N, n, q, qKS, gadget_map, samples, sigma)
# --------------------------------------------------------------------------
ACC_LMKCDEY = [
    (24,  1024,    32,  2048,   16777216, {512: 32},                 8000,   25.578829),
    (25,  1024,    32,  2048,   33554432, {512: 32},                 8000,   13.130457),
    (25,  1024,    32,  2048,   33554432, {512: 32},                 8000,   23.301570),
    (25,  1024,    32,  2048,   33554432, {512: 32},                 8000,   16.229172),
    (25,  1024,    32,  2048,   33554432, {512: 32},                 8000,   11.204994),
    (25,  1024,    32,  2048,   33554432, {512: 32},                 8000,   14.078428),
    (25,  1024,    64,  2048,   33554432, {512: 64},                 8000,   15.688535),
    (25,  1024,   128,  2048,   33554432, {512: 128},                8000,   20.125438),
    (26,  1024,    32,  2048,   67108864, {512: 32},                 8000,    7.107424),
    (27,  1024,    32,  2048,  134217728, {512: 32},                 8000,    4.560247),
]

# --------------------------------------------------------------------------
# The gadget-split ladder repeated at logQ=25 and 23. Rungs move only the split
# between two adjacent bases, so keyswitch and rounding are identical within a
# rung and cancel in differences.
# (logQ, N, n, q, qKS, gadget_map, samples, sigma)
# --------------------------------------------------------------------------
LADDER2 = [
    (23,  1024,   192,  2048,    8388608, {32: 48, 128: 144},        5600,   44.992665),
    (23,  1024,   192,  2048,    8388608, {32: 96, 128: 96},         5600,   37.752029),
    (23,  1024,   192,  2048,    8388608, {128: 192},                5600,   51.015635),
    (25,  1024,  1024,  2048,   33554432, {32: 256, 128: 768},       5600,   27.114722),
    (25,  1024,  1024,  2048,   33554432, {32: 512, 128: 512},       5600,   23.399647),
    (25,  1024,  1024,  2048,   33554432, {32: 768, 128: 256},       5600,   18.910993),
    (25,  1024,  1024,  2048,   33554432, {32: 1024},                5600,   12.880884),
    (25,  1024,   192,  2048,   33554432, {128: 48, 512: 144},       5600,   33.102425),
    (25,  1024,   192,  2048,   33554432, {128: 96, 512: 96},        5600,   28.178044),
    (25,  1024,   192,  2048,   33554432, {128: 144, 512: 48},       5600,   22.039088),
    (25,  1024,   192,  2048,   33554432, {128: 192},                5600,   13.143818),
    (25,  1024,  1024,  2048,   33554432, {128: 1024},               5600,   30.701145),
    (25,  1024,   192,  2048,   33554432, {512: 192},                5600,   37.577547),
]

# --------------------------------------------------------------------------
# Gate-level pooled noise for shipped sets, 400 gates x 6 keys, extending
# SHIPPED_SIGMA above. The two GAUSSIAN sets are the first Gaussian gate
# measurements taken: they are both LMKCDEY, because GINX cannot represent a
# Gaussian secret at all (see METHOD_KEYDIST below).
# set -> (method, samples, sigma, mean)
# --------------------------------------------------------------------------
GATE_NOISE_2026_08_31 = {
    "MEDIUM":                (2,  2400,   12.325778, -1.098750),
    "STD128Q_LMKCDEY":       (3,  2400,   10.201997, +0.192917),
    "STD128_3":              (2,  2400,    9.969262, +0.092500),
    "STD128_4":              (2,  2400,    6.643823, +0.401667),
    "STD128_LMKCDEY":        (3,  2400,   10.255194, -0.192083),
    "STD192Q_LMKCDEY":       (3,  2400,   40.255967, +1.822500),
    "STD192_LMKCDEY":        (3,  2400,   41.821107, +3.064583),
    "STD256Q":               (2,  2400,   17.642305, -0.664583),
}


# --------------------------------------------------------------------------
# Gate-time calibration, three passes, 632 timings total. The fitted constants
# live in dse_model.GATE_COST with their provenance; only the summary is kept
# here because these are cheap to regenerate (31 minutes of machine time in
# total, against ~2 hours for the noise runs) and 632 rows would bloat this file.
#
# Raw logs: llserver:~/lattice-estimator-docker/results-2026-08-31/
#   gatecost.log   72 runs, n=256 only            -- and that was the mistake
#   gatecost2.log  144 runs, n in {64,256,448}    -- identified the n term
#   gatecost3.log  416 runs, n to 960, duplicated -- production fit
# Drivers: gatecost.sh (reducer), gatecost{,2,3}.cmds, gatecost-when-idle.sh
#
# GATE_COST_DUPLICATE_SPREAD records the reproducibility of a 5-gate timing,
# measured by running all 208 configurations of pass 3 twice. It is what justifies
# taking the MINIMUM of each pair rather than the mean: at 0.34% median the pairs
# agree, and the 8 configurations that do not are one-sided.
# --------------------------------------------------------------------------
GATE_COST_DUPLICATE_SPREAD = {
    "configs":       208,
    "median":        0.0034,
    "p90":           0.0171,
    "max":           0.237,
    "over_5pct":     8,
    "worst_config":  ("LMKCDEY", 2048, 32, 256, 32),   # method, N, word, n, base
    "worst_values":  (76972.0, 53090.2),               # 45% apart; one pass only
}

# Word-size factor at IDENTICAL (method, N, logQ, gadget) -- review item 6, which
# asked for this to be isolated before being spent. The sweep's "~2.6x" was
# confounded: there, d_g >= 4 also dropped the modulus 54 -> 27 bits. Here nothing
# moves but NATIVEINT.
#   (method, N) -> mean NS64/NS32 ratio over the four gadget bases
GATE_WORD_SIZE_FACTOR = {
    ("GINX",     512): 2.40, ("GINX",    1024): 2.69, ("GINX",    2048): 2.82,
    ("LMKCDEY",  512): 1.95, ("LMKCDEY", 1024): 2.09, ("LMKCDEY", 2048): 2.06,
    ("AP",       512): 2.11, ("AP",      1024): 2.37, ("AP",      2048): 2.41,
}
