#!/usr/bin/env bash
#
# ---------------------------------------------------------------------------
# Designed cells that make a key-layout noise change VISIBLE. Run at both pins,
# from the repo root inside the container:
#
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
#       estimator bash scripts/dse-arms/keylayout-cells.sh > cells-<pin>.log
#
# then compare the two logs:
#
#   sage -python scripts/dse-tools/keylayout_fit.py cells-OLD.log cells-NEW.log
#
# About 25 minutes per pin. Sole job on the box is NOT required: these measure
# noise, not time.
# ---------------------------------------------------------------------------
# WHY THESE GEOMETRIES. A key-layout change that touches noise touches it through
# the key-switch term, whose size relative to the whole is `ks_share`, and whose
# per-position factor is 1/b_KS. On any shipped row that product is tiny: at
# b_KS 256 the OpenFHE 94229558 zero-row change is 0.2 percent of sigma, which no
# affordable run resolves and which the correctness gate's 400-gate rows cannot
# see at all. Push b_KS down to 4..16 with a key-switch-dominated geometry and the
# same change is 3 to 7 percent -- a 5 to 10 sigma test at 8 keys x 2500 gates.
#
# These are noise-physics cells and are NOT deployable: b_KS 4 over 8 digits is
# absurd for a real set. That is the point. The last row is the control, at
# b_KS 512, where the factor is 1/512 and the prediction is no change; if the
# control moves, something other than the layout did.
#
# WHAT IT MEASURED. At 94229558 against 41709fbc, all five cells landed within
# 1 sigma of prediction (ratios 0.9267, 0.9290, 0.9620, 0.9785, control 0.9957),
# which is what confirmed the (1 - 1/r) factor. The same cells also exposed that
# the model predicts POOLED sigma while this harness reports the WITHIN-key sigma
# -- a gap of ks_share/b_KS, invisible at b_KS 256 and 13 percent at 4. Compare
# against the within-key statistic (keylayout_fit.py does).
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT" || exit 2

KEYS=${KEYS:-8}
SAMPLES=${SAMPLES:-2500}

PLAN=$(mktemp /tmp/keylayout-cells.XXXXXX.cmds)
{
  echo "# Designed key-layout cells, ${KEYS} keys x ${SAMPLES} gates each."
  echo "# One key per process, so each record's sigma is that key's own and the"
  echo "# per-key spread is recoverable."
  #        label                n     N     q     Q   qKS      bKS   G
  for spec in \
      "zr_bks4_d8_n512      512   1024  2048  27  65536    4    128:512" \
      "zr_bks8_d5_n512      512   1024  2048  27  16384    8    128:512" \
      "zr_bks16_d4_n512     512   1024  2048  27  16384   16    128:512" \
      "zr_bks8_d6_n1024    1024   1024  2048  27  65536    8   128:1024" \
      "zr_ctl_bks512        512   1024  2048  27  65536  512    128:512" ; do
      set -- $spec
      echo "# label: $1"
      for _ in $(seq "$KEYS"); do
          echo "build/bin/boolean_noise_estimate_script -n $2 -N $3 -q $4 -Q $5 -k $6 -b $7 -G $8 -r 64 -s 3.19 -d 1 -t 2 -I 2 -i $SAMPLES -K 1 -Z"
      done
  done
} > "$PLAN"

echo "keylayout-cells: 5 cells x $KEYS keys from $PLAN" >&2
bash scripts/run-plan.sh "$PLAN"
rc=$?
rm -f "$PLAN"
[[ $rc -eq 0 ]] || echo "keylayout-cells: the plan did not run to completion" >&2
echo "KEYLAYOUT CELLS DONE"
exit $rc
