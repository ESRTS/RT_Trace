"""Exercise real plotting methods headlessly; capture canvas rectangle coordinates."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from TraceEventsFreeRTOS import read_recording
from TraceParserFreeRTOS import events_to_tasks
from TraceTask import TraceTask, ExecutionType


def load_view():
    # Only GUI/PDF imports are stubbed. Drawing, clipping, zoom and task selection
    # execute the production TraceView code without opening a desktop window.
    ctk = types.ModuleType('customtkinter')
    ctk.CTkCanvas = object
    for name in ('N', 'NW', 'E', 'W', 'CENTER', 'LAST'):
        setattr(ctk, name, name)
    pdfgen = types.ModuleType('reportlab.pdfgen')
    pdfgen.canvas = object()
    pagesizes = types.ModuleType('reportlab.lib.pagesizes')
    pagesizes.A4 = (595, 842)
    units = types.ModuleType('reportlab.lib.units')
    units.mm = 72 / 25.4
    colors = types.ModuleType('reportlab.lib.colors')
    colors.HexColor = lambda color: color
    modules = {'customtkinter': ctk, 'reportlab': types.ModuleType('reportlab'),
               'reportlab.pdfgen': pdfgen, 'reportlab.lib': types.ModuleType('reportlab.lib'),
               'reportlab.lib.pagesizes': pagesizes, 'reportlab.lib.units': units,
               'reportlab.lib.colors': colors}
    spec = importlib.util.spec_from_file_location('trace_view_under_test', ROOT / 'src/TraceView.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module.TraceView


View = load_view()


def make_view(left=0, right=1000):
    view = View.__new__(View)
    view.sizeX_px, view.borderX_px, view.legend_px = 1000, 40, 90
    view.leftBound_tks, view.rightBound_tks = left, right
    view.oldLeftBound_tks, view.oldRightBound_tks = 0, 0
    view.zoomFactor, view.zoomMax, view.zoomMin = 1.2, 50, right
    view.taskHeight_px, view.cores = 25, 1
    view.releaseArrowLength_px = 10
    view.deadlineMissColor = '#f5697c'
    view.rectangles = []
    view.draw_rectangle = lambda x1, y1, x2, y2, **kwargs: view.rectangles.append((x1, x2, kwargs.get('fill')))
    view.draw_arrow = lambda *args: None
    view.drawMutex = lambda *args: None
    return view


def make_task(start, stop, intervals=None):
    task = TraceTask(500, 'Task', 1, '#112233')
    task.newJob(start, None)
    job = task.currentJob
    for a, b in intervals if intervals is not None else [(start, stop)]:
        job.startExec(a, 0, ExecutionType.EXECUTE)
        job.stopExec(b)
    job.completionTime = stop
    task.finishJob()
    return task


class ViewClippingTests(unittest.TestCase):
    def assert_inside_frame(self, view):
        for left, right, _ in view.rectangles:
            self.assertGreaterEqual(left, view.plotXOffset())
            self.assertLessEqual(right, view.sizeX_px - view.borderX_px)
            self.assertLessEqual(left, right)

    def test_job_spanning_both_boundaries(self):
        view = make_view(50, 100)
        task = make_task(-100, 200)
        view.paintJob(task, task.jobs[0], 0, None)
        self.assertTrue(view.rectangles)
        self.assert_inside_frame(view)
        self.assertEqual(view.rectangles[0][:2], (130, 960))

    def test_subpixel_interval_at_right_edge(self):
        view = make_view()
        task = make_task(999.99, 1000)
        view.paintJob(task, task.jobs[0], 0, None)
        self.assert_inside_frame(view)
        colored = [r for r in view.rectangles if r[2] == task.taskColor]
        self.assertTrue(colored)
        self.assertGreaterEqual(colored[-1][1] - colored[-1][0], 1)

    def test_offscreen_intervals_do_not_leave_one_pixel_artifacts(self):
        view = make_view(0, 100)
        task = make_task(-20, 120, [(-20, 0), (100, 120)])
        view.paintJob(task, task.jobs[0], 0, None)
        self.assertFalse([r for r in view.rectangles if r[2] == task.taskColor])
        self.assert_inside_frame(view)

    def test_deadline_coloring_is_clipped(self):
        for deadline in (25, 75, 150):
            with self.subTest(deadline=deadline):
                view = make_view(50, 100)
                task = make_task(-100, 200)
                view.paintJob(task, task.jobs[0], 0, deadline)
                self.assert_inside_frame(view)
                colored = [r for r in view.rectangles if r[2] in (task.taskColor, view.deadlineMissColor)]
                self.assertEqual(sum(right - left for left, right, _ in colored), 830)
                if deadline == 75:
                    self.assertEqual([color for _, _, color in colored], [task.taskColor, view.deadlineMissColor])

    def test_example_after_up_zoom_and_pan(self):
        view = make_view(0, 43000)
        tasks = events_to_tasks(read_recording(ROOT / 'examples/freertos_smp', 2, strict=True), strict=True)
        view.cores = 2
        view.coreColors = [(98, 152, 210), (51, 156, 156)]
        def draw():
            view.rectangles.clear()
            for task in tasks:
                view.paintTask(task, 0)
        view.draw = draw
        view.zoom(1)  # Same default-center zoom as the Up key.
        self.assertGreater(view.leftBound_tks, 0)
        self.assertLess(view.rightBound_tks, 42000)
        self.assert_inside_frame(view)
        for fraction in (0.15, -0.25):
            view.pan(fraction)
            self.assert_inside_frame(view)

    def test_mutex_markers_use_attempt_time_and_result(self):
        view = make_view(0, 100)
        task = make_task(0, 100)
        task.mutexAttemptsExplicit = True
        task.jobs[0].mutexTake(40, 123, 'A')
        task.jobs[0].mutexGive(80, 123)
        task.mutexAttempt(10, 123, 'A', True, acquiredAt=40)
        task.mutexAttempt(60, 123, 'A', False)
        markers = []
        view.drawMutex = lambda x, y, letter, take, success=True: markers.append((x, take, success))
        view.paintTask(task, 0)
        self.assertEqual(sorted(markers), [(view.tickToPixel(10), True, True),
                                           (view.tickToPixel(60), True, False),
                                           (view.tickToPixel(80), False, True)])
        markers.clear()
        view.leftBound_tks, view.rightBound_tks = 15, 75
        view.paintTask(task, 0)
        self.assertEqual(markers, [(view.tickToPixel(60), True, False)])

    def test_incomplete_ownership_does_not_draw_a_give(self):
        view = make_view(0, 100)
        task = make_task(0, 100)
        task.mutexAttemptsExplicit = True
        task.mutexAttempt(10, 123, 'A', True, acquiredAt=40)
        task.jobs[0].mutexTake(40, 123, 'A')
        task.jobs[0].mutexGive(100, 123)
        task.jobs[0].mutexAccess[0].released = False
        markers = []
        view.drawMutex = lambda x, y, letter, take, success=True: markers.append((x, take, success))
        view.paintTask(task, 0)
        self.assertEqual(markers, [(view.tickToPixel(10), True, True)])

    def test_failed_mutex_symbol_is_red_and_crossed(self):
        for success, color, line_count in ((True, '#000000', 1), (False, '#C94C4C', 3), (None, '#8A8A8A', 1)):
            with self.subTest(success=success):
                view = make_view()
                view.mutexAccessHeight, view.mutexAccessDiameter = 15, 10
                view.mutexFailureColor, view.mutexUnknownColor = '#C94C4C', '#8A8A8A'
                ovals, lines, labels = [], [], []
                view.draw_line = lambda *args, **kwargs: lines.append(kwargs)
                view.draw_oval = lambda *args, **kwargs: ovals.append(kwargs)
                view.draw_text = lambda x, y, text, **kwargs: labels.append(text)
                View.drawMutex(view, 200, 100, 'A', True, success)
                self.assertEqual(ovals[0]['fill'], color)
                self.assertEqual(len(lines), line_count)
                self.assertEqual(labels, ['A'])


class RowVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tasks = events_to_tasks(read_recording(ROOT / 'examples/freertos_smp', 2, strict=True), strict=True)
        self.items = {}
        self.next_id = 0
        view = View.__new__(View)
        self.view = view
        attributes = dict(
            sizeX_px=1000, borderX_px=40, borderY_px=30, legend_px=90,
            leftBound_tks=0, rightBound_tks=43000, oldLeftBound_tks=0,
            oldRightBound_tks=0, zoomFactor=1.2, zoomMax=50, zoomMin=43000,
            maxTimelineHeight_px=45, minTimelineHeight_px=30, maxTicks=20,
            releaseArrowLength_px=10, releaseArrowWidth_px=2,
            releaseArrowH_px=4, releaseArrowD_px=3, mutexAccessHeight=15,
            mutexAccessDiameter=10, mutexFailureColor='#C94C4C',
            mutexUnknownColor='#8A8A8A', deadlineMissColor='#f5697c',
            coreColors=[(98, 152, 210), (51, 156, 156)], canvasItems=[],
            gui=types.SimpleNamespace(uiScale=1), exportCanvas=None,
        )
        for name, value in attributes.items():
            setattr(view, name, value)
        view.winfo_width = lambda: 1000
        view.winfo_height = lambda: 400
        view.xScrollbar = Mock()
        view.ctk_textbox_scrollbar = Mock()
        view.ctk_textbox_scrollbar.winfo_ismapped.return_value = False
        view.configure = Mock()
        view.yview_moveto = Mock()
        for kind in ('text', 'rectangle', 'line', 'oval'):
            setattr(view, 'create_' + kind, lambda *args, kind=kind, **kwargs: self.create(kind, args, kwargs))
        view.delete = lambda ident: self.items.pop(ident, None)
        view.bbox = lambda ident: (0, 0, len(self.items[ident][2].get('text', '')) * 7, 15)
        view.setTasks(self.tasks)
        view.draw()

    def create(self, kind, args, kwargs):
        self.next_id += 1
        self.items[self.next_id] = kind, args, kwargs
        return self.next_id

    def labels(self):
        return {kwargs['text'] for kind, _, kwargs in self.items.values() if kind == 'text'}

    def test_hidden_task_irq_and_scheduler_rows_leave_no_labels(self):
        for kind in ('task', 'irq', 'scheduler'):
            with self.subTest(kind=kind):
                hidden = [task for task in self.tasks if task.kind == kind]
                self.assertTrue(hidden)
                visible = [task for task in self.tasks if task.kind != kind]
                self.view.setVisibleTasks(visible)
                self.assertTrue({task.name for task in visible} <= self.labels())
                self.assertFalse({task.name for task in hidden} & self.labels())

    def test_hide_all_then_restore_without_stale_canvas_items(self):
        for _ in range(3):
            self.view.setVisibleTasks([])
            self.assertEqual(len(self.items), 1)
            self.assertTrue(any('No rows selected' in label for label in self.labels()))
            self.assertEqual(set(self.view.canvasItems), set(self.items))
            self.view.setVisibleTasks(self.tasks)
            self.assertTrue({task.name for task in self.tasks} <= self.labels())
            self.assertEqual(set(self.view.canvasItems), set(self.items))

    def test_filter_preserves_zoom_full_duration_and_core_colors(self):
        self.view.zoom(1)
        before = self.view.leftBound_tks, self.view.rightBound_tks, self.view.zoomMin
        control = next(task for task in self.tasks if task.name == 'Control')
        self.view.setVisibleTasks([control])
        self.assertEqual(before, (self.view.leftBound_tks, self.view.rightBound_tks, self.view.zoomMin))
        self.assertEqual(self.view.cores, 2)
        fills = {kwargs.get('fill') for kind, _, kwargs in self.items.values() if kind == 'rectangle'}
        self.assertIn('#6298D2', fills)
        self.assertNotIn(control.taskColor, fills)


if __name__ == '__main__':
    unittest.main()
