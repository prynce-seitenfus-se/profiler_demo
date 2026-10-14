#!/usr/bin/env bash
# Generate the WCET CSV with OTAWA and compare it with the profiler captures.
# Usage: run_wcet.sh <config.json> [extra wcet_csv.py options, e.g. --model generic]
# Environment: ELF overrides the JSON 'elf' entry.
# The JSON 'otawa_env' entry is optional; it defaults to ../otawa-env.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if (( $# < 1 )); then
    echo "usage: $0 <config.json> [--model m0plus|trivial|generic]" >&2
    exit 2
fi
CONFIG="$(realpath "$1")"
shift

json_value() {
    python3 -c 'import json,os,sys; p=sys.argv[1]; print(json.load(open(p)).get(sys.argv[2], "") if os.path.isfile(p) else "")' "$CONFIG" "$1"
}

OTAWA_ENV_SCRIPT="$(json_value otawa_env)"
if [[ -z "$OTAWA_ENV_SCRIPT" ]]; then
    OTAWA_ENV_SCRIPT="$SCRIPT_DIR/../otawa-env.sh"
fi
if [[ ! -f "$OTAWA_ENV_SCRIPT" ]]; then
    echo "error: OTAWA environment script not found: $OTAWA_ENV_SCRIPT" >&2
    exit 2
fi
# shellcheck source=/dev/null
source "$OTAWA_ENV_SCRIPT"

if [[ -z "${ELF:-}" ]]; then
    cfg_elf="$(json_value elf)"
    if [[ -n "$cfg_elf" ]]; then
        ELF="$(cd "$(dirname "$CONFIG")" && realpath -m "$cfg_elf")"
    fi
fi
export ELF

wcet_status=0
if [[ -z "${COMPARE_ONLY:-}" ]]; then
    python3 "$SCRIPT_DIR/wcet_csv.py" --config "$CONFIG" "$@" || wcet_status=$?
fi

if [[ -n "${WCET_ONLY:-}" ]]; then
    exit "$wcet_status"
fi

compare_args=(--config "$CONFIG")
if [[ -n "${COMPARE_CSV:-}" ]]; then
    compare_args+=("$COMPARE_CSV")
fi
if [[ -n "${WCET_OUT_CSV:-}" ]]; then
    compare_args+=("$WCET_OUT_CSV")
fi
if [[ -n "${COMPARE_OUT_CSV:-}" ]]; then
    compare_args+=(-o "$COMPARE_OUT_CSV")
fi

compare_status=0
python3 "$SCRIPT_DIR/compare_csv.py" "${compare_args[@]}" || compare_status=$?

if (( wcet_status != 0 )); then
    exit "$wcet_status"
fi
exit "$compare_status"
