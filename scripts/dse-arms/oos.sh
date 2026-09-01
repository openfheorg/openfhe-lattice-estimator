#!/usr/bin/env bash
#
# ---------------------------------------------------------------------------
# A DSE calibration arm. Run from the repo root INSIDE the container, which is
# where build/ lives:
#
#   docker compose run --rm estimator bash scripts/dse-arms/oos.sh
#
# Output goes to stdout as one parseable record per configuration; redirect it
# and keep the file -- these runs cost machine time and the records are the only
# durable product.
#
# TIMING ARMS NEED AN IDLE MACHINE. Anything measuring gate_us is measuring wall
# clock; another job on the box makes the numbers wrong rather than noisy. State
# the thread count and, on a multi-socket machine, the NUMA binding with any
# many-thread figure.
# ---------------------------------------------------------------------------
# OUT-OF-SAMPLE validation of the re-fitted GATE_COST cells.
#
# The 236-point refit residuals are IN-SAMPLE. The evidence that the FORM is
# right has to come from configurations the fit never saw -- which is what the
# shipped sets are. Same protocol as the original SHIPPED_COST: idle box,
# 8 threads, boolean_estimate_time so keygen is separated from gate time.
#
# boolean_estimate_time takes no -G, so multi-base sets (the LPF family) are
# excluded, and no -p, so parameters are passed explicitly. AP excluded: the
# model refuses it.
set -u
run() { local label=$1 build=$2 meth=$3; shift 3
  out=$($build/bin/boolean_estimate_time -n $1 -N $2 -q $3 -Q $4 -k $5 -b $6 -g $7 -t $meth -d $8 -a $9 2>&1)
  gt=$(sed -n "s/.*gate evaluation \([0-9]*\) milliseconds.*/\1/p" <<<"$out" | head -1)
  bk=$(sed -n "s/.*bootstrapping key size: \([0-9]*\).*/\1/p" <<<"$out" | head -1)
  ks=$(sed -n "s/.*key switching key size: \([0-9]*\).*/\1/p" <<<"$out" | head -1)
  printf "oos| set=%s meth=%s build=%s n=%s N=%s q=%s logQ=%s gate_us=%s btkey_b=%s ksk_b=%s\n" \
    "$label" "$meth" "$build" "$1" "$2" "$3" "$4" \
    "$( [ -n "${gt:-}" ] && echo $(( gt * 1000 / 8 )) || echo NA )" "${bk:-NA}" "${ks:-NA}"; }
run MEDIUM                 build    2   422  1024  1024  28    16384  128     1024 1 10
run STD128                 build    2   556  1024  2048  27    32768   32      128 1 10
run STD128Q                build    2   601  1024  2048  25    32768   32       16 1 10
run STD128Q_3              build    2   641  1024  2048  25    65536   64       16 1 10
run STD128Q_3_LMKCDEY      build    3   641  1024  2048  25    65536   64       16 1 10
run STD128Q_4              build    2   683  2048  4096  50   131072   64   131072 1 10
run STD128Q_4_LMKCDEY      build    3   685  1024  2048  25   131072   64       16 1 10
run STD128Q_LMKCDEY        build    3   640  1024  1024  25    32768   32      128 1 10
run STD128_3               build    2   595  1024  2048  27    65536   64      128 1 10
run STD128_3_LMKCDEY       build    3   595  1024  2048  27    65536   64      128 1 10
run STD128_4               build    2   635  1024  2048  27   131072   64       32 1 10
run STD128_4_LMKCDEY       build    3   635  1024  2048  27   131072   64       64 1 10
run STD128_LMKCDEY         build    3   581  1024  1024  27    32768   32      512 1 10
run STD192                 build    2   821  2048  2048  37    32768   32     8192 1 10
run STD192Q                build    2   890  2048  2048  34    32768   32     4096 1 10
run STD192Q_3              build    2   948  2048  2048  34    65536   64     4096 1 10
run STD192Q_3_LMKCDEY      build    3   948  2048  2048  34    65536   64     4096 1 10
run STD192Q_4              build    2  1009  2048  4096  34   131072   64     4096 1 10
run STD192Q_4_LMKCDEY      build    3  1009  2048  4096  34   131072   64     4096 1 10
run STD192Q_LMKCDEY        build    3   778  2048  4096  36    32768   32     4096 0 10
run STD192_3               build    2   876  2048  2048  37    65536   64     8192 1 10
run STD192_3_LMKCDEY       build    3   876  2048  2048  37    65536   64     1024 1 10
run STD192_4               build    2   932  2048  4096  37   131072   64     8192 1 10
run STD192_4_LMKCDEY       build    3   932  2048  4096  37   131072   64     1024 1 10
run STD192_LMKCDEY         build    3   716  2048  4096  39    32768   32  1048576 0 10
run STD256                 build    2  1299  2048  2048  29   262144   64     1024 1 10
run STD256Q                build    2  1242  2048  2048  26    65536   64       64 1 10
run STD256Q_3              build    2  1319  2048  4096  26   131072   64       32 1 10
run STD256Q_3_LMKCDEY      build    3  1319  2048  4096  26   131072   64       64 1 10
run STD256Q_4              build    2  1319  2048  4096  26   131072   64       16 1 10
run STD256Q_4_LMKCDEY      build    3  1319  2048  4096  26   131072   64       32 1 10
run STD256Q_LMKCDEY        build    3  1242  2048  2048  26    65536   64      128 1 10
run STD256_3               build    2  1241  2048  2048  29   131072   64      256 1 10
run STD256_3_LMKCDEY       build    3  1218  2048  2048  29   131072   64      256 1 10
run STD256_4               build    2  1218  2048  4096  29   131072   64       32 1 10
run STD256_4_LMKCDEY       build    3  1218  2048  4096  29   131072   64      256 1 10
run STD256_LMKCDEY         build    3  1079  2048  2048  29    32768   32     1024 1 10
echo "OOS DONE"
