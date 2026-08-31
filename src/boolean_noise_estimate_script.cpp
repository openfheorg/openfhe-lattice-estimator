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
  Example for the FHEW scheme using the default bootstrapping method (GINX)
 */
#define PROFILE

#include <type_traits>
#include "binfhecontext.h"
#include "utils/memory.h"
#include "utils/sertype.h"
#include "utils/serial.h"
#include <getopt.h>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <unordered_map>

using namespace lbcrypto;

static uint64_t parse_uint(const char* s, const char* name, uint64_t hi) {
    if ((s == nullptr) || (*s == '\0') || (std::strchr(s, '-') != nullptr))
        OPENFHE_THROW(std::string("--") + name + ": expected a non-negative integer, got '" +
                      (s ? s : "(none)") + "'");
    errno     = 0;
    char* end = nullptr;
    unsigned long long v = std::strtoull(s, &end, 10);
    if ((end == s) || (*end != '\0') || (errno == ERANGE) || (v > hi))
        OPENFHE_THROW(std::string("--") + name + ": expected an integer in [0, " +
                      std::to_string(hi) + "], got '" + s + "'");
    return v;
}

static uint32_t parse_u32(const char* s, const char* name) {
    return static_cast<uint32_t>(parse_uint(s, name, std::numeric_limits<uint32_t>::max()));
}

static double parse_double(const char* s, const char* name) {
    errno     = 0;
    char* end = nullptr;
    double v  = std::strtod(s ? s : "", &end);
    if ((s == nullptr) || (end == s) || (*end != '\0') || (errno == ERANGE) || !(v > 0.0))
        OPENFHE_THROW(std::string("--") + name + ": expected a positive number, got '" +
                      (s ? s : "(none)") + "'");
    return v;
}

inline std::string usage() {
    return std::string("\n\nusage: \n"
                       "  -n lattice dimension\n"
                       "  -N ring dimension\n"
                       "  -q ct modulus\n"
                       "  -Q size of ring modulus\n"
                       "  -k size of key switching mod Qks\n"
                       "  -g digit base B_g\n"
                       "  -r refreshing key base B_rk\n"
                       "  -b key switching base B_ks\n"
                       "  -s sigma (standard deviation)\n"
                       "  -t bootstrapping technique\n"
                       "  -d secret key distribution\n"
                       "  -a number of auto keys\n"
                       "  -I number of gate inputs\n"
                       "  -i gates per key (noise samples produced per key)\n"
                       "  -K independent keys to pool noise over (default 8)\n"
                       "     total noise samples = -i * -K\n"
                       "  -p label for named binfhe param set (overrides other settings)\n"
                       "  -Z skip key/ciphertext size reporting (avoids a large transient allocation)\n"
                       "  -h display this message\n"
                     );
}

