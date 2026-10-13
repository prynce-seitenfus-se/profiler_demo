# profiler_demo

STM32G070RB / NUCLEO-G070RB firmware demonstrating the `profiler` module and comparing
measured execution times with static WCET estimates. The application runs deterministic
benchmarks, reports measurements over USART2, and includes scripts for flashing, serial
interaction, and WCET analysis.

## Hardware and timing

- Target: STM32G070RBT6 on NUCLEO-G070RB.
- System clock: HSI at 16 MHz, with the PLL disabled.
- Default profiler timer: two cascaded 16-bit timers, TIM1 and TIM3, with no prescaling.
  This produces a 32-bit timestamp at 16 MHz (one timer tick per CPU cycle).
- Console and profiler transport: USART2 on PA2/PA3, 115200 baud, 8 data bits, no parity,
  one stop bit. The default CMake presets use the STM32 HAL UART DMA transport.

The timer frequency passed to `profiler_init()` in `Core/Src/main.c` is the PCLK1 rate;
it is 16 MHz with the current clock configuration.

## Build

The CMake project expects `arm-none-eabi-gcc`, CMake, and Ninja to be available. It uses
the sibling repositories `..\profiler`, `..\stream`, and `..\hashmap` by default. Configure
and build the scope-based measurement preset from this directory:

```powershell
cmake --preset Measure
cmake --build --preset Measure
```

The presets use `cmake/gcc-arm-none-eabi.cmake` for the Cortex-M0+ target. `Debug` and
`Release` select the same timer and transport but leave application probes disabled by
default. `Measure` enables explicit call-site scopes in the demo while leaving compiler
function instrumentation off.

The app's CMake options are:

| Option | Effect |
|---|---|
| `PROFILER_APP_SCOPES` | Enable explicit `PROFILER_SCOPE` and `PROFILER_FUNCTION` probes. |
| `PROFILER_APP_INSTRUMENTED` | Compile `benchmark_algo.c` with GCC function instrumentation. |

`PROFILER_APP_SCOPES` and `PROFILER_APP_INSTRUMENTED` are mutually exclusive. To configure
a compiler-instrumented build, for example:

```powershell
cmake --preset Debug -DPROFILER_APP_INSTRUMENTED=ON
cmake --build --preset Debug
```

The CMake project also accepts `PROFILER_APP_TIME_MODE`, but the current profiler core
records active cycles and marks PROF-BIN dumps as active regardless of that setting.

The demo places call-site scopes around the three exact-cycle assembly routines and the
four C benchmark routines. The LED heartbeat function is also measured when scope support
is enabled. The assembly functions in `benchmark/Src/benchmark_exact.s` provide
100-, 500-, and 1000-cycle Cortex-M0+ workloads under the stated zero-wait-state model;
the C workloads cover an unrolled polynomial, 2x2 matrix multiplication, a bounded sum,
and CRC-8 over a fixed block.

## Run and interact

Program the board with the generated ELF using STM32CubeProgrammer:

```powershell
.\burn.ps1 .\build\Measure\profiler_demo.elf
```

The script verifies the programmed image and resets the target. It expects
`STM32_Programmer_CLI.exe` at the path configured in `burn.ps1`.

Connect to the board's USART2 virtual COM port:

```powershell
.\serial-console.ps1 COM3
```

Replace `COM3` with the port assigned by Windows. The console defaults to 115200 8N1 and
supports `help`, `comm`, `clear`, and `exit`. Send these target commands:

| Command | Result |
|---|---|
| `prof-help` | Show profiler commands |
| `prof-status` | Show profiler state and timer frequency |
| `prof-top` | Print a sorted, human-readable cycle statistics table |
| `prof-dump` | Stream a binary PROF-BIN v4 capture |
| `prof-reset` | Clear metrics and restart measurement |

`prof-top` is suitable for viewing directly in the serial console. Use a binary-capable
capture tool for `prof-dump`; the PowerShell console's text logging is not suitable for
binary captures.

## Analyze captures

Use the parser in the sibling profiler repository to verify the binary CRC and resolve
addresses against the exact ELF used to build the firmware:

```powershell
python ..\profiler\parse_prof_dump.py .\capture.bin `
  --elf .\build\Measure\profiler_demo.elf --summary
```

The parser writes a detailed CSV beside the capture by default. `--output` selects a
different CSV path. An ELF is optional if function addresses are sufficient.

## Static WCET analysis

The `sewcet` directory contains the analysis target configuration, timing assumptions,
flow facts, and comparison CSVs. Run its wrapper after building the `Measure` preset:

```powershell
.\sewcet\run_wcet.ps1
```

The wrapper uses the external `sewcet` tools in `..\..\sewcet`, or the directory in
`SEWCET_DIR`. The default config points to `build\Measure\profiler_demo.elf`, writes
`sewcet\1mw.csv`, and compares estimates with the captured target data in `1m.csv`;
override the ELF or model with `-Elf` and `-Model` when needed.