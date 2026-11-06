#ifndef RT_TRACE_H
#define RT_TRACE_H

/* RP2350 / Pico SDK / FreeRTOS SMP. Include at the END of FreeRTOSConfig.h.
 * Add this directory with CMake and call rt_trace_enable(firmware).
 * Do not include target/trace.h or compile target/trace.c.
 * Defaults may be overridden before this header (no traceConfig.h required).
 * Application instrumentation: call traceTimeZero() once, on either core.
 * No allocation, task lookup, global lock, printf or buffering timer in hooks.
 */
#include <stdint.h>
#include <stddef.h>

#define TRACE_AVAILABLE 1
#define TRACE_RTT 1
#define TRACE_FORMAT_VERSION 3u
#ifndef TRACE_RTT_BUFFER_SIZE
#define TRACE_RTT_BUFFER_SIZE 1536u
#endif
#ifndef TRACE_SCRATCH_RESERVED_BYTES
#define TRACE_SCRATCH_RESERVED_BYTES 2048u /* default SDK stack in each bank */
#endif
#ifndef TRACE_NAME_MAX
#define TRACE_NAME_MAX 32u
#endif
#ifndef TRACE_SYNC_INTERVAL_US
#define TRACE_SYNC_INTERVAL_US 1000000u
#endif
#ifndef TRACE_SYNC_INTERVAL_EVENTS
#define TRACE_SYNC_INTERVAL_EVENTS 256u
#endif
#ifndef TRACE_ENABLE_IRQ_TRACE
#define TRACE_ENABLE_IRQ_TRACE 0
#endif

/* Wire format: little-endian uint32 words, no compiler structs.
 * Common header: [delta_us:16 | task:8 | event:8]. Task 0 = no task;
 * task 255 means a full uint32 ID follows the header, before event payload.
 * IDs are FreeRTOS uxTCBNumber, never a truncated pointer or a shared mapping.
 * One RTT write atomically commits an optional SYNC followed by one event.
 * No batching. All payload sizes below are in words.
 *
 * SYNC (12 words, no common header):
 *   magic, version|core<<8|cores<<16|irq_enabled<<23|tick_bits<<24, time_lo, time_hi,
 *   next_event_sequence, cumulative_drops, epoch_lo, epoch_hi,
 *   session_lo, session_hi, running_task, tick_hz.
 * Epoch UINT64_MAX means not set. Sequence increments for EVERY attempted
 * event, including drops (both counters wrap modulo 2^32). SYNC's time is
 * the following event's time; that event's delta is zero. Subsequent deltas
 * refer to the previous COMMITTED event. SYNC appears initially, after loss,
 * on delta overflow, periodically on traffic, and on time-zero/end events.
 * There are no sync-only writes, so a decoder can discard a truncated pair.
 * SYNC permits recovery of time/current task, not all task/object/job state.
 * Never guess missing state after loss. No checksum: RTT is byte preserving;
 * framing recovery uses the magic plus version/core/length validation.
 */
#define TRACE_SYNC_MAGIC UINT32_C(0x33545452) /* bytes "RTT3" */
#define TRACE_SYNC_IRQ_ENABLED (UINT32_C(1) << 23) /* core count occupies bits 16..22 */
#define TRACE_CURRENT_TASK UINT32_MAX       /* writer argument, not a wire ID */

