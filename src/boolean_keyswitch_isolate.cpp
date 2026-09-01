//==================================================================================
// BSD 2-Clause License
//
// Copyright (c) 2014-2022, NJIT, Duality Technologies Inc. and other contributors
//
// All rights reserved.
//
// Author TPOC: contact@openfhe.org
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are met:
//
// 1. Redistributions of source code must retain the above copyright notice, this
//    list of conditions and the following disclaimer.
//
// 2. Redistributions in binary form must reproduce the above copyright notice,
//    this list of conditions and the following disclaimer in the documentation
//    and/or other materials provided with the distribution.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
// AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
// IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
// DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
// FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
// DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
// SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
// CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
// OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
// OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
//==================================================================================

/*
  Isolated measurement of the post-accumulator chain -- "stage 4" of the parameter
  search plan.

  A bootstrapped ciphertext's noise is the sum of four contributions, and a gate
  measurement gives their total. Three of the four live AFTER the accumulator, in
  the deterministic tail

      (N, Q) --ModSwitch--> (N, q_KS) --KeySwitch--> (n, q_KS) --ModSwitch--> (n, q)

  and none of them needs a bootstrapping key. Feeding that tail a ciphertext whose
  error is known instead of an accumulator output therefore measures each term on
  its own, in seconds rather than hours, with NO free constant to fit -- the model
  predicts these three outright, so the comparison is a test and not a
  calibration.

  Why this before more accumulator work: at STD128 key switching is 61% of noise
  variance against the accumulator's 23%, so the keyswitch term's +-10% band costs
  7.5 bits of log2Pf against the accumulator's 5.7. Pinning it is worth 3.1 bits
  per candidate, nearly 3x what halving the accumulator band buys.

  Stages
  ------
    round2   final q_KS -> q switch, dimension n. Needs one secret key and no
             switching key, so it runs in milliseconds. Predicted variance
             Var(s)/12 * (1 - 1/M^2) * n with M = q_KS/q -- the term whose
             coefficient is 15x larger for a Gaussian secret than a ternary one
             and which no sweep record has ever exercised on the Gaussian side.
    round1   first Q -> q_KS switch, dimension N, read under the ring secret.
             Predicted Var(s)/12 * N at q_KS. Derived but never measured: it is
             invisible in a gate measurement because (q/q_KS)^2 scales it away,
             which is exactly why fitting it returned zero.
    ksonly   KeySwitch alone at q_KS, no modulus switch either side. The cleanest
             possible read of sqrt(N*d_KS)*sigma, at the modulus where it is
             largest and so at the best available signal-to-noise.
    tail     all three together, matching BootstrapFunc exactly. Equal to
             sigma_total_at_q with the accumulator term removed, so it validates
             the composition and not just the parts.

  Every stage reports per-key mean and stddev separately from the pooled figure,
  because the split is a prediction in its own right. Key switching draws its
  noise once per switching key: over samples a given digit position selects one of
  baseKS stored rows, so the WITHIN-key variance is (1 - 1/baseKS) of
  N*d_KS*sigma^2 and the remaining 1/baseKS appears as a fixed per-key offset. A
  deployment runs one key and sees the offset as bias, not as noise -- which is
  the distinction a per-key quantile certification turns on and a pooled sigma
  hides.
*/

#include "binfhe_cli.h"

#include "math/discreteuniformgenerator.h"
#include "utils/memory.h"

#include <getopt.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

using namespace lbcrypto;
using namespace estimator;

