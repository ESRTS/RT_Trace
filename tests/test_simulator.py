"""Simulator engine and fixed-priority policy against hand-computed schedules."""
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from simulator import Simulator, TaskSetError, TraceWriter, get_policy, parse_taskset, load_taskset


def run(data, horizon):
    """Simulate a task-set dict; return the event records (header and task lines dropped)."""
    taskset = parse_taskset(data)
    stream = io.StringIO()
    Simulator(taskset, get_policy(taskset.scheduler), TraceWriter(stream)).run(horizon)
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    return [r for r in records if r['type'] not in ('header', 'task')]


def intervals(events, task_id):
    """(job, core, start, stop) execution intervals of one task."""
    open_, result = {}, []
    for e in events:
        if e.get('task') != task_id:
            continue
        if e['type'] == 'start':
            open_[e['job']] = (e['core'], e['t'])
        elif e['type'] in ('preempt', 'complete'):
            core, start = open_.pop(e['job'])
            result.append((e['job'], core, start, e['t']))
    return result


def completions(events, task_id):
    return {e['job']: e['t'] for e in events if e['type'] == 'complete' and e['task'] == task_id}


def task(name, period, wcet, priority, **extra):
    return dict(name=name, period=period, wcet=wcet, priority=priority, **extra)


class FixedPrioritySingleCore(unittest.TestCase):
    def test_preemption_by_higher_priority(self):
        events = run({'cores': 1, 'tasks': [task('High', 4, 1, 2), task('Low', 6, 3, 1)]}, 12)
        self.assertEqual(intervals(events, 1), [(0, 0, 0, 1), (1, 0, 4, 5), (2, 0, 8, 9)])
        # Low job 0 completes at 4, just as High is released; job 1 runs 6..8, is preempted, and finishes 9..10.
        self.assertEqual(intervals(events, 2), [(0, 0, 1, 4), (1, 0, 6, 8), (1, 0, 9, 10)])

    def test_deadline_miss_keeps_running_and_next_job_queues(self):
        events = run({'cores': 1, 'tasks': [task('Overrun', 10, 15, 1)]}, 40)
        self.assertEqual(completions(events, 1), {0: 15, 1: 30})
        releases = [(e['t'], e['job'], e['deadline']) for e in events if e['type'] == 'release']
        self.assertEqual(releases, [(0, 0, 10), (10, 1, 20), (20, 2, 30), (30, 3, 40)])
        self.assertEqual(events[-1], {'t': 40, 'type': 'end'})

    def test_equal_priority_is_fifo_and_never_preempts(self):
        events = run({'cores': 1, 'tasks': [task('A', 100, 10, 1), task('B', 100, 10, 1, phase=5),
                                            task('C', 100, 5, 1, phase=3)]}, 50)
        self.assertEqual(intervals(events, 1), [(0, 0, 0, 10)])
        self.assertEqual(intervals(events, 3), [(0, 0, 10, 15)])     # C was released before B
        self.assertEqual(intervals(events, 2), [(0, 0, 15, 25)])

    def test_unfinished_jobs_stay_open_at_the_horizon(self):
        events = run({'cores': 1, 'tasks': [task('Long', 100, 30, 1)]}, 20)
        self.assertEqual([e['type'] for e in events], ['release', 'start', 'end'])


class FixedPriorityMultiCore(unittest.TestCase):
    def test_global_uses_all_cores(self):
        events = run({'cores': 2, 'tasks': [task('A', 10, 4, 3), task('B', 10, 4, 2), task('C', 10, 4, 1)]}, 10)
        self.assertEqual(intervals(events, 1), [(0, 0, 0, 4)])
        self.assertEqual(intervals(events, 2), [(0, 1, 0, 4)])
        self.assertEqual(intervals(events, 3), [(0, 0, 4, 8)])

    def test_affinity_restricts_cores(self):
        events = run({'cores': 2, 'tasks': [task('Pinned', 10, 4, 1, affinity=[1]), task('Free', 10, 4, 2)]}, 10)
        self.assertEqual(intervals(events, 1), [(0, 1, 0, 4)])
        self.assertEqual(intervals(events, 2), [(0, 0, 0, 4)])

    def test_partitioned_job_waits_although_another_core_idles(self):
        events = run({'cores': 2, 'tasks': [task('A', 20, 5, 2, affinity=[0]), task('B', 20, 5, 1, affinity=[0])]}, 20)
        self.assertEqual(intervals(events, 2), [(0, 0, 5, 10)])

    def test_pinned_job_pushes_running_job_to_other_core(self):
        events = run({'cores': 2, 'tasks': [task('Pinned', 10, 2, 3, affinity=[0], phase=1),
                                            task('Mid', 20, 4, 2), task('Low', 20, 6, 1)]}, 20)
        # Mid starts on core 0; at 1 Pinned takes core 0, Mid migrates to core 1 and preempts Low.
        self.assertEqual(intervals(events, 2), [(0, 0, 0, 1), (0, 1, 1, 4)])
        at_one = [(e['type'], e['task']) for e in events if e['t'] == 1]
        self.assertEqual(at_one, [('release', 1), ('preempt', 2), ('preempt', 3), ('start', 1), ('start', 2)])

    def test_example_task_set_runs(self):
        taskset = load_taskset(ROOT / 'examples/simulator/fp_two_cores/taskset.json')
        events = run(json.loads((ROOT / 'examples/simulator/fp_two_cores/taskset.json').read_text()), 40000)
        self.assertEqual(taskset.cores, 2)
        self.assertTrue(all(e['core'] == 0 for e in events if e.get('task') == 1 and 'core' in e))
        logger = completions(events, 4)
        self.assertGreater(logger[1], 35000)        # The example contains one deadline miss


class TaskSetValidation(unittest.TestCase):
    def check(self, data, message):
        with self.assertRaisesRegex(TaskSetError, message):
            taskset = parse_taskset(data)
            get_policy(taskset.scheduler).validate(taskset)

    def test_defaults(self):
        taskset = parse_taskset({'cores': 1, 'tasks': [task('A', 10, 2, 1)]})
        self.assertEqual((taskset.scheduler, taskset.tasks[0].deadline, taskset.tasks[0].phase), ('fp', 10, 0))
        self.assertIsNone(taskset.tasks[0].affinity)

    def test_errors(self):
        self.check({'cores': 1, 'tasks': [dict(name='A', period=10, wcet=2)]}, "needs a 'priority'")
        self.check({'cores': 2, 'tasks': [task('A', 10, 2, 1, affinity=[2])]}, 'core 2 does not exist')
        self.check({'cores': 1, 'tasks': [task('A', 10, 2, 1, bcet=3)]}, "'bcet'")
        self.check({'cores': 1, 'scheduler': 'edf', 'tasks': [task('A', 10, 2, 1)]}, "unknown scheduler 'edf'")
        self.check({'cores': 1, 'tasks': [task('A', 10.5, 2, 1)]}, 'must be an integer')
        self.check({'cores': 1, 'tasks': [task('A', 10, 2, 1), task('A', 10, 2, 1)]}, 'duplicate name')
        self.check({'cores': 0, 'tasks': [task('A', 10, 2, 1)]}, "'cores' must be at least 1")
        self.check({'cores': 1, 'tasks': []}, "'tasks' must be a non-empty list")
        self.check({'cores': 1, 'tasks': [task('A', 10, 2, 1, phase=-1)]}, "'phase' must be at least 0")


if __name__ == '__main__':
    unittest.main()
