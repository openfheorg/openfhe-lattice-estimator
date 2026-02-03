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
#include "binfhecontext.h"
#include "utils/sertype.h"
#include "utils/serial.h"
#include <getopt.h>

using namespace lbcrypto;

uint32_t dim_n  = 0;
int64_t Qks     = 0;
uint32_t dim_N  = 0;
uint32_t ctmodq = 0;
uint32_t logQ   = 0;
uint32_t B_g    = 0;
uint32_t B_ks   = 0;
uint32_t B_rk   = 32;
double sigma    = 3.19;
uint32_t bootstrapping_technique = 0;
uint32_t secret_dist = 0;

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
                       "  -h display this message\n"
                     );
}

int main(int argc, char* argv[]) {
    // Sample Program: Step 1: Set CryptoContext
    TimeVar t;
    auto cc = BinFHEContext();

    char opt(0);
    //*********************
    static struct option long_options[] = {{"Lattice dimension", required_argument, NULL, 'n'},
                                           {"Ring dimension", required_argument, NULL, 'N'},
                                           {"ct modulus", required_argument, NULL, 'q'},
                                           {"size of ring modulus", required_argument, NULL, 'Q'},
                                           {"size of kew switching mod Qks", required_argument, NULL, 'k'},
                                           {"Digit base B_g", required_argument, NULL, 'g'},
                                           {"Refreshing key base B_rk", required_argument, NULL, 'r'},
                                           {"Key switching base B_ks", required_argument, NULL, 'b'},
                                           {"sigma (standard deviation)", required_argument, NULL, 's'},
                                           {"Bootstrapping technique", required_argument, NULL, 't'},
                                           {"Secret key distribution", required_argument, NULL, 'd'},
                                           {"help", no_argument, NULL, 'h'},
                                           {NULL, 0, NULL, 0}};

    const char* optstring = "n:N:q:Q:k:g:r:b:s:t:d:h";
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
                sigma = atoi(optarg);
                break;
            case 't':
                bootstrapping_technique = atoi(optarg);
                break;
            case 'd':
                secret_dist = atoi(optarg);
                break;
            case 'h':
            default:
                OPENFHE_THROW(usage());
        }
    }

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
    paramset.numAutoKeys = 10;

    if (secret_dist == 0) {
        paramset.keyDist = GAUSSIAN;
    } else if (secret_dist == 1) {
        paramset.keyDist = UNIFORM_TERNARY;
    } else {
        OPENFHE_THROW("Invalid Secret Key distribution");
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
        OPENFHE_THROW("Invalid bootstrapping technique");
    }
    cc.GenerateBinFHEContext(paramset, bt);

    // Sample Program: Step 2: Key Generation

    // Generate the secret key
    auto sk = cc.KeyGen();

    std::cout << "Generating the bootstrapping keys..." << std::endl;

    TIC(t);
    // Generate the bootstrapping keys (refresh and switching keys)
    cc.BTKeyGen(sk);

    auto es = TOC_MS(t);
    std::cout << "time for bootstrapping key generation " << es << " milliseconds" << std::endl;

    auto bkey  = cc.GetRefreshKey();
    auto kskey = cc.GetSwitchKey();
    std::ostringstream bkeystring;
    lbcrypto::Serial::Serialize(bkey, bkeystring, lbcrypto::SerType::BINARY);
    std::cout << "bootstrapping key size: " << bkeystring.str().size() << std::endl;

    std::ostringstream kskeystring;
    lbcrypto::Serial::Serialize(kskey, kskeystring, lbcrypto::SerType::BINARY);
    std::cout << "key switching key size: " << kskeystring.str().size() << std::endl;

    std::cout << "Completed the key generation." << std::endl;

    // Sample Program: Step 3: Encryption

    // Encrypt two ciphertexts representing Boolean True (1).
    // By default, freshly encrypted ciphertexts are bootstrapped.
    // If you wish to get a fresh encryption without bootstrapping, write
    // auto   ct1 = cc.Encrypt(sk, 1, FRESH);
    auto p   = 6;
    auto ct1 = cc.Encrypt(sk, 1, SMALL_DIM, p);
    auto ct2 = cc.Encrypt(sk, 1, SMALL_DIM, p);
    auto ct3 = cc.Encrypt(sk, 0, SMALL_DIM, p);
    auto ct4 = cc.Encrypt(sk, 0, SMALL_DIM, p);
    auto ct5 = cc.Encrypt(sk, 1, SMALL_DIM, p);
    auto ct6 = cc.Encrypt(sk, 0, SMALL_DIM, p);

    std::ostringstream ctstring;
    lbcrypto::Serial::Serialize(ct1, ctstring, lbcrypto::SerType::BINARY);
    std::cout << "ciphertext size: " << ctstring.str().size() << std::endl;
    std::cout << "ciphertext modulus: " << ct1->GetModulus() << std::endl;
    std::cout << "ciphertext dimension n: " << ct1->GetLength() << std::endl;

    std::vector<LWECiphertext> ct123, ct134, ct125, ct346;

    ct123.push_back(ct1);
    ct123.push_back(ct2);
    ct123.push_back(ct3);

    ct134.push_back(ct1);
    ct134.push_back(ct3);
    ct134.push_back(ct4);

    ct125.push_back(ct1);
    ct125.push_back(ct2);
    ct125.push_back(ct5);

    ct346.push_back(ct3);
    ct346.push_back(ct4);
    ct346.push_back(ct6);

    // Sample Program: Step 4: Evaluation
    TIC(t);
    // 1, 0, 0
    auto ctAND1 = cc.EvalBinGate(AND3, ct134);

    // 1, 1, 0
    auto ctAND2 = cc.EvalBinGate(AND3, ct123);

    // 1, 1, 1
    auto ctAND3 = cc.EvalBinGate(AND3, ct125);

    // 0, 0, 0
    auto ctAND4 = cc.EvalBinGate(AND3, ct346);

    // 1, 0, 0
    auto ctOR1 = cc.EvalBinGate(OR3, ct134);
    // 1, 1, 0
    auto ctOR2 = cc.EvalBinGate(OR3, ct123);

    // 1, 1, 1
    auto ctOR3 = cc.EvalBinGate(OR3, ct125);

    // 1, 1, 1
    auto ctOR4 = cc.EvalBinGate(OR3, ct346);

    es = TOC_MS(t);
    std::cout << "time for gate evaluation " << es << " milliseconds" << std::endl;

    LWEPlaintext result;

    cc.Decrypt(sk, ctAND1, &result, p);
    std::cout << "Result of encrypted computation of AND(1, 0, 0) = " << result << std::endl;
    if (result != 0)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctAND2, &result, p);
    std::cout << "Result of encrypted computation of AND(1, 1, 0) = " << result << std::endl;
    if (result != 0)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctAND3, &result, p);
    std::cout << "Result of encrypted computation of AND(1, 1, 1) = " << result << std::endl;
    if (result != 1)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctAND4, &result, p);
    std::cout << "Result of encrypted computation of AND(0, 0, 0) = " << result << std::endl;
    if (result != 0)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctOR1, &result, p);
    std::cout << "Result of encrypted computation of OR(1, 0, 0) = " << result << std::endl;
    if (result != 1)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctOR2, &result, p);
    std::cout << "Result of encrypted computation of OR(1, 1, 0) = " << result << std::endl;
    if (result != 1)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctOR3, &result, p);
    std::cout << "Result of encrypted computation of OR(1, 1, 1) = " << result << std::endl;
    if (result != 1)
        OPENFHE_THROW("Decryption failure");

    cc.Decrypt(sk, ctOR4, &result, p);
    std::cout << "Result of encrypted computation of OR(0, 0, 0) = " << result << std::endl;
    if (result != 0)
        OPENFHE_THROW("Decryption failure");

    return 0;
}
