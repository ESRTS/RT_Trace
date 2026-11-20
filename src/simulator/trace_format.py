"""
Simulator trace format: the contract between the simulators (writers) and TraceParserSim (reader).

One JSON object per line, in this order:
    {"type": "header", "format": "rt-trace-sim", "version": 1, "cores": 2, "time_unit": "us",
     "horizon": 100000, "scheduler": "fp"}
    {"type": "task", "id": 1, "name": "Control", "priority": 3, "period": 10000, "wcet": 3000,
     "deadline": 10000, "phase": 0, "affinity": null}                       # one per task
    {"t": 0, "type": "release", "task": 1, "job": 0, "deadline": 10000}
    {"t": 10, "type": "start", "task": 1, "job": 0, "core": 0}
    {"t": 3000, "type": "preempt", "task": 1, "job": 0, "core": 0}
    {"t": 3100, "type": "complete", "task": 1, "job": 0, "core": 1}
    {"t": 100000, "type": "end"}                                            # last line

Timestamps are non-decreasing integers in time_unit. Job ids count from 0 per task.
At one timestamp, completions and preemptions precede starts, so a migrating job
is stopped on its old core before it starts on the new one.
"""

FORMAT = 'rt-trace-sim'
VERSION = 1
TRACE_FILE = 'sim_trace.jsonl'
TIME_UNIT = 'us'

HEADER = 'header'
TASK = 'task'
RELEASE = 'release'
START = 'start'
PREEMPT = 'preempt'
COMPLETE = 'complete'
END = 'end'

EVENT_TYPES = (RELEASE, START, PREEMPT, COMPLETE, END)
