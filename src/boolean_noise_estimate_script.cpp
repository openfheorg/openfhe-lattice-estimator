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

// lookup_paramset asks the LIBRARY which sets exist, so this binary can address
// every row of its table rather than a list transcribed when there were 45.
#include "binfhe_cli.h"
#include "utils/memory.h"
#include "utils/sertype.h"
#include "utils/serial.h"
#include <getopt.h>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <map>
#include <sstream>
#include <string>
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

// "base:count,base:count" -> the map OpenFHE wants. Parsed strictly: the whole
// point of this option is calibrating the per-coefficient accumulator model, and
// a silently mis-parsed split would be fitted as physics.
static std::map<uint32_t, uint32_t> parse_gadget_map(const char* s) {
    std::map<uint32_t, uint32_t> out;
    std::stringstream ss(s ? s : "");
    std::string item;
    while (std::getline(ss, item, ',')) {
        auto colon = item.find(':');
        if (colon == std::string::npos)
            OPENFHE_THROW("--gadget-map: expected \"base:count\" pairs, got '" + item + "'");
        auto base  = parse_uint(item.substr(0, colon).c_str(), "gadget-map base",
                                std::numeric_limits<uint32_t>::max());
        auto count = parse_uint(item.substr(colon + 1).c_str(), "gadget-map count",
                                std::numeric_limits<uint32_t>::max());
        if ((base < 2) || ((base & (base - 1)) != 0))
            OPENFHE_THROW("--gadget-map: base must be a power of two >= 2, got " +
                          std::to_string(base));
        if (count == 0)
            OPENFHE_THROW("--gadget-map: count must be > 0 for base " + std::to_string(base));
        if (!out.emplace(static_cast<uint32_t>(base), static_cast<uint32_t>(count)).second)
            OPENFHE_THROW("--gadget-map: base " + std::to_string(base) + " given twice");
    }
    if (out.empty())
        OPENFHE_THROW("--gadget-map: no base:count pairs parsed");
    return out;
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
                       "  -G per-dimension gadget map, \"base:count,base:count\" (counts must sum to -n)\n"
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
                       "  -3 32-bit internal key forms where they fit (Q <= 2^28); the DEFAULT since OpenFHE 9e8045db; no-op on NATIVE_SIZE=32\n"
                       "  -6 force the 64-bit key forms (the A/B against -3; noise is bit-identical either way)\n"
                       "  -h display this message\n"
                     );
}

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
    std::map<uint32_t, uint32_t> gadgetBaseMap;
    // The runtime-NS32 hybrid: a bootstrapping key held internally at 32 bits
    // inside a 64-bit build, for Q <= 2^28 (OpenFHE f56c301b). It was opt-in
    // until 9e8045db; BTKeyGen's internal32 now defaults to TRUE, and this
    // default follows the library's so that "no flag" measures what a default
    // caller gets. Each key is still gated on its own Fits() check, so a
    // configuration can hold the 32-bit form for the switching key and not the
    // accumulator key (every shipped set does: qKS is 2^14..2^17 whatever Q is).
    // -3 asks for the 32-bit forms explicitly (kept for old plans), -6 forces
    // the 64-bit forms -- the A/B against the old path needs it now.
    bool internal32 = true;
    std::string namedparamset;

    static struct option long_options[] = {{"lattice-dimension", required_argument, NULL, 'n'},
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
                                           {"num-gate-inputs", required_argument, NULL, 'I'},
                                           {"num-iterations", required_argument, NULL, 'i'},
                                           {"num-keys", required_argument, NULL, 'K'},
                                           {"param-set", required_argument, NULL, 'p'},
                                           {"no-sizes", no_argument, NULL, 'Z'},
                                           {"internal32", no_argument, NULL, '3'},
                                           {"internal64", no_argument, NULL, '6'},
                                           {"help", no_argument, NULL, 'h'},
                                           {NULL, 0, NULL, 0}};

    int opt(0);
    const char* optstring = "n:N:q:Q:k:g:G:r:b:s:t:d:a:I:i:K:p:Z36h";
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
            case 'G':
                gadgetBaseMap = parse_gadget_map(optarg);
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
            case '3':
                internal32 = true;
                break;
            case '6':
                internal32 = false;
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

    // Validate the name up front rather than at context construction: a run that
    // dies after keygen has already spent the expensive part.
    if (!namedparamset.empty())
        (void)estimator::lookup_paramset(namedparamset);

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

    if (!gadgetBaseMap.empty()) {
        uint64_t covered = 0;
        for (auto&& kv : gadgetBaseMap)
            covered += kv.second;
        // OpenFHE throws "Gadget base map does not cover the LWE dimension" lazily
        // at keygen; say it here, with the numbers.
        if (covered != dim_n)
            OPENFHE_THROW("--gadget-map counts sum to " + std::to_string(covered) +
                          " but -n is " + std::to_string(dim_n) +
                          "; the map must cover the LWE dimension exactly");
        paramset.gadgetBaseMap = gadgetBaseMap;
        // gadgetBase stays the default base (LMKCDEY's automorphism keys use it)
        if (B_g == 0)
            paramset.gadgetBase = gadgetBaseMap.begin()->first;
    }

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

    // Fills a caller-owned context rather than returning one. BinFHEContext is
    // neither copyable nor movable as of 1c3e83ec: it holds a
    // `mutable std::mutex m_widenMutex` (binfhecontext.h:615) guarding the lazy
    // 64-bit widening in GetRefreshKey()/GetSwitchKey(), and a mutex member
    // deletes both the copy and the move constructor. Returning by value here
    // therefore stopped compiling ("call to implicitly-deleted copy
    // constructor"). Do not "simplify" this back to a return.
    auto make_context = [&](BinFHEContext& c) {
        if (!namedparamset.empty())
            c.GenerateBinFHEContext(estimator::lookup_paramset(namedparamset), bt);
        else
            c.GenerateBinFHEContext(paramset, bt);
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
        BinFHEContext cc;
        make_context(cc);
        ctmod = cc.GetParams()->GetLWEParams()->Getq();

        TIC(t);
        auto sk = cc.KeyGen();
        cc.BTKeyGen(sk, SYM_ENCRYPT, internal32);
        keygen_ms += TOC_MS(t);

        std::vector<LWECiphertext> cts(num_of_inputs);
        for (auto&& ct : cts)
            ct = cc.Encrypt(sk, 0, SMALL_DIM, p);

        // Measuring a 64-bit key costs a full serialized copy of it, which for a
        // multi-GB key-switching key dwarfs everything else the process holds; a
        // 32-bit key is counted in place (estimator::key_sizes). Only one run per
        // candidate needs the sizes (the speed probe), so the parallel noise runs
        // pass -Z and skip it entirely.
        if ((k == 0) && report_sizes) {
            const auto sizes = estimator::key_sizes(cc);
            std::cout << "BootstrappingKeySize: " << sizes.btkey << std::endl;
            std::cout << "KeySwitchingKeySize: " << sizes.ksk << std::endl;
            std::cout << "KeyForm: refresh " << sizes.btkey_form << ", switch " << sizes.ksk_form << std::endl;
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