enum trace_event_id {
    TRACE_SWITCH_IN = 1,       /* 0 */
    TRACE_SWITCH_OUT,          /* 0 */
    TRACE_READY,               /* 1: current kernel tick */
    TRACE_TASK_CREATE,         /* 3+ceil(name_len/4): handle, priority, len, bytes */
    TRACE_TASK_DELETE,         /* 0 */
    TRACE_PRIORITY,            /* 1: new priority (including inheritance) */
    TRACE_SUSPEND,             /* 0 */
    TRACE_RESUME,              /* 0 */
    TRACE_TICK,                /* 2: tick BEFORE increment, scheduler suspended */
    TRACE_TICK_JUMP,           /* 2: tick AFTER jump, actual jump */
    TRACE_SYSCALL_ENTER,       /* 6: kind, object/index, timeout, arg0, arg1, caller */
    TRACE_SYSCALL_RETURN,      /* 2: kind, result; correlate per TASK, not core */
    TRACE_BLOCK,               /* 2: kind, object/index; may repeat on retries */
    TRACE_WAKE_TARGET,         /* 1: actual kernel wake tick, not a job marker */
    TRACE_OBJECT_CREATE,       /* 3: handle, type, caller (types below) */
    TRACE_OBJECT_DELETE,       /* 1: handle */
    TRACE_QUEUE_RECEIVED,      /* 2: handle, queue type; successful operation */
    TRACE_QUEUE_SENT,          /* 2: handle, queue type; successful operation */
    TRACE_NOTIFY_TAKEN,        /* 2: index, value BEFORE consumption */
    TRACE_NOTIFY_WAITED,       /* 2: index, received boolean */
    TRACE_EVENT_WAIT_END,      /* 2: object, timed-out boolean; MAY follow dispatch */
    TRACE_EVENT_SYNC_END,      /* 2: object, timed-out boolean; MAY follow dispatch */
    TRACE_RECURSIVE_TAKEN,     /* 2: object, recursion depth AFTER successful take */
    TRACE_RECURSIVE_GIVEN,     /* 2: object, depth BEFORE successful give */
    TRACE_TIME_ZERO,           /* 2: absolute epoch lo, hi */
    TRACE_END,                 /* 0: this core only; drain RTT afterwards */
    TRACE_IRQ_ENTER,           /* 1: exception number */
    TRACE_IRQ_EXIT,            /* 0 */
    TRACE_IRQ_EXIT_SCHEDULE,   /* 0 */
    TRACE_AFFINITY,            /* 1: core mask */
    TRACE_IDLE_TASK            /* 1: idle core; task field gives actual ID */
};

enum trace_syscall_kind {
    TRACE_CALL_DELAY_UNTIL = 1, /* args: previous wake tick, increment */
    TRACE_CALL_DELAY,
    TRACE_CALL_QUEUE_RECEIVE,
    TRACE_CALL_SEMAPHORE_TAKE,
    TRACE_CALL_MUTEX_TAKE,
    TRACE_CALL_NOTIFY_TAKE,     /* arg0: clear-on-exit */
    TRACE_CALL_NOTIFY_WAIT,     /* args: clear-on-entry, clear-on-exit */
    TRACE_CALL_EVENT_WAIT,      /* args: bits, clear|wait-all<<1 */
    TRACE_CALL_EVENT_SYNC,      /* args: bits-to-set, bits-to-wait */
    TRACE_CALL_MUTEX_RECURSIVE, /* nested SEMAPHORE_TAKE is implementation detail */
    TRACE_CALL_STREAM_SEND,    /* within-job wait */
    TRACE_CALL_STREAM_RECEIVE  /* within-job wait */
};
/* Queue object types 0..4 are FreeRTOS queueQUEUE_TYPE_* values. */
#define TRACE_OBJECT_EVENT_GROUP 256u

#ifdef __cplusplus
extern "C" {
#endif
void trace_init(void);          /* automatic pre-main SDK initializer; idempotent */
void trace_stop(void);          /* current core only; no cross-core halt/lock */
void trace_timeZero(void);      /* once, after init; timestamped inside local mask */
void trace_event(uint32_t type, uint32_t task, const uint32_t *payload, uint32_t words);
void trace_switch(uint32_t task, uint32_t entering);
void trace_task_create(uint32_t task, uintptr_t handle, uint32_t priority, const char *name);
void trace_syscall(uint32_t kind, uintptr_t object, uint32_t timeout,
                   uint32_t arg0, uint32_t arg1, uintptr_t caller);
void trace_irq_enter(void);
#ifdef __cplusplus
}
#endif

/* Small constant-size wrappers. Transient arguments may use the caller's
 * stack; persistent state, RTT ring and serialization workspace use scratch. */
static inline void trace_e0(uint32_t event, uint32_t task) {
    trace_event(event, task, NULL, 0);
}
static inline void trace_e1(uint32_t event, uint32_t task, uint32_t a) {
    const uint32_t data[] = {a};
    trace_event(event, task, data, 1);
}
static inline void trace_e2(uint32_t event, uint32_t task, uint32_t a, uint32_t b) {
    const uint32_t data[] = {a, b};
    trace_event(event, task, data, 2);
}
static inline void trace_object_create(uintptr_t object, uint32_t kind, uintptr_t caller) {
    const uint32_t data[] = {(uint32_t)object, kind, (uint32_t)caller};
    if (object != 0) trace_event(TRACE_OBJECT_CREATE, TRACE_CURRENT_TASK, data, 3);
}

