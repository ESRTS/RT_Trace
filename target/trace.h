#ifndef TRACE_H
#define TRACE_H

#include "stdint.h"

/****************************************************************************************************************
 * Variable declarations. For multiple cores, each core has its own copies.
 ****************************************************************************************************************/

/**
 * Set the platform that is used.
 */
#define TRACE_STM32L476RG
//#define TRACE_PICO2

/**
 * Size of the trace buffer for each core, in uint32_t. 
 */
#define TRACE_BUFFER_SIZE 1000

/**
 * Enable this to trace IRQ enter/exit
 */
#define TRACE_ENABLE_IRQ_TRACE

/** 
 * Enable this to trace idle events
 */
#define TRACE_ENABLE_IDLE_TRACE

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

// Not yet supported
/*
#define TRACE_EVTID_ISR_TO_SCHEDULER    (18u)
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
void trace_delayUntil(uint32_t* prev, uint32_t timeInc);
void trace_isrEnter(void);
void trace_isrExit(void);
void trace_isrExitToScheduler(void);

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

#define traceENTER_xTaskDelayUntil( x, y )              trace_delayUntil(x, y)

#define traceMOVED_TASK_TO_SUSPENDED_LIST( pxTCB )      trace_readyStop((uint32_t)pxTCB)

#ifdef TRACE_ENABLE_IRQ_TRACE
    #define traceISR_EXIT()                             trace_isrExit()

    #define traceISR_EXIT_TO_SCHEDULER()                trace_isrExitToScheduler()
    
    #define traceISR_ENTER()                            trace_isrEnter()
#endif






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
