"""
Reads an NVIDIA Nsight Systems trace of a Jetson (exported to SQLite) and builds the TraceTask rows.

Recording (on the Jetson; see arch_description/06-jetson.md):
    nsys profile --trace=cuda,nvtx,osrt --sample=none --cpuctxsw=process-tree -o trace ./app
    nsys export --type sqlite trace.nsys-rep
The .sqlite file goes into a recording folder: data/Jetson_Thor/<recording>/.

Stage 1 (read_export) reads the tables into plain events. Stage 2 (build_rows) builds the rows:
  - one row per thread of the traced process. Threads the application names through NVTX are tasks:
    their jobs are the NVTX ranges of the domain 'rt-trace'; the markers of that domain give the period,
    the priority, the release of each job and the time zero (see the NVTX contract below);
  - one row per GPU stream: kernels, copies and memsets, released at their launch call;
  - CPU intervals come from the context switches (SCHED_EVENTS).

NVTX contract of the application (domain 'rt-trace', all values in the markers' integer payload):
    thread name   nvtxNameOsThread         task name of the thread
    mark          period_ms                period of the task [ms]
    mark          priority                 priority of the task
    mark          clock_sync               CLOCK_MONOTONIC_RAW [ns] when the mark was written (repeat it)
    mark          release                  common release of all tasks, CLOCK_MONOTONIC_RAW [ns] (time zero)
    mark          next_release             release of the thread's next job, CLOCK_MONOTONIC_RAW [ns]
    push/pop range named after the task    one job, payload = job id
The trace's own clock is CLOCK_MONOTONIC_RAW with an offset. The offset is the largest difference
between a clock_sync payload and its trace time: the mark with the shortest delay between reading the
clock and writing the mark. The k-th next_release of a thread is the release of its k-th job.

All times of the rows are integer nanoseconds (timeunits.py) relative to the time zero.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
import os
import sqlite3

from TraceTask import TraceTask, TraceJob, TraceInterval, ExecutionType
from timeunits import MS

NVTX_DOMAIN = 'rt-trace'
MARK_PERIOD, MARK_PRIORITY, MARK_CLOCK_SYNC = 'period_ms', 'priority', 'clock_sync'
MARK_RELEASE, MARK_NEXT_RELEASE = 'release', 'next_release'
TESTED_SCHEMA = (3, 24)         # Export schema of Nsight Systems 2025.6.1, the version this parser is tested with

PLATFORM = 'Jetson Thor'
_COLORS = ('#64ED9D', '#648FED', '#D4ED4C', '#ED7B64', '#8D64ED', '#E29578', '#006D77')
_GPU_COLOR = '#9CCB5A'
_THREAD_COLOR = '#B5B5B5'
_EVENT_TYPES = {'NvtxMark': 34, 'NvtxThread': 39, 'NvtxPushPopRange': 59, 'NvtxStartEndRange': 60,
                'NvtxDomainCreate': 75}     # Fallback if the export has no ENUM_NSYS_EVENT_TYPE table
_GPU_TABLES = (('kernel', 'CUPTI_ACTIVITY_KIND_KERNEL'), ('memcpy', 'CUPTI_ACTIVITY_KIND_MEMCPY'),
               ('memset', 'CUPTI_ACTIVITY_KIND_MEMSET'))


class JetsonTraceError(ValueError):
    """The file is no Nsight Systems SQLite export, or it lacks what a trace needs."""


def thread_id(global_tid):
    """The Linux thread id of an Nsight Systems globalTid (bits 0-23)."""
    return global_tid & 0xFFFFFF


def process_id(global_id):
    """The Linux process id of an Nsight Systems globalTid or globalPid (bits 24-47)."""
    return (global_id >> 24) & 0xFFFFFF


# ---------------------------------------------------------------------------------------------- stage 1

@dataclass
class GpuOp:
    kind: str           # 'kernel', 'memcpy' or 'memset'
    start: int
    end: int
    device: int
    stream: int
    correlation: int
    name: str


@dataclass
class Export:
    """The parts of an Nsight Systems SQLite export the parser uses. Times: ns of the trace's clock."""
    version: str = None                                 # Nsight Systems version
    schema: tuple = None                                # (major, minor) of the export schema
    gpus: dict = field(default_factory=dict)            # device id -> name
    processes: dict = field(default_factory=dict)       # pid -> name
    thread_names: dict = field(default_factory=dict)    # globalTid -> name (ThreadNames)
    nvtx_names: dict = field(default_factory=dict)      # globalTid -> name given through NVTX
    sched: list = None                                  # (time, cpu, switched in, globalTid); None: not recorded
    exits: dict = field(default_factory=dict)           # globalTid -> time the thread terminated
    marks: list = field(default_factory=list)           # (time, globalTid, name, payload) of the domain
    ranges: list = field(default_factory=list)          # (start, end, globalTid, name, payload) of the domain
    domain_found: bool = False
    calls: dict = field(default_factory=dict)           # correlation id -> (start, end, globalTid, name)
    gpu_ops: list = field(default_factory=list)         # GpuOp
    syncs: list = field(default_factory=list)           # (start, end, correlation id): waits for the GPU
    diagnostics: list = field(default_factory=list)     # (severity, text); severity 2 = warning, 3 = error


