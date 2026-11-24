"""Nsight Systems SQLite exports of the Jetson: reading, rows, jobs, releases, GPU streams and the CLI."""
import contextlib
import csv
import importlib.util
import io
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import analysis
from analysis.__main__ import main as analysis_main
import DataExport
import TraceParserJetson as jetson
from timeunits import US, MS

EXAMPLE = ROOT / 'examples/jetsonThor/thor_test.sqlite'
PID = 100
OFFSET = 7_000_000_000          # CLOCK_MONOTONIC_RAW = trace time + OFFSET
ZERO = 10_000                   # Trace time of the common release
MARK, THREAD, RANGE, DOMAIN = 34, 39, 59, 75


def gtid(tid, pid=PID):
    return 1 << 48 | pid << 24 | tid


def raw(time):
    """CLOCK_MONOTONIC_RAW of a time relative to the release, as the application writes it."""
    return ZERO + time + OFFSET


class Nsys:
    """Builds a minimal SQLite export with the tables and columns the parser reads."""

    def __init__(self, sched=True, schema=(3, 24)):
        self.sched, self.schema = [] if sched else None, schema
        self.strings, self.names, self.nvtx, self.calls, self.kernels, self.syncs = [], [], [], [], [], []
        self.diagnostics = []

    def string(self, value):
        self.strings.append(value)
        return len(self.strings)

    def thread(self, tid, name, nvtx=False):
        self.names.append((self.string(name), 20, gtid(tid)))
        if nvtx:
            self.nvtx.append((1000, None, THREAD, gtid(tid), name, None, 0, None, None))
        return self

    def switch(self, time, cpu, into, tid, state=0):
        if self.sched is not None:              # None: the recording has no context switches
            self.sched.append((ZERO + time, cpu, int(into), gtid(tid), state))
        return self

    def mark(self, time, tid, name, value):
        self.nvtx.append((ZERO + time, None, MARK, gtid(tid), name, None, 1, value, None))
        return self

    def job(self, start, end, tid, name, ident):
        self.nvtx.append((ZERO + start, ZERO + end, RANGE, gtid(tid), name, None, 1, ident, None))
        return self

    def call(self, start, end, tid, correlation, name):
        self.calls.append((ZERO + start, ZERO + end, gtid(tid), correlation, self.string(name)))
        return self

    def kernel(self, start, end, stream, correlation, name):
        self.kernels.append((ZERO + start, ZERO + end, 0, stream, correlation, self.string(name)))
        return self

    def save(self, path):
        db = sqlite3.connect(path)
        meta = [('EXPORT_PRODUCT_VERSION', '2025.6.1.196'), ('EXPORT_SCHEMA_VERSION_MAJOR', str(self.schema[0])),
                ('EXPORT_SCHEMA_VERSION_MINOR', str(self.schema[1]))]
        tables = {
            'META_DATA_EXPORT(name, value)': meta,
            'StringIds(id, value)': list(enumerate(self.strings, 1)),
            'TARGET_INFO_GPU(id, name)': [(0, 'NVIDIA Thor')],
            'PROCESSES(globalPid, pid, name)': [(PID << 24, PID, './app')],
            'ThreadNames(nameId, priority, globalTid)': self.names,
            'NVTX_EVENTS(start, end, eventType, globalTid, text, textId, domainId, uint64Value, int64Value)':
                [(0, None, DOMAIN, gtid(1), jetson.NVTX_DOMAIN, None, 1, None, None)] + self.nvtx,
            'CUPTI_ACTIVITY_KIND_RUNTIME(start, end, globalTid, correlationId, nameId)': self.calls,
            'CUPTI_ACTIVITY_KIND_KERNEL(start, end, deviceId, streamId, correlationId, shortName)': self.kernels,
            'CUPTI_ACTIVITY_KIND_SYNCHRONIZATION(start, end, correlationId)': self.syncs,
            'DIAGNOSTIC_EVENT(timestamp, severity, text)': self.diagnostics,
        }
        if self.sched is not None:
            tables['SCHED_EVENTS(start, cpu, isSchedIn, globalTid, threadState)'] = self.sched
        for table, rows in tables.items():
            db.execute(f'CREATE TABLE {table}')
            if rows:
                db.executemany(f'INSERT INTO {table.split("(")[0]} VALUES ({", ".join("?" * len(rows[0]))})', rows)
        db.commit()
        db.close()
        return path


