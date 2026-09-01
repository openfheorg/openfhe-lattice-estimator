#!/usr/bin/env bash
#
# ---------------------------------------------------------------------------
# Recalibrate the gate-cost cells for THIS machine, in one command. Run from the
# repo root inside the container, on an otherwise idle box:
#
#   est estimator bash scripts/dse-arms/recalibrate.sh [OUTDIR]
#
# OUTDIR defaults to `recal-<date>`. That measures GINX, the method `dse.py
# search` selects by default.
#
# ONE METHOD IS A COMPLETE ANSWER FOR THAT METHOD. A cost cell is keyed by
# (method, N, word size) and nothing extrapolates across methods, so measuring
# GINX gives you correct GINX gate times whatever the rest of the table holds.
# What it does not give you is a method-against-method comparison: the cells you
# did not measure still describe the box named in their table's provenance
# comment. Measure what you intend to search:
#
#   METHODS=LMKCDEY      ... recalibrate.sh recal-lmk
#   METHODS=GINX,LMKCDEY ... recalibrate.sh recal-two
#   METHODS=all          ... recalibrate.sh recal-all    # AP too, and AP is slow
#
# Names or the arm's numbers (1 AP, 2 GINX, 3 LMKCDEY), comma or space
# separated. `DRY=1` prints the stages, methods and CPU binding it would use and
# measures nothing. `STAGES` picks stages by hand, `NS` and `WORDS` cut the grid,
# `MT_THREADS` sets the many-thread count.
#
# BUDGET HOURS for all three methods, and expect AP to dominate: the measured
# rate for GINX and LMKCDEY is about 2.4 seconds a row at 8 threads (530 rows in
# 21 minutes), while an AP gate runs hundreds of milliseconds and its key
# generation tens of seconds on the large rows. Of the 1423 timings behind the
# shipped multi/libomp cells, 330 are GINX, 530 LMKCDEY and 563 AP. This prints
# the rows, elapsed time and rate of every stage as it finishes, so the number
# for YOUR hardware ends up in the log rather than in someone's estimate.
# Re-running is safe and resumes: a stage whose log already ends in
# `GATECOST DONE` is skipped, so an interrupted night costs only the stage it
# died in. A finished log that does not hold the methods now being asked for is
# REFUSED rather than skipped, so give each method set its own OUTDIR.
#
# It measures and fits. It does NOT edit dse_model.py: it prints the exact
# apply_cells.py commands and writes a provenance stub for you to fill in and
# pass with --comment. A cell block nobody reviewed is a cell block nobody can
# retire, and the provenance is the part only the person at the machine knows.
# ---------------------------------------------------------------------------
# THE MACHINE MUST BE IDLE. Every number here is wall clock. A contaminated box
# spread repeated timings of one setting by 15 to 359 percent against under 0.7
# percent clean, and no amount of fitting recovers that.
#
# AND IT MUST BE PINNED, on anything bigger than a single-socket desktop. Eight
# OpenMP threads left to the scheduler on a many-core machine land somewhere
# different every run -- one socket, split across two, scattered over four
# sub-NUMA domains -- and the bootstrapping key is faulted in by whoever touched
# it first, so a placement that separates threads from that memory pays remote
# access on every gate. That is not scatter around one value, it is several
# values, and it shows up as fit residuals of 3 to 11 percent where a pinned box
# gives 2 to 3, plus `c3` terms that stop resolving because they come from
# matched pairs. This pins to one physical core per thread inside ONE NUMA node
# (scripts/dse-tools/pick_cpus.py) and records the binding. `PIN_CPUS=0-7` sets
# it by hand; `PIN=0` turns it off, which is only right on a single-node box
# with nothing else on it.
#
# WHAT THE STAGES ARE, and why they follow from the methods. `gatecost-regime.sh`
# sweeps every method it is handed in ONE pass over the grid, so the single-base
# stage is a single invocation however many methods you ask for; invoking it once
# per method re-measures the shared grid each time. What a plain sweep does not
# cover is the two-base gadget maps, and those come along with the methods that
# can use one: `maps` is LMKCDEY's own two-base rows, and `maps-ginx` is the GINX
# control on those same maps, which is the evidence that the team-width term the
# model carries is LMKCDEY's alone -- and so that a GINX multi-base gate is
# priced with no width penalty on this box. The single-thread regime is a
# separate pin by design: no OpenMP region executes at one thread.
#
# The OTHER OpenMP runtime needs its own image (OpenFHE and the harness are both
# built with the compiler that selects it), so it is out of scope here. Build the
# gcc image and run this again inside it; docs/cost-model.md#regimes has the
# recipe.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT" || exit 2

