# RP2350 target tracing

Uses the kernel at `/Users/mabecker/Code/ES-Lab-Kit/Software/FreeRTOS-Kernel`
(version string `V11.1.0+`) and Pico SDK 2.3.1. ARM build/link verified; no board measurements yet.

1. **Start with a working Pico 2 SDK project**, using the Arm cores. Have the
   FreeRTOS kernel and this repository available locally. SEGGER RTT is already
   included in the SDK; no separate download is needed.

2. **Update `CMakeLists.txt`.** Keep any generated VS Code SDK/toolchain setup
   above the SDK import. Adjust the two local paths and your source filenames:

   ```cmake
   cmake_minimum_required(VERSION 3.13)
   include(pico_sdk_import.cmake)

   set(FREERTOS_KERNEL_PATH "/Users/mabecker/Code/ES-Lab-Kit/Software/FreeRTOS-Kernel")
   set(FREERTOS_CONFIG_FILE_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}")
   include("${FREERTOS_KERNEL_PATH}/portable/ThirdParty/GCC/RP2040/FreeRTOS_Kernel_import.cmake")

   project(my_firmware C CXX ASM)
   set(CMAKE_C_STANDARD 11)
   set(CMAKE_CXX_STANDARD 17)
   pico_sdk_init()

   add_executable(my_firmware main.c)
   target_include_directories(my_firmware PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
   target_link_libraries(my_firmware PRIVATE pico_stdlib FreeRTOS-Kernel-Heap4)

   add_subdirectory("/Users/mabecker/Code/RT_Trace_Experimental/target" rt-trace)
   rt_trace_enable(my_firmware)
   pico_add_extra_outputs(my_firmware)
   ```

   Import FreeRTOS **before `project()`**; add tracing **after `pico_sdk_init()`**.
   Despite its `RP2040` filename, this kernel's import selects the RP2350 port.
   Keep your existing executable name instead of `my_firmware`, if preferred.

3. **Create `FreeRTOSConfig.h` beside `CMakeLists.txt`.** This starting
   configuration enables two cores, 32-bit ticks, mutexes and notifications:

   ```c
   #ifndef FREERTOS_CONFIG_H
   #define FREERTOS_CONFIG_H

   #include <assert.h>

   #define configCPU_CLOCK_HZ                       150000000u
   #define configTICK_RATE_HZ                       1000
   #define configTICK_TYPE_WIDTH_IN_BITS            TICK_TYPE_WIDTH_32_BITS
   #define configMAX_PRIORITIES                     8
   #define configMINIMAL_STACK_SIZE                 512
   #define configMAX_TASK_NAME_LEN                  16
   #define configUSE_PREEMPTION                     1
   #define configUSE_TIME_SLICING                   1
   #define configUSE_TICKLESS_IDLE                  0
   #define configUSE_IDLE_HOOK                      0
   #define configUSE_PASSIVE_IDLE_HOOK              0
   #define configUSE_TICK_HOOK                      0

   #define configNUMBER_OF_CORES                    2
   #define configTICK_CORE                          0
   #define configRUN_MULTIPLE_PRIORITIES            1
   #define configUSE_CORE_AFFINITY                   1
   #define configSUPPORT_PICO_SYNC_INTEROP          1
   #define configSUPPORT_PICO_TIME_INTEROP          1
   #define configNUM_THREAD_LOCAL_STORAGE_POINTERS  5

   #define configSUPPORT_DYNAMIC_ALLOCATION         1
   #define configSUPPORT_STATIC_ALLOCATION          1
   #define configKERNEL_PROVIDED_STATIC_MEMORY      1
   #define configTOTAL_HEAP_SIZE                    (64 * 1024)
   #define configUSE_MUTEXES                        1
   #define configUSE_RECURSIVE_MUTEXES              1
   #define configUSE_COUNTING_SEMAPHORES            1
   #define configUSE_TASK_NOTIFICATIONS             1
   #define configUSE_TIMERS                         1
   #define configTIMER_TASK_PRIORITY                3
   #define configTIMER_QUEUE_LENGTH                 10
   #define configTIMER_TASK_STACK_DEPTH             512

   #define configENABLE_MPU                         0
   #define configENABLE_TRUSTZONE                   0
   #define configRUN_FREERTOS_SECURE_ONLY           1
   #define configENABLE_FPU                         1
   #define configMAX_SYSCALL_INTERRUPT_PRIORITY     0x10
   #define configASSERT(x)                          assert(x)
   #define INCLUDE_vTaskDelay                       1
   #define INCLUDE_xTaskDelayUntil                  1
   #define INCLUDE_vTaskSuspend                     1
   #define INCLUDE_xTaskGetSchedulerState           1
   #define INCLUDE_xTimerPendFunctionCall           1

   #define configUSE_TRACE_FACILITY                 1
   #define TRACE_ENABLE_IRQ_TRACE                   0
   #include "trace.h"  /* Last include, before the closing guard. */
   #endif
   ```

   If FreeRTOS is already configured, retain its application settings; enable
   two cores and tracing, use 16/32-bit ticks, and add the final `trace.h` include.
   Remove any earlier trace-hook definitions. `configCPU_CLOCK_HZ` must match
   the system clock (150 MHz by default). IRQ tracing is optional; see below.

