"""Run the real target writer with mocked hardware/RTT (no external packages).

python3 tests/test_target.py
The host build omits only the 32-bit pointer assertion; the RP2350 cross-build
checks it. All wire words, bounds and writer logic are the production code.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

STUBS = {
    'FreeRTOS.h': '''#include <stdint.h>
#define configNUMBER_OF_CORES 2
#define configUSE_TRACE_FACILITY 1
#define configTICK_RATE_HZ 1000
#define configMAX_TASK_NAME_LEN 64
#define PICO_RP2350 1
typedef uint32_t TickType_t;
''',
    'task.h': '',
    'pico/platform.h': '''#define __scratch_x(name)
#define __scratch_y(name)
#define __not_in_flash_func(name) name
extern unsigned mock_core;
static inline unsigned get_core_num(void) { return mock_core; }
static inline unsigned __get_current_exception(void) { return 15; }
''',
    'pico/runtime.h': '#define PICO_RUNTIME_INIT_FUNC(fn, priority)\n',
    'hardware/sync.h': '''extern unsigned mock_mask;
static inline unsigned save_and_disable_interrupts(void) {
    unsigned old = mock_mask; mock_mask = 1; return old;
}
static inline void restore_interrupts(unsigned old) { mock_mask = old; }
''',
    'hardware/timer.h': '''extern uint64_t mock_time;
#define timer0_hw ((void *)0)
static inline uint64_t timer_time_us_64(void *timer) { (void)timer; return mock_time; }
''',
    'SEGGER_RTT.h': '''#define SEGGER_RTT_MAX_NUM_UP_BUFFERS 3
#define SEGGER_RTT_MODE_NO_BLOCK_SKIP 0
int SEGGER_RTT_ConfigUpBuffer(unsigned, const char *, void *, unsigned, unsigned);
unsigned SEGGER_RTT_WriteSkipNoLock(unsigned, const void *, unsigned);
''',
}

HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "writer.c"
unsigned mock_core, mock_mask;
uint64_t mock_time;
static unsigned fail, writes[3], used[3], last_channel, last_size;
static uint32_t last[64];
static FILE *record_files[3];

int SEGGER_RTT_ConfigUpBuffer(unsigned ch, const char *name, void *buf, unsigned size, unsigned flags) {
    assert(ch == 1 || ch == 2);
    assert(name && buf && size == TRACE_RTT_BUFFER_SIZE);
    assert(flags == SEGGER_RTT_MODE_NO_BLOCK_SKIP);
    return 0;
}
unsigned SEGGER_RTT_WriteSkipNoLock(unsigned ch, const void *buf, unsigned size) {
    assert(mock_mask == 1);
    assert(ch == mock_core + 1 && size <= sizeof(last) && size % 4 == 0);
    last_channel = ch;
    last_size = size;
    memcpy(last, buf, size);
    if (fail || used[ch] + size >= TRACE_RTT_BUFFER_SIZE) return 0;
    used[ch] += size;
    ++writes[ch];
    if (record_files[ch]) assert(fwrite(buf, 1, size, record_files[ch]) == size);
    return 1;  /* Actual SEGGER API returns boolean. */
}
static uint64_t pair(unsigned offset) {
    return (uint64_t)last[offset] | ((uint64_t)last[offset+1] << 32);
}
static unsigned has_sync(void) { return last[0] == TRACE_SYNC_MAGIC; }
static unsigned event_offset(void) { return has_sync() ? 12 : 0; }
static unsigned event_type(void) { return last[event_offset()] & 255; }
static unsigned event_task(void) { return (last[event_offset()] >> 8) & 255; }
static unsigned event_delta(void) { return last[event_offset()] >> 16; }
static void drain(void) { used[1] = used[2] = 0; }
static void check_names(void) {
    char name[80];
    memset(name, 'A', sizeof(name));
    for (unsigned length = 0; length <= 64; ++length) {
        name[length] = 0;
        drain();
        trace_task_create(7, 0x20001234, 9, name);
        unsigned i = event_offset();
        unsigned bounded = length < TRACE_NAME_MAX ? length : TRACE_NAME_MAX;
        assert(event_type() == TRACE_TASK_CREATE && event_task() == 7);
        assert(last[i+1] == 0x20001234 && last[i+2] == 9 && last[i+3] == bounded);
        assert(last_size / 4 == i + 4 + (bounded+3)/4);
        const unsigned char *bytes = (const unsigned char *)(last+i+4);
        for (unsigned j=0; j<bounded; ++j) assert(bytes[j] == 'A');
        for (unsigned j=bounded; j<(bounded+3)/4*4; ++j) assert(bytes[j] == 0);
        name[length] = 'A';
    }
}
int main(void) {
    if (getenv("RT_TRACE_FIXTURE")) {
        char filename[1024];
        for (unsigned ch=1; ch<=2; ++ch) {
            snprintf(filename, sizeof(filename), "%s/raw_buffer%u.txt", getenv("RT_TRACE_FIXTURE"), ch-1);
            record_files[ch] = fopen(filename, "wb");
            assert(record_files[ch]);
        }
        mock_time = 9000000;
        trace_init();
        trace_task_create(300, 0x20001234, 3, "worker");
        trace_e1(TRACE_READY, 300, 0);
        trace_switch(300, 1);
        mock_time += 37;
        trace_timeZero();
        mock_time += 10;
        trace_syscall(TRACE_CALL_DELAY, 0, 5, 0, 0, 0);
        trace_e1(TRACE_WAKE_TARGET, TRACE_CURRENT_TASK, 5);
        trace_switch(300, 0);
        for (unsigned tick=1; tick<=5; ++tick) {
            mock_time = 9000000 + tick*1000;
            trace_e2(TRACE_TICK, 0, tick-1, 0);
        }
        trace_e1(TRACE_READY, 300, 5);
        mock_time += 10;
        trace_switch(300, 1);
        traceRETURN_vTaskDelay();
        mock_time = 9005037;
        trace_stop();
        mock_core = 1;
        trace_stop();
        fclose(record_files[1]);
        fclose(record_files[2]);
        return 0;
    }
    /* No RTT access before init. Startup is already beyond the old delta limit. */
    mock_time = UINT64_C(9000000000);
    trace_e0(TRACE_SWITCH_IN, 7);
    assert(writes[1] == 0 && mock_mask == 0);
    trace_init();
    trace_switch(7, 1);
    assert(has_sync() && pair(2) == mock_time && last[4] == 0 && last[5] == 0);
    assert(pair(6) == UINT64_MAX && pair(8) == mock_time && last[10] == 7);
    assert(last_size == 52 && event_task() == 7 && event_delta() == 0);
    assert((last[1] & 0xff) == 3 && (last[1] >> 24) == 32 && last[11] == 1000);

    mock_time += 10;
    trace_e0(TRACE_SWITCH_OUT, 7);
    assert(!has_sync() && last_size == 4 && event_delta() == 10);
    mock_time += 10;
    trace_e0(TRACE_SWITCH_IN, 300);
    assert(!has_sync() && last_size == 8 && event_task() == 255 && last[1] == 300);

    /* Context, syscall data, ticks and outcomes survive migration between streams. */
    trace_syscall(TRACE_CALL_DELAY_UNTIL, 0, 0, UINT32_MAX-3, 10, 0x10001234);
    assert(event_type() == TRACE_SYSCALL_ENTER && last_size == 28);
    assert(event_task() == 7 && last[1] == TRACE_CALL_DELAY_UNTIL);
    assert(last[4] == UINT32_MAX-3 && last[5] == 10 && last[6] == 0x10001234);
    mock_core = 1;
    trace_switch(7, 1);
    assert(has_sync() && last_channel == 2 && ((last[1] >> 8) & 255) == 1);
    assert(pair(8) == trace_state_core0.session);
    TRACE_RETURN(TRACE_CALL_DELAY_UNTIL, 0);  /* late, no-block result */
    assert(event_task() == 7 && event_type() == TRACE_SYSCALL_RETURN && last[2] == 0);
    trace_e1(TRACE_READY, 300, UINT32_MAX);
    assert(event_task() == 255 && last[1] == 300 && last[2] == UINT32_MAX);

    /* Long silence never wraps; masked callers remain masked. */
    drain();
    mock_time += UINT64_C(20000000);
    mock_mask = 1;
    trace_e0(TRACE_SWITCH_OUT, 7);
    assert(mock_mask == 1 && has_sync() && pair(2) == mock_time && event_delta() == 0);
    mock_mask = 0;
    uint64_t committed = trace_state_core1.last_timestamp;
    fail = 1;
    mock_time += UINT64_C(40000000);
    trace_e0(TRACE_SWITCH_IN, 7);
    mock_time += 1;
    trace_e0(TRACE_SWITCH_OUT, 7);
    assert(trace_state_core1.last_timestamp == committed && trace_state_core1.dropped == 2);
    fail = 0;
    trace_e0(TRACE_SWITCH_IN, 7);
    assert(has_sync() && pair(2) == mock_time && last[5] == 2);
    assert(last[4] == trace_state_core1.sequence - 1);

    /* The ring really fills; failed writes do not advance committed state. */
    drain();
    unsigned before = trace_state_core1.dropped;
    for (unsigned i=0; i<600; ++i) trace_e0(TRACE_SWITCH_OUT, 7);
    assert(trace_state_core1.dropped > before);
    drain();
    trace_e0(TRACE_SWITCH_IN, 7);
    assert(has_sync() && last[5] == trace_state_core1.dropped);

    /* A lost t=0 is recoverable from either stream; origin is never tick-snapped. */
    fail = 1;
    mock_time += 37;
    trace_timeZero();
    uint64_t epoch = mock_time;
    assert(trace_state_core0.epoch_valid && trace_state_core0.epoch == epoch);
    fail = 0;
    mock_core = 0;
    drain();
    mock_time += 2000000;
    trace_e0(TRACE_SWITCH_OUT, 7);
    assert(has_sync() && pair(6) == epoch);
    unsigned count = writes[1];
    trace_timeZero();
    trace_init();
    assert(writes[1] == count && trace_state_core0.epoch == epoch);

    check_names();
    /* Periodic sequence anchors and counter wrap. */
    drain();
    trace_state_core0.need_sync = 1;
    trace_state_core0.sequence = UINT32_MAX;
    trace_e0(TRACE_SWITCH_OUT, 7);
    assert(has_sync() && last[4] == UINT32_MAX && trace_state_core0.sequence == 0);
    for (unsigned i=0; i<TRACE_SYNC_INTERVAL_EVENTS; ++i) {
        drain(); trace_e0(TRACE_SWITCH_IN, 7);
    }
    assert(has_sync() && last[4] == TRACE_SYNC_INTERVAL_EVENTS-1);

    /* Stop affects only the calling core. Missing END remains inspectable. */
    mock_core = 1;
    drain();
    fail = 1;
    before = trace_state_core1.dropped;
    trace_stop();
    assert(!trace_state_core1.enabled && trace_state_core1.dropped == before+1);
    count = trace_state_core1.sequence;
    trace_e0(TRACE_SWITCH_IN, 7);
    assert(trace_state_core1.sequence == count);
    mock_core = 0;
    fail = 0;
    trace_stop();
    assert(has_sync() && event_type() == TRACE_END && trace_state_core0.enabled == 0);
    assert(mock_mask == 0);
    puts("target writer: all protocol, bounds and loss checks passed");
}
'''


class TargetWriter(unittest.TestCase):
    def test_protocol(self):
        with tempfile.TemporaryDirectory(prefix='rttrace-test-') as directory:
            temp = Path(directory)
            for name, content in STUBS.items():
                path = temp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            source = (ROOT / 'target/trace.c').read_text()
            pointer_assert = '_Static_assert(sizeof(uintptr_t) == 4, "target requires 32-bit pointers");'
            self.assertEqual(source.count(pointer_assert), 1)
            (temp / 'writer.c').write_text(source.replace(pointer_assert, '/* host pointers are wider */'))
            (temp / 'main.c').write_text(HARNESS)
            for name_max in (1, 32, 64):
                subprocess.run([
                    'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', f'-DTRACE_NAME_MAX={name_max}',
                    '-I', str(temp), '-I', str(ROOT / 'target'),
                    str(temp / 'main.c'), '-o', str(temp / 'check'),
                ], check=True)
                subprocess.run([str(temp / 'check')], check=True)
                subprocess.run([str(temp / 'check')], check=True,
                               env=dict(os.environ, RT_TRACE_FIXTURE=str(temp)))
                sys.path.insert(0, str(ROOT / 'src'))
                from TraceEventsFreeRTOS import read_recording
                from TraceParserFreeRTOS import events_to_tasks
                decoded = read_recording(temp, 2, strict=True)
                self.assertEqual(decoded.epoch, 9000037)
                task = next(t for t in events_to_tasks(decoded, strict=True) if t.id == 300)
                self.assertEqual(task.name, 'worker'[:name_max])
                self.assertEqual(task.jobs[0].completionTime, 10)
                self.assertEqual(task.jobs[0].getExecutionTime(), 47)
                self.assertEqual(task.jobs[0].deadline, 4963)
                self.assertEqual(task.jobs[1].releaseTime, 4963)


if __name__ == '__main__':
    unittest.main()
