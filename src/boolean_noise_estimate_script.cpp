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
#include "utils/sertype.h"
#include "utils/serial.h"
#include <getopt.h>
#include <unordered_map>

using namespace lbcrypto;

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
                       "  -i number of iterations\n"
                       "  -p label for named binfhe param set (overrides other settings)\n"
                       "  -h display this message\n"
                     );
}

static const std::unordered_map<std::string, BINFHE_PARAMSET> ptable = {
    {"TOY", TOY}, {"MEDIUM", MEDIUM}, {"STD128_AP", STD128_AP},
    {"STD128", STD128}, {"STD128_3", STD128_3}, {"STD128_4", STD128_4},
    {"STD128Q", STD128Q}, {"STD128Q_3", STD128Q_3}, {"STD128Q_4", STD128Q_4},
    {"STD192", STD192}, {"STD192_3", STD192_3}, {"STD192_4", STD192_4},
    {"STD192Q", STD192Q}, {"STD192Q_3", STD192Q_3}, {"STD192Q_4", STD192Q_4},
    {"STD256", STD256}, {"STD256_3", STD256_3}, {"STD256_4", STD256_4},
    {"STD256Q", STD256Q}, {"STD256Q_3", STD256Q_3}, {"STD256Q_4", STD256Q_4},
    {"STD128_LMKCDEY", STD128_LMKCDEY}, {"STD128_3_LMKCDEY", STD128_3_LMKCDEY}, {"STD128_4_LMKCDEY", STD128_4_LMKCDEY},
    {"STD128Q_LMKCDEY", STD128Q_LMKCDEY}, {"STD128Q_3_LMKCDEY", STD128Q_3_LMKCDEY}, {"STD128Q_4_LMKCDEY", STD128Q_4_LMKCDEY},
    {"STD192_LMKCDEY", STD192_LMKCDEY}, {"STD192_3_LMKCDEY", STD192_3_LMKCDEY}, {"STD192_4_LMKCDEY", STD192_4_LMKCDEY},
    {"STD192Q_LMKCDEY", STD192Q_LMKCDEY}, {"STD192Q_3_LMKCDEY", STD192Q_3_LMKCDEY}, {"STD192Q_4_LMKCDEY", STD192Q_4_LMKCDEY},
    {"STD256_LMKCDEY", STD256_LMKCDEY}, {"STD256_3_LMKCDEY", STD256_3_LMKCDEY}, {"STD256_4_LMKCDEY", STD256_4_LMKCDEY},
    {"STD256Q_LMKCDEY", STD256Q_LMKCDEY}, {"STD256Q_3_LMKCDEY", STD256Q_3_LMKCDEY}, {"STD256Q_4_LMKCDEY", STD256Q_4_LMKCDEY},
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
    uint32_t B_rk                    = 32;
    double sigma                     = 3.19;
    uint32_t bootstrapping_technique = 2;
    uint32_t secret_dist             = 1;
    uint32_t numAutoKeys             = 10;
    uint32_t num_of_inputs           = 2;
    uint32_t num_of_runs             = 200;
    std::string namedparamset;

    static struct option long_options[] = {{"lattice dimension", required_argument, NULL, 'n'},
                                           {"ring dimension", required_argument, NULL, 'N'},
                                           {"ct modulus", required_argument, NULL, 'q'},
                                           {"size of ring modulus", required_argument, NULL, 'Q'},
                                           {"size of key switching mod Qks", required_argument, NULL, 'k'},
                                           {"digit base B_g", required_argument, NULL, 'g'},
                                           {"refreshing key base B_rk", required_argument, NULL, 'r'},
                                           {"key switching base B_ks", required_argument, NULL, 'b'},
                                           {"sigma (standard deviation)", required_argument, NULL, 's'},
                                           {"bootstrapping technique", required_argument, NULL, 't'},
                                           {"secret key distribution", required_argument, NULL, 'd'},
                                           {"number of auto keys", required_argument, NULL, 'a'},
                                           {"number of gate inputs", required_argument, NULL, 'I'},
                                           {"number of iterations", required_argument, NULL, 'i'},
                                           {"label for named binfhe param set (overrides other settings)", required_argument, NULL, 'p'},
                                           {"help", no_argument, NULL, 'h'},
                                           {NULL, 0, NULL, 0}};

    char opt(0);
    const char* optstring = "n:N:q:Q:k:g:r:b:s:t:d:a:I:i:p:h";
    while ((opt = getopt_long(argc, argv, optstring, long_options, NULL)) != -1) {
        std::cout << "opt1: " << opt << "; optarg: " << optarg << std::endl;
        switch (opt) {
            case 'n':
                dim_n = atoi(optarg);
                break;
            case 'N':
                dim_N = atoi(optarg);
                break;
            case 'Q':
                logQ = atoi(optarg);
                break;
            case 'q':
                ctmodq = atoi(optarg);
                break;
            case 'k':
                // Qks = atoi(optarg);
                std::stringstream(optarg) >> Qks;
                break;
            case 'g':
                B_g = atoi(optarg);
                break;
            case 'b':
                B_ks = atoi(optarg);
                break;
            case 'r':
                B_rk = atoi(optarg);
                break;
            case 's':
                sigma = atof(optarg);
                break;
            case 't':
                bootstrapping_technique = atoi(optarg);
                break;
            case 'd':
                secret_dist = atoi(optarg);
                break;
            case 'a':
                numAutoKeys = atoi(optarg);
                break;
            case 'I':
                num_of_inputs = atoi(optarg);
                break;
            case 'i':
                num_of_runs = atoi(optarg);
                break;
            case 'p':
                std::stringstream(optarg) >> namedparamset;
                break;
            case 'h':
            default:
                OPENFHE_THROW(not_available_error, usage());
        }
    }

    if ((num_of_inputs < 2) || (num_of_inputs > 4))
        OPENFHE_THROW(not_available_error, "num_of_inputs not in [2, 3, 4]");

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
        OPENFHE_THROW(not_available_error, "invalid secret key distribution");
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
        OPENFHE_THROW(not_available_error, "invalid bootstrapping technique");
    }

    // Sample Program: Step 1: Set CryptoContext
    auto cc = BinFHEContext();
    if (!namedparamset.empty()) {
        std::cout << "parameters from commandline overridden with: " << namedparamset << std::endl;
        cc.GenerateBinFHEContext(ptable.at(namedparamset), bt);
    } else {
        cc.GenerateBinFHEContext(paramset, bt);
    }

    // Sample Program: Step 2: Key Generation

    TimeVar t;
    TIC(t);
    auto sk = cc.KeyGen();
    cc.BTKeyGen(sk);
    std::cout << "BootstrapKeyGenTime: " << TOC_MS(t) << " milliseconds" << std::endl;

    {
        auto bkey  = cc.GetRefreshKey();
        std::ostringstream bkeystring;
        lbcrypto::Serial::Serialize(bkey, bkeystring, lbcrypto::SerType::BINARY);
        std::cout << "BootstrappingKeySize: " << bkeystring.str().size() << std::endl;
    }

    {
        auto kskey = cc.GetSwitchKey();
        std::ostringstream kskeystring;
        lbcrypto::Serial::Serialize(kskey, kskeystring, lbcrypto::SerType::BINARY);
        std::cout << "KeySwitchingKeySize: " << kskeystring.str().size() << std::endl;
    }


    // Sample Program: Step 3: Encryption

    auto p   = 2 * num_of_inputs;
    std::vector<LWECiphertext> cts(num_of_inputs);
    for (auto&& ct : cts)
        ct = cc.Encrypt(sk, 0, SMALL_DIM, p);

    {
        std::ostringstream ctstring;
        lbcrypto::Serial::Serialize(cts.front(), ctstring, lbcrypto::SerType::BINARY);
        std::cout << "CiphertextSize: " << ctstring.str().size() << std::endl;
    }


    // Sample Program: Step 4: Evaluation

    const auto eq2  = num_of_inputs == 2;
    const auto gate = gtable.at(num_of_inputs);

    TIC(t);
    LWEPlaintext result;
    auto fcnt = 0;
    for (uint32_t i = 0; i < num_of_runs; ++i) {
        for (auto&& ct : cts)
            ct = cc.Encrypt(sk, 0, SMALL_DIM, p);

        auto ct = eq2 ? cc.EvalBinGate(gate, cts[0], cts[1]) : cc.EvalBinGate(gate, cts);

        cc.Decrypt(sk, ct, &result, p);

        if (result != 0)
            ++fcnt;

    }
    std::cout << "EvalBinGateTime: " << (TOC_MS(t)/num_of_runs) << " milliseconds" << std::endl;
    std::cout << "Gate: " << gate << std::endl;
    std::cout << "Failures: " << fcnt << std::endl;
    std::cout << "ctmodq: " << cc.GetParams()->GetLWEParams()->Getq() << std::endl;

    return 0;
}