def scenario(**options):
    """
    Ctrl (tid 11, 1 ms, prio 90): job 1 preempted 100-150 us and migrated; job 2; a third release before the
    thread exits (released, never ran) and a fourth after it. Gpu (tid 12, prio 80): one job that launches a
    kernel on stream 13 and waits for it. helper (tid 13): no NVTX. early (tid 14): runs since the start.
    Times are ns relative to the common release.
    """
    n = Nsys(**options)
    n.thread(11, 'Ctrl', nvtx=True).thread(12, 'Gpu', nvtx=True).thread(13, 'helper').thread(14, 'early')
    n.mark(-9_000, 11, 'clock_sync', raw(-9_000) - 50).mark(-8_900, 11, 'clock_sync', raw(-8_900) - 10)
    n.mark(-8_000, 11, 'release', raw(0) - 10)
    for tid, period, priority in ((11, 1, 90), (12, 2, 80)):
        n.mark(-7_000, tid, 'period_ms', period).mark(-7_000, tid, 'priority', priority)
    for k in range(4):
        n.mark(-6_000 + k, 11, 'next_release', raw(k * MS) - 10)
    n.mark(-6_000, 12, 'next_release', raw(0) - 10)
    n.job(5_000, 205_000, 11, 'Ctrl', 1).job(1_003_000, 1_303_000, 11, 'Ctrl', 2)
    n.switch(4_000, 1, True, 11).switch(100_000, 1, False, 11).switch(150_000, 2, True, 11)
    n.switch(210_000, 2, False, 11).switch(1_002_000, 1, True, 11).switch(1_310_000, 1, False, 11)
    n.switch(2_400_000, 1, True, 11).switch(2_500_000, 1, False, 11, state=5)      # Exits at 2.5 ms
    n.job(10_000, 500_000, 12, 'Gpu', 1)
    n.switch(10_000, 3, True, 12).switch(50_000, 3, False, 12).switch(450_000, 3, True, 12)
    n.switch(500_000, 3, False, 12)
    n.call(40_000, 45_000, 12, 7, 'cudaLaunchKernel_v7000').kernel(60_000, 440_000, 13, 7, 'spin_kernel')
    n.call(45_500, 449_500, 12, 8, 'cudaEventSynchronize_v3020')
    n.syncs.append((ZERO + 46_000, ZERO + 449_000, 8))
    n.switch(-5_000, 0, True, 13).switch(-1_000, 0, False, 13).switch(600_000, 0, True, 13)   # Runs until the end
    n.switch(-8_000, 4, False, 14)
    n.diagnostics += [(0, 1, 'Profiling has started.'), (5, 2, 'Something to know.')]
    return n


def real(task):
    return [job for job in task.jobs if not getattr(job, 'outsideJob', False)]


