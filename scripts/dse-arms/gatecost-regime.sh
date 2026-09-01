#!/usr/bin/env bash
# Measure GATE_COST cells for ONE (thread mode, compiler) regime, BOTH widths.
#
# WHY BOTH WIDTHS IN ONE ARM. The unified search compares a w32 candidate
# directly against a w64 baseline, so the two widths must be measured at the SAME
# pin under the SAME regime or that comparison inherits whatever changed between
# them. Carrying w32 cells at one pin and w64 cells at another is not a footnote:
# bf3c9ca5 moved 8-thread w32 by 13-21% and w64 by only 5-10%, and an uneven
# shift lands entirely on the w32-vs-w64 verdict.
#
# The cells are also only valid for the thread count and compiler they were
# measured under. gcc leads clang on GINX gates at 8 threads and clang leads at 1
# thread, so the regime REORDERS builds rather than scaling them -- the same
# hazard as f9694c39 parallelising CGGI alone, which invalidated every
# method-vs-method conclusion drawn on the serial cells.
#
#   THREADS=1 bash scripts/dse-arms/gatecost-regime.sh > st.log      # single
#   THREADS=8 bash scripts/dse-arms/gatecost-regime.sh > mt.log      # multi
#
# The COMPILER axis needs a separate image, since OpenFHE and the harness both
# have to be built with it (gcc-14 and clang-18 are both installed):
#
#   docker build -t openfhe-lattice-estimator:<ref>-gcc \
#       --build-arg CC_BIN=gcc-14 --build-arg CXX_BIN=g++-14 .
#
# Keep WITH_NATIVEOPT=ON and build on the box you will measure on, or the images
# are not comparable. Then, per log:
#
#   dse.py costfit <log> --emit
#
# and paste each cell into the matching COST_REGIMES entry in dse_model.py. The
# fitter partitions by the `build` field this arm records, so one log yields both
# widths' cells.
#
# WIDTHS. w32 is measured through the HYBRID (the 32-bit internal key forms on
# the 64-bit build), which measured within 3.3% of a genuine NATIVE_SIZE=32
# build at four matched configs (that genuine build is gone from the image: it
# lacked HAVE_INT128 and lost past six digits). Since OpenFHE 9e8045db the hybrid is the
# library DEFAULT -- BTKeyGen's internal32 defaults to true -- so it is exactly
# what a user on a default build gets; -3 below just says so explicitly. w64 is
# the same binary at logQ 37, where the refresh key cannot fit, run WITHOUT a
# flag so that it too is the default caller's path: the SWITCHING key still
# narrows there (qKS 2^15 fits whatever Q is), and the record's internal32ks=
# field says so. Pass -6 to force both keys 64-bit; no cell here does, because
# no default caller gets that path any more.
#
# LOG Q. Held at 27 for w32 (the hybrid caps Q at 2^28) and 37 for w64 (a real
# w64 value; every shipped w64 set is logQ 29-37). Gate cost measured flat in
# logQ at fixed digit count, so logQ enters only through the digit count, and the
# base sweeps below are exactly the undominated bases at each logQ -- every one
# checked against the width rule before spending machine time on it.
set -u
: "${THREADS:=8}"
export OMP_NUM_THREADS=$THREADS

emit() {  # meth N q n bG w logQ flag word baseR
  # -r is AP's refresh-key base. It is emitted for every method because the
  # binary takes it harmlessly elsewhere, and for AP it is the parameter that
  # moves the external-product count -- and therefore the accumulator work the
  # fit is trying to identify. Without it swept, an AP cell has no variation in
  # gate work beyond the gadget and c2 comes back meaningless.
  out=$(build/bin/boolean_estimate_time -n $4 -N $2 -q $3 -Q $7 -k 32768 -b 32 \
        -G $5:$4 -t $1 -d 1 -a $6 -r ${10:-64} $8 2>&1)
  gt=$(sed -n 's/.*gate evaluation \([0-9]*\) milliseconds.*/\1/p' <<<"$out" | head -1)
  kg=$(sed -n 's/.*key generation \([0-9]*\) milliseconds.*/\1/p' <<<"$out" | head -1)
  bk=$(sed -n 's/.*bootstrapping key size: \([0-9]*\).*/\1/p' <<<"$out" | head -1)
  i32=$(sed -n 's/.*internal32 refresh key: \(.*\)/\1/p' <<<"$out" | head -1)
  i32ks=$(sed -n 's/.*internal32 switch key: \(.*\)/\1/p' <<<"$out" | head -1)
  printf 'gatecost| meth=%s threads=%s word=%s build=build N=%s q=%s n=%s bG=%s w=%s baseR=%s logQ=%s internal32=%s internal32ks=%s gate_us=%s keygen_ms=%s btkey_b=%s\n' \
    "$1" "$THREADS" "$9" "$2" "$3" "$4" "$5" "$6" "${10:-64}" "$7" "${i32:-?}" "${i32ks:-?}" \
    "$( [ -n "${gt:-}" ] && echo $(( gt * 1000 / 8 )) || echo NA )" \
    "${kg:-NA}" "${bk:-NA}"
}

