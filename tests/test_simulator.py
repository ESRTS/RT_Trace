"""Simulator engine and scheduling policies against hand-computed schedules."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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


def edf(name, period, wcet, **extra):
    return dict(name=name, period=period, wcet=wcet, **extra)


class EarliestDeadlineFirst(unittest.TestCase):
    def test_schedules_set_that_rate_monotonic_misses(self):
        # U = 2/5 + 4/7 < 1. Under RM, B job 0 would finish at 8 > 7; EDF keeps all deadlines.
        tasks = [edf('A', 5, 2), edf('B', 7, 4)]
        events = run({'cores': 1, 'scheduler': 'edf', 'tasks': tasks}, 17)
        # At 5, A job 1 (d=10) does not preempt B job 0 (d=7); at 15, A job 3 (d=20) preempts B job 2 (d=21).
        self.assertEqual(intervals(events, 1), [(0, 0, 0, 2), (1, 0, 6, 8), (2, 0, 12, 14), (3, 0, 15, 17)])
        self.assertEqual(intervals(events, 2), [(0, 0, 2, 6), (1, 0, 8, 12), (2, 0, 14, 15)])
        fp = run({'cores': 1, 'tasks': [task('A', 5, 2, 2), task('B', 7, 4, 1)]}, 17)
        self.assertEqual(completions(fp, 2)[0], 8)

    def test_equal_deadlines_are_fifo_and_never_preempt(self):
        # All three jobs have absolute deadline 10.
        tasks = [edf('A', 10, 4), edf('B', 8, 3, phase=2), edf('C', 9, 1, phase=1)]
        events = run({'cores': 1, 'scheduler': 'edf', 'tasks': tasks}, 10)
        self.assertEqual([intervals(events, i) for i in (1, 3, 2)],
                         [[(0, 0, 0, 4)], [(0, 0, 4, 5)], [(0, 0, 5, 8)]])

    def test_late_job_keeps_running(self):
        events = run({'cores': 1, 'scheduler': 'edf', 'tasks': [edf('Overrun', 10, 15)]}, 40)
        self.assertEqual(completions(events, 1), {0: 15, 1: 30})

    def test_global_with_affinity(self):
        tasks = [edf('A', 10, 5, affinity=[0]), edf('B', 20, 10), edf('C', 12, 6)]
        events = run({'cores': 2, 'scheduler': 'edf', 'tasks': tasks}, 20)
        # 0: A (d10) on core 0, C (d12) on core 1, B (d20) waits. 5: B takes the idle core 0.
        # 10: A job 1 (d20) ties with running B (d20), so it waits for core 0 although core 1 idles.
        self.assertEqual(intervals(events, 1), [(0, 0, 0, 5), (1, 0, 15, 20)])
        self.assertEqual(intervals(events, 2), [(0, 0, 5, 15)])
        self.assertEqual(intervals(events, 3), [(0, 1, 0, 6), (1, 1, 12, 18)])

    def test_priority_is_not_required(self):
        taskset = parse_taskset({'cores': 1, 'scheduler': 'edf', 'tasks': [edf('A', 10, 2)]})
        get_policy('edf').validate(taskset)


def prec(pred, succ, pred_first=0, pred_every=1, succ_first=0, succ_every=1):
    return {'predecessor': {'task': pred, 'first_job': pred_first, 'every': pred_every},
            'successor': {'task': succ, 'first_job': succ_first, 'every': succ_every}}


class Precedence(unittest.TestCase):
    def test_successor_waits_although_it_has_higher_priority(self):
        tasks = [task('P', 10, 3, 1), task('S', 10, 2, 2)]
        events = run({'cores': 1, 'tasks': tasks, 'precedence': [prec('P', 'S')]}, 10)
        self.assertEqual(intervals(events, 1), [(0, 0, 0, 3)])
        self.assertEqual(intervals(events, 2), [(0, 0, 3, 5)])
        # The release and the deadline of the successor stay nominal.
        self.assertIn({'t': 0, 'type': 'release', 'task': 2, 'job': 0, 'deadline': 10}, events)

    def test_first_job_and_every_select_the_pairs(self):
        # Sensor job k must complete before Control job 1 + 2k; Control jobs 0, 2, 4 are free.
        tasks = [task('Sensor', 10, 6, 1), task('Control', 5, 1, 2)]
        constraint = prec('Sensor', 'Control', succ_first=1, succ_every=2)
        events = run({'cores': 1, 'tasks': tasks, 'precedence': [constraint]}, 20)
        # 5: Control 1 waits for Sensor 0 (done at 7) and does not preempt it; same at 15 for Sensor 1.
        self.assertEqual(intervals(events, 2), [(0, 0, 0, 1), (1, 0, 7, 8), (2, 0, 10, 11), (3, 0, 17, 18)])
        self.assertEqual(intervals(events, 1), [(0, 0, 1, 7), (1, 0, 11, 17)])

    def test_predecessor_first_job_offset(self):
        # S job k waits for P job 1 + k, which is released one P period later.
        tasks = [task('P', 10, 1, 1), task('S', 20, 1, 2)]
        events = run({'cores': 1, 'tasks': tasks, 'precedence': [prec('P', 'S', pred_first=1)]}, 30)
        self.assertEqual(intervals(events, 2), [(0, 0, 11, 12), (1, 0, 21, 22)])

    def test_all_predecessors_must_complete(self):
        tasks = [task('A', 10, 2, 1), task('B', 10, 4, 1), task('C', 10, 1, 3)]
        events = run({'cores': 2, 'tasks': tasks, 'precedence': [prec('A', 'C'), prec('B', 'C')]}, 10)
        # A completes at 2, but C also waits for B (4), although core 0 idles from 2.
        self.assertEqual([i[2:] for i in intervals(events, 3)], [(4, 5)])

    def test_works_with_edf(self):
        tasks = [edf('P', 10, 3, deadline=10), edf('S', 10, 2, deadline=5)]
        events = run({'cores': 1, 'scheduler': 'edf', 'tasks': tasks, 'precedence': [prec('P', 'S')]}, 10)
        self.assertEqual(intervals(events, 2), [(0, 0, 3, 5)])

    def test_validation(self):
        tasks = [task('A', 10, 1, 1), task('B', 10, 1, 1), task('C', 10, 1, 1)]
        def check(constraints, message):
            with self.assertRaisesRegex(TaskSetError, message):
                parse_taskset({'cores': 1, 'tasks': tasks, 'precedence': constraints})
        check([prec('A', 'A')], "same task 'A'")
        check([prec('A', 'X')], "successor task 'X' is not defined")
        check([prec('A', 'B'), prec('B', 'C'), prec('C', 'A')], 'cycle: A -> B -> C -> A')
        check([prec('A', 'B', pred_every=0)], "'every' must be at least 1")
        check([prec('A', 'B', succ_first=-1)], "'first_job' must be at least 0")
        check([{'predecessor': {'task': 'A'}}], "'successor' must be an object")
        check({'A': 'B'}, "'precedence' must be a list")

    def test_defaults(self):
        taskset = parse_taskset({'cores': 1, 'tasks': [task('A', 10, 1, 1), task('B', 10, 1, 1)],
                                 'precedence': [{'predecessor': {'task': 'A'}, 'successor': {'task': 'B'}}]})
        (constraint,) = taskset.precedence
        self.assertEqual([constraint.predecessor_of(j) for j in range(3)], [0, 1, 2])
        # A diamond is not a cycle.
        parse_taskset({'cores': 1, 'tasks': [task(n, 10, 1, 1) for n in 'ABCD'],
                       'precedence': [prec('A', 'B'), prec('A', 'C'), prec('B', 'D'), prec('C', 'D')]})


class CommandLine(unittest.TestCase):
    EXAMPLE = ROOT / 'examples/simulator/fp_two_cores/taskset.json'

    def simulate(self, *args, cwd=ROOT):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'src/simulator'), *map(str, args)],
                              cwd=cwd, capture_output=True, text=True)

    def test_writes_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'sub' / 'trace.jsonl'
            result = self.simulate(self.EXAMPLE, 0.02, '-o', out)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual((records[0]['horizon'], records[-1]), (20000, {'t': 20000, 'type': 'end'}))

    def test_default_output_in_current_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.simulate(self.EXAMPLE, 0.001, cwd=folder).returncode, 0)
            self.assertTrue((Path(folder) / 'sim_trace.jsonl').is_file())

    def test_invalid_task_set_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / 'taskset.json'
            bad.write_text(json.dumps({'cores': 1, 'tasks': [{'name': 'A', 'period': 10, 'wcet': 2}]}))
            out = Path(folder) / 'out' / 'trace.jsonl'
            result = self.simulate(bad, 1, '-o', out)
            self.assertEqual(result.returncode, 1)
            self.assertIn("needs a 'priority'", result.stderr)
            self.assertFalse(out.parent.exists())

    def test_invalid_duration(self):
        result = self.simulate(self.EXAMPLE, 0)
        self.assertEqual(result.returncode, 2)
        self.assertIn('positive number of seconds', result.stderr)


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
        self.check({'cores': 1, 'scheduler': 'llf', 'tasks': [task('A', 10, 2, 1)]}, "unknown scheduler 'llf'")
        self.check({'cores': 1, 'tasks': [task('A', 10.5, 2, 1)]}, 'must be an integer')
        self.check({'cores': 1, 'tasks': [task('A', 10, 2, 1), task('A', 10, 2, 1)]}, 'duplicate name')
        self.check({'cores': 0, 'tasks': [task('A', 10, 2, 1)]}, "'cores' must be at least 1")
        self.check({'cores': 1, 'tasks': []}, "'tasks' must be a non-empty list")
        self.check({'cores': 1, 'tasks': [task('A', 10, 2, 1, phase=-1)]}, "'phase' must be at least 0")


if __name__ == '__main__':
    unittest.main()
