"""Writes the simulator trace as JSON lines (see trace_format.py)."""
import json

from . import trace_format as fmt


class TraceWriter:
    def __init__(self, stream):
        self.stream = stream

    def _write(self, record):
        self.stream.write(json.dumps(record, separators=(',', ':')) + '\n')

    def header(self, taskset, horizon):
        self._write({'type': fmt.HEADER, 'format': fmt.FORMAT, 'version': fmt.VERSION,
                     'cores': taskset.cores, 'time_unit': fmt.TIME_UNIT, 'horizon': horizon,
                     'scheduler': taskset.scheduler})
        for task in taskset.tasks:
            self._write({'type': fmt.TASK, 'id': task.id, 'name': task.name, 'priority': task.priority,
                         'period': task.period, 'wcet': task.wcet, 'deadline': task.deadline,
                         'phase': task.phase,
                         'affinity': list(task.affinity) if task.affinity is not None else None})

    def release(self, t, job):
        self._write({'t': t, 'type': fmt.RELEASE, 'task': job.task.id, 'job': job.id, 'deadline': job.deadline})

    def start(self, t, job, core):
        self._write({'t': t, 'type': fmt.START, 'task': job.task.id, 'job': job.id, 'core': core})

    def preempt(self, t, job, core):
        self._write({'t': t, 'type': fmt.PREEMPT, 'task': job.task.id, 'job': job.id, 'core': core})

    def complete(self, t, job, core):
        self._write({'t': t, 'type': fmt.COMPLETE, 'task': job.task.id, 'job': job.id, 'core': core})

    def end(self, t):
        self._write({'t': t, 'type': fmt.END})