OUT=${1:-recal-$(date +%Y%m%d)}
MT_THREADS=${MT_THREADS:-8}
DRY=${DRY:-0}

# WHICH METHODS. Names in, the arm's numbers out. An unknown name is refused
# rather than silently dropped: a typo would otherwise measure fewer methods
# than asked for and the log would look complete.
want_ap=0 want_ginx=0 want_lmk=0
for tok in ${METHODS:-GINX}; do
    for t in ${tok//,/ }; do
        case "$(printf '%s' "$t" | tr '[:lower:]' '[:upper:]')" in
            ALL)         want_ap=1; want_ginx=1; want_lmk=1 ;;
            AP|1)        want_ap=1 ;;
            GINX|CGGI|2) want_ginx=1 ;;
            LMKCDEY|3)   want_lmk=1 ;;
            '')          ;;
            *) echo "recalibrate: unknown method '$t' -- want GINX, LMKCDEY, AP," \
                    "a comma-separated list of those, or all" >&2; exit 2 ;;
        esac
    done
done
METH_NUMS=""; METH_NAMES=""
[[ "$want_ap"   == 1 ]] && { METH_NUMS="$METH_NUMS 1"; METH_NAMES="$METH_NAMES AP"; }
[[ "$want_ginx" == 1 ]] && { METH_NUMS="$METH_NUMS 2"; METH_NAMES="$METH_NAMES GINX"; }
[[ "$want_lmk"  == 1 ]] && { METH_NUMS="$METH_NUMS 3"; METH_NAMES="$METH_NAMES LMKCDEY"; }
METH_NUMS=${METH_NUMS# }; METH_NAMES=${METH_NAMES# }
# The same list as prose, for the provenance comment and the closing notes.
METH_PROSE=$(printf '%s' "$METH_NAMES" | sed 's/ /, /g; s/\(.*\), /\1 and /')
[[ -n "$METH_NUMS" ]] || { echo "recalibrate: METHODS names no method" >&2; exit 2; }
ALL_THREE=$(( want_ap + want_ginx + want_lmk == 3 ))

# The two-base stages are not optional extras, they are the map rows of the
# methods asked for: LMKCDEY's own, and the GINX control that says the width
# term is LMKCDEY's alone.
default_stages() {
    local s="multi"
    [[ "$want_lmk"  == 1 ]] && s="$s maps"
    [[ "$want_lmk" == 1 || "$want_ginx" == 1 ]] && s="$s maps-ginx"
    echo "$s single"
}
STAGES=${STAGES:-$(default_stages)}
mkdir -p "$OUT" || exit 2

# Where the threads will run. A binding that could not be chosen is a loud
# warning rather than a silent unpinned run: the numbers are still numbers, but
# they are not comparable to a pinned set and the provenance has to say so.
PIN=${PIN:-1}
PIN_CPUS=${PIN_CPUS:-}
TASKSET=()
PIN_NOTE="unpinned"
if [[ "$PIN" != "0" ]]; then
    if [[ -z "$PIN_CPUS" ]]; then
        PIN_CPUS=$(python3 scripts/dse-tools/pick_cpus.py "$MT_THREADS" 2>"$OUT/pin.err") || PIN_CPUS=""
    fi
    if [[ -n "$PIN_CPUS" ]] && command -v taskset >/dev/null; then
        TASKSET=(taskset -c "$PIN_CPUS")
        PIN_NOTE="cpus $PIN_CPUS, one per physical core, single NUMA node"
        # Keep each thread on its own core inside that set rather than letting the
        # runtime drift within it.
        export OMP_PROC_BIND=${OMP_PROC_BIND:-close} OMP_PLACES=${OMP_PLACES:-cores}
    else
        echo "recalibrate: WARNING -- running UNPINNED." >&2
        [[ -s "$OUT/pin.err" ]] && sed -n '1,3p' "$OUT/pin.err" >&2
        command -v taskset >/dev/null || echo "  taskset is not on PATH" >&2
        echo "  On a many-core or multi-socket box these timings will not be" >&2
        echo "  reproducible: set PIN_CPUS to a core list on one NUMA node." >&2
    fi
fi

# stage name -> the environment that selects it, METHODS excepted (a value with
# a space in it cannot ride through the unquoted expansion below).
stage_env() {
    case "$1" in
        multi)          echo "THREADS=$MT_THREADS" ;;
        maps|maps-ginx) echo "THREADS=$MT_THREADS MAPS_ONLY=1" ;;
        single)         echo "THREADS=1" ;;
        *)              echo "" ;;
    esac
}