def read_export(path):
    """Read an Nsight Systems SQLite export (nsys export --type sqlite) into an Export."""
    if not os.path.isfile(path):
        raise JetsonTraceError(f'file not found: {path}')
    try:
        db = sqlite3.connect(f'file:{os.path.abspath(path)}?mode=ro', uri=True)
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as error:
        raise JetsonTraceError(f'{path} is no SQLite file: {error}') from None
    try:
        if 'META_DATA_EXPORT' not in tables or 'StringIds' not in tables:
            raise JetsonTraceError(f'{path} is no Nsight Systems export (META_DATA_EXPORT/StringIds missing)')
        return _read(db, tables)
    finally:
        db.close()


def _read(db, tables):
    q = lambda sql, *args: db.execute(sql, args).fetchall()
    export = Export()
    meta = dict(q("SELECT name, value FROM META_DATA_EXPORT"))
    export.version = meta.get('EXPORT_PRODUCT_VERSION')
    try:
        export.schema = (int(meta['EXPORT_SCHEMA_VERSION_MAJOR']), int(meta['EXPORT_SCHEMA_VERSION_MINOR']))
    except (KeyError, ValueError):
        export.schema = None
    strings = dict(q("SELECT id, value FROM StringIds"))

    if 'TARGET_INFO_GPU' in tables:
        export.gpus = {ident: name for ident, name in q("SELECT id, name FROM TARGET_INFO_GPU")}
    if 'PROCESSES' in tables:
        export.processes = {pid: name for pid, name in q("SELECT pid, name FROM PROCESSES")}
    if 'ThreadNames' in tables:
        export.thread_names = {gtid: strings.get(nameId) for nameId, gtid in q("SELECT nameId, globalTid FROM ThreadNames")}
    if 'SCHED_EVENTS' in tables:
        rows = q("SELECT start, cpu, isSchedIn, globalTid, threadState FROM SCHED_EVENTS ORDER BY start")
        export.sched = [row[:4] for row in rows]
        terminated = 5                              # ENUM_SAMPLING_THREAD_STATE 'Terminated'
        if 'ENUM_SAMPLING_THREAD_STATE' in tables:
            terminated = next((ident for ident, name in q("SELECT id, name FROM ENUM_SAMPLING_THREAD_STATE")
                               if name == 'Terminated'), terminated)
        export.exits = {gtid: time for time, _, switched_in, gtid, state in rows
                        if not switched_in and state == terminated}
    if 'DIAGNOSTIC_EVENT' in tables:
        export.diagnostics = q("SELECT severity, text FROM DIAGNOSTIC_EVENT ORDER BY timestamp")

    if 'NVTX_EVENTS' in tables:
        types = dict(_EVENT_TYPES)
        if 'ENUM_NSYS_EVENT_TYPE' in tables:
            types.update({name: ident for ident, name in q("SELECT id, name FROM ENUM_NSYS_EVENT_TYPE")})
        rows = q("SELECT start, end, eventType, globalTid, text, textId, domainId, uint64Value, int64Value "
                 "FROM NVTX_EVENTS ORDER BY start")
        text = lambda row: row[4] if row[4] is not None else strings.get(row[5])
        payload = lambda row: row[7] if row[7] is not None else row[8]
        domains = {row[6] for row in rows if row[2] == types['NvtxDomainCreate'] and text(row) == NVTX_DOMAIN}
        export.domain_found = bool(domains)
        for row in rows:
            if row[2] == types['NvtxThread']:
                export.nvtx_names[row[3]] = text(row)
            elif row[6] in domains and row[2] == types['NvtxMark']:
                export.marks.append((row[0], row[3], text(row), payload(row)))
            elif row[6] in domains and row[2] in (types['NvtxPushPopRange'], types['NvtxStartEndRange']):
                export.ranges.append((row[0], row[1], row[3], text(row), payload(row)))

    if 'CUPTI_ACTIVITY_KIND_RUNTIME' in tables:
        for start, end, gtid, correlation, nameId in q(
                "SELECT start, end, globalTid, correlationId, nameId FROM CUPTI_ACTIVITY_KIND_RUNTIME"):
            export.calls[correlation] = (start, end, gtid, strings.get(nameId))
    for kind, table in _GPU_TABLES:
        if table not in tables:
            continue
        name_column = 'shortName' if kind == 'kernel' else 'NULL'
        for start, end, device, stream, correlation, nameId in q(
                f"SELECT start, end, deviceId, streamId, correlationId, {name_column} FROM {table}"):
            export.gpu_ops.append(GpuOp(kind, start, end, device, stream, correlation,
                                        strings.get(nameId, kind) if nameId is not None else kind))
    export.gpu_ops.sort(key=lambda op: op.start)
    if 'CUPTI_ACTIVITY_KIND_SYNCHRONIZATION' in tables:
        export.syncs = q("SELECT start, end, correlationId FROM CUPTI_ACTIVITY_KIND_SYNCHRONIZATION ORDER BY start")
    return export