# undominated bases at each logQ; d = 2..27 (w32) and 2..37 (w64)
BASES32="16384 512 128 64 32 16 8 4 2"
BASES64="524288 8192 1024 256 128 64 32 16 8 4 2"

log2i() { local v=$1 r=0; while [ "$v" -gt 1 ]; do v=$((v / 2)); r=$((r + 1)); done; echo $r; }

# AP refresh-key size in GiB: 4 * N * wordbytes * (baseR-1) * digitsR * n * (digitsG-1).
# Every argument here is a power of two, so the digit counts are bit arithmetic.
ap_key_gib() {  # N wordbytes baseR q n baseG logQ
  local N=$1 WB=$2 BR=$3 Q=$4 n=$5 BG=$6 LQ=$7
  local gb dg qb bb dr
  gb=$(log2i "$BG"); dg=$(( (LQ + gb - 1) / gb ))
  qb=$(log2i "$Q");  bb=$(log2i "$BR"); dr=$(( (qb + bb - 1) / bb ))
  [ "$dg" -lt 2 ] && dg=2
  echo $(( 4 * N * WB * (BR - 1) * dr * n * (dg - 1) / 1073741824 ))
}

# WHICH CELLS TO MEASURE. Default is every one; set METHODS and/or NS to
# measure a subset, which is what you want after adding a method or after a pin
# move that touches one region. A cell is a (method, N, word), so these three
# select cells directly:
#
#   METHODS=1 bash scripts/dse-arms/gatecost-regime.sh > ap.log      # AP only
#   NS="1024 2048" METHODS=3 ... > lmk-big.log                       # two cells
#   WORDS=32 ...                                                     # one width
#   METHODS=3 MAPS=1 ...            # LMKCDEY cells plus its two-base map rows
#   METHODS=2 MAPS_ONLY=1 ...       # GINX two-base rows only, as the control
#
# Re-running the whole arm to add six cells re-measures the twelve you already
# have, which at these gate times is over an hour of the machine for nothing.
: "${METHODS:=1 2 3}"
: "${NS:=512 1024 2048}"
: "${WORDS:=32 64}"

sweep() {  # logQ flag word bases
  local LQ=$1 FLAG=$2 TAG=$3 BASES=$4
  case " $WORDS " in *" $TAG "*) ;; *) return 0 ;; esac
  # N=4096 is present but only runs when NS names it. It exists for the three
  # STD256Q arity-4 cells that have no survivor at N <= 2048, and it is NOT in the
  # default sweep: a cost cell nobody's candidate can reach is machine time spent
  # on nothing, and every other regime's cells stop at 2048.
  for spec in "1 512 1024" "1 1024 2048" "1 2048 4096" "1 4096 8192" \
              "2 512 1024" "2 1024 2048" "2 2048 4096" "2 4096 8192" \
              "3 512 1024" "3 1024 2048" "3 2048 4096" "3 4096 8192"; do
    set -- $spec; local METH=$1 N=$2 Q=$3
    case " $METHODS " in *" $METH "*) ;; *) continue ;; esac
    case " $NS " in *" $N "*) ;; *) continue ;; esac
    for BG in $BASES; do
      # n reaches N/2 so the fit does not have to extrapolate its linear term to
      # the n the cells actually want: the STD256Q arity-4 geometries sit near
      # n = 1300-1500, which is off the end of the 64..1024 ladder every other
      # cell was fitted on.
      local NLIST="64 256 512 1024"
      [ "$N" -ge 4096 ] && NLIST="64 256 512 1024 2048"
      for n in $NLIST; do
        [ "$n" -gt "$N" ] && continue
        # LMKCDEY only: w must move INSIDE a cell or c3 comes back None. The
        # fitter takes c3 from matched pairs differing only in w, so both values
        # are needed at the same (n, base).
        if [ "$METH" = 3 ]; then
          for W in 10 40; do emit $METH $N $Q $n $BG $W $LQ "$FLAG" "$TAG"; done
        elif [ "$METH" = 1 ]; then
          # AP: sweep the refresh base, because that is what moves its external
          # product count. Three bases span the range 5.5 -> 1.95 products per
          # coefficient at q = 2048, which is the variation c2 needs.
          for BR in 2 8 64; do
            # AP's refresh key holds (baseR-1)*digitsR keys PER COEFFICIENT, so a
            # coarse gadget and a coarse refresh base together are ruinous: at
            # N=2048, n=1024, base 2 (27 digits) and baseR 64 the key is ~110 GB
            # and the run would take the box down rather than fail. Skip and say
            # so, rather than discovering it as an OOM an hour in.
            gb=$(ap_key_gib $N $([ "$TAG" = 32 ] && echo 4 || echo 8) $BR $Q $n $BG $LQ)
            if [ "$gb" -gt "${AP_KEY_GIB_MAX:-8}" ]; then
              printf 'gatecost-skip| meth=1 N=%s n=%s bG=%s baseR=%s word=%s reason=refresh_key_%sGiB_over_%sGiB\n' \
                "$N" "$n" "$BG" "$BR" "$TAG" "$gb" "${AP_KEY_GIB_MAX:-8}"
              continue
            fi
            emit $METH $N $Q $n $BG 10 $LQ "$FLAG" "$TAG" $BR
          done
        else
          emit $METH $N $Q $n $BG 10 $LQ "$FLAG" "$TAG"
        fi
      done
    done
  done
}

