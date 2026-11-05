#ifndef TRACE_H
#define TRACE_H

#include "stdint.h"
#include "traceConfig.h"

/**
 * Used for other code to check if the trace infrastructure is included.
 */
#define TRACE_AVAILABLE

/**
 * Size of the trace buffer for each core, in uint32_t. Can be set in traceConfig.h.
 * RT-Trace reads the address and size of the SRAM buffers from the ELF file.
 */
#ifndef TRACE_BUFFER_SIZE
#if defined(PICO_USE_PSRAM)
/* 20 KB of PSRAM for each core. */
#define TRACE_BUFFER_SIZE 5000
#elif defined(TRACE_PICO2)
/* 96 KB of SRAM for each core, about 2 s of six periodic tasks. Together with a 128 KB FreeRTOS heap
 * this leaves about 190 KB of the 520 KB SRAM for the program. */
#define TRACE_BUFFER_SIZE 24576
#else
/* 2 KB in RAM2 of the STM32L476. */
#define TRACE_BUFFER_SIZE 500
#endif
#endif /* TRACE_BUFFER_SIZE */

/**
 * Size of the RTT buffer of each core in bytes. RT-Trace empties it about every millisecond,
 * events that do not fit in between are lost (and reported). Can be set in traceConfig.h.
 */
#ifndef TRACE_RTT_BUFFER_SIZE
#define TRACE_RTT_BUFFER_SIZE 8192
#endif
/****************************************************************************************************************
 * Defines and function declarations.
 ****************************************************************************************************************/

/**
 * Supported trace IDs
 */
#define TRACE_IDLE                      (1u)
#define TRACE_TASK_START_EXEC           (2u)
#define TRACE_TASK_STOP_EXEC            (3u)
#define TRACE_TASK_START_READY          (4u)
#define TRACE_TASK_STOP_READY           (5u)
#define TRACE_TASK_CREATE               (6u)
#define TRACE_START                     (7u)
#define TRACE_STOP                      (8u)
#define TRACE_DELAY_UNTIL               (9u)
#define TRACE_ISR_ENTER                 (10u)
#define TRACE_ISR_EXIT                  (11u)
#define TRACE_ISR_EXIT_TO_SCHEDULER     (12u)
#define TRACE_DELAY                     (13u)
#define TRACE_TIME_START                (14u)
#define TRACE_TASK_EVT_GROUP_WAIT       (15u)
#define TRACE_TASK_EVT_GROUP_SYNC       (16u)
#define TRACE_DEADLINE_MISS             (17u)
#define TRACE_MUTEX_CREATE              (18u)
#define TRACE_MUTEX_TAKE                (19u)
#define TRACE_MUTEX_GIVE                (20u)
#define TRACE_JLDJOB_FINISHED           (21u)
#define TRACE_EVENTS_LOST               (22u)   /* RTT only: number of events dropped since the last written event */
#define TRACE_TASK_BLOCK                (23u)   /* The task blocks on a queue, semaphore or task notification */
#define TRACE_VERSION                   (24u)   /* Written by trace_init(): format version of the trace */

/**
 * Format version written at trace_init(). Version 2 reports lost events with TRACE_EVENTS_LOST.
 */
#define TRACE_FORMAT_VERSION            (2u)

// Not yet supported
/*
#define TRACE_EVTID_TIMER_ENTER         (19u)
#define TRACE_EVTID_TIMER_EXIT          (20u)
#define TRACE_EVTID_STACK_INFO          (21u)
#define TRACE_EVTID_INIT                (24u)
#define TRACE_EVTID_PRINT_FORMATTED     (26u)
*/

/*
 * The only function that must be called in main to initialize all buffers.
 */
void trace_init();

/**
 * Functions to add a trace event to the buffer.
 */
