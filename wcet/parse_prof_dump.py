#!/usr/bin/env python3
"""Parse PROF-BIN binary profiler dumps and report execution statistics."""

import argparse
import csv
import html
import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, TextIO, Tuple


HEADER_V2_FORMAT = "<4sHHIHH"
HEADER_V2_SIZE = struct.calcsize(HEADER_V2_FORMAT)
HEADER_V3_FORMAT = "<4sHHIHHHHHBB"
HEADER_V3_SIZE = struct.calcsize(HEADER_V3_FORMAT)
HEADER_V4_FORMAT = HEADER_V3_FORMAT + "HH"
HEADER_V4_SIZE = struct.calcsize(HEADER_V4_FORMAT)
HEADER_V5_FORMAT = HEADER_V4_FORMAT + "I"
HEADER_V5_SIZE = struct.calcsize(HEADER_V5_FORMAT)
HEADER_PREFIX_FORMAT = "<4sH"
SUPPORTED_VERSIONS = {2: HEADER_V2_SIZE, 3: HEADER_V3_SIZE, 4: HEADER_V4_SIZE, 5: HEADER_V5_SIZE}
TIME_MODES = {0: "wall", 1: "active"}
RECORD_V4_FORMAT = "<IIQII"
RECORD_V4_SIZE = struct.calcsize(RECORD_V4_FORMAT)
RECORD_V5_FORMAT = "<IIIIQII8H"
RECORD_V5_SIZE = struct.calcsize(RECORD_V5_FORMAT)
RECORD_FORMAT = RECORD_V4_FORMAT
RECORD_SIZE = RECORD_V4_SIZE
TRAILER_FORMAT = "<I"
TRAILER_SIZE = struct.calcsize(TRAILER_FORMAT)

OUTPUT_FIELDS = (
    "function",
    "function_address",
    "caller",
    "caller_site",
    "calls",
    "total_cycles",
    "min_cycles",
    "max_cycles",
    "avg_cycles",
    "p50_cycles",
    "p99_cycles",
    "avg_us",
    "min_us",
    "max_us",
    "source",
)


class ProfilerHeader(NamedTuple):
    magic: bytes
    version: int
    record_count: int
    frequency: int
    dropped_functions: int
    stack_overflows: int
    unmatched_exits: int = 0
    stale_frames: int = 0
    context_overflows: int = 0
    time_mode: int = 0
    header_size: int = HEADER_V2_SIZE
    probe_overhead: int = 0
    nested_overhead: int = 0
    session_id: int = 0

    @property
    def time_mode_name(self) -> str:
        return TIME_MODES.get(self.time_mode, f"unknown({self.time_mode})")


class MetricRecord(NamedTuple):
    fn_address: int
    call_count: int
    total_cycles: int
    min_cycles: int
    max_cycles: int
    caller_site: int = 0
    buckets: Tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0, 0)


def calculate_crc32(seed: int, data: bytes) -> int:
    crc = seed
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
    return crc


def parse_header(data: bytes) -> ProfilerHeader:
    if len(data) < struct.calcsize(HEADER_PREFIX_FORMAT):
        raise ValueError("dump file is smaller than minimum binary packet size")

    magic, version = struct.unpack_from(HEADER_PREFIX_FORMAT, data, 0)
    if magic != b"PROF":
        raise ValueError(f"invalid magic signature: {magic!r} (expected b'PROF')")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported PROF-BIN protocol version: {version}")

    header_size = SUPPORTED_VERSIONS[version]
    if len(data) < header_size + TRAILER_SIZE:
        raise ValueError("dump file is smaller than minimum binary packet size")

    if version == 2:
        _, _, count, frequency, dropped, stack_of = struct.unpack_from(
            HEADER_V2_FORMAT, data, 0
        )
        return ProfilerHeader(
            magic=magic,
            version=version,
            record_count=count,
            frequency=frequency,
            dropped_functions=dropped,
            stack_overflows=stack_of,
            header_size=header_size,
        )

    probe, nested, session_id = 0, 0, 0
    if version == 3:
        fields = struct.unpack_from(HEADER_V3_FORMAT, data, 0)
    elif version == 4:
        fields = struct.unpack_from(HEADER_V4_FORMAT, data, 0)
        probe, nested = fields[11], fields[12]
    else:
        fields = struct.unpack_from(HEADER_V5_FORMAT, data, 0)
        probe, nested = fields[11], fields[12]
        session_id = fields[13]
    (_, _, count, frequency, dropped, stack_of, unmatched, stale, ctx_of, mode, _) = fields[:11]
    return ProfilerHeader(
        magic=magic,
        version=version,
        record_count=count,
        frequency=frequency,
        dropped_functions=dropped,
        stack_overflows=stack_of,
        unmatched_exits=unmatched,
        stale_frames=stale,
        context_overflows=ctx_of,
        time_mode=mode,
        header_size=header_size,
        probe_overhead=probe,
        nested_overhead=nested,
        session_id=session_id,
    )


