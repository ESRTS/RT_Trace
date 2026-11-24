"""Per-task CSV export: deadline columns consistent with the trace view."""
import csv
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
import unittest.mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from TraceTask import TraceTask, TraceJob, ExecutionType
from simulator import Simulator, TraceWriter, get_policy, parse_taskset
from TraceParserSim import parse_lines


def load_export():
    # The CSV part must work without the GUI module HelperFunctions: importing it fails here.
    spec = importlib.util.spec_from_file_location('export_under_test', ROOT / 'src/DataExport.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'HelperFunctions': None}):
        spec.loader.exec_module(module)
    return module


Export = load_export()


def rows(task):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'task.csv'
        Export.createDataFile(path, task)
        with path.open() as file:
            return list(csv.reader(file))


def job(task, ident, release, start, stop, deadline=None):
    result = TraceJob(task, ident, release, deadline)
    result.startExec(start, 0, ExecutionType.EXECUTE)
    result.stopExec(stop)
    task.jobs.append(result)
    return result


class DeadlineColumns(unittest.TestCase):
    def test_header(self):
        self.assertEqual(rows(TraceTask(1, 'T', 1, '#000'))[0], ['JobId', 'ReleaseTime_ns', 'StartTime_ns', 'FinishTime_ns',
                                                            'ExecutionTime_ns', 'Deadline_ns', 'DeadlineEstimated'])

    def test_simulator_deadlines_are_exact(self):
        taskset = parse_taskset({'cores': 1, 'tasks': [
            {'name': 'Overrun', 'period': 10, 'wcet': 15, 'deadline': 8, 'priority': 1}]})
        stream = io.StringIO()
        Simulator(taskset, get_policy('fp'), TraceWriter(stream)).run(40)
        _, (task,), _ = parse_lines(stream.getvalue().splitlines())
        # Jobs 0 and 1 complete (15, 30) after their deadlines (8, 18); the others are incomplete.
        self.assertEqual(rows(task)[1:], [['0', '0', '0', '15000', '15000', '8000', '0'],      # ns
                                          ['1', '10000', '15000', '30000', '15000', '18000', '0']])

    def test_explicit_deadlines_keep_estimate_flag_and_unknown_stays_empty(self):
        task = TraceTask(1, 'FreeRTOS', 1, '#000')
        task.deadlinesExplicit = True
        job(task, 0, 0, 0, 3, deadline=10)
        job(task, 1, 10, 10, 13, deadline=20).deadlineEstimated = True
        job(task, 2, 20, 20, 23)
        self.assertEqual([row[-2:] for row in rows(task)[1:]], [['10', '0'], ['20', '1'], ['', '']])

    def test_without_explicit_deadlines_the_next_release_is_used(self):
        task = TraceTask(201, 'Linux', None, '#000')
        job(task, 0, 0, 1, 4)
        job(task, 1, 10, 11, 14)
        self.assertEqual([row[-2:] for row in rows(task)[1:]], [['10', '1'], ['', '']])

    def test_rows_with_unknown_deadline_are_kept_but_incomplete_jobs_are_not(self):
        task = TraceTask(201, 'Linux', None, '#000')
        job(task, 0, 0, 1, 4)
        job(task, 1, 10, 11, 14).incomplete = True
        self.assertEqual([row[0] for row in rows(task)[1:]], ['0'])
        self.assertEqual(rows(task)[1][-2:], ['10', '1'])     # The excluded job still provides the release


class Split(unittest.TestCase):
    def test_csv_export_without_gui_module(self):
        task = TraceTask(1, 'My Task', 1, '#000')
        job(task, 0, 0, 0, 3, deadline=10)
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'HelperFunctions': None}):
            written = Export.exportCsvFiles(folder, [task])
            self.assertEqual([Path(p).name for p in written], ['My_Task.csv'])
            self.assertEqual(Export.exportCsvFiles(folder, [task]), [])          # Existing files are kept
            self.assertEqual(len(Export.exportCsvFiles(folder, [task], overwrite=True)), 1)

    def test_file_names_are_valid_on_every_system(self):
        self.assertEqual([Export.csvFileName(name) for name in ('My Task', '[NSys] (12)', '*2Comms (13)', 'a/b:c?')],
                         ['My_Task', '[NSys]_(12)', '_2Comms_(13)', 'a_b_c_'])

    def test_export_data_still_prints_statistics(self):
        # The statistics are now the default analysis; no other analysis runs after loading.
        task = TraceTask(1, 'T', 1, '#000')
        job(task, 0, 0, 0, 3, deadline=10)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'HelperFunctions': None}), \
                patch('sys.stdout', output):
            Export.exportData(types.SimpleNamespace(viewingFolder=folder), [task], overwrite=True)
            self.assertTrue((Path(folder) / 'T.csv').is_file())
            self.assertFalse((Path(folder) / 'analysis').exists())
        self.assertIn('=> TASK STATISTICS', output.getvalue())
        self.assertNotIn('DEADLINE MISSES', output.getvalue())


if __name__ == '__main__':
    unittest.main()