# ---------------------------------------------------------------------------------------------- stage 2

def build_rows(export):
    """
    Build the rows of an Export. Returns (rows, info, warnings): rows are TraceTask objects (tasks first,
    then GPU streams, then the other threads), info a dict for the user (see describe()).
    """
    warnings, info = [], {'platform': PLATFORM, 'version': export.version}
    if export.schema is not None and export.schema != TESTED_SCHEMA:
        warnings.append(f'export schema {export.schema[0]}.{export.schema[1]} (Nsight Systems {export.version}); '
                        f'the parser is tested with {TESTED_SCHEMA[0]}.{TESTED_SCHEMA[1]} (2025.6.1)')
    for severity, text in export.diagnostics:
        if severity >= 2:
            warnings.append('nsys: ' + text.strip().replace('\n', ' '))
    if export.sched is None:
        warnings.append('no CPU context switches in the trace: record with --cpuctxsw=process-tree '
                        '(needs kernel.perf_event_paranoid <= 1); the rows show no CPU execution')
    if not export.domain_found:
        warnings.append(f"no NVTX events of the domain '{NVTX_DOMAIN}': threads are shown without jobs")

    # Clock: trace time = CLOCK_MONOTONIC_RAW - offset; the time zero is the common release
    syncs = [(value - time) for time, _, name, value in export.marks if name == MARK_CLOCK_SYNC and value is not None]
    offset = max(syncs) if syncs else None
    info['clockSyncs'] = len(syncs)
    raw = lambda value: None if offset is None or value is None else value - offset     # -> trace time
    release_marks = [value for _, _, name, value in export.marks if name == MARK_RELEASE and value is not None]
    if release_marks and offset is not None:
        zero, info['zero'] = raw(release_marks[0]), 'common release (NVTX mark release)'
    else:
        zero, info['zero'] = 0, 'start of the recording'
        if export.domain_found:
            missing = 'release' if not release_marks else 'clock_sync'
            warnings.append(f'no {missing} mark: the time zero is the start of the recording')
    if offset is None and any(name == MARK_NEXT_RELEASE for _, _, name, _ in export.marks):
        warnings.append('no clock_sync mark: the next_release values cannot be converted; '
                        'job releases are the job starts (estimated)')

    # Which threads become rows: those of the processes with NVTX or CUDA activity (process tree: all)
    traced = {process_id(gtid) for _, gtid, _, _ in export.marks} | \
             {process_id(gtid) for _, _, gtid, _, _ in export.ranges} | \
             {process_id(call[2]) for call in export.calls.values()}
    sched = export.sched or []
    scheduled = {process_id(gtid) for _, _, _, gtid in sched}
    if not traced:
        traced = scheduled
    elif scheduled - traced:
        warnings.append(f'the trace has context switches of {len(scheduled - traced)} other process(es); '
                        f'only the traced process is shown')
    info['processes'] = sorted(f'{export.processes.get(pid, "?")} ({pid})' for pid in traced)
    threads = {gtid for _, _, _, gtid in sched if process_id(gtid) in traced}       # Threads that ran,
    threads |= {gtid for _, _, gtid, _, _ in export.ranges}                         # have jobs,
    threads |= {gtid for gtid in export.nvtx_names if process_id(gtid) in traced}   # are named tasks
    threads |= {call[2] for call in export.calls.values()}                          # or use CUDA

    times = [time for time, _, _, _ in sched] + [op.end for op in export.gpu_ops] + \
            [end for _, end, _, _, _ in export.ranges if end is not None]
    end = max(times, default=0) - zero
    info['end'] = end

    cpu, unpaired = _cpu_intervals(sched, threads, end, zero)
    if unpaired:
        warnings.append(f'{unpaired} context switch(es) without their counterpart were repaired')
    cpus = {core for intervals in cpu.values() for _, _, core in intervals}
    info['cpus'] = max(cpus) + 1 if cpus else 0

    tasks, others = [], []
    marks = {}
    for time, gtid, name, value in export.marks:
        marks.setdefault(gtid, []).append((time, name, value))
    ranges = {}
    for start, stop, gtid, name, value in export.ranges:
        ranges.setdefault(gtid, []).append((start, stop, value))
    counters = {'early': 0, 'unreleased': 0, 'pending': 0}
    for gtid in sorted(threads, key=thread_id):
        if gtid in ranges or gtid in export.nvtx_names and gtid in marks:
            tasks.append(_task_row(gtid, export, marks.get(gtid, []), ranges.get(gtid, []), cpu.get(gtid, []),
                                   raw, zero, end, len(tasks), counters))
        else:
            others.append(_thread_row(gtid, export, cpu.get(gtid, []), end))
    if counters['early']:
        warnings.append(f"{counters['early']} job(s) start before their release: check the clock_sync marks")
    if counters['unreleased']:
        warnings.append(f"{counters['unreleased']} job(s) have no next_release; their release is their start")
    tasks.sort(key=lambda task: (-(task.priority or 0), task.name))

    gpu_rows, late = _gpu_rows(export, tasks, zero)
    if late:
        warnings.append(f'{late} GPU operation(s) end after the job that launched them')
    _attach_waits(export, tasks, zero)
    info['gpus'] = sorted(set(export.gpus.values()))
    info['rows'] = {'tasks': len(tasks), 'gpu streams': len(gpu_rows), 'threads': len(others)}
    return tasks + gpu_rows + others, info, warnings