def read_binary_dump(path: Path) -> Tuple[ProfilerHeader, List[MetricRecord], int]:
    data = path.read_bytes()
    header = parse_header(data)
    count = header.record_count

    rec_format = RECORD_V5_FORMAT if header.version >= 5 else RECORD_V4_FORMAT
    rec_size = RECORD_V5_SIZE if header.version >= 5 else RECORD_V4_SIZE

    expected_len = header.header_size + (count * rec_size) + TRAILER_SIZE
    if len(data) < expected_len:
        raise ValueError(
            f"truncated binary dump: expected {expected_len} bytes, got {len(data)} bytes"
        )

    records: List[MetricRecord] = []
    offset = header.header_size
    for _ in range(count):
        if header.version >= 5:
            (
                fn_addr,
                caller_site,
                calls,
                _flags,
                total_cyc,
                min_cyc,
                max_cyc,
                b0,
                b1,
                b2,
                b3,
                b4,
                b5,
                b6,
                b7,
            ) = struct.unpack_from(rec_format, data, offset)
            buckets = (b0, b1, b2, b3, b4, b5, b6, b7)
        else:
            fn_addr, calls, total_cyc, min_cyc, max_cyc = struct.unpack_from(
                rec_format, data, offset
            )
            caller_site = 0
            buckets = (0, 0, 0, 0, 0, 0, 0, 0)
        offset += rec_size
        records.append(
            MetricRecord(
                fn_address=fn_addr,
                call_count=calls,
                total_cycles=total_cyc,
                min_cycles=min_cyc,
                max_cycles=max_cyc,
                caller_site=caller_site,
                buckets=buckets,
            )
        )

    (trailer_crc,) = struct.unpack_from(TRAILER_FORMAT, data, offset)

    # Validate CRC-32 over header and records payload
    crc = calculate_crc32(0xFFFFFFFF, data[:offset]) ^ 0xFFFFFFFF
    if crc != trailer_crc:
        raise ValueError(
            f"CRC-32 checksum mismatch: calculated 0x{crc:08X}, expected 0x{trailer_crc:08X}"
        )

    return header, records, trailer_crc

def resolve_symbols(
    addresses: Sequence[int], elf_path: Optional[Path], addr2line: str
) -> Dict[int, Tuple[str, str]]:
    if not addresses:
        return {}

    if elf_path is None:
        return {address: ("0x{:08x}".format(address), "") for address in addresses}

    with elf_path.open("rb") as elf:
        elf_header = elf.read(20)
    if len(elf_header) < 20 or elf_header[:4] != b"\x7fELF" or elf_header[5] not in (1, 2):
        raise ValueError("symbol file is not a valid ELF")
    byte_order = "little" if elf_header[5] == 1 else "big"
    is_arm = int.from_bytes(elf_header[18:20], byte_order) == 40
    command = [
        addr2line,
        "-f",
        "-C",
        "-e",
        str(elf_path),
    ] + ["0x{:08x}".format(address & ~1 if is_arm else address) for address in addresses]

    process = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    lines = [line.strip() for line in process.stdout.splitlines()]
    if len(lines) != len(addresses) * 2:
        raise ValueError("addr2line returned an unexpected number of lines")

    symbols: Dict[int, Tuple[str, str]] = {}
    for index, address in enumerate(addresses):
        function_name = lines[index * 2]
        source_location = lines[index * 2 + 1]
        if function_name == "??":
            function_name = "0x{:08x}".format(address)
        symbols[address] = (function_name, source_location)
    return symbols


def resolve_text_log(text: str, elf_path: Path, addr2line: str) -> str:
    """Resolve the address fields of CSV/TOP rows, preserving other output."""
    pattern = re.compile(
        r"^(0x[0-9a-fA-F]{8})(?:<-(0x[0-9a-fA-F]{8}))?(?=,|\s)", re.MULTILINE
    )
    raw_matches = list(pattern.finditer(text))
    if not raw_matches:
        raise ValueError("text log contains no address-based profiler rows")

    addresses: List[int] = []
    for match in raw_matches:
        addresses.append(int(match.group(1), 16))
        if match.group(2):
            addresses.append(int(match.group(2), 16))
    symbols = resolve_symbols(list(dict.fromkeys(addresses)), elf_path, addr2line)

    def replace_row(match: re.Match) -> str:
        fn_sym = symbols[int(match.group(1), 16)][0]
        if match.group(2):
            caller_sym = symbols[int(match.group(2), 16)][0]
            return f"{fn_sym}<-{caller_sym}"
        return fn_sym

    return pattern.sub(replace_row, text)


def estimate_percentiles(buckets: Sequence[int], total_calls: int) -> Tuple[int, int]:
    """Estimate p50 and p99 cycle thresholds from log2 bucket counts."""
    if total_calls == 0 or sum(buckets) == 0:
        return 0, 0
    bounds = [16, 64, 256, 1024, 4096, 16384, 65536, 131072]
    cum = 0
    p50, p99 = bounds[0], bounds[-1]
    p50_target = total_calls * 0.50
    p99_target = total_calls * 0.99
    found_50, found_99 = False, False
    for b_idx, count in enumerate(buckets):
        cum += count
        if not found_50 and cum >= p50_target:
            p50 = bounds[b_idx]
            found_50 = True
        if not found_99 and cum >= p99_target:
            p99 = bounds[b_idx]
            found_99 = True
    return p50, p99


