# RT-Trace

Record and visualize task execution, inferred jobs, deadlines, mutex operations,
IRQ activity and scheduling on a shared timeline. The main target is **Pico 2
with FreeRTOS SMP**, using one SEGGER RTT channel per core. Linux tracing is
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

1. Integrate tracing using the [target setup guide](target/README.md). Call
   `traceTimeZero()` once at the desired application origin, then build the
   firmware. The GUI expects `<project>/build/<project>.elf`; the executable and
   project-folder names must match. Trace support is checked in the ELF;
   `traceConfig.h` is unnecessary.
2. Power the Pico 2 and connect its SWD pins to a debug probe supported by pyOCD.
   A USB serial connection alone cannot read RTT. Close other debugger sessions.
3. Select **Pico2 FreeRTOS** under **Platform**. Click the folder button beside
   **Program** and choose your project, or a directory containing projects.
4. Select the program and enter a recording name under **Record Trace**.
5. Click **Record Trace**. The recording workflow flashes the ELF, runs for the
   configured duration, then halts the target and loads the captured trace.
6. Keep the matching ELF as `firmware.elf` beside the recording and load again
   to enable caller-based exclusion of internal SDK/kernel waits.

Recordings are stored in `data/Pico2_FreeRTOS/<name>_<timestamp>/`.
`raw_buffer0.txt` and `raw_buffer1.txt` contain binary data despite their extension.

## Load and inspect

Select **Pico2 FreeRTOS**, choose a recording under **View Trace**, then click
**Load Trace**. No connected hardware or selected program is needed for loading.
Reselect the platform to refresh the recording list.

To try the supplied example, copy the two `raw_buffer*.txt` files from
[examples/freertos_smp](examples/freertos_smp/) into
`data/Pico2_FreeRTOS/Example_format3/`, then select **Example_format3** and load it.
It includes task migration, nested IRQs, mutex contention and a deadline miss.
Only current format-3 FreeRTOS recordings are accepted.

| Action | Control |
| --- | --- |
| Zoom | Up/Down keys or Ctrl + mouse wheel |
| Move through time | Mouse wheel, horizontal scrollbar or drag the plot |
| Scroll task rows | Shift + mouse wheel or vertical scrollbar |
| Save the current view | **Export View to PDF** |

Execution colors identify cores; arrows indicate inferred job releases. Red
execution marks a deadline overrun. Mutex markers appear at take-call entry:
black means success, red/crossed means failure, gray means unknown; white marks
an actual give.

Job boundaries are inferred from syscalls; each next release defines the
previous job's deadline. Review warnings in the output panel. Incomplete or
uncertain jobs are excluded from completion statistics and CSV exports.

Loading writes `events.txt` and per-task CSVs into the recording folder. CSV
times are in microseconds. PDFs go to `output/<platform>/<recording>/`.
Optional `job_policy.json` overrides inference; see the
[task/job architecture](arch_description/03-task-builder.md).

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
