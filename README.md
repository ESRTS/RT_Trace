# RT-Trace

Record and visualize task execution, inferred jobs, deadlines, mutex operations,
IRQ activity and scheduling on a shared timeline. The main target is **Pico 2
with FreeRTOS SMP**, using one SEGGER RTT channel per core. NVIDIA Jetson traces
of Nsight Systems, including the GPU, can be loaded. Linux tracing is
experimental.

## Run

Open the packaged application, or run from the repository root with Python 3.12
and Tkinter:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python src/RT-Trace.py
```

On Windows, activate with `.venv\Scripts\activate`.

## Record a trace

1. Integrate tracing using the [target setup guide](target/README.md). Optionally
   call `traceTimeZero()` once at the desired application origin, then build the
   firmware. The GUI expects `<project>/build/<project>.elf`; the executable and
   project-folder names must match. Trace support is checked in the ELF;
   `traceConfig.h` is unnecessary.
2. Power the Pico 2 and connect its SWD pins to a debug probe supported by pyOCD.
   A USB serial connection alone cannot read RTT. Close other debugger sessions.
3. Select **Pico2 FreeRTOS** under **Platform**. Click the folder button beside
   **Program** and choose your project, or a directory containing projects.
4. Select the program, enter a recording name and set **Duration (s)** above
   **Record Trace**. Positive fractional seconds are supported. Valid edits are
   saved automatically to `Resources/config.local.ini` for subsequent runs.
5. Click **Record Trace**. The recorder saves `firmware.elf` in the recording
   folder, flashes that copy, runs for the configured duration, then halts the
   target and loads the trace. The saved ELF enables internal caller exclusions
   automatically and keeps symbols matched to the capture after rebuilding.

Recordings are stored in `data/Pico2_FreeRTOS/<name>_<timestamp>/`.
`raw_buffer0.txt` and `raw_buffer1.txt` contain binary data despite their extension.

The recorder also runs without the GUI. It takes the program and the duration in
seconds:

```sh
python src/RttTraceRecorder.py MyProject 2           # a project in project_path
python src/RttTraceRecorder.py path/to/MyProject 0.5 --csv --analyze deadline_misses
python src/RttTraceRecorder.py build/MyProject.elf -o out/rec
```

The program can be a project folder (`build/<name>.elf`), a project name in
`project_path`, or an ELF file. The duration defaults to `measure_seconds`. The
recording goes to the same folder as from the GUI, so it appears under
**View Trace**; `-o` sets another folder and `-n` another recording name.
`--csv` writes `events.txt` and the per-task CSVs, as loading does; `--analyze`
runs [analyses](#analyze-a-trace). `--probe`, `--target` and `--frequency`
override the config. The ELF is checked before the probe is used.

## Simulate a task set

The **Simulator** platform schedules a task set on the host instead of recording
hardware. A simulator program is a folder in the project folder that contains a
`taskset.json`; [examples/simulator](examples/simulator) has one to copy:

```json
{"cores": 2, "scheduler": "fp", "tasks": [
  {"name": "Control", "period": 5000, "wcet": 1500, "priority": 4, "affinity": [0]}]}
```

Times are integer microseconds. Per task, `deadline` defaults to `period`,
`phase` (release time of the first job) to 0 and `affinity` (allowed cores) to
all cores. `bcet` is validated but jobs currently always run for `wcet`. Select
the folder under **Program**, set **Duration (s)** and click **Record Trace**.
The recording folder receives `sim_trace.jsonl` and a copy of `taskset.json`.

`scheduler` selects the policy. Both are preemptive, and late jobs run to
completion:

- `fp`: fixed priority; a larger `priority` wins.
- `edf`: earliest absolute deadline first; `priority` is not needed.

Ties are served FIFO and never preempt a running job.

Optional `precedence` constraints make jobs of one task wait for jobs of another:

```json
"precedence": [
  {"predecessor": {"task": "Sensor",  "first_job": 0, "every": 1},
   "successor":   {"task": "Control", "first_job": 1, "every": 2}}]
