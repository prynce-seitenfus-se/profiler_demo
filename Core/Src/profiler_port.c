/*
 * Hardware timer port implementation for profiler on STM32G070RB.
 */

#include "profiler_port.h"

#include "main.h"
#include "tim.h"

void profiler_port_hardware_timer16_init(void)
{
    __HAL_TIM_SET_COUNTER(&htim1, 0U);
    __HAL_TIM_SET_COUNTER(&htim3, 0U);

#if defined(PROFILER_PORT_USE_16BIT_IT)
    /* Start TIM1 with update interrupts enabled for software overflow extension */
    if (HAL_TIM_Base_Start_IT(&htim1) != HAL_OK) {
        Error_Handler();
    }
#else
    /* Start both cascaded timers in hardware master/slave mode */
    if (HAL_TIM_Base_Start(&htim3) != HAL_OK) {
        Error_Handler();
    } else if (HAL_TIM_Base_Start(&htim1) != HAL_OK) {
        Error_Handler();
    }
#endif
}

uint16_t profiler_port_hardware_timer16_read_low(void)
{
    return (uint16_t)__HAL_TIM_GET_COUNTER(&htim1);
}

uint16_t profiler_port_hardware_timer16_read_high(void)
{
    return (uint16_t)__HAL_TIM_GET_COUNTER(&htim3);
}

bool profiler_port_hardware_timer16_overflow_pending(void)
{
    return (__HAL_TIM_GET_FLAG(&htim1, TIM_FLAG_UPDATE) != RESET);
}
