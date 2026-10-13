/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body - Profiler Demo & Ground-Truth Benchmark
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "dma.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "benchmark.h"
#include "profiler.h"
#include "profiler_port.h"
#include "profiler_transport.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef struct BenchExpectedItem {
    const char* name;
    const void* fn_addr;
    uint32_t    expected_cycles;
    uint32_t    tolerance;
} BenchExpectedItem;
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */

static const BenchExpectedItem s_bench_items[] = {
    { "bench_exact_100_cycles",  (const void*)bench_exact_100_cycles,  103U,  5U },
    { "bench_exact_500_cycles",  (const void*)bench_exact_500_cycles,  503U,  5U },
    { "bench_exact_1000_cycles", (const void*)bench_exact_1000_cycles, 1003U, 5U },
    { "bench_unrolled_poly32",   (const void*)bench_unrolled_poly32,   104U,  5U },
    { "bench_matrix_mul_2x2",    (const void*)bench_matrix_mul_2x2,    148U,  5U },
    { "bench_fixed_loop_sum",    (const void*)bench_fixed_loop_sum,    455U,  5U },
    { "bench_crc8_fixed_block",  (const void*)bench_crc8_fixed_block,  4819U, 20U }
};
#define BENCH_ITEM_COUNT (sizeof(s_bench_items) / sizeof(s_bench_items[0]))

static volatile bool g_diag_all_pass = true;

void* const volatile g_bench_symbols[] = {
    (void*)bench_call_overhead,
    (void*)bench_matrix_overhead
};
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static bool run_diagnostics(void);
static void update_led_indicator(bool all_pass);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* Standalone diagnostic measurement: does NOT need profiler dump pipeline */
static bool run_diagnostics(void)
{
    bool all_pass = true;
    static const int32_t mat_a[4] = { 1, 2, 3, 4 };
    static const int32_t mat_b[4] = { 5, 6, 7, 8 };
    int32_t mat_out[4] = { 0, 0, 0, 0 };
    static const uint8_t crc_data[16] = {
        0x01U, 0x02U, 0x03U, 0x04U, 0x05U, 0x06U, 0x07U, 0x08U,
        0x09U, 0x0AU, 0x0BU, 0x0CU, 0x0DU, 0x0EU, 0x0FU, 0x10U
    };

    uint32_t baseline;
    uint32_t measured[BENCH_ITEM_COUNT];

    __disable_irq();

    /* Calibrate measurement baseline overhead (counter reset + read) */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    baseline = profiler_port_ticks();

    /* 1. bench_exact_100_cycles */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    (void)bench_exact_100_cycles();
    measured[0] = profiler_port_ticks() - baseline;

    /* 2. bench_exact_500_cycles */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    (void)bench_exact_500_cycles();
    measured[1] = profiler_port_ticks() - baseline;

    /* 3. bench_exact_1000_cycles */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    (void)bench_exact_1000_cycles();
    measured[2] = profiler_port_ticks() - baseline;

    /* 4. bench_unrolled_poly32 */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    (void)bench_unrolled_poly32(3U);
    measured[3] = profiler_port_ticks() - baseline;

    /* 5. bench_matrix_mul_2x2 */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    bench_matrix_mul_2x2(mat_a, mat_b, mat_out);
    measured[4] = profiler_port_ticks() - baseline;

    /* 6. bench_fixed_loop_sum */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    (void)bench_fixed_loop_sum(25U);
    measured[5] = profiler_port_ticks() - baseline;

    /* 7. bench_crc8_fixed_block */
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    (void)bench_crc8_fixed_block(crc_data, sizeof(crc_data));
    measured[6] = profiler_port_ticks() - baseline;

    __enable_irq();

    (void)mat_out;

    for (size_t i = 0U; i < BENCH_ITEM_COUNT; i++) {
        /* In standalone mode, measured duration includes caller BL (3 cyc) + baseline subtraction */
        if ((measured[i] < (s_bench_items[i].expected_cycles - s_bench_items[i].tolerance)) ||
            (measured[i] > (s_bench_items[i].expected_cycles + s_bench_items[i].tolerance))) {
            all_pass = false;
        }
    }

    return all_pass;
}

