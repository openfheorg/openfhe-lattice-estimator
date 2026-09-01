#!/usr/bin/env bash
#
# ---------------------------------------------------------------------------
# Gate time for the same sets at two OpenFHE pins, on THIS machine.
# Runs on the HOST, not inside the container, because it drives two images:
#
#   bash scripts/dse-arms/gatetime-ab.sh <NEW_REF> <OLD_REF>
#
# Both images must already be built (`OPENFHE_REF=<ref> docker compose build`).
# Prints a `gatetime-ab|` header naming the pins, then one `gatetime|` record per
# (pin, set, repetition); scripts/dse-tools/gatetime_ab.py reads the ratio per
# set. THE MACHINE MUST BE IDLE: this measures wall clock.
#
# TWO DESIGN RULES, each learned from a pass that misled.
#
#   The GEOMETRY is timed, not the name. A named set's parameters belong to the
#   library's table, and the table changes between pins: at 12858277 STD192_4
#   moved from b_KS 512 to 64 and read 3 percent slower for it, which is a row
#   change and not a library one. So by default (GEOM=explicit) the sets are
#   resolved ONCE, at the newer pin, into explicit flags (set_flags.py) and both
#   pins are handed the same flags. GEOM=named times `-p NAME` as the library
#   ships it at each pin, which is a different question.
#
#   The pins are INTERLEAVED, alternating which goes first. Pin-major order put
#   whichever pin ran first about 3 percent ahead on the GINX sets and moved an
#   AP set 10 percent between two passes of the same image (12858277, REPS=3 then
#   REPS=8): the box drifts on the scale of a pass, and a design that measures
#   all of one pin and then all of the other cannot tell drift from a pin. Each
#   switch costs a harness rebuild (one checkout mounted into two images), about
#   40 seconds.
# ---------------------------------------------------------------------------
# WHY IT EXISTS. Cost cells are per machine, and "the library says gate times did
# not move" is a measurement taken on someone else's CPU. A commit that only
# shrinks a key can still move a gate here through cache behaviour, and if it
# does then the cells are wrong, the table's picks were chosen on wrong prices,
# and measuring those picks wastes a day. Half an hour here protects that.
#
# THREE THINGS IT DOES THAT THE OBVIOUS VERSION GETS WRONG.
#
#   A named LMKCDEY or AP set needs its own `-t`. `BinFHEContext` refuses a set
#   its method cannot serve, so `-p STD128_LMKCDEY` with the default GINX throws
#   and prints nothing. Six sets became three that way, and the stage still said
#   "done" for all six.
#
#   `boolean_estimate_time` has no `-i`. Passing one makes every run print a
#   usage message; the loop completes, the log fills with errors, and a naive
#   grep reports zero rows -- which reads as "the measurement failed" when the
#   truth is "the measurement never ran". Every run here is checked for a timing.
#
#   The statistic is the MIN of the eight gates, not the mean. The first gate of a
#   run is cold: measured 30252 us against ~16350 for the other seven on STD128,
#   a 185 percent skew. The mean carries that; the floor does not. Three
#   repetitions per set, smallest min kept, because one min is still one draw.
#   (The shipped GATE_COST cells rest on the 8-gate MEAN. Do not refit from these
#   numbers without refitting all of them -- see docs/cost-model.md.)
set -u