namespace {

enum class Stage { ROUND1, ROUND2, KSONLY, TAIL };

Stage parse_stage(const char* s) {
    std::string v(s ? s : "");
    if (v == "round1") return Stage::ROUND1;
    if (v == "round2") return Stage::ROUND2;
    if (v == "ksonly") return Stage::KSONLY;
    if (v == "tail")   return Stage::TAIL;
    OPENFHE_THROW("--stage: expected one of round1, round2, ksonly, tail; got '" + v + "'");
}

const char* stage_name(Stage s) {
    switch (s) {
        case Stage::ROUND1: return "round1";
        case Stage::ROUND2: return "round2";
        case Stage::KSONLY: return "ksonly";
        default:            return "tail";
    }
}

std::string usage() {
    return std::string(
        "\nusage: boolean_keyswitch_isolate --stage {round1,round2,ksonly,tail} [params]\n"
        "\n"
        "  -S --stage                which term to isolate (see the file comment)\n"
        "  -p --param-set            named binfhe set; overrides every parameter below\n"
        "  -n --lattice-dimension    n\n"
        "  -N --ring-dimension       N\n"
        "  -q --ct-modulus           q\n"
        "  -Q --ring-modulus-bits    log2(Q)\n"
        "  -k --keyswitch-modulus    q_KS\n"
        "  -g --gadget-base          B_g (unused by these stages; accepted so one\n"
        "                            command line drives both programs)\n"
        "  -G --gadget-map           \"base:count,...\", counts must sum to -n\n"
        "  -r --refresh-key-base     B_rk\n"
        "  -b --keyswitch-base       B_ks\n"
        "  -s --sigma                sigma\n"
        "  -t --bootstrapping-technique  1=AP 2=GINX 3=LMKCDEY\n"
        "  -d --secret-key-distribution  0=GAUSSIAN 1=UNIFORM_TERNARY\n"
        "  -a --num-auto-keys        LMKCDEY automorphism key count\n"
        "  -P --plaintext-modulus    p (default 4)\n"
        "  -i --samples              samples per key (default 20000)\n"
        "  -K --num-keys             independent keys (default 8)\n"
        "  -h --help                 this message\n"
        "\n"
        "Sample count: the relative error on a stddev is about 1/sqrt(2*samples), so\n"
        "20000 per key over 8 keys puts the pooled figure inside 0.2% -- the point of\n"
        "this program is a band tight enough to stop dominating log2Pf, and a short run\n"
        "does not deliver one.\n");
}

// Per-key accumulator. Kept in long double because ksonly's errors are O(10^3)
// and 20k samples of squares is where a float sum starts to lose digits.
struct Stats {
    uint64_t     n{0};
    long double  sum{0.0L};
    long double  sumsq{0.0L};

