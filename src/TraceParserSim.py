"""
Reads a simulator trace (simulator/trace_format.py) and builds the TraceTask rows for the visualization.
The simulator knows every release, deadline and completion, so nothing is inferred or estimated here.
The simulator works in microseconds (time_unit "us" in the header); the rows are in the nanoseconds of the
trace model (timeunits.py).
"""
import json
from pathlib import Path

from TraceTask import TraceTask, TraceJob, ExecutionType
from simulator import trace_format as fmt
from timeunits import NS, US, MS

UNITS = {'us': US, 'ns': NS}        # time_unit of the header -> factor to nanoseconds

_COLORS = ('#64ED9D', '#648FED', '#D4ED4C', '#ED7B64', '#8D64ED', '#E29578', '#006D77')


class SimTraceError(ValueError):
    """The trace file is malformed; the message names the line."""


def read_trace(path):
    """Parse a simulator trace file. Returns (header, tasks, warnings)."""
    with open(path) as stream:
        return parse_lines(stream)


def parse_lines(lines):
    header, tasks, jobs, running, warnings = None, {}, {}, {}, []
    now, ended = None, False

    def fail(number, message):
        raise SimTraceError(f'line {number}: {message}')

    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            fail(number, f'invalid JSON ({error.msg})')
        if not isinstance(record, dict):
            fail(number, 'expected a JSON object')
        kind = record.get('type')

        if header is None:
            if kind != fmt.HEADER or record.get('format') != fmt.FORMAT:
                fail(number, f"the first line must be the '{fmt.FORMAT}' header")
            if record.get('version') != fmt.VERSION:
                fail(number, f"unsupported version {record.get('version')}, expected {fmt.VERSION}")
            if record.get('time_unit') not in UNITS:
                fail(number, f"unsupported time unit {record.get('time_unit')!r}, expected one of {', '.join(UNITS)}")
            header = record
            unit = UNITS[record['time_unit']]
            continue
        if ended:
            fail(number, f"data after the '{fmt.END}' event")

        if kind == fmt.TASK:
            if jobs:
                fail(number, 'task definitions must precede the events')
            ident = record.get('id')
            if ident in tasks:
                fail(number, f'task id {ident} defined twice')
            if not isinstance(record.get('name'), str):
                fail(number, "task 'name' must be a string")
            task = TraceTask(ident, record['name'], record.get('priority'), _COLORS[len(tasks) % len(_COLORS)])
            task.kind = 'task'
            task.traceFormat = 3                # TraceView treats only these rows as current-format rows (time bounds)
            task.deadlinesExplicit = True       # Deadline misses use job.deadline, not the next release
            task.period = None if record.get('period') is None else record['period'] * unit
            tasks[ident] = task
            continue
        if kind not in fmt.EVENT_TYPES:
            fail(number, f'unknown record type {kind!r}')

        t = record.get('t')
        if not isinstance(t, int) or isinstance(t, bool):
            fail(number, "'t' must be an integer")
        if now is not None and t < now:
            fail(number, f'time goes backwards ({t} < {now})')
        now = t

        if kind == fmt.END:
            ended = True
            continue

        key = (record.get('task'), record.get('job'))
        if key[0] not in tasks:
            fail(number, f'unknown task id {key[0]}')
        task = tasks[key[0]]
        if kind == fmt.RELEASE:
            if key in jobs:
                fail(number, f'{task.name} job {key[1]} released twice')
            deadline = record.get('deadline')
            job = TraceJob(task, key[1], t * unit, None if deadline is None else deadline * unit)
            jobs[key] = job
            task.jobs.append(job)               # Jobs can overlap after a deadline miss, so TraceTask.newJob() is not used
            continue

        job = jobs.get(key)
        if job is None:
            fail(number, f'{task.name} job {key[1]} was not released')
        if job.completionTime is not None:
            fail(number, f'{task.name} job {key[1]} already completed')
        core = record.get('core')
        if not isinstance(core, int) or not 0 <= core < header['cores']:
            fail(number, f'invalid core {core!r}')
        if kind == fmt.START:
            if job.activeInterval is not None:
                fail(number, f'{task.name} job {key[1]} started while running')
            if core in running:
                fail(number, f'core {core} started {task.name} while running {running[core]}')
            job.startExec(t * unit, core, ExecutionType.EXECUTE)
            running[core] = job
        else:                                   # preempt or complete
            if running.get(core) is not job:
                fail(number, f'{task.name} job {key[1]} is not running on core {core}')
            job.stopExec(t * unit)
            del running[core]
            if kind == fmt.COMPLETE:
                job.completionTime = t * unit

    if header is None:
        raise SimTraceError('empty trace file')
    if not ended:
        warnings.append(f"trace has no '{fmt.END}' event, it was probably cut off; jobs end at the last event")
    end = now * unit if now is not None else 0

    for job in jobs.values():                   # Jobs still open at the horizon
        if job.completionTime is None:
            if job.activeInterval is not None:
                job.stopExec(end)
            job.incomplete = True
            job.observedUntil = end
    return header, [task for task in tasks.values() if task.jobs], warnings


def horizon_ns(header):
    """The simulated horizon of a trace header in nanoseconds, None if unknown."""
    horizon = header.get('horizon')
    return None if horizon is None else horizon * UNITS.get(header.get('time_unit'), US)


def loadRecording(gui):
    import HelperFunctions
    import DataExport
    header, tasks, warnings = read_trace(Path(gui.viewingFolder) / fmt.TRACE_FILE)
    HelperFunctions.printState('Simulation', info=f"scheduler '{header.get('scheduler')}', {header['cores']} core(s), "
                                                  f"{(horizon_ns(header) or 0) / MS:g} ms")
    for warning in warnings:
        HelperFunctions.printState('Warning', info=warning)
    DataExport.exportData(gui, tasks, overwrite=True)
    return tasks


def parser_thread(gui):
    import HelperFunctions
    try:
        return loadRecording(gui)
    except Exception as error:
        HelperFunctions.reportError('Parsing failed', error, gui.viewingFolder, 'parse_error.log',
                                    f'Check {fmt.TRACE_FILE}; details are in parse_error.log.')
        return None


def parseTraceFiles(gui, numCores=None):
    """loadTraceFunc of the simulator target. The number of cores comes from the trace header."""
    import HelperFunctions
    HelperFunctions.startWorker(gui, parser_thread, gui.loadingFinished)
