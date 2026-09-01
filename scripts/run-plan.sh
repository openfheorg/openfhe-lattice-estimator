#!/usr/bin/env bash
#
# Run a measurement plan -- a list of boolean_noise_estimate_script command lines,
# as `dse.py plan` or `dse.py table plan` emits -- and reduce each run to one
# parseable record: sigma, the mean phase error, the sample count, and the
# binary's OWN failure count.
#
# Usage, from the repo root inside the container:
#
#   docker compose run --rm estimator bash scripts/run-plan.sh <plan.cmds>
#
# where <plan.cmds> comes from `dse.py plan`. Records go to stdout; feed them to
# `dse.py decide` or `dse.py validate`.
#
# A "# label: NAME" line starts a new candidate block, so per-key rows can be
# grouped back together by set rather than inferred from order. A "# key_mib: N"
# line under it says how much key material one process of that block holds.
#
# FAN-OUT. JOBS=n runs up to n command lines at once, each as ONE OpenMP thread:
# a plan line is one key and keys are independent, so they parallelise fully
# where OpenMP inside a gate does not. Measured on the 8-core i7-9700 at
# 12858277, STD128: one 8-thread process takes 23 s per 1250-gate key, one
# single-threaded process 44 s, and eight of those side by side finish 8 keys in
# 46 s against 185 s serially -- 4.0x (2.3x on 100-gate keys, where key
# generation is most of the run). Records come out in completion order, which is
# why consumers group by label and never by position. JOBS=1 (the default) is
# the serial path, unchanged.
#
# MEMORY BINDS BEFORE CPU. Every process holds its own keys, hundreds of MB to
# tens of GB, so the fan-out is sized by a budget as well as a count: MEM_GIB
# (default three quarters of MemAvailable, or of the cgroup limit if smaller)
# against each block's "# key_mib" hint plus headroom; a line without a hint is
# booked at an equal share of the budget. A block too large for the budget on
# its own runs alone rather than not at all. Without a limit on the container
# an over-eager fan-out is a host-wide OOM, which is what this prevents.
#
# WHY IT CAPTURES FAILURES. The noise values arrive on stderr (WITH_NOISE_DEBUG)
# and everything else on stdout, including the "Failures:" line, so both streams
# have to be kept: send stdout to /dev/null and the failure count goes with it.
# A gate can return a clean,
# LOW-NOISE encryption of the WRONG bit: that is exactly how GINX with a Gaussian
# secret hid 202 wrong gates in 400 behind a sigma only 25% above normal. No
# sigma-based metric can see it. Never measure noise here without also reading
# failures.
# WHY IT REFUSES TO SAY "DONE" FOR NOTHING. `done < "$PLAN"` on a missing plan
# leaves the loop body unexecuted, so a marker printed at the end of the script
# would claim a run that never happened, and a chain gating on it reads "0 of 0
# rows, FAILURES 0" as a pass. A run whose every command dies on a bad flag has
# the same shape: records exist and every sigma is NA. Both are silent success,
# which is the one outcome a measurement runner must never produce, so the plan is
# checked before the first run and the marker is earned rather than printed.
set -u

if [[ $# -lt 1 ]]; then
    echo "usage: run-plan.sh <plan.cmds>   (from the repo root, inside the container)" >&2
    exit 2
fi
PLAN="$1"
if [[ ! -r "$PLAN" ]]; then
    echo "run-plan.sh: cannot read plan '$PLAN'" >&2
    exit 2
fi
WANT=$(grep -c '^[^#[:space:]]' "$PLAN" || true)
if [[ "$WANT" -eq 0 ]]; then
    echo "run-plan.sh: '$PLAN' holds no command lines" >&2
    exit 2
fi
# Only demand the harness the plan actually names: a plan may point somewhere
# else (a stub, another build directory), and a check that fires on the path
# rather than on the plan would refuse a run it has no business refusing.
if grep -q '^build/bin/boolean_noise_estimate_script' "$PLAN" \
        && [[ ! -x build/bin/boolean_noise_estimate_script ]]; then
    echo "run-plan.sh: the plan calls build/bin/boolean_noise_estimate_script, which is" >&2
    echo "  missing or not executable. Run from the repo root inside the container," >&2
    echo "  where the entrypoint builds it." >&2
    exit 2
fi
JOBS=${JOBS:-1}
MEM_GIB=${MEM_GIB:-}
if ! [[ "$JOBS" =~ ^[0-9]+$ ]] || [[ "$JOBS" -lt 1 ]]; then
    echo "run-plan.sh: JOBS must be a positive integer (got '$JOBS')" >&2
    exit 2
fi
if [[ "$JOBS" -gt 1 ]]; then
    # `wait -n -p` names the child that finished, which the memory accounting
    # needs; bash 5.1 has it, and the container's bash does.
    if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1) )); then
        echo "run-plan.sh: JOBS=$JOBS needs bash 5.1 or newer (this is $BASH_VERSION); run inside the container" >&2
        exit 2
    fi
    if [[ -z "$MEM_GIB" ]]; then
        avail_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
        MEM_GIB=$(( avail_kib * 3 / 4 / 1048576 ))
        cg=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || echo max)
        if [[ "$cg" =~ ^[0-9]+$ ]]; then
            cg_gib=$(( cg * 3 / 4 / 1073741824 ))
            (( cg_gib < MEM_GIB )) && MEM_GIB=$cg_gib
        fi
        (( MEM_GIB < 1 )) && MEM_GIB=1
    fi
    if ! [[ "$MEM_GIB" =~ ^[0-9]+$ ]]; then
        echo "run-plan.sh: MEM_GIB must be a whole number of GiB (got '$MEM_GIB')" >&2
        exit 2
    fi
