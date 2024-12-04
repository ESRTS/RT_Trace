#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include "trace.h"

#ifdef TRACE_STM32L476RG
#include "stm32l4xx_hal.h"
#include "core_cm4.h"
#endif

/****************************************************************************************************************
 * Variable declarations. For multiple cores, each core has its own copies.
 ****************************************************************************************************************/

/**
 * @brief Buffer used for trace information of each core.
 * We use two buffer and place them in separate memory banks. That way, the buffer
 * can be accessed by each core in isolation.
 */
#ifdef TRACE_STM32L476RG
/**
 * To store the trace buffer at a specific location, a dedicated section is created in the memory map.
 * The trace buffer is stored in RAM2 at location 0x10000000. To create the memory section add the following
 * to SECTIONS in STM32L476RGTX_FLASH.ld.
 *
 * .traceRAMBlock 0x10000000 :
 * {
 *   . = ALIGN(4);
 *   KEEP(*(.traceRAMsection))
 *   . = ALIGN(4);
 * } > RAM2
 */
uint32_t __attribute__((section(".traceRAMsection"))) traceBuffer[TRACE_BUFFER_SIZE];
#endif
#ifdef TRACE_PICO2
/**
 * The trace buffer for each core is allocated in SRAM banks 8 and 9, respectively.
 * This way there is no access conflicts between the two cores.
 */
static uint32_t __scratch_x("traceBufferCore0") traceBuffer_core0[TRACE_BUFFER_SIZE];
static uint32_t __scratch_y("traceBufferCore1") traceBuffer_core1[TRACE_BUFFER_SIZE];
#endif

/**
 * @brief Index to the next free element in the trace buffer for each core.
 *
 */
#ifdef TRACE_STM32L476RG
static uint32_t writeIndex;
#endif
#ifdef TRACE_PICO2
static uint32_t __scratch_x("traceBufferCore0") writeIndex_core0;
static uint32_t __scratch_y("traceBufferCore0") writeIndex_core1;
#endif

/**
 * @brief Timestamp of the last logged event on each core.
 * This is needed since we record delta times between the individual timestamps (to save memory).
 */
#ifdef TRACE_STM32L476RG
static uint64_t lastTimestamp;
#endif
#ifdef TRACE_PICO2
static uint64_t __scratch_x("traceBufferCore0") lastTimestamp_core0;
static uint64_t __scratch_y("traceBufferCore0") lastTimestamp_core1;
#endif
/**
 * @brief Flags for each core to indicate that traceing is enabled.
 * This is set to TRUE at initialization and set to FALSE if an event can't be written to the buffer anymore.
 */
#ifdef TRACE_STM32L476RG
static bool enableTrace;
#endif
#ifdef TRACE_PICO2
static bool __scratch_x("traceBufferCore0") enableTrace_core0;
static bool __scratch_x("traceBufferCore1") enableTrace_core1;
#endif

/****************************************************************************************************************
 * Platform specific makro definitions. For multiple cores, those should work on each core independently.
 ****************************************************************************************************************/
/**
 * @brief Makro that returns the correct trace buffer for each core.
 *
 */
#ifdef TRACE_STM32L476RG
#define getBuffer() traceBuffer
static bool enableTrace;
#endif
#ifdef TRACE_PICO2
#define getBuffer() (sio_hw->cpuid == 0) ? traceBuffer_core0 : traceBuffer_core1;
#endif

/**
 * @brief Makro that returns a pointer to the correct write index for each core.
 *
 */
#ifdef TRACE_STM32L476RG
#define getWriteIndex() &writeIndex
static bool enableTrace;
#endif
#ifdef TRACE_PICO2
#define getWriteIndex() (sio_hw->cpuid == 0) ? &writeIndex_core0 : &writeIndex_core1;
#endif

/**
 * @brief Makro that returns a pointer to the last event timestamp for each core.
 *
 */
#ifdef TRACE_STM32L476RG
#define getLastTimestamp() &lastTimestamp
static bool enableTrace;
#endif
#ifdef TRACE_PICO2
#define getLastTimestamp() (sio_hw->cpuid == 0) ? &lastTimestamp_core0 : &lastTimestamp_core1;
#endif

/**
 * @brief Makro that returns a pointer to the flag that indicates if traceing is enabled for each core.
 *
 */
