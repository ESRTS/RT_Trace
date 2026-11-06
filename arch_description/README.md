# RT-Trace architecture

Read these descriptions together with their linked sources/tests; update them
when contracts or assumptions change.

[Target](01-target.md) → per-core RTT bytes →
[stage 1](02-binary-parser.md) → `Recording`/events →
[stage 2](03-task-builder.md) → `TraceTask`/jobs → visualization/export.

**Goals and rationale.**

- Only `traceTimeZero()` annotates the application. Infer job boundaries from
  syscalls on the host, where policies can change without rebuilding firmware.
  A boundary completes the current job; the next release defines its deadline.
- Keep target overhead bounded and nonblocking: immediate whole-record writes,
  local IRQ masking, no allocation/global trace lock. Per-core scratch buffers
  avoid producer contention; the shared hardware timer aligns both timelines.
- Compact delta records reduce RTT traffic; absolute SYNC records restore framing,
  time and available state after loss/long gaps. Repeat the application epoch so
  losing TIME_ZERO does not lose synchronization.
- Separate binary validation from interpretation so captures can be decoded,
  inspected and reinterpreted independently. Preserve uncertainty and raw CPU
  coverage when jobs cannot be reconstructed reliably.

**Verified baseline.** RP2350 Arm, two-core FreeRTOS SMP, Pico SDK **2.3.1**
(bundled RTT **7.88c**), Arm GCC **14.2.Rel1**, wire format **3**. Kernel version
string: **V11.1.0+**; clean source revision:
`8b1389eb30b418249f947d27530ae931a1eeaf53`.
This records the tested combination, not a guarantee for other kernel revisions;
hooks depend on kernel internals. Integration: [target guide](../target/README.md).

**Limits.** Job inference needs application-specific policy when defaults split
work incorrectly; caller exclusions need matching ELF symbols or host overrides.
IRQ separation requires all relevant IRQs instrumented. Scheduler coverage is
bounded by switch hooks. ARM build/link and host tests are verified; board
captures, hardware overhead and sustained throughput remain unverified.

**Validation.** From the repository root:

```sh
python3 -B -m unittest discover -s tests
```

Covers decoding, conversion, visualization and C-writer/parser round trips.
Writer checks require Clang with ASan/UBSan; macOS may need a compatible `SDKROOT`
(see target guide). After target/hook changes, configure the guide's Pico 2
firmware and run from its project directory:

```sh
cmake --build build-trace --parallel
```

Check the scratch-bank map and trace initialization/RTT symbols. Protocol changes
must update both stages, versioning and fixtures. Hardware changes additionally
need real captures and overhead/loss measurements.
