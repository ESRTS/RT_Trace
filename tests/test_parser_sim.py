"""Simulator trace (JSON lines) to TraceTask rows, and the GUI glue of the simulator target."""
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from simulator import Simulator, TraceWriter, get_policy, parse_taskset
from simulator import trace_format as fmt
from TraceParserSim import SimTraceError, parse_lines, read_trace

HEADER = json.dumps({'type': 'header', 'format': fmt.FORMAT, 'version': fmt.VERSION, 'cores': 2,
                     'time_unit': 'us', 'horizon': 100, 'scheduler': 'fp'})
TASK = json.dumps({'type': 'task', 'id': 1, 'name': 'A', 'priority': 1, 'period': 50, 'wcet': 10,
                   'deadline': 50, 'phase': 0, 'affinity': None})


def simulated(data, horizon):
    taskset = parse_taskset(data)
    stream = io.StringIO()
    Simulator(taskset, get_policy(taskset.scheduler), TraceWriter(stream)).run(horizon)
    return parse_lines(stream.getvalue().splitlines())


def lines(*events):
    return [HEADER, TASK] + [json.dumps(e) for e in events]


class RoundTrip(unittest.TestCase):
    def test_jobs_intervals_and_rows(self):
        header, tasks, warnings = simulated({'cores': 2, 'tasks': [
            {'name': 'Pinned', 'period': 10, 'wcet': 2, 'priority': 3, 'affinity': [0], 'phase': 1},
            {'name': 'Mid', 'period': 20, 'wcet': 4, 'priority': 2},
            {'name': 'Low', 'period': 20, 'wcet': 6, 'priority': 1}]}, 20)
        self.assertEqual((header['cores'], warnings), (2, []))
        self.assertEqual([t.name for t in tasks], ['Pinned', 'Mid', 'Low'])
        mid = tasks[1]
        self.assertEqual((mid.kind, mid.traceFormat, mid.deadlinesExplicit, mid.period), ('task', 3, True, 20))
        job = mid.jobs[0]
        self.assertEqual([(i.core, i.start, i.stop) for i in job.execIntervals], [(0, 0, 1), (1, 1, 4)])
        self.assertEqual((job.releaseTime, job.deadline, job.completionTime, job.getFinishTime()), (0, 20, 4, 4))
        self.assertFalse(job.incomplete or job.uncertain)

    def test_deadline_miss_overlapping_jobs(self):
        _, (task,), _ = simulated({'cores': 1, 'tasks': [
            {'name': 'Overrun', 'period': 10, 'wcet': 15, 'priority': 1}]}, 40)
        self.assertEqual([(j.id, j.releaseTime, j.deadline, j.completionTime) for j in task.jobs],
                         [(0, 0, 10, 15), (1, 10, 20, 30), (2, 20, 30, None), (3, 30, 40, None)])
        self.assertEqual(task.getMaxResponseTime(), 20)

    def test_open_jobs_are_incomplete_at_the_horizon(self):
        _, (task,), _ = simulated({'cores': 1, 'tasks': [
            {'name': 'Long', 'period': 100, 'wcet': 30, 'priority': 1}]}, 20)
        job = task.jobs[0]
        self.assertTrue(job.incomplete)
        self.assertEqual(([(i.start, i.stop) for i in job.execIntervals], job.getFinishTime()), ([(0, 20)], 20))

    def test_tasks_without_jobs_are_dropped(self):
        _, tasks, _ = simulated({'cores': 1, 'tasks': [
            {'name': 'Now', 'period': 10, 'wcet': 1, 'priority': 1},
            {'name': 'Later', 'period': 10, 'wcet': 1, 'priority': 2, 'phase': 500}]}, 100)
        self.assertEqual([t.name for t in tasks], ['Now'])