#ifdef TRACE_STM32L476RG
#define getTraceState() &enableTrace
static bool enableTrace;
#endif
#ifdef TRACE_PICO2
#define getTraceState() (sio_hw->cpuid == 0) ? &enableTrace_core0 : &enableTrace_core1;
#endif

/**
 * @brief Makro to get the correct function that returns the current time in us.
 *
 */
#ifdef TRACE_STM32L476RG
#define getTimeUs() timer_time_us_64()
#endif
#ifdef TRACE_PICO2
#define getTimeUs() timer_time_us_64(timer0_hw)
#endif

/****************************************************************************************************************
 * Platform specific functions.
 ****************************************************************************************************************/

#ifdef TRACE_STM32L476RG
/**
 * @brief Save the current interrupt state and disable interrupts.
 *
 */
uint32_t save_and_disable_interrupts() {
	uint32_t irqState = __get_PRIMASK();              				/* Save the current interrupt state */
	__disable_irq();												/* Disable interrupts on the current core */
	return irqState;
}

/**
 * @brief Restore the interrupt state and enable interrupts.
 *
 */
void restore_interrupts(uint32_t irqState) {
	__set_PRIMASK(irqState);										/* Restore the interrupt state */
	__enable_irq();                                  				/* Enable interrupts */
}

/**
 * @brief Returns the current time in us. The assumption is that TIM1 is used as systick.
 *
 */
uint64_t timer_time_us_64()
{
    uint32_t ms;
    uint32_t st;

    uint32_t uptimeMillis = HAL_GetTick();	/* Get the current time in ms, the HAL function is used since this starts counting before the FreeRTOS tick. */

    do
    {
        ms = uptimeMillis;					/* Current time in ms. */
        st = TIM1->CNT;						/* Current counter of TIM1, counts up to 999. */
        uptimeMillis = HAL_GetTick();
    } while (ms != uptimeMillis);			/* Make sure the timer didn't roll over during the measurement. */

    uint64_t ts = (ms * 1000) + st;			/* TIM1 is used as systick, counts up until 999 */
    return ts;

}

/**
 * @brief Initializes the trace mechanism.
 *
 */
void trace_init() {

    /* Set the complete trace buffer to 0x00 */
    for (int i = 0; i < TRACE_BUFFER_SIZE; i++) {
        traceBuffer[i] = 0x00;
    }

    writeIndex = 0;

    enableTrace = true;
}
#endif

#ifdef TRACE_PICO2
/**
 * @brief Initializes the trace mechanism.
 *
 */
void trace_init() {

    /* Set the complete trace buffer to 0x00 */
    for (int i = 0; i < TRACE_BUFFER_SIZE; i++) {
        traceBuffer_core0[i] = 0x00;
        traceBuffer_core1[i] = 0x00;
    }

    writeIndex_core0 = 0;
    writeIndex_core1 = 0;

    enableTrace_core0 = true;
    enableTrace_core1 = true;
}
#endif

/****************************************************************************************************************
 * Platform independent functions
 ****************************************************************************************************************/

/**
 * @brief Encodes the event ID as well as the timestamp delta into one byte.
 *
 * The event ID is encoded into the upper 16 bits.
 * The delta between the last timestamp and the current time is encoded into the lower byte.
 * With the 1us timer granularity, this allows to record timestamps that are up to 65,536 ms apart.
 * As the FreeRTOS ticks appear at 1ms granularity (if not changed), this is safe.
 *
 * Note: We assume interrupts are disabled when this function is called.
 * @param event
 * @return uint32_t
 */
uint32_t trace_encodeTime(uint16_t event) {
	bool* enabled = getTraceState();
	if (*enabled == false) return 0;                    /* Only increment the timestamps if treacing is still enabled. */

	uint64_t* lastTs = getLastTimestamp();              /* Get the timestamp of the last event that was written */
	uint64_t ts = timer_time_us_64();          /* 1us timestamp, can be accessed from both cores */

	uint16_t delta = ((ts - *lastTs) & 0xffff);
	*lastTs = ts;

	return (event << 16) | delta;
}

/**
 * @brief Returns a pointer to the correct buffer location, considering which core it is and
 * which event needs to be written.
 * Note: We assume interrupts are disabled when this function is called.
 *
 * @param length Length of the event data.
 * @return Pointer to the reserved event buffer.
 */