static void update_led_indicator(bool all_pass)
{
    PROFILER_FUNCTION();
    static uint32_t s_last_toggle = 0U;
    static uint8_t s_step = 0U;
    uint32_t now = HAL_GetTick();

    if (all_pass) {
        /* PASS: Calm, periodic double-pulse heartbeat every 1000ms
         * Step 0: ON  (80ms)
         * Step 1: OFF (120ms)
         * Step 2: ON  (80ms)
         * Step 3: OFF (720ms) */
        uint32_t step_delays[4] = { 80U, 120U, 80U, 720U };
        if ((now - s_last_toggle) >= step_delays[s_step % 4U]) {
            s_last_toggle = now;
            s_step = (uint8_t)((s_step + 1U) % 4U);
            if ((s_step == 1U) || (s_step == 3U)) {
                HAL_GPIO_WritePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin, GPIO_PIN_SET);
            } else {
                HAL_GPIO_WritePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin, GPIO_PIN_RESET);
            }
        }
    } else {
        /* FAIL: Rapid urgent warning strobe (10 Hz, 50ms toggle) */
        if ((now - s_last_toggle) >= 50U) {
            s_last_toggle = now;
            HAL_GPIO_TogglePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin);
        }
    }
}

static void run_benchmark_suite(void)
{
    PROFILER_FUNCTION();

    static const int32_t mat_a[4] = { 1, 2, 3, 4 };
    static const int32_t mat_b[4] = { 5, 6, 7, 8 };
    int32_t mat_out[4] = { 0, 0, 0, 0 };
    static const uint8_t crc_data[16] = {
        0x01U, 0x02U, 0x03U, 0x04U, 0x05U, 0x06U, 0x07U, 0x08U,
        0x09U, 0x0AU, 0x0BU, 0x0CU, 0x0DU, 0x0EU, 0x0FU, 0x10U
    };

    __disable_irq();

    PROFILER_SCOPE(bench_exact_100_cycles, bench_exact_100_cycles());
    PROFILER_SCOPE(bench_exact_500_cycles, bench_exact_500_cycles());
    PROFILER_SCOPE(bench_exact_1000_cycles, bench_exact_1000_cycles());
    PROFILER_SCOPE(bench_unrolled_poly32, bench_unrolled_poly32(3U));
    PROFILER_SCOPE(bench_matrix_mul_2x2, bench_matrix_mul_2x2(mat_a, mat_b, mat_out));
    PROFILER_SCOPE(bench_fixed_loop_sum, bench_fixed_loop_sum(25U));
    PROFILER_SCOPE(bench_crc8_fixed_block, bench_crc8_fixed_block(crc_data, sizeof(crc_data)));
    __enable_irq();

    (void)mat_out;
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_USART2_UART_Init();
  MX_TIM1_Init();
  MX_TIM3_Init();
  /* USER CODE BEGIN 2 */
  if (!profiler_init(HAL_RCC_GetPCLK1Freq())) {
      Error_Handler();
  }
  if (profiler_transport_init(&huart2) != PROFILER_TRANSPORT_STATUS_OK) {
      Error_Handler();
  }

  (void)bench_call_overhead();
  (void)bench_matrix_overhead();

  profiler_start();
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    (void)profiler_transport_process();

    /* Periodic standalone diagnostic test (independent of profiler) */
    static uint32_t s_last_diag = 0U;
    uint32_t now = HAL_GetTick();
    if ((now - s_last_diag) >= 200U) {
        s_last_diag = now;
        g_diag_all_pass = run_diagnostics();
    }

    /* Update intuitive status LED pattern */
    update_led_indicator(g_diag_all_pass);

    /* Benchmark workload execution under profiling */
    run_benchmark_suite();

    HAL_Delay(5);
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSIDiv = RCC_HSI_DIV1;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM6 or TIM1 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base, and calls the
  * profiler overflow handler for TIM1.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM6)
  {
    HAL_IncTick();
  }
  else if (htim->Instance == TIM1)
  {
    profiler_port_16bit_it_overflow_isr();
  }
  /* USER CODE BEGIN Callback 1 */

  /* USER CODE END Callback 1 */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