4. **Run application work as FreeRTOS tasks.** For a first trace, use this
   `main.c`; replace the busy work with your application later:

   ```c
   #include "pico/stdlib.h"
   #include "FreeRTOS.h"
   #include "task.h"
   #include "trace.h"

   static void periodic(void *argument) {
       const TickType_t period = pdMS_TO_TICKS((uintptr_t)argument);
       TickType_t wake = xTaskGetTickCount();
       for (;;) {
           busy_wait_us_32(200);
           xTaskDelayUntil(&wake, period);
       }
   }

   int main(void) {
       stdio_init_all();
       traceTimeZero();
       if (xTaskCreate(periodic, "Control", 512, (void *)(uintptr_t)10, 2, NULL) != pdPASS ||
           xTaskCreate(periodic, "Worker", 512, (void *)(uintptr_t)20, 1, NULL) != pdPASS) {
           panic("Task allocation failed");
       }
       vTaskStartScheduler();
       panic("Scheduler could not start");
   }
   ```

   Tracing initializes before `main()`. `traceTimeZero()` is the only added
   application annotation; put it at your chosen origin, once. Jobs are inferred
   from syscalls such as `xTaskDelayUntil()`. FreeRTOS launches core 1: remove any
   existing `multicore_launch_core1()` call.

5. **Configure and build**, using a fresh build directory:

   ```sh
   cmake -S . -B build-trace -DPICO_BOARD=pico2 -DPICO_PLATFORM=rp2350-arm-s -DCMAKE_BUILD_TYPE=RelWithDebInfo
   cmake --build build-trace --parallel
   ```

   Use your normal SDK/toolchain environment. The output is
   `build-trace/my_firmware.elf` and `.uf2`. Keep the ELF, including its symbols,
   for recording and caller identification.

6. **Flash and read RTT through SWD.** Channel 1 carries core 0; channel 2 carries
   core 1. Let `trace_init()` return before a reader examines the channels;
   they are ready at `main()`. Save binary bytes as `raw_buffer0.txt` and
   `raw_buffer1.txt` in one recording folder. Preserve each stream's order;
   do not decode it as text.
   Copy the matching ELF there as `firmware.elf`. A USB serial connection alone
   does not read RTT. Continue with the host parser instructions below.

The supplied Pico FreeRTOS port compiles kernel sources in the firmware target.
If your kernel is a separate compiled target, give it this header's include path too.

CMake builds the SDK's `SEGGER_RTT.c` into `librt_trace_rtt.a`, selects three up
channels and `RTT_USE_ASM=0`, and links the required SDK libraries. Trace hooks
compile with each firmware's FreeRTOS configuration. `rt_trace_enable` disables
SDK stdio RTT: `stdio_init_all()` would otherwise reset the trace channels.
Do not add another RTT implementation or reinitialize RTT. Channels 1/2 belong
to tracing; direct RTT debug writes may use channel 0.

