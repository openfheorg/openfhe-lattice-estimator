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
#include <vector>
#include <algorithm>
#include <chrono>
#include "utils/sertype.h"
#include "utils/serial.h"
#include "binfhe_cli.h"
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
uint32_t bootstrapping_technique = 2;
uint32_t secret_dist = 0;
// numAutoKeys was hardcoded to 10, which made c3 -- the LMKCDEY automorphism
// term the cost model already carries -- impossible to calibrate with this
// binary at all.
uint32_t numAutoKeys = 10;
// Single -g cannot express a split map, and the search now proposes them (the
// best STD192Q candidate is {2^12:240, 2^18:720}), so their cost was
// unmeasurable here.
std::map<uint32_t, uint32_t> gadgetBaseMap;
// Follows the LIBRARY default, which flipped at 9e8045db: BTKeyGen(sk, mode,
// internal32 = true). A 64-bit build now narrows each bootstrapping key to the
// 32-bit internal form whenever that key's own moduli fit, with no flag and no
// rebuild, so "no option given" here measures what a default caller gets.
// -3 asks for it explicitly (a no-op now, kept so old plans still parse) and -6
// forces the 64-bit forms, which is the only way left to time that path at
// Q <= 2^28. See the note in boolean_noise_estimate_script.
bool internal32 = true;
// Timing a shipped set meant retyping its whole row and risking a transcription
// error against the very table being compared to.
std::string namedparamset;

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
                       "  -a number of auto keys (LMKCDEY)\n"
                       "  -G per-dimension gadget map, \"base:count,base:count\" (counts must sum to -n)\n"
                       "  -p label for named binfhe param set (overrides other settings)\n"
                       "  -3 32-bit internal key forms where they fit (Q <= 2^28); the DEFAULT since OpenFHE 9e8045db\n"
                       "  -6 force the 64-bit key forms (times the pre-9e8045db path; the A/B against -3)\n"
                       "  -h display this message\n"
                     );
}

