# Profiler & Static WCET Workflow

Generic workflow for building instrumented firmware, interacting with the `profiler` module
over a serial console, and comparing measured execution times against static WCET estimates.

## Run and interact

Build the firmware and program the board:

```powershell
.\build.bat Debug -i          # Debug with compiler instrumentation (-finstrument-functions)
.\burn.ps1 .\build\Debug\<project>.elf
```

Connect to the board's serial COM port:

```powershell
.\serial-console.ps1 COM<x>
```

The console defaults to 115200 8N1 and supports `help`, `comm`, `clear`, and `exit`. Send these target commands:

| Command | Result |
|---|---|
| `prof-help` | Show profiler commands |
| `prof-status` | Show profiler state and timer frequency |
| `prof-top` | Print a sorted, human-readable cycle statistics table |
| `prof-dump` | Stream a binary PROF-BIN v4 capture |
| `prof-reset` | Clear metrics and restart measurement |

Use `prof-top` for readable output. Inside the serial console, save a binary dump with:

```text
prof-dump > capture.bin
```

Paths are relative to the current directory; quote paths containing spaces.
The console verifies the packet's CRC32 before saving and refuses to overwrite
existing files. Plain `prof-dump` and text logging do not save a binary capture.

## Static WCET analysis

The `wcet` directory contains the analysis target configuration (`wcet\wcet_targets.json`),
timing assumptions, flow facts, and comparison CSVs. Run `.\wcet\run_wcet.ps1` to generate
the static WCET CSV:

```powershell
.\wcet\run_wcet.ps1 -Elf .\build\Debug\<project>.elf
```

## `run_wcet.ps1` options

```powershell
# Specify a custom output WCET CSV path
.\wcet\run_wcet.ps1 -Elf .\build\Debug\<project>.elf -Out .\wcet\<wcet>.csv

# Generate static WCET CSV and compare against a target measurement CSV
.\wcet\run_wcet.ps1 -Elf .\build\Debug\<project>.elf -Compare .\<measured>.csv

# Compare two existing CSV files directly (without regenerating WCET)
.\wcet\run_wcet.ps1 -Compare .\<measured>.csv .\wcet\<wcet>.csv

# Using short parameter aliases
.\wcet\run_wcet.ps1 -e .\build\Debug\<project>.elf -o .\wcet\<wcet>.csv -cmp .\<measured>.csv
.\wcet\run_wcet.ps1 -cmp .\<measured>.csv .\wcet\<wcet>.csv -o .\wcet\<comparison>.csv
```

| Parameter | Aliases | Required | Description |
|---|---|---|---|
| `-Elf <path.elf>` | `-elf`, `-e` (or positional) | Required unless `-Compare` is used | Path to the built firmware `.elf` binary |
| `-Out <path.csv>` | `-out`, `-o`, `-Output` | No | Output path for the static WCET CSV (or comparison CSV when running `-Compare` without `-Elf`) |
| `-Compare <csv1> [<csv2>]` | `-compare`, `-cmp`, `-c` | No | Compare `<measured>.csv` against the generated WCET CSV (with `-Elf`), or pass two CSVs `<measured>.csv <wcet>.csv` without `-Elf` to only run the comparison |