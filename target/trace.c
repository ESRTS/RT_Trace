#include "FreeRTOS.h"
#include "task.h"
#include "trace.h"
#include "pico/platform.h"
#include "pico/runtime.h"
#include "hardware/sync.h"
#include "hardware/timer.h"
#include "SEGGER_RTT.h"
#include <string.h>

#if !PICO_RP2350
#error "RT-Trace target requires RP2350"
#endif
#if configNUMBER_OF_CORES != 2 || configUSE_TRACE_FACILITY != 1
#error "Set configNUMBER_OF_CORES=2 and configUSE_TRACE_FACILITY=1"
#endif
#if SEGGER_RTT_MAX_NUM_UP_BUFFERS < 3
#error "RT-Trace requires RTT up channels 1 and 2 (at least 3 channels total)"
#endif
_Static_assert(sizeof(TickType_t) <= 4, "target currently supports 16/32-bit ticks");
_Static_assert(sizeof(uintptr_t) == 4, "target requires 32-bit pointers");
_Static_assert(TRACE_NAME_MAX > 0 && TRACE_NAME_MAX <= 64, "TRACE_NAME_MAX must be 1..64");
_Static_assert(TRACE_SYNC_INTERVAL_EVENTS > 0, "sync event interval must be positive");
_Static_assert(TRACE_RTT_BUFFER_SIZE >= 256, "RTT buffer too small");
_Static_assert(TRACE_RTT_BUFFER_SIZE % 4 == 0, "RTT buffer must preserve word alignment");

#define TRACE_PACKET_WORDS 40u
#define TRACE_NAME_PAYLOAD_WORDS (3u + (TRACE_NAME_MAX + 3u) / 4u)
#define TRACE_MAX_PAYLOAD_WORDS (TRACE_NAME_PAYLOAD_WORDS < 6u ? 6u : TRACE_NAME_PAYLOAD_WORDS)
#define TRACE_SYNC_WORDS 12u
#define TRACE_EPOCH_UNSET UINT64_MAX

/* One writer per core, serialized against normal IRQs by local PRIMASK.
 * Neither NMI nor HardFault may call these functions. Buffer and workspace
 * reside in that core's scratchpad. RTT's global descriptor must stay in the
 * SEGGER implementation; never reinitialize RTT while tracing is active.
 * These exported symbols also expose final sequence/drop counts to a debugger.
 */
struct trace_core_state {
    uint64_t last_timestamp;
    uint64_t last_sync;
    uint64_t session;
    uint64_t epoch;       /* core0 publication, read only when making a SYNC */
    uint32_t epoch_valid; /* release/acquire publication of the immutable epoch */
    uint32_t initialized;
    uint32_t enabled;
    uint32_t current_task;
    uint32_t sequence;
    uint32_t dropped;
    uint32_t since_sync;
    uint32_t need_sync;
    uint32_t packet[TRACE_PACKET_WORDS];
    uint8_t rtt[TRACE_RTT_BUFFER_SIZE];
};
/* Match the default SDK stacks: core0 in scratch Y, core1 in scratch X. */
struct trace_core_state __scratch_y("rt_trace_core0") trace_state_core0;
struct trace_core_state __scratch_x("rt_trace_core1") trace_state_core1;
/* Trace state starts at the bottom; the same core's stack grows from the top.
 * Reserve stack space; custom stacks/other scratch data need a link check. */
_Static_assert(TRACE_SCRATCH_RESERVED_BYTES <= 4096, "invalid scratch reservation");
_Static_assert(sizeof(struct trace_core_state) <= 4096 - TRACE_SCRATCH_RESERVED_BYTES,
               "trace state exceeds scratch budget; reduce RTT buffer/name size");
_Static_assert(TRACE_SYNC_WORDS + 2 + TRACE_MAX_PAYLOAD_WORDS <= TRACE_PACKET_WORDS,
               "trace packet workspace too small");

static inline struct trace_core_state *trace_local(void) {
    return get_core_num() == 0 ? &trace_state_core0 : &trace_state_core1;
}

/* Must be called with local interrupts masked. The entire packet is committed
 * by one skip-or-write operation. In particular a lost SYNC never consumes a
 * timestamp delta. SEGGER_RTT_WriteSkipNoLock returns BOOLEAN, not byte count.
 */
static void __not_in_flash_func(trace_write_locked)(struct trace_core_state *state,
        uint32_t type, uint32_t task, const uint32_t *payload, uint32_t words,
        uint64_t now) {
    uint64_t delta = now - state->last_timestamp;
    uint32_t *out = state->packet;
    uint32_t sequence = state->sequence++;
    uint32_t sync = state->need_sync || delta > UINT16_MAX ||
                    state->since_sync >= TRACE_SYNC_INTERVAL_EVENTS ||
                    now - state->last_sync >= TRACE_SYNC_INTERVAL_US ||
                    type == TRACE_TIME_ZERO || type == TRACE_END;
    if (task == TRACE_CURRENT_TASK) task = state->current_task;
    if (sync) {
        uint64_t epoch = TRACE_EPOCH_UNSET;
        if (__atomic_load_n(&trace_state_core0.epoch_valid, __ATOMIC_ACQUIRE)) {
            epoch = trace_state_core0.epoch;
        }
        *out++ = TRACE_SYNC_MAGIC;
        *out++ = TRACE_FORMAT_VERSION | ((uint32_t)get_core_num() << 8) |
                 (2u << 16) | (TRACE_ENABLE_IRQ_TRACE ? TRACE_SYNC_IRQ_ENABLED : 0u) |
                 ((uint32_t)(8u * sizeof(TickType_t)) << 24);
        *out++ = (uint32_t)now;
        *out++ = (uint32_t)(now >> 32);
        *out++ = sequence;
        *out++ = state->dropped;
        *out++ = (uint32_t)epoch;
        *out++ = (uint32_t)(epoch >> 32);
        *out++ = (uint32_t)state->session;
        *out++ = (uint32_t)(state->session >> 32);
        *out++ = state->current_task;
        *out++ = (uint32_t)configTICK_RATE_HZ;
        delta = 0;
    }
    *out++ = (uint32_t)(delta << 16) | ((task < 255 ? task : 255u) << 8) | type;
    if (task >= 255) *out++ = task;
    /* words is bounded at the public entry point. Compiler uses a short
     * memcpy; this is never a string scan or an unbounded queue operation. */
    if (words) {
        memcpy(out, payload, words * sizeof(uint32_t));
        out += words;
    }
    if (SEGGER_RTT_WriteSkipNoLock(get_core_num() + 1u, state->packet,
                                   (unsigned)((out - state->packet) * 4u))) {
        state->last_timestamp = now;
        state->need_sync = 0;
        if (sync) {
            state->last_sync = now;
            state->since_sync = 0;
        }
        ++state->since_sync;
    } else {
        ++state->dropped;
        state->need_sync = 1;
    }
}

void trace_init(void) {
    /* SDK preinit runs on core0, before constructors/main/core1. This is not a
     * concurrent reset API. Calling again after initialization is a no-op. */
    if (__atomic_load_n(&trace_state_core0.initialized, __ATOMIC_ACQUIRE)) return;
    if (get_core_num() != 0) return;
    memset(&trace_state_core0, 0, sizeof(trace_state_core0));
    memset(&trace_state_core1, 0, sizeof(trace_state_core1));
    if (SEGGER_RTT_ConfigUpBuffer(1, "RT-Trace core0", trace_state_core0.rtt,
            sizeof(trace_state_core0.rtt), SEGGER_RTT_MODE_NO_BLOCK_SKIP) < 0 ||
        SEGGER_RTT_ConfigUpBuffer(2, "RT-Trace core1", trace_state_core1.rtt,
            sizeof(trace_state_core1.rtt), SEGGER_RTT_MODE_NO_BLOCK_SKIP) < 0) return;
    uint64_t session = timer_time_us_64(timer0_hw);
    trace_state_core0.session = trace_state_core1.session = session;
    trace_state_core0.need_sync = trace_state_core1.need_sync = 1;
    trace_state_core0.enabled = trace_state_core1.enabled = 1;
    __atomic_store_n(&trace_state_core1.initialized, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&trace_state_core0.initialized, 1, __ATOMIC_RELEASE);
}
PICO_RUNTIME_INIT_FUNC(trace_init, "09999");