RTT source redistribution is permitted: retain its copyright, condition and
disclaimer. Both the SDK copy (7.88c) and
[SEGGER's published RTT license](https://github.com/SEGGERMicro/RTT/blob/main/LICENSE.md)
allow source and binary redistribution.
This integration uses the installed SDK copy without vendoring SEGGER files.

Tracing initializes automatically before `main()`; no application `trace_init()`
call is needed. Call `traceTimeZero()` once, from one core. It records the exact
shared timer value. Repeated calls do nothing. Ordinary IRQs may trace; NMI and
HardFault must not.

Defaults allocate 1,536-byte RTT rings plus management/workspace: 1,760 bytes
in scratch Y for core 0 and scratch X for core 1, matching the default SDK
stacks. Trace state starts at each bank's bottom; its core's stack grows from
the top. Inspect the final linker map when adding scratch data/changing stacks.
`TRACE_SCRATCH_RESERVED_BYTES` defaults to 2,048.
Override settings before including `trace.h`. IRQ tracing is off by default;
set `TRACE_ENABLE_IRQ_TRACE=1` for the port's instrumented IRQ paths. This does
not automatically instrument every application ISR.

The wire protocol and payloads are specified in `trace.h`. Common switch records
are four bytes. Writes are immediate, nonblocking and all-or-nothing. Recovery
records repeat absolute time, sequence, cumulative losses, current task and t=0.
The writer uses local IRQ masking, no allocation, global locks or task lookup;
its hot functions execute from SRAM. Worst-case write size is bounded by the
configured maximum task-name length. No throughput/latency bound is claimed
without board measurements.

Syscall events contain arguments, block/result information and caller addresses.
The host infers jobs and can identify SDK/internal waits from caller symbols;
the target does not guess internal objects from creation time. Preserve the ELF.
Correlate calls by task across migration, suppress nested recursive-mutex calls,
and distinguish polling, timeouts, retries and within-job waits. Event-group
immediate success has no exact pre-dispatch hook in this kernel: its end event
provides an upper time bound, not necessarily the instant the condition matched.

`trace_stop()` ends only the calling core. Stop both producers through the
recorder/debugger, then drain both channels. If END is lost, inspect exported
`trace_state_core0/1` sequence/drop counters; EOF alone does not imply completion.

Host parsing uses two files:

1. `src/TraceEventsFreeRTOS.py`: binary files → Python events, clocks and gaps.
2. `src/TraceParserFreeRTOS.py`: events → `TraceTask` jobs and execution intervals.

```python
from TraceEventsFreeRTOS import read_recording
from TraceParserFreeRTOS import events_to_tasks

recording = read_recording("recording_folder", num_cores=2)
tasks = events_to_tasks(recording)
```

Stage 1 reads `raw_buffer<N>.txt` as binary; `recording.events` contains events
with `kind`, absolute `timestamp`, `core`, `sequence`, `task_id` and named `data`.
Stage 2 uses application-relative microseconds. Unknown/estimated jobs are
flagged and excluded from completion statistics; unknown CPU coverage has its
own row. The GUI uses this parser; it accepts only format-3 recordings.
Reloading format-3 traces regenerates their CSV exports to reflect policy changes.
Mutex take markers use call-entry time: black for success, red/crossed for failure,
gray for unknown outcomes. Ownership intervals retain actual grant timestamps.

Optional `job_policy.json` in the recording folder overrides syscall assumptions;
for example `{"task_boundaries": {"Control": ["DELAY_UNTIL"]}}`.
Save the matching ELF as `firmware.elf` beside the recording, or pass `elf_path`
to `events_to_tasks`, for automatic SDK/internal caller exclusions. Without it,
use policy overrides for internal waits. No application job annotations are needed.

Parser checks: `python3 -B -m unittest discover -s tests -p 'test_parser_freertos.py'`.

Writer checks: `python3 tests/test_target.py` (host Clang, ASan/UBSan). If the
installed macOS SDK is newer than the linker, select a compatible `SDKROOT`;
checks here used `/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`.