#define traceTimeZero() trace_timeZero()
#define TRACE_TCB_ID(tcb) ((uint32_t)((tcb)->uxTCBNumber))
#define TRACE_RUNNING_TCB (pxCurrentTCBs[portGET_CORE_ID()]) /* tasks.c only, SMP */
#define TRACE_PTR(p) ((uintptr_t)(p))
#define TRACE_CALLER() ((uintptr_t)__builtin_extract_return_addr(__builtin_return_address(0)))
#define TRACE_CURRENT TRACE_CURRENT_TASK
#define TRACE_RETURN(kind, result) trace_e2(TRACE_SYSCALL_RETURN, TRACE_CURRENT, (kind), (uint32_t)(result))
#define TRACE_BLOCKED(kind, object) trace_e2(TRACE_BLOCK, TRACE_CURRENT, (kind), (uint32_t)TRACE_PTR(object))

/* Direct core-local TCB access avoids the kernel's pxCurrentTCB API alias. */
#define traceTASK_SWITCHED_IN() trace_switch(TRACE_TCB_ID(TRACE_RUNNING_TCB), 1)
#define traceTASK_SWITCHED_OUT() trace_switch(TRACE_TCB_ID(TRACE_RUNNING_TCB), 0)
#define traceSTARTING_SCHEDULER(handles) do { \
    trace_e1(TRACE_IDLE_TASK, TRACE_TCB_ID((TCB_t *)(handles)[0]), 0); \
    trace_e1(TRACE_IDLE_TASK, TRACE_TCB_ID((TCB_t *)(handles)[1]), 1); \
} while (0)
#define traceTASK_CREATE(tcb) do { \
    trace_task_create(TRACE_TCB_ID(tcb), TRACE_PTR(tcb), (uint32_t)(tcb)->uxPriority, (tcb)->pcTaskName); \
} while (0)
#define traceTASK_DELETE(tcb) trace_e0(TRACE_TASK_DELETE, TRACE_TCB_ID(tcb))
#define traceMOVED_TASK_TO_READY_STATE(tcb) trace_e1(TRACE_READY, TRACE_TCB_ID(tcb), (uint32_t)xTickCount)
#define traceTASK_PRIORITY_SET(tcb, priority) trace_e1(TRACE_PRIORITY, TRACE_TCB_ID(tcb), (uint32_t)(priority))
#define traceTASK_PRIORITY_INHERIT(tcb, priority) trace_e1(TRACE_PRIORITY, TRACE_TCB_ID(tcb), (uint32_t)(priority))
#define traceTASK_PRIORITY_DISINHERIT(tcb, priority) trace_e1(TRACE_PRIORITY, TRACE_TCB_ID(tcb), (uint32_t)(priority))
#define traceTASK_SUSPEND(tcb) trace_e0(TRACE_SUSPEND, TRACE_TCB_ID(tcb))
#define traceTASK_RESUME(tcb) trace_e0(TRACE_RESUME, TRACE_TCB_ID(tcb))
#define traceTASK_RESUME_FROM_ISR(tcb) trace_e0(TRACE_RESUME, TRACE_TCB_ID(tcb))
#define traceENTER_vTaskCoreAffinitySet(task, mask) do { \
    trace_e1(TRACE_AFFINITY, TRACE_TCB_ID((task) ? (TCB_t *)(task) : TRACE_RUNNING_TCB), (uint32_t)(mask)); \
} while (0)
#define traceTASK_INCREMENT_TICK(tick) trace_e2(TRACE_TICK, 0, (uint32_t)(tick), (uint32_t)uxSchedulerSuspended)
#define traceINCREASE_TICK_COUNT(jump) trace_e2(TRACE_TICK_JUMP, 0, (uint32_t)xTickCount, (uint32_t)(jump))