# The map stages measure one method each, whatever was asked for: `maps` is the
# LMKCDEY rows the width term is fitted from, `maps-ginx` the control.
stage_methods() {
    case "$1" in
        maps)      echo "3" ;;
        maps-ginx) echo "2" ;;
        *)         echo "$METH_NUMS" ;;
    esac
}

echo "recalibrate: $OUT, threads $MT_THREADS, stages: $STAGES"
echo "recalibrate: methods: $METH_NAMES"
echo "recalibrate: binding: $PIN_NOTE"
if [[ "$ALL_THREE" != 1 ]]; then
    echo "recalibrate: the methods NOT in that list keep the cells they have, which"
    echo "recalibrate: were measured elsewhere -- so these gate times are comparable"
    echo "recalibrate: within a method, not across them."
fi
echo "recalibrate: the box must be idle for the whole run; nothing here re-checks that."
echo

if [[ "$DRY" != "0" ]]; then
    echo "recalibrate: DRY run -- the stages it would measure, in order:"
    for st in $STAGES; do
        env_str=$(stage_env "$st")
        [[ -n "$env_str" ]] || { echo "recalibrate: unknown stage '$st'" >&2; exit 2; }
        printf '  %-10s %s METHODS="%s"\n' "$st" "$env_str" "$(stage_methods "$st")"
    done
    echo "recalibrate: nothing measured (DRY=1)."
    echo "RECALIBRATE DRY DONE"
    exit 0
fi

[[ -x build/bin/boolean_estimate_time ]] || {
    echo "recalibrate: build/bin/boolean_estimate_time is missing. Run this from the" >&2
    echo "  repo root inside the container, where the entrypoint builds it." >&2
    exit 2; }