def write_metrics_csv(
    output: TextIO,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
    frequency: int,
) -> None:
    writer = csv.DictWriter(output, fieldnames=OUTPUT_FIELDS)
    writer.writeheader()
    for record in records:
        func_name, source = symbols.get(
            record.fn_address, ("0x{:08x}".format(record.fn_address), "")
        )
        caller_name = "-"
        if record.caller_site != 0:
            caller_name = symbols.get(
                record.caller_site, ("0x{:08x}".format(record.caller_site), "")
            )[0]
        caller_site_str = "0x{:08x}".format(record.caller_site) if record.caller_site != 0 else "-"

        avg_cycles = (
            record.total_cycles / record.call_count if record.call_count > 0 else 0
        )
        p50_cyc, p99_cyc = estimate_percentiles(record.buckets, record.call_count)
        avg_us = (avg_cycles * 1000000.0) / frequency if frequency > 0 else 0.0
        min_us = (record.min_cycles * 1000000.0) / frequency if frequency > 0 else 0.0
        max_us = (record.max_cycles * 1000000.0) / frequency if frequency > 0 else 0.0

        writer.writerow(
            {
                "function": func_name,
                "function_address": "0x{:08x}".format(record.fn_address),
                "caller": caller_name,
                "caller_site": caller_site_str,
                "calls": record.call_count,
                "total_cycles": record.total_cycles,
                "min_cycles": record.min_cycles,
                "max_cycles": record.max_cycles,
                "avg_cycles": f"{avg_cycles:.1f}",
                "p50_cycles": p50_cyc,
                "p99_cycles": p99_cyc,
                "avg_us": f"{avg_us:.3f}",
                "min_us": f"{min_us:.3f}",
                "max_us": f"{max_us:.3f}",
                "source": source,
            }
        )


def write_summary(
    output: TextIO,
    header: ProfilerHeader,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
) -> None:
    writer = csv.writer(output)
    writer.writerow(("frequency_hz", header.frequency))
    writer.writerow(("unique_functions", header.record_count))
    writer.writerow(("dropped_functions", header.dropped_functions))
    writer.writerow(("stack_overflows", header.stack_overflows))
    writer.writerow(("protocol_version", header.version))
    if header.version >= 3:
        writer.writerow(("unmatched_exits", header.unmatched_exits))
        writer.writerow(("stale_frames", header.stale_frames))
        writer.writerow(("context_overflows", header.context_overflows))
        writer.writerow(("time_mode", header.time_mode_name))
    if header.version >= 4:
        writer.writerow(("probe_overhead_cycles", header.probe_overhead))
        writer.writerow(("nested_overhead_cycles", header.nested_overhead))
    if header.version >= 5 and header.session_id != 0:
        writer.writerow(("session_id", f"0x{header.session_id:08X}"))
    writer.writerow(
        ("function", "calls", "total_cycles", "min_cycles", "avg_cycles", "max_cycles", "avg_us")
    )
    for record in sorted(records, key=lambda r: r.total_cycles, reverse=True):
        func_name = symbols.get(
            record.fn_address, ("0x{:08x}".format(record.fn_address), "")
        )[0]
        avg_cycles = (
            record.total_cycles / record.call_count if record.call_count > 0 else 0
        )
        avg_us = (avg_cycles * 1000000.0) / header.frequency if header.frequency > 0 else 0.0
        writer.writerow(
            (
                func_name,
                record.call_count,
                record.total_cycles,
                record.min_cycles,
                f"{avg_cycles:.1f}",
                record.max_cycles,
                f"{avg_us:.3f}",
            )
        )


