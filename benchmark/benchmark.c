/* Deterministic C99 benchmark workloads for WCET validation. */

#include "benchmark.h"

#include "gpio.h"
#include "main.h"

extern void bench_exact_100_cycles_asm(void);
extern void bench_exact_500_cycles_asm(void);
extern void bench_exact_1000_cycles_asm(void);

void bench_delay_ms(uint32_t ms)
{
    HAL_Delay(ms);
}

void bench_update_user_led(void)
{
    /* Calm, periodic double-pulse heartbeat every 1000ms:
     * Step 0: ON  (80ms)
     * Step 1: OFF (120ms)
     * Step 2: ON  (80ms)
     * Step 3: OFF (720ms) */
    static uint32_t s_last_toggle = 0U;
    static uint8_t s_step = 0U;
    const uint32_t now = HAL_GetTick();
    const uint32_t step_delays[4] = { 80U, 120U, 80U, 720U };

    if ((now - s_last_toggle) >= step_delays[s_step % 4U]) {
        s_last_toggle = now;
        s_step = (uint8_t)((s_step + 1U) % 4U);
        if ((s_step == 1U) || (s_step == 3U)) {
            HAL_GPIO_WritePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin, GPIO_PIN_SET);
        } else {
            HAL_GPIO_WritePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin, GPIO_PIN_RESET);
        }
    }
}

void bench_exact_100_cycles(void)
{
    bench_exact_100_cycles_asm();
}

void bench_exact_500_cycles(void)
{
    bench_exact_500_cycles_asm();
}

void bench_exact_1000_cycles(void)
{
    bench_exact_1000_cycles_asm();
}

uint32_t bench_unrolled_poly32(uint32_t x)
{
    uint32_t acc = 11U;

    acc = (acc * x) + 10U;
    acc = (acc * x) + 9U;
    acc = (acc * x) + 8U;
    acc = (acc * x) + 7U;
    acc = (acc * x) + 6U;
    acc = (acc * x) + 5U;
    acc = (acc * x) + 4U;
    acc = (acc * x) + 3U;
    acc = (acc * x) + 2U;
    acc = (acc * x) + 1U;

    return acc;
}

void bench_matrix_mul_2x2(const int32_t a[4], const int32_t b[4], int32_t out[4])
{
    int32_t a00 = a[0];
    int32_t a01 = a[1];
    int32_t a10 = a[2];
    int32_t a11 = a[3];

    int32_t b00 = b[0];
    int32_t b01 = b[1];
    int32_t b10 = b[2];
    int32_t b11 = b[3];

    out[0] = (a00 * b00) + (a01 * b10);
    out[1] = (a00 * b01) + (a01 * b11);
    out[2] = (a10 * b00) + (a11 * b10);
    out[3] = (a10 * b01) + (a11 * b11);
}

uint32_t bench_fixed_loop_sum(uint32_t count)
{
    uint32_t sum = 0U;

    while (count > 0U) {
        sum += count;
        count--;
    }

    return sum;
}

uint8_t bench_crc8_fixed_block(const uint8_t* data, size_t length)
{
    uint8_t crc = 0x00U;

    if (data == NULL) {
        return 0U;
    }

    for (size_t i = 0U; i < length; i++) {
        crc ^= data[i];
        for (uint8_t bit = 0U; bit < 8U; bit++) {
            if ((crc & 0x80U) != 0U) {
                crc = (uint8_t)((uint8_t)(crc << 1U) ^ 0x07U);
            } else {
                crc = (uint8_t)(crc << 1U);
            }
        }
    }

    return crc;
}
