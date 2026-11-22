# Analysis: tasks/jobs → results

Sources: [analysis/](../src/analysis) ([plugin guide](../src/analysis/README.md)),
[examples/analysis_plugins](../examples/analysis_plugins). Tests:
[test_analysis.py](../tests/test_analysis.py).

**Boundary.** Analyses read only the `TraceTask`/`TraceJob`/`TraceInterval`
model, so one analysis serves every platform. The package has no GUI imports;
the GUI, both command lines and `DataExport.exportData()` drive the same runner.

| File | Role |
| --- | --- |
| `base.py` | `Analysis` (`name`, `title`, `check`, `run`), `Result`, `Table`, `AnalysisContext` and its helpers. |
| `report.py` | Runner: catches errors, writes CSV tables, prints in the output-box format. |
| `registry.py` | Built-in analyses first, then plugin folders (`[analysis] plugin_folders`); `DEFAULT`. |
| `recording.py` | Context from parsed tasks (GUI) or from a recording folder (CLI). |
| `builtin/` | `statistics`, `deadline_misses`, `response_times`, `preemptions`. |
| `__main__.py` | `python src/analysis`; also used by `python src/simulator --analyze`. |

**Flow.** After loading, `exportData()` writes the per-task CSVs and runs
`DEFAULT` (only `statistics`). **Analyze** runs the selected analysis in a worker
thread on the loaded tasks. Analyses must not change the tasks, because the
trace view draws them at the same time. Each run imports plugin files again;
import errors, name clashes and exceptions are reported and never stop loading.

**Consistency.** `completed_jobs()` excludes jobs that are `incomplete` or
`uncertain`, and `deadline()` reuses `DataExport.jobDeadline()`. Results
therefore agree with the per-task CSVs and the trace view. Files go to
`<recording>/analysis/`.

**Limits.** Analyses run on all rows, not only the visible ones. Results are not
drawn in the trace view. Plugins in the packaged app can import only bundled
modules.
