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
from analysis import Analysis, AnalysisContext, Input, Result, Table
from analysis.__main__ import main as analysis_main
from analysis.report import format_table, run_analysis
from simulator import Simulator, TraceWriter, get_policy, parse_taskset
from simulator.__main__ import main as simulator_main
from TraceParserSim import parse_lines
from TraceTask import TraceTask, TraceJob, ExecutionType
from timeunits import US

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


class Inputs(unittest.TestCase):
    """Input files that analyses read from the recording folder."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.folder))
        _, tasks, _ = simulated_tasks()
        self.ctx = AnalysisContext(tasks, str(self.folder))
        self.seen = {}
        seen = self.seen

        class Periods(Analysis):
            name, title = 'periods', 'Periods'
            inputs = {'spec': Input('spec.json', key='periods'),
                      'notes': Input('notes.txt', required=False),
                      'limits': Input('data/limits.csv', required=False)}
            def run(self, ctx):
                spec = ctx.input('spec')
                seen['periods'] = dict(spec), ctx.input('notes'), ctx.input('limits'), ctx.input_path('spec')
                spec['High'] = -1                               # Changes only this copy
                return Result(headline=f"{len(spec)} task(s)")

        class Budgets(Analysis):
            name, title = 'budgets', 'Budgets'
            inputs = {'spec': Input('spec.json', key='budgets')}
            def run(self, ctx):
                seen['budgets'] = ctx.input('spec')
                return Result()

        self.Periods, self.Budgets = Periods, Budgets

    def write(self, name, text):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))

    def test_missing_required_input_skips_with_the_file_name(self):
        result, out, _ = captured(run_analysis, self.Periods, self.ctx)
        self.assertIsNotNone(result)                            # Skipped, not failed
        self.assertIn(f"=> PERIODS SKIPPED needs spec.json (element 'periods') in {self.folder}", out)
        self.write('spec.json', '{"budgets": {"High": 4}}')     # The file without the element
        self.assertIn("needs spec.json (element 'periods')", captured(run_analysis, self.Periods, self.ctx)[1])
        self.assertNotIn('periods', self.seen)

    def test_one_file_shared_by_two_analyses(self):
        self.write('spec.json', '{"periods": {"High": 10, "Low": 20}, "budgets": {"Low": 9}}')
        self.write('notes.txt', 'free text')
        self.write('data/limits.csv', 'Task,Limit\nHigh,4\nLow,9\n')
        self.assertTrue(captured(analysis.run_analyses, [self.Periods, self.Budgets, self.Periods], self.ctx)[0])
        spec, notes, limits, path = self.seen['periods']
        self.assertEqual(spec, {'High': 10, 'Low': 20})         # The second run did not see the change of the first
        self.assertEqual(notes, 'free text')
        self.assertEqual(limits, [{'Task': 'High', 'Limit': '4'}, {'Task': 'Low', 'Limit': '9'}])
        self.assertEqual(path, str(self.folder / 'spec.json'))
        self.assertEqual(self.seen['budgets'], {'Low': 9})

    def test_optional_inputs_are_none_when_missing(self):
        self.write('spec.json', '{"periods": {}}')
        captured(run_analysis, self.Periods, self.ctx)
        self.assertEqual(self.seen['periods'][:3], ({}, None, None))

    def test_unreadable_input_fails_without_traceback(self):
        self.write('spec.json', '{"periods": {"High": 10,}\n')
        result, out, err = captured(run_analysis, self.Periods, self.ctx)
        self.assertIsNone(result)
        self.assertIn(f'=> ERROR ANALYSIS PERIODS INPUT {self.folder / "spec.json"}: line 1, column 25', out)
        self.assertEqual(err, '')
        self.write('spec.json', '[1, 2]')                       # An element needs a JSON object
        self.assertIn('expected a JSON object', captured(run_analysis, self.Periods, self.ctx)[1])

    def test_undeclared_input_is_an_error_of_the_analysis(self):
        class Undeclared(Analysis):
            name, title = 'undeclared', 'Undeclared'
            def run(self, ctx):
                return ctx.input('spec')

        result, out, _ = captured(run_analysis, Undeclared, self.ctx)
        self.assertIsNone(result)
        self.assertIn("'spec' is not an input of this analysis", out)

    def test_invalid_declarations_are_rejected_at_discovery(self):
        with tempfile.TemporaryDirectory() as plugins:
            write_plugin(plugins, 'bad.py', '''
                from analysis import Analysis, Input
                class A(Analysis):
                    name, title, inputs = 'a', 'A', {'x': 'spec.json'}
                class B(Analysis):
                    name, title, inputs = 'b', 'B', {'x': Input('../spec.json')}
                class C(Analysis):
                    name, title, inputs = 'c', 'C', {'x': Input('spec.csv', key='k')}
                class D(Analysis):
                    name, title, inputs = 'd', 'D', {'x': Input('specs/spec.json', key='k')}
            ''')
            available, errors = analysis.discover([plugins])
        self.assertEqual([name for name in 'abcd' if name in available], ['d'])
        self.assertEqual(len(errors), 3)
        self.assertIn('is not an analysis.Input', errors[0])
        self.assertIn('inside the recording folder', errors[1])
        self.assertIn('only possible for JSON', errors[2])

    def test_example_plugins(self):
        available, errors = analysis.discover([ROOT / 'examples/analysis_plugins'])
        self.assertEqual(errors, [])
        self.write('timing_spec.json', '{"periods": {"High": 10, "Low": 20, "Gone": 5}, "budgets": {"Low": 8}}')
        ctx = self.ctx
        result, out, _ = captured(run_analysis, available['period_check'], ctx)
        self.assertEqual(result.lines, ['not in the trace: Gone'])
        self.assertEqual([row[0] for row in result.tables[0].rows], ['High', 'Low'])
        self.assertEqual(result.tables[0].rows[0][-1], 0)        # Periodic releases: no deviation
        result, _, _ = captured(run_analysis, available['exec_budget'], ctx)
        self.assertEqual(result.tables[0].rows, [['Low', 0.008, 2, 2, 0.009]])
        self.assertTrue((self.folder / 'analysis/exec_budget.csv').is_file())


class Builtins(unittest.TestCase):
    def run_builtin(self, name, ctx):
        return captured(run_analysis, dict(analysis.discover()[0])[name], ctx)[0]

    def test_deadline_misses(self):
        _, tasks, _ = simulated_tasks()
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_builtin('deadline_misses', AnalysisContext(tasks, folder, trace_end=40 * US))
            late = list(csv.reader((Path(folder) / 'analysis/deadline_misses.csv').read_text().splitlines()))
        # Low: released 0 and 20, preempted by High (0-4, 10-14, ...), completes 17 and 37, deadlines 12 and 32
        self.assertEqual(result.headline, '2 late job(s)')
        self.assertEqual(result.tables[0].rows, [['High', 4, 0, None, 0, 0], ['Low', 2, 2, 0.005, 0, 0]])
        self.assertEqual(late[0][2:6], ['ReleaseTime_ns', 'Deadline_ns', 'FinishTime_ns', 'Lateness_ns'])
        self.assertEqual(late[1:], [['Low', '0', '0', '12000', '17000', '5000', '0'],
                                    ['Low', '1', '20000', '32000', '37000', '5000', '0']])

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
        u = US
        job(task, 0, 0, [(1 * u, 3 * u, 0), (5 * u, 6 * u, 1)])           # Preempted once, migrated once
        job(task, 1, 10 * u, [(10 * u, 12 * u, 0), (12 * u, 14 * u, 0)])  # Back-to-back intervals: no preemption
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
        self.assertEqual((ctx.platform, ctx.cores, ctx.trace_end), ('Simulator', 1, 40 * US))
        self.assertEqual([t.name for t in ctx.taskset.tasks], ['High', 'Low'])
        self.assertEqual([(t.period, t.wcet, t.deadline) for t in ctx.taskset.tasks],     # Like the trace: ns
                         [(10 * US, 4 * US, 10 * US), (20 * US, 9 * US, 12 * US)])

    def test_gui_context_reads_the_task_set(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make_recording(folder)
            _, tasks, _ = simulated_tasks()
            ctx = analysis.context_for_tasks(tasks, folder, 'Simulator', None)
        self.assertEqual((ctx.cores, ctx.trace_end, ctx.taskset.cores), (1, 40 * US, 1))

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
            code, out, _ = captured(analysis_main, ['--list', '--plugins', str(ROOT / 'examples/analysis_plugins')])
            self.assertIn("input: timing_spec.json (element 'budgets') - execution time budget", out)
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