if [[ $# -lt 2 ]]; then
    echo "usage: gatetime-ab.sh <NEW_OPENFHE_REF> <OLD_OPENFHE_REF>" >&2
    exit 2
fi
NEW=$1; OLD=$2
REPS=${REPS:-3}
GEOM=${GEOM:-explicit}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT" || exit 2

command -v docker >/dev/null || { echo "gatetime-ab: no docker on PATH" >&2; exit 2; }
export APP_UID=${APP_UID:-$(id -u)} APP_GID=${APP_GID:-$(id -g)}
DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

# set:method -- the method a named set requires, and the six chosen to span what a
# key-layout commit can touch: a 25-percent switching-key saving (STD128), an
# aligned pair that saves nothing as the control (STD128_3), the largest ring
# dimension (STD192_4, where a 4.3-percent regression showed up at 94229558), and
# one set per method.
#
# A very large AP row is deliberately absent. `boolean_estimate_time` serializes
# the keys to report their size and has no way to skip it, so STD256_4_AP asks for
# 21.6 GiB of transient on top of 10.8 GiB resident and is OOM-killed on a 62 GiB
# box -- it dies after printing its option parse and nothing else, which is what
# the no-timing check below catches. STD128_AP covers the refresh-key compaction
# at a size that fits.
SETS=${SETS:-"STD128:2 STD128_3:2 STD192_4:2 STD128_LMKCDEY:3 STD128_AP:1 STD128_3_AP:1"}

for ref in "$NEW" "$OLD"; do
    docker image inspect "openfhe-lattice-estimator:$ref" >/dev/null 2>&1 || {
        echo "gatetime-ab: no image for $ref. Build it first:" >&2
        echo "  OPENFHE_REF=$ref MAKE_JOBS=8 docker compose build" >&2
        exit 2; }
done

# The flags each set is timed with. Explicit geometry is resolved at the NEWER
# pin's table, once, so both pins time identical parameters.
declare -A FLAGS
if [[ "$GEOM" == "explicit" ]]; then
    rm -rf build
    resolved=$(OPENFHE_REF=$NEW "${DC[@]}" run -T --rm --name "gtab_flags_${NEW:0:6}" estimator \
                   sage -python scripts/dse-tools/set_flags.py $SETS 2>&1 | grep '^set=')
    for st in $SETS; do
        s=${st%%:*}
        f=$(sed -n "s/^set=$s meth=[0-9]* flags=//p" <<<"$resolved" | head -1)
        [[ -n "$f" ]] || { echo "gatetime-ab: could not resolve $s from the table at ${NEW:0:8}:" >&2
                           sed 's/^/    /' <<<"$resolved" >&2; exit 2; }
        FLAGS[$s]=$f
    done
else
    for st in $SETS; do s=${st%%:*}; t=${st##*:}; FLAGS[$s]="-p $s -t $t"; done
fi
echo "gatetime-ab| new=$NEW old=$OLD reps=$REPS geometry=$GEOM order=interleaved"
for st in $SETS; do s=${st%%:*}; echo "gatetime-ab| set=$s flags=${FLAGS[$s]}"; done

missing=0
last=""
for rep in $(seq "$REPS"); do
    # Alternate which pin goes first so a first-position advantage cancels.
    if (( rep % 2 )); then order="$NEW $OLD"; else order="$OLD $NEW"; fi
    for ref in $order; do
        # One checkout mounted into two images must be reconfigured between them:
        # the entrypoint only configures when the cmake cache is absent, and both
        # images install OpenFHE to the same path, so a harness linked against one
        # would otherwise be reused unchanged against the other.
        [[ "$ref" == "$last" ]] || rm -rf build
        last=$ref
        for st in $SETS; do
            s=${st%%:*}; t=${st##*:}
            # shellcheck disable=SC2086
            out=$(OPENFHE_REF=$ref "${DC[@]}" run -T --rm \
                    --name "gtab_${ref:0:6}_${s}_${rep}" estimator \
                    build/bin/boolean_estimate_time ${FLAGS[$s]} 2>&1)
            read -r mn me <<< "$(sed -n 's/.*gate us min \([0-9]*\) mean \([0-9]*\).*/\1 \2/p' <<<"$out" | head -1)"
            ks=$(sed -n 's/.*key switching key size: \([0-9]*\).*/\1/p' <<<"$out" | head -1)
            bk=$(sed -n 's/.*bootstrapping key size: \([0-9]*\).*/\1/p' <<<"$out" | head -1)
            if [[ -z "${mn:-}" ]]; then
                echo "gatetime-ab: NO TIMING for set=$s pin=${ref:0:8} rep=$rep" >&2
                sed -n '1,6p' <<<"$out" | sed 's/^/    /' >&2
                missing=$((missing + 1))
                continue
            fi
            printf 'gatetime| pin=%s set=%s meth=%s rep=%s gate_us_min=%s gate_us_mean=%s ksk_b=%s btkey_b=%s\n' \
                "${ref:0:8}" "$s" "$t" "$rep" "$mn" "$me" "${ks:-NA}" "${bk:-NA}"
        done
    done
done

if [[ "$missing" -ne 0 ]]; then
    echo "gatetime-ab: FAILED -- $missing run(s) produced no timing. The ratios below" >&2
    echo "  would be computed from a partial set, which is how a broken check passes." >&2
    exit 1
fi
echo "GATETIME AB DONE"