uint32_t* trace_getEventBuffer(uint16_t length) {

	/* Get a core dependent pointer to buffer, write index and traceing state. */
	uint32_t* buffer = getBuffer();
	uint32_t* index = getWriteIndex();
	bool* enabled = getTraceState();
	uint32_t* retval = NULL;

	if (*enabled == true) {                         /* Check if traceing is enabled for this core */
	    if (*index + length <= TRACE_BUFFER_SIZE) { /* Check if the buffer has sufficient space for the message */
	        retval = &buffer[*index];               /* Return the pointer to the buffer location the event should be written to. */
	        *index += length;                       /* Reserve space, i.e. increment the index. */
	        buffer[*index] = 0xaabbccdd;
	    } else {
	    *enabled = false;                       /* If this event can't be added to the buffer, tracing is disabled for this core. */
	    }
	}

	return retval;
}

/**
 * @brief Record the event that the CPU starts to be idle.
 *
 */
void trace_idle(void) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(1);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_IDLE);             /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
    }
}

/**
 * @brief Record the event that the task has started to execute.
 *
 */
void trace_execStart(uint32_t taskId) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(2);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_TASK_START_EXEC);  /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = taskId;
    }
}

/**
 * @brief Record the event that the task has stopped to execute.
 *
 */
void trace_execStop(uint32_t taskId) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(2);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_TASK_STOP_EXEC);   /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = taskId;
    }
}

/**
 * @brief Record the event that the task is in ready state.
 *
 */
void trace_readyStart(uint32_t taskId) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(2);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_TASK_START_READY); /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = taskId;
    }
}

/**
 * @brief Record the event that the task is not in ready state anymore.
 *
 */
void trace_readyStop(uint32_t taskId) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(2);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_TASK_STOP_READY);  /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = taskId;
    }
}


/**
 * @brief Record the event that a new task was created.
 *
 */
void trace_taskCreate(uint32_t taskId, uint32_t priority, const char* name) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */

    /* Get the length of the string, with 4 byte alignment */
    uint16_t len = strlen(name);
    uint16_t slen = len / 4;
    if ( len % 4 != 0) slen++;

    uint32_t* buffer = trace_getEventBuffer(4 + slen);              /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_TASK_CREATE);      /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = taskId;
        buffer[2] = slen;
        buffer[3] = priority;

        strcpy((char*)&buffer[4], name);  /* Copy the string to the trace buffer and increase the index. */
    }
}


/**
 * @brief Record the event that the tracing started.
 *
 */
void trace_start(void) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(1);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_START);            /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
    }
}

/**
 * @brief Record the event that the tracing started.
 *
 */
void trace_stop(void) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(1);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_STOP);             /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
    }
}

/**
 * @brief Record the event that the tracing started.
 *
 */
void trace_delayUntil(uint32_t* prev, uint32_t timeInc) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(2);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_DELAY_UNTIL);      /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = *prev + timeInc;
    }
}

/**
 * @brief Record the ISR enter event.
 *
 */
void trace_isrEnter(void) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
#ifdef TRACE_STM32L476RG
    uint32_t irqId = SCB->ICSR & 0x000001ff;						/* Read VECTACTIVE, he exception number of the current executing exception. */
#endif
#ifdef TRACE_PICO2
    uint32_t irqId = m33_hw->icsr & 0x000001ff;                     /* Read VECTACTIVE, he exception number of the current executing exception. */
#endif
    uint32_t* buffer = trace_getEventBuffer(2);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_ISR_ENTER);        /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
        buffer[1] = irqId;
    }
}

/**
 * @brief Record the ISR exit event.
 *
 */
void trace_isrExit(void) {

    uint32_t irqState = save_and_disable_interrupts();              /* Disable interrupts on the current core */
    uint32_t* buffer = trace_getEventBuffer(1);                     /* Request a buffer */
    uint32_t identifyer = trace_encodeTime(TRACE_ISR_EXIT);         /* Encodes the event ID and the timestamp delta */
    restore_interrupts(irqState);                                   /* Enable and restore interrupts */

    if (buffer != NULL) {
        buffer[0] = identifyer;
    }
}
