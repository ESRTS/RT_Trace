# Jetson: Nsight Systems export → tasks/jobs

Sources: [TraceParserJetson.py](../src/TraceParserJetson.py). Tests:
[test_parser_jetson.py](../tests/test_parser_jetson.py), reference trace
[examples/jetsonThor](../examples/jetsonThor).

**Boundary.** Nsight Systems (`nsys`, tested 2025.6.1, export schema 3.24)
records on the Jetson; RT-Trace reads its SQLite export (`nsys export --type
sqlite`) with Python's `sqlite3`. Recording from RT-Trace is not implemented.
Only the process tree of the profiled application is supported
(`--cpuctxsw=process-tree`, needs `perf_event_paranoid <= 1`); system-wide
context switches of other processes are ignored with a warning.

**Stage 1** (`read_export`): tables → `Export` with times on the trace clock:
`SCHED_EVENTS` (switch in/out per CPU; `Terminated` gives the thread exit),
`NVTX_EVENTS` of the domain `rt-trace` (marks, push/pop or start/end ranges) and
thread names, `CUPTI_ACTIVITY_KIND_RUNTIME` (CUDA calls by correlation id),
`KERNEL`/`MEMCPY`/`MEMSET` (GPU operations per device and stream),
`SYNCHRONIZATION` (waits for the GPU), `DIAGNOSTIC_EVENT` (warnings).
`globalTid` holds the thread id in bits 0-23 and the process id in bits 24-47.

**NVTX contract.** The application names its task threads
(`nvtxNameOsThread`) and writes, in the domain `rt-trace`, the marks `period_ms`,
`priority`, `clock_sync`, `release` and `next_release` with an integer payload,
plus one range per job (payload: job id). Absolute times are
`CLOCK_MONOTONIC_RAW` ns: the trace clock runs on it, `CLOCK_MONOTONIC` drifts
about 7 ppm against it. The application sleeps on `CLOCK_MONOTONIC` and converts
each release when it logs it. Reference implementation: `trace.c` of the
GpuTasksExample (bpfTrace repository).

**Stage 2** (`build_rows`).

- Clock: offset = max(payload − trace time) over the `clock_sync` marks, the
  mark written with the least delay; the first one is late (NVTX initialises).
  Time zero = `release`. Without them: the recording start, releases = job starts
  (estimated), with warnings.
- Rows: threads of the processes with NVTX or CUDA activity. Named threads with
  NVTX jobs are tasks (`kind` `task`, sorted by priority); the others
  (`thread`) have one job per execution interval, like ISR rows. One `gpu` row
  per (device, stream).
- Jobs: the k-th range is job k, released at the k-th `next_release`,
  deadline = release + period (`deadlinesExplicit`). CPU intervals are clipped to
  the range. A release after the last job before the thread exits is a job that
  never ran; releases after the exit are dropped. Execution outside the ranges
  (start-up, between the end of a job and the next sleep) stays in the row as jobs
  with `outsideJob`, `incomplete` and `uncertain`, so statistics, CSVs and
  deadline checks ignore it.
- GPU: each operation is a job of its stream row, released at its launch call
  (`launchedBy`: the task row); interval core `GPU<device>` (the view uses the
  row color, CPU-core analyses skip it). It joins `gpuWork` of the job whose range
  contains the launch; `gpuWaits` lists the waits for the GPU of a job. The
  preemption analysis does not count pauses during these waits.
- Checks: switch pairs (repaired and counted), jobs starting before their
  release (a wrong clock offset), GPU work ending after its job, the schema version.

**Limits.** Releases are as exact as the application's clock conversion (a few
ns). The GPU timestamps on Thor are software instrumented (CUPTI legacy trace).
Blocking reasons and preemptions by other processes are not visible in the process
tree. No `trace_version` mark yet: a changed contract is not detected.
