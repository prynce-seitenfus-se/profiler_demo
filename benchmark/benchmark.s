/*
 * Category A: Mathematically deterministic constant-cycle assembly workloads
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

    .section .text.bench_exact_100_cycles_asm, "ax"
    .align 2
    .global bench_exact_100_cycles_asm
    .type bench_exact_100_cycles_asm, %function
bench_exact_100_cycles_asm:
    movs r0, #0
    .rept 97
    nop
    .endr
    bx lr
    .size bench_exact_100_cycles_asm, . - bench_exact_100_cycles_asm

    .section .text.bench_exact_500_cycles_asm, "ax"
    .align 2
    .global bench_exact_500_cycles_asm
    .type bench_exact_500_cycles_asm, %function
bench_exact_500_cycles_asm:
    movs r0, #0
    .rept 497
    nop
    .endr
    bx lr
    .size bench_exact_500_cycles_asm, . - bench_exact_500_cycles_asm

    .section .text.bench_exact_1000_cycles_asm, "ax"
    .align 2
    .global bench_exact_1000_cycles_asm
    .type bench_exact_1000_cycles_asm, %function
bench_exact_1000_cycles_asm:
    movs r0, #0
    .rept 997
    nop
    .endr
    bx lr
    .size bench_exact_1000_cycles_asm, . - bench_exact_1000_cycles_asm