def intervals(job):
    return [(i.start, i.stop, i.core) for i in job.execIntervals]


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def load(self, builder):
        return jetson.read_trace(str(builder.save(self.folder / 'trace.sqlite')))

    def test_rows_clock_and_time_zero(self):
        rows, info, warnings = self.load(scenario())
        self.assertEqual([(r.kind, r.name) for r in rows],
                         [('task', 'Ctrl'), ('task', 'Gpu'), ('gpu', 'GPU0 Stream 13 (Gpu)'),
                          ('thread', 'helper (13)'), ('thread', 'early (14)')])
        self.assertEqual(warnings, ['nsys: Something to know.'])
        self.assertEqual((info['zero'], info['clockSyncs'], info['cpus'], info['end']),
                         ('common release (NVTX mark release)', 2, 5, 2_500_000))
        ctrl = rows[0]
        self.assertEqual((ctrl.priority, ctrl.period, ctrl.tid, ctrl.deadlinesExplicit), (90, 1 * MS, 11, True))

    def test_jobs_releases_deadlines_and_cpu_intervals(self):
        ctrl = self.load(scenario())[0][0]
        first, second, pending = real(ctrl)
        # The clock_sync mark with the shortest delay (10 ns) defines the offset: releases are exact
        self.assertEqual([(j.id, j.releaseTime, j.deadline) for j in (first, second)], [(1, 0, MS), (2, MS, 2 * MS)])
        self.assertEqual(intervals(first), [(5_000, 100_000, 1), (150_000, 205_000, 2)])
        self.assertEqual((first.completionTime, second.getStartTime(), second.completionTime),
                         (205_000, 1_003_000, 1_303_000))
        # Released at 2 ms before the thread exited at 2.5 ms, never ran; the release at 3 ms is after the exit
        self.assertEqual((pending.releaseTime, pending.incomplete, pending.execIntervals, pending.observedUntil),
                         (2 * MS, True, [], 2_500_000))

    def test_execution_outside_jobs_stays_visible_but_out_of_statistics(self):
        ctrl = self.load(scenario())[0][0]
        outside = [job for job in ctrl.jobs if getattr(job, 'outsideJob', False)]
        # One job per execution interval: none spans the sleep between two jobs (the view would draw it ready)
        self.assertEqual([intervals(job) for job in outside],
                         [[(4_000, 5_000, 1)], [(205_000, 210_000, 2)], [(1_002_000, 1_003_000, 1)],
                          [(1_303_000, 1_310_000, 1)], [(2_400_000, 2_500_000, 1)]])
        self.assertTrue(all(job.getFinishTime() - job.releaseTime == job.getExecutionTime() for job in outside))
        self.assertTrue(all(job.incomplete and job.uncertain and not job.releaseKnown for job in outside))
        self.assertEqual([j.getFinishTime() for j in ctrl.jobs], sorted(j.getFinishTime() for j in ctrl.jobs))
        DataExport.exportCsvFiles(self.folder, [ctrl])
        rows = list(csv.reader((self.folder / 'Ctrl.csv').read_text().splitlines()))
        self.assertEqual([row[0] for row in rows[1:]], ['1', '2'])           # Only the jobs, in ns
        self.assertEqual(rows[1][1:6], ['0', '5000', '205000', '150000', '1000000'])
        self.assertEqual(ctrl.getMaxExecutionTime(), 300_000)

    def test_gpu_stream_rows_and_waits(self):
        rows = self.load(scenario())[0]
        gpu_task, stream = rows[1], rows[2]
        kernel, = stream.jobs
        self.assertEqual((kernel.releaseTime, intervals(kernel), kernel.completionTime, kernel.name, kernel.operation),
                         (40_000, [(60_000, 440_000, 'GPU0')], 440_000, 'spin_kernel', 'kernel'))
        self.assertIs(kernel.launchedBy, gpu_task)
        job, = real(gpu_task)
        self.assertEqual((job.gpuWork, job.gpuWaits), ([kernel], [(46_000, 449_000)]))
        self.assertEqual(intervals(job), [(10_000, 50_000, 3), (450_000, 500_000, 3)])

    def test_preemptions_ignore_waits_for_the_gpu(self):
        rows = self.load(scenario())[0]
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ctx = analysis.AnalysisContext(rows, folder, jetson.PLATFORM)
            result = analysis.run_analysis(analysis.discover()[0]['preemptions'], ctx)
        # Ctrl: job 1 preempted once and migrated once; Gpu: the pause while it waits for its kernel is no preemption
        self.assertEqual(result.tables[0].rows, [['Ctrl', 2, 1, 1, 1, 1], ['Gpu', 1, 0, 0, 0, 0]])

    def test_threads_without_jobs(self):
        rows = self.load(scenario())[0]
        helper, early = rows[3], rows[4]
        self.assertEqual([intervals(job) for job in helper.jobs], [[(-5_000, -1_000, 0)], [(600_000, 2_500_000, 0)]])
        self.assertEqual([job.incomplete for job in helper.jobs], [False, True])     # Still running at the end
        self.assertEqual(intervals(early.jobs[0]), [(-ZERO, -8_000, 4)])               # Running since the start

    def test_missing_scheduling_and_clock(self):
        n = scenario(sched=False, schema=(3, 30))
        n.nvtx = [row for row in n.nvtx if row[4] != 'clock_sync']
        rows, info, warnings = self.load(n)
        text = '\n'.join(warnings)
        self.assertIn('export schema 3.30', text)
        self.assertIn('no CPU context switches', text)
        self.assertIn('no clock_sync mark', text)
        ctrl = rows[0]
        first = real(ctrl)[0]
        # Without the clock, the release is the job start (estimated) and the time zero the recording start
        self.assertEqual((info['zero'], first.releaseTime, first.releaseEstimated, first.execIntervals),
                         ('start of the recording', ZERO + 5_000, True, []))

    def test_jobs_before_their_release_are_reported(self):
        n = scenario()
        n.nvtx = [row for row in n.nvtx if row[4] != 'clock_sync']
        n.mark(-8_800, 11, 'clock_sync', raw(-8_800) - 20_000)     # Read 20 us before the mark: releases too late
        warnings = self.load(n)[2]
        self.assertTrue(any('start before their release' in w for w in warnings), warnings)

    def test_not_an_export(self):
        path = self.folder / 'other.sqlite'
        sqlite3.connect(path).execute('CREATE TABLE x(a)').connection.close()
        with self.assertRaisesRegex(jetson.JetsonTraceError, 'no Nsight Systems export'):
            jetson.read_trace(str(path))
        (self.folder / 'text.sqlite').write_text('no database')
        with self.assertRaisesRegex(jetson.JetsonTraceError, 'expected one .sqlite file'):
            jetson.find_export(self.folder)


