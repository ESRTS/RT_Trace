# Stage 2: events → tasks/jobs

Sources: [TraceParserFreeRTOS.py](../src/TraceParserFreeRTOS.py),
[TraceTask.py](../src/TraceTask.py). Input: [stage-1 Recording](02-binary-parser.md).
Output: `list[TraceTask]` for visualization/export; binary decoding stays in stage 1.

**API.** `events_to_tasks(recording, policy=None, strict=False, elf_path=None)`
runs `_Converter`; `lastReport` exposes warnings, gaps, loss and origin.
`parser(buffers, ...)` wraps both stages. GUI `loadRecording` additionally loads
`job_policy.json` and `firmware.elf`/`trace.elf`, writes events and refreshes CSVs.
GUI imports are lazy. `lastReport` and model repair settings are module globals;
parallel conversions need isolation. Strict structural contradictions raise
`TraceInconsistency`; missing evidence remains explicitly flagged.

**Passes/state.** `_Converter.finish()` orchestrates:

1. `prepare`: collect task/idle/object metadata and per-task event indexes; pair
   syscall entry, block, wake, operation and return using per-task stacks, allowing
   migration/nesting. Match mutex entry to semaphore return; suppress nested job
   boundaries. GAP marks open calls uncertain and clears stacks.
2. `build_jobs`: apply `JobPolicy`, calculate completion/release/deadline and
   execution windows. `_TickClock` unwraps tick values against timestamps/rate;
   unsuspended TICK anchors are exact, jumps/READY fallback/interpolation estimated.
3. `consume`: maintain each core's running task, scheduler interval, IRQ stack and
   known/unknown state; attribute elapsed time before applying each event. Track
   mutex ownership globally by handle.
4. Finalize: close open observations, build mutex-attempt records, intersect
   ownership spans with job windows, and return rows containing jobs.

**Default job policy.** A qualifying syscall completes the current job at entry:

| Call | Next release |
| --- | --- |
| Delay-until, positive increment | Nominal previous-wake + increment tick, even when late/nonblocking. |
| Delay, positive ticks | Recorded wake tick; READY fallback is estimated. |
| Queue receive, non-mutex semaphore take, notification wait/take, event wait/sync; positive timeout | Final READY after blocking; otherwise successful operation. Return-only/event-group end timing is estimated. Timeout also releases a job. |

Other calls, polling and preemption keep the job. Only initial/qualifying wait
releases create jobs; ordinary READY resumes execution. One wait resolution
produces one job, including coalesced notifications. `JobPolicy` supports per-task
boundary sets, ignored objects and caller patterns. ELF symbols exclude internal
port/stream/timer waits; without symbols, provide policy overrides.

**Model invariants.** All output times subtract the exact epoch; missing epoch
uses the first event provisionally. `deadline[k] = release[k+1]`; late nominal
releases preserve overdue execution. Completion after the deadline is a miss;
equality is on time. Skipped periods must not create fictitious executed jobs.
Next-job execution starts no earlier than previous completion.
Completion (`completionTime`), observation extent
(`observedUntil`) and CPU intervals are distinct. Missing evidence, estimates,
loss, EOF and deletion retain `releaseKnown`, estimate, `uncertain`, `incomplete`
and `aborted` flags; incomplete/uncertain jobs are excluded from completion
statistics/CSV.

CPU attribution prioritizes unknown → innermost IRQ → scheduler → running task.
SWITCH_OUT/IN bounds scheduler time; IRQ_EXIT_SCHEDULE only requests scheduling.
Execution outside job windows goes to runtime rows. Synthetic scheduler/IRQ/runtime/
unknown rows use negative IDs; idle IDs come from metadata. No per-core EOF padding.
GAP invalidates ownership and affected jobs; CPU stays unknown until SWITCH_IN.
SYNC alone cannot restore lost call/IRQ state. Uninstrumented IRQ time remains
inside task/scheduler time.

Mutex attempts belong to tasks at call-entry time, with success `True/False/None`;
ownership belongs to jobs at actual grant/release times. Recursive implementation
calls produce no duplicate attempt; final QUEUE_SENT ends ownership. Split/open
spans preserve `continued`, `released`, `uncertain`; clipping is not a real give.

**Change together.** Policy changes affect job windows, deadlines and statistics;
CPU accounting must still retain execution outside jobs. Preserve
`deadlinesExplicit`, `mutexAttemptsExplicit` and the model flags consumed by
[TraceView](../src/TraceView.py)/[DataExport](../src/DataExport.py). Verify boundary,
migration, IRQ, loss, tick and mutex cases in [parser tests](../tests/test_parser_freertos.py).