fi
BUDGET_MIB=$(( MEM_GIB * 1024 ))
if [[ "$JOBS" -gt 1 ]]; then
    echo "PLAN START $WANT command(s) from $PLAN  jobs=$JOBS mem_gib=$MEM_GIB (one OpenMP thread per process)"
else
    echo "PLAN START $WANT command(s) from $PLAN"
fi

TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

# One command line, start to record. Backgrounded under JOBS>1, so everything it
# touches is its own; the record goes to stdout as it finishes and to a file the
# summary counts from.
run_one() {   # idx label cmd [omp_threads]
    local idx=$1 LABEL=$2 cmd=$3 t0 out err sd mn cnt fails keydist
    [[ -n "${4:-}" ]] && export OMP_NUM_THREADS=$4
    t0=$(date +%s)
    out=$(mktemp); err=$(mktemp)
    eval "$cmd" > "$out" 2> "$err" < /dev/null
    read -r sd mn cnt < <(awk 'NF==1 && $1+0==$1 { n++; s+=$1; ss+=$1*$1 }
        END { if (n>1) printf "%.6f %.6f %d", sqrt((ss-s*s/n)/(n-1)), s/n, n;
              else printf "NA NA %d", n+0 }' "$err")
    # The count is printed once per key, so sum them rather than taking the last.
    fails=$(awk '/Failures:/ { for (i=1;i<=NF;i++) if ($i=="Failures:") t+=$(i+1) }
                 END { printf "%d", t+0 }' "$out")
    field() { awk -v f="-$1" '{for (i=1;i<NF;i++) if ($i==f) {print $(i+1); exit}}' <<< "$2" | grep . || echo -; }
    # The harness spells the distribution as a number (-d 0 GAUSSIAN, -d 1
    # UNIFORM_TERNARY). Record the NAME: these lines get read by people, and a
    # record saying keydist=0 puts the harness's encoding in every consumer.
    case "$(field d "$cmd")" in
        0) keydist=GAUSSIAN ;;
        1) keydist=UNIFORM_TERNARY ;;
        *) keydist=- ;;
    esac
    # set= must be emitted: named-set runs (-p) carry no geometry flags, and it is
    # the only field that identifies them. Omitting it made every -p record
    # anonymous to dse_shipfit, which keys on it.
    # baseg= and baserk= are NOT optional extras. A single-base run passes -g and
    # no -G, so gmap= is "-" and the record could not say which gadget base it
    # used; and AP's refresh base (-r) drives its noise, its gate time and its key
    # size, so an AP record without it identifies no configuration at all.
    # keydist= and autokeys= are here for the same reason, and were missing for
    # longer. Both are search DIMENSIONS, so neither has a safe default any more:
    # a consumer that assumed UNIFORM_TERNARY scored the four Gaussian LMKCDEY
    # picks of the parameter table against the wrong Var(s)/12 rounding term and
    # reported -25% to -41% "model error" on configurations the model actually
    # predicts to within 2%. The rule this keeps re-learning: a record must name
    # every dimension the search is free to move, or its consumers guess.
    printf 'record| label=%s set=%s inputs=%s N=%s n=%s q=%s logQ=%s qks=%s baseks=%s gmap=%s baseg=%s baserk=%s method=%s autokeys=%s keydist=%s keys=%s sigma=%s mean=%s samples=%s FAILURES=%s secs=%s\n' \
        "$LABEL" "$(field p "$cmd")" \
        "$(field I "$cmd")" \
        "$(field N "$cmd")" "$(field n "$cmd")" "$(field q "$cmd")" "$(field Q "$cmd")" \
        "$(field k "$cmd")" "$(field b "$cmd")" "$(field G "$cmd")" \
        "$(field g "$cmd")" "$(field r "$cmd")" "$(field t "$cmd")" \
        "$(field a "$cmd")" "$keydist" \
        "$(field K "$cmd")" "$sd" "$mn" "$cnt" "$fails" "$(( $(date +%s) - t0 ))" \
        | tee "$TMPD/rec.$idx"
    rm -f "$out" "$err"
}

