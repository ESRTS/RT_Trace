# Target: FreeRTOS hooks → RTT bytes

Sources: [trace.h](../target/trace.h), [trace.c](../target/trace.c),
[CMakeLists.txt](../target/CMakeLists.txt). Output feeds [stage 1](02-binary-parser.md).

**Boundary.** RP2350, Pico SDK, two FreeRTOS SMP cores, 32-bit pointers and
16/32-bit ticks. Hooks record kernel facts; the host infers jobs.
Application annotation: `traceTimeZero()` once, from one core.

**Build/lifecycle.** `rt_trace_enable(firmware)` compiles hooks with the firmware's
FreeRTOS configuration, builds the SDK's RTT source, and disables SDK stdio RTT.
Include `trace.h` last in `FreeRTOSConfig.h`. SDK preinitialization calls
`trace_init()` on core 0 before `main()`/core 1. It configures both channels and
publishes initialization with release/acquire atomics. Readers must wait for its
return. Never reset RTT while tracing. `trace_stop()` stops only its calling core;
halt both producers and drain both channels to finish a capture.

**State/write path.**

- Core 0: `trace_state_core0`, scratch Y, RTT channel 1. Core 1: scratch X,
  channel 2. Default ring: 1,536 bytes; state plus ring: 1,760 bytes per bank.
  Stacks share these banks; preserve the scratch reservation and check the map.
- Local IRQ masking serializes each core's task/ISR writers and restores the
  previous mask. No allocation, global trace lock or task lookup. NMI/HardFault
  cannot trace. Hot entry points/serialization use `__not_in_flash_func`.
- Sample `timer_time_us_64(timer0_hw)` inside the mask. Serialize into the
  core-local workspace; commit once with `SEGGER_RTT_WriteSkipNoLock` (Boolean).
  Failure drops the whole packet, increments drops, and requests synchronization.
  Advance the timestamp baseline only after success; advance sequence on every
  attempted event. Update running-task state even when its switch record is lost.

**Wire contract.** `trace.h` specifies format 3 and payloads: little-endian words,
four-byte event header, extended task-ID escape, optional 48-byte SYNC immediately
followed by a complete zero-delta event. IDs use `uxTCBNumber`; handles identify
objects separately. Only tasks carry names. SYNC repeats absolute time, session/configuration, sequence,
drops, running task and application epoch. It appears initially, after loss,
delta overflow, periodic traffic, time zero and END. Epoch publication survives a
dropped TIME_ZERO. No checksum or batching.

**Change together.** Append event IDs; keep existing numeric meanings. Update
writer bounds, [stage-1 schemas](../src/TraceEventsFreeRTOS.py), stage-2 semantics,
fixtures and [writer tests](../tests/test_target.py). Incompatible layouts require
a format-version change in target and decoder. Kernel upgrades require checking
hook placement/internal variables. IRQ tracing requires instrumentation of every
relevant ISR; scheduler hooks cover only switch-out → switch-in. Hardware timing
and throughput remain unmeasured.