failed=0
for st in $STAGES; do
    env_str=$(stage_env "$st")
    [[ -n "$env_str" ]] || { echo "recalibrate: unknown stage '$st'" >&2; exit 2; }
    smeth=$(stage_methods "$st")
    log="$OUT/$st.log"
    if [[ -s "$log" ]] && tail -n 1 "$log" | grep -q 'GATECOST DONE'; then
        # Resume only where the finished log actually holds rows for the methods
        # now being asked for. One OUTDIR reused for a second method set would
        # otherwise skip the stage on the strength of the marker and then fit a
        # log that never measured the method you came back for -- complete,
        # plausible, and about another method entirely. The same check catches a
        # stage that printed its marker having measured nothing at all.
        have=$(sed -n 's/^gatecost| meth=\([0-9]*\).*/\1/p' "$log" | sort -u | tr '\n' ' ')
        gap=""
        for mnum in $smeth; do
            case " $have " in *" $mnum "*) ;; *) gap="$gap $mnum" ;; esac
        done
        if [[ -n "$gap" ]]; then
            echo "recalibrate: $log ends in GATECOST DONE but holds no row for" >&2
            echo "  method(s)$gap of the requested [$smeth]; it holds: ${have:-none}." >&2
            echo "  Resuming would skip the stage and then fit a log that never measured" >&2
            echo "  them. Give this method set its own OUTDIR, or delete that log." >&2
            exit 2
        fi
        echo "[$(date +%H:%M:%S)] $st: already complete ($(grep -c '^gatecost|' "$log") rows), skipping"
        continue
    fi
    echo "[$(date +%H:%M:%S)] $st: $env_str METHODS=\"$smeth\"  (no progress until it finishes; watch $log)"
    t0=$(date +%s)
    # shellcheck disable=SC2086
    "${TASKSET[@]}" env $env_str METHODS="$smeth" bash scripts/dse-arms/gatecost-regime.sh \
        > "$log" 2>"$OUT/$st.err"
    rc=$?
    secs=$(( $(date +%s) - t0 ))
    rows=$(grep -c '^gatecost|' "$log" || true)
    skips=$(grep -c '^gatecost-skip|' "$log" || true)
    done_marker=$(tail -n 1 "$log" | grep -c 'GATECOST DONE' || true)
    rate=$( [[ "$rows" -gt 0 ]] && echo "scale=1; $secs / $rows" | bc 2>/dev/null || echo "-" )
    printf '[%s] %s: exit %d, %s rows, %s skipped, %dm%02ds, %s s/row\n' \
        "$(date +%H:%M:%S)" "$st" "$rc" "$rows" "$skips" $((secs / 60)) $((secs % 60)) "$rate"
    printf 'recal| stage=%s methods=%s rows=%s skips=%s secs=%d threads=%s binding=%s\n' \
        "$st" "$(printf '%s' "$smeth" | tr -d ' ')" "$rows" "$skips" "$secs" \
        "$( [[ $st == single ]] && echo 1 || echo "$MT_THREADS" )" \
        "${PIN_CPUS:-none}" >> "$OUT/timing.log"
    # A stage that exits 0 having measured nothing is the failure this refuses to
    # report as success: an arm can complete its loops while every run inside them
    # died on a rejected flag or a missing binary.
    if [[ $rc -ne 0 || "$rows" -eq 0 || "$done_marker" -ne 1 ]]; then
        echo "recalibrate: STAGE '$st' FAILED -- see $OUT/$st.err" >&2
        sed -n '1,5p' "$OUT/$st.err" >&2
        failed=$((failed + 1))
    fi
done

if [[ "$failed" -ne 0 ]]; then
    echo "recalibrate: $failed stage(s) failed; not fitting a partial set." >&2
    exit 1
fi

echo
echo "recalibrate: fitting"
FIT_FAIL=0
for st in multi single; do
    log="$OUT/$st.log"
    [[ -s "$log" ]] || continue
    fit="$OUT/$st.fit.txt"
    if sage -python scripts/paramsestimator/dse.py costfit "$log" \
            --weight relative --emit > "$fit" 2>"$OUT/$st.fit.err"; then
        echo "  $st -> $fit ($(grep -c '): (' "$fit" || true) cell(s))"
    else
        echo "  $st: costfit FAILED, see $OUT/$st.fit.err" >&2
        FIT_FAIL=1
    fi
done
[[ "$FIT_FAIL" -eq 0 ]] || { echo "recalibrate: a fit failed; nothing to paste." >&2; exit 1; }