def write_markdown_report(
    output: TextIO,
    header: ProfilerHeader,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
) -> None:
    total_active_cycles = sum(r.total_cycles for r in records)
    total_calls = sum(r.call_count for r in records)
    output.write("# Profiler Execution Report\n\n")
    output.write("## Target Summary\n\n")
    output.write("| Metric | Value |\n| :--- | :--- |\n")
    output.write(f"| Protocol Version | PROF-BIN v{header.version} |\n")
    output.write(f"| Clock Frequency | {header.frequency} Hz |\n")
    output.write(f"| Time Mode | {header.time_mode_name} |\n")
    output.write(f"| Unique Functions | {header.record_count} |\n")
    output.write(f"| Total Calls | {total_calls} |\n")
    output.write(f"| Total Active Cycles | {total_active_cycles} |\n")
    output.write(f"| Dropped Functions | {header.dropped_functions} |\n")
    output.write(f"| Stack Overflows | {header.stack_overflows} |\n")
    if header.version >= 3:
        output.write(f"| Unmatched Exits | {header.unmatched_exits} |\n")
        output.write(f"| Stale Frames | {header.stale_frames} |\n")
        output.write(f"| Context Overflows | {header.context_overflows} |\n")
    if header.version >= 4:
        output.write(f"| Calibrated Probe Overhead | {header.probe_overhead} cyc |\n")
        output.write(f"| Calibrated Nested Overhead | {header.nested_overhead} cyc |\n")
    if header.version >= 5 and header.session_id != 0:
        output.write(f"| Session ID | `0x{header.session_id:08X}` |\n")

    output.write("\n## Function Metrics\n\n")
    has_callers = any(r.caller_site != 0 for r in records)
    has_buckets = any(any(r.buckets) for r in records)
    if has_callers and has_buckets:
        output.write(
            "| Function | Caller | Address | Calls | Total Cycles | % Work | Min (cyc) | p50 (cyc) | p99 (cyc) | Avg (cyc) | Max (cyc) | Avg (us) | Max (us) | Source |\n"
        )
        output.write(
            "| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |\n"
        )
    elif has_callers:
        output.write(
            "| Function | Caller | Address | Calls | Total Cycles | % Work | Min (cyc) | Avg (cyc) | Max (cyc) | Avg (us) | Max (us) | Source |\n"
        )
        output.write(
            "| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |\n"
        )
    elif has_buckets:
        output.write(
            "| Function | Address | Calls | Total Cycles | % Work | Min (cyc) | p50 (cyc) | p99 (cyc) | Avg (cyc) | Max (cyc) | Avg (us) | Max (us) | Source |\n"
        )
        output.write(
            "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |\n"
        )
    else:
        output.write(
            "| Function | Address | Calls | Total Cycles | % Work | Min (cyc) | Avg (cyc) | Max (cyc) | Avg (us) | Max (us) | Source |\n"
        )
        output.write(
            "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |\n"
        )
    for record in sorted(records, key=lambda r: r.total_cycles, reverse=True):
        func_name, source = symbols.get(
            record.fn_address, ("0x{:08x}".format(record.fn_address), "")
        )
        avg_cycles = (
            record.total_cycles / record.call_count if record.call_count > 0 else 0.0
        )
        p50_cyc, p99_cyc = estimate_percentiles(record.buckets, record.call_count)
        work_pct = (
            (record.total_cycles * 100.0) / total_active_cycles
            if total_active_cycles > 0
            else 0.0
        )
        avg_us = (
            (avg_cycles * 1000000.0) / header.frequency if header.frequency > 0 else 0.0
        )
        max_us = (
            (record.max_cycles * 1000000.0) / header.frequency
            if header.frequency > 0
            else 0.0
        )
        caller_str = ""
        if has_callers:
            c_name = (
                symbols.get(record.caller_site, ("0x{:08x}".format(record.caller_site), ""))[0]
                if record.caller_site != 0
                else "-"
            )
            caller_str = f"| `{c_name}` "

        pct_str = f"| {p50_cyc} | {p99_cyc} " if has_buckets else ""
        output.write(
            f"| `{func_name}` {caller_str}| `0x{record.fn_address:08x}` | {record.call_count} | "
            f"{record.total_cycles} | {work_pct:.1f}% | {record.min_cycles} "
            f"{pct_str}| {avg_cycles:.1f} | {record.max_cycles} | {avg_us:.3f} | {max_us:.3f} | "
            f"{source or '-'} |\n"
        )


def write_speedscope_json(
    output: TextIO,
    header: ProfilerHeader,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
) -> None:
    frames: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    cursor = 0
    for idx, record in enumerate(
        sorted(records, key=lambda r: r.total_cycles, reverse=True)
    ):
        func_name, source = symbols.get(
            record.fn_address, ("0x{:08x}".format(record.fn_address), "")
        )
        frame: Dict[str, Any] = {"name": func_name}
        if source and ":" in source:
            file_part, line_part = source.rsplit(":", 1)
            frame["file"] = file_part
            if line_part.isdigit():
                frame["line"] = int(line_part)
        frames.append(frame)
        events.append({"type": "O", "frame": idx, "at": cursor})
        cursor += record.total_cycles
        events.append({"type": "C", "frame": idx, "at": cursor})

    doc = {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "exporter": f"profiler-parse_prof_dump/v{header.version}",
        "name": "PROF-BIN Aggregate Profile",
        "activeProfileIndex": 0,
        "shared": {"frames": frames},
        "profiles": [
            {
                "type": "evented",
                "name": f"Active Cycles ({header.frequency} Hz)",
                "unit": "none",
                "startValue": 0,
                "endValue": cursor,
                "events": events,
            }
        ],
    }
    json.dump(doc, output, indent=2)
    output.write("\n")


def write_chrome_trace_json(
    output: TextIO,
    header: ProfilerHeader,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
) -> None:
    trace_events: List[Dict[str, Any]] = [
        {
            "name": "process_name",
            "ph": "M",
            "pid": 1,
            "tid": 1,
            "args": {"name": "Target Firmware (PROF-BIN)"},
        },
        {
            "name": "thread_name",
            "ph": "M",
            "pid": 1,
            "tid": 1,
            "args": {"name": f"Active Execution ({header.time_mode_name})"},
        },
    ]
    cursor_us = 0.0
    for record in sorted(records, key=lambda r: r.total_cycles, reverse=True):
        func_name, source = symbols.get(
            record.fn_address, ("0x{:08x}".format(record.fn_address), "")
        )
        dur_us = (
            (record.total_cycles * 1000000.0) / header.frequency
            if header.frequency > 0
            else float(record.total_cycles)
        )
        avg_cycles = (
            record.total_cycles / record.call_count if record.call_count > 0 else 0.0
        )
        trace_events.append(
            {
                "name": func_name,
                "cat": "function",
                "ph": "X",
                "pid": 1,
                "tid": 1,
                "ts": round(cursor_us, 3),
                "dur": round(dur_us, 3),
                "args": {
                    "address": f"0x{record.fn_address:08x}",
                    "calls": record.call_count,
                    "total_cycles": record.total_cycles,
                    "min_cycles": record.min_cycles,
                    "avg_cycles": round(avg_cycles, 1),
                    "max_cycles": record.max_cycles,
                    "source": source,
                },
            }
        )
        cursor_us += dur_us

    doc = {
        "displayTimeUnit": "ms",
        "metadata": {
            "protocol_version": header.version,
            "frequency_hz": header.frequency,
            "time_mode": header.time_mode_name,
            "dropped_functions": header.dropped_functions,
            "stack_overflows": header.stack_overflows,
            "unmatched_exits": header.unmatched_exits,
            "stale_frames": header.stale_frames,
            "context_overflows": header.context_overflows,
            "probe_overhead_cycles": header.probe_overhead,
            "nested_overhead_cycles": header.nested_overhead,
        },
        "traceEvents": trace_events,
    }
    json.dump(doc, output, indent=2)
    output.write("\n")


