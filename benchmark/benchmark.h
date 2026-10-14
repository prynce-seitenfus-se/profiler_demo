#ifndef BENCHMARK_H
#define BENCHMARK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Category A: Millisecond delay utility.
 *
 * Delays execution for the specified number of milliseconds.
 *
 * @param[in] ms Number of milliseconds to delay.
 */
void bench_delay_ms(uint32_t ms);

/**
 * @brief Runs the instrumented, file-local LED heartbeat benchmark.
 */
void bench_update_user_led(void);

/**
 * @brief Category A: Cortex-M0+ workload with an exact 100-cycle assembly body.
 *
 * The instrumentable C wrapper calls the assembly body. Profiler measurements include
 * the wrapper and assembly-call overhead; only the assembly body is exactly 100 cycles.
 *
 */
void bench_exact_100_cycles(void);

/**
 * @brief Category A: Cortex-M0+ workload with an exact 500-cycle assembly body.
 *
 * The instrumentable C wrapper calls the assembly body. Profiler measurements include
 * the wrapper and assembly-call overhead; only the assembly body is exactly 500 cycles.
 *
 */
void bench_exact_500_cycles(void);

/**
 * @brief Category A: Cortex-M0+ workload with an exact 1000-cycle assembly body.
 *
 * The instrumentable C wrapper calls the assembly body. Profiler measurements include
 * the wrapper and assembly-call overhead; only the assembly body is exactly 1000 cycles.
 *
 */
void bench_exact_1000_cycles(void);

/**
 * @brief Category B: Unrolled 10th-order polynomial evaluation using Horner's method.
 *
 * Computes P(x) = sum_{i=0}^{10} c_i * x^i with fixed coefficients.
 * Fully unrolled to eliminate internal branches and conditional execution jitter.
 *
 * @param[in] x Polynomial input value.
 * @return Evaluated polynomial result.
 */
uint32_t bench_unrolled_poly32(uint32_t x);

/**
 * @brief Category B: 2x2 integer matrix multiplication in SRAM.
 *
 * Computes out = a * b for 2x2 integer matrices (8 multiplications, 4 additions).
 * Fully unrolled SRAM load/store pipeline.
 *
 * @param[in]  a   First 2x2 matrix (row-major: a00, a01, a10, a11).
 * @param[in]  b   Second 2x2 matrix (row-major: b00, b01, b10, b11).
 * @param[out] out Result 2x2 matrix.
 */
void bench_matrix_mul_2x2(const int32_t a[4], const int32_t b[4], int32_t out[4]);

/**
 * @brief Category C: Bounded loop summation down from count iterations.
 *
 * Computes sum_{i=1}^{count} i in a bounded loop.
 *
 * @param[in] count Iteration count bound (normally 25U).
 * @return Accumulated sum.
 */
uint32_t bench_fixed_loop_sum(uint32_t count);

/**
 * @brief Category C: Deterministic CRC-8 calculation over a static byte block.
 *
 * Computes CRC-8 with polynomial 0x07 (SMBus / ATM HEC).
 *
 * @param[in] data   Pointer to byte buffer.
 * @param[in] length Number of bytes to process.
 * @return Computed CRC-8 checksum.
 */
uint8_t bench_crc8_fixed_block(const uint8_t* data, size_t length);

#ifdef __cplusplus
}
#endif

#endif /* BENCHMARK_H */