@unittest.skipUnless(EXAMPLE.is_file(), 'example trace missing')
class Example(unittest.TestCase):
    """The reference trace: four periodic tasks, two of them launch kernels and wait for them."""

    @classmethod
    def setUpClass(cls):
        cls.rows, cls.info, cls.warnings = jetson.read_trace(str(EXAMPLE))
        cls.tasks = {row.name: row for row in cls.rows if row.kind == 'task'}

    def test_rows(self):
        self.assertEqual({name: (len(real(task)), task.period // MS, task.priority) for name, task in self.tasks.items()},
                         {'Task_CpuA': (176, 20, 98), 'Task_GpuB': (88, 40, 97),
                          'Task_GpuA': (71, 50, 96), 'Task_CpuB': (36, 100, 95)})
        self.assertEqual([row.name for row in self.rows if row.kind == 'gpu'],
                         ['GPU0 Stream 7', 'GPU0 Stream 13 (Task_GpuA)', 'GPU0 Stream 14 (Task_GpuB)'])
        self.assertEqual((self.info['cpus'], self.info['clockSyncs']), (14, 20))
        self.assertEqual(len(self.warnings), 1)         # Only the Unified Memory note of nsys
        self.assertIn('Unified Memory', self.warnings[0])

    def test_releases_and_jobs(self):
        for task in self.tasks.values():
            jobs = real(task)
            self.assertEqual([job.id for job in jobs], list(range(1, len(jobs) + 1)))
            self.assertLess(abs(jobs[0].releaseTime), 1 * US)              # The first release is the time zero
            # The application sleeps on CLOCK_MONOTONIC, the trace runs on CLOCK_MONOTONIC_RAW: they differ by
            # about 7 ppm (700 ns per 100 ms), and an NTP correction shifts every task once by about 3.6 us
            steps = {b.releaseTime - a.releaseTime for a, b in zip(jobs, jobs[1:])}
            self.assertTrue(all(abs(step - task.period) < 10 * US for step in steps), task.name)
            self.assertTrue(all(job.getStartTime() >= job.releaseTime for job in jobs))

    def test_gpu_work_belongs_to_the_jobs(self):
        for name, kernel_ms in (('Task_GpuA', 10), ('Task_GpuB', 8)):
            jobs = real(self.tasks[name])
            self.assertTrue(all(len(job.gpuWork) == 1 and len(job.gpuWaits) == 1 for job in jobs))
            work = [job.gpuWork[0] for job in jobs]
            self.assertTrue(all(abs(k.completionTime - k.execIntervals[0].start - kernel_ms * MS) < 10 * US for k in work))

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                tempfile.TemporaryDirectory() as folder:
            shutil.copy(EXAMPLE, folder)
            ctx = analysis.load_recording(folder)
            self.assertEqual((ctx.platform, ctx.cores), ('Jetson Thor', 14))
            code = analysis_main([str(Path(folder) / EXAMPLE.name), 'preemptions'])
        self.assertEqual(code, 0)
        self.assertIn('Task_GpuA    71           24', out.getvalue())


def make_view(*args):
    """make_view() of test_trace_view: the production TraceView without a window."""
    spec = importlib.util.spec_from_file_location('trace_view_tests', ROOT / 'tests/test_trace_view.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_view(*args)


class View(unittest.TestCase):
    def test_gpu_intervals_use_the_row_color_and_cores_beyond_the_palette(self):
        from TraceTask import TraceTask, TraceJob, TraceInterval, ExecutionType
        view = make_view(0, 1000)
        view.cores, view.coreColors = 14, list(view.CORE_COLORS)
        row = TraceTask(-1, 'GPU0 Stream 1', None, '#9CCB5A')
        job = TraceJob(row, 0, 0, None)
        for start, stop, core in ((100, 200, 'GPU0'), (300, 400, 13), (500, 600, 13 + len(view.coreColors))):
            interval = TraceInterval(start, core, ExecutionType.EXECUTE)
            interval.stop = stop
            job.execIntervals.append(interval)
        view.drawJobsSection(row, job, 0, None)
        colors = [fill for _, _, fill in view.rectangles]
        self.assertEqual(colors[0], '#9CCB5A')
        self.assertEqual(colors[1], colors[2])          # Core 13 and core 13 + palette length share a color
        self.assertEqual(colors[1], '#%02X%02X%02X' % view.CORE_COLORS[13])

    def test_axis_labels_down_to_nanoseconds(self):
        view = make_view()
        for scale, tick, label in ((1, 500, '500 ns'), (US, 20 * US, '20 us'), (MS, 3 * MS, '3 ms'), (10 ** 9, 2 * 10 ** 9, '2 s')):
            view.tickScale = scale
            self.assertEqual(view.getTimeString(tick), label)


if __name__ == '__main__':
    unittest.main()