def write_callgrind(
    output: TextIO,
    header: ProfilerHeader,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
) -> None:
    total_cycles = sum(r.total_cycles for r in records)
    total_calls = sum(r.call_count for r in records)
    output.write("# callgrind format\n")
    output.write("version: 1\n")
    output.write(f"creator: profiler-parse_prof_dump/v{header.version}\n")
    output.write(
        f"desc: Target: PROF-BIN v{header.version} ({header.frequency} Hz, mode={header.time_mode_name})\n"
    )
    output.write("positions: instr line\n")
    output.write("events: Cycles Calls\n")
    output.write(f"summary: {total_cycles} {total_calls}\n\n")

    def parse_source(src: str) -> Tuple[str, int]:
        file_name, line_num = "??", 0
        if src and ":" in src:
            file_part, line_part = src.rsplit(":", 1)
            if file_part:
                file_name = file_part
            if line_part.isdigit():
                line_num = int(line_part)
        elif src:
            file_name = src
        return file_name, line_num

    for record in sorted(records, key=lambda r: r.total_cycles, reverse=True):
        func_name, source = symbols.get(
            record.fn_address, ("0x{:08x}".format(record.fn_address), "")
        )
        file_name, line_num = parse_source(source)

        if record.caller_site != 0:
            caller_name, caller_source = symbols.get(
                record.caller_site, ("0x{:08x}".format(record.caller_site), "")
            )
            c_file, c_line = parse_source(caller_source)
            output.write(f"fl={c_file}\n")
            output.write(f"fn={caller_name}\n")
            output.write(f"cfl={file_name}\n")
            output.write(f"cfn={func_name}\n")
            output.write(f"calls={record.call_count} {line_num}\n")
            output.write(
                f"0x{record.caller_site:08x} {c_line} {record.total_cycles} {record.call_count}\n\n"
            )

        output.write(f"fl={file_name}\n")
        output.write(f"fn={func_name}\n")
        output.write(
            f"0x{record.fn_address:08x} {line_num} {record.total_cycles} {record.call_count}\n\n"
        )
    output.write(f"totals: {total_cycles} {total_calls}\n")


