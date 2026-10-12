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
#include <string.h>

#include "benchmark.h"
#include "profiler.h"
#include "profiler_port.h"
#include "profiler_transport.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define PROFILER_MAP_CAPACITY     (64U)
#define PROFILER_STACK_DEPTH      (16U)
#define PROFILER_CONTEXT_CAPACITY (1U)
#define PROFILER_TX_BUFFER_SIZE   (256U)
#define PROFILER_RX_BUFFER_SIZE   (32U)
#define PROFILER_TX_TIMEOUT_MS    (1000U)
#define PROFILER_DUMP_COMMAND     "prof-dump"

#ifndef PROFILER_APP_TIME_MODE
#define PROFILER_APP_TIME_MODE    (1)
#endif

#if (PROFILER_APP_TIME_MODE == 1)
#define PROFILER_APP_TIME_MODE_VALUE PROFILER_TIME_ACTIVE
#else
#define PROFILER_APP_TIME_MODE_VALUE PROFILER_TIME_WALL
#endif
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
static HashMapEntry s_profiler_map[PROFILER_MAP_CAPACITY];
static ProfilerMetric s_profiler_metrics[PROFILER_MAP_CAPACITY];
static ProfilerStackFrame s_profiler_stack[PROFILER_CONTEXT_CAPACITY * PROFILER_STACK_DEPTH];
static ProfilerContext s_profiler_contexts[PROFILER_CONTEXT_CAPACITY];
static uint8_t s_profiler_tx_buffer[PROFILER_TX_BUFFER_SIZE] __attribute__((aligned(32)));
static uint8_t s_profiler_rx_buffer[PROFILER_RX_BUFFER_SIZE] __attribute__((aligned(32)));

void* const volatile g_bench_symbols[] = {
    (void*)bench_call_overhead,
    (void*)bench_matrix_overhead
};
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static bool profiler_command_matches(const uint8_t* buffer, size_t length);
static bool profiler_dump_metrics(void);
static uint32_t profiler_tick_ms(void);
static void profiler_yield_ms(uint32_t milliseconds);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static bool profiler_command_matches(const uint8_t* buffer, size_t length)
{
    size_t first = 0U;
    size_t last = length;
    size_t command_length = sizeof(PROFILER_DUMP_COMMAND) - 1U;

    if ((buffer == NULL) || (length > PROFILER_RX_BUFFER_SIZE)) {
        return false;
    }

    while ((first < last) &&
           ((buffer[first] == (uint8_t)' ') || (buffer[first] == (uint8_t)'\t') ||
            (buffer[first] == (uint8_t)'\r') || (buffer[first] == (uint8_t)'\n'))) {
        first++;
    }
    while ((last > first) &&
           ((buffer[last - 1U] == (uint8_t)' ') || (buffer[last - 1U] == (uint8_t)'\t') ||
            (buffer[last - 1U] == (uint8_t)'\r') || (buffer[last - 1U] == (uint8_t)'\n'))) {
        last--;
    }

    if ((last - first) != command_length) {
        return false;
    }

    for (size_t index = 0U; index < command_length; index++) {
        if (buffer[first + index] != (uint8_t)PROFILER_DUMP_COMMAND[index]) {
            return false;
        }
    }

    return true;
}

static bool profiler_dump_metrics(void)
{
    const Stream* stream = profiler_transport_stream();
    bool success;

    if (stream == NULL) {
        return false;
    }

    profiler_stop();
    (void)profiler_transport_status();
    success = profiler_dump(stream);
    if (profiler_transport_status() != PROFILER_TRANSPORT_STATUS_OK) {
        success = false;
    }
    profiler_start();

    return success;
}

static uint32_t profiler_tick_ms(void)
{
    return HAL_GetTick();
}

static void profiler_yield_ms(uint32_t milliseconds)
{
    HAL_Delay(milliseconds);
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
  ProfilerConfig profiler_config = {
      .frequency = HAL_RCC_GetPCLK1Freq(),
      .map_entries = s_profiler_map,
      .map_capacity = PROFILER_MAP_CAPACITY,
      .metrics = s_profiler_metrics,
      .metrics_capacity = PROFILER_MAP_CAPACITY,
      .stack_frames = s_profiler_stack,
      .stack_depth = PROFILER_STACK_DEPTH,
      .contexts = s_profiler_contexts,
      .context_capacity = PROFILER_CONTEXT_CAPACITY,
      .time_mode = PROFILER_APP_TIME_MODE_VALUE
  };
  ProfilerTransportConfig transport_config = {
      .device = &huart2,
      .tx_buffer = s_profiler_tx_buffer,
      .tx_buffer_size = sizeof(s_profiler_tx_buffer),
      .rx_buffer = s_profiler_rx_buffer,
      .rx_buffer_size = sizeof(s_profiler_rx_buffer),
      .tx_timeout_ms = PROFILER_TX_TIMEOUT_MS,
      .tick_ms = profiler_tick_ms,
      .yield = profiler_yield_ms
  };

  if (!profiler_init(&profiler_config)) {
      Error_Handler();
  }
  if (profiler_transport_init(&transport_config) != PROFILER_TRANSPORT_STATUS_OK) {
      Error_Handler();
  }
  if (profiler_transport_listen() != PROFILER_TRANSPORT_STATUS_OK) {
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
    size_t frame_length = 0U;
    uint8_t events = profiler_transport_poll(&frame_length);

    if ((events & PROFILER_TRANSPORT_EVENT_FRAME) != 0U) {
        if (profiler_command_matches(s_profiler_rx_buffer, frame_length)) {
            (void)profiler_dump_metrics();
        }
        (void)profiler_transport_listen();
    }


    /* Benchmark workload execution under profiling */
    static const int32_t mat_a[4] = { 1, 2, 3, 4 };
    static const int32_t mat_b[4] = { 5, 6, 7, 8 };
    int32_t mat_out[4] = { 0, 0, 0, 0 };
    static const uint8_t crc_data[16] = {
        0x01U, 0x02U, 0x03U, 0x04U, 0x05U, 0x06U, 0x07U, 0x08U,
        0x09U, 0x0AU, 0x0BU, 0x0CU, 0x0DU, 0x0EU, 0x0FU, 0x10U
    };

    __disable_irq();
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);

    PROFILER_SCOPE(bench_exact_100_cycles, bench_exact_100_cycles());
    PROFILER_SCOPE(bench_exact_500_cycles, bench_exact_500_cycles());
    PROFILER_SCOPE(bench_exact_1000_cycles, bench_exact_1000_cycles());
    PROFILER_SCOPE(bench_unrolled_poly32, bench_unrolled_poly32(3U));
    PROFILER_SCOPE(bench_matrix_mul_2x2, bench_matrix_mul_2x2(mat_a, mat_b, mat_out));
    PROFILER_SCOPE(bench_fixed_loop_sum, bench_fixed_loop_sum(25U));
    PROFILER_SCOPE(bench_crc8_fixed_block, bench_crc8_fixed_block(crc_data, sizeof(crc_data)));
    __enable_irq();

    (void)mat_out;

    HAL_GPIO_TogglePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin);
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