void trace_idle(void);
void trace_execStart(uint32_t taskId);
void trace_execStop(uint32_t taskId);
void trace_readyStart(uint32_t taskId);
void trace_readyStop(uint32_t taskId);
void trace_taskCreate(uint32_t taskId, uint32_t priority, const char* name);
void trace_start(void);
void trace_stop(void);
void trace_delayUntil(uint32_t* prev, uint32_t timeInc, uint32_t tickCount);
void trace_delay(uint32_t delayTime);
void trace_isrEnter(void);
void trace_isrExit(void);
void trace_isrExitToScheduler(void);
void trace_timeZero(void);
void trace_eventGroupWait(uint32_t taskId);
void trace_eventGroupSync(uint32_t taskId);
void trace_mutexCreate(uint32_t mutexId);
void trace_mutexTake(uint32_t mutexId);
void trace_mutexGive(uint32_t mutexId);
void trace_jldJobFinished(uint32_t period_us);
void trace_taskBlock(uint32_t taskId);
void trace_eventGroupCreate(uint32_t eventGroup);
int trace_isInternalEventGroup(uint32_t eventGroup);

/****************************************************************************************************************
 * FreeRTOS trace defines to map to the trace infrastructure.
 ****************************************************************************************************************/

/* Used to perform any necessary initialisation - for example, open a file
 * into which trace is to be written. */
#define traceSTART()                                    trace_start()

/* Use to close a trace, for example close a file into which trace has been
 * written. */
#define traceEND()                                      trace_stop()

/* Called after a task has been selected to run.  pxCurrentTCB holds a pointer
 * to the task control block of the selected task. */
#ifndef TRACE_ENABLE_IDLE_TRACE
#define traceTASK_SWITCHED_IN()                         if (memcmp(pxCurrentTCB->pcTaskName, "IDLE", 4) != 0) {   \
                                                            trace_execStart((uint32_t)pxCurrentTCB);                         \
                                                        }
#else
#define traceTASK_SWITCHED_IN()                         if (memcmp(pxCurrentTCB->pcTaskName, "IDLE", 4) == 0) {   \
                                                            trace_idle();                                                    \
                                                         } else {                                                            \
                                                            trace_execStart((uint32_t)pxCurrentTCB);                         \
                                                        }
#endif

/* Called before a task has been selected to run.  pxCurrentTCB holds a pointer
 * to the task control block of the task being switched out. */
#define traceTASK_SWITCHED_OUT()                        if (memcmp(pxCurrentTCB->pcTaskName, "IDLE", 4) != 0) {   \
                                                            trace_execStop((uint32_t)pxCurrentTCB);                          \
                                                        }

/* Called when a new task is created. */
#define traceTASK_CREATE( pxNewTCB )                    if (pxNewTCB != NULL) {                                              \
                                                            trace_taskCreate((uint32_t)pxNewTCB,                             \
                                                                        pxNewTCB->uxPriority,                                \
                                                                        &(pxNewTCB->pcTaskName[0])                           \
                                                                        );                                                   \
                                                        }

//#define traceENTER_xTaskCreateStaticAffinitySet( pxTaskCode, pcName, uxStackDepth, pvParameters, uxPriority, puxStackBuffer, pxTaskBuffer, uxCoreAffinityMask )     trace_taskCreate((uint32_t)NULL, uxPriority,  pcName)


#define traceMOVED_TASK_TO_READY_STATE( pxTCB )         trace_readyStart((uint32_t)pxTCB)

#define traceENTER_xTaskDelayUntil( x, y )              trace_delayUntil(x, y, xTickCount  )

#define traceENTER_vTaskDelay( x )                      trace_delay(x)

#define traceMOVED_TASK_TO_SUSPENDED_LIST( pxTCB )      trace_readyStop((uint32_t)pxTCB)

#ifdef TRACE_ENABLE_IRQ_TRACE
    #define traceISR_EXIT()                             trace_isrExit()

    #define traceISR_EXIT_TO_SCHEDULER()                trace_isrExitToScheduler()

    #define traceISR_ENTER()                            trace_isrEnter()