    void add(double x) {
        ++n;
        sum   += x;
        sumsq += static_cast<long double>(x) * x;
    }
    double mean() const { return n ? static_cast<double>(sum / n) : 0.0; }
    // about the sample mean: this is the WITHIN-key figure, deliberately not the
    // pooled one. The two differ by the per-key offset and that difference is a
    // prediction, so they must never be conflated.
    double sd() const {
        if (n < 2)
            return 0.0;
        long double v = (sumsq - sum * sum / static_cast<long double>(n)) / (n - 1);
        return static_cast<double>(std::sqrt(std::max<long double>(v, 0.0L)));
    }
};

// The signed phase error of `ct` under `s_raw`, in units of ct's own modulus.
//
// Reproduces what Decrypt's WITH_NOISE_DEBUG block reports, but computed here so
// the program does not require a noise-debug build and so per-key statistics are
// available without parsing a stderr stream.
double phase_error(const NativeVector& s_raw, ConstLWECiphertext& ct, uint32_t p) {
    const NativeInteger q  = ct->GetModulus();
    const NativeVector  s(s_raw, q);          // centered switch, as Decrypt does
    const NativeInteger mu = q.ComputeMu();
    const auto&         a  = ct->GetA();
    const uint32_t      len = s.GetLength();
    if (a.GetLength() != len)
        OPENFHE_THROW("phase_error: ciphertext dimension " + std::to_string(a.GetLength()) +
                      " does not match key dimension " + std::to_string(len));

    NativeInteger inner(0);
    // ModAddFastEq rather than the library's bare += : at q_KS near 2^32 and
    // N = 2048 the unreduced accumulator is fine, but this costs nothing next to
    // KeySwitch and removes the question entirely.
    for (uint32_t i = 0; i < len; ++i)
        inner.ModAddFastEq(a[i].ModMulFast(s[i], q, mu), q);

    NativeInteger r = ct->GetB();
    r.ModSubFastEq(inner, q);

    const double x    = r.ConvertToDouble();
    const double cell = q.ConvertToDouble() / p;   // plaintext slots sit cell apart
    return x - cell * std::round(x / cell);        // signed distance to the nearest
}

// An EXACTLY noiseless LWE ciphertext: a uniform, b = <a,s> + m*(mod/p), mod mod.
//
// Built here rather than with LWEEncryptionScheme::Encrypt for two reasons. The
// small one is that a zero-noise input removes the last confound: every stage's
// output error is then wholly attributable to the operation under test, with no
// input variance to subtract.
//
// The large one is that the library's private-key Encrypt cannot be used at
// modulus Q at all in a 32-bit build. It accumulates
//     b += a[i].ModMulFast(s[i], q, mu)
// UNREDUCED over the whole dimension and reduces once at the end, which needs
// n*q to fit the native word. That holds comfortably for its intended use
// (n <= 1319, q <= 4096, so about 2^22) and fails at (N, Q): 1024 * 2^27 = 2^37
// against a 32-bit NativeInteger. Measured, before this was fixed: round1 on
// STD128 read mean -160 sd 8.21 in the NS32 build against mean -0.19 sd 7.57 in
// NS64, where the model predicts 7.554. The corruption is quiet -- a plausible
// sigma, no exception -- which is exactly the kind of number that gets fitted.
// Decrypt has the same unreduced accumulation, so it is avoided here too.
LWECiphertext noiseless_ct(const NativeVector& s_raw, const NativeInteger& mod, LWEPlaintext m, uint32_t p) {
    const NativeVector s(s_raw, mod);
    const uint32_t     d = s.GetLength();
    DiscreteUniformGeneratorImpl<NativeVector> dug;
    NativeVector a = dug.GenerateVector(d, mod);

    const NativeInteger mu = mod.ComputeMu();
    NativeInteger b(static_cast<uint64_t>(m % p) * (mod.ConvertToInt() / p));
    b.ModEq(mod);
    for (uint32_t i = 0; i < d; ++i)
        b.ModAddFastEq(a[i].ModMulFast(s[i], mod, mu), mod);

    return std::make_shared<LWECiphertextImpl>(std::move(a), b, p);
}

// The two moments of the secret that the predictions are linear in.
//
//   SUM_i s_i    the rounding bias is (1/(2M)) * (1 - SUM_i s_i), so this fixes
//                the per-key MEAN exactly.
//   SUM_i s_i^2  the rounding variance is Var(d) * SUM_i s_i^2, so this fixes the
//                per-key VARIANCE exactly.
//
// Both are reported because using n*Var(s) instead of SUM_i s_i^2 charges the
// model for key-to-key sampling: at n = 422 the count of nonzero ternary
// coefficients scatters 3.4% per key, which is 0.9% on a pooled sigma over 16
// keys -- an order of magnitude above this program's sampling floor, and enough
// to look like a model error. With SUM_i s_i^2 the prediction is per key and the
// residual is measurement noise alone.
struct KeyMoments {
    double sum{0.0};
    double sumsq{0.0};
};

KeyMoments key_moments(const NativeVector& s) {
    const NativeInteger q = s.GetModulus();
    KeyMoments km;
    for (uint32_t i = 0; i < s.GetLength(); ++i) {
        const double v = centered(s[i], q);
        km.sum   += v;
        km.sumsq += v * v;
    }
    return km;
}

}  // namespace

