"""Row orders of the trace view (TraceRowOrder) and the sort/move controls of the row selector."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import TraceRowOrder
from TraceTask import TraceTask, TraceJob


def row(name, kind=None, priority=None):
    task = TraceTask(name, name, priority, '#000000')
    if kind is not None:
        task.kind = kind
    return task


def gpu(name, *launchers):
    stream = row(name, 'gpu')
    for index, launcher in enumerate(launchers):
        job = TraceJob(stream, index, index, None)
        job.launchedBy = launcher
        stream.jobs.append(job)
    return stream


def names(rows):
    return [task.name for task in rows]


class Orders(unittest.TestCase):
    def setUp(self):
        # Jetson-like recorded order: tasks by priority, GPU streams, threads.
        self.a = row('GpuA', 'task', 9)
        self.b = row('cpuB', 'task', 5)
        self.c = row('GpuB', 'task', 7)
        self.sa = gpu('GPU0 Stream 13', self.a, self.a)
        self.sb = gpu('GPU0 Stream 14', self.c, self.a, self.c)     # Mostly GpuB
        self.default = gpu('GPU0 Stream 7')                         # No launcher known
        self.thread = row('main (100)', 'thread')
        self.rows = [self.a, self.c, self.b, self.sa, self.sb, self.default, self.thread]

    def test_gpu_streams_below_their_task(self):
        self.assertEqual(names(TraceRowOrder.tasks_with_gpu_streams(self.rows)),
                         ['GpuA', 'GPU0 Stream 13', 'GpuB', 'GPU0 Stream 14', 'cpuB', 'GPU0 Stream 7', 'main (100)'])

    def test_gpu_owner_ties_go_to_the_earlier_row(self):
        stream = gpu('GPU0 Stream 15', self.c, self.a)
        self.assertIs(TraceRowOrder.gpu_owner(stream, self.rows), self.a)

    def test_gpu_owner_must_be_in_the_list(self):
        # A launcher that is not among the rows leaves the stream where it was.
        self.assertEqual(names(TraceRowOrder.tasks_with_gpu_streams([self.b, self.sa])), ['cpuB', 'GPU0 Stream 13'])

    def test_priority_highest_first_rows_without_priority_after(self):
        self.assertEqual(names(TraceRowOrder.by_priority(self.rows)),
                         ['GpuA', 'GpuB', 'cpuB', 'GPU0 Stream 13', 'GPU0 Stream 14', 'GPU0 Stream 7', 'main (100)'])

    def test_name_ignores_case(self):
        self.assertEqual(names(TraceRowOrder.by_name([self.c, self.b, self.a])), ['cpuB', 'GpuA', 'GpuB'])

    def test_kind_keeps_recorded_order_within_a_kind(self):
        irq, scheduler, plain = row('IRQ_15_C0', 'irq'), row('Scheduler Core 0', 'scheduler'), row('Task_3')
        rows = [scheduler, irq, self.thread, self.sa, plain, self.a]
        self.assertEqual(names(TraceRowOrder.by_kind(rows)),
                         ['Task_3', 'GpuA', 'GPU0 Stream 13', 'main (100)', 'IRQ_15_C0', 'Scheduler Core 0'])

    def test_orders_keep_all_rows(self):
        for label, order in TraceRowOrder.ORDERS.items():
            with self.subTest(label):
                self.assertCountEqual(map(id, order(self.rows)), map(id, self.rows))
                self.assertIsNot(order(self.rows), self.rows)

    def test_gpu_grouping_only_offered_with_gpu_rows(self):
        self.assertIn('Tasks with GPU streams', TraceRowOrder.available_orders(self.rows))
        self.assertNotIn('Tasks with GPU streams', TraceRowOrder.available_orders([self.a, self.thread]))
        self.assertEqual(TraceRowOrder.available_orders([self.a])[0], 'As recorded')

    def test_jetson_example(self):
        import TraceParserJetson
        tasks, _, _ = TraceParserJetson.build_rows(
            TraceParserJetson.read_export(ROOT / 'examples/jetsonThor/thor_test.sqlite'))
        ordered = TraceRowOrder.tasks_with_gpu_streams(tasks)
        owners = {id(task): TraceRowOrder.gpu_owner(task, tasks) for task in tasks if TraceRowOrder.kind(task) == 'gpu'}
        self.assertTrue(any(owners.values()))
        for index, task in enumerate(ordered):
            owner = owners.get(id(task))
            if owner is not None:       # Directly below its task, or below another stream of that task
                above = ordered[index - 1]
                self.assertTrue(above is owner or owners.get(id(above)) is owner, task.name)


class Selector(unittest.TestCase):
    """The real widget; skipped where Tk cannot open a display."""

    @classmethod
    def setUpClass(cls):
        try:
            import customtkinter
            cls.root = customtkinter.CTk()
        except Exception as error:      # No display (CI)
            raise unittest.SkipTest(f'no Tk display: {error}')
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from TraceRowSelector import TraceRowSelector, CUSTOM_ORDER
        self.custom = CUSTOM_ORDER
        self.shown = []
        self.selector = TraceRowSelector(self.root, command=self.shown.append)
        self.a, self.b = row('A', 'task', 1), row('B', 'task', 2)
        self.stream = gpu('GPU0 Stream 1', self.a)
        self.selector.setTasks([self.a, self.b, self.stream])

    def tearDown(self):
        self.selector.destroy()

    def test_move_and_sort_keep_hidden_rows_in_place(self):
        rows = self.selector.rows
        rows[1].variable.set(False)                         # Hide B
        self.selector.selectionChanged()
        self.assertEqual(names(self.shown[-1]), ['A', 'GPU0 Stream 1'])
        self.selector.move(rows[2], -1)                     # Stream above B
        self.assertEqual(names(self.selector.order()), ['A', 'GPU0 Stream 1', 'B'])
        self.assertEqual(self.selector.orderMenu.get(), self.custom)
        self.selector.move(self.selector.rows[0], -1)       # First row stays first
        self.assertEqual(names(self.selector.order()), ['A', 'GPU0 Stream 1', 'B'])
        self.selector.sortRows('By priority')
        self.assertEqual(names(self.selector.order()), ['B', 'A', 'GPU0 Stream 1'])
        self.assertEqual(names(self.shown[-1]), ['A', 'GPU0 Stream 1'])     # B is still hidden
        self.assertEqual(str(self.selector.rows[0].up.cget('state')), 'disabled')
        self.assertEqual(str(self.selector.rows[-1].down.cget('state')), 'disabled')

    def test_rows_hidden_by_default_start_unchecked(self):
        helper = row('[NSys] (12)', 'thread')
        helper.hiddenByDefault = True
        self.selector.setTasks([self.a, helper, self.b])
        self.assertEqual(names(self.selector.visibleTasks()), ['A', 'B'])
        self.selector.rows[1].variable.set(True)
        self.selector.selectionChanged()
        self.assertEqual(names(self.shown[-1]), ['A', '[NSys] (12)', 'B'])

    def test_new_trace_resets_order(self):
        self.selector.sortRows('By name')
        self.selector.setTasks([self.b, self.a])
        self.assertEqual(names(self.selector.order()), ['B', 'A'])
        self.assertEqual(self.selector.orderMenu.get(), 'As recorded')
        self.assertNotIn('Tasks with GPU streams', self.selector.orderMenu.cget('values'))