declare -A PID_MIB=()
INFLIGHT_MIB=0
RUNNING=0
IDX=0
LABEL=candidate
LABEL_MIB=""
reap() {   # the child `wait -n -p` named has finished: give its memory back
    INFLIGHT_MIB=$(( INFLIGHT_MIB - ${PID_MIB[$1]:-0} ))
    unset "PID_MIB[$1]"
    RUNNING=$(( RUNNING - 1 ))
}
while IFS= read -r cmd; do
    if [[ "$cmd" == "# label:"* ]]; then LABEL="${cmd#\# label: }"; LABEL_MIB=""; continue; fi
    if [[ "$cmd" == "# key_mib:"* ]]; then LABEL_MIB="${cmd#\# key_mib: }"; LABEL_MIB="${LABEL_MIB%%.*}"; continue; fi
    [[ -z "$cmd" || "$cmd" == \#* ]] && continue
    IDX=$((IDX + 1))
    if [[ "$JOBS" -eq 1 ]]; then
        run_one "$IDX" "$LABEL" "$cmd"
        continue
    fi
    # What this process will hold: the block's hint plus a quarter and half a GB
    # of headroom for the context and ciphertexts, or an equal share when the
    # plan says nothing.
    if [[ "$LABEL_MIB" =~ ^[0-9]+$ ]]; then
        need=$(( LABEL_MIB * 5 / 4 + 512 ))
    else
        need=$(( BUDGET_MIB / JOBS ))
    fi
    while (( RUNNING >= JOBS )) || (( RUNNING > 0 && INFLIGHT_MIB + need > BUDGET_MIB )); do
        (( RUNNING < JOBS )) && echo "run-plan: memory budget: waiting for a slot (in flight ${INFLIGHT_MIB} MiB + ${need} MiB > ${BUDGET_MIB} MiB)" >&2
        wait -n -p done_pid
        reap "$done_pid"
    done
    (( need > BUDGET_MIB )) && echo "run-plan: $LABEL needs about ${need} MiB, more than the ${BUDGET_MIB} MiB budget; running it alone" >&2
    run_one "$IDX" "$LABEL" "$cmd" 1 &
    PID_MIB[$!]=$need
    INFLIGHT_MIB=$(( INFLIGHT_MIB + need ))
    RUNNING=$(( RUNNING + 1 ))
done < "$PLAN"
while (( RUNNING > 0 )); do
    wait -n -p done_pid
    reap "$done_pid"
done
GOT=$(ls "$TMPD" | grep -c '^rec\.')
NA=$(cat "$TMPD"/rec.* 2>/dev/null | grep -c ' sigma=NA ' || true)

# The marker is a claim that the plan ran. Earn it: every command has to have
# produced a record, and a record whose sigma is NA measured nothing -- the run
# died, or emitted no noise values, which is what a missing WITH_NOISE_DEBUG or a
# rejected flag looks like from here.
if [[ "$GOT" -ne "$WANT" ]]; then
    echo "PLAN FAILED: $GOT record(s) for $WANT command(s)" >&2
    exit 1
fi
if [[ "$NA" -eq "$GOT" ]]; then
    echo "PLAN FAILED: all $GOT record(s) have sigma=NA -- no run produced noise values." >&2
    echo "  Check that the OpenFHE in use was built WITH_NOISE_DEBUG=ON and that the" >&2
    echo "  command lines are accepted by this harness (-h lists its flags)." >&2
    exit 1
fi
if [[ "$NA" -gt 0 ]]; then
    echo "PLAN WARNING: $NA of $GOT record(s) measured no noise (sigma missing)" >&2
fi
# The count, not the literal token: writing "sigma=NA" into the summary plants a
# string in the log that the obvious `grep -c sigma=NA plan.log` then counts,
# over-reporting by one and reading as "some run measured nothing" on a clean run.
echo "PLAN DONE $(date -u) $GOT record(s), $NA measured no noise"