int main(int argc, char* argv[]) {
    Stage stage           = Stage::TAIL;
    bool  stage_given     = false;
    uint32_t dim_n        = 0;
    uint32_t dim_N        = 0;
    uint32_t ctmodq       = 0;
    uint32_t logQ         = 0;
    uint64_t Qks          = 0;
    uint32_t B_g          = 0;
    uint32_t B_ks         = 0;
    uint32_t B_rk         = 64;
    double   sigma        = 3.19;
    uint32_t method_idx   = 2;
    uint32_t secret_dist  = 1;
    uint32_t numAutoKeys  = 10;
    uint32_t ptmod        = 4;
    uint32_t samples      = 20000;
    uint32_t num_of_keys  = 8;
    std::map<uint32_t, uint32_t> gadgetBaseMap;
    std::string namedparamset;

    static struct option long_options[] = {{"stage", required_argument, NULL, 'S'},
                                           {"lattice-dimension", required_argument, NULL, 'n'},
                                           {"ring-dimension", required_argument, NULL, 'N'},
                                           {"ct-modulus", required_argument, NULL, 'q'},
                                           {"ring-modulus-bits", required_argument, NULL, 'Q'},
                                           {"keyswitch-modulus", required_argument, NULL, 'k'},
                                           {"gadget-base", required_argument, NULL, 'g'},
                                           {"gadget-map", required_argument, NULL, 'G'},
                                           {"refresh-key-base", required_argument, NULL, 'r'},
                                           {"keyswitch-base", required_argument, NULL, 'b'},
                                           {"sigma", required_argument, NULL, 's'},
                                           {"bootstrapping-technique", required_argument, NULL, 't'},
                                           {"secret-key-distribution", required_argument, NULL, 'd'},
                                           {"num-auto-keys", required_argument, NULL, 'a'},
                                           {"plaintext-modulus", required_argument, NULL, 'P'},
                                           {"samples", required_argument, NULL, 'i'},
                                           {"num-keys", required_argument, NULL, 'K'},
                                           {"param-set", required_argument, NULL, 'p'},
                                           {"help", no_argument, NULL, 'h'},
                                           {NULL, 0, NULL, 0}};

    int opt(0);
    const char* optstring = "S:n:N:q:Q:k:g:G:r:b:s:t:d:a:P:i:K:p:h";
    while ((opt = getopt_long(argc, argv, optstring, long_options, NULL)) != -1) {
        switch (opt) {
            case 'S': stage = parse_stage(optarg); stage_given = true;            break;
            case 'n': dim_n       = parse_u32(optarg, "lattice-dimension");       break;
            case 'N': dim_N       = parse_u32(optarg, "ring-dimension");          break;
            case 'q': ctmodq      = parse_u32(optarg, "ct-modulus");              break;
            case 'Q': logQ        = parse_u32(optarg, "ring-modulus-bits");       break;
            case 'k': Qks         = parse_uint(optarg, "keyswitch-modulus",
                                               std::numeric_limits<uint64_t>::max()); break;
            case 'g': B_g         = parse_u32(optarg, "gadget-base");             break;
            case 'G': gadgetBaseMap = parse_gadget_map(optarg);                   break;
            case 'r': B_rk        = parse_u32(optarg, "refresh-key-base");        break;
            case 'b': B_ks        = parse_u32(optarg, "keyswitch-base");          break;
            case 's': sigma       = parse_double(optarg, "sigma");                break;
            case 't': method_idx  = parse_u32(optarg, "bootstrapping-technique"); break;
            case 'd': secret_dist = parse_u32(optarg, "secret-key-distribution"); break;
            case 'a': numAutoKeys = parse_u32(optarg, "num-auto-keys");           break;
            case 'P': ptmod       = parse_u32(optarg, "plaintext-modulus");       break;
            case 'i': samples     = parse_u32(optarg, "samples");                 break;
            case 'K': num_of_keys = parse_u32(optarg, "num-keys");                break;
            // Validated eagerly rather than at context construction: a typo that
            // fell through to the hand-built parameters would be measured and
            // recorded under the wrong label.
            case 'p': namedparamset = optarg ? optarg : "";
                      (void)lookup_paramset(namedparamset);                       break;
            case 'h': std::cout << usage() << std::endl; return 0;
            default:  std::cerr << usage() << std::endl; return 1;
        }
    }

    if (!stage_given)
        OPENFHE_THROW("--stage is required; " + std::string("expected one of round1, round2, ksonly, tail"));
    if (samples < 2)
        OPENFHE_THROW("--samples must be >= 2 for a stddev");
    if (num_of_keys < 1)
        OPENFHE_THROW("--num-keys must be >= 1");
    if ((ptmod < 2) || ((ptmod & (ptmod - 1)) != 0))
        OPENFHE_THROW("--plaintext-modulus must be a power of two >= 2");
    if (Qks > std::numeric_limits<uint32_t>::max())
        OPENFHE_THROW("q_KS does not fit in uint32_t (BinFHEContextParams::modKS)");

    const BINFHE_METHOD bt = method_from_index(method_idx);

    BinFHEContextParams paramset;
    paramset.cyclOrder    = 2 * dim_N;
    paramset.modKS        = Qks;
    paramset.gadgetBase   = B_g;
    paramset.baseKS       = B_ks;
    paramset.baseRK       = B_rk;
    paramset.mod          = ctmodq;
    paramset.numberBits   = logQ;
    paramset.stdDev       = sigma;
    paramset.latticeParam = dim_n;
    paramset.numAutoKeys  = numAutoKeys;
    paramset.keyDist      = keydist_from_index(secret_dist);

    if (!gadgetBaseMap.empty()) {
        uint64_t covered = 0;
        for (auto&& kv : gadgetBaseMap)
            covered += kv.second;
        if (covered != dim_n)
            OPENFHE_THROW("--gadget-map counts sum to " + std::to_string(covered) + " but -n is " +
                          std::to_string(dim_n) + "; the map must cover the LWE dimension exactly");
        paramset.gadgetBaseMap = gadgetBaseMap;
        if (B_g == 0)
            paramset.gadgetBase = gadgetBaseMap.begin()->first;
    }

    BinFHEContext cc;
    if (!namedparamset.empty())
        cc.GenerateBinFHEContext(lookup_paramset(namedparamset), bt);
    else
        cc.GenerateBinFHEContext(paramset, bt);

    auto&& lwe    = cc.GetLWEScheme();
    auto&& lparams = cc.GetParams()->GetLWEParams();

    const NativeInteger q   = lparams->Getq();
    const NativeInteger Q   = lparams->GetQ();
    const NativeInteger qKS = lparams->GetqKS();
    const uint32_t      n   = lparams->Getn();
    const uint32_t      N   = lparams->GetN();
    const uint32_t      bKS = lparams->GetBaseKS();

    // Two digit counts, deliberately both reported. KeySwitch computes its own
    // with a floating-point ceil(log(q_KS)/log(baseKS)); the model mirrors
    // GetDigitCount, which is exact integer arithmetic. They agree at every
    // shipped set, but a disagreement would put the model's d_KS and the
    // library's actual loop bound out of step, so it is checked rather than
    // assumed.
    const uint32_t dks_lib =
        static_cast<uint32_t>(std::ceil(std::log(qKS.ConvertToDouble()) / std::log(static_cast<double>(bKS))));
    uint32_t dks_exact = 0;
    for (uint64_t v = 1; v < qKS.ConvertToInt(); v *= bKS)
        ++dks_exact;

    std::cout << "stage=" << stage_name(stage) << std::endl;
    std::cout << std::setprecision(17);
    std::cout << "param"
              << " set=" << (namedparamset.empty() ? "-" : namedparamset)
              << " n=" << n << " N=" << N
              << " q=" << q << " Q=" << Q << " qKS=" << qKS
              << " baseKS=" << bKS << " dks_lib=" << dks_lib << " dks_exact=" << dks_exact
              << " sigma=" << lparams->GetDgg().GetStd()
              << " sigmaKS=" << lparams->GetDggKS().GetStd()
              << " keydist=" << keydist_name(lparams->GetKeyDist())
              << " method=" << method_idx
              << " p=" << ptmod
              << " samples=" << samples << " keys=" << num_of_keys
              << std::endl;

    const bool need_skN = (stage != Stage::ROUND2);
    const bool need_ksk = (stage == Stage::KSONLY) || (stage == Stage::TAIL);

    std::vector<double> key_means;
    Stats pooled;                       // uncentered pool: keeps the per-key offset in
    std::vector<double> within_sds;

    for (uint32_t k = 0; k < num_of_keys; ++k) {
        auto sk = cc.KeyGen();
        LWEPrivateKey skN;
        if (need_skN)
            skN = cc.KeyGenN();
        LWESwitchingKey ksk;
        if (need_ksk)
            ksk = cc.KeySwitchGen(sk, skN);

        // Self-check, once per key: the constructed input has zero error by
        // construction, so a nonzero reading here means the arithmetic is wrong
        // rather than the model. This is the check that would have caught the
        // NS32 overflow described above on the first run instead of the fourth.
        {
            const NativeInteger inmod = (stage == Stage::ROUND2) ? qKS
                                      : ((stage == Stage::KSONLY) ? qKS : Q);
            const NativeVector& inkey = (stage == Stage::ROUND2) ? sk->GetElement()
                                                                 : skN->GetElement();
            ConstLWECiphertext probe = noiseless_ct(inkey, inmod, 0, ptmod);
            std::cout << "input idx=" << k << " phase=" << phase_error(inkey, probe, ptmod)
                      << " (must be 0)" << std::endl;
        }

        Stats per_key;
        for (uint32_t i = 0; i < samples; ++i) {
            LWECiphertext ct;
            double err = 0.0;
            switch (stage) {
                case Stage::ROUND2: {
                    ct  = noiseless_ct(sk->GetElement(), qKS, 0, ptmod);
                    ct  = lwe->ModSwitch(q, ct);
                    err = phase_error(sk->GetElement(), ct, ptmod);
                    break;
                }
                case Stage::ROUND1: {
                    ct  = noiseless_ct(skN->GetElement(), Q, 0, ptmod);
                    ct  = lwe->ModSwitch(qKS, ct);
                    err = phase_error(skN->GetElement(), ct, ptmod);
                    break;
                }
                case Stage::KSONLY: {
                    ct  = noiseless_ct(skN->GetElement(), qKS, 0, ptmod);
                    ct  = lwe->KeySwitch(lparams, ksk, ct);
                    err = phase_error(sk->GetElement(), ct, ptmod);
                    break;
                }
                case Stage::TAIL: {
                    ct  = noiseless_ct(skN->GetElement(), Q, 0, ptmod);
                    ct  = lwe->ModSwitch(qKS, ct);
                    ct  = lwe->KeySwitch(lparams, ksk, ct);
                    ct  = lwe->ModSwitch(q, ct);
                    err = phase_error(sk->GetElement(), ct, ptmod);
                    break;
                }
            }
            per_key.add(err);
            pooled.add(err);
        }

        // Printed per key so both the predicted mean and the predicted variance
        // can be compared key by key rather than only in aggregate.
        const KeyMoments ks_m  = key_moments(sk->GetElement());
        const KeyMoments ksN_m = need_skN ? key_moments(skN->GetElement()) : KeyMoments{};
        std::cout << "key idx=" << k
                  << " samples=" << per_key.n
                  << " mean=" << per_key.mean()
                  << " sd=" << per_key.sd()
                  << " sum_s=" << ks_m.sum
                  << " sumsq_s=" << ks_m.sumsq
                  << " sum_sN=" << ksN_m.sum
                  << " sumsq_sN=" << ksN_m.sumsq
                  << std::endl;

        key_means.push_back(per_key.mean());
        within_sds.push_back(per_key.sd());
        lbcrypto::AllocTrim();
    }

    // Between-key scatter of the mean. For ksonly this is the predicted
    // sqrt(N*d_KS/baseKS)*sigma offset; for round2 it is the rounding bias
    // tracking (1 - SUM_i s_i) across keys. Reported with the caveat that K is
    // small: the sampling error on a stddev from K keys is 1/sqrt(2K), i.e. 25%
    // at K = 8, so this figure is an order-of-magnitude check, not a band.
    double mm = 0.0;
    for (double v : key_means)
        mm += v;
    mm /= key_means.size();
    double bvar = 0.0;
    for (double v : key_means)
        bvar += (v - mm) * (v - mm);
    bvar = (key_means.size() > 1) ? bvar / (key_means.size() - 1) : 0.0;

    double wvar = 0.0;
    for (double v : within_sds)
        wvar += v * v;
    wvar /= within_sds.size();

    std::cout << "pooled"
              << " samples=" << pooled.n
              << " mean=" << pooled.mean()
              << " sd=" << pooled.sd()
              << " within_sd=" << std::sqrt(wvar)
              << " between_sd=" << std::sqrt(bvar)
              << " keys=" << key_means.size()
              << std::endl;

    return 0;
}
