#!/usr/bin/env python3
"""Correctness tests for the DSE modules. `dse.py doctor` checks the ENVIRONMENT;
this checks the arithmetic.

    sage -python tests/test_dse.py          (inside the container)
    python3 tests/test_dse.py               (any python3 with scipy)

Every test here encodes something that was once wrong. That is the selection
criterion: a regression test earns its place by having caught a real defect, not
by covering a line.
"""
import os
import json
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'scripts', 'paramsestimator'))

FAILED = []


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def check(name, got, want, tol=None):
    ok = (abs(got - want) <= tol) if tol is not None else (got == want)
    print("  %-4s %-52s got %s" % ("ok" if ok else "FAIL", name, got)
          + ("" if ok else "  want %s%s" % (want, "" if tol is None else " +-%s" % tol)))
    if not ok:
        FAILED.append(name)


def main():
    import dse_model as m
    import dse_constraints as c
    import dse_shipfit as sf

    print("digit counts agree with OpenFHE's integer GetDigitCount")
    # nbtheory.h:208, power-of-two fast path. An earlier docstring here claimed no
    # exact GetDigitCount existed and that OpenFHE used floating point; that was
    # true of one pin and false of the next.
    def cpp(x, base):
        if x < 2:
            return 0
        if base & (base - 1) == 0:
            bits = base.bit_length() - 1
            return ((x - 1).bit_length() + bits - 1) // bits
        d, acc = 0, 1
        while acc < x:
            acc *= base; d += 1
        return d
    bad = [(lq, k) for lq in range(8, 61) for k in range(1, 21)
           if m.digits_for_base(lq, 1 << k) != cpp(1 << lq, 1 << k)]
    check("power-of-two bases, logQ 8..60", len(bad), 0)
    check("baseKS=25 (TOY, SIGNED_MOD_TEST use it)",
          m.digits_for_base(15, 25, modulus=1 << 15), cpp(1 << 15, 25))

    print("\nthe accumulator shape is read off SignedDigitDecompose, not fitted")
    # gadget_shape(Q,b) = (d-2)*b^2 + min(b, Q/b^(d-1))^2. Over-coverage IS the
    # top digit's range; treating it as a correction gave a 35% spread in k.
    check("gadget_shape(27, 2^7)", m.gadget_shape(27, 128), 36864.0, tol=1.0)
    check("digits_g2 = 2*(d-1), the allocated RGSW rows", m.digits_g2(4), 6)

    print("\nfailure probability uses p = 2*inputs")
    # The decision window narrows q/4 -> q/6 -> q/8 with arity. Reporting a _3 or
    # _4 set at inputs=2 overstates its margin by 150 to 800 bits.
    s = 10.0
    check("log2_pf is monotone in arity (3 worse than 2)",
          m.log2_pf(s, 3, 2048) > m.log2_pf(s, 2, 2048), True)
    check("log2_pf is monotone in arity (4 worse than 3)",
          m.log2_pf(s, 4, 2048) > m.log2_pf(s, 3, 2048), True)
    check("designed_arity(STD256Q_4)", sf.designed_arity('STD256Q_4'), 4)
    check("designed_arity(STD128_3_LMKCDEY)", sf.designed_arity('STD128_3_LMKCDEY'), 3)
    check("designed_arity(STD128)", sf.designed_arity('STD128'), 2)

    print("\nconstraints that encode a measured crash or a wrong answer")
    # GINX stores the LWE secret as an indicator pair over {-1,0,+1}: a Gaussian
    # secret loses 63.7% of coefficients and the gate returns a clean, low-noise
    # encryption of the WRONG bit. 202 failures in 400, sigma only 25% high.
    check("GINX + GAUSSIAN refused", c.method_keydist_ok('GINX', 'GAUSSIAN'), False)
    check("LMKCDEY + GAUSSIAN allowed", c.method_keydist_ok('LMKCDEY', 'GAUSSIAN'), True)
    # LMKCDEY indexes an n-sized automorphism vector by numAutoKeys; w == n writes
    # one past the end. Measured: n=64 works to w=63 and SIGSEGVs at 64.
    check("autokeys w = n-1 allowed", c.autokeys_ok('LMKCDEY', 63, 64), True)
    check("autokeys w = n refused", c.autokeys_ok('LMKCDEY', 64, 64), False)
    check("autokeys w = 0 refused", c.autokeys_ok('LMKCDEY', 0, 64), False)
    # NS32 caps the modulus at 28 bits, not 32.
    check("logQ 28 fits a 32-bit word", c.modulus_fits_word(28, 32), True)
    check("logQ 29 does not", c.modulus_fits_word(29, 32), False)

    print("\nthe 32-bit switching key follows the library's own modulus cap")
    # Transcribed from LWESwitchingKey32Impl::Fits at 09913224, which caps qKS at
    # MAX_MODULUS_SIZE32 = 28 rather than the 32 of the storage word: generation
    # runs on 32-bit kernels that are exact only to 28 bits. These are the four
    # points the library's own UnitTestFHEWNativeSize pins.
    check("2^28 - 57 fits", c.hybrid_switch32_ok(512, 2**28 - 57, 32), True)
    check("2^28 does not", c.hybrid_switch32_ok(512, 2**28, 32), False)
    check("2^31 + 11 does not", c.hybrid_switch32_ok(512, 2**31 + 11, 32), False)
    check("2^32 - 5 does not", c.hybrid_switch32_ok(512, 2**32 - 5, 32), False)
    check("the cap is the library's constant, not the word width",
          c.MAX_MODULUS_SIZE32, 28)
    # A shipped geometry is far below the cap, so the change moves no pick -- and
    # the direction matters: correcting it makes a large-qKS candidate MORE
    # expensive, so it can only lose ground to a winner that was already below.
    check("every shipped qKS still narrows", c.hybrid_switch32_ok(1024, 2**15, 256), True)

    print("\nthe ciphertext modulus must divide 2N, which the library now enforces")
    check("q = N", c.ct_modulus_divides_2n(1024, 1024), True)
    check("q = 2N", c.ct_modulus_divides_2n(2048, 1024), True)
    check("q = 4N is refused (BootstrapGateCore throws)",
          c.ct_modulus_divides_2n(4096, 1024), False)
    check("a modulus that does not divide is refused",
          c.ct_modulus_divides_2n(3000, 1024), False)
    check("zero is refused rather than dividing by it",
          c.ct_modulus_divides_2n(0, 1024), False)

    print("\nruntime-NS32 hybrid qualification (reviewer Addendum 28)")
    # Coverage the reviewer states: STD128 (27), STD128Q (25), STD256Q (26)
    # qualify; STD192 (37) and STD256 (29) do not -- STD256 by one bit.
    check("STD128  logQ=27 qualifies",
          c.hybrid_ns32_ok(27, {128: 556}, 'GINX', default_base=128), True)
    check("STD256  logQ=29 does not",
          c.hybrid_ns32_ok(29, {1024: 1299}, 'GINX', default_base=1024), False)
    check("STD192  logQ=37 does not",
          c.hybrid_ns32_ok(37, {8192: 821}, 'GINX', default_base=8192), False)
    # AP and LMKCDEY gained the 32-bit path in 1b8648e9 and roughly DOUBLE on it.
    # An earlier revision of the predicate admitted GINX only -- correct for
    # f56c301b, wrong one commit later, and it silently excluded LMKCDEY from a
    # method comparison for the second time.
    check("LMKCDEY now qualifies",
          c.hybrid_ns32_ok(27, {128: 581}, 'LMKCDEY', default_base=128), True)
    check("AP now qualifies",
          c.hybrid_ns32_ok(27, {512: 559}, 'AP', default_base=512), True)
    # LMKCDEY's automorphism key switch uses the DEFAULT base regardless of the
    # per-index map, so a map that all qualifies can still be refused on it.
    # base 2^8 at logQ=27 fails excess-H: d=4, 4*8+1 = 33 > 32. (base 2 does NOT
    # fail -- d=27, 27*1+1 = 28 -- which is what an earlier version of this test
    # wrongly assumed.) The check is non-monotone in the base, so the example has
    # to be computed rather than guessed.
    check("base 2^8 at logQ=27 fails excess-H (33 > 32)",
          c.hybrid_ns32_ok(27, {256: 500}, 'GINX', default_base=256), False)
    check("LMKCDEY refused on a bad DEFAULT base despite a fine map",
          c.hybrid_ns32_ok(27, {128: 500}, 'LMKCDEY', default_base=256), False)
    check("same map+default is fine for GINX (no automorphism keys)",
          c.hybrid_ns32_ok(27, {128: 500}, 'GINX', default_base=256), True)
    # Omitting default_base for LMKCDEY leaves that check unmade, so it raises
    # rather than silently passing.
    try:
        c.hybrid_ns32_ok(27, {128: 500}, 'LMKCDEY')
        check("LMKCDEY without default_base raises", False, True)
    except ValueError:
        check("LMKCDEY without default_base raises", True, True)

    print("\nper-key scatter: two constants, opposite conservatism")
    # Overstating widens a decision band (safe) but asks for FEWER keys (unsafe),
    # so one number cannot serve both uses.
    check("bound exceeds point estimate",
          m.KEY_SCATTER_BOUND > m.KEY_SCATTER_POINT, True)

    print("\ncost model completeness")
    need = {(me, N, w) for me in ('GINX', 'LMKCDEY')
            for N in (512, 1024, 2048) for w in (32, 64)}
    check("all 12 (method, N, word) cells present",
          len(need - set(m.GATE_COST)), 0)
    # 8192, not 4096: N=4096 was measured on 2026-09-12 for the STD256Q arity-4
    # cells, so it is calibrated now and no longer an example of a refusal.
    check("gate_us refuses an uncalibrated cell rather than guessing",
          m.gate_us({128: 512}, 27, 8192, method='GINX', word_size=32), None)

    print("\ncost provenance is recorded and self-consistent")
    # A method comparison is only as good as the build both methods were timed
    # on. This exists because "LMKCDEY beats GINX" was retracted when the CGGI
    # accumulator went parallel and LMKCDEY did not -- a stale pin here would
    # invite exactly that mistake again.
    check("COST_PIN looks like a full 40-char sha",
          len(m.COST_PIN) == 40 and all(ch in '0123456789abcdef' for ch in m.COST_PIN),
          True)
    # Regimes are re-measured one at a time after a pin move and carry their own
    # pin meanwhile, so the provenance line must name the ACTIVE regime's pin,
    # which is not necessarily the table default.
    check("cost_provenance names the active regime's pin",
          m.COST_REGIMES[m.DEFAULT_REGIME]['pin'][:8] in m.cost_provenance(), True)
    check("every regime pin looks like a full sha",
          all(len(r['pin']) == 40 for r in m.COST_REGIMES.values()), True)
    check("same-method comparison gets no cross-method warning",
          any('CROSS-METHOD' in l for l in m.comparison_caveat(['GINX', 'GINX'])),
          False)
    check("cross-method comparison DOES get it",
          any('CROSS-METHOD' in l for l in m.comparison_caveat(['GINX', 'LMKCDEY'])),
          True)
    # The list may be EMPTY (nothing scheduled upstream, as at a0c3f2cd); what it
    # may not hold is a vague entry. Each one names a commit or says "queued".
    check("pending invalidations are enumerated, not vague",
          all(re.search(r'\b[0-9a-f]{7,40}\b', e) or 'queued' in e for e in m.COST_PIN_LACKS),
          True)
    check("the pin's contents are enumerated too", len(m.COST_PIN_CONTAINS) >= 3, True)

    # The image records the OpenFHE commit it installed so `doctor` can compare
    # the LIBRARY against the pin the cells carry. A force-pushed sha keeps
    # resolving, so a build can describe superseded code with nothing to show it;
    # this file is what makes that visible. Absent (native install) and malformed
    # must both read as "unknown" rather than as provenance.
    import tempfile
    import dse as door
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, 'good')
        with open(good, 'w') as f:
            f.write(m.COST_PIN + "\nsome commit subject\n")
        os.environ['OPENFHE_REF_FILE'] = good
        check("installed-ref file parses to (sha, subject)",
              door._installed_openfhe_ref(), (m.COST_PIN, "some commit subject"))
        short = os.path.join(td, 'short')
        with open(short, 'w') as f:
            f.write("238153db\n")               # abbreviated: not provenance
        os.environ['OPENFHE_REF_FILE'] = short
        check("an abbreviated sha is refused, not reported",
              door._installed_openfhe_ref(), (None, None))
        os.environ['OPENFHE_REF_FILE'] = os.path.join(td, 'absent')
        check("a missing ref file reads as unknown",
              door._installed_openfhe_ref(), (None, None))
    os.environ.pop('OPENFHE_REF_FILE', None)

    # AP's refresh base drives its noise, gate time AND key size, so a plan that
    # did not carry it measured a different configuration than was searched --
    # silently, since the run succeeds and reports a plausible sigma.
    import dse_verify as _v
    _ap = dict(n=256, q=2048, N=1024, log_q_big=27, q_ks=32768, base_ks=32,
               sigma=3.19, inputs=2, method='AP', key_dist='UNIFORM_TERNARY',
               autokeys=10, base_r=8, gadget_map={512: 256})
    check("an AP plan carries its own refresh base",
          " -r 8 " in _v.command(_ap, 1250), True)
    check("a GINX plan still defaults it",
          " -r 64 " in _v.command(dict(_ap, method='GINX', base_r=None), 1250), True)

    print("\ncomparison criterion is two-sided")
    # The exact numbers that exposed the one-sided bug: a candidate at -64.2 +-2.8
    # against a shipped point prediction of -59.6 (band 3.7). One-sided accepts it
    # and the measured pair came out 0.5 bits the WRONG way.
    check("one-sided would have accepted", (-64.2 + 2.8) <= -59.6, True)
    check("two-sided rejects it",
          m.beats_at_no_worse_pf(-64.2, 2.8, -59.6, 3.7), False)
    check("a genuinely better candidate still passes",
          m.beats_at_no_worse_pf(-90.0, 2.0, -60.0, 3.7), True)

    print("\ngadget width is the library's inequality, in one place")
    # rgsw-acc-common.h:51: digitsG*gBits + 1 <= word bits. This was spelled
    # `< 31` / `< 63`, one bit stricter, and the two copies of the rule (general
    # path and hybrid) then read differently. base 2^21 at logQ 43 gives d=3 and
    # 3*21 + 1 = 64 > 64 is false, so the library ACCEPTS it and the old rule did
    # not. Nothing moved in the search because 2^21 is dominated at d=3, but the
    # divergence is the defect, not its current blast radius.
    check("library accepts d*gbits == word-1 (base 2^21, logQ 43)",
          c.gadget_width_ok(43, 1 << 21, 64), True)
    # Computed, not guessed: my first attempt here used base 2^22 at logQ 44,
    # which gives d=2 and so fits comfortably. logQ 33 at base 2^32 gives d=2 and
    # 2*32 + 1 = 65, the first case past a 64-bit word.
    check("one bit past the word is still refused",
          c.gadget_width_ok(33, 1 << 32, 64), False)
    check("28-bit Q at gBits 5 keeps its one spare bit",
          c.gadget_width_ok(28, 32, 32), True)
    # The hybrid must not carry its own copy: it has to agree with the general
    # rule evaluated at 32 for every base, since it narrows to a 32-bit word.
    agree = all(c.hybrid_ns32_ok(27, {b: 1}, "GINX")
                == c.gadget_width_ok(27, b, 32)
                for b in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 16384))
    check("hybrid agrees with the general rule at 32", agree, True)

    print("\ncost refitting recovers what it was given")
    import dse_gatefit as gf
    # Generate rows from KNOWN coefficients using the model's own feature
    # builder, then require recovery. A refit whose features drift from the
    # predictor's produces plausible cells and wrong rankings, so this is the
    # control that matters. c3 is separated on the w-arm, so the synthetic data
    # has to carry one.
    true = (1234.5, 42.0, 7.5, 99.0)
    def synth(n, b, w):
        gw = m.gate_work({b: n}, 27)
        return dict(meth='LMKCDEY', N=2048, word=32, logQ=27,
                    d=m.digits_for_base(27, b), n=n, w=w, gw=gw, threads=8,
                    gate_us=(true[0] + true[1] * n + true[2] * gw
                             + true[3] * 2048.0 / w))
    rows = [synth(n, b, 10) for b in (2, 8, 32, 128, 1024) for n in (64, 256, 512)]
    rows += [synth(64, 2, 40)]          # the w-arm: only w moves
    f = gf.fit_cell(rows, 2048)
    check("refit recovers c1", abs(f['c1'] - true[1]) < 1e-6, True)
    check("refit recovers c2", abs(f['c2'] - true[2]) < 1e-6, True)
    check("refit recovers c3 from the w-arm", abs(f['c3'] - true[3]) < 1e-6, True)
    check("refit recovers c0 after removing c3*N/10",
          abs(f['c0'] - true[0]) < 1e-5, True)
    # With no w-arm, c3 must come back None rather than a fitted constant column.
    f2 = gf.fit_cell([r for r in rows if r['w'] == 10], 2048)
    check("c3 stays None with no w-arm", f2['c3'], None)
    # The shifted-printf log put `build32` in the threads field; every row must
    # be rejected rather than parsed into a plausible number.
    import tempfile          # `os` is imported at module scope; re-importing it
                             # here made it function-local and broke earlier uses
    fh = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False)
    fh.write("timing3| meth=2 threads=build32 build=64 n=512 N=1024 q=27 "
             "logQ=32768 qKS=32 bKS=16384 bG=1125 gate_us=16 keygen_ms=\n")
    fh.close()
    good, badrows = gf.read(fh.name)
    os.unlink(fh.name)
    check("corrupt (field-shifted) rows are rejected", (len(good), len(badrows)),
          (0, 1))
    # The build field is what separates word sizes; without it a log carrying
    # both fits one cell with a 38% residual instead of two at 2%.
    fh = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False)
    fh.write("timing2| meth=2 threads=8 build=build32 n=512 N=1024 q=2048 "
             "logQ=27 qKS=32768 bKS=32 bG=16384 gate_us=14500 keygen_ms=220\n"
             "timing2| meth=2 threads=8 build=build n=512 N=1024 q=2048 "
             "logQ=27 qKS=32768 bKS=32 bG=16384 gate_us=29000 keygen_ms=220\n")
    fh.close()
    got, _ = gf.read(fh.name)
    os.unlink(fh.name)
    check("word size is derived from the build field",
          sorted(r['word'] for r in got), [32, 64])

    # Since 9e8045db the binary reports which key forms a run actually held, and
    # the word a row is keyed under has to agree with it. The regime arm writes
    # `word=32 build=build` (one binary), so the check is on the resolved WORD:
    # a build-tag check rejected all 297 w32 rows of the first 238153db log.
    fh = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False)
    common = "N=512 q=1024 n=256 bG=16384 w=10 logQ=27 internal32ks=yes gate_us=1125 keygen_ms=25 btkey_b=1\n"
    fh.write("gatecost| meth=2 threads=8 word=32 build=build internal32=yes " + common)     # ok
    fh.write("gatecost| meth=2 threads=8 word=64 build=build internal32=no " + common)      # ok
    fh.write("gatecost| meth=2 threads=8 word=64 build=build internal32=yes " + common)     # mis-keyed
    fh.write("gatecost| meth=2 threads=8 build=build32 internal32=no " + common)            # mis-keyed
    fh.close()
    got, bad = gf.read(fh.name)
    os.unlink(fh.name)
    check("word=32 with a narrowed refresh key is accepted", sorted(r['word'] for r in got), [32, 64])
    check("rows whose word contradicts the reported key form are refused", len(bad), 2)

    print("\ncost cells are keyed by (thread mode, compiler)")
    # Cells measured at 8 threads with clang do not describe a 1-thread gcc
    # build: clang leads single-threaded and gcc multi-threaded, so the regime
    # reorders builds. An unmeasured regime must hold NO cells and refuse, since
    # an empty frontier reads as "nothing beats it".
    before = m.ACTIVE_REGIME
    try:
        check("default regime is measured", m.regime_is_measured(("multi", "clang")), True)
        # Pick an unmeasured regime dynamically. Hardcoding one made this test
        # fail the moment that regime was measured -- the test broke on success,
        # which is the wrong direction for a test to be sensitive in.
        unmeasured = [r for r in m.COST_REGIMES if not m.COST_REGIMES[r]["cells"]]
        if unmeasured:
            m.set_cost_regime(*unmeasured[0])
            check("an unmeasured regime is reported unmeasured", m.regime_is_measured(), False)
            check("and prices nothing at all",
                  m.gate_us({128: 512}, 27, 1024, method="GINX", word_size=32), None)
            check("its provenance says NOT MEASURED",
                  "NOT MEASURED" in m.cost_provenance(), True)
        else:
            print("  skip   every regime is measured, so the refusal path has no "
                  "example left to exercise")
        try:
            m.set_cost_regime("multi", "icc")
            check("an unknown regime raises", False, True)
        except ValueError:
            check("an unknown regime raises", True, True)
        # Selecting back must restore real cells, not leave the table empty.
        m.set_cost_regime(*before)
        check("switching back restores the measured cells",
              m.gate_us({128: 512}, 27, 1024, method="GINX", word_size=32) is not None,
              True)
    finally:
        m.set_cost_regime(*before)

    print("\nmargin is scored against a step that exists, including a partial one")
    # The STD128 skeleton at base 2^7, logQ 27, 32-bit words. Every frontier row
    # used to read "margin-unspendable" here, for two reasons that were bugs and
    # not findings: the step cost had its sign inverted (log2Pf is negative, so a
    # worse Pf is the LESS negative number), and the search for a coarser base
    # walked by doubling and stopped at 2^8, which fails the width rule, before
    # reaching 2^9, which passes it. With both fixed, moving one eighth of the
    # coefficients to 2^9 costs ~29 bits against ~60 of net margin: spendable.
    import dse_enumerate as e
    skel = dict(N=1024, n=576, q=2048, log_q_big=27, q_ks=1 << 15, base_ks=32,
                sigma=3.19, inputs=2, method='GINX', key_dist='UNIFORM_TERNARY',
                word_size=32, autokeys=10, split_count=0)
    one = e.evaluate(dict(skel, gadget_shape_keys=(128,)))
    step = e.next_step_cost(one)
    check("a coarser base exists past the 2^8 width failure", step is not None, True)
    check("the step costs a POSITIVE number of bits", step is not None and step > 0, True)
    check("one eighth at 2^9 costs less than the margin (spendable)",
          e.spendable_margin(one, -64)[1], True)
    two = e.evaluate(dict(skel, gadget_shape_keys=(128, 512), split_count=216))
    check("a map already near the target has no spendable margin",
          e.spendable_margin(two, -64)[1], False)
    top = e.evaluate(dict(skel, gadget_shape_keys=(512,)))
    check("the coarsest fitting base has no next step", e.next_step_cost(top), None)

    print("\nring dimension above the measured range is opt-in, marked, and bounded")
    import dse_enumerate as _en2
    # The noise model is measured over N 512..2048. Its error is FLAT across that
    # range (predicted/measured pooled sigma 1.004 at N=1024, 1.005 at N=2048 over
    # the 105 cells of the 94229558 table), which is what makes ONE doubling an
    # argument from shape rather than a guess -- and why two doublings is not.
    check("N=2048 needs no opt-in", m.within_calibrated_envelope(2048, 28), True)
    check("N=4096 is refused by default", m.within_calibrated_envelope(4096, 28), False)
    check("...and admitted when asked for",
          m.within_calibrated_envelope(4096, 28, extrapolate_n=4096), True)
    check("asking for one dimension does not admit another",
          m.within_calibrated_envelope(4096, 28, extrapolate_n=8192), False)
    check("two doublings is refused however it is asked for",
          m.within_calibrated_envelope(8192, 28, extrapolate_n=8192), False)
    check("logQ is not widened with it",
          m.within_calibrated_envelope(4096, 60, extrapolate_n=4096), False)
    check("the limit is one doubling of the measured maximum",
          m.N_EXTRAPOLATION_LIMIT, 2 * m.CALIBRATED_N_RANGE[1])
    check("a measured dimension is not marked extrapolated", m.n_is_extrapolated(2048), False)
    check("...and an unmeasured one is", m.n_is_extrapolated(4096), True)
    _g = dict(N=(2048,), log_q_big=(28,), q=(4096,), log_q_ks=(17,), base_ks=(64,),
              n_step=512, inputs=4, autokeys=(10,))
    t0, c0, k0 = _en2.run(-128.0, method='GINX', grid=dict(_g), level=None)
    t1, c1, k1 = _en2.run(-128.0, method='GINX', grid=dict(_g), level=None,
                          extrapolate_n=4096)
    check("the opt-in ADDS the dimension rather than replacing the grid", t1 > t0, True)
    check("...and the measured dimensions still survive", len(k1) >= len(k0), True)
    # The three gates are independent. The opt-in opens the grid and the envelope;
    # the third is the cost cell, and N=4096 was measured on 2026-09-12, so the
    # "cost uncalibrated" refusal is gone from that dimension. Whether such a
    # candidate then WINS is a separate question the target decides.
    check("the cost gate no longer refuses the extrapolated dimension",
          c1.get('cost uncalibrated', 0), 0)
    _pr = [_en2.prune(c, level=None, extrapolate_n=4096, w32_needs_hybrid=True)
           for c in _en2.candidates(dict(_g, N=(4096,)), 'GINX')]
    check("...and every gate before evaluation passes it",
          {x for x in _pr}, {None})
    check("a dimension past the one-doubling limit has no cell either",
          m.gate_us({64: 1300}, 28, 8192, method='GINX', word_size=32), None)

    print("\na truncated enumeration does not get to look like a refusal")
    import dse_enumerate as _en
    import io as _io2, contextlib as _c2
    def _rep(**kw):
        b = _io2.StringIO()
        with _c2.redirect_stdout(b):
            _en.report(-64.0, 4, {'SURVIVED': 0, 'keyswitch saturated': 4}, [], **kw)
        return b.getvalue()
    out = _rep(limit=3)
    check("--limit is named in the enumeration line", "TRUNCATED at --limit 3" in out, True)
    check("...and the refusal says to re-run without it", "Re-run without --limit" in out, True)
    check("...and does not claim the breakdown is the binding constraint",
          "That is a result, not a bug" in out, False)
    out2 = _rep()
    check("an untruncated refusal still reads as a result",
          ("That is a result, not a bug" in out2, "TRUNCATED" in out2), (True, False))

    print("\nthe search enumerates the security boundary in n, not just the 32-step grid")
    # The legacy selector found n=558 (the smallest n the estimator admits at
    # q_KS 2^15 for STD128) by bisecting n with live estimator calls; a 32-step
    # grid straddles it (544 fails, 576 wastes 18 coefficients, ~2% of gate).
    import dse_security as sec
    calls = []
    def secure(n):
        calls.append(n); return n >= 558
    check("n-bisection lands on the boundary", sec._bisect_n(secure, 544, 576), 558)
    check("...in five calls for a 32-wide straddle", len(calls), 5)
    fake = {"entries": {"standard|STD128|ternary|classical|tol1": {"544": 14, "556": 15, "576": 15}},
            "boundaries": {"standard|STD128|ternary|classical|tol1": {"15": 558, "16": 601}}}
    check("extra_dims = priced off-grid n plus boundaries",
          sec.extra_dims("STD128", "ternary", "standard", False, 1, cache=fake), [556, 558, 601])
    check("bit counts map onto cache curves for the legacy selector",
          (sec.level_name(128), sec.level_name(192, True), sec.level_name(256, False), sec.level_name(100)),
          ("STD128", "STD192Q", "STD256", None))
    check("extra_dims is empty for an unpriced curve",
          sec.extra_dims("STD192", "ternary", "standard", False, 1, cache=fake), [])
    seen = {c['n'] for c in e.candidates(grid=dict(N=(1024,), log_q_big=(27,), q=(2048,), log_q_ks=(15,),
                                                      base_ks=(32,), autokeys=(10,), extra_n=(558, 1500, 16)),
                                            method='GINX', word_size=32)}
    check("candidates() yields the extra n inside [step, N]", 558 in seen and 1500 not in seen and 16 not in seen, True)
    check("...and still the grid", 544 in seen and 576 in seen, True)

    print("\nthe leave-region-out map labels regions the way the search does")
    import dse_lso as lso
    fh = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False)
    fh.write("record| label=a set=- inputs=2 N=1024 n=576 q=2048 logQ=27 qks=32768 baseks=32 gmap=128:360,512:216 method=2 keys=2 sigma=18.5 mean=0 samples=400 FAILURES=0 secs=1\n"
             "record| label=b set=- inputs=2 N=2048 n=832 q=4096 logQ=28 qks=32768 baseks=32 gmap=128:832 method=2 keys=2 sigma=12.0 mean=0 samples=400 FAILURES=0 secs=1\n")
    fh.close()
    recs = [lso.annotate(r) for r in lso.load_records([fh.name])]
    os.unlink(fh.name)
    check("two records parsed", len(recs), 2)
    check("a two-base 2^7/2^9 map at logQ 27 is 'two-base' and misaligned",
          (recs[0]['regions']['map'], recs[0]['regions']['alignment']), ('two-base', 'misaligned'))
    check("a single 2^7 map at logQ 28 is 'single' and aligned",
          (recs[1]['regions']['map'], recs[1]['regions']['alignment']), ('single', 'aligned'))
    check("word follows the modulus", (recs[0]['regions']['word'], recs[1]['regions']['word']), ('w32', 'w32'))
    check("prediction attached and positive", all(r['pred'] > 0 for r in recs), True)

    print("\nan unresolved per-key scatter is certified at its bound, not at zero")
    import dse_verify as dv
    # twelve keys whose sigmas differ by less than the sampling error at 1250
    # gates: the deconvolution cannot see the scatter
    tight = [(19.40 + 0.01 * (i % 3), 0.0) for i in range(12)]
    cert, q_se, d = dv.certify(tight, 2, 2048, samples_per_key=1250)
    check("sampling-limited run flags the assumption", d['scatter_assumed'], True)
    floor = dv.log2pf_sensitivity(sum(s for s, _ in tight) / 12, 2, 2048) * m.KEY_SCATTER_BOUND
    check("certified = mean + Z90 * bound-scatter", abs(cert - (d['mean_log2pf'] + dv.Z_90 * floor)) < 1e-9, True)
    check("...which is a penalty of about two bits at -64", 1.5 < dv.Z_90 * floor < 3.5, True)
    # keys whose sigmas genuinely scatter by 5%: the data resolves it and the bound is not used
    wide = [(19.4 * (1 + 0.05 * ((-1) ** i)), 0.0) for i in range(12)]
    cert2, _, d2 = dv.certify(wide, 2, 2048, samples_per_key=1250)
    check("a resolved scatter is used as measured", d2['scatter_assumed'], False)
    check("...and exceeds the bound", d2['spread'] > floor, True)

    print("\nthe streaming frontier equals the batch one")
    # run() used to keep every survivor and call pareto() at the end; the STD128
    # multi-base search was OOM-killed holding tens of millions of them. The
    # running insert must give exactly the batch answer on the same axes.
    import random
    rnd = random.Random(7)
    rows = [dict(gate_us=rnd.randrange(10000, 90000), key_bytes=rnd.randrange(1, 60) * 2**20,
                 neg_margin=-rnd.uniform(-5, 80)) for _ in range(4000)]
    rows += [dict(r) for r in rows[:50]]                      # exact duplicates must not multiply
    streamed = []
    for r in rows:
        e.front_insert(streamed, r)
    batch = e.pareto(rows)
    key = lambda r: (r['gate_us'], r['key_bytes'], round(r['neg_margin'], 9))
    check("same frontier size", len(streamed), len(batch))
    check("same frontier points", sorted(map(key, streamed)) == sorted(map(key, batch)), True)

    print("\nthe model refuses rather than extrapolating")
    # AP IS priced now (2026-09-07), so the old form of this check -- "AP returns
    # None" -- no longer states anything true. What must still hold is the rule
    # it was there to protect: the model refuses where it cannot honestly answer.
    # For AP that is a missing baseR, without which its product count, and so its
    # gate work, is unknown.
    check("AP prices with its refresh base",
          m.gate_us({128: 512}, 27, 1024, method='AP', word_size=32,
                    base_r=8, q=2048) > 0, True)
    try:
        m.gate_us({128: 512}, 27, 1024, method='AP', word_size=32)
        check("AP without a refresh base raises rather than guessing", False, True)
    except ValueError:
        check("AP without a refresh base raises rather than guessing", True, True)
    check("a ring dimension with no cell still returns None",
          m.gate_us({128: 512}, 27, 8192, method='AP', word_size=32,
                    base_r=8, q=2048), None)

    print("\nthe runner records every dimension the search can move")
    # run-plan.sh recorded neither the secret distribution nor numAutoKeys, and
    # dse.py validate filled both in with defaults. That scored the parameter
    # table's four GAUSSIAN LMKCDEY picks against ternary's Var(s)/12 -- a 15x
    # difference in the rounding terms -- and reported -25% to -41% "model
    # error" on rows the model predicts to within 3.1%. The runner is the only
    # place that sees the command line, so it is where this has to be caught.
    import subprocess
    import tempfile
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'scripts', 'run-plan.sh')
    # `eval` runs the line, so a stub that prints three noise values on stderr
    # and a Failures line on stdout exercises the real parsing path without the
    # harness. The flags only have to be present for `field` to find them.
    def record_for(flags):
        with tempfile.NamedTemporaryFile('w', suffix='.cmds', delete=False) as f:
            f.write('# label: T1\n')
            f.write("sh -c 'printf \"1\\n2\\n3\\n\" >&2; echo Failures: 0' "
                    + flags + "\n")
            path = f.name
        try:
            out = subprocess.run(['bash', runner, path], capture_output=True,
                                 text=True).stdout
        finally:
            os.unlink(path)
        return out

    rec = record_for('-N 1024 -n 554 -q 2048 -Q 27 -k 32768 -b 32 -g 128 '
                     '-t 3 -a 40 -d 0 -I 2 -K 1')
    check("runner records keydist=GAUSSIAN for -d 0",
          'keydist=GAUSSIAN' in rec, True)
    check("runner records autokeys from -a", 'autokeys=40' in rec, True)
    rec_t = record_for('-N 1024 -n 554 -q 2048 -Q 27 -k 32768 -b 32 -g 128 '
                       '-t 2 -d 1 -I 2 -K 1')
    check("runner records keydist=UNIFORM_TERNARY for -d 1",
          'keydist=UNIFORM_TERNARY' in rec_t, True)
    # An unrecognised or absent -d must not silently become a distribution.
    rec_n = record_for('-N 1024 -n 554 -q 2048 -Q 27 -k 32768 -b 32 -g 128 '
                       '-t 2 -I 2 -K 1')
    check("no -d records keydist=- rather than a default",
          'keydist=-' in rec_n, True)

    import dse_sweeplog as sl
    with tempfile.NamedTemporaryFile('w', suffix='.log', delete=False) as f:
        f.write(rec)
        logpath = f.name
    try:
        rows = sl.runner_records(logpath)
        check("keydist parses as a name, not an int",
              rows[0]['keydist'], 'GAUSSIAN')
        check("autokeys parses as an int", rows[0]['autokeys'], 40)
    finally:
        os.unlink(logpath)

    print("\nthe search selects on the statistic certification decides on")
    # Seven cells of the 2026-09-08 table certified 1.7 to 5.4 bits short of
    # target while every one had a predicted log2Pf that cleared it. The cause was
    # structural, not a model error: the enumerator gated on the MEAN-key log2Pf
    # and `certify` accepts on the 0.9-quantile key, which is worse by
    # quantile_penalty(). All seven were at arity 3 or 4, where the per-key offset
    # is amplified 3x and 4x.
    import dse_verify as vf
    check("prediction and certification use the SAME quantile", m.Z_90, vf.Z_90)
    # The sensitivity is duplicated too (the enumerator must not import the
    # verifier), so the two implementations have to agree.
    check("the two sensitivity implementations agree",
          abs(m.log2pf_sensitivity(20.0, 2, 2048)
              - vf.log2pf_sensitivity(20.0, 2, 2048)) < 1e-9, True)
    check("the penalty is positive: the quantile key is WORSE",
          m.quantile_penalty(20.0, 2, 2048) > 0, True)
    # Scaling: the penalty is z * sensitivity * scatter, and sensitivity grows
    # with |log2Pf|, so a safer candidate carries a LARGER penalty in bits. This
    # is why a -128 target is hit harder than a -64 one.
    lo = m.quantile_penalty(30.0, 2, 2048)
    hi = m.quantile_penalty(15.0, 2, 2048)
    check("a lower-sigma (safer) candidate pays more bits of penalty", hi > lo, True)
    check("the budget constant is NOT the sigma-space one",
          m.PER_KEY_LOG2PF_SCATTER != m.KEY_SCATTER_BOUND, True)

    # The gate must move. A candidate whose mean key clears the target by less
    # than its penalty has to be refused, and the same candidate must be admitted
    # with the budget off -- that is the regression, stated as a difference.
    import dse_enumerate as en
    # next_step_cost wants a full candidate; only the margin arithmetic is under
    # test here, so the discrete-step half is stubbed out.
    _real_step = en.next_step_cost
    en.next_step_cost = lambda cand: None
    ev = {'log2pf': -65.0, 'log2pf_cert': -61.0, 'band': 0.5}
    check("margin is scored against the certifiable value",
          en.spendable_margin(ev, -64.0)[0], -64.0 - (-61.0) - 0.5, tol=1e-9)
    ev_old = {'log2pf': -65.0, 'band': 0.5}
    check("...and falls back to log2pf where no certifiable value exists",
          en.spendable_margin(ev_old, -64.0)[0], -64.0 - (-65.0) - 0.5, tol=1e-9)
    en.next_step_cost = _real_step

    print("\nnumAutoKeys is not a trade, so it is not a search dimension")
    # Both of its effects improve together -- noise K_AUTO/w and gate time
    # c3*N/w both fall as w rises -- and the only cost is key material. So the
    # optimum is the largest priced value, not an interior point, and carrying 5
    # values in the grid multiplied LMKCDEY's enumeration by 5 (52.7% of a table
    # search) to rediscover the ceiling every time.
    gm_lm = {64: 630}
    def lm(w, field):
        sig = m.sigma_total_at_q(1024, 630, 2048, 27, 131072, 64, 3.19, gm_lm,
                                 method='LMKCDEY', autokeys=w)
        if field == 'pf':
            return m.log2_pf(sig, 4, 2048)
        if field == 'gate':
            return m.gate_us(gm_lm, 27, 1024, method='LMKCDEY', word_size=32, autokeys=w)
        return m.btkey_bytes(1024, gm_lm, 27, 4, method='LMKCDEY', autokeys=w)
    check("more autokeys is never noisier", lm(40, 'pf') <= lm(10, 'pf'), True)
    check("more autokeys is never slower", lm(40, 'gate') <= lm(10, 'gate'), True)
    check("...and that is what it costs: key material",
          lm(40, 'key') > lm(10, 'key'), True)
    # The direction must not depend on thread count: c3 >= 0 in EVERY regime, and
    # dse_gatefit refuses a wrong-sign majority rather than fitting it, so a cell
    # at c3 = 0.0 means "below the measurement quantum", not "negative".
    neg = [(reg, k) for reg, v in m.COST_REGIMES.items()
           for k, cell in (v.get('cells') or {}).items()
           if k[0] == 'LMKCDEY' and cell[3] is not None and cell[3] < 0]
    check("no regime prices autokeys as a slowdown", neg, [])
    check("max_priced_autokeys takes the ceiling for LMKCDEY",
          m.max_priced_autokeys(2048, 32, 'LMKCDEY', (2, 5, 10, 20, 40)), 40)
    check("...and leaves GINX at its single value",
          m.max_priced_autokeys(2048, 32, 'GINX', (2, 5, 10, 20, 40)), 10)

    print("\nthe gadget map is refined from the frontier, not enumerated in the grid")
    # Multi-base is a pure trade (0 of 252 admissible maps at fixed
    # everything-else were both faster and better), so a grid sweep costs 28.2x
    # for points a local move along a straight ladder reaches in seconds.
    import dse_enumerate as en
    g_one = dict(N=(512,), log_q_big=(27,), q=(1024,), n_step=128)
    n_default = sum(1 for _ in en.candidates(g_one, 'LMKCDEY'))
    n_akgrid = sum(1 for _ in en.candidates(g_one, 'LMKCDEY', autokeys_grid=True))
    check("autokeys out of the grid shrinks LMKCDEY's enumeration 5x",
          round(n_akgrid / n_default), 5)
    n_maps = sum(1 for _ in en.candidates(g_one, 'LMKCDEY', multi_base=True))
    check("...and maps in the grid would multiply it by more than 20",
          n_maps / n_default > 20, True)
    check("the default grid yields single-base shapes only",
          {len(c['gadget_shape_keys']) for c in en.candidates(g_one, 'LMKCDEY')}, {1})

    # Staging must reach the same answer as the exhaustive sweep. Checked on a
    # real cell rather than asserted: STD128 arity 2, where the winner is a
    # two-base map that only the refinement can reach.
    grid = dict(inputs=2, N=(1024,), log_q_big=(27,), q=(2048,), n_step=32)
    _, _, f_stage = en.run(-64.0, method='GINX', grid=grid, level='STD128',
                           sec_source='estimator', sec_tolerance=1,
                           refine=True, multi_base=False)
    _, _, f_full = en.run(-64.0, method='GINX', grid=grid, level='STD128',
                          sec_source='estimator', sec_tolerance=1,
                          refine=False, multi_base=True)
    p_stage, p_full = en.default_policy(f_stage), en.default_policy(f_full)
    check("staged refinement finds a pick at all", p_stage is not None, True)
    # The exhaustive sweep samples the split at 7 ladder points; the refinement
    # also bisects to the admissibility BOUNDARY (Hong & Lee, Thm 4.3), so it
    # may now be strictly faster than the grid. It must never be slower.
    check("staged pick is at least as fast as the exhaustive-grid pick",
          p_stage['gate_us'] <= p_full['gate_us'] + 1e-6, True)
    check("...and still clears the target net of its band", p_stage['net_margin'] > 0, True)
    check("and that pick really is a two-base map",
          len(p_stage['gadget_map']), 2)

    print("\nthe map refinement follows Hong & Lee (ePrint 2025/1892)")
    # Theorems 4.1-4.2: the optimal heterogeneous map has two bases with ADJACENT
    # digit counts. Every winning map seen here obeys it, so only those pairs
    # are tried -- 2 per frontier member instead of up to 8 of the 36 pairs.
    prs = en.adjacent_pairs(26, 32, 64)
    check("adjacent_pairs returns pairs containing the base",
          all(64 in pr for pr in prs), True)
    check("...its neighbours in the undominated list", sorted(prs), [(32, 64), (64, 128)])
    check("...whose digit counts here differ by exactly one",
          all(abs(m.digits_for_base(26, pr[0]) - m.digits_for_base(26, pr[1])) == 1
              for pr in prs), True)
    # Power-of-two bases leave GAPS in the achievable digit counts (26, 13, 9 at
    # logQ 26), so a literal d+1 reading finds nothing for base 4 and lost a
    # cell. "Adjacent" is the next available item, which is what the proof uses.
    check("base 4 still has neighbours despite the digit gap",
          sorted(en.adjacent_pairs(26, 32, 4)), [(2, 4), (4, 8)])
    check("the finest base has exactly one neighbour",
          en.adjacent_pairs(26, 32, 2), [(2, 4)])
    for lq, gm in ((26, {32: 165, 64: 1152}), (26, {16: 348, 32: 1043}),
                   (27, {128: 347, 512: 207}), (27, {128: 547, 512: 9})):
        ds = sorted(m.digits_for_base(lq, b) for b in gm)
        check("observed winner %s is adjacent-digit" % sorted(gm), ds[1] - ds[0], 1)
    # Theorem 4.3: the boundary split is admissible and one more coarse
    # coefficient is not -- the step bisection is looking for.
    if p_stage is not None:
        single = next((r for r in f_stage if len(r['gadget_shape_keys']) == 1), None)
        if single is not None:
            pr = en.adjacent_pairs(single['log_q_big'], single['word_size'],
                                   single['gadget_shape_keys'][0])
            if pr:
                tgt = -64.0
                def _adm(ev):
                    return ev['log2pf_cert'] <= tgt and (tgt - ev['log2pf_cert'] - ev['band']) > 0
                def _ev(cand):
                    why = en.prune(cand, level='STD128', secret_dist='ternary',
                                   sec_source='estimator', sec_tolerance=1,
                                   sec_cache=__import__('dse_security').load(),
                                   sec_model=__import__('dse_security').active_model(),
                                   w32_needs_hybrid=True)
                    return why if why else en.evaluate(cand)
                b = en.boundary_split(single, pr[0], _ev, _adm)
                if b is not None:
                    x = b['split_count']
                    nxt = _ev(en._map_candidate(single, pr[0], x + 1)) if x + 1 < single['n'] else None
                    check("boundary split is admissible", _adm(b), True)
                    check("...and one more coarse coefficient is not",
                          (nxt is None) or (not isinstance(nxt, dict)) or (not _adm(nxt)), True)

    print("\nthe known-answer control is self-contained and re-baselined on the live table")
    # A control that read the live library file would have changed its question
    # when f3944448 gave STD128 new parameters under the same name, and reported
    # the model wrong on 5 of 5. The parameters now travel with the measurement.
    import dse_shipfit as sf2
    import dse_measured as dm2
    check("live control has 4 sets measured at 41709fbc", len(dm2.CONTROL), 4)
    check("...each carries its own parameters and sigma",
          all({'params', 'sigma', 'method'} <= set(c) for c in dm2.CONTROL.values()), True)
    check("...STD128 in it is the re-selected geometry, n=554", dm2.CONTROL['STD128']['params']['n'], 554)
    check("historical control keeps the five 238153db sets inline", len(dm2.CONTROL_HISTORICAL), 5)
    check("...with STD128 at n=556 (the geometry that was measured)",
          dm2.CONTROL_HISTORICAL['STD128']['params']['n'], 556)
    ok_model, lines = sf2.control(live=None)
    check("model half passes on both sets, no library file involved", ok_model, True)
    live_src = os.environ.get('BINFHECONTEXT_SRC')
    if live_src and os.path.exists(live_src):
        live = sf2.shipped_params(strict=False, src=live_src)
        if all(n in live for n in dm2.CONTROL):
            ok_both, lines = sf2.control(live=live)
            check("parser half passes against the live table", ok_both, True)
            check("...and says so per row", sum('parser ok' in l for l in lines), len(dm2.CONTROL))
        else:
            print("  skip parser half: BINFHECONTEXT_SRC predates the re-selected table")
    # a parser regression must be caught: hand the control a live table whose
    # STD128 reads a different n, as the cyclOrder misread once would have
    fake = {k: dict(v['params']) for k, v in dm2.CONTROL.items()}
    fake['STD128']['n'] = 556
    ok_fake, lines = sf2.control(live=fake)
    check("a live row that disagrees with the inline parameters FAILS the control", ok_fake, False)
    check("...and names the field", any("DIFFERS on n" in l for l in lines), True)
    P45 = sf2.shipped_params(strict=False, src=sf2.PRE_RESELECT_SRC)
    check("the pre-re-selection table (key-cap reference) has 45 rows", len(P45), 45)
    check("...with STD192 at logQ 37, not the re-selected 28", P45['STD192']['logQ'], 37)

    print("\nthe hybrid check tests the autokey base the library actually uses")
    import inspect
    check("prune() passes min(gm) as the default base (harness, library, btkey_bytes agree)",
          "default_base=min(gm)" in inspect.getsource(en.prune), True)

    print("\nkey cap: gate-first within it, least-key beyond it")
    import dse_table as tb
    refs = {'STD128_3': 100.0, 'STD192_3': 200.0, 'LPF_STD128': 300.0}
    check("same name resolves", tb.key_cap_for('LPF_STD128', refs, 2.0), (600.0, 'LPF_STD128'))
    check("method suffix stripped", tb.key_cap_for('STD128_3_AP', refs, 1.0), (100.0, 'STD128_3'))
    check("LPF_ prefix stripped when the library shipped no LPF row",
          tb.key_cap_for('LPF_STD192_3_LMKCDEY', refs, 1.5), (300.0, 'STD192_3'))
    check("no reference -> no cap", tb.key_cap_for('STD256Q_4', refs, 1.0), (None, None))
    check("mult None -> no cap", tb.key_cap_for('STD128_3', refs, None), (None, None))
    fr = [dict(gate_us=10.0, key_bytes=900, net_margin=1.0),   # fastest, over any small cap
          dict(gate_us=12.0, key_bytes=400, net_margin=1.0),   # mid
          dict(gate_us=15.0, key_bytes=100, net_margin=1.0),   # slow, smallest key
          dict(gate_us= 9.0, key_bytes= 50, net_margin=-1.0)]  # fastest+smallest but not admissible
    check("the default exchange rate is 2", en.KEY_CAP_LAMBDA, 2.0)
    pk, over = en.key_cap_policy(fr, 500)
    check("soft: 900B row scores 10*(1+2*0.8)=26 vs 12 and 15 -> the 400B row", (pk['gate_us'], over), (12.0, False))
    pk, over = en.key_cap_policy(fr, 5000)
    check("cap above everything: same as default_policy", (pk['gate_us'], over), (10.0, False))
    pk, over = en.key_cap_policy(fr, 60, lam=float('inf'))
    check("nothing fits, lambda=inf: least key material, flagged over_cap", (pk['key_bytes'], over), (100, True))
    pk, over = en.key_cap_policy(fr, 60, lam=0.0)
    check("nothing fits, lambda=0: gate-first everywhere (key is free)", (pk['gate_us'], over), (10.0, True))
    # lambda=1 at cap 60: scores 10*(1+14)=150, 12*(1+5.67)=80, 15*(1+0.67)=25 -> the 100-byte row
    pk, over = en.key_cap_policy(fr, 60, lam=1.0)
    check("nothing fits, lambda=1: a doubling of key costs a doubling of gate", (pk['key_bytes'], over), (100, True))
    # at cap 300, lambda=1: 10*(1+2)=30, 12*(1+0.33)=16, 15 -> the 100-byte row, which fits
    pk, over = en.key_cap_policy(fr, 300, lam=1.0)
    check("soft at cap 300: the under-cap row wins on score", (pk['key_bytes'], over), (100, False))
    # the cliff case: a row just over the cap and much faster
    fr_cliff = fr + [dict(gate_us=8.0, key_bytes=330, net_margin=1.0)]
    pk, over = en.key_cap_policy(fr_cliff, 300, lam=2.0)
    check("soft: 10% over the cap for 47% less gate wins (8*(1+0.2)=9.6 < 15), flagged over_cap",
          (pk['key_bytes'], over), (330, True))
    pk, over = en.key_cap_policy(fr_cliff, 300, lam=6.0)
    check("...and loses once lambda prices the excess above the gain (8*(1+0.6)=12.8 < 15 still wins at 6)", pk['key_bytes'], 330)
    pk, over = en.key_cap_policy(fr_cliff, 300, lam=10.0)
    check("...at lambda=10 (8*2=16 > 15) the under-cap row is back", (pk['key_bytes'], over), (100, False))
    pk, over = en.key_cap_policy(fr_cliff, 300, lam=2.0, hard=True)
    check("hard: the two-stage rule never looks past a row that fits", (pk['key_bytes'], over), (100, False))
    pk, over = en.key_cap_policy(fr_cliff, 300, lam=float('inf'))
    check("lambda=inf with a fitting row: never exceed the cap", (pk['key_bytes'], over), (100, False))
    check("inadmissible rows never win, however small",
          en.key_cap_policy([fr[3]], 5000), (None, False))
    r = tb.shipped_key_refs()
    check("shipped references priced for the 41 STD/LPF rows", len(r), 41)
    check("...STD128's reference is a few hundred MiB", 200 * 2**20 < r['STD128'] < 400 * 2**20, True)

    print("\nkey cap levels are per method (4 GiB GINX/LMKCDEY, 8 GiB AP by default)")
    check("default spec", tb.KEY_CAP_DEFAULT, "4,AP=8")
    check("one number caps every method", tb.parse_key_cap("4"), {'*': 4.0})
    check("method overrides ride on the default", tb.parse_key_cap("4,AP=8"), {'*': 4.0, 'AP': 8.0})
    check("every method named needs no default", tb.parse_key_cap("GINX=4,LMKCDEY=4,AP=8"),
          {'GINX': 4.0, 'LMKCDEY': 4.0, 'AP': 8.0})
    check("'none' is no cap", tb.parse_key_cap("none"), None)
    check("a number is accepted (the older CLI form)", tb.parse_key_cap(2.0), {'*': 2.0})
    check("a manifest's dict round-trips", tb.parse_key_cap({'*': 4, 'AP': 8}), {'*': 4.0, 'AP': 8.0})
    check("spec text is the inverse", tb.key_cap_spec_text(tb.parse_key_cap("4,AP=8")), "4,AP=8")
    for bad in ("4,DM=8", "4,8", "AP=8"):
        try:
            tb.parse_key_cap(bad); got = "accepted"
        except ValueError:
            got = "refused"
        check("%r is refused" % bad, got, "refused")
    caps = tb.parse_key_cap("4,AP=8")
    check("AP resolves to its own level", tb.key_cap_gib_for('AP', caps), 8.0)
    check("GINX resolves to the default level", tb.key_cap_gib_for('GINX', caps), 4.0)
    check("method read off a cell name", (tb._method_of('STD128_3_AP'), tb._method_of('LPF_STD192_LMKCDEY'), tb._method_of('STD256Q_4')),
          ('AP', 'LMKCDEY', 'GINX'))
    G = 1 << 30
    fr2 = [dict(gate_us=10.0, key_bytes=5 * G, net_margin=1.0),
           dict(gate_us=12.0, key_bytes=3 * G, net_margin=1.0)]
    pk, info = tb._select([dict(x) for x in fr2], 'STD128_AP', 0, None, "4,AP=8", None, method='AP')
    check("AP under 8 GiB takes the faster 5 GiB row", (pk['gate_us'], info['key_cap_mib'], info['over_cap'], info['key_cap_lambda'], info['key_cap_rule']), (10.0, 8192.0, False, 2.0, 'soft'))
    pk, info = tb._select([dict(x) for x in fr2], 'STD128', 0, None, "4,AP=8", None, method='GINX')
    check("GINX at 4 GiB: 5 GiB row scores 10*(1+2*0.25)=15 > 12 -> the 3 GiB row", (pk['gate_us'], info['key_cap_mib'], info['over_cap']), (12.0, 4096.0, False))
    pk, info = tb._select([dict(x) for x in fr2], 'STD128', 0, None, "4,AP=8", 0.5, method='GINX')
    check("GINX at lambda 0.5: 10*(1+0.125)=11.25 < 12 -> the 5 GiB row, over_cap", (pk['gate_us'], info['over_cap']), (10.0, True))
    pk, info = tb._select([dict(x) for x in fr2], 'STD128', 0, None, "4,AP=8", 0.5, method='GINX', key_cap_hard=True)
    check("...but not under the hard rule", (pk['gate_us'], info['over_cap'], info['key_cap_rule']), (12.0, False, 'hard'))
    pk, info = tb._select([dict(x) for x in fr2], 'STD128_LMKCDEY', 0, None, "none", 1.0, method='LMKCDEY')
    check("'none' is the plain gate-first policy", (pk['gate_us'], info['key_cap_mib']), (10.0, None))

    print("\nrepick re-prices stored rows under the current model")
    import dse_enumerate as _en
    row = None
    for cand in _en.candidates(method='GINX', word_size=32, key_dist='UNIFORM_TERNARY'):
        ev = _en.evaluate(cand)
        if isinstance(ev, dict):
            row = ev; break
    check("a priced candidate exists in the grid", row is not None, True)
    if row is not None:
        stale = dict(row); stale['gate_us'] = row['gate_us'] * 2.0; stale['log2pf_cert'] = row['log2pf_cert'] + 3
        out, moved = tb._reprice([tb._revive(json.loads(json.dumps(tb._jsonable(stale))))])
        check("gate_us comes back from the current cells, not the stored value", out[0]['gate_us'], row['gate_us'], tol=1e-6)
        check("...and the move is reported", moved[0], -0.5, tol=1e-9)
        check("the certifiable statistic is recomputed too", out[0]['log2pf_cert'], row['log2pf_cert'], tol=1e-9)
        check("the stored price is kept beside it", out[0]['stored_gate_us'], stale['gate_us'])
        check("the row says it was repriced", out[0]['repriced'], True)
        refined = _en._map_candidate(out[0], (128, 512), 10)
        check("a refinement built from a repriced row does not inherit its stored price",
              ('stored_gate_us' in refined, 'repriced' in refined), (False, False))

    print("\nkey layout follows the pin: three flags, all on since 94229558")
    check("all three layout flags are on", (m.KSK_TOP_COMPACT, m.RK_TOP_COMPACT, m.KSK_ZERO_ROWS_DROPPED), (True, True, True))
    check("top extent of q_KS 2^15 at base 256 over 2 digits is 128", m.top_digit_extent(32768, 256, 2), 128)
    check("an aligned pair has a full top position (2^15 = 32^3)", m.top_digit_extent(32768, 32, 3), 32)
    check("digit extents run low to high, top confined", m.ks_digit_extents(32768, 256), [256, 128])
    check("...and are the full base with the top uncompacted", m.ks_digit_extents(32768, 256, compact=False), [256, 256])
    check("rows with nothing enabled are base*d",
          m.ksk_rows(32768, 256, compact=False, zero_dropped=False), 512)
    check("top compaction alone: (d-1)*base + top = 384", m.ksk_rows(32768, 256, zero_dropped=False), 384)
    check("both: (d-1)*(base-1) + (top-1) = 382", m.ksk_rows(32768, 256), 382)
    check("...which is what the pinned library allocates for STD128",
          m.ksk_rows(32768, 256), 1 * 255 + 127)
    check("the zero rows are exactly 1/base where every position is full (2^20 = 32^4)",
          1 - m.ksk_rows(1 << 20, 32) / m.ksk_rows(1 << 20, 32, zero_dropped=False), 1 / 32, tol=1e-9)
    check("...and a shade more where the top position is short (2^19 at base 32)",
          (m.ksk_rows(1 << 19, 32, zero_dropped=False), m.ksk_rows(1 << 19, 32)), (32 * 3 + 16, 31 * 3 + 15))
    check("an aligned pair loses nothing to top compaction",
          m.ksk_rows(32768, 32, zero_dropped=False), 32 * 3)
    check("AP refresh keys per coefficient, q 2048 base 32: 93 -> 63 (dead share 32.3%)",
          (m.ap_refresh_keys_per_coeff(2048, 32, compact=False), m.ap_refresh_keys_per_coeff(2048, 32)), (93, 63))
    check("q 2048 base 128: 254 -> 142 (44.1%)",
          (m.ap_refresh_keys_per_coeff(2048, 128, compact=False), m.ap_refresh_keys_per_coeff(2048, 128)), (254, 142))
    check("q 4096 base 8: aligned, unchanged", m.ap_refresh_keys_per_coeff(4096, 8), m.ap_refresh_keys_per_coeff(4096, 8, compact=False))
    check("q 1024 base 32: aligned, unchanged", m.ap_refresh_keys_per_coeff(1024, 32), m.ap_refresh_keys_per_coeff(1024, 32, compact=False))

    print("\nthe zero-digit rows change noise as well as size")
    kv = lambda **kw: m.keyswitch_var_at_q(1024, 2048, 32768, 256, 3.19, **kw)
    check("a position of extent r contributes (1 - 1/r), not 1",
          kv() / kv(zero_dropped=False),
          ((1 - 1 / 256) + (1 - 1 / 128)) / 2, tol=1e-9)
    check("...so sigma falls a little at base 256", kv() < kv(zero_dropped=False), True)
    b32 = m.keyswitch_var_at_q(1024, 2048, 1 << 20, 32, 3.19)
    check("and more at base 32", 1 - b32 / m.keyswitch_var_at_q(1024, 2048, 1 << 20, 32, 3.19, zero_dropped=False),
          1 / 32, tol=2e-3)
    w, bw = m.keyswitch_variance_split(1024, 2048, 32768, 256, 3.19)
    check("the within/between split still sums to the pooled variance", w + bw, kv(), tol=1e-6)

    print("\ndroppedDigitsKS: the approximate key-switching decomposition")
    check("delta drops whole positions from the key: (d-1-delta)*(base-1) + (top-1)",
          m.ksk_rows(1 << 20, 32, dropped_digits=1), 2 * 31 + 31)
    check("delta >= d is refused", _raises(lambda: m.ksk_rows(32768, 256, dropped_digits=2)), True)
    check("the rounding term is zero at delta 0", m.ks_round_var_at_q(1024, 2048, 32768, 256, 0), 0.0)
    check("and is N*(base^2delta - 1)/12*Var(z)*(q/qKS)^2 at delta 1",
          m.ks_round_var_at_q(1024, 2048, 32768, 256, 1),
          1024 * (256.0 ** 2 - 1) / 12.0 * m.SECRET_VARIANCE['UNIFORM_TERNARY'] * (2048.0 / 32768) ** 2, tol=1e-9)
    check("it scales with the ring secret's variance, so Gaussian costs 15x ternary",
          m.ks_round_var_at_q(1024, 2048, 32768, 256, 1, 'GAUSSIAN')
          / m.ks_round_var_at_q(1024, 2048, 32768, 256, 1, 'UNIFORM_TERNARY'),
          m.SECRET_VARIANCE['GAUSSIAN'] / m.SECRET_VARIANCE['UNIFORM_TERNARY'], tol=1e-9)
    check("it dwarfs the position it removes at every base in use",
          [round(m.ks_round_var_at_q(1024, 2048, qks, b, 1)
                 / (m.keyswitch_var_at_q(1024, 2048, qks, b, 3.19)
                    - m.keyswitch_var_at_q(1024, 2048, qks, b, 3.19, dropped_digits=1)), 1)
           for b, qks in ((32, 1 << 20), (64, 1 << 18), (256, 1 << 16))],
          [5.8, 22.7, 359.2])
    check("the band table carries the new term", 'ks_round' in m.TERM_VAR_BAND, True)

    print("\nthe runner refuses to report success for a run that measured nothing")
    import subprocess as _sp
    RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                          'scripts', 'run-plan.sh')
    def _runner(*args):
        r = _sp.run(['bash', RUNNER] + list(args), capture_output=True, text=True,
                    cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
        return r.returncode, r.stdout + r.stderr
    rc, out = _runner()
    check("no plan argument is a usage error", rc, 2)
    rc, out = _runner('/nonexistent/plan.cmds')
    check("an unreadable plan is refused, not reported done", (rc, 'PLAN DONE' in out), (2, False))
    with tempfile.TemporaryDirectory() as td:
        empty = os.path.join(td, 'empty.cmds')
        open(empty, 'w').write("# only a comment\n")
        rc, out = _runner(empty)
        check("a plan with no commands is refused", (rc, 'PLAN DONE' in out), (2, False))
        # a plan whose every command emits no noise values: records exist but say NA
        na = os.path.join(td, 'na.cmds')
        open(na, 'w').write("# label: quiet\ntrue -n 1 -K 1\n")
        rc, out = _runner(na)
        check("all-NA records fail rather than print the marker",
              (rc, 'PLAN DONE' in out), (1, False))
        check("...and say why", 'no run produced noise values' in out, True)

    print("\nthe runner fans out one single-threaded process per key inside a memory budget")
    # A stub that prints OMP_NUM_THREADS as its first noise value: the serial path
    # leaves it unset (two values, sigma 0.707), the fan-out sets it to 1 (three
    # values 1,2,3, sigma 1.000) -- so the record itself says which path ran.
    STUB = ("sh -c 'echo \"$OMP_NUM_THREADS\" >&2; sleep 0.4; printf \"2\\n3\\n\" >&2; echo Failures: 0' "
            "-N 1024 -n %d -q 2048 -Q 27 -k 32768 -b 32 -g 128 -t 2 -d 1 -I 2 -K 1\n")
    def _fan_runner(plan, **env):
        e = dict(os.environ); e.update({k: str(v) for k, v in env.items()})
        r = _sp.run(['bash', RUNNER, plan], capture_output=True, text=True, env=e,
                    cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
        return r.returncode, r.stdout, r.stderr
    with tempfile.TemporaryDirectory() as td:
        plan = os.path.join(td, 'fan.cmds')
        with open(plan, 'w') as f:
            for lab, mib, n in (('A', 900, 554), ('B', 100, 600), ('C', None, 700)):
                f.write("# label: %s\n" % lab)
                if mib:
                    f.write("# key_mib: %d\n" % mib)
                f.write(STUB % n); f.write(STUB % n)
        import time as _time
        t0 = _time.time(); rc1, out1, err1 = _fan_runner(plan); t1 = _time.time() - t0
        t0 = _time.time(); rc3, out3, err3 = _fan_runner(plan, JOBS=3, MEM_GIB=8); t3 = _time.time() - t0
        recs1 = [l for l in out1.splitlines() if l.startswith('record|')]
        recs3 = [l for l in out3.splitlines() if l.startswith('record|')]
        check("serial and JOBS=3 both run every line and earn the marker",
              (rc1, rc3, len(recs1), len(recs3), 'PLAN DONE' in out1, 'PLAN DONE' in out3), (0, 0, 6, 6, True, True))
        check("...the fan-out says so on its START line", 'jobs=3 mem_gib=8' in out3 and 'jobs=' not in out1, True)
        check("...each fan-out process runs ONE OpenMP thread, the serial path is untouched",
              (all('sigma=1.000000' in r for r in recs3), all('sigma=0.707107' in r for r in recs1)), (True, True))
        check("...records keep their labels whatever order they finish in",
              sorted(r.split()[1] for r in recs3), sorted(r.split()[1] for r in recs1))
        check("...and the plan finishes sooner", t3 < t1, True)
        rc, out, err = _fan_runner(plan, JOBS=3, MEM_GIB=2)
        check("a budget the hints exceed makes the runner wait for a slot",
              (rc, 'memory budget: waiting for a slot' in err, len([l for l in out.splitlines() if l.startswith('record|')])), (0, True, 6))
        rc, out, err = _fan_runner(plan, JOBS=3, MEM_GIB=1)
        check("a block too big for the budget runs alone rather than never",
              (rc, 'more than the 1024 MiB budget; running it alone' in err), (0, True))
        rc, out, err = _fan_runner(plan, JOBS='x')
        check("a JOBS that is not a count is refused", (rc, 'JOBS must be a positive integer' in err), (2, True))
        na = os.path.join(td, 'na.cmds')
        open(na, 'w').write("# label: quiet\ntrue -n 1 -K 1\ntrue -n 2 -K 1\n")
        rc, out, err = _fan_runner(na, JOBS=2)
        check("all-NA under the fan-out still fails rather than print the marker",
              (rc, 'PLAN DONE' in out), (1, False))

    print("\na timing run is pinned to one core per thread inside one NUMA node")
    TOOLS_D = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                           'scripts', 'dse-tools')
    sys.path.insert(0, TOOLS_D)
    import importlib
    pc = importlib.import_module('pick_cpus')
    check("a range list expands", pc.expand('0-3,8,12-13'), [0, 1, 2, 3, 8, 12, 13])
    check("and compresses back for taskset", pc.compress([0, 1, 2, 3, 8, 12, 13]), '0-3,8,12-13')
    with tempfile.TemporaryDirectory() as td:
        # a 72-thread box: 36 cores over 4 sub-NUMA nodes of 9, two threads per core,
        # siblings enumerated 36 apart, which is where taking "the first 8 cpus" goes wrong
        for n in range(4):
            cores = list(range(n * 9, (n + 1) * 9))
            d = os.path.join(td, 'devices/system/node/node%d' % n)
            os.makedirs(d)
            open(os.path.join(d, 'cpulist'), 'w').write(
                ",".join(str(x) for x in sorted(cores + [c + 36 for c in cores])) + "\n")
        for c in range(72):
            core = c % 36
            d = os.path.join(td, 'devices/system/cpu/cpu%d/topology' % c)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, 'thread_siblings_list'), 'w').write("%d,%d\n" % (core, core + 36))
        ns = pc.nodes(td)
        check("four NUMA nodes are seen", len(ns), 4)
        check("each exposes 18 cpus but only 9 cores",
              (len(ns[0][1]), len(pc.one_per_core(td, ns[0][1]))), (18, 9))
        r = _sp.run([sys.executable, os.path.join(TOOLS_D, 'pick_cpus.py'), '8',
                     '--sysfs-root', td], capture_output=True, text=True)
        picked = pc.expand(r.stdout.strip())
        check("eight threads get eight DISTINCT physical cores", len(set(picked)), 8)
        check("...no two of them hyperthread siblings",
              len({c % 36 for c in picked}), 8)
        check("...all within one node", set(picked) <= set(ns[0][1]), True)
        r2 = _sp.run([sys.executable, os.path.join(TOOLS_D, 'pick_cpus.py'), '12',
                      '--sysfs-root', td], capture_output=True, text=True)
        check("a request no single node can satisfy is refused, not spread", r2.returncode, 1)
        check("...and says what the nodes offer", 'node0:9' in r2.stderr, True)
        r3 = _sp.run([sys.executable, os.path.join(TOOLS_D, 'pick_cpus.py'), '4',
                      '--sysfs-root', td, '--node', '2'], capture_output=True, text=True)
        check("a named node is honoured", pc.expand(r3.stdout.strip()), [18, 19, 20, 21])
    sys.path.remove(TOOLS_D)

    print("\nthe recalibration wrapper refuses a partial or silent run")
    ARMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                        'scripts', 'dse-arms')

    def _bare(td, name, harness=True, sage=False):
        """A tree holding just the script, so the suite never touches the real
        build/. Writing a stub over build/bin/boolean_estimate_time and deleting
        it afterwards -- which is what testing against the checkout means -- takes
        out the harness the container just built, in the container where these
        tests are supposed to run."""
        root = os.path.join(td, name)
        os.makedirs(os.path.join(root, 'scripts', 'dse-arms'))
        with open(os.path.join(ARMS, 'recalibrate.sh')) as f:
            body = f.read()
        with open(os.path.join(root, 'scripts', 'dse-arms', 'recalibrate.sh'), 'w') as f:
            f.write(body)
        if harness:
            os.makedirs(os.path.join(root, 'build', 'bin'))
            hp = os.path.join(root, 'build', 'bin', 'boolean_estimate_time')
            open(hp, 'w').write('#!/bin/sh\nexit 0\n')
            os.chmod(hp, 0o755)
        path = os.environ['PATH']
        if sage:
            binp = os.path.join(root, 'stubbin')
            os.makedirs(binp)
            sp = os.path.join(binp, 'sage')
            open(sp, 'w').write(
                "#!/bin/sh\n"
                "echo '  method N word pts c0 c1 c2 c3 med|res| worst'\n"
                "echo '  GINX 512 32 27 -81.2 14.7 1.04 - 1.67% 5.81%'\n"
                "echo ''\n"
                "echo 'GATE_COST = {'\n"
                "echo \"    ('GINX', 512, 32): (-81.2, 14.7, 1.04, None),\"\n"
                "echo '}'\n")
            os.chmod(sp, 0o755)
            path = binp + os.pathsep + path
        return root, path

    def _recal(root, path, outdir, **env):
        e = dict(os.environ)
        e.update(PIN='0', PATH=path)
        e.update(env)
        r = _sp.run(['bash', os.path.join(root, 'scripts', 'dse-arms', 'recalibrate.sh'),
                     outdir], capture_output=True, text=True, env=e, cwd=root)
        return r.returncode, r.stdout + r.stderr

    def _done_log(d, stage, meth=2):
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, stage + '.log'), 'w').write(
            'gatecost| meth=%d threads=8 word=64 N=512 gate_us=1\nGATECOST DONE\n' % meth)

    with tempfile.TemporaryDirectory() as td:
        root, path = _bare(td, 'nobuild', harness=False)
        rc, out = _recal(root, path, os.path.join(root, 'out'))
        check("without the harness built it refuses rather than measuring nothing", rc, 2)
        check("...and says where to run it", 'inside the container' in out, True)

        root, path = _bare(td, 'built', sage=True)
        rc, out = _recal(root, path, os.path.join(root, 'b'), STAGES='bogus')
        check("an unknown stage is refused", (rc, "unknown stage 'bogus'" in out), (2, True))

        d = os.path.join(root, 'c'); _done_log(d, 'multi')
        rc, out = _recal(root, path, d, STAGES='multi')
        check("a completed stage is skipped on a re-run", 'already complete' in out, True)
        # ...but only when it holds the methods now being asked for. The same
        # OUTDIR reused for a second method would otherwise skip the stage on the
        # marker alone and fit a log about a different method.
        rc, out = _recal(root, path, d, STAGES='multi', METHODS='LMKCDEY')
        check("resuming a log measured for another method is refused",
              (rc, 'holds no row for' in out), (2, True))
        check("...and says what it does hold", 'it holds: 2' in out, True)

        e = os.path.join(root, 'e'); os.makedirs(e)
        open(os.path.join(e, 'multi.log'), 'w').write('GATECOST DONE\n')
        rc, out = _recal(root, path, e, STAGES='multi')
        check("a marker over an empty log is not a completed stage", rc, 2)

        # the provenance file arrives written from the machine: a run that leaves
        # a <placeholder> in it is a run whose provenance gets pasted with the
        # placeholder still in it
        pv = os.path.join(root, 'pv')
        for st_ in ('multi', 'maps-ginx', 'single'):
            _done_log(pv, st_)
        _recal(root, path, pv)
        prov = open(os.path.join(pv, 'provenance.txt')).read()
        check("the provenance arrives with no placeholder to complete", '<' in prov, False)
        check("...naming the box, the methods and the residuals it saw",
              all(k in prov for k in ('Box:', 'Methods:', 'Residuals, multi:')), True)
        check("...with the residual range parsed out of the fit",
              'median 1.7-1.7%, worst 5.8%' in prov, True)

    print("\nrecalibration defaults to one method and derives its stages")
    def _dry(**env):
        with tempfile.TemporaryDirectory() as dd:
            root, path = _bare(dd, 'dry', harness=False)
            rc, out = _recal(root, path, os.path.join(dd, 'out'), DRY='1', **env)
        stages = ([l.split('stages:')[1].strip() for l in out.splitlines()
                   if 'stages:' in l] or [''])[0]
        meths = ([l.split('methods:')[1].strip() for l in out.splitlines()
                  if 'recalibrate: methods:' in l] or [''])[0]
        # 'multi      THREADS=8 METHODS="2"' -> {'multi': '2'}
        per = dict(re.findall(r'^  (\S+)\s+.*METHODS="([^"]*)"', out, re.M))
        return rc, stages, meths, per, out

    rc, stages, meths, per, out = _dry()
    check("the default is GINX, the method `search` also defaults to", meths, "GINX")
    check("...measured as GINX in the arm's numbering", per.get('multi'), "2")
    check("...and the single-thread stage measures the same method", per.get('single'), "2")
    check("...LMKCDEY's own map rows are not measured for a GINX run",
          ('maps' in stages.split(), 'maps-ginx' in stages.split()), (False, True))
    check("...a dry run measures nothing and says so",
          (rc, 'RECALIBRATE DRY DONE' in out), (0, True))
    check("...and warns that the untouched methods keep other cells",
          'not across them' in out, True)

    rc, stages, meths, per, out = _dry(METHODS='all')
    check("`all` is every method, in the arm's order", (meths, per.get('multi')),
          ("AP GINX LMKCDEY", "1 2 3"))
    check("...and brings both map stages with it",
          stages.split(), ['multi', 'maps', 'maps-ginx', 'single'])
    check("...with no cross-method warning, because nothing is left behind",
          'not across them' in out, False)

    rc, stages, meths, per, out = _dry(METHODS='lmkcdey')
    check("a name is case-insensitive", meths, "LMKCDEY")
    check("...LMKCDEY brings its two-base rows and the GINX control on them",
          (per.get('maps'), per.get('maps-ginx')), ("3", "2"))
    rc, stages, meths, per, out = _dry(METHODS='AP')
    check("AP has no two-base map rows of its own", stages.split(), ['multi', 'single'])
    rc, stages, meths, per, out = _dry(METHODS='2,3')
    check("the arm's numbers are accepted, comma separated", meths, "GINX LMKCDEY")
    check("...and reach the sweep as a list", per.get('multi'), "2 3")
    rc, _, _, _, out = _dry(METHODS='GNIX')
    check("a misspelt method is refused, not silently dropped",
          (rc, 'unknown method' in out), (2, True))

    print("\na partial paste records that the table is now mixed")
    with tempfile.TemporaryDirectory() as td:
        fit = os.path.join(td, 'fit.txt')
        open(fit, 'w').write("GATE_COST = {\n"
                             "    ('GINX', 512, 32): (1.0, 2.0, 3.0, 0.000),\n}\n")
        note = os.path.join(td, 'note.txt')
        open(note, 'w').write("    # Measured on a 72-core box.\n")
        r = _sp.run([sys.executable, os.path.join(TOOLS_D, 'apply_cells.py'), fit,
                     '--regime', 'multi/libomp', '--comment', note, '--merge', '-n'],
                    capture_output=True, text=True,
                    cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
        check("a one-method fit keeps the other methods' cells",
              ('kept cells for AP, LMKCDEY' in r.stdout, r.returncode), (True, 0))
        check("...and the comment says which rows it describes",
              'describes the GINX rows' in r.stdout, True)
        check("...naming the ones it does not",
              'The AP and LMKCDEY rows below' in r.stdout, True)
        # Extending a table -- same methods, new (N, word) cells -- keeps rows
        # whose provenance lives in the comment the new --comment replaces. That
        # deleted the N<=2048 provenance once; the previous comment is preserved.
        fit2 = os.path.join(td, 'fit2.txt')
        open(fit2, 'w').write("GATE_COST = {\n"
                              "    ('GINX', 4096, 32): (1.0, 2.0, 3.0, None),\n}\n")
        r2 = _sp.run([sys.executable, os.path.join(TOOLS_D, 'apply_cells.py'), fit2,
                      '--regime', 'multi/libomp', '--comment', note, '--merge', '-n'],
                     capture_output=True, text=True,
                     cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
        check("extending a table keeps the comment describing the kept rows",
              'kept the previous comment' in r2.stdout, True)
        check("...so the older measurement is still documented",
              'AP at 238153db' in r2.stdout, True)

    print("\nthe regime's provenance is per method, and derived rather than kept by hand")
    _r = dict(by_method={
        "GINX": dict(pin="a" * 40, on="2026-09-10", points=220, box="box A"),
        "AP": dict(pin="b" * 40, on="2026-09-07", points=563, box="box A")})
    m._rollup_regime(_r)
    check("points is the sum of the per-method counts, not a literal", _r["points"], 783)
    check("...measured is the newest of them", _r["measured"], "2026-09-10")
    check("...pins_by_method is derived", _r["pins_by_method"]["AP"], "b" * 40)
    check("...one box everywhere is that box", _r["box"], "box A")
    _r2 = dict(by_method={
        "GINX": dict(pin="a" * 40, on="2026-09-10", points=220, box="box B"),
        "AP": dict(pin="b" * 40, on="2026-09-07", points=563, box="box A")})
    m._rollup_regime(_r2)
    # Naming the box with the most timings would say "box A" over a table whose
    # GINX rows -- the ones a default search prices -- came off box B.
    check("two boxes are not collapsed into whichever has more rows",
          "two or more machines" in _r2["box"], True)
    # AP exists in the default regime only, and a regime with no AP cell prices
    # no AP candidate rather than extrapolating one from the 8-thread numbers.
    check("AP is calibrated in multi/libomp and nowhere else",
          {"/".join(k) for k, r in m.COST_REGIMES.items()
           if any(c[0] == "AP" for c in r["cells"])}, {"multi/libomp"})
    _saved = m.ACTIVE_REGIME
    try:
        m.set_cost_regime("single", "libomp")
        check("...so AP at one thread is refused, not guessed",
              m.gate_us({64: 512}, 27, 1024, method="AP", word_size=32,
                        base_r=8, q=2048), None)
    finally:
        m.set_cost_regime(*_saved)
    # The INVARIANT, not the shipped totals: a recalibration on this machine is
    # supposed to change those numbers, and a test that pins them fails for the
    # one user who did what the docs asked.
    check("every regime's points is the sum of its per-method records",
          [k for k, r in m.COST_REGIMES.items()
           if r["points"] != sum(v["points"] for v in r["by_method"].values())], [])
    check("...and every method with cells has a record",
          [k for k, r in m.COST_REGIMES.items()
           if {c[0] for c in r["cells"]} - set(r["by_method"])], [])
    prov2 = m.cost_provenance()
    _active = m.COST_REGIMES[m.ACTIVE_REGIME]
    check("provenance prints each method's pin, timing count and date",
          [me for me in _active["by_method"]
           if not re.search(r"%s [0-9a-f]{8} \(\d+ timings, \d{4}-\d\d-\d\d\)" % me,
                            prov2)], [])

    print("\napply_cells writes that record for the methods it pasted")
    sys.path.insert(0, TOOLS_D)
    import apply_cells as ac
    with tempfile.TemporaryDirectory() as td:
        fit = os.path.join(td, "fit.txt")
        open(fit, "w").write(
            "  method   N      word  pts  c0     c1      c2      c3   med|res| worst\n"
            "  GINX     512    32    27   -81.2  14.746  1.0439  -    1.67%    5.81%\n"
            "  GINX     512    64    33   -72.2  22.149  2.5567  -    1.54%    4.59%\n"
            "\nGATE_COST = {\n"
            "    ('GINX', 512, 32): (-81.2, 14.746, 1.0439, None),\n"
            "    ('GINX', 512, 64): (-72.2, 22.149, 2.5567, None),\n}\n")
        check("the timing count comes from the fit's own pts column",
              ac.fit_points(fit), {"GINX": 60})
        model_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      os.pardir, "scripts", "paramsestimator",
                                      "dse_model.py")).read()
        # The regime's own pin, read from the regime rather than named: the
        # constant moves with every pin move and this test is about the carry
        # logic, not about which commit is current.
        here = m.COST_REGIMES[("multi", "libomp")]["pin"]
        out, moved = ac.patch_regime(model_src, "multi/libomp",
                                     {"GINX": (here, "2026-09-10", 60, "box B")})
        check("the pasted method's record is rewritten",
              'dict(pin="%s", on="2026-09-10", points=60' % here in out, True)
        check("...the others keep theirs verbatim, symbols and all",
              '"AP":      dict(pin=PIN_2381, on="2026-09-07",  points=563, box=COST_BOX)' in out,
              True)
        check("...and a fit at the regime's own pin does not move it", moved, None)
        out2, moved2 = ac.patch_regime(model_src, "multi/libomp",
                                       {"GINX": ("c" * 40, "2026-09-10", 60, "box B")})
        check("a fit at a different commit moves the pin and says so",
              (moved2[0], moved2[1]), (here, "c" * 40))
        # the patched source must still roll up, which is the check that the
        # rewrite produced valid Python rather than plausible-looking text
        mdir = os.path.join(td, "m")
        os.makedirs(mdir)
        open(os.path.join(mdir, "dse_model.py"), "w").write(out)
        sys.path.insert(0, mdir)
        for name in [n for n in list(sys.modules) if n == "dse_model"]:
            del sys.modules[name]
        import dse_model as m3
        check("the patched model imports and rolls up to the new total",
              m3.COST_REGIMES[("multi", "libomp")]["points"], 563 + 60 + 530)
        # A pin move makes the methods NOT in the fit carried to a commit nobody
        # measured them at. Where the entry has no carried_why, that claim stands
        # with nothing behind it, and the entry that needs one is the quiet case.
        m2dir = os.path.join(td, "m2")
        os.makedirs(m2dir)
        m2 = os.path.join(m2dir, "dse_model.py")
        open(m2, "w").write(model_src)
        ac_py = os.path.join(TOOLS_D, "apply_cells.py")
        r_s = _sp.run([sys.executable, ac_py, fit, "--regime", "single/libomp",
                       "--merge", "--update-regime", "--model", m2,
                       "--pin", "c" * 40, "--box", "box B", "--on", "2026-09-10"],
                      capture_output=True, text=True)
        check("a pin move into an entry with no carried_why says so",
              "NO carried_why" in r_s.stdout, True)
        check("...naming the method it now carries",
              "LMKCDEY is now CARRIED" in r_s.stdout, True)
        open(m2, "w").write(model_src)
        r_m = _sp.run([sys.executable, ac_py, fit, "--regime", "multi/libomp",
                       "--merge", "--update-regime", "--model", m2,
                       "--pin", "c" * 40, "--box", "box B", "--on", "2026-09-10"],
                      capture_output=True, text=True)
        check("...and where there is one, asks whether it still covers the span",
              "Check `carried_why`" in r_m.stdout, True)
        check("...listing both carried methods", "AP and LMKCDEY are now CARRIED"
              in r_m.stdout, True)
        check("...with the two boxes reported separately",
              "boxes: AP on" in m3.cost_provenance(), True)
        sys.path.remove(mdir)
        del sys.modules["dse_model"]
        import dse_model as m  # noqa: F811  restore the real one for later checks
    sys.path.remove(TOOLS_D)

    print("\na verdict reads the wrong-answer count, not sigma alone")
    import io as _io, contextlib as _ctx
    import dse_verify as _v

    def _log(path, fails_on_one=0, field=True, sd0=18.4165):
        import random as _rnd
        _rnd.seed(7)
        with open(path, 'w') as f:
            for k in range(12):
                sd = sd0 * (1 + _rnd.gauss(0, 0.014))
                ff = ' FAILURES=%d' % (fails_on_one if k == 3 else 0) if field else ''
                f.write("record| label=pick set=- inputs=2 N=1024 n=554 q=2048 "
                        "logQ=27 qks=32768 baseks=32 gmap=128:554 method=2 keys=1 "
                        "sigma=%.6f mean=0.5 samples=1250%s\n" % (sd, ff))

    def _run(path):
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc_ = _v.report(_v.read_keys(path), 2, 2048, -64.0,
                            samples_per_key=1250, log=path)
        return rc_, buf.getvalue()

    with tempfile.TemporaryDirectory() as td:
        clean = os.path.join(td, 'clean.log'); _log(clean, 0)
        rc_c, out_c = _run(clean)
        check("a clean run passes and says the check was made",
              (rc_c, 'PASS' in out_c, '0 wrong answer(s)' in out_c), (0, True, True))
        # Same sigma, same margin, 37 wrong answers: a gate that decrypts
        # incorrectly cannot be argued PASS on the noise distribution.
        wrong = os.path.join(td, 'wrong.log'); _log(wrong, 37)
        rc_w, out_w = _run(wrong)
        check("wrong answers override the verdict", (rc_w, 'FAIL' in out_w), (2, True))
        check("...and are named, not left to the reader to grep for",
              'WRONG ANSWERS: 37' in out_w, True)
        nof = os.path.join(td, 'nofield.log'); _log(nof, 0, field=False)
        rc_n, out_n = _run(nof)
        check("a log with no FAILURES field is unchecked, not zero",
              ('CORRECTNESS NOT CHECKED' in out_n, '0 wrong answer(s)' in out_n),
              (True, False))
        check("read_failures separates counted rows from rows without the field",
              (_v.read_failures(wrong)['pick'], _v.read_failures(nof)['pick']),
              ((37, 12, 0), (0, 0, 12)))
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            _v.report(_v.read_keys(clean), 2, 2048, -64.0, samples_per_key=1250)
        check("a report with no log says correctness was not checked",
              'not checked' in buf.getvalue(), True)

        # The table path emits the labels that ship in OpenFHE's enum comments,
        # so a cell that decrypts incorrectly must not get one.
        import dse_table as _t
        man = dict(samples=1250, quantile_budget=True, cells=[dict(
            name='pick', level='STD128', inputs=2, target=-64.0,
            pick=dict(N=1024, n=554, q=2048, logQ=27, log_q_big=27, q_ks=32768,
                      base_ks=32, gadget_map=[[128, 554]], autokeys=10,
                      key_dist='UNIFORM_TERNARY', sigma=3.19, inputs=2,
                      log2pf=-72.0, sigma_total=18.4, gate_us=1000.0,
                      base_r=None))])
        with open(os.path.join(td, 'manifest.json'), 'w') as f:
            json.dump(man, f)

        class _A(object):
            def __init__(self, log):
                self.dir = td; self.log = log
                self.emit_rows = True; self.emit_labels = True
                self.round = 1; self.label_from = 'edge'
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc_t = _t.finish(_A(wrong))
        out_t = buf.getvalue()
        check("a table cell with wrong answers fails the run", rc_t, 1)
        check("...and is excluded from the emitted rows and labels",
              ('enum comments' in out_t, 'STD128 : 2^(' in out_t), (True, False))
        check("...naming it as decrypting incorrectly",
              'DECRYPT INCORRECTLY' in out_t, True)
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc_t2 = _t.finish(_A(clean))
        check("a clean table emits its label and says the check was made",
              (rc_t2, 'STD128 : 2^(' in buf.getvalue(),
               'correctness: 0 wrong answers' in buf.getvalue()), (0, True, True))

    print("\nthe wizard runs the documented flow from a handful of answers")
    _repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
    WZ = os.path.join(_repo, 'scripts', 'paramsestimator', 'dse_wizard.py')
    def _wz(*args):
        r = _sp.run([sys.executable, WZ] + list(args), capture_output=True, text=True, cwd=_repo)
        return r.returncode, r.stdout + r.stderr
    rc, out = _wz('--yes', '--dry-run')
    check("every default names the set STD128", 'set name:  STD128' in out, True)
    check("...and runs four commands, none executed", (rc, '(dry run: 4 commands' in out), (0, True))
    order = [out.find(x) for x in (' doctor', 'dse_table.py plan', 'run-plan.sh', 'table finish')]
    check("...in the order doctor, plan, measure, certify", order == sorted(order) and -1 not in order, True)
    check("...carrying the documented defaults",
          '--key-cap-gib 4,AP=8 --key-cap-lambda 2' in out and '--keys 12 --samples 1250' in out, True)
    check("...as repo-relative paths, the way getting-started writes them",
          'scripts/paramsestimator/dse.py --threads multi --compiler clang doctor' in out
          and '/home/' not in out.split('== 1/5')[1], True)
    rc, out = _wz('--yes', '--dry-run', '--method', 'LMKCDEY', '--level', 'STD192Q',
                  '--inputs', '3', '--target=-128', '--keys', '16', '--key-cap-gib', 'none')
    check("answers reach the set name", 'set name:  LPF_STD192Q_3_LMKCDEY' in out, True)
    check("...and the plan command verbatim",
          '--methods LMKCDEY --inputs 3 --targets=-128' in out and '--key-cap-gib none' in out
          and '--keys 16' in out, True)
    rc, out = _wz('--yes', '--dry-run', '--method', 'AP', '--threads', 'single')
    check("a method with no cells in the regime forces recalibration",
          'recalibration is not optional' in out and 'recalibrate.sh' in out, True)
    check("...for that method and stage only", 'METHODS=AP STAGES=single bash' in out, True)
    check("...pasting into that regime with the provenance record",
          '--regime single/libomp' in out and '--update-regime' in out, True)
    rc, out = _wz('--yes', '--dry-run', '--method', 'AP', '--threads', 'single', '--recalibrate', 'no')
    check("declining it is a refusal, not a silent empty search",
          (rc != 0, 'nothing can be ranked' in out), (True, True))
    with tempfile.TemporaryDirectory() as td:
        d = os.path.join(td, 'wz')
        rc, out = _wz('--yes', '--dry-run', '--out-dir', d, '--samples', '2500')
        ans = json.load(open(os.path.join(d, 'wizard.json')))
        check("a dry run with a named directory records the resolved answers",
              (ans['set_name'], ans['samples'], ans['compiler']), ('STD128', 2500, 'clang'))
        harness = os.path.join(_repo, 'build', 'bin', 'boolean_noise_estimate_script')
        if os.path.exists(harness):
            print("  skip   outside-the-container refusal: this checkout has the harness built")
        else:
            rc, out = _wz('--yes', '--out-dir', os.path.join(td, 'wz2'))
            check("without the harness it refuses before anything measures",
                  (rc != 0, 'inside the container' in out), (True, True))

    print("\nthe control re-baseline tool writes what the log supports and refuses what it does not")
    import shutil, subprocess
    TOOLS_CR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'scripts', 'dse-tools')
    import dse_shipfit as _sf
    import dse_measured as _dm
    _live = _sf.shipped_params()
    _meth = {'GINX': '2', 'AP': '1', 'LMKCDEY': '3'}
    def _ctl_log(path, scale_on=1.004, fails=0, drop=None):
        lines = []
        for name, c in _dm.CONTROL.items():
            if name == drop:
                continue
            sig = _sf.predict(_live[name], c['method'])
            for arm, f in (('off', 1.0), ('on', scale_on)):
                lines.append("record| label=ctl_%s_%s set=%s inputs=2 N=- n=- q=- logQ=- qks=- baseks=- gmap=- "
                             "baseg=- baserk=- method=%s autokeys=- keydist=- keys=6 sigma=%.6f mean=0.1 "
                             "samples=14400 FAILURES=%d secs=500" % (name, arm, name, _meth[c['method']], sig * f, fails))
        open(path, 'w').write("\n".join(lines) + "\n")
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, 'ctl.log'); copy = os.path.join(td, 'dse_measured.py')
        shutil.copy(os.path.join(TOOLS_CR, os.pardir, 'paramsestimator', 'dse_measured.py'), copy)
        def _run(logpath, *extra):
            r = subprocess.run([sys.executable, os.path.join(TOOLS_CR, 'control_rebaseline.py'), logpath,
                                '--pin', 'f' * 40, '--on', '2026-01-02', '--file', copy] + list(extra),
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr
        _ctl_log(log)
        rc, out = _run(log)
        check("a clean log passes every check without --write", rc, 0)
        check("...and says what it would write", 'would write' in out, True)
        rc, out = _run(log, '--write')
        check("--write rewrites the copy", rc, 0)
        src = open(copy).read()
        check("...with the pin and date given", ('CONTROL_PIN = "ffffffff"' in src, 'CONTROL_MEASURED = "2026-01-02"' in src), (True, True))
        gm = _live['STD128']['gmap']
        check("...and each entry's parameters from the LIVE table",
              ("gmap={%s}" % ", ".join("%d: %d" % kv for kv in sorted(gm.items()))) in src, True)
        check("...the header naming the measurement", '# Measured at ffffffff, 2026-01-02, plans/control-rebaseline.cmds' in src, True)
        import importlib.util
        spec = importlib.util.spec_from_file_location("dm_copy", copy); dmc = importlib.util.module_from_spec(spec); spec.loader.exec_module(dmc)
        check("...the rewritten block still imports, with every set and 28800 pooled samples",
              (sorted(dmc.CONTROL) == sorted(_dm.CONTROL), {c['samples'] for c in dmc.CONTROL.values()}), (True, {28800}))
        _ctl_log(log, fails=1)
        rc, out = _run(log, '--write')
        check("a wrong answer refuses the write", (rc, 'WRONG ANSWERS' in out), (1, True))
        _ctl_log(log, scale_on=1.10)
        rc, out = _run(log, '--write')
        check("arms 10% apart refuse the write", (rc, 'ARMS DISAGREE' in out), (1, True))
        _ctl_log(log, drop='STD256Q')
        rc, out = _run(log, '--write')
        check("a set missing from the log refuses the write, naming it", (rc, 'STD256Q' in out), (1, True))

    print("\nthe correctness gate's error bar counts every gate once")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                                    'scripts', 'dse-tools'))
    import gate_compare as gc
    # run-plan records `samples` as the TOTAL over keys (-i 200 -K 2 -> 400), so
    # the sampling variance is 1/(2*S), not 1/(2*S*K): a two-key gate row
    # resolves 5.2 percent on a ratio and a six-key -i 400 row 2.2, not the 3.8
    # and 1.2 that counting each gate K times gave.
    _two = dict(sigma='6.0', keys=2, samples=400)
    _six = dict(sigma='18.0', keys=6, samples=2400)
    check("a 400-gate, 2-key row has a 5.2% 1-sigma on its ratio",
          round(100 * gc.z_between(_two, _two, 0.0138)[2], 1), 5.2)
    check("a 2400-gate, 6-key row has a 2.2% 1-sigma on its ratio",
          round(100 * gc.z_between(_six, _six, 0.0138)[2], 1), 2.2)
    check("...so a 6.8% off/on difference at six keys is 3.1 sigma, not 5.9",
          round(gc.z_between(dict(_six, sigma='18.012'), dict(_six, sigma='19.232'), 0.0138)[1], 1), 3.1)
    check("the scatter term still shrinks with keys and the sampling term with gates",
          gc.rel_var(dict(keys=12, samples=2400), 0.0138) < gc.rel_var(_six, 0.0138)
          and gc.rel_var(dict(keys=6, samples=4800), 0.0138) < gc.rel_var(_six, 0.0138), True)

    print("\nthe correctness gate tests against the shift a pin should produce")
    import subprocess, textwrap
    TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'scripts', 'dse-tools')
    def _rec(label, sigma, keys, samples, **kw):
        f = dict(label=label, set='-', inputs=2, N=512, n=512, q=1024, logQ=27, qks=32768,
                 baseks=32, gmap='32:512', baseg='-', baserk='-', method=2, autokeys='-',
                 keydist='UNIFORM_TERNARY', keys=keys, sigma='%.6f' % sigma, mean=0.5,
                 samples=samples, FAILURES=0)
        f.update(kw)
        return 'record| ' + ' '.join('%s=%s' % kv for kv in f.items())
    with tempfile.TemporaryDirectory() as td:
        # one row measured coarsely (2 keys x 200, the gate's stock plan) and one precisely
        old_log = os.path.join(td, 'old.log'); new_log = os.path.join(td, 'new.log')
        open(old_log, 'w').write("\n".join([
            _rec('coarse', 6.0, 2, 400), _rec('precise', 6.0, 16, 40000)]) + "\n")
        # both moved +20%, far more than any layout change predicts
        open(new_log, 'w').write("\n".join([
            _rec('coarse', 7.2, 2, 400), _rec('precise', 7.2, 16, 40000)]) + "\n")
        def run(*extra):
            r = subprocess.run([sys.executable, os.path.join(TOOLS, 'gate_compare.py'),
                                old_log, new_log] + list(extra),
                               capture_output=True, text=True)
            return r.returncode, r.stdout
        rc, out = run()
        check("a 20% move fails the gate on BOTH rows, coarse included", rc, 1)
        named = out.split('|z| >= 3.0:')[1].split('missing')[0]
        check("...naming both", ("'precise'" in named, "'coarse'" in named), (True, True))
        check("with no shift expected, nothing is flagged unconfirmable",
              'cannot confirm' in out, False)
        check("the summary prices a lone exceedance against chance",
              'draw, not a result' in out, True)
        # with the shift EXPECTED, both agree and the gate passes
        rc2, out2 = run('--expect', 'precise=1.20', '--expect', 'coarse=1.20')
        check("a row that moves by its expected ratio passes", rc2, 0)
        check("...and the verdict says so", 'GATE PASS' in out2, True)
        # a 2% predicted shift: the coarse row (1-sigma 5.2%) cannot confirm it,
        # the precise one (0.7%) can, and both match it so the gate passes
        open(new_log, 'w').write("\n".join([
            _rec('coarse', 5.88, 2, 400), _rec('precise', 5.88, 16, 40000)]) + "\n")
        rc4, out4 = run('--expect', 'coarse=0.98', '--expect', 'precise=0.98')
        check("a predicted shift the rows match passes", rc4, 0)
        MARK = '1-sigma exceeds it): '
        named4 = out4.split(MARK)[1].split(chr(10))[0] if MARK in out4 else ''
        check("...with the coarse row unable to confirm it", "'coarse'" in named4, True)
        check("...and the precise row able to", "'precise'" in named4, False)
        open(new_log, 'w').write("\n".join([
            _rec('coarse', 7.2, 2, 400), _rec('precise', 7.2, 16, 40000)]) + "\n")
        # a wrong answer fails whatever the ratio says
        open(new_log, 'w').write("\n".join([
            _rec('coarse', 6.0, 2, 400), _rec('precise', 6.0, 16, 40000, FAILURES=3)]) + "\n")
        rc3, out3 = run('--expect', 'precise=1.0')
        check("a wrong answer fails the gate even at the expected ratio", rc3, 1)
        # the expect-file generator reproduces what the model predicts for a row
        exp = os.path.join(td, 'expect.txt')
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'gate_expect.py'),
                            new_log, '--out', exp], capture_output=True, text=True)
        check("gate_expect writes a ratio per geometry row", r.returncode, 0)
        got = dict(l.split() for l in open(exp) if not l.startswith('#'))
        m.KSK_ZERO_ROWS_DROPPED = False
        s_off = m.sigma_total_at_q(512, 512, 1024, 27, 32768, 32, 3.19, {32: 512},
                                   key_dist='UNIFORM_TERNARY', method='GINX')
        m.KSK_ZERO_ROWS_DROPPED = True
        s_on = m.sigma_total_at_q(512, 512, 1024, 27, 32768, 32, 3.19, {32: 512},
                                  key_dist='UNIFORM_TERNARY', method='GINX')
        check("...matching the model's own two-layout ratio", float(got['precise']), s_on / s_off, tol=1e-6)
        check("...which is a decrease, not a wash", s_on < s_off, True)

    print("\ntwo-base LMKCDEY pays for the widest team width in a threaded regime")
    gm2 = {128: 300, 512: 254}
    c = m.GATE_COST[('LMKCDEY', 1024, 32)]
    plain = c[0] + c[1] * 554 + c[2] * m.gate_work(gm2, 27, method='LMKCDEY') + c[3] * 1024 / 40
    extra = m.map_width_us(gm2, 27, c[2], m.LMKCDEY_MAP_WIDTH_SHARE)
    check("the term is share*c2 on the digits the coarse indices do not have",
          extra, 0.4 * c[2] * 254 * (m.digits_g2(m.digits_for_base(27, 128)) - m.digits_g2(m.digits_for_base(27, 512))), tol=1e-9)
    check("gate_us carries it in multi/libomp", m.gate_us(gm2, 27, 1024, method='LMKCDEY', word_size=32, autokeys=40), plain + extra, tol=1e-6)
    check("...at 1-3% of the gate", 0.005 < extra / plain < 0.03, True)
    check("a single-base map pays nothing", m.map_width_us({128: 554}, 27, c[2], 0.4), 0.0)
    check("GINX pays nothing on the same map",
          m.gate_us(gm2, 27, 1024, method='GINX', word_size=32),
          m.GATE_COST[('GINX', 1024, 32)][0] + m.GATE_COST[('GINX', 1024, 32)][1] * 554 + m.GATE_COST[('GINX', 1024, 32)][2] * m.gate_work(gm2, 27), tol=1e-6)
    saved = m.ACTIVE_REGIME
    try:
        m.set_cost_regime('single', 'clang')
        c1s = m.GATE_COST[('LMKCDEY', 1024, 32)]
        check("the single-thread regime has no width term (no team to idle)",
              m.gate_us(gm2, 27, 1024, method='LMKCDEY', word_size=32, autokeys=10),
              c1s[0] + c1s[1] * 554 + c1s[2] * m.gate_work(gm2, 27, method='LMKCDEY') + (c1s[3] or 0) * 1024 / 10, tol=1e-6)
        m.set_cost_regime('multi', 'gcc')
        check("multi/libgomp is flagged stale for two-base LMKCDEY", "STALE" in m.cost_provenance(), True)
    finally:
        m.set_cost_regime(*saved)
    prov = m.cost_provenance()
    _r = m.COST_REGIMES[("multi", "libomp")]
    check("multi/libomp says which library its cells describe",
          "describing OpenFHE %s" % _r["pin"][:8] in prov, True)
    check("...and where each method's cells were measured",
          [me for me, pin in _r["pins_by_method"].items()
           if "%s %s" % (me, pin[:8]) not in prov], [])
    check("...and what the carry to the pin rests on",
          "carried to %s" % _r["pin"][:8] in prov, True)
    check("...and is not flagged stale", "STALE" in prov, False)

    print("\nthe cost fitter reads two-base rows from the maps arm")
    import dse_gatefit as gf
    with tempfile.NamedTemporaryFile('w', suffix='.log', delete=False) as f:
        f.write("gatecost| meth=3 threads=8 word=32 build=build N=1024 q=2048 n=512 bG=- gmap=128:410,512:102 w=10 baseR=64 logQ=27 internal32=yes internal32ks=yes gate_us=21000 keygen_ms=100 btkey_b=1\n")
        f.write("gatecost| meth=3 threads=8 word=32 build=build N=1024 q=2048 n=512 bG=128 w=10 baseR=64 logQ=27 internal32=yes internal32ks=yes gate_us=22000 keygen_ms=100 btkey_b=1\n")
        gpath = f.name
    try:
        rows, bad = gf.read(gpath)
        check("both rows parse", (len(rows), len(bad)), (2, 0))
        two = next(r for r in rows if r['gmap'])
        check("two-base gate work is the map's, not a single base's",
              two['gw'], m.gate_work({128: 410, 512: 102}, 27, method='LMKCDEY'))
        check("...and is less than the all-fine single base's", two['gw'] < rows[1]['gw'], True)
        check("digit count reported is the map's maximum (the team width)",
              two['d'], m.digits_for_base(27, 128))
        check("gw_max charges every index at the widest base", two['gw_max'], 512 * m.digits_g2(m.digits_for_base(27, 128)))
    finally:
        os.unlink(gpath)

    print()
    if FAILED:
        print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