static const std::unordered_map<std::string, BINFHE_PARAMSET> ptable = {
    {"TOY", TOY}, {"TOY_MULTI_BASE", TOY_MULTI_BASE}, {"MEDIUM", MEDIUM}, {"STD128_AP", STD128_AP},
    {"STD128", STD128}, {"STD128_3", STD128_3}, {"STD128_4", STD128_4},
    {"STD128Q", STD128Q}, {"STD128Q_3", STD128Q_3}, {"STD128Q_4", STD128Q_4},
    {"STD192", STD192}, {"STD192_3", STD192_3}, {"STD192_4", STD192_4},
    {"STD192Q", STD192Q}, {"STD192Q_3", STD192Q_3}, {"STD192Q_4", STD192Q_4},
    {"STD256", STD256}, {"STD256_3", STD256_3}, {"STD256_4", STD256_4},
    {"STD256Q", STD256Q}, {"STD256Q_3", STD256Q_3}, {"STD256Q_4", STD256Q_4},
    {"STD128_LMKCDEY", STD128_LMKCDEY}, {"STD128Q_LMKCDEY", STD128Q_LMKCDEY},
    {"STD128_3_LMKCDEY", STD128_3_LMKCDEY}, {"STD128Q_3_LMKCDEY", STD128Q_3_LMKCDEY},
    {"STD128_4_LMKCDEY", STD128_4_LMKCDEY}, {"STD128Q_4_LMKCDEY", STD128Q_4_LMKCDEY},
    {"STD192_LMKCDEY", STD192_LMKCDEY}, {"STD192Q_LMKCDEY", STD192Q_LMKCDEY},
    {"STD192_3_LMKCDEY", STD192_3_LMKCDEY}, {"STD192Q_3_LMKCDEY", STD192Q_3_LMKCDEY},
    {"STD192_4_LMKCDEY", STD192_4_LMKCDEY}, {"STD192Q_4_LMKCDEY", STD192Q_4_LMKCDEY},
    {"STD256_LMKCDEY", STD256_LMKCDEY}, {"STD256Q_LMKCDEY", STD256Q_LMKCDEY},
    {"STD256_3_LMKCDEY", STD256_3_LMKCDEY}, {"STD256Q_3_LMKCDEY", STD256Q_3_LMKCDEY},
    {"STD256_4_LMKCDEY", STD256_4_LMKCDEY}, {"STD256Q_4_LMKCDEY", STD256Q_4_LMKCDEY},
    {"LPF_STD128", LPF_STD128}, {"LPF_STD128Q", LPF_STD128Q},
    {"LPF_STD128_LMKCDEY", LPF_STD128_LMKCDEY}, {"LPF_STD128Q_LMKCDEY", LPF_STD128Q_LMKCDEY},
    {"SIGNED_MOD_TEST", SIGNED_MOD_TEST}
};

static const std::unordered_map<uint32_t, BINGATE> gtable = { {2, OR}, {3, OR3}, {4, OR4} };