# THE PROVENANCE, WRITTEN FROM THE MACHINE. Every cell table records the pin,
# thread count, runtime, box, binding, methods and residuals its rows were
# measured under, and that record is what lets a future reader retire them. None
# of it is a judgement call, so none of it is left as a placeholder to fill in:
# lscpu knows the box better than the person at it does, ldd knows which OpenMP
# runtime the harness actually linked, and the residuals are in the fit that was
# just written. A stub with blanks in it is a stub that gets pasted with the
# blanks still in it.
REF_FILE=${OPENFHE_REF_FILE:-/opt/openfhe/share/openfhe-src/OPENFHE_REF}
REF=$( [[ -r "$REF_FILE" ]] && head -1 "$REF_FILE" || echo "unknown (no $REF_FILE)" )

lscpu_field() { lscpu 2>/dev/null | sed -n "s/^$1: *//p" | head -1; }

box_model() {
    local m; m=$(lscpu_field 'Model name')
    echo "${m:-unknown (lscpu reported no model)}"
}

box_topology() {
    local sock core thr node
    sock=$(lscpu_field 'Socket(s)'); core=$(lscpu_field 'Core(s) per socket')
    thr=$(lscpu_field 'Thread(s) per core'); node=$(lscpu_field 'NUMA node(s)')
    [[ -n "$sock$core" ]] || { echo "unknown (no lscpu)"; return; }
    printf '%s socket(s) x %s core(s) x %s thread(s), %s NUMA node(s), %s online' \
        "${sock:-?}" "${core:-?}" "${thr:-?}" "${node:-?}" "$(nproc 2>/dev/null || echo '?')"
}

runtime_line() {  # which OpenMP runtime the harness LINKED, not the one we meant
    local lib ver
    lib=$(ldd build/bin/boolean_estimate_time 2>/dev/null \
          | sed -n 's/.*\(libgomp\|libomp\)[.-].*/\1/p' | head -1)
    ver=$("${ESTIMATOR_CXX:-c++}" --version 2>/dev/null | head -1)
    printf '%s (%s)' "${ver:-unknown compiler}" \
        "${lib:-unknown OpenMP runtime}"
}

resid_line() {  # the med|res| and worst columns of one fit, as a range per method
    local fit=$1
    [[ -s "$fit" ]] || { echo "stage not run"; return; }
    awk '$1 ~ /^(GINX|LMKCDEY|AP)$/ && $NF ~ /%$/ {
             m=$1; r=$(NF-1)+0; w=$NF+0
             if (!(m in n) || r < lo[m]) lo[m] = r
             if (!(m in n) || r > hi[m]) hi[m] = r
             if (!(m in n) || w > wo[m]) wo[m] = w
             n[m]++
         }
         END {
             if (!length(n)) { print "not parsed -- read the fit by hand"; exit }
             sep = ""
             for (m in n) {
                 printf "%s%s median %.1f-%.1f%%, worst %.1f%% over %d cell(s)",
                        sep, m, lo[m], hi[m], wo[m], n[m]
                 sep = "; "
             }
             print ""
         }' "$fit"
}

# The pin as a COMMAND-LINE argument, which is not the same as the pin as prose:
# an unreadable OPENFHE_REF must not be pasted into a shell as bare words.
if [[ "$REF" =~ ^[0-9a-f]{7,40}$ ]]; then
    PIN_ARG="--pin $REF"
else
    PIN_ARG="--pin <the commit these were measured at>"
fi
BOX_MODEL=$(box_model)
BOX_TOPO=$(box_topology)
BOX="$BOX_MODEL, $BOX_TOPO"
{
cat <<EOF
    # Measured $(date +%Y-%m-%d), sole job on the box, relative fit.
    # Box:       ${BOX_MODEL}
    #            ${BOX_TOPO}
    # OpenFHE:   ${REF}
    # Runtime:   $(runtime_line)
    # Threads:   ${MT_THREADS} (multi regime), 1 (single regime)
    # Binding:   ${PIN_NOTE}
    # Methods:   ${METH_PROSE}
    # Rows:      $(grep -hc '^gatecost|' "$OUT"/*.log 2>/dev/null | paste -sd+ | bc 2>/dev/null || echo '?') timings over the stages in $(basename "$OUT")/
    # Residuals, multi:  $(resid_line "$OUT/multi.fit.txt")
    # Residuals, single: $(resid_line "$OUT/single.fit.txt")
EOF
[[ "$ALL_THREE" == 1 ]] || cat <<EOF
    # The other methods' rows below are NOT from this run or this box; see the
    # comment history for where they came from.
EOF
} > "$OUT/provenance.txt"