void __not_in_flash_func(trace_event)(uint32_t type, uint32_t task, const uint32_t *payload, uint32_t words) {
    uint32_t irq = save_and_disable_interrupts();
    struct trace_core_state *state = trace_local();
    if (__atomic_load_n(&state->initialized, __ATOMIC_ACQUIRE) && state->enabled &&
        type >= TRACE_SWITCH_IN && type <= TRACE_IDLE_TASK &&
        words <= TRACE_MAX_PAYLOAD_WORDS && (words == 0 || payload != NULL)) {
        trace_write_locked(state, type, task, payload, words, timer_time_us_64(timer0_hw));
    }
    restore_interrupts(irq);
}

void __not_in_flash_func(trace_switch)(uint32_t task, uint32_t entering) {
    uint32_t irq = save_and_disable_interrupts();
    struct trace_core_state *state = trace_local();
    if (__atomic_load_n(&state->initialized, __ATOMIC_ACQUIRE) && state->enabled) {
        /* Update even if RTT is full, so later SYNC reflects actual execution. */
        if (entering) state->current_task = task;
        trace_write_locked(state, entering ? TRACE_SWITCH_IN : TRACE_SWITCH_OUT,
                           task, NULL, 0, timer_time_us_64(timer0_hw));
        if (!entering) state->current_task = 0;
    }
    restore_interrupts(irq);
}

void trace_task_create(uint32_t task, uintptr_t handle, uint32_t priority, const char *name) {
    /* Creation is rare. Bounded transient stack copy; no strlen/strcpy or
     * heap storage. The persistent serialization packet is in scratch. */
    uint32_t irq = save_and_disable_interrupts();
    struct trace_core_state *state = trace_local();
    if (__atomic_load_n(&state->initialized, __ATOMIC_ACQUIRE) && state->enabled) {
        uint32_t length = 0;
        if (name) {
            while (length < TRACE_NAME_MAX && length < configMAX_TASK_NAME_LEN && name[length]) ++length;
        }
        uint32_t name_words = (length + 3u) / 4u;
        uint32_t data[3u + (TRACE_NAME_MAX + 3u) / 4u];
        data[0] = (uint32_t)handle;
        data[1] = priority;
        data[2] = length;
        if (name_words) {
            memset(data + 3, 0, name_words * 4u);
            memcpy(data + 3, name, length);
        }
        trace_write_locked(state, TRACE_TASK_CREATE, task, data, 3u + name_words,
                           timer_time_us_64(timer0_hw));
    }
    restore_interrupts(irq);
}

void __not_in_flash_func(trace_syscall)(uint32_t kind, uintptr_t object, uint32_t timeout,
                   uint32_t arg0, uint32_t arg1, uintptr_t caller) {
    const uint32_t data[] = {kind, (uint32_t)object, timeout, arg0, arg1, (uint32_t)caller};
    trace_event(TRACE_SYSCALL_ENTER, TRACE_CURRENT_TASK, data, 6);
}

void trace_timeZero(void) {
    uint32_t irq = save_and_disable_interrupts();
    struct trace_core_state *state = trace_local();
    if (__atomic_load_n(&state->initialized, __ATOMIC_ACQUIRE) && state->enabled &&
        !__atomic_load_n(&trace_state_core0.epoch_valid, __ATOMIC_ACQUIRE)) {
        /* Contract: one application caller, once. Publish even if RTT is full;
         * subsequent SYNCs on either core repeat the epoch. */
        uint64_t now = timer_time_us_64(timer0_hw);
        trace_state_core0.epoch = now;
        __atomic_store_n(&trace_state_core0.epoch_valid, 1, __ATOMIC_RELEASE);
        const uint32_t data[] = {(uint32_t)now, (uint32_t)(now >> 32)};
        trace_write_locked(state, TRACE_TIME_ZERO, TRACE_CURRENT_TASK, data, 2, now);
    }
    restore_interrupts(irq);
}

void trace_stop(void) {
    uint32_t irq = save_and_disable_interrupts();
    struct trace_core_state *state = trace_local();
    if (__atomic_load_n(&state->initialized, __ATOMIC_ACQUIRE) && state->enabled) {
        trace_write_locked(state, TRACE_END, TRACE_CURRENT_TASK, NULL, 0,
                           timer_time_us_64(timer0_hw));
        state->enabled = 0;
        /* A dropped END is visible in the exported state. Host must drain and
         * inspect final counters if no END record arrived, not assume clean EOF. */
    }
    restore_interrupts(irq);
}

void trace_irq_enter(void) {
    trace_e1(TRACE_IRQ_ENTER, 0, __get_current_exception());
}
