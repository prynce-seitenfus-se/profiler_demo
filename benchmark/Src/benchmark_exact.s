/*
 * Category A: Mathematically deterministic constant-cycle assembly routines
 * Target: ARM Cortex-M0+ (ARMv6-M) with zero wait-state flash at 16 MHz.
 *
 * Per ARM Cortex-M0+ Technical Reference Manual (DDI 0484C):
 *   MOVS Rd, #imm : 1 cycle
 *   NOP           : 1 cycle
 *   BX lr         : 2 cycles
 */

    .syntax unified
    .cpu cortex-m0plus
    .thumb

    .section .text.bench_exact_100_cycles, "ax"
    .align 2
    .global bench_exact_100_cycles
    .type bench_exact_100_cycles, %function
bench_exact_100_cycles:
    movs r0, #0
    .rept 97
    nop
    .endr
    bx lr
    .size bench_exact_100_cycles, . - bench_exact_100_cycles

    .section .text.bench_exact_500_cycles, "ax"
    .align 2
    .global bench_exact_500_cycles
    .type bench_exact_500_cycles, %function
bench_exact_500_cycles:
    movs r0, #0
    .rept 497
    nop
    .endr
    bx lr
    .size bench_exact_500_cycles, . - bench_exact_500_cycles

    .section .text.bench_exact_1000_cycles, "ax"
    .align 2
    .global bench_exact_1000_cycles
    .type bench_exact_1000_cycles, %function
bench_exact_1000_cycles:
    movs r0, #0
    .rept 997
    nop
    .endr
    bx lr
    .size bench_exact_1000_cycles, . - bench_exact_1000_cycles

    .section .text.bench_call_overhead, "ax"
    .align 2
    .global bench_call_overhead
    .type bench_call_overhead, %function
bench_call_overhead:
    movs r0, #0
    nop
    bx lr
    .size bench_call_overhead, . - bench_call_overhead

    .section .text.bench_matrix_overhead, "ax"
    .align 2
    .global bench_matrix_overhead
    .type bench_matrix_overhead, %function
bench_matrix_overhead:
    movs r0, #0
    .rept 6
    nop
    .endr
    bx lr
    .size bench_matrix_overhead, . - bench_matrix_overhead


