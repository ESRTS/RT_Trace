"""Analysis plugins: discovery, runner, built-in analyses and the command lines."""
import configparser
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import analysis
from analysis import Analysis, AnalysisContext, Result, Table
from analysis.__main__ import main as analysis_main
from analysis.report import format_table, run_analysis
from simulator import Simulator, TraceWriter, get_policy, parse_taskset
from simulator.__main__ import main as simulator_main
from TraceParserSim import parse_lines
from TraceTask import TraceTask, TraceJob, ExecutionType

TASKSET = {'cores': 1, 'scheduler': 'fp', 'tasks': [
    {'name': 'High', 'period': 10, 'wcet': 4, 'priority': 2},
    {'name': 'Low', 'period': 20, 'wcet': 9, 'deadline': 12, 'priority': 1}]}


def simulated_tasks(taskset=TASKSET, horizon=40):
    stream = io.StringIO()
    Simulator(parse_taskset(taskset), get_policy(taskset['scheduler']), TraceWriter(stream)).run(horizon)
    header, tasks, warnings = parse_lines(stream.getvalue().splitlines())
    return header, tasks, stream.getvalue()


def captured(function, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = function(*args, **kwargs)
    return result, out.getvalue(), err.getvalue()


def write_plugin(folder, filename, source):
    path = Path(folder) / filename
    path.write_text(textwrap.dedent(source))
    return path


PLUGIN = '''
    from analysis import Analysis, Result, Table

    class Count(Analysis):
        name = 'count'
        title = 'Job count'
        def run(self, ctx):
            with open(ctx.output_path('count.txt'), 'w') as file:
                file.write(str(sum(len(t.jobs) for t in ctx.tasks)))
            return Result(headline='VERSION', tables=[Table(['Task', 'Jobs'], [[t.name, len(t.jobs)] for t in ctx.tasks],
                                                            csv='count.csv')])
'''


class Discovery(unittest.TestCase):
    def test_builtins_first_and_default_is_statistics(self):
        available, errors = analysis.discover()
        self.assertEqual(list(available)[:4], ['statistics', 'deadline_misses', 'response_times', 'preemptions'])
        self.assertEqual(errors, [])
        self.assertEqual(analysis.DEFAULT, ('statistics',))

    def test_plugins_from_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            write_plugin(folder, 'count.py', PLUGIN)
            write_plugin(folder, '_helper.py', 'raise RuntimeError("not imported")')
            write_plugin(folder, 'notes.txt', 'ignored')
            available, errors = analysis.discover([folder])
        self.assertEqual(errors, [])
        self.assertEqual(available['count'].title, 'Job count')

    def test_edits_take_effect_on_the_next_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_plugin(folder, 'count.py', PLUGIN)
            first = analysis.discover([folder])[0]['count']
            path.write_text(path.read_text().replace("title = 'Job count'", "title = 'Jobs'"))
            self.assertEqual(analysis.discover([folder])[0]['count'].title, 'Jobs')
            self.assertEqual(first.title, 'Job count')

    def test_broken_plugins_are_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            write_plugin(folder, 'a_syntax.py', 'def broken(:\n')
            write_plugin(folder, 'b_empty.py', 'x = 1\n')
            write_plugin(folder, 'c_clash.py', '''
                from analysis import Analysis
                class Mine(Analysis):
                    name = 'statistics'
                    title = 'Mine'
            ''')
            write_plugin(folder, 'd_good.py', PLUGIN)
            available, errors = analysis.discover([folder, os.path.join(folder, 'missing')])
        self.assertIn('count', available)
        self.assertEqual(available['statistics'], analysis.BUILTIN[0])
        self.assertEqual(len(errors), 4)
        self.assertIn('SyntaxError', errors[0])
        self.assertIn('defines no subclass', errors[1])
        self.assertIn("'statistics' is already used", errors[2])
        self.assertIn('plugin folder not found', errors[3])

    def test_plugin_folders_from_config(self):
        config = configparser.ConfigParser()
        config.read_string('[analysis]\nplugin_folders = plugins ; /abs/other\n  ~/more\n')
        self.assertEqual(analysis.plugin_folders(config, '/base'),
                         ['/base/plugins', '/abs/other', os.path.expanduser('~/more')])
        self.assertEqual(analysis.plugin_folders(configparser.ConfigParser(), '/base'), [])


class Runner(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        _, tasks, _ = simulated_tasks()
        self.ctx = AnalysisContext(tasks, self.folder.name)

    def test_files_and_csv_tables_go_to_the_analysis_folder(self):
        with tempfile.TemporaryDirectory() as plugins:
            write_plugin(plugins, 'count.py', PLUGIN)
            count = analysis.discover([plugins])[0]['count']
        result, out, _ = captured(run_analysis, count, self.ctx)
        self.assertEqual(result.headline, 'VERSION')
        folder = Path(self.folder.name) / 'analysis'
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['count.csv', 'count.txt'])
        self.assertEqual((folder / 'count.csv').read_text().splitlines()[0], 'Task,Jobs')
        self.assertIn('=> JOB COUNT VERSION', out)
        self.assertEqual(out.count('=> SAVED'), 2)

    def test_errors_are_reported_not_raised(self):
        class Broken(Analysis):
            name, title = 'broken', 'Broken'
            def run(self, ctx):
                return 1 / 0

        class WrongType(Analysis):
            name, title = 'wrong', 'Wrong'
            def run(self, ctx):
                return 'text'

        result, out, err = captured(run_analysis, Broken, self.ctx)
        self.assertIsNone(result)
        self.assertIn('=> ERROR ANALYSIS BROKEN FAILED ZeroDivisionError', out)
        self.assertIn('Traceback', err)
        self.assertIsNone(captured(run_analysis, WrongType, self.ctx)[0])
        allRan, out, _ = captured(analysis.run_analyses, [Broken, analysis.BUILTIN[0]], self.ctx)
        self.assertFalse(allRan)
        self.assertIn('=> TASK STATISTICS', out)        # The next analysis still runs

    def test_check_skips_without_failing(self):
        class NeedsTaskSet(Analysis):
            name, title = 'needs', 'Needs'
            def check(self, ctx):
                return None if ctx.taskset else 'needs a task set'
            def run(self, ctx):
                raise AssertionError('not reached')

        result, out, _ = captured(run_analysis, NeedsTaskSet, self.ctx)
        self.assertIsNotNone(result)
        self.assertIn('=> NEEDS SKIPPED needs a task set', out)

    def test_table_format(self):
        table = Table(['Task', 'Time', 'Share'], [['A', 1.23456, 0.5], ['Long name', None, 0.125], ['C', 2, 1]],
                      formats=[None, '.2f', '.1%'], max_rows=2, csv='t.csv', title='Title')
        self.assertEqual(format_table(table), [
            '  Title',
            '  Task       Time  Share',
            '  ----------------------',
            '  A          1.23  50.0%',
            '  Long name     -  12.5%',
            '  ... 1 more row(s) in t.csv'])


def job(task, ident, release, intervals, deadline=None, incomplete=False):
    result = TraceJob(task, ident, release, deadline)
    for start, stop, core in intervals:
        result.startExec(start, core, ExecutionType.EXECUTE)
        result.stopExec(stop)
    result.completionTime = None if incomplete else intervals[-1][1]
    result.incomplete = incomplete
    task.jobs.append(result)
    return result


class Builtins(unittest.TestCase):
    def run_builtin(self, name, ctx):
        return captured(run_analysis, dict(analysis.discover()[0])[name], ctx)[0]

    def test_deadline_misses(self):
        _, tasks, _ = simulated_tasks()
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_builtin('deadline_misses', AnalysisContext(tasks, folder, trace_end=40))
            late = list(csv.reader((Path(folder) / 'analysis/deadline_misses.csv').read_text().splitlines()))
        # Low: released 0 and 20, preempted by High (0-4, 10-14, ...), completes 17 and 37, deadlines 12 and 32
        self.assertEqual(result.headline, '2 late job(s)')
        self.assertEqual(result.tables[0].rows, [['High', 4, 0, None, 0, 0], ['Low', 2, 2, 0.005, 0, 0]])
        self.assertEqual(late[1:], [['Low', '0', '0', '12', '17', '5', '0'], ['Low', '1', '20', '32', '37', '5', '0']])

    def test_unfinished_late_jobs_and_unknown_deadlines(self):
        task = TraceTask(1, 'T', 1, '#000')
        task.kind, task.deadlinesExplicit = 'task', True
        job(task, 0, 0, [(0, 3, 0)], deadline=10)
        job(task, 1, 10, [(10, 13, 0)])                                  # No deadline known
        job(task, 2, 20, [(20, 50, 0)], deadline=30, incomplete=True)    # Still running past its deadline
        irq = TraceTask(-1, 'IRQ', None, '#000')
        irq.kind = 'irq'
        job(irq, 0, 0, [(1, 2, 0)])
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_builtin('deadline_misses', AnalysisContext([task, irq], folder))
        self.assertEqual(result.tables[0].rows, [['T', 1, 0, None, 0, 1]])
        self.assertEqual(len(result.lines), 2)

    def test_response_times_and_preemptions(self):
        task = TraceTask(1, 'T', 1, '#000')
        job(task, 0, 0, [(1, 3, 0), (5, 6, 1)])           # Preempted once, migrated once
        job(task, 1, 10, [(10, 12, 0), (12, 14, 0)])      # Back-to-back intervals: no preemption
        with tempfile.TemporaryDirectory() as folder:
            ctx = AnalysisContext([task], folder)
            times = self.run_builtin('response_times', ctx)
            preemptions = self.run_builtin('preemptions', ctx)
        self.assertEqual(times.tables[0].rows, [['T', 2, 0.004, 0.005, 0.006, 0.002, 0.001]])
        self.assertEqual(preemptions.tables[0].rows, [['T', 2, 1, 1, 1, 1]])

    def test_statistics_match_the_task_methods(self):
        _, tasks, _ = simulated_tasks()
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_builtin('statistics', AnalysisContext(tasks, folder))
            self.assertFalse((Path(folder) / 'analysis').exists())
        high = result.tables[0].rows[0]
        self.assertEqual(high[:3], ['High', 0.004, 0.004])


class Recording(unittest.TestCase):
    def make_recording(self, folder):
        _, _, trace = simulated_tasks()
        (Path(folder) / 'sim_trace.jsonl').write_text(trace)
        (Path(folder) / 'taskset.json').write_text(json.dumps(TASKSET))

    def test_simulator_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make_recording(folder)
            ctx = analysis.load_recording(folder)
        self.assertEqual((ctx.platform, ctx.cores, ctx.trace_end), ('Simulator', 1, 40))
        self.assertEqual([t.name for t in ctx.taskset.tasks], ['High', 'Low'])

    def test_gui_context_reads_the_task_set(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make_recording(folder)
            _, tasks, _ = simulated_tasks()
            ctx = analysis.context_for_tasks(tasks, folder, 'Simulator', None)
        self.assertEqual((ctx.cores, ctx.trace_end, ctx.taskset.cores), (1, 40, 1))

    def test_not_a_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(analysis.RecordingError, 'no recording'):
                analysis.load_recording(folder)
            with self.assertRaisesRegex(analysis.RecordingError, 'not a recording folder'):
                analysis.load_recording(os.path.join(folder, 'missing'))


class CommandLine(unittest.TestCase):
    def test_analysis_cli(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as plugins:
            Recording().make_recording(folder)
            write_plugin(plugins, 'count.py', PLUGIN)
            code, out, _ = captured(analysis_main, [folder])
            self.assertEqual(code, 0)
            self.assertIn('=> TASK STATISTICS', out)
            self.assertNotIn('DEADLINE MISSES', out)

            code, out, _ = captured(analysis_main, [folder, 'deadline_misses', 'count', '--plugins', plugins])
            self.assertEqual(code, 0)
            self.assertIn('=> DEADLINE MISSES 2 late job(s)', out)
            self.assertTrue((Path(folder) / 'analysis/count.csv').is_file())

            code, out, _ = captured(analysis_main, ['--list', '--plugins', plugins])
            self.assertEqual(code, 0)
            self.assertIn('count', out)
            self.assertEqual(captured(analysis_main, [folder, 'nothing'])[0], 2)
            self.assertEqual(captured(analysis_main, [plugins])[0], 1)     # No recording

    def test_simulator_analyze_option(self):
        with tempfile.TemporaryDirectory() as folder:
            taskset = Path(folder) / 'taskset.json'
            taskset.write_text(json.dumps(TASKSET))
            trace = Path(folder) / 'out' / 'trace.jsonl'
            code, out, _ = captured(simulator_main, [str(taskset), '0.00004', '-o', str(trace),
                                                     '--analyze', 'deadline_misses'])
            self.assertEqual(code, 0)
            self.assertIn('=> DEADLINE MISSES 2 late job(s)', out)
            self.assertTrue((trace.parent / 'analysis/deadline_misses.csv').is_file())
            code, out, _ = captured(simulator_main, [str(taskset), '0.00004', '-o', str(trace), '--analyze'])
            self.assertIn('=> TASK STATISTICS', out)


if __name__ == '__main__':
    unittest.main()