class Malformed(unittest.TestCase):
    def check(self, source, message):
        with self.assertRaisesRegex(SimTraceError, message):
            parse_lines(source)

    def test_errors(self):
        self.check([], 'empty trace')
        self.check(['{"type": "task"}'], 'first line must be')
        self.check([HEADER.replace('"version": 1', '"version": 9')], 'unsupported version 9')
        self.check(lines({'t': 0, 'type': 'start', 'task': 1, 'job': 0, 'core': 0}), 'was not released')
        self.check(lines({'t': 0, 'type': 'release', 'task': 2, 'job': 0, 'deadline': 5}), 'unknown task id 2')
        self.check(lines({'t': 5, 'type': 'release', 'task': 1, 'job': 0, 'deadline': 50},
                         {'t': 4, 'type': 'start', 'task': 1, 'job': 0, 'core': 0}), 'time goes backwards')
        self.check(lines({'t': 0, 'type': 'release', 'task': 1, 'job': 0, 'deadline': 50},
                         {'t': 0, 'type': 'start', 'task': 1, 'job': 0, 'core': 7}), 'invalid core 7')
        self.check(lines({'t': 0, 'type': 'release', 'task': 1, 'job': 0, 'deadline': 50},
                         {'t': 1, 'type': 'preempt', 'task': 1, 'job': 0, 'core': 0}), 'not running on core 0')
        self.check(lines({'t': 0, 'type': 'end'}, {'t': 0, 'type': 'end'}), "after the 'end'")
        self.check([HEADER, '{not json'], 'line 2: invalid JSON')

    def test_missing_end_is_a_warning(self):
        _, (task,), warnings = parse_lines(lines(
            {'t': 0, 'type': 'release', 'task': 1, 'job': 0, 'deadline': 50},
            {'t': 0, 'type': 'start', 'task': 1, 'job': 0, 'core': 0},
            {'t': 7, 'type': 'release', 'task': 1, 'job': 1, 'deadline': 57}))
        self.assertIn('cut off', warnings[0])
        self.assertEqual(task.jobs[0].execIntervals[0].stop, 7)


class SimulatorTarget(unittest.TestCase):
    """Recording and loading through the target functions, with a stand-in for the GUI."""

    def setUp(self):
        import HelperFunctions
        import SimulatorRecorder
        self.helpers, self.recorder = HelperFunctions, SimulatorRecorder
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        projects = self.tmp / 'projects'
        shutil.copytree(ROOT / 'examples/simulator', projects)
        (projects / 'firmware').mkdir()
        (projects / 'firmware' / 'CMakeLists.txt').write_text('')
        patcher = patch.object(HelperFunctions, 'projectPathOverride', str(projects))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_program_list_per_target(self):
        self.assertEqual(self.helpers.getPossiblePrograms(), ['firmware'])
        self.assertEqual(self.helpers.getPossiblePrograms(self.recorder.isTaskSetFolder), ['fp_two_cores'])

    def test_record_then_load(self):
        folder = self.tmp / 'data' / 'run'
        gui = types.SimpleNamespace(selectedProgram='fp_two_cores', recordingFolder=str(folder),
                                    recordingSeconds=0.04, viewingFolder=str(folder))
        with patch('builtins.print'):
            self.assertTrue(self.recorder.validateTaskSet(gui))
            self.assertEqual(self.recorder.simulation_thread(gui), 0)
        self.assertTrue((folder / 'taskset.json').is_file())
        header, tasks, _ = read_trace(folder / fmt.TRACE_FILE)
        self.assertEqual((header['horizon'], len(tasks)), (40000, 4))

        import TraceParserSim
        with patch('builtins.print'):
            loaded = TraceParserSim.loadRecording(gui)
        self.assertEqual([t.name for t in loaded], ['Control', 'Sensor', 'Comm', 'Logger'])
        self.assertTrue((folder / 'Control.csv').is_file())

    def test_invalid_task_set_is_rejected(self):
        gui = types.SimpleNamespace(selectedProgram='fp_two_cores')
        path = Path(self.helpers.getProjectPath()) / 'fp_two_cores' / 'taskset.json'
        data = json.loads(path.read_text())
        del data['tasks'][0]['priority']
        path.write_text(json.dumps(data))
        with patch.object(self.helpers, 'printState') as state:
            self.assertFalse(self.recorder.validateTaskSet(gui))
        self.assertIn("needs a 'priority'", state.call_args.kwargs['info'])


if __name__ == '__main__':
    unittest.main()
