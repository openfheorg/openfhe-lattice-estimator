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
  Argument parsing shared by the measurement programs.

  Extracted so the named-parameter-set table exists exactly once: two copies of it
  drift the moment OpenFHE adds an enumerator, and a program that silently lacks a
  set would look like a measurement gap rather than a missing table entry.

  Everything here is strict on purpose. These programs feed a calibration, and a
  silently mis-parsed argument is fitted as physics -- so a bad argument is an
  exception with the offending text in it, never a default.
*/

#ifndef OPENFHE_LATTICE_ESTIMATOR_BINFHE_CLI_H
#define OPENFHE_LATTICE_ESTIMATOR_BINFHE_CLI_H

#include "binfhecontext.h"
#include "utils/serial.h"
#include "utils/sertype.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>

namespace estimator {

using namespace lbcrypto;

inline uint64_t parse_uint(const char* s, const char* name, uint64_t hi) {
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

inline uint32_t parse_u32(const char* s, const char* name) {
    return static_cast<uint32_t>(parse_uint(s, name, std::numeric_limits<uint32_t>::max()));
}

inline double parse_double(const char* s, const char* name) {
    errno     = 0;
    char* end = nullptr;
    double v  = std::strtod(s ? s : "", &end);
    if ((s == nullptr) || (end == s) || (*end != '\0') || (errno == ERANGE) || !(v > 0.0))
        OPENFHE_THROW(std::string("--") + name + ": expected a positive number, got '" +
                      (s ? s : "(none)") + "'");
    return v;
}

// "base:count,base:count" -> the map OpenFHE wants. Parsed strictly: the whole
// point of this option is calibrating the per-coefficient accumulator model, and
// a silently mis-parsed split would be fitted as physics.
inline std::map<uint32_t, uint32_t> parse_gadget_map(const char* s) {
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

// The named sets come from the LIBRARY, not from a copy of its list here. This
// used to be a hand-written map of 45 names, which was every set OpenFHE had when
// it was written; the library's table is now 109 rows, so the harness could not
// address 64 of them -- every AP row but one, and every re-selected LPF row. A
// `-p` for one of those failed as "unknown --param-set", which reads as a typo
// rather than as a stale transcription. Same rule as the shipped-set table on the
// Python side: parse the library, never transcribe it.
//
// BINFHE_PARAMSET_LIST is the library's own macro list, so expanding it here for
// the error message keeps that message exhaustive without a second source of
// truth. convertToBINFHE_PARAMSET is the library's own parser.
inline const std::string& paramset_names() {
    static const std::string names = [] {
        std::string out;
#define BINFHE_CLI_PARAMSET_NAME(name, methods) out += (out.empty() ? "" : " "); out += #name;
        BINFHE_PARAMSET_LIST(BINFHE_CLI_PARAMSET_NAME)
#undef BINFHE_CLI_PARAMSET_NAME
        return out;
    }();
    return names;
}

// Unknown -p is a hard error listing the valid names: a typo that fell through to
// the hand-built parameters would be measured and recorded under the wrong label.
inline BINFHE_PARAMSET lookup_paramset(const std::string& name) {
    try {
        return convertToBINFHE_PARAMSET(name);
    }
    catch (const std::exception&) {
        OPENFHE_THROW("unknown --param-set '" + name + "'; known sets are: " +
                      paramset_names());
    }
}

inline BINFHE_METHOD method_from_index(uint32_t i) {
    switch (i) {
        case 1:  return AP;
        case 2:  return GINX;
        case 3:  return LMKCDEY;
        default: OPENFHE_THROW("invalid bootstrapping technique " + std::to_string(i) +
                               "; expected 1 (AP), 2 (GINX) or 3 (LMKCDEY)");
    }
}

inline SecretKeyDist keydist_from_index(uint32_t i) {
    switch (i) {
        case 0:  return GAUSSIAN;
        case 1:  return UNIFORM_TERNARY;
        default: OPENFHE_THROW("invalid secret key distribution " + std::to_string(i) +
                               "; expected 0 (GAUSSIAN) or 1 (UNIFORM_TERNARY)");
    }
}

inline const char* keydist_name(SecretKeyDist d) {
    return (d == GAUSSIAN) ? "GAUSSIAN" : "UNIFORM_TERNARY";
}

// The signed representative of v mod q, i.e. the value in (-q/2, q/2].
// Secret coefficients and rounding errors are both stored as residues, and their
// UNSIGNED sums are meaningless -- every bias figure below needs this.
inline double centered(const NativeInteger& v, const NativeInteger& q) {
    double x  = v.ConvertToDouble();
    double qd = q.ConvertToDouble();
    return (x > qd / 2.0) ? (x - qd) : x;
}

// ---------------------------------------------------------------------------
// Key material a context holds, in bytes, and which form was measured.
//
// Two library behaviours meet here. Up to OpenFHE 94229558, GetRefreshKey() and
// GetSwitchKey() widened a resident 32-bit key into a 64-bit copy on demand, so
// serializing them always measured the 64-bit form: twice the resident footprint
// of an internal32 key. From the commits after it they return the 64-bit member
// as held, which is NULL whenever the 32-bit form is the resident one (the
// default), and a serialized null pointer is five bytes -- which is what these
// harnesses printed for every key at pin 12858277 until this helper.
//
// So a key held at 32 bits is measured by the library's own KeyBytes() on its
// resident arrays, reached through the key map: BTKeyGen stores the context's
// key there under its gadget base at every pin this tree is compiled against,
// whereas GetBTKey() exists only after 94229558. A key not held at 32 bits is
// measured as its 64-bit serialized size, the only form it has. Either way the
// number is the RESIDENT footprint, which is what the model's key_word_bytes
// prices, and the form string says which was taken. gatetime-ab.sh compiles
// this one tree against two images, so nothing here may depend on an accessor
// that only one pin has.
// ---------------------------------------------------------------------------
struct KeySizes {
    uint64_t btkey = 0;
    uint64_t ksk   = 0;
    std::string btkey_form;
    std::string ksk_form;
};

template <class Key>
inline uint64_t serialized_bytes(const Key& key) {
    std::ostringstream ss;
    lbcrypto::Serial::Serialize(key, ss, lbcrypto::SerType::BINARY);
    return static_cast<uint64_t>(static_cast<std::streamoff>(ss.tellp()));
}

inline KeySizes key_sizes(const lbcrypto::BinFHEContext& cc) {
    KeySizes out;
#if NATIVEINT != 32
    const bool bs32 = cc.HasInternal32RefreshKey();
    const bool ks32 = cc.HasInternal32SwitchKey();
    if (bs32 || ks32) {
        // The switching key is one object shared by every map entry; a
        // multi-base context holds one refresh key per entry. Count each once.
        // GetBTKeyMap() returns a fresh shared_ptr to a COPY of the map, so it
        // has to be held for the loop: `for (kv : *cc.GetBTKeyMap())` would
        // iterate a map freed at the end of the range initialiser.
        const auto map = cc.GetBTKeyMap();
        std::set<const void*> seen;
        for (const auto& kv : *map) {
            const auto& k = kv.second;
            if (bs32 && k.BSkey32 && seen.insert(k.BSkey32.get()).second)
                out.btkey += k.BSkey32->KeyBytes();
            if (ks32 && k.KSkey32 && seen.insert(k.KSkey32.get()).second)
                out.ksk += k.KSkey32->KeyBytes();
        }
        if (bs32)
            out.btkey_form = "32-bit resident (KeyBytes)";
        if (ks32)
            out.ksk_form = "32-bit resident (KeyBytes)";
    }
#endif
    if (out.btkey_form.empty()) {
        out.btkey      = serialized_bytes(cc.GetRefreshKey());
        out.btkey_form = "64-bit serialized";
    }
    if (out.ksk_form.empty()) {
        out.ksk      = serialized_bytes(cc.GetSwitchKey());
        out.ksk_form = "64-bit serialized";
    }
    return out;
}

}  // namespace estimator

#endif