def write_html_report(
    output: TextIO,
    header: ProfilerHeader,
    records: Sequence[MetricRecord],
    symbols: Dict[int, Tuple[str, str]],
) -> None:
    total_active_cycles = sum(r.total_cycles for r in records)
    total_calls = sum(r.call_count for r in records)
    has_callers = any(r.caller_site != 0 for r in records)
    has_buckets = any(any(r.buckets) for r in records)
    sorted_records = sorted(records, key=lambda r: r.total_cycles, reverse=True)

    freq_str = (
        f"{header.frequency / 1e6:.2f} MHz"
        if header.frequency >= 1e6
        else f"{header.frequency:,} Hz"
    )
    session_str = (
        f"0x{header.session_id:08X}"
        if header.version >= 5 and header.session_id != 0
        else "N/A"
    )

    colors = [
        "#38bdf8", "#818cf8", "#f472b6", "#fb923c", "#4ade80",
        "#a78bfa", "#facc15", "#2dd4bf", "#f87171", "#60a5fa",
        "#c084fc", "#34d399", "#fbbf24", "#e879f9", "#22d3ee"
    ]

    flame_segments: List[str] = []
    flame_legend: List[str] = []
    cum_pct = 0.0
    for idx, r in enumerate(sorted_records[:15]):
        fn_name = symbols.get(r.fn_address, (f"0x{r.fn_address:08x}", ""))[0]
        pct = (r.total_cycles * 100.0) / total_active_cycles if total_active_cycles > 0 else 0.0
        if pct < 0.2:
            continue
        color = colors[idx % len(colors)]
        escaped_fn = html.escape(fn_name)
        flame_segments.append(
            f'<rect x="{cum_pct:.2f}%" y="0" width="{pct:.2f}%" height="36" fill="{color}" rx="3">'
            f'<title>{escaped_fn}: {r.total_cycles:,} cycles ({pct:.1f}%), {r.call_count:,} calls</title>'
            f'</rect>'
        )
        flame_legend.append(
            f'<span style="display:inline-flex;align-items:center;margin-right:16px;margin-bottom:8px;font-size:12px;">'
            f'<span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:{color};margin-right:6px;"></span>'
            f'<span>{escaped_fn} ({pct:.1f}%)</span>'
            f'</span>'
        )
        cum_pct += pct

    if cum_pct < 99.9 and total_active_cycles > 0:
        remainder = 100.0 - cum_pct
        flame_segments.append(
            f'<rect x="{cum_pct:.2f}%" y="0" width="{remainder:.2f}%" height="36" fill="#475569" rx="3">'
            f'<title>Other functions: {remainder:.1f}%</title>'
            f'</rect>'
        )
        flame_legend.append(
            f'<span style="display:inline-flex;align-items:center;margin-right:16px;margin-bottom:8px;font-size:12px;">'
            f'<span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:#475569;margin-right:6px;"></span>'
            f'<span>Other ({remainder:.1f}%)</span>'
            f'</span>'
        )

    table_rows: List[str] = []
    bucket_labels = ["<16", "16-63", "64-255", "256-1k", "1k-4k", "4k-16k", "16k-65k", ">=65k"]
    for r in sorted_records:
        fn_name, source = symbols.get(r.fn_address, (f"0x{r.fn_address:08x}", ""))
        avg_cycles = r.total_cycles / r.call_count if r.call_count > 0 else 0.0
        p50_cyc, p99_cyc = estimate_percentiles(r.buckets, r.call_count)
        work_pct = (r.total_cycles * 100.0) / total_active_cycles if total_active_cycles > 0 else 0.0
        avg_us = (avg_cycles * 1000000.0) / header.frequency if header.frequency > 0 else 0.0
        max_us = (r.max_cycles * 1000000.0) / header.frequency if header.frequency > 0 else 0.0

        caller_td = ""
        if has_callers:
            c_name = symbols.get(r.caller_site, (f"0x{r.caller_site:08x}", ""))[0] if r.caller_site != 0 else "-"
            caller_td = f"<td><code>{html.escape(c_name)}</code></td>"

        sparkline_td = ""
        percentile_tds = ""
        if has_buckets:
            max_b = max(r.buckets) if any(r.buckets) else 1
            spark_bars = []
            for b_idx, b_count in enumerate(r.buckets):
                h = max(2, int((b_count / max_b) * 16)) if b_count > 0 else 0
                spark_bars.append(
                    f'<span class="spark-bar" style="height:{h}px;" title="{bucket_labels[b_idx]} cyc: {b_count} calls"></span>'
                )
            sparkline_td = (
                f'<td style="white-space:nowrap;vertical-align:middle;">'
                f'<div style="display:inline-flex;align-items:flex-end;height:18px;">{"".join(spark_bars)}</div>'
                f'</td>'
            )
            percentile_tds = f'<td data-sort="{p50_cyc}">{p50_cyc:,}</td><td data-sort="{p99_cyc}">{p99_cyc:,}</td>'

        table_rows.append(
            f"<tr>"
            f"<td><code>{html.escape(fn_name)}</code></td>"
            f"{caller_td}"
            f"<td><code>0x{r.fn_address:08x}</code></td>"
            f'<td data-sort="{r.call_count}">{r.call_count:,}</td>'
            f'<td data-sort="{r.total_cycles}">{r.total_cycles:,}</td>'
            f'<td data-sort="{work_pct:.2f}">'
            f'  <div style="display:flex;align-items:center;gap:8px;">'
            f'    <div class="bar-bg"><div class="bar-fill" style="width:{min(100.0, work_pct):.1f}%"></div></div>'
            f'    <span style="min-width:42px;">{work_pct:.1f}%</span>'
            f'  </div>'
            f'</td>'
            f"{sparkline_td}"
            f"{percentile_tds}"
            f'<td data-sort="{r.min_cycles}">{r.min_cycles:,}</td>'
            f'<td data-sort="{avg_cycles:.1f}">{avg_cycles:.1f}</td>'
            f'<td data-sort="{r.max_cycles}">{r.max_cycles:,}</td>'
            f'<td data-sort="{avg_us:.3f}">{avg_us:.3f}</td>'
            f'<td data-sort="{max_us:.3f}">{max_us:.3f}</td>'
            f'<td style="color:#94a3b8;font-size:11px;">{html.escape(source or "-")}</td>'
            f"</tr>"
        )

    caller_th = "<th>Caller</th>" if has_callers else ""
    bucket_ths = "<th>Latency Sparkline</th><th>p50 (cyc)</th><th>p99 (cyc)</th>" if has_buckets else ""

    output.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Embedded Execution Profile Report</title>
