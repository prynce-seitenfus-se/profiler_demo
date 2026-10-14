#!/usr/bin/env python3
"""Generate a profiler-style CSV with OTAWA static WCET results.

Runs inside WSL with the OTAWA environment loaded (see run_wcet.sh).
Project-specific inputs (ELF, target functions, flow facts, timing model
parameters) come from the --config JSON; relative paths in it are resolved
against the JSON's directory.
The output has the same columns as the runtime profiler CSV (1.csv); since a
static analysis yields one upper bound per function, calls is 1 and
total/min/max/avg all hold the WCET.
"""

import argparse
import csv
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

CSV_HEADER = [
    "function", "function_address", "calls", "total_cycles", "min_cycles",
    "max_cycles", "avg_cycles", "avg_us", "min_us", "max_us", "source",
]
WCET_RE = re.compile(r"WCET\[(?P<fn>[^\]]+)\]\s*=\s*(?P<cycles>\d+)\s*cycles")
UNBOUNDED_LOOP_RE = re.compile(r"no limit for the loop at (?P<fn>\S+) \+ (?P<offset>0x[0-9a-fA-F]+)")
# Windows DWARF paths are joined by Linux addr2line ("C:/build/C:/src.c").
WINDOWS_DRIVE_RE = re.compile(r"[A-Za-z]:/")
OWCET_TIMEOUT_S = 600
SCHEMA_VERSION = 1
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def load_config(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION}
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    version = config.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise SystemExit(f"error: {path}: schema_version {version!r} not supported "
                         f"(expected {SCHEMA_VERSION})")
    return config


