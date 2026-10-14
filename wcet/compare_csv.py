#!/usr/bin/env python3
"""Compare two profiler / WCET CSV files as simply as possible."""

import argparse
import csv
import json
import sys
from pathlib import Path

OUTPUT_HEADER = [
    "function",
    "number of calls",
    "total_cycles",
    "work_%",
    "min_cycles",
    "max_cycles",
    "avg_cycles",
    "jitter_cycles",
    "wcet_cycles",
    "margin_cycles",
    "ratio",
    "wcet_check",
]


def read_csv(path: Path) -> dict:
    if not path or not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return {row["function"].strip(): row for row in reader if row.get("function")}


def to_float(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def to_int(val):
    f = to_float(val)
    return int(round(f)) if f is not None else None


def is_unbounded(row: dict, work_dir: Path, fn: str) -> bool:
    if row:
        raw = str(row.get("max_cycles") or row.get("wcet_cycles") or "").strip().upper()
        if raw in ("-", "INF", "UNBOUNDED", "FAILED"):
            return True
    if work_dir and work_dir.is_dir():
        log_file = work_dir / f"{fn}.log"
        if log_file.is_file():
            try:
                text = log_file.read_text(encoding="utf-8", errors="ignore")
                if "WCET[" not in text and ("unbounded" in text.lower() or "ERROR" in text):
                    return True
            except OSError:
                pass
    return False


def main():
    parser = argparse.ArgumentParser(description="Compare two profiler / WCET CSV files.")
    parser.add_argument("file1", nargs="?", type=Path, help="first CSV file (e.g. measured / renode)")
    parser.add_argument("file2", nargs="?", type=Path, help="second CSV file (e.g. WCET)")
    parser.add_argument("-o", "--output", type=Path, nargs="?", const=Path("__DEFAULT__"),
                        help="output comparison CSV file (default: wcet_<csv1>_vs_<csv2>.csv beside csv2)")
    parser.add_argument("--all", action="store_true", help="include unmeasured static functions from file2")
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)

    args = parser.parse_args()

    file1 = args.file1
    file2 = args.file2
    output = args.output

    cfg_functions = {}
    work_dir = None
    if args.config:
        with args.config.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg_functions = {f["name"]: f for f in cfg.get("functions", []) if isinstance(f, dict) and "name" in f}
        base = args.config.resolve().parent
        work_dir = (base / cfg.get("work_dir", "work")).resolve()
        captures = cfg.get("captures", {})

        # Use target if exists, otherwise fallback to renode
        if not file1:
            target_p = (base / captures.get("target", "")).resolve() if captures.get("target") else None
            renode_p = (base / captures.get("renode", "")).resolve() if captures.get("renode") else None
            if target_p and target_p.is_file():
                file1 = target_p
            elif renode_p and renode_p.is_file():
                file1 = renode_p
            else:
                file1 = target_p or renode_p

        if not file2:
            out_csv = cfg.get("output_csv", "1w.csv")
            file2 = (base / out_csv).resolve()

    elif file2 and file2.parent.is_dir():
        work_dir = (file2.parent / "work").resolve()
        # Auto-detect config in the same directory as the static WCET CSV
        for candidate in ("wcet_targets.json", "wcet_measure.json"):
            cfg_p = file2.parent / candidate
            if cfg_p.is_file():
                try:
                    with cfg_p.open("r", encoding="utf-8") as f:
                        cfg_data = json.load(f)
                    if cfg_data.get("output_csv") == file2.name or not cfg_functions:
                        cfg_functions = {f["name"]: f for f in cfg_data.get("functions", []) if isinstance(f, dict) and "name" in f}
                        work_dir = (file2.parent / cfg_data.get("work_dir", "work")).resolve()
                        if cfg_data.get("output_csv") == file2.name:
                            break
                except Exception:
                    pass

    if not file1 or not file1.is_file():
        print(f"error: file not found: {file1}", file=sys.stderr)
        return 1
    if not file2 or not file2.is_file():
        print(f"error: file not found: {file2}", file=sys.stderr)
        return 1

    if not output or output == Path("__DEFAULT__"):
        output = (file2.parent / f"wcet_{file1.stem}_vs_{file2.stem}.csv").resolve()

    rows1 = read_csv(file1)
    rows2 = read_csv(file2)

    if not rows1:
        print(f"error: no function records found in {file1}", file=sys.stderr)
        return 1
    if not rows2:
        print(f"error: no function records found in {file2}", file=sys.stderr)
        return 1

    # Focus on measured targets by default; include extra unmeasured static functions only with --all
    if args.all:
        functions = list(rows1.keys()) + [fn for fn in rows2.keys() if fn not in rows1]
    else:
        functions = list(rows1.keys())

    total_active_cycles = 0
    total_calls = 0
    active_count = 0
    for fn in functions:
        r1 = rows1.get(fn)
        if not r1:
            continue
        calls = to_int(r1.get("calls"))
        if calls is None:
            calls = 1 if to_int(r1.get("max_cycles")) is not None else 0
        tot = to_int(r1.get("total_cycles"))
        if tot is None:
            avg_v = to_float(r1.get("avg_cycles"))
            max_v = to_float(r1.get("max_cycles"))
            base_cyc = avg_v if avg_v is not None else (max_v or 0.0)
            tot = int(round(base_cyc * calls))
        if calls > 0:
            total_active_cycles += tot
            total_calls += calls
            active_count += 1

    entries = []
    counts = {}

    for fn in functions:
        r1 = rows1.get(fn)
        r2 = rows2.get(fn)

        m_calls = to_int(r1.get("calls")) if r1 else None
        m_min = to_int(r1.get("min_cycles")) if r1 else None
        m_max = to_int(r1.get("max_cycles")) if r1 else None
        if m_max is None and r1:
            m_max = to_int(r1.get("measured_max"))
        m_avg = to_float(r1.get("avg_cycles")) if r1 else None
        m_total = to_int(r1.get("total_cycles")) if r1 else None

        if r1 and m_calls is None and m_max is not None:
            m_calls = 1
        if m_avg is None and m_total is not None and m_calls and m_calls > 0:
            m_avg = m_total / float(m_calls)
        elif m_avg is None and m_max is not None:
            m_avg = float(m_max)
        if m_min is None and m_max is not None:
            m_min = m_max
        if m_total is None and m_avg is not None and m_calls is not None:
            m_total = int(round(m_avg * m_calls))

        w_max = to_float(r2.get("max_cycles")) if r2 else None
        if w_max is None and r2:
            w_max = to_float(r2.get("wcet_cycles"))

        # Check for formal RTOS scheduling primitive composition (e.g. primitive + PendSV_Handler)
        fn_spec = cfg_functions.get(fn, {})
        switch_symbol = fn_spec.get("switch_overhead")
        call_site_overhead = to_float(fn_spec.get("call_site_overhead_cycles"))
        effective_w = int(round(w_max)) if w_max is not None else None
        if call_site_overhead is not None and effective_w is not None:
            effective_w += int(round(call_site_overhead))
        elif switch_symbol and effective_w is not None and switch_symbol in rows2:
            sw_val = to_float(rows2[switch_symbol].get("max_cycles"))
            if sw_val is not None:
                effective_w += int(round(sw_val))

        unbounded = is_unbounded(r2, work_dir, fn) if effective_w is None else False

        if m_max is None and effective_w is None and not unbounded:
            continue

        has_run = (m_calls is not None and m_calls > 0 and m_max is not None)
        jitter = (m_max - m_min) if (has_run and m_min is not None and m_max >= m_min) else None
        avg_int = int(m_avg) if (has_run and m_avg is not None) else None
        outlier = bool(jitter is not None and avg_int is not None and jitter > (avg_int // 10) and jitter > 5)
        pct = ((m_total * 100.0) / total_active_cycles) if (has_run and m_total is not None and total_active_cycles > 0) else 0.0

        margin = None
        ratio = None
        if not has_run:
            wcet_check = "NOT_RUN"
        elif unbounded:
            wcet_check = "UNBOUNDED"
        elif effective_w is None:
            wcet_check = "NO_WCET"
        else:
            margin = effective_w - m_max
            ratio = (effective_w / float(m_max)) if m_max > 0 else 0.0
            wcet_check = "SAFE" if margin >= 0 else "EXCEEDED !"

        counts[wcet_check] = counts.get(wcet_check, 0) + 1

        entries.append({
            "fn": fn,
            "sort_total": m_total if (has_run and m_total is not None) else -1,
            "has_run": has_run,
            "calls": m_calls if has_run else 0,
            "total": m_total if has_run else 0,
            "pct": pct,
            "min": m_min if has_run else None,
            "max": m_max if has_run else None,
            "avg_int": avg_int,
            "avg_float": m_avg if has_run else None,
            "jitter": jitter,
            "outlier": outlier,
            "wcet": effective_w,
            "unbounded": unbounded,
            "margin": margin,
            "ratio": ratio,
            "wcet_check": wcet_check,
        })

    # Stable sort by total_cycles descending (mirroring prof-top)
    entries.sort(key=lambda e: e["sort_total"], reverse=True)

    results = []
    for idx, e in enumerate(entries, start=2):
        has_bound = e["margin"] is not None and e["ratio"] is not None
        results.append({
            "function": e["fn"],
            "number of calls": str(e["calls"]) if e["has_run"] else "0",
            "total_cycles": str(e["total"]) if e["has_run"] else "",
            "work_%": f"{e['pct']:.1f}" if e["has_run"] else "",
            "min_cycles": str(e["min"]) if e["min"] is not None else "",
            "max_cycles": str(e["max"]) if e["max"] is not None else "",
            "avg_cycles": f"{e['avg_float']:.1f}" if e["avg_float"] is not None else "",
            "jitter_cycles": str(e["jitter"]) if e["jitter"] is not None else "",
            "wcet_cycles": "-" if e["unbounded"] else (str(e["wcet"]) if e["wcet"] is not None else ""),
            "margin_cycles": f"=I{idx}-F{idx}" if has_bound else "",
            "ratio": f"=ROUND(I{idx}/F{idx},2)" if has_bound else "",
            "wcet_check": e["wcet_check"],
        })

    # Print prof-top mirror table to stdout
    width = 123
    title = f"=== PROFILER vs STATIC WCET [{file1.name} vs {file2.name} | Units: Cycles] "
    print("\n" + title + "=" * max(4, width - len(title)))
    print(
        f"{'Function':<30} {'Calls':>8} {'Total (kcyc)':>14} {'%Work':>7} "
        f"{'Min':>6} {'Max':>6} {'Avg':>6} {'Jitter':>7}   "
        f"{'WCET':>7} {'Margin':>8} {'Ratio':>7}  {'WCET Check'}"
    )
    print("-" * width)

    for e in entries:
        fn_disp = e["fn"][:30]
        if e["has_run"]:
            calls_s = f"{e['calls']:8d}"
            tot_k_s = f"{e['total'] // 1000:14d}"
            pct_s = f"{e['pct']:6.1f}%"
            min_s = f"{e['min']:6d}"
            max_s = f"{e['max']:6d}"
            avg_s = f"{e['avg_int']:6d}"
            jit_val = f"+{e['jitter']}"
            jit_s = f"{jit_val:>7} {'!' if e['outlier'] else ' '}"
        else:
            calls_s = f"{0:8d}"
            tot_k_s = f"{'-':>14}"
            pct_s = f"{'-':>7}"
            min_s = f"{'-':>6}"
            max_s = f"{'-':>6}"
            avg_s = f"{'-':>6}"
            jit_s = f"{'-':>7}  "

        wcet_s = f"{'-':>7}" if e["unbounded"] else (f"{e['wcet']:7d}" if e["wcet"] is not None else f"{'-':>7}")
        margin_s = f"{e['margin']:+8d}" if e["margin"] is not None else f"{'-':>8}"
        ratio_s = f"{e['ratio']:6.2f}x" if e["ratio"] is not None else f"{'-':>7}"

        print(
            f"{fn_disp:<30} {calls_s} {tot_k_s} {pct_s} "
            f"{min_s} {max_s} {avg_s} {jit_s} "
            f"{wcet_s} {margin_s} {ratio_s}  {e['wcet_check']}"
        )

    print("-" * width)
    active_mcyc = total_active_cycles / 1_000_000.0
    print(f"Function Summary:   {active_count} active functions | {total_calls} calls | Active: {active_mcyc:.1f}M cyc")

    safe_n = counts.get("SAFE", 0)
    exceeded_n = counts.get("EXCEEDED !", 0)
    other_parts = [f"{counts[k]} {k}" for k in ("UNBOUNDED", "NO_WCET", "NOT_RUN") if counts.get(k)]
    check_parts = [f"{safe_n} SAFE", f"{exceeded_n} EXCEEDED"] + other_parts

    comparable = [e for e in entries if e["ratio"] is not None]
    if comparable:
        tightest = min(comparable, key=lambda e: e["ratio"])
        avg_ratio = sum(e["ratio"] for e in comparable) / len(comparable)
        print(
            f"WCET Check Summary: {' | '.join(check_parts)} | "
            f"Tightest bound: {tightest['ratio']:.2f}x ({tightest['fn']}) | "
            f"Avg ratio: {avg_ratio:.2f}x"
        )
    else:
        print(f"WCET Check Summary: {' | '.join(check_parts)}")
    print("=" * width)

    # Write output CSV if requested
    if output:
        try:
            with output.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADER)
                writer.writeheader()
                writer.writerows(results)
            print(f"wrote {output}")
        except PermissionError:
            print(f"warning: could not write {output} (file is open in another program)", file=sys.stderr)

    return 1 if exceeded_n > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