def _cpu_intervals(sched, threads, end, zero):
    """{globalTid: [(start, stop, cpu)]} from the context switches; unpaired events are closed or opened."""
    open_, result, unpaired = {}, {}, 0
    for time, core, switched_in, gtid in sched:
        if gtid not in threads:
            continue
        time -= zero
        intervals = result.setdefault(gtid, [])
        if switched_in:
            if gtid in open_:                       # Switch-out missing: end the old interval here
                unpaired += 1
                intervals.append((open_[gtid][0], time, open_[gtid][1]))
            open_[gtid] = (time, core)
        else:
            started = open_.pop(gtid, None)
            if started is None:                     # Running since before the first event of the thread
                if intervals:
                    unpaired += 1
                    continue
                started = (min(time, -zero), core)     # -zero: the start of the recording
            intervals.append((started[0], time, started[1]))
    for gtid, (start, core) in open_.items():       # Still running at the end of the trace
        result[gtid].append((start, max(start, end), core))
    return result, unpaired


def _thread_name(gtid, export):
    return export.nvtx_names.get(gtid) or export.thread_names.get(gtid) or f'Thread {thread_id(gtid)}'


def _clip(intervals, start, stop):
    return [(max(a, start), min(b, stop), core) for a, b, core in intervals if b > start and a < stop]


def _add_intervals(job, intervals):
    for start, stop, core in intervals:
        if stop > start:
            interval = TraceInterval(start, core, ExecutionType.EXECUTE)
            interval.stop = stop
            job.execIntervals.append(interval)