#define traceENTER_xTaskDelayUntil(previous, increment) do { \
    if ((previous) != NULL) trace_syscall(TRACE_CALL_DELAY_UNTIL, 0, 0, (uint32_t)*(previous), (uint32_t)(increment), TRACE_CALLER()); \
} while (0)
#define traceRETURN_xTaskDelayUntil(result) TRACE_RETURN(TRACE_CALL_DELAY_UNTIL, result)
#define traceENTER_vTaskDelay(ticks) trace_syscall(TRACE_CALL_DELAY, 0, (uint32_t)(ticks), 0, 0, TRACE_CALLER())
#define traceRETURN_vTaskDelay() TRACE_RETURN(TRACE_CALL_DELAY, 0)
/* Also called for other timed waits: classify using surrounding syscall events. */
#define traceTASK_DELAY_UNTIL(wake) trace_e1(TRACE_WAKE_TARGET, TRACE_CURRENT, (uint32_t)(wake))
#define traceMOVED_TASK_TO_DELAYED_LIST() trace_e1(TRACE_WAKE_TARGET, TRACE_CURRENT, (uint32_t)xTimeToWake)
#define traceMOVED_TASK_TO_OVERFLOW_DELAYED_LIST() traceMOVED_TASK_TO_DELAYED_LIST()

#define traceENTER_xQueueReceive(queue, buffer, ticks) \
    trace_syscall(TRACE_CALL_QUEUE_RECEIVE, TRACE_PTR(queue), (uint32_t)(ticks), 0, 0, TRACE_CALLER())
#define traceRETURN_xQueueReceive(result) TRACE_RETURN(TRACE_CALL_QUEUE_RECEIVE, result)
#define traceENTER_xQueueSemaphoreTake(queue, ticks) do { \
    if ((queue) != NULL) trace_syscall( \
        (((queue)->ucQueueType == queueQUEUE_TYPE_MUTEX || (queue)->ucQueueType == queueQUEUE_TYPE_RECURSIVE_MUTEX) \
         ? TRACE_CALL_MUTEX_TAKE : TRACE_CALL_SEMAPHORE_TAKE), \
        TRACE_PTR(queue), (uint32_t)(ticks), (uint32_t)(queue)->ucQueueType, 0, TRACE_CALLER()); \
} while (0)
/* Return kind SEMAPHORE_TAKE covers both; entry carries the precise kind. */
#define traceRETURN_xQueueSemaphoreTake(result) TRACE_RETURN(TRACE_CALL_SEMAPHORE_TAKE, result)
#define traceBLOCKING_ON_QUEUE_RECEIVE(queue) TRACE_BLOCKED(TRACE_CALL_QUEUE_RECEIVE, queue)
#define traceBLOCKING_ON_QUEUE_SEND(queue) TRACE_BLOCKED(0, queue)
#define traceBLOCKING_ON_STREAM_BUFFER_SEND(buffer) TRACE_BLOCKED(TRACE_CALL_STREAM_SEND, buffer)
#define traceBLOCKING_ON_STREAM_BUFFER_RECEIVE(buffer) TRACE_BLOCKED(TRACE_CALL_STREAM_RECEIVE, buffer)
#define traceQUEUE_RECEIVE(queue) trace_e2(TRACE_QUEUE_RECEIVED, TRACE_CURRENT, (uint32_t)TRACE_PTR(queue), (uint32_t)(queue)->ucQueueType)
#define traceQUEUE_SEND(queue) trace_e2(TRACE_QUEUE_SENT, TRACE_CURRENT, (uint32_t)TRACE_PTR(queue), (uint32_t)(queue)->ucQueueType)
#define traceQUEUE_CREATE(queue) trace_object_create(TRACE_PTR(queue), (uint32_t)(queue)->ucQueueType, TRACE_CALLER())
#define traceQUEUE_DELETE(queue) trace_e1(TRACE_OBJECT_DELETE, TRACE_CURRENT, (uint32_t)TRACE_PTR(queue))
/* TAKE_MUTEX_RECURSIVE fires before success in this kernel. Use the successful
 * return, where pxMutex is still in scope. Suppress duplicate host ownership
 * updates from the nested semaphore take / final queue send. */
#define traceENTER_xQueueTakeMutexRecursive(mutex, ticks) \
    trace_syscall(TRACE_CALL_MUTEX_RECURSIVE, TRACE_PTR(mutex), (uint32_t)(ticks), 0, 0, TRACE_CALLER())
#define traceRETURN_xQueueTakeMutexRecursive(result) do { \
    if ((result) != 0) trace_e2(TRACE_RECURSIVE_TAKEN, TRACE_CURRENT, (uint32_t)TRACE_PTR(pxMutex), (uint32_t)pxMutex->u.xSemaphore.uxRecursiveCallCount); \
    TRACE_RETURN(TRACE_CALL_MUTEX_RECURSIVE, result); \
} while (0)
#define traceGIVE_MUTEX_RECURSIVE(mutex) trace_e2(TRACE_RECURSIVE_GIVEN, TRACE_CURRENT, (uint32_t)TRACE_PTR(mutex), (uint32_t)(mutex)->u.xSemaphore.uxRecursiveCallCount)