int main(int argc, char* argv[]) {
    // Sample Program: Step 1: Set CryptoContext
    TimeVar t;
    auto cc = BinFHEContext();

    int opt(0);
    // *********************
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
                                           {"num auto keys", required_argument, NULL, 'a'},
                                           {"gadget base map", required_argument, NULL, 'G'},
                                           {"internal32", no_argument, NULL, '3'},
                                           {"internal64", no_argument, NULL, '6'},
                                           {"named paramset", required_argument, NULL, 'p'},
                                           {"help", no_argument, NULL, 'h'},
                                           {NULL, 0, NULL, 0}};

    const char* optstring = "n:N:q:Q:k:g:r:b:s:t:d:a:G:p:36h";
    while ((opt = getopt_long(argc, argv, optstring, long_options, NULL)) != -1) {
        std::cout << "opt1: " << static_cast<char>(opt) << "; optarg: " << (optarg ? optarg : "(none)") << std::endl;
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
                sigma = atof(optarg);
                break;
            case 't':
                bootstrapping_technique = atoi(optarg);
                break;
            case 'd':
                secret_dist = atoi(optarg);
                break;
            case 'a':
                numAutoKeys = estimator::parse_u32(optarg, "num-auto-keys");
                break;
            case '3':
                internal32 = true;
                break;
            case '6':
                internal32 = false;
                break;
            case 'G':
                gadgetBaseMap = estimator::parse_gadget_map(optarg);
                break;
            case 'p':
                namedparamset = optarg;
                break;
            case 'h':
                std::cout << usage() << std::endl;
                return 0;
            default:
                std::cerr << usage() << std::endl;
                return 1;
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
    paramset.numAutoKeys  = numAutoKeys;

    // A split map is only meaningful alongside the base it splits from, so -G
    // supplies gadgetBase too when -g was not given -- mirroring
    // boolean_noise_estimate_script, so the two harnesses cannot disagree about
    // what a given command line means.
    if (!gadgetBaseMap.empty()) {
        uint32_t covered = 0;
        for (auto&& kv : gadgetBaseMap)
            covered += kv.second;
        if (covered != dim_n)
            OPENFHE_THROW("--G counts sum to " + std::to_string(covered) + ", not -n " +
                          std::to_string(dim_n));
        paramset.gadgetBaseMap = gadgetBaseMap;
        if (B_g == 0)
            paramset.gadgetBase = gadgetBaseMap.begin()->first;
    }

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
    // -p overrides everything above, so a shipped set can be timed without
    // retyping its row -- transcribing 12 fields by hand to time the very table
    // you are comparing against is a needless way to measure the wrong thing.
    if (!namedparamset.empty()) {
        std::cout << "parameters from commandline overridden with: " << namedparamset << std::endl;
        cc.GenerateBinFHEContext(estimator::lookup_paramset(namedparamset), bt);
    }
    else {
        cc.GenerateBinFHEContext(paramset, bt);
    }

    // Sample Program: Step 2: Key Generation

    // Generate the secret key
    auto sk = cc.KeyGen();

    std::cout << "Generating the bootstrapping keys..." << std::endl;

    TIC(t);
    // Generate the bootstrapping keys (refresh and switching keys)
    cc.BTKeyGen(sk, SYM_ENCRYPT, internal32);

    auto es = TOC_MS(t);
    std::cout << "time for bootstrapping key generation " << es << " milliseconds" << std::endl;

    // HasInternal32*Key() never widens, so it is the safe way to ask which form
    // the context holds.
    std::cout << "internal32 refresh key: "
              << (cc.HasInternal32RefreshKey() ? "yes" : "no") << std::endl;
    std::cout << "internal32 switch key: "
              << (cc.HasInternal32SwitchKey() ? "yes" : "no") << std::endl;

    // Resident key material: a 32-bit key by the library's own byte count of its
    // arrays, a 64-bit key by its serialized size. estimator::key_sizes says why
    // the two have to be measured differently and why serializing the getters
    // stopped working after OpenFHE 94229558. This is the footprint the model's
    // key_word_bytes prices, so the table's key column and these lines describe
    // the same quantity; gatetime_ab.py reads them as the check that a pin move
    // landed. Nothing is widened or copied for a 32-bit key, so there is nothing
    // to release afterwards (and CompressBTKeys() is private in any case).
    const auto sizes = estimator::key_sizes(cc);
    std::cout << "bootstrapping key size: " << sizes.btkey << std::endl;
    std::cout << "key switching key size: " << sizes.ksk << std::endl;
    std::cout << "key form: refresh " << sizes.btkey_form << ", switch " << sizes.ksk_form << std::endl;

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
    std::cout << "ciphertext size: " << static_cast<std::streamoff>(ctstring.tellp()) << std::endl;
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
    // Per-gate timings alongside the mean. A mean over 8 gates cannot tell
    // "every gate is slower" from "one gate stalled", and a min-based
    // measurement elsewhere is immune to the second -- so report both.
    std::vector<double> _per;
    std::chrono::steady_clock::time_point _gt0;
    TIC(t);
    // 1, 0, 0
    _gt0 = std::chrono::steady_clock::now();

    auto ctAND1 = cc.EvalBinGate(AND3, ct134);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    // 1, 1, 0
    _gt0 = std::chrono::steady_clock::now();

    auto ctAND2 = cc.EvalBinGate(AND3, ct123);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    // 1, 1, 1
    _gt0 = std::chrono::steady_clock::now();

    auto ctAND3 = cc.EvalBinGate(AND3, ct125);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    // 0, 0, 0
    _gt0 = std::chrono::steady_clock::now();

    auto ctAND4 = cc.EvalBinGate(AND3, ct346);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    // 1, 0, 0
    _gt0 = std::chrono::steady_clock::now();

    auto ctOR1 = cc.EvalBinGate(OR3, ct134);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());
    // 1, 1, 0
    _gt0 = std::chrono::steady_clock::now();

    auto ctOR2 = cc.EvalBinGate(OR3, ct123);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    // 1, 1, 1
    _gt0 = std::chrono::steady_clock::now();

    auto ctOR3 = cc.EvalBinGate(OR3, ct125);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    // 1, 1, 1
    _gt0 = std::chrono::steady_clock::now();

    auto ctOR4 = cc.EvalBinGate(OR3, ct346);

    _per.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - _gt0).count());

    es = TOC_MS(t);
    std::cout << "time for gate evaluation " << es << " milliseconds" << std::endl;
    if (!_per.empty()) {
        auto mn = *std::min_element(_per.begin(), _per.end());
        auto mx = *std::max_element(_per.begin(), _per.end());
        double sum = 0.0;
        for (double v : _per) sum += v;
        std::cout << "per-gate us:";
        for (double v : _per) std::cout << " " << static_cast<long>(v);
        std::cout << std::endl;
        std::cout << "gate us min " << static_cast<long>(mn)
                  << " mean " << static_cast<long>(sum / _per.size())
                  << " max " << static_cast<long>(mx)
                  << " skew " << (mn > 0 ? static_cast<long>(100 * mx / mn) : 0L)
                  << "%" << std::endl;
    }

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