def _task_row(gtid, export, marks, ranges, intervals, raw, zero, end, index, counters):
    """A thread with NVTX jobs: jobs from the ranges, releases from next_release, deadline = release + period."""
    values = {}
    releases = []
    for _, name, value in marks:
        if name == MARK_NEXT_RELEASE:
            releases.append(raw(value))
        elif value is not None:
            values.setdefault(name, value)
    task = TraceTask(thread_id(gtid), _thread_name(gtid, export), values.get(MARK_PRIORITY),
                     _COLORS[index % len(_COLORS)])
    task.kind, task.traceFormat, task.deadlinesExplicit = 'task', 3, True
    task.period = values[MARK_PERIOD] * MS if MARK_PERIOD in values else None
    task.tid, task.pid = thread_id(gtid), process_id(gtid)

    jobs, bounds = [], []
    for number, (start, stop, ident) in enumerate(ranges):
        start -= zero
        stop = None if stop is None else stop - zero
        release = releases[number] - zero if number < len(releases) and releases[number] is not None else None
        job = TraceJob(task, ident if ident is not None else number, release if release is not None else start, None)
        if release is None:
            job.releaseKnown, job.releaseEstimated = False, True
            counters['unreleased'] += 1
        if task.period is not None:
            job.deadline = job.releaseTime + task.period
            job.deadlineEstimated = release is None
        _add_intervals(job, _clip(intervals, start, end if stop is None else stop))
        first = job.getStartTime()
        if release is not None and first is not None and first < release:
            counters['early'] += 1
        if stop is None:
            job.incomplete, job.observedUntil = True, end
        else:
            job.completionTime = stop
        job.rangeStart = start                      # Start of the NVTX range (the job's own start mark)
        job.gpuWork, job.gpuWaits = [], []
        jobs.append(job)
        bounds.append((start, end if stop is None else stop))

    # Releases announced but no job started before the trace ended: released, never ran. A thread that
    # announces its next release and then exits (end of the application) has no such job.
    last = end if gtid not in export.exits else min(end, export.exits[gtid] - zero)
    for number in range(len(ranges), len(releases)):
        release = None if releases[number] is None else releases[number] - zero
        if release is None or release > last:
            continue
        job = TraceJob(task, (jobs[-1].id + 1) if jobs and isinstance(jobs[-1].id, int) else number, release, None)
        if task.period is not None:
            job.deadline = release + task.period
        job.incomplete, job.observedUntil = True, last
        job.gpuWork, job.gpuWaits = [], []
        jobs.append(job)
        counters['pending'] += 1

    # Execution outside the jobs (start-up, between the end of a job and the next sleep): kept in the row
    # as jobs flagged outsideJob, incomplete and uncertain, so statistics, CSVs and deadlines ignore them.
    # One per execution interval: a job spanning the sleep between two jobs would look ready (gray) there.
    edges = [(-float('inf'), -float('inf'))] + sorted(bounds) + [(float('inf'), float('inf'))]
    for (_, left), (right, _) in zip(edges, edges[1:]):
        for start, stop, core in _clip(intervals, left, right):
            job = TraceJob(task, None, start, None)
            _add_intervals(job, [(start, stop, core)])
            job.outsideJob = True
            job.incomplete = job.uncertain = True
            job.releaseKnown = False
            job.observedUntil = stop
            job.gpuWork, job.gpuWaits = [], []
            jobs.append(job)
    jobs.sort(key=lambda job: (job.getFinishTime(), job.releaseTime))
    task.jobs = jobs
    return task


def _thread_row(gtid, export, intervals, end):
    """A thread without NVTX jobs: every execution interval is one job, like an ISR row."""
    task = TraceTask(thread_id(gtid), f'{_thread_name(gtid, export)} ({thread_id(gtid)})', None, _THREAD_COLOR)
    task.kind, task.traceFormat = 'thread', 3
    task.tid, task.pid = thread_id(gtid), process_id(gtid)
    for number, (start, stop, core) in enumerate(intervals):
        job = TraceJob(task, number, start, None)
        _add_intervals(job, [(start, stop, core)])
        if stop >= end:
            job.incomplete, job.observedUntil = True, stop
        else:
            job.completionTime = stop
        task.jobs.append(job)
    return task


def _job_at(task, time):
    """The job of a task row whose NVTX range contains time, or None."""
    if not hasattr(task, 'rangeIndex'):           # (range start, job) of the jobs with an NVTX range, by start
        task.rangeIndex = sorted(((job.rangeStart, job) for job in task.jobs if hasattr(job, 'rangeStart')),
                                 key=lambda entry: entry[0])
        task.rangeStarts = [start for start, _ in task.rangeIndex]
    index = bisect_right(task.rangeStarts, time) - 1
    if index >= 0:
        job = task.rangeIndex[index][1]
        if time <= job.getFinishTime():
            return job
    return None