```

For k = 0, 1, 2, …, predecessor job `first_job + k·every` must complete before
successor job `first_job + k·every` may start: here Sensor job k gates Control
job 1 + 2k. Job ids count from 0; `first_job` defaults to 0 and `every` to 1. The
successor is released at its nominal time and keeps its deadline; it only waits
to start. A job with several constraints waits for all its predecessors.
Constraints within one task and cycles between tasks are rejected. Waiting is
not marked in the trace view.

The simulator also runs without the GUI. It takes the task set and the
simulated time in seconds and writes `sim_trace.jsonl` (`-o` sets another path):

```sh
python src/simulator path/to/taskset.json 0.2 -o out/sim_trace.jsonl
```

`--csv` also writes the per-task CSV files next to the trace, as loading in
the GUI does. To view such a trace, put it as `data/Simulator/<name>/sim_trace.jsonl`. It then
appears under **View Trace** for the Simulator platform.

See the
[simulator architecture](arch_description/04-simulator.md) and
[how to add a policy](src/simulator/policies/README.md).

## Load and inspect

Select **Pico2 FreeRTOS**, choose a recording under **View Trace**, then click
**Load Trace**. No connected hardware or selected program is needed for loading.
Reselect the platform to refresh the recording list.

Use the scrollable **Show in trace** checkboxes below the buttons to show or hide
tasks, interrupts and scheduler rows. Changes redraw immediately and preserve
the time view. All rows start checked when a trace loads. PDF export follows
this selection; CSV exports retain all tasks.

To try the supplied example, copy the two `raw_buffer*.txt` files from
[examples/freertos_smp](examples/freertos_smp/) into
`data/Pico2_FreeRTOS/Example_format3/`, then select **Example_format3** and load it.
It includes task migration, nested IRQs, mutex contention and a deadline miss.
Only current format-3 FreeRTOS recordings are accepted.

| Action | Control |
| --- | --- |
| Zoom | Up/Down keys or Ctrl + mouse wheel |
| Move through time | Mouse wheel or drag the plot |
| Scroll task rows | Shift + mouse wheel or vertical scrollbar |
| Save the current view | **Export View to PDF** |

Execution colors identify cores; arrows indicate inferred job releases. Red
execution marks a deadline overrun. Mutex markers appear at take-call entry:
black means success, red/crossed means failure, gray means unknown; white marks
an actual give.

Job boundaries are inferred from syscalls; each next release defines the
previous job's deadline. Review warnings in the output panel. Incomplete or
uncertain jobs are excluded from completion statistics and CSV exports.
Without an explicit time zero, the parser estimates SysTick start from an intact
startup trace with IRQ tracing enabled; otherwise it uses the first event.
Both fallback origins are reported as estimates.

Loading writes `events.txt` and per-task CSVs into the recording folder. CSV
times are in nanoseconds, like all times of the trace model (FreeRTOS and the
simulator record whole microseconds). Each job row ends with `Deadline_ns`, the deadline
the trace view uses, and `DeadlineEstimated`. A 1 means the deadline time is
estimated, or is the next job's release because the parser sets no deadlines
(Linux). Both cells are empty if the deadline is unknown. PDFs go to
`output/<platform>/<recording>/`.
Optional `job_policy.json` overrides inference; see the
[task/job architecture](arch_description/03-task-builder.md).

## Load a Jetson trace (Nsight Systems)

RT-Trace loads CPU and GPU traces of a Jetson (tested on a Jetson Thor) that
Nsight Systems recorded; recording from RT-Trace is not supported yet. On the
Jetson, with `kernel.perf_event_paranoid` at 1 or lower (otherwise the trace has
no CPU context switches):

```sh
nsys profile --trace=cuda,nvtx,osrt --sample=none --cpuctxsw=process-tree -o trace ./app
nsys export --type sqlite trace.nsys-rep
```

Copy `trace.sqlite` into `data/Jetson_Thor/<recording>/`, select **Jetson Thor**
and load the recording. Every thread of the application is a row; each GPU stream
has a row of its own, where each kernel starts at its launch call. Tasks and jobs
come from NVTX annotations in the application: the thread name, the period,
priority and release of each job, and one NVTX range per job (the contract is in
[TraceParserJetson.py](src/TraceParserJetson.py) and the
[Jetson architecture](arch_description/06-jetson.md)). Without them, threads
are shown without jobs. Times are nanoseconds; the time zero is the common
release of the tasks. [examples/jetsonThor](examples/jetsonThor) has a trace
with two CPU and two GPU tasks.

## Analyze a trace

After loading, the output panel shows the task statistics. Use **Analyze** below
**Export View to PDF** to run another analysis on all rows of the loaded trace:

- **Deadline misses**: late jobs per task and their lateness.
- **Response times**: min/avg/max response, jitter and start delay.
- **Preemptions and migrations**: per task and job.

Results appear in the output panel. Tables are also saved as CSV in
`<recording>/analysis/`.

You can add your own analyses as Python plugins in a folder outside the
repository. Set it in `Resources/config.local.ini`:

```ini
[analysis]
plugin_folders = ~/rt-trace-plugins
```

The [plugin guide](src/analysis/README.md) explains the interface;
[examples/analysis_plugins](examples/analysis_plugins) has a per-core load
example. A plugin can also read input files from the recording folder, such as
a timing specification; the example `timing_spec.py` shows how. If a file is
missing, the output panel says which file the analysis needs and where. Analyses
also run without the GUI:

```sh
python src/analysis --list
python src/analysis data/Simulator/<recording> deadline_misses response_times
python src/simulator taskset.json 0.2 --analyze deadline_misses
python src/RttTraceRecorder.py MyProject 2 --analyze deadline_misses
```

## Settings and development

Put overrides in `Resources/config.local.ini`; defaults are in
[Resources/config.ini](Resources/config.ini). For example:

```ini
[general]
measure_seconds = 2
frequency = 4000000
strict = False
ui_scale = auto
```

The duration field starts with `measure_seconds` from the merged configuration.
It controls Pico 2 recording; Linux's remote recorder runs until its application
exits, so the duration field is disabled for that platform.

`strict = True` rejects event loss and structural inconsistencies instead of
loading recoverable data with warnings. Recording and parsing failures write
`record_error.log` or `parse_error.log` beside the capture.

The folder button saves the project location. To override it for one run:

```sh
python src/RT-Trace.py --project-path /path/to/project
```

For implementation context and validation commands, see
[arch_description](arch_description/README.md). To package the GUI, install
`requirements-build.txt` and run `bash createGui.sh` on the destination OS.