#endif

#define traceTimeZero()                                 trace_timeZero()

/* The Pico port waits on an internal event group for SDK locks (pico_sync interop, for example the stdio mutex
 * inside printf). That group is created before main(), so before trace_init(). Waits on it are not traced, as
 * they are not job boundaries. */
#define traceEVENT_GROUP_CREATE( pxEventBits )          trace_eventGroupCreate((uint32_t)(pxEventBits))

#define traceENTER_xEventGroupWaitBits( x ,y, z, u, i ) do { if (!trace_isInternalEventGroup((uint32_t)(x))) {           \
                                                            trace_eventGroupWait((uint32_t)xTaskGetCurrentTaskHandle());     \
                                                        } } while (0)

#define traceENTER_xEventGroupSync( x, y, z, u )        do { if (!trace_isInternalEventGroup((uint32_t)(x))) {           \
                                                            trace_eventGroupSync((uint32_t)xTaskGetCurrentTaskHandle());     \
                                                        } } while (0)

/* A task that blocks on a queue, a semaphore or a notification waits for its next release. A task that
 * blocks on a mutex waits inside its job, so mutexes are left out. */
#define traceBLOCKING_ON_QUEUE_RECEIVE( pxQueue )       do { if ( (pxQueue)->ucQueueType != queueQUEUE_TYPE_MUTEX &&                    \
                                                              (pxQueue)->ucQueueType != queueQUEUE_TYPE_RECURSIVE_MUTEX ) {          \
                                                            trace_taskBlock((uint32_t)xTaskGetCurrentTaskHandle());          \
                                                        } } while (0)

#define traceTASK_NOTIFY_TAKE_BLOCK( uxIndexToWait )    trace_taskBlock((uint32_t)xTaskGetCurrentTaskHandle())

#define traceTASK_NOTIFY_WAIT_BLOCK( uxIndexToWait )    trace_taskBlock((uint32_t)xTaskGetCurrentTaskHandle())

#define traceRETURN_xQueueCreateMutex( x )              if ( x->ucQueueType == queueQUEUE_TYPE_MUTEX ) trace_mutexCreate((uint32_t)x)

#define traceENTER_xQueueSemaphoreTake( x, y )          if ( x->ucQueueType == queueQUEUE_TYPE_MUTEX ) trace_mutexTake((uint32_t)x)

#define traceENTER_xQueueGenericSend( x, y , z, u )     if ( x->ucQueueType == queueQUEUE_TYPE_MUTEX ) trace_mutexGive((uint32_t)x)

#define trace_JLD_JOB_FINISHED( x )                     trace_jldJobFinished( x )

/*

#define traceSTARTING_SCHEDULER( xIdleTaskHandles )

#define traceMOVED_TASK_TO_DELAYED_LIST()

#define traceMOVED_TASK_TO_OVERFLOW_DELAYED_LIST()

#define traceTASK_DELETE( pxTaskToDelete )

#define traceTASK_DELAY()

#define traceTASK_RESUME( pxTaskToResume )

#define traceTASK_RESUME_FROM_ISR( pxTaskToResume )

#define traceTASK_INCREMENT_TICK( xTickCount )

#define traceISR_EXIT_TO_SCHEDULER()

#define traceENTER_xTaskCreateStatic( pxTaskCode, pcName, uxStackDepth, pvParameters, uxPriority, puxStackBuffer, pxTaskBuffer )

#define traceRETURN_xTaskCreateStatic( xReturn )

#define traceENTER_xTaskCreateStaticAffinitySet( pxTaskCode, pcName, uxStackDepth, pvParameters, uxPriority, puxStackBuffer, pxTaskBuffer, uxCoreAffinityMask )

#define traceRETURN_xTaskCreateStaticAffinitySet( xReturn )
*/
#endif //TRACE_H