def _gpu_rows(export, tasks, zero):
    """One row per (device, stream); each GPU operation is a job released at its launch call."""
    by_thread = {task.tid: task for task in tasks}
    rows, late = {}, 0
    for op in export.gpu_ops:
        key = op.device, op.stream
        if key not in rows:
            row = TraceTask(-1 - len(rows), f'GPU{op.device} Stream {op.stream}', None, _GPU_COLOR)
            row.kind, row.traceFormat, row.device, row.stream = 'gpu', 3, op.device, op.stream
            row.launchers = set()
            rows[key] = row
        row = rows[key]
        call = export.calls.get(op.correlation)
        launch = (call[0] - zero) if call is not None else op.start - zero
        job = TraceJob(row, len(row.jobs), launch, None)
        job.releaseKnown = call is not None
        _add_intervals(job, [(op.start - zero, op.end - zero, f'GPU{op.device}')])
        job.completionTime = op.end - zero
        job.operation, job.name = op.kind, op.name
        job.launchedBy = None
        if call is not None:
            task = by_thread.get(thread_id(call[2]))
            if task is not None:
                job.launchedBy = task
                row.launchers.add(task.name)
                owner = _job_at(task, launch)
                if owner is not None:
                    owner.gpuWork.append(job)
                    late += owner.getFinishTime() < job.completionTime
        row.jobs.append(job)
    for row in rows.values():
        if len(row.launchers) == 1:                 # Name the stream after the only task that uses it
            row.name += f' ({next(iter(row.launchers))})'
    return [rows[key] for key in sorted(rows)], late


def _attach_waits(export, tasks, zero):
    """Waits for the GPU (cudaEventSynchronize, ...) as job.gpuWaits [(start, stop)] of the waiting job."""
    by_thread = {task.tid: task for task in tasks}
    for start, stop, correlation in export.syncs:
        call = export.calls.get(correlation)
        task = None if call is None else by_thread.get(thread_id(call[2]))
        job = None if task is None else _job_at(task, start - zero)
        if job is not None:
            job.gpuWaits.append((start - zero, stop - zero))


def describe(info):
    """The info of build_rows() as text lines for the user."""
    rows = ', '.join(f'{count} {kind}' for kind, count in info['rows'].items())
    lines = [f"{', '.join(info['processes']) or 'no process'}: {rows}, {info['cpus']} CPU(s)"
             + (f", GPU {', '.join(info['gpus'])}" if info['gpus'] else ''),
             f"time zero: {info['zero']}; {info['clockSyncs']} clock_sync mark(s); "
             f"trace end {info['end'] / MS:.3f} ms; Nsight Systems {info['version']}"]
    return lines


def find_export(folder):
    """The SQLite export in a recording folder (exactly one .sqlite file)."""
    files = sorted(name for name in os.listdir(folder) if name.endswith('.sqlite'))
    if len(files) != 1:
        found = ', '.join(files) if files else 'none'
        raise JetsonTraceError(f'expected one .sqlite file (nsys export --type sqlite) in {folder}, found {found}')
    return os.path.join(folder, files[0])


def read_trace(path):
    """Parse an Nsight Systems SQLite export. Returns (rows, info, warnings)."""
    return build_rows(read_export(path))


# ---------------------------------------------------------------------------------------------- GUI

def loadRecording(gui):
    import HelperFunctions
    import DataExport
    rows, info, warnings = read_trace(find_export(gui.viewingFolder))
    for line in describe(info):
        HelperFunctions.printState('Jetson', info=line)
    for warning in warnings:
        HelperFunctions.printState('Warning', info=warning)
    DataExport.exportData(gui, rows, overwrite=True)
    return rows


def parser_thread(gui):
    import HelperFunctions
    try:
        return loadRecording(gui)
    except Exception as error:
        HelperFunctions.reportError('Parsing failed', error, gui.viewingFolder, 'parse_error.log',
                                    'Check the .sqlite export of nsys; details are in parse_error.log.')
        return None


def parseTraceFiles(gui, numCores=None):
    """loadTraceFunc of the Jetson target. The number of CPUs comes from the trace."""
    import HelperFunctions
    HelperFunctions.startWorker(gui, parser_thread, gui.loadingFinished)


def noRecording(gui):
    """recordTraceFunc of the Jetson target: recording runs on the Jetson for now."""
    import HelperFunctions
    HelperFunctions.printState('Not supported', info='record with nsys on the Jetson, see the README')
    gui.btn_recordTrace.configure(text="Record Trace", state="disabled")