def resolve_path(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def read_function_symbols(elf: Path) -> dict:
    result = subprocess.run(["readelf", "-sW", str(elf)], check=True,
                            capture_output=True, text=True)
    symbols = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if (len(fields) >= 8) and (fields[3] == "FUNC"):
            symbols.setdefault(fields[7], int(fields[1], 16))
    return symbols


def find_addr2line() -> str:
    for tool in ("arm-none-eabi-addr2line", "addr2line"):
        if shutil.which(tool):
            return tool
    return ""


def source_location(addr2line: str, elf: Path, address: int) -> str:
    if (not addr2line) or (address is None):
        return "??:?"
    result = subprocess.run([addr2line, "-e", str(elf), hex(address & ~1)],
                            capture_output=True, text=True)
    location = result.stdout.strip().splitlines()
    if (result.returncode != 0) or (not location):
        return "??:?"
    text = location[0].split(" (discriminator")[0]
    drives = list(WINDOWS_DRIVE_RE.finditer(text))
    if len(drives) > 1:
        text = text[drives[-1].start():]
    return text


def merged_loops(config: dict, function: dict, discovered_loops: dict = None) -> list:
    loops = {}
    flow_facts = config.get("flow_facts", {})
    raw_loops = flow_facts.get("loops", [])
    if isinstance(raw_loops, dict):
        # Shorthand map: {"fn_name": 25, "other_fn": [16, 8]}
        expanded = []
        for fn_name, bounds in raw_loops.items():
            b_list = bounds if isinstance(bounds, list) else [bounds]
            offsets = (discovered_loops or {}).get(fn_name, [])
            for idx, b in enumerate(b_list):
                if isinstance(b, dict):
                    entry = dict(b)
                    entry.setdefault("function", fn_name)
                    expanded.append(entry)
                elif idx < len(offsets):
                    expanded.append({"function": fn_name, "offset": offsets[idx], "bound": int(b)})
        raw_loops = expanded

    fn_loops = function.get("loops", [])
    if isinstance(fn_loops, (int, float)):
        fn_loops = [int(fn_loops)]
    expanded_fn_loops = []
    fn_offsets = (discovered_loops or {}).get(function["name"], [])
    auto_idx = 0
    for item in fn_loops:
        if isinstance(item, dict) and "offset" in item:
            entry = dict(item)
            entry.setdefault("function", function["name"])
            expanded_fn_loops.append(entry)
        else:
            bound_val = item["bound"] if isinstance(item, dict) else int(item)
            if auto_idx < len(fn_offsets):
                expanded_fn_loops.append({
                    "function": function["name"],
                    "offset": fn_offsets[auto_idx],
                    "bound": int(bound_val),
                })
            auto_idx += 1

    for loop in raw_loops + expanded_fn_loops:
        if "offset" in loop:
            key = (loop["function"], int(str(loop["offset"]), 16))
            loops[key] = loop
    return list(loops.values())


def parse_unbounded_loops(output: str) -> dict:
    discovered = {}
    for match in UNBOUNDED_LOOP_RE.finditer(output):
        fn_name = match.group("fn")
        offset = match.group("offset")
        offsets = discovered.setdefault(fn_name, [])
        if offset not in offsets:
            offsets.append(offset)
    return discovered


def write_flow_facts(path: Path, config: dict, function: dict, elf: Path, discovered_loops: dict = None) -> None:
    lines = []
    checksum = config.get("elf_checksum")
    if checksum:
        lines.append(f'checksum "{elf.name}" {checksum};')
    flow_facts = config.get("flow_facts", {})
    for multicall in flow_facts.get("multicalls", []):
        targets = ", ".join(f'"{target}"' for target in multicall["targets"])
        if multicall.get("reason"):
            lines.append(f'// {multicall["reason"]}')
        lines.append(f'multicall "{multicall["function"]}" + {multicall["offset"]} to {targets};')
    for loop in merged_loops(config, function, discovered_loops):
        if loop.get("reason"):
            lines.append(f'// {loop["reason"]}')
        lines.append(f'loop "{loop["function"]}" + {loop["offset"]} {int(loop["bound"])} ;')
    instrumentation = config.get("instrumentation", {})
    if not instrumentation.get("include_profiler_hooks", False):
        hooks = instrumentation.get("hooks", ["__cyg_profile_func_enter", "__cyg_profile_func_exit"])
        for hook in hooks:
            lines.append(f'nocall "{hook}";')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def bank_xml(bank: dict) -> str:
    writable = "true" if bank.get("writable", True) else "false"
    return (
        "\t\t<bank>"
        f"<name>{bank['name']}</name>"
        f"<address>{bank['address']}</address>"
        f"<size>{bank['size']}</size>"
        f"<type>{bank.get('type', 'SPM')}</type>"
        f"<latency>{int(bank['latency'])}</latency>"
        f"<writable>{writable}</writable>"
        "<cached>false</cached>"
        "</bank>"
    )


def write_generic_hardware(work_dir: Path, generic: dict) -> tuple:
    pipeline = generic["pipeline"]
    pipeline_path = work_dir / "pipeline.xml"
    pipeline_path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<processor class="otawa::hard::Processor">
\t<arch>arm</arch>
\t<model>{pipeline.get('model', 'cortex-m0plus')}</model>
\t<builder>OTAWA</builder>
\t<stages>
\t\t<stage id="FE"><name>FE</name><width>1</width><type>FETCH</type></stage>
\t\t<stage id="EX">
\t\t\t<name>EX</name><type>EXEC</type><width>1</width><ordered>true</ordered>
\t\t\t<fus>
\t\t\t\t<fu id="MEM"><name>MEM</name><width>1</width><latency>{int(pipeline['mem_latency'])}</latency><mem>true</mem></fu>
\t\t\t\t<fu id="ALU"><name>ALU</name><width>1</width><latency>{int(pipeline['alu_latency'])}</latency></fu>
\t\t\t</fus>
\t\t\t<dispatch>
\t\t\t\t<inst><type>IS_LOAD</type><fu ref="MEM"/></inst>
\t\t\t\t<inst><type>IS_STORE</type><fu ref="MEM"/></inst>
\t\t\t\t<inst><type/><fu ref="ALU"/></inst>
\t\t\t</dispatch>
\t\t</stage>
\t\t<stage id="CM"><name>CM</name><type>COMMIT</type><width>1</width><latency>0</latency></stage>
\t</stages>
\t<queues>
\t\t<queue><name>FQ</name><size>{int(pipeline.get('fetch_queue_size', 1))}</size><input ref="FE"/><output ref="EX"/></queue>
\t</queues>
</processor>
""", encoding="utf-8")
    memory_path = work_dir / "memory.xml"
    banks = "\n".join(bank_xml(bank) for bank in generic["memory_banks"])
    memory_path.write_text(f'<?xml version="1.0"?>\n<memory>\n\t<banks>\n{banks}\n\t</banks>\n</memory>\n',
                           encoding="utf-8")
    return pipeline_path, memory_path


def write_script(work_dir: Path, timing: dict, model: str) -> Path:
    scripts_dir = Path(os.environ["OTAWA_INSTALL"]) / "share" / "Otawa" / "scripts"
    text = (scripts_dir / f"{model}.osx").read_text(encoding="iso-8859-1")
    text = text.replace("$ORIGIN", str(scripts_dir))
    if timing.get("reduce_irreducible_loops", True):
        reduce_step = '<step require="otawa::REDUCED_LOOPS_FEATURE"/>\n\t'
        anchor = ('<step processor="otawa::trivial::BlockTime">' if model == "trivial"
                  else '<step require="otawa::etime::EDGE_TIME_FEATURE"/>')
        if anchor not in text:
            raise RuntimeError(f"cannot insert loop reduction into {model}.osx")
        text = text.replace(anchor, reduce_step + anchor, 1)
    script_path = work_dir / f"{model}.osx"
    script_path.write_text(text, encoding="iso-8859-1")
    return script_path


def build_m0plus_plugin(work_dir: Path) -> Path:
    """Compiles the Cortex-M0+ plugin into work/.otawa/proc/otawa (OTAWA searches $PWD/.otawa/proc)."""
    source_dir = MODELS_DIR / "m0plus"
    plugin_dir = work_dir / ".otawa" / "proc" / "otawa"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "m0plus.cpp"
    library = plugin_dir / "m0plus.so"
    if (not library.is_file()) or (library.stat().st_mtime < source.stat().st_mtime):
        flags = subprocess.run(["otawa-config", "--cflags"], check=True, capture_output=True, text=True).stdout.split()
        libs = subprocess.run(["otawa-config", "--libs"], check=True, capture_output=True, text=True).stdout.split()
        rpath = f"-Wl,-rpath,{Path(os.environ['OTAWA_INSTALL']) / 'lib'}"
        command = (["g++", "-std=c++11", "-O2", "-Wall", "-Wno-c++20-compat", "-fPIC", "-shared"]
                   + flags + [str(source), "-o", str(library)] + libs + [rpath])
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"m0plus plugin build failed:\n{result.stderr}")
        print(f"built {library}")
    shutil.copyfile(source_dir / "m0plus.eld", plugin_dir / "m0plus.eld")
    return source_dir / "m0plus.osx"


def owcet_args(config: dict, work_dir: Path, model: str) -> list:
    timing = config.get("timing", {})
    if model == "m0plus":
        m0plus = timing.get("m0plus", {})
        reduce = 1 if timing.get("reduce_irreducible_loops", True) else 0
        return ["-s", str(build_m0plus_plugin(work_dir)),
                "-p", f"reduce={reduce}",
                "-p", f"mul_time={int(m0plus.get('mul_cycles', 1))}",
                "-p", f"mem_wait={int(m0plus.get('mem_wait_states', 0))}",
                "-p", f"taken_penalty={int(m0plus.get('taken_branch_penalty', 1))}"]
    args = ["-s", str(write_script(work_dir, timing, model))]
    if model == "trivial":
        trivial = timing.get("trivial", {})
        args += ["-p", f"stages={int(trivial.get('instruction_cycles', 2))}"]
    elif model == "generic":
        pipeline_path, memory_path = write_generic_hardware(work_dir, timing["generic"])
        args += ["-p", f"pipeline={pipeline_path}", "-p", f"memory={memory_path}"]
    else:
        raise ValueError(f"unsupported timing model '{model}'")
    return args


def run_owcet(elf: Path, function: str, base_args: list, ff_path: Path, log_path: Path, work_dir: Path):
    command = ["owcet"] + base_args + ["-f", str(ff_path), str(elf), function]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=OWCET_TIMEOUT_S,
                                cwd=str(work_dir))
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        output = f"owcet timed out after {OWCET_TIMEOUT_S} s\n"
    log_path.write_text(" ".join(command) + "\n\n" + output, encoding="utf-8")
    for match in WCET_RE.finditer(output):
        if match.group("fn") == function:
            return int(match.group("cycles")), "", output
    errors = [line for line in output.splitlines() if "ERROR" in line]
    return None, (errors[0] if errors else "no WCET in owcet output"), output


def cycles_to_us(cycles: int, clock_hz: int) -> str:
    return f"{(cycles * 1000000.0) / clock_hz:.3f}"


def find_default_elf(base: Path) -> Path:
    build_debug = (base.parent / "build" / "Debug").resolve()
    if build_debug.is_dir():
        elfs = sorted(build_debug.glob("*.elf"))
        if elfs:
            return elfs[0]
    return base / "firmware.elf"


def normalize_function(fn) -> dict:
    return {"name": fn} if isinstance(fn, str) else fn


def autodetect_model(elf: Path) -> str:
    try:
        out = subprocess.check_output(["readelf", "-A", str(elf)], text=True)
        if "6S-M" in out or "Cortex-M0" in out:
            return "m0plus"
    except Exception:
        pass
    return "m0plus"


def autodetect_clock_hz(base: Path) -> int:
    for csv_path in sorted(base.glob("*.csv")) + sorted(base.parent.glob("*.csv")):
        if csv_path.name.startswith("wcet_"):
            continue
        try:
            with csv_path.open("r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    avg_cyc = float(row.get("avg_cycles") or 0)
                    avg_us = float(row.get("avg_us") or 0)
                    if avg_cyc > 0 and avg_us > 0 and int(row.get("calls") or 0) > 1:
                        mhz = round((avg_cyc / avg_us))
                        if mhz > 0:
                            return int(mhz * 1_000_000)
        except Exception:
            pass
    return 16000000


def autodetect_instrumented_functions(elf: Path) -> list:
    sym_out = subprocess.check_output(["readelf", "-sW", str(elf)], text=True)
    funcs = []
    enter_addr = None
    for line in sym_out.splitlines():
        f = line.split()
        if len(f) >= 8 and f[3] == "FUNC":
            addr = int(f[1], 16) & ~1
            size = int(f[2], 16) if f[2].startswith("0x") else int(f[2], 10)
            name = f[7]
            if name == "__cyg_profile_func_enter":
                enter_addr = addr
            elif size > 0 and not name.startswith("__"):
                funcs.append((addr, size, name))
    if enter_addr is None:
        return []

    sec_out = subprocess.check_output(["readelf", "-SW", str(elf)], text=True)
    sections = []
    for line in sec_out.splitlines():
        m = re.search(r"\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)", line)
        if m:
            vma, off, sz = int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16)
            if vma > 0 and sz > 0:
                sections.append((vma, off, sz))

    data = elf.read_bytes()
    instrumented = []
    for f_addr, f_size, f_name in sorted(funcs):
        for vma, off, sz in sections:
            if vma <= f_addr < vma + sz:
                file_off = off + (f_addr - vma)
                for pos in range(0, min(f_size, 32) - 2, 2):
                    hw1, hw2 = struct.unpack_from("<HH", data, file_off + pos)
                    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
                        s = (hw1 >> 10) & 1
                        imm10 = hw1 & 0x3FF
                        j1 = (hw2 >> 13) & 1
                        j2 = (hw2 >> 11) & 1
                        imm11 = hw2 & 0x7FF
                        i1 = (~(j1 ^ s)) & 1
                        i2 = (~(j2 ^ s)) & 1
                        imm32 = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
                        if s:
                            imm32 -= (1 << 25)
                        target = (f_addr + pos + 4 + imm32) & 0xFFFFFFFF
                        if target == enter_addr and f_name not in instrumented:
                            instrumented.append(f_name)
                            break
    return instrumented


def to_local_source_path(loc: str, project_root: Path) -> Path:
    path_part = loc.rsplit(":", 1)[0]
    m = re.match(r"^([A-Za-z]):/(.*)$", path_part)
    if m and Path("/mnt").is_dir():
        wsl_p = Path(f"/mnt/{m.group(1).lower()}/{m.group(2)}")
        if wsl_p.is_file():
            return wsl_p
    p = Path(path_part)
    if p.is_file():
        return p
    candidates = list(project_root.rglob(p.name))
    return candidates[0] if candidates else p


def split_top_level_args(args_str: str) -> list:
    parts = []
    cur = []
    depth = 0
    for ch in args_str:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def resolve_call_arg_bound(project_root: Path, fn_name: str, param_idx: int):
    c_files = [p for p in project_root.rglob("*.[ch]") if "build" not in p.parts]
    call_re = re.compile(rf"\b{re.escape(fn_name)}\s*\(")
    best = None
    for cf in c_files:
        try:
            text = cf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if fn_name not in text:
            continue
        for m in call_re.finditer(text):
            start = m.end()
            depth = 1
            pos = start
            while pos < len(text) and depth > 0:
                if text[pos] == "(":
                    depth += 1
                elif text[pos] == ")":
                    depth -= 1
                pos += 1
            args_str = text[start: pos - 1]
            # Skip function declaration/definition (contains type keywords)
            if re.search(r"\b(uint\d+_t|int\d+_t|size_t|const|void|char|int|unsigned)\b", args_str):
                continue
            args = split_top_level_args(args_str)
            if param_idx >= len(args):
                continue
            arg = args[param_idx]
            num_m = re.match(r"^(\d+)[uUlL]*$", arg)
            if num_m:
                val = int(num_m.group(1))
                best = max(best or 0, val)
                continue
            sz_m = re.match(r"^sizeof\s*\(\s*([A-Za-z_]\w*)\s*\)$", arg)
            if sz_m:
                arr_name = sz_m.group(1)
                arr_m = re.search(rf"\b{re.escape(arr_name)}\s*\[\s*(\d+)\s*\]", text)
                if arr_m:
                    val = int(arr_m.group(1))
                    best = max(best or 0, val)
    return best


def infer_loop_bound_from_source(addr2line: str, elf: Path, fn_name: str, fn_addr: int, offset_hex: str, project_root: Path):
    loop_addr = (fn_addr & ~1) + int(offset_hex, 16)
    loc = source_location(addr2line, elf, loop_addr)
    if loc == "??:?" or ":" not in loc:
        return None
    src_file = to_local_source_path(loc, project_root)
    try:
        line_no = int(loc.rsplit(":", 1)[1])
        lines = src_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except (ValueError, OSError):
        return None
    if not (1 <= line_no <= len(lines)):
        return None

    window = "\n".join(lines[max(0, line_no - 2): min(len(lines), line_no + 2)])
    # 1. Explicit annotation comment e.g. // wcet: 25 or /* WCET_LOOP: 25 */
    ann_m = re.search(r"wcet(?:_loop)?\s*[:=]\s*(\d+)", window, re.IGNORECASE)
    if ann_m:
        return int(ann_m.group(1))

    line_text = lines[line_no - 1]
    # 2. Constant upper bound in for/while condition e.g. bit < 8U or i <= 15
    lt_m = re.search(r"<\s*(\d+)[uUlL]*\b", line_text)
    if lt_m:
        return int(lt_m.group(1))
    le_m = re.search(r"<=\s*(\d+)[uUlL]*\b", line_text)
    if le_m:
        return int(le_m.group(1))

    # 3. Parameter-driven loop bound e.g. while (count > 0U) or for (...; i < length; ...)
    var_m = re.search(r"(?:<\s*([A-Za-z_]\w*)|while\s*\(\s*([A-Za-z_]\w*)\s*>\s*0)", line_text)
    if var_m:
        var_name = var_m.group(1) or var_m.group(2)
        # Find parameter index in function signature above line_no
        header_text = "\n".join(lines[max(0, line_no - 30): line_no])
        sig_m = re.search(rf"\b{re.escape(fn_name)}\s*\(([^)]*)\)", header_text)
        if sig_m:
            params = [p.strip() for p in sig_m.group(1).split(",") if p.strip()]
            for idx, p in enumerate(params):
                if re.search(rf"\b{re.escape(var_name)}$", p):
                    return resolve_call_arg_bound(project_root, fn_name, idx)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="project wcet_targets.json")
    parser.add_argument("--elf", type=Path, default=None, help="overrides $ELF and the JSON 'elf'")
    parser.add_argument("--output", type=Path, default=None, help="overrides the JSON 'output_csv'")
    parser.add_argument("--model", choices=["m0plus", "trivial", "generic"], default=None,
                        help="overrides timing.model")
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = load_config(config_path)
    base = config_path.parent

    if args.elf is not None:
        elf = args.elf.resolve()
    elif os.environ.get("ELF"):
        elf = Path(os.environ["ELF"]).resolve()
    elif config.get("elf"):
        elf = resolve_path(base, config["elf"])
    else:
        elf = find_default_elf(base)
    if not elf.is_file():
        print(f"error: ELF not found: {elf}", file=sys.stderr)
        return 2
    if "OTAWA_INSTALL" not in os.environ or shutil.which("owcet") is None:
        print("error: OTAWA environment not loaded (source otawa-env.sh)", file=sys.stderr)
        return 2

    output = args.output.resolve() if args.output else resolve_path(base, config.get("output_csv", "1w.csv"))
    work_dir = resolve_path(base, config.get("work_dir", "work"))
    work_dir.mkdir(parents=True, exist_ok=True)

    timing = config.get("timing", {})
    model = args.model or timing.get("model") or autodetect_model(elf)
    clock_hz = int(timing.get("cpu_clock_hz") or autodetect_clock_hz(base))
    config.setdefault("timing", {})
    config["timing"].setdefault("cpu_clock_hz", clock_hz)
    config["timing"].setdefault("model", model)

    base_args = owcet_args(config, work_dir, model)
    symbols = read_function_symbols(elf)
    addr2line = find_addr2line()

    config_updated = not config_path.is_file()
    if not config.get("functions"):
        detected_fns = autodetect_instrumented_functions(elf)
        if detected_fns:
            config["functions"] = detected_fns
            config_updated = True
            print(f"auto-detected {len(detected_fns)} instrumented functions from ELF")

    if "loops" in config and "flow_facts" not in config:
        config["flow_facts"] = {"loops": config["loops"]}
    elif "loops" in config and isinstance(config.get("flow_facts"), dict) and "loops" not in config["flow_facts"]:
        config["flow_facts"]["loops"] = config["loops"]

    print(f"ELF   : {elf}")
    print(f"model : {model} ({' '.join(base_args)})")
    print(f"clock : {clock_hz} Hz")

    rows = []
    failures = 0
    discovered_loops = {}
    project_root = base.parent.resolve()
    raw_functions = config.get("functions", []) + config.get("interference_functions", [])
    for fn_item in raw_functions:
        function = normalize_function(fn_item)
        name = function["name"]
        address = symbols.get(name)
        if address is None:
            print(f"  {name:40s} MISSING symbol")
            failures += 1
            continue
        ff_path = work_dir / f"{name}.ff"
        log_path = work_dir / f"{name}.log"
        write_flow_facts(ff_path, config, function, elf, discovered_loops)
        wcet, error, output_log = run_owcet(elf, name, base_args, ff_path, log_path, work_dir)
        if wcet is None:
            new_loops = parse_unbounded_loops(output_log)
            if new_loops:
                for fn_k, offs in new_loops.items():
                    cur = discovered_loops.setdefault(fn_k, [])
                    for o in offs:
                        if o not in cur:
                            cur.append(o)
                    # If no loop bounds configured for fn_k, try to infer them from C source
                    loops_cfg = config.setdefault("flow_facts", {}).setdefault("loops", {})
                    has_cfg = (fn_k in loops_cfg) if isinstance(loops_cfg, dict) else any(
                        isinstance(x, dict) and x.get("function") == fn_k for x in loops_cfg
                    )
                    if not has_cfg and fn_k in symbols:
                        inferred = []
                        for o in cur:
                            b = infer_loop_bound_from_source(addr2line, elf, fn_k, symbols[fn_k], o, project_root)
                            if b is not None:
                                inferred.append(b)
                        if len(inferred) == len(cur) and inferred:
                            val = inferred[0] if len(inferred) == 1 else inferred
                            if isinstance(loops_cfg, dict):
                                loops_cfg[fn_k] = val
                            config.setdefault("loops", {})[fn_k] = val
                            config_updated = True
                            print(f"  auto-detected loop bound(s) for {fn_k}: {val}")
                write_flow_facts(ff_path, config, function, elf, discovered_loops)
                wcet, error, _ = run_owcet(elf, name, base_args, ff_path, log_path, work_dir)
        if wcet is None:
            print(f"  {name:40s} FAILED  {error}")
            failures += 1
            continue
        print(f"  {name:40s} {wcet:10d} cycles")
        us = cycles_to_us(wcet, clock_hz)
        rows.append([name, f"0x{address:08x}", 1, wcet, wcet, wcet, f"{float(wcet):.1f}",
                     us, us, us, source_location(addr2line, elf, address)])

    if config_updated:
        saved_cfg = {
            "schema_version": SCHEMA_VERSION,
            "timing": {
                "cpu_clock_hz": clock_hz,
                "model": model,
            },
        }
        loops_out = config.get("loops") or (
            config.get("flow_facts", {}).get("loops")
            if isinstance(config.get("flow_facts", {}).get("loops"), dict)
            else {}
        )
        if loops_out:
            saved_cfg["loops"] = loops_out
        saved_cfg["functions"] = config.get("functions", [])
        config_path.write_text(json.dumps(saved_cfg, indent=4) + "\n", encoding="utf-8")
        print(f"generated {config_path}")

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    print(f"wrote {output} ({len(rows)} functions, {failures} failures)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