# A probe that could not answer says so in the file, and saying so on the
# terminal too is the difference between a reader fixing one line and a reader
# never noticing. Nothing here blocks: the cells are still good.
if grep -qi 'unknown' "$OUT/provenance.txt"; then
    echo "recalibrate: NOTE -- something could not be probed automatically:" >&2
    grep -ni 'unknown' "$OUT/provenance.txt" | sed 's/^/    /' >&2
    echo "    Edit $OUT/provenance.txt before pasting; everything else is filled in." >&2
fi

cat <<EOF

recalibrate: done. Nothing in dse_model.py has been touched. To adopt these:

  1. Read $OUT/multi.fit.txt and $OUT/single.fit.txt -- the med|res| column is
     the evidence, and a cell whose residual jumped needs looking at, not pasting.
  2. Read $OUT/provenance.txt. It is written from this machine -- box, runtime,
     pin, binding, methods, rows, residuals -- so there is nothing to fill in
     unless a line above said a probe could not answer.
  3. Paste, one command per regime. Each writes three things: the cells, that
     provenance as the table's comment, and the regime's per-method record (pin,
     date, timing count, box) in COST_REGIMES.

       python3 scripts/dse-tools/apply_cells.py $OUT/multi.fit.txt \\
           --regime multi/libomp  --comment $OUT/provenance.txt --merge \\
           --update-regime $PIN_ARG --box "$BOX"
       python3 scripts/dse-tools/apply_cells.py $OUT/single.fit.txt \\
           --regime single/libomp --comment $OUT/provenance.txt --merge \\
           --update-regime $PIN_ARG --box "$BOX"

     (--regime multi/libgomp and single/libgomp if this was the gcc image.)
     --merge keeps the cells this run did not measure; --update-regime keeps
     their provenance, so points, measured, box and pins_by_method stay true
     per method instead of describing whichever machine came last.
  4. sage -python tests/test_dse.py && dse.py doctor
     The regime lines then show your box, your date and your timing count. One
     thing is still yours: if the pin moved, apply_cells says so, and the methods
     this run did not measure are now CARRIED to it -- which only carried_why can
     justify. A carry is a claim about a measurement nobody made.
EOF
step=5
if [[ -s "$OUT/maps.log" ]]; then
cat <<EOF
  $step. Check the two-base width term still holds:
       python3 scripts/dse-tools/maps_fit.py $OUT/maps.log
       python3 scripts/dse-tools/maps_fit.py $OUT/maps-ginx.log --method GINX --baseline model
     The GINX control should read near zero; if it does not, the width term is
     not LMKCDEY's alone and docs/cost-model.md#the-form needs revisiting.
EOF
step=$((step + 1))
elif [[ -s "$OUT/maps-ginx.log" ]]; then
cat <<EOF
  $step. Check a two-base GINX map pays no width penalty on this box:
       python3 scripts/dse-tools/maps_fit.py $OUT/maps-ginx.log --method GINX --baseline model
     It should read near zero. If it does not, GINX multi-base candidates are
     mispriced here and docs/cost-model.md#the-form needs revisiting.
EOF
step=$((step + 1))
fi
if [[ "$ALL_THREE" != 1 ]]; then
cat <<EOF
  $step. Keep the search inside what you measured. "dse.py search" defaults to
     --method GINX; a run that prices another method, and every "table" run
     (--methods all), reads cells this measurement did not touch. Rankings
     between $METH_PROSE and the rest are not yours until you measure the rest.
EOF
fi
echo
echo "RECALIBRATE DONE"