int main(int argc, char* argv[]) {
    uint32_t dim_n                   = 0;
    uint64_t Qks                     = 0;
    uint32_t dim_N                   = 0;
    uint32_t ctmodq                  = 0;
    uint32_t logQ                    = 0;
    uint32_t B_g                     = 0;
    uint32_t B_ks                    = 0;
    uint32_t B_rk                    = 64;
    double sigma                     = 3.19;
    uint32_t bootstrapping_technique = 2;
    uint32_t secret_dist             = 1;
    uint32_t numAutoKeys             = 10;
    uint32_t num_of_inputs           = 2;
    uint32_t num_of_runs             = 200;
    uint32_t num_of_keys             = 8;
    bool report_sizes                = true;
    std::string namedparamset;

    static struct option long_options[] = {{"lattice-dimension", required_argument, NULL, 'n'},
                                           {"ring-dimension", required_argument, NULL, 'N'},
                                           {"ct-modulus", required_argument, NULL, 'q'},
                                           {"ring-modulus-bits", required_argument, NULL, 'Q'},
                                           {"keyswitch-modulus", required_argument, NULL, 'k'},
                                           {"gadget-base", required_argument, NULL, 'g'},
                                           {"refresh-key-base", required_argument, NULL, 'r'},
                                           {"keyswitch-base", required_argument, NULL, 'b'},
                                           {"sigma", required_argument, NULL, 's'},
                                           {"bootstrapping-technique", required_argument, NULL, 't'},
                                           {"secret-key-distribution", required_argument, NULL, 'd'},
                                           {"num-auto-keys", required_argument, NULL, 'a'},
                                           {"num-gate-inputs", required_argument, NULL, 'I'},
                                           {"num-iterations", required_argument, NULL, 'i'},
                                           {"num-keys", required_argument, NULL, 'K'},
                                           {"param-set", required_argument, NULL, 'p'},
                                           {"no-sizes", no_argument, NULL, 'Z'},
                                           {"help", no_argument, NULL, 'h'},
                                           {NULL, 0, NULL, 0}};

    int opt(0);
    const char* optstring = "n:N:q:Q:k:g:r:b:s:t:d:a:I:i:K:p:Zh";
    while ((opt = getopt_long(argc, argv, optstring, long_options, NULL)) != -1) {
        std::cout << "opt1: " << static_cast<char>(opt) << "; optarg: " << (optarg ? optarg : "(none)") << std::endl;
        switch (opt) {
            case 'n':
                dim_n = parse_u32(optarg, "lattice-dimension");
                break;
            case 'N':
                dim_N = parse_u32(optarg, "ring-dimension");
                break;
            case 'Q':
                logQ = parse_u32(optarg, "ring-modulus-bits");
                break;
            case 'q':
                ctmodq = parse_u32(optarg, "ct-modulus");
                break;
            case 'k':
                Qks = parse_uint(optarg, "keyswitch-modulus", std::numeric_limits<uint64_t>::max());
                break;
            case 'g':
                B_g = parse_u32(optarg, "gadget-base");
                break;
            case 'b':
                B_ks = parse_u32(optarg, "keyswitch-base");
                break;
            case 'r':
                B_rk = parse_u32(optarg, "refresh-key-base");
                break;
            case 's':
                sigma = parse_double(optarg, "sigma");
                break;
            case 't':
                bootstrapping_technique = parse_u32(optarg, "bootstrapping-technique");
                break;
            case 'd':
                secret_dist = parse_u32(optarg, "secret-key-distribution");
                break;
            case 'a':
                numAutoKeys = parse_u32(optarg, "num-auto-keys");
                break;
            case 'I':
                num_of_inputs = parse_u32(optarg, "num-gate-inputs");
                break;
            case 'i':
                num_of_runs = parse_u32(optarg, "num-iterations");
                break;
            case 'K':
                num_of_keys = parse_u32(optarg, "num-keys");
                break;
            case 'Z':
                report_sizes = false;
                break;
            case 'p':
                std::stringstream(optarg) >> namedparamset;
                break;
            case 'h':
                std::cout << usage() << std::endl;
                return 0;
            default:
                std::cerr << usage() << std::endl;
                return 1;
        }
    }

    if ((num_of_inputs < 2) || (num_of_inputs > 4))
        OPENFHE_THROW("num_of_inputs not in [2, 3, 4]");

    if (num_of_keys < 1)
        OPENFHE_THROW("num_of_keys must be >= 1");

    if (num_of_runs < 1)
        OPENFHE_THROW("num_of_runs must be >= 1");

    if (!namedparamset.empty() && (ptable.find(namedparamset) == ptable.end())) {
        std::string known;
        for (auto&& kv : ptable)
            known += (known.empty() ? "" : " ") + kv.first;
        OPENFHE_THROW("unknown --param-set '" + namedparamset + "'; known sets are: " + known);
    }

    if (Qks > std::numeric_limits<uint32_t>::max())
        OPENFHE_THROW("Qks does not fit in uint32_t (BinFHEContextParams::modKS)");

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
    paramset.numAutoKeys = numAutoKeys;

    if (secret_dist == 0) {
        paramset.keyDist = GAUSSIAN;
    } else if (secret_dist == 1) {
        paramset.keyDist = UNIFORM_TERNARY;
    } else {
        OPENFHE_THROW("invalid secret key distribution");
    }


    // ********************
    // STD128 is the security level of 128 bits of security based on LWE Estimator
    // and HE standard. Other common options are TOY, MEDIUM, STD192, and STD256.
    // MEDIUM corresponds to the level of more than 100 bits for both quantum and
    // classical computer attacks.
    // cc.GenerateBinFHEContext(STD128_AP_3, AP);

    std::cout << "parameters from commandline dim_n, dim_N, logQ, q, Qks, B_g, B_ks: "
              << " " << dim_n << " " << dim_N << " " << logQ << " " << ctmodq << " " << Qks << " " << B_g << " " << B_ks
              << std::endl;

    std::cout << "parameters from commandline secret_dist, bootstrapping technique: "
              << secret_dist << " " <<  bootstrapping_technique
              << std::endl;

    BINFHE_METHOD bt;
    if (bootstrapping_technique == 1) {
        bt = AP;
    } else if (bootstrapping_technique == 2) {
        bt = GINX;
    } else if (bootstrapping_technique == 3) {
        bt = LMKCDEY;
    } else {
        OPENFHE_THROW("invalid bootstrapping technique");
    }

    // Sample Program: Step 1: Set CryptoContext
    if (!namedparamset.empty())
        std::cout << "parameters from commandline overridden with: " << namedparamset << std::endl;

    auto make_context = [&]() {
        BinFHEContext c;
        if (!namedparamset.empty())
            c.GenerateBinFHEContext(ptable.at(namedparamset), bt);
        else
            c.GenerateBinFHEContext(paramset, bt);
        return c;
    };

    // Sample Program: Step 2: Key Generation

    auto p          = 2 * num_of_inputs;
    const auto eq2  = num_of_inputs == 2;
    const auto gate = gtable.at(num_of_inputs);

    TimeVar t;
    double keygen_ms = 0.0;
    double gate_us   = 0.0;
    uint64_t gates   = 0;
    auto fcnt        = 0;
    LWEPlaintext result;

    // One key hides a per-key noise bias entirely (sigma is taken about the
    // sample mean) and leaves a ~2% key-to-key spread that no amount of
    // sampling reduces. Pooling over -K independent keys is what makes a
    // failure-probability claim mean anything below a few bits.
    NativeInteger ctmod(0);
    for (uint32_t k = 0; k < num_of_keys; ++k) {
        auto cc = make_context();
        ctmod   = cc.GetParams()->GetLWEParams()->Getq();

        TIC(t);
        auto sk = cc.KeyGen();
        cc.BTKeyGen(sk);
        keygen_ms += TOC_MS(t);

        std::vector<LWECiphertext> cts(num_of_inputs);
        for (auto&& ct : cts)
            ct = cc.Encrypt(sk, 0, SMALL_DIM, p);

        // Measuring the keys costs a full serialized copy of them, which for a
        // multi-GB key-switching key dwarfs everything else the process holds.
        // Only one run per candidate needs the sizes (the speed probe), so the
        // parallel noise runs pass -Z and skip it entirely.
        if ((k == 0) && report_sizes) {
            // Separate scopes so two multi-GB buffers are never alive at once.
            {
                std::ostringstream ss;
                lbcrypto::Serial::Serialize(cc.GetRefreshKey(), ss, lbcrypto::SerType::BINARY);
                std::cout << "BootstrappingKeySize: " << static_cast<std::streamoff>(ss.tellp()) << std::endl;
            }
            {
                std::ostringstream ss;
                lbcrypto::Serial::Serialize(cc.GetSwitchKey(), ss, lbcrypto::SerType::BINARY);
                std::cout << "KeySwitchingKeySize: " << static_cast<std::streamoff>(ss.tellp()) << std::endl;
            }
            {
                std::ostringstream ss;
                lbcrypto::Serial::Serialize(cts.front(), ss, lbcrypto::SerType::BINARY);
                std::cout << "CiphertextSize: " << static_cast<std::streamoff>(ss.tellp()) << std::endl;
            }
        }

        // Keygen and the serialization above are transient peaks; hand the freed
        // arenas back before the gate loop, which is the long-lived phase and
        // the one that decides how many of these fit in memory side by side.
        lbcrypto::AllocTrim();

        for (uint32_t i = 0; i < num_of_runs; ++i) {
            for (auto&& ct : cts)
                ct = cc.Encrypt(sk, 0, SMALL_DIM, p);

            TIC(t);
            auto ct = eq2 ? cc.EvalBinGate(gate, cts[0], cts[1]) : cc.EvalBinGate(gate, cts);
            gate_us += TOC_US(t);
            ++gates;

            cc.Decrypt(sk, ct, &result, p);

            if (result != 0)
                ++fcnt;
        }
    }

    std::cout << "BootstrapKeyGenTime: " << static_cast<uint64_t>(keygen_ms/num_of_keys) << " milliseconds" << std::endl;
    std::cout << "EvalBinGateTime: " << static_cast<uint64_t>(gate_us/gates/1000.0) << " milliseconds" << std::endl;
    // finer resolution than the integer millisecond above, for ranking candidates
    std::cout << "EvalBinGateTimeUs: " << (gate_us/gates) << std::endl;
    std::cout << "NumKeys: " << num_of_keys << std::endl;
    std::cout << "Gate: " << gate << std::endl;
    std::cout << "Failures: " << fcnt << std::endl;
    std::cout << "ctmodq: " << ctmod << std::endl;

    return 0;
}
