#!/usr/bin/env bash
#
# ---------------------------------------------------------------------------
# The correctness gate at a new OpenFHE pin. Run it from the repo root INSIDE
# the container, before any timing is trusted:
#
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
#       estimator bash scripts/dse-arms/pinmove-gate.sh [OLD.log]
#
# With no argument it measures and stops, which is what a first pin has to do.
# With the previous pin's log it also compares, and exits nonzero if the gate
# fails -- so a chain can launch the cost arm on this script's status.
#
# Output: the 20 records on stdout, the verdict on stdout after them. Keep the
# log; it is the baseline the NEXT pin move compares against.
# ---------------------------------------------------------------------------
# WHY THIS RUNS FIRST. Noise has survived every pin move so far and gate cost has
# survived none. Twenty rows of 400 gates cost minutes; a table measurement costs
# a day, and cost cells cost an hour per regime. This is the cheap check that the
# expensive step is measuring the right thing.
#
# WHY IT COMPARES AGAINST A PREDICTION, not against 1.0. Most pins leave noise
# alone. A pin that changes a noise term does not, and holding its rows to "no
# change" asks the wrong question: OpenFHE 94229558 drops the switching key's
# digit-value-zero rows, which lowers every row's key-switch variance by
# (1 - 1/r) per digit position. gate_expect.py prints the model's predicted ratio
# per row and gate_compare scores against it.
#
# WHAT THE ROWS CANNOT DO. 400 gates over 2 keys is about 5 percent of 1-sigma
# on a ratio. It detects a broken path; it cannot resolve a sub-percent shift. One
# row of twelve at 3 sigma is a 1-in-30 draw, and at 94229558 exactly that
# happened: a row read +11.4 percent and, re-measured at 8 keys x 2500 gates,
# read -2.0 percent, within 1.5 sigma of prediction. gate_compare prints each
# row's own 1-sigma and prices a lone exceedance against chance; when a row's
# verdict has to mean something, re-measure that geometry with more keys
# (docs/measurement-practice.md#correctness-gate-before-calibration).
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PLAN="$HERE/plans/pinmove-gate.cmds"
BASE="${1:-}"

cd "$ROOT" || exit 2
[[ -r "$PLAN" ]] || { echo "pinmove-gate: missing plan $PLAN" >&2; exit 2; }

# The log is the baseline the NEXT pin move compares against, so it lands in the
# repo root, named by the installed pin (gate-<pin8>.log, the name the docs use),
# not in the container's /tmp, which `run --rm` discards with the container.
# PINMOVE_LOG overrides the path.
ref=$(head -c 8 "${OPENFHE_REF_FILE:-/opt/openfhe/share/openfhe-src/OPENFHE_REF}" 2>/dev/null)
LOG="${PINMOVE_LOG:-$ROOT/gate-${ref:-unknown}.log}"
: > "$LOG" || { echo "pinmove-gate: cannot write $LOG" >&2; exit 2; }
echo "pinmove-gate: $(grep -c '^build/bin' "$PLAN") rows -> $LOG" >&2

# run-plan.sh refuses to print its completion marker for a run that produced
# nothing, so its status is meaningful here.
if ! bash scripts/run-plan.sh "$PLAN" | tee "$LOG"; then
    echo "pinmove-gate: the plan did not run to completion; nothing to compare." >&2
    exit 1
fi

fails=$(awk -F'FAILURES=' '/^record\|/ {split($2,a," "); t+=a[1]} END {printf "%d", t+0}' "$LOG")
rows=$(grep -c '^record|' "$LOG")
echo
echo "pinmove-gate: $rows record(s), $fails wrong answer(s)"
if [[ "$fails" -ne 0 ]]; then
    echo "pinmove-gate: GATE FAIL -- a gate returned the wrong bit. Stop here: no" >&2
    echo "  noise or timing number from this build means anything until that is fixed." >&2
    exit 1
fi

if [[ -z "$BASE" ]]; then
    echo "pinmove-gate: no baseline given, so nothing is compared. Keep $LOG as the"
    echo "  baseline for the next pin move, and re-run with it as the argument."
    exit 0
fi
[[ -r "$BASE" ]] || { echo "pinmove-gate: cannot read baseline '$BASE'" >&2; exit 2; }

# The predicted ratios come from the model's key-layout flags, which follow the
# pin. With no layout change between the two pins every ratio is 1.0 and this is
# a no-op; with one, it is the difference between a fair test and a wrong one.
EXP=$(mktemp /tmp/pinmove-expect.XXXXXX.txt)
# gate_expect imports the noise model, which wants SciPy: inside the container
# that is Sage's python, not the bare python3 on PATH.
PY=python3
command -v sage >/dev/null 2>&1 && PY="sage -python"
if $PY scripts/dse-tools/gate_expect.py "$LOG" --out "$EXP" >/dev/null 2>&1; then
    echo "pinmove-gate: comparing against the model's predicted ratios ($EXP)"
    python3 scripts/dse-tools/gate_compare.py "$BASE" "$LOG" --expect-file "$EXP"
else
    echo "pinmove-gate: could not predict the ratios (no SciPy outside Sage?); comparing" >&2
    echo "  against no change, which is only right if this pin leaves noise alone." >&2
    python3 scripts/dse-tools/gate_compare.py "$BASE" "$LOG"
fi
rc=$?
echo "pinmove-gate: log kept at $LOG"
exit $rc