<style>
  :root {{
    --bg: #0f172a;
    --card: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --accent: #38bdf8;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px;
    line-height: 1.5;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 24px;
  }}
  h1 {{ font-size: 22px; font-weight: 700; margin: 0; }}
  .badge {{ background: #38bdf822; color: #38bdf8; border: 1px solid #38bdf866; border-radius: 9999px; padding: 4px 12px; font-size: 12px; font-weight: 600; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }}
  .card {{ background: var(--card); border-radius: 8px; padding: 14px 16px; border: 1px solid var(--border); }}
  .card-label {{ font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }}
  .card-val {{ font-size: 18px; font-weight: 700; color: #f8fafc; }}
  .section-title {{ font-size: 16px; font-weight: 600; margin: 24px 0 12px 0; color: #f8fafc; }}
  .chart-box {{ background: var(--card); border-radius: 8px; padding: 18px; border: 1px solid var(--border); margin-bottom: 24px; }}
  .table-box {{ background: var(--card); border-radius: 8px; padding: 18px; border: 1px solid var(--border); overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; text-align: left; }}
  th {{ background: #0f172a; color: var(--text-muted); padding: 10px 12px; font-weight: 600; cursor: pointer; user-select: none; border-bottom: 2px solid var(--border); white-space: nowrap; }}
  th:hover {{ color: var(--accent); }}
  th.sorted-asc::after {{ content: " ▲"; font-size: 10px; }}
  th.sorted-desc::after {{ content: " ▼"; font-size: 10px; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: rgba(56, 189, 248, 0.04); }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; background: rgba(255,255,255,0.06); padding: 2px 5px; border-radius: 4px; }}
  .search-input {{ width: 100%; max-width: 380px; padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border); background: #0f172a; color: #fff; margin-bottom: 14px; box-sizing: border-box; font-size: 13px; }}
  .search-input:focus {{ outline: none; border-color: var(--accent); }}
  .bar-bg {{ width: 80px; background: #334155; height: 10px; border-radius: 5px; overflow: hidden; }}
  .bar-fill {{ background: linear-gradient(90deg, #38bdf8, #818cf8); height: 100%; border-radius: 5px; }}
  .spark-bar {{ width: 4px; margin-right: 1.5px; background: #38bdf8; border-radius: 1px; display: inline-block; }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>Embedded Execution Profile Report</h1>
      <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">PROF-BIN Protocol Dashboard &bull; Exported automatically</div>
    </div>
    <div class="badge">PROF-BIN v{header.version}</div>
  </div>

  <div class="card-grid">
    <div class="card">
      <div class="card-label">Clock Frequency</div>
      <div class="card-val">{freq_str}</div>
    </div>
    <div class="card">
      <div class="card-label">Session ID</div>
      <div class="card-val" style="font-family:monospace;font-size:16px;">{session_str}</div>
    </div>
    <div class="card">
      <div class="card-label">Time Mode</div>
      <div class="card-val" style="text-transform:capitalize;">{header.time_mode_name}</div>
    </div>
    <div class="card">
      <div class="card-label">Total Active Cycles</div>
      <div class="card-val">{total_active_cycles:,}</div>
    </div>
    <div class="card">
      <div class="card-label">Total Calls</div>
      <div class="card-val">{total_calls:,}</div>
    </div>
    <div class="card">
      <div class="card-label">Probe / Nested Overhead</div>
      <div class="card-val" style="font-size:16px;">{header.probe_overhead} / {header.nested_overhead} cyc</div>
    </div>
    <div class="card">
      <div class="card-label">Dropped / Overflows</div>
      <div class="card-val" style="font-size:16px;color:{'#f87171' if (header.dropped_functions or header.stack_overflows) else '#4ade80'};">
        {header.dropped_functions} / {header.stack_overflows}
      </div>
    </div>
  </div>

  <div class="section-title">Execution Distribution (Top Functions)</div>
  <div class="chart-box">
    <svg width="100%" height="36" style="border-radius:6px;overflow:hidden;background:#0f172a;border:1px solid var(--border);">
      {''.join(flame_segments)}
    </svg>
    <div style="display:flex;flex-wrap:wrap;margin-top:14px;">
      {''.join(flame_legend)}
    </div>
  </div>

  <div class="section-title">Function Call Metrics ({len(sorted_records)} Entries)</div>
  <div class="table-box">
    <input type="text" id="searchBox" class="search-input" placeholder="Filter functions, callers, source..." onkeyup="filterTable()">
    <table id="metricsTable">
      <thead>
        <tr>
          <th onclick="sortTable(0)">Function</th>
          {caller_th}
          <th onclick="sortTable({1 if has_callers else 0} + 1)">Address</th>
          <th onclick="sortTable({1 if has_callers else 0} + 2)">Calls</th>
          <th onclick="sortTable({1 if has_callers else 0} + 3)">Total Cycles</th>
          <th onclick="sortTable({1 if has_callers else 0} + 4)">% Active</th>
          {bucket_ths}
          <th onclick="sortTable({(1 if has_callers else 0) + (3 if has_buckets else 0)} + 5)">Min (cyc)</th>
          <th onclick="sortTable({(1 if has_callers else 0) + (3 if has_buckets else 0)} + 6)">Avg (cyc)</th>
          <th onclick="sortTable({(1 if has_callers else 0) + (3 if has_buckets else 0)} + 7)">Max (cyc)</th>
          <th onclick="sortTable({(1 if has_callers else 0) + (3 if has_buckets else 0)} + 8)">Avg (µs)</th>
          <th onclick="sortTable({(1 if has_callers else 0) + (3 if has_buckets else 0)} + 9)">Max (µs)</th>
          <th onclick="sortTable({(1 if has_callers else 0) + (3 if has_buckets else 0)} + 10)">Source Location</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>
  </div>

  <script>
    function sortTable(n) {{
      var table = document.getElementById("metricsTable");
      var tbody = table.querySelector("tbody");
      var rows = Array.from(tbody.querySelectorAll("tr"));
      var ths = table.querySelectorAll("th");
      var th = ths[n];
      if (!th) return;
      var isAsc = th.classList.contains("sorted-asc");
      ths.forEach(function(h) {{ h.classList.remove("sorted-asc", "sorted-desc"); }});
      th.classList.add(isAsc ? "sorted-desc" : "sorted-asc");
      var dir = isAsc ? -1 : 1;

      rows.sort(function(a, b) {{
        var cellA = a.children[n];
        var cellB = b.children[n];
        if (!cellA || !cellB) return 0;
        var valA = cellA.hasAttribute("data-sort") ? parseFloat(cellA.getAttribute("data-sort")) : cellA.innerText.trim();
        var valB = cellB.hasAttribute("data-sort") ? parseFloat(cellB.getAttribute("data-sort")) : cellB.innerText.trim();
        if (typeof valA === "number" && !isNaN(valA) && typeof valB === "number" && !isNaN(valB)) {{
          return (valA - valB) * dir;
        }}
        return valA.toString().localeCompare(valB.toString()) * dir;
      }});
      rows.forEach(function(r) {{ tbody.appendChild(r); }});
    }}

    function filterTable() {{
      var input = document.getElementById("searchBox");
      var filter = input.value.toLowerCase();
      var rows = document.querySelectorAll("#metricsTable tbody tr");
      rows.forEach(function(row) {{
        var text = row.innerText.toLowerCase();
        row.style.display = text.indexOf(filter) > -1 ? "" : "none";
      }});
    }}
  </script>
</body>
</html>
""")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decode and resolve execution statistics from a PROF-BIN binary profiler dump."
    )
    parser.add_argument("dump_file", type=Path, help="PROF-BIN binary dump file")
    parser.add_argument("-e", "--elf", type=Path, help="ELF file used by arm-none-eabi-addr2line")
    parser.add_argument(
        "--text",
        action="store_true",
        help="resolve an address-based CSV/TOP text log (requires --elf)",
    )
    parser.add_argument(
        "--addr2line",
        default="arm-none-eabi-addr2line",
        help="addr2line executable (default: arm-none-eabi-addr2line)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write metrics CSV to this file (default: input filename with .csv suffix)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print aggregate summary table to stderr",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        metavar="MD_FILE",
        help="export a Markdown execution report to this file",
    )
    parser.add_argument(
        "--speedscope",
        type=Path,
        metavar="JSON_FILE",
        help="export a Speedscope JSON profile to this file",
    )
    parser.add_argument(
        "--chrome-trace",
        "--perfetto",
        dest="chrome_trace",
        type=Path,
        metavar="TRACE_JSON",
        help="export a Chrome Tracing / Perfetto JSON trace to this file",
    )
    parser.add_argument(
        "--callgrind",
        type=Path,
        metavar="CALLGRIND_FILE",
        help="export a Valgrind/Callgrind profile (KCachegrind/QCachegrind) to this file",
    )
    parser.add_argument(
        "--html",
        type=Path,
        metavar="HTML_FILE",
        help="export an interactive standalone HTML report dashboard to this file",
    )
    parser.add_argument(
        "--verify-session-id",
        metavar="ID",
        help="verify that the target binary dump session ID matches expected 32-bit ID (hex or decimal)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        if args.text:
            if args.elf is None:
                raise ValueError("--text requires --elf")
            text = resolve_text_log(args.dump_file.read_text(encoding="utf-8"), args.elf, args.addr2line)
            output_path = args.output or args.dump_file.with_name(args.dump_file.stem + "_resolved.txt")
            if output_path.resolve() == args.dump_file.resolve():
                raise ValueError("text output must not overwrite the input log")
            output_path.write_text(text, encoding="utf-8")
            return 0
        header, records, _ = read_binary_dump(args.dump_file)
        if args.verify_session_id is not None:
            expected_id = int(args.verify_session_id, 0)
            if header.session_id != expected_id:
                raise ValueError(
                    f"Session ID mismatch: dump reported 0x{header.session_id:08X}, expected 0x{expected_id:08X}"
                )
        if header.version >= 3:
            print(
                f"PROF-BIN v{header.version}: time_mode={header.time_mode_name} "
                f"unmatched_exits={header.unmatched_exits} stale_frames={header.stale_frames} "
                f"context_overflows={header.context_overflows} "
                f"stack_overflows={header.stack_overflows} "
                f"dropped_functions={header.dropped_functions}"
                + (
                    f" probe_overhead={header.probe_overhead} "
                    f"nested_overhead={header.nested_overhead}"
                    if header.version >= 4
                    else ""
                ),
                file=sys.stderr,
            )
        addresses = list(
            dict.fromkeys(
                [record.fn_address for record in records]
                + [record.caller_site for record in records if record.caller_site != 0]
            )
        )
        symbols = resolve_symbols(addresses, args.elf, args.addr2line)

        output_path = (
            args.output if args.output is not None else args.dump_file.with_suffix(".csv")
        )
        with output_path.open("w", encoding="utf-8", newline="") as csv_out:
            write_metrics_csv(csv_out, records, symbols, header.frequency)

        if args.markdown is not None:
            with args.markdown.open("w", encoding="utf-8", newline="\n") as md_out:
                write_markdown_report(md_out, header, records, symbols)

        if args.speedscope is not None:
            with args.speedscope.open("w", encoding="utf-8", newline="\n") as ss_out:
                write_speedscope_json(ss_out, header, records, symbols)

        if args.chrome_trace is not None:
            with args.chrome_trace.open("w", encoding="utf-8", newline="\n") as ct_out:
                write_chrome_trace_json(ct_out, header, records, symbols)

        if args.callgrind is not None:
            with args.callgrind.open("w", encoding="utf-8", newline="\n") as cg_out:
                write_callgrind(cg_out, header, records, symbols)

        if args.html is not None:
            with args.html.open("w", encoding="utf-8", newline="\n") as html_out:
                write_html_report(html_out, header, records, symbols)

        if args.summary:
            write_summary(sys.stderr, header, records, symbols)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