# TWO-BASE MAP ROWS. The cells above are fitted on single-base maps, and LIB-63
# measured that after the library's team-width and scratch fixes (41709fbc) a
# two-base LMKCDEY pick still runs 2-3 points slower than the sum-of-digits work
# model says, and one N=2048 pick 14 points slower; GINX and AP two-base picks
# show no such residual. Since every region now runs at the WIDEST base's team
# width, the cost per index may follow the map's maximum digit count rather than
# each index's own -- which is what these rows are for: the same (N, n, pair) at
# five coarse fractions, so the fitter can tell a per-index term from a
# per-width one. MAPS=1 turns it on; GINX rows are the control that should show
# nothing. Rows carry gmap= (and bG=-), which dse_gatefit.read understands.
emit_map() {  # meth N q n MAP w logQ flag word
  out=$(build/bin/boolean_estimate_time -n $4 -N $2 -q $3 -Q $7 -k 32768 -b 32 \
        -G $5 -t $1 -d 1 -a $6 -r 64 $8 2>&1)
  gt=$(sed -n 's/.*gate evaluation \([0-9]*\) milliseconds.*/\1/p' <<<"$out" | head -1)
  kg=$(sed -n 's/.*key generation \([0-9]*\) milliseconds.*/\1/p' <<<"$out" | head -1)
  bk=$(sed -n 's/.*bootstrapping key size: \([0-9]*\).*/\1/p' <<<"$out" | head -1)
  i32=$(sed -n 's/.*internal32 refresh key: \(.*\)/\1/p' <<<"$out" | head -1)
  i32ks=$(sed -n 's/.*internal32 switch key: \(.*\)/\1/p' <<<"$out" | head -1)
  printf 'gatecost| meth=%s threads=%s word=%s build=build N=%s q=%s n=%s bG=- gmap=%s w=%s baseR=64 logQ=%s internal32=%s internal32ks=%s gate_us=%s keygen_ms=%s btkey_b=%s\n' \
    "$1" "$THREADS" "$9" "$2" "$3" "$4" "$5" "$6" "$7" "${i32:-?}" "${i32ks:-?}" \
    "$( [ -n "${gt:-}" ] && echo $(( gt * 1000 / 8 )) || echo NA )" \
    "${kg:-NA}" "${bk:-NA}"
}

maps_sweep() {  # logQ flag word
  local LQ=$1 FLAG=$2 TAG=$3
  case " $WORDS " in *" $TAG "*) ;; *) return 0 ;; esac
  # neighbouring undominated pairs at logQ 27 (digits 4/3, 5/4, 6/5): the pairs
  # every winning two-base pick has used. FINE first, then COARSE.
  for pair in "128 512" "64 128" "32 64"; do
    set -- $pair; local FINE=$1 COARSE=$2
    for spec in "3 1024 2048" "3 2048 4096" "2 1024 2048" "2 2048 4096"; do
      set -- $spec; local METH=$1 N=$2 Q=$3
      case " $METHODS " in *" $METH "*) ;; *) continue ;; esac
      case " $NS " in *" $N "*) ;; *) continue ;; esac
      for n in 256 512 1024; do
        [ "$n" -gt "$N" ] && continue
        # coarse-base fraction of the LWE indices; 0 and 100 are the single-base
        # rows the main sweep already has
        for PCT in 6 20 35 50 80; do
          local C=$(( (n * PCT + 50) / 100 )); local F=$(( n - C ))
          [ "$C" -lt 1 ] || [ "$F" -lt 1 ] && continue
          emit_map $METH $N $Q $n "$FINE:$F,$COARSE:$C" 10 $LQ "$FLAG" "$TAG"
        done
      done
    done
  done
}

# MAPS_ONLY=1 skips the single-base cells: the GINX control rows for the maps
# arm should not cost a re-timing of every GINX cell that LIB-63 says is unchanged.
if [ "${MAPS_ONLY:-0}" != 1 ]; then
  sweep 27 "-3" 32 "$BASES32"   # w32 accumulator (the default at this Q; -3 is explicit); word=32 keys the cell
  sweep 37 ""   64 "$BASES64"   # w64 accumulator, default flags: refresh key 64-bit, switching key 32-bit
fi
if [ "${MAPS:-0}" = 1 ] || [ "${MAPS_ONLY:-0}" = 1 ]; then maps_sweep 27 "-3" 32; fi
echo "GATECOST DONE"