#define traceENTER_ulTaskGenericNotifyTake(index, clear, ticks) \
    trace_syscall(TRACE_CALL_NOTIFY_TAKE, (uintptr_t)(index), (uint32_t)(ticks), (uint32_t)(clear), 0, TRACE_CALLER())
#define traceRETURN_ulTaskGenericNotifyTake(result) TRACE_RETURN(TRACE_CALL_NOTIFY_TAKE, result)
#define traceTASK_NOTIFY_TAKE_BLOCK(index) TRACE_BLOCKED(TRACE_CALL_NOTIFY_TAKE, index)
#define traceTASK_NOTIFY_TAKE(index) trace_e2(TRACE_NOTIFY_TAKEN, TRACE_CURRENT, (uint32_t)(index), TRACE_RUNNING_TCB->ulNotifiedValue[index])
#define traceENTER_xTaskGenericNotifyWait(index, clearIn, clearOut, value, ticks) \
    trace_syscall(TRACE_CALL_NOTIFY_WAIT, (uintptr_t)(index), (uint32_t)(ticks), (uint32_t)(clearIn), (uint32_t)(clearOut), TRACE_CALLER())
#define traceRETURN_xTaskGenericNotifyWait(result) TRACE_RETURN(TRACE_CALL_NOTIFY_WAIT, result)
#define traceTASK_NOTIFY_WAIT_BLOCK(index) TRACE_BLOCKED(TRACE_CALL_NOTIFY_WAIT, index)
#define traceTASK_NOTIFY_WAIT(index) trace_e2(TRACE_NOTIFY_WAITED, TRACE_CURRENT, (uint32_t)(index), (uint32_t)(TRACE_RUNNING_TCB->ucNotifyState[index] == taskNOTIFICATION_RECEIVED))

#define traceEVENT_GROUP_CREATE(group) trace_object_create(TRACE_PTR(group), TRACE_OBJECT_EVENT_GROUP, TRACE_CALLER())
#define traceEVENT_GROUP_DELETE(group) trace_e1(TRACE_OBJECT_DELETE, TRACE_CURRENT, (uint32_t)TRACE_PTR(group))
#define traceENTER_xEventGroupWaitBits(group, bits, clear, all, ticks) \
    trace_syscall(TRACE_CALL_EVENT_WAIT, TRACE_PTR(group), (uint32_t)(ticks), (uint32_t)(bits), (uint32_t)((!!(clear)) | ((!!(all)) << 1)), TRACE_CALLER())
#define traceRETURN_xEventGroupWaitBits(result) TRACE_RETURN(TRACE_CALL_EVENT_WAIT, result)
#define traceEVENT_GROUP_WAIT_BITS_BLOCK(group, bits) TRACE_BLOCKED(TRACE_CALL_EVENT_WAIT, group)
#define traceEVENT_GROUP_WAIT_BITS_END(group, bits, timeout) trace_e2(TRACE_EVENT_WAIT_END, TRACE_CURRENT, (uint32_t)TRACE_PTR(group), (uint32_t)(timeout))
#define traceENTER_xEventGroupSync(group, set, wait, ticks) \
    trace_syscall(TRACE_CALL_EVENT_SYNC, TRACE_PTR(group), (uint32_t)(ticks), (uint32_t)(set), (uint32_t)(wait), TRACE_CALLER())
#define traceRETURN_xEventGroupSync(result) TRACE_RETURN(TRACE_CALL_EVENT_SYNC, result)
#define traceEVENT_GROUP_SYNC_BLOCK(group, set, wait) TRACE_BLOCKED(TRACE_CALL_EVENT_SYNC, group)
#define traceEVENT_GROUP_SYNC_END(group, set, wait, timeout) trace_e2(TRACE_EVENT_SYNC_END, TRACE_CURRENT, (uint32_t)TRACE_PTR(group), (uint32_t)(timeout))

#if TRACE_ENABLE_IRQ_TRACE
#define traceISR_ENTER() trace_irq_enter()
#define traceISR_EXIT() trace_e0(TRACE_IRQ_EXIT, 0)
#define traceISR_EXIT_TO_SCHEDULER() trace_e0(TRACE_IRQ_EXIT_SCHEDULE, 0)
#endif
#endif
