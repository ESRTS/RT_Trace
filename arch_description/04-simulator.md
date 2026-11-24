# Simulator: task set → JSON-lines trace → tasks/jobs

Sources: [simulator/](../src/simulator), [SimulatorRecorder.py](../src/SimulatorRecorder.py),
[TraceParserSim.py](../src/TraceParserSim.py). Tests:
[test_simulator.py](../tests/test_simulator.py), [test_parser_sim.py](../tests/test_parser_sim.py).

**Boundary.** Independent of the FreeRTOS path: no wire format, no job inference.
The simulator knows every release, deadline and completion and writes them
explicitly; `TraceParserSim` builds `TraceTask`/`TraceJob`/`TraceInterval` rows
directly. Nothing is estimated, so `uncertain` stays false.

**Package.** `simulator/` has no GUI imports.

| File | Role |
| --- | --- |
| `model.py` | `taskset.json` validation; `Task`, `TaskSet`, `Job`, `Precedence` (rejects cycles). Integer µs; `TraceParserSim` converts to the ns of the trace model by the header's `time_unit`. |
| `engine.py` | Time, releases, execution accounting, dispatch; only file that advances time. |
| `policies/base.py` | `SchedulingPolicy`: `validate`, `on_release`, `on_complete`, `order`, `select`; `assign_ranked`. |
| `policies/fixed_priority.py` | `fp`: preemptive fixed priority with affinity. |
| `policies/edf.py` | `edf`: preemptive earliest deadline first with affinity. |
| `policies/__init__.py` | Registry; `taskset.json` `scheduler` names the policy. |
| `trace_format.py` | Format contract shared by writer and parser. |
| `trace_writer.py` | Header, task lines, events. |

**Engine.** Event-driven. Per step: release due jobs, ask the policy for
`{core: job}`, check it (one core per job, existing cores, affinity), stop every
job that loses or changes its core, then start the new ones; advance to the next
release or completion. Completions at a timestamp precede its releases. Jobs are
released before the horizon; completions at the horizon are kept; open jobs end
there as incomplete. A late job continues; later jobs of its task queue behind
it. Execution time is `Simulator.execution_time()` (WCET for now).

**Precedence.** A `Precedence` pairs predecessor job `pred_first + k·pred_every`
with successor job `succ_first + k·succ_every`. The engine records completed job
ids per task and offers the policy only running jobs and jobs whose predecessors
have all completed (`Simulator.eligible`), so policies need no precedence logic.
Releases and deadlines stay nominal. The loader rejects self-constraints and
cycles in the task graph; without cycles, waiting cannot deadlock. The trace
format is unchanged: waiting is not recorded.

**Policies.** Ranking policies define `order(job)`; the default `select()`
(`assign_ranked`) lets each job in that order take a free allowed core: its
current core, else an idle core, else the core running the lowest-ranked job.
Affinity therefore covers global, partitioned and mixed scheduling. `fp` ranks
by priority (higher value first), `edf` by absolute deadline (a late job stays
most urgent; `priority` is ignored). Both then rank running before waiting (ties
never preempt), release time, task order. A new policy is a `SchedulingPolicy`
subclass plus a registry entry ([policies/README.md](../src/simulator/policies/README.md));
resource protocols and overheads should become separate engine components
rather than policy code.

**Trace format** (`rt-trace-sim`, version 1). One JSON object per line: header,
one `task` line per task, then events `release` (absolute `deadline`), `start`,
`preempt`, `complete` (with `core`) and a final `end` at the horizon. Timestamps
never decrease; job ids count from 0 per task. See
[trace_format.py](../src/simulator/trace_format.py). Format changes must bump
`VERSION` and update writer, parser and tests together.

**Parser.** Strict: malformed lines, unknown tasks/jobs, time going backwards,
double starts or stops on a core raise `SimTraceError` with the line number. A
missing `end` is a warning (truncated file); open jobs end at the last event.
Rows get `kind='task'`, `deadlinesExplicit=True` and `traceFormat=3`, which
`TraceView` uses for current-format rows. Jobs may overlap after a deadline miss,
so jobs are created directly rather than via `TraceTask.newJob()`.

**Command line.** `simulator/__main__.py`: `python src/simulator taskset.json
<seconds> [-o file]` (or `python -m simulator` with `src` on the path). It validates
before writing anything; exit code 1 for an invalid task set, 2 for bad arguments.
`--csv` writes the per-task CSVs next to the trace via `DataExport.exportCsvFiles()`,
which needs no GUI modules (`printStatistics()` alone imports `HelperFunctions`).

**GUI.** Target `Simulator` in `RT-Trace.py`. `programCheckFunc` lists folders
with `taskset.json` in the project folder; `pathValidationFunc` validates the task
set and the policy; recording simulates **Duration (s)** and stores
`sim_trace.jsonl` with a copy of `taskset.json`. The core count comes from the
trace header; `TraceView` colors at most six cores.
