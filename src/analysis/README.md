# Analysis plugins

An analysis evaluates a loaded trace: simulator, Pico 2 FreeRTOS or Linux. It
reads the parsed tasks and jobs, never the raw trace files, and returns a
`Result`. The runner prints the result, writes tables as CSV and reports errors.
The same analysis therefore runs in the GUI (**Analyze**) and on the command
line. After every trace load, only `statistics` runs (`DEFAULT` in
[registry.py](registry.py)).

## Write a plugin

Put a `.py` file in a folder of your own. Files starting with `_` are skipped.
Each file defines one or more subclasses of `Analysis`:

```python
from analysis import Analysis, Result, Table, ms

class LongestJob(Analysis):
    name = 'longest_job'                  # unique, used on the command line
    title = 'Longest job'                 # shown in the dropdown
    description = 'The job with the longest response time per task'

    def check(self, ctx):                 # optional: None, or why the analysis cannot run
        return None if ctx.task_rows() else 'no tasks in this trace'

    def run(self, ctx):
        rows = []
        for task in ctx.task_rows():
            jobs = ctx.completed_jobs(task)
            if jobs:
                job = max(jobs, key=lambda j: j.getFinishTime() - j.releaseTime)
                rows.append([task.name, job.id, ms(job.getFinishTime() - job.releaseTime)])
        return Result(headline='(ms)', tables=[
            Table(['Task', 'Job', 'Response'], rows, formats=[None, None, '.3f'], csv='longest_job.csv')])
```

Then add the folder to `Resources/config.local.ini` (separate several folders
with `;`):

```ini
[analysis]
plugin_folders = ~/rt-trace-plugins
```

Plugins are imported again on every run, so you can edit a plugin without
restarting the GUI; newly added files appear in the dropdown after the next
trace load. Plugins that cannot be imported, or whose name is already taken,
are reported in the output box. See
[examples/analysis_plugins](../../examples/analysis_plugins) for a complete
plugin.

## Contract

- `run()` only computes. It must not print, use GUI modules or modify the tasks,
  because the trace view draws the same objects while it runs.
- `Table`: values keep their unit, and `formats` only changes how they print.
  `csv='name.csv'` also saves the table, and `max_rows` shortens the printed copy.
- To write other files, use `ctx.output_path('file')`, which points into
  `<recording>/analysis/`. The runner reports each file.
- Exceptions are reported with a traceback; they never stop a trace from loading.

`AnalysisContext` ([base.py](base.py)):

| Attribute | Content |
| --- | --- |
| `tasks` | all rows (`TraceTask`), including the IRQ, idle and scheduler rows (`task.kind`) |
| `task_rows()` | only the task rows |
| `completed_jobs(task)` | completed jobs with trustworthy boundaries, as used by the CSVs |
| `deadline(job)` | `(deadline, estimated)`, the deadline the trace view uses, or `(None, None)` |
| `platform`, `cores`, `trace_end` | platform name, number of cores, end of the trace in µs |
| `taskset` | the simulator `TaskSet` (from `taskset.json`), else `None` |
| `folder`, `warnings` | recording folder and parser warnings |

All times are integer microseconds.

## Run from the command line

```sh
python src/analysis --list                                   # built-in and configured plugins
python src/analysis data/Simulator/<recording>               # default analyses
python src/analysis <recording> deadline_misses longest_job  # selected ones
python src/analysis <recording> --all --plugins ~/rt-trace-plugins
python src/simulator taskset.json 0.2 --analyze deadline_misses
```

In the packaged app, plugins can import only the standard library and the
packages bundled with RT-Trace, such as numpy.
