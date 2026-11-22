"""Exercise the recorder's real control flow with a simulated target and clock."""
from contextlib import redirect_stdout
import configparser
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MAIN = 0x10001001  # Thumb symbol addresses retain bit zero.
INIT = 0x10002001


def load_recorder():
    helper = types.ModuleType('HelperFunctions')
    helper.printState = Mock()
    helper.NO_PROBE_HINT = 'No probe'
    spec = importlib.util.spec_from_file_location('recorder_under_test', ROOT / 'src/RttTraceRecorder.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'HelperFunctions': helper}):
        spec.loader.exec_module(module)
    return module


recorder = load_recorder()


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Core:
    def __init__(self, events, files):
        self.events, self.files = events, files
        self.breakpoints = set()
        self.state = recorder.Target.State.HALTED
        self.initialized = False
        self.reach_main = True
        self.fault = False
        self.breakpoint_ok = True
        self.resumes = 0
        self.pc = 0
        self.secondary = None
        self.reset_type = None

    def reset_and_halt(self, reset_type=None):
        self.events.append('reset')
        self.reset_type = reset_type
        self.initialized = False
        self.state = recorder.Target.State.HALTED

    def set_breakpoint(self, address):
        self.events.append(('breakpoint', address))
        if self.breakpoint_ok:
            self.breakpoints.add(address)
        return self.breakpoint_ok

    def remove_breakpoint(self, address):
        self.events.append(('remove', address))
        self.breakpoints.discard(address)

    def resume(self):
        self.resumes += 1
        self.events.append(('resume', self.resumes))
        self.state = recorder.Target.State.RUNNING
        # Model RP2350 timer-dependent startup: a debug-halted peer prevents
        # main from being reached, even though core 0 itself is running.
        if self.secondary.state == recorder.Target.State.HALTED:
            return
        if self.resumes == 1:
            if INIT in self.breakpoints:
                # The previous recorder stopped here before any RTT setup ran.
                self.pc, self.state = INIT & ~1, recorder.Target.State.HALTED
            elif self.fault:
                self.pc, self.state = 0x10000300, recorder.Target.State.HALTED
            elif self.reach_main and MAIN in self.breakpoints:
                self.initialized = True
                self.events.append('initialized')
                self.pc, self.state = MAIN & ~1, recorder.Target.State.HALTED
        else:
            assert not self.breakpoints, 'startup breakpoint still installed'
            assert all(path.exists() for path in self.files), 'output files not ready'

    def halt(self):
        self.events.append('halt0')
        self.state = recorder.Target.State.HALTED

    def get_state(self):
        return self.state

    def read_core_register(self, name):
        assert name == 'pc'
        return self.pc


class SecondaryCore:
    def __init__(self, events):
        self.events = events
        self.state = recorder.Target.State.HALTED
        self.resumes = 0
        self.fail_resume = None
        self.reset_type = None

    def reset_and_halt(self, reset_type=None):
        self.events.append('reset1')
        self.reset_type = reset_type
        self.state = recorder.Target.State.HALTED

    def resume(self):
        assert self.reset_type == recorder.Target.ResetType.SYSRESETREQ, \
            'secondary core resumed stale firmware without a warm reset'
        self.resumes += 1
        self.events.append(('resume1', self.resumes))
        if self.resumes == self.fail_resume:
            raise RuntimeError('secondary resume failed')
        self.state = recorder.Target.State.RUNNING

    def halt(self):
        self.events.append('halt1')
        self.state = recorder.Target.State.HALTED

    def get_state(self):
        return self.state


class RecorderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.files = [Path(temp.name) / f'raw_buffer{core}.txt' for core in (0, 1)]
        self.elf = Path(temp.name) / 'build' / 'Trace2Test.elf'
        self.elf.parent.mkdir()
        self.elf.write_bytes(b'firmware with caller symbols')
        self.events = []
        self.core = Core(self.events, self.files)
        self.core1 = SecondaryCore(self.events)
        self.core.secondary = self.core1
        self.target = types.SimpleNamespace(cores={0: self.core, 1: self.core1})
        self.session = Mock()
        self.session.board.target = self.target
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.channels = []
        for index in (0, 1):
            channel = Mock(size=1536)
            # A poll consumes the first chunk; a final post-halt read gets tail.
            chunks = iter([f'run{index}'.encode(), b'', f'tail{index}'.encode()])
            channel.read.side_effect = lambda chunks=chunks: self.read_channel(chunks)
            self.channels.append(channel)
        self.rtt = types.SimpleNamespace(up_channels=[Mock(), *self.channels], start=Mock())
        self.rtt.start.side_effect = lambda: self.events.append('rtt_start')
        symbols = {'main': MAIN, 'trace_init': INIT, '_SEGGER_RTT': 0x20000100}
        provider = Mock()
        provider.get_symbol_value.side_effect = symbols.get
        patches = [
            patch.object(recorder.ConnectHelper, 'session_with_chosen_probe', return_value=self.session),
            patch.object(recorder, 'FileProgrammer'),
            patch.object(recorder, 'ELFSymbolProvider', return_value=provider),
            patch.object(recorder.RTTControlBlock, 'from_target', side_effect=self.attach_rtt),
            patch.object(recorder.HelperFunctions, 'printState'),
            patch.object(recorder, 'time', Clock()),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def attach_rtt(self, target, address):
        self.assertIs(target, self.target)
        self.assertEqual(address, 0x20000100)
        self.assertTrue(self.core.initialized, 'RTT inspected before the initializer ran')
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.assertEqual(self.core1.state, recorder.Target.State.HALTED)
        self.assertNotIn(MAIN, self.core.breakpoints)
        self.events.append('rtt_attach')
        return self.rtt

    def read_channel(self, chunks):
        self.assertEqual(self.core.state, self.core1.state,
                         'RTT read while only one core is halted')
        return next(chunks)

    def record(self):
        with redirect_stdout(io.StringIO()):
            return recorder.record_data(str(self.elf), None, 'rp2350', 4000000, 0.002,
                                        *(str(path) for path in self.files))

    def assert_startup_failed(self):
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.assertEqual(self.core1.state, recorder.Target.State.HALTED)
        self.assertFalse(self.core.breakpoints)
        self.assertEqual(self.core.resumes, 1)
        self.assertFalse(any(path.exists() for path in self.files))

    def test_attach_after_initialization_and_prepare_files_before_resume(self):
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.core.resumes, 2)
        self.assertEqual(self.core1.resumes, 2)
        self.assertEqual(self.core.reset_type, recorder.Target.ResetType.SYSRESETREQ)
        self.assertEqual(self.core1.reset_type, recorder.Target.ResetType.SYSRESETREQ)
        self.assertLess(self.events.index('reset1'), self.events.index('reset'))
        for phase in (1, 2):
            self.assertLess(self.events.index(('resume1', phase)),
                            self.events.index(('resume', phase)))
        self.assertEqual([e for e in self.events if isinstance(e, tuple) and e[0] == 'breakpoint'],
                         [('breakpoint', MAIN)])
        self.assertLess(self.events.index('reset'), self.events.index(('breakpoint', MAIN)))
        self.assertLess(self.events.index('initialized'), self.events.index('rtt_attach'))
        self.assertLess(self.events.index('rtt_start'), self.events.index(('resume', 2)))
        self.assertEqual(self.files[0].read_bytes(), b'run0tail0')
        self.assertEqual(self.files[1].read_bytes(), b'run1tail1')
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.assertEqual(self.core1.state, recorder.Target.State.HALTED)
        self.assertEqual(self.events.count('halt1'), 3)

    def test_saved_elf_is_used_for_flashing_and_symbols_after_project_rebuild(self):
        expected = self.elf.read_bytes()
        saved = self.files[0].resolve().parent / 'firmware.elf'

        def program(filename):
            self.elf.write_bytes(b'rebuilt firmware with different addresses')
            self.assertEqual(Path(filename), saved)
            self.assertEqual(Path(filename).read_bytes(), expected)

        self.mocks[1].return_value.program.side_effect = program
        self.assertEqual(self.record(), 0)
        self.assertEqual(saved.read_bytes(), expected)
        self.assertEqual(self.target.elf, str(saved))
        self.mocks[2].assert_called_once_with(str(saved))
        self.assertNotEqual(self.elf.read_bytes(), expected)

    def test_input_already_in_recording_folder_is_preserved(self):
        saved = self.files[0].parent / 'firmware.elf'
        self.elf.rename(saved)
        self.elf = saved
        self.assertEqual(self.record(), 0)
        self.assertEqual(saved.read_bytes(), b'firmware with caller symbols')

    def test_copy_failure_does_not_connect_or_flash(self):
        with patch.object(recorder.shutil, 'copyfile', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.record()
        self.mocks[0].assert_not_called()
        self.mocks[1].assert_not_called()
        self.assertFalse(any(path.exists() for path in self.files))

    def test_missing_probe_is_reported_instead_of_waiting(self):
        self.mocks[0].return_value = None
        self.assertEqual(self.record(), 1)
        self.assertIs(self.mocks[0].call_args.kwargs['blocking'], False)
        self.mocks[1].assert_not_called()                   # Nothing flashed
        self.assertFalse(any(path.exists() for path in self.files))

    def test_missing_secondary_core_does_not_program(self):
        del self.target.cores[1]
        self.assertEqual(self.record(), 1)
        self.mocks[1].assert_not_called()

    def test_secondary_resume_failure_during_startup_stops_both_cores(self):
        self.core1.fail_resume = 1
        with self.assertRaisesRegex(RuntimeError, 'secondary resume failed'):
            self.record()
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.assertEqual(self.core1.state, recorder.Target.State.HALTED)
        self.assertFalse(self.core.breakpoints)
        self.mocks[3].assert_not_called()

    def test_secondary_resume_failure_during_recording_stops_both_cores(self):
        self.core1.fail_resume = 2
        with self.assertRaisesRegex(RuntimeError, 'secondary resume failed'):
            self.record()
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.assertEqual(self.core1.state, recorder.Target.State.HALTED)
        self.assertFalse(self.core.breakpoints)

    def test_timeout_halts_core_and_removes_breakpoint_without_reading_rtt(self):
        self.core.reach_main = False
        self.assertEqual(self.record(), 1)
        self.assert_startup_failed()
        self.mocks[3].assert_not_called()

    def test_fault_before_main_is_not_treated_as_success(self):
        self.core.fault = True
        self.assertEqual(self.record(), 1)
        self.assert_startup_failed()
        self.mocks[3].assert_not_called()

    def test_unavailable_breakpoint_does_not_resume(self):
        self.core.breakpoint_ok = False
        self.assertEqual(self.record(), 1)
        self.assertEqual(self.core.resumes, 0)
        self.mocks[3].assert_not_called()

    def test_unconfigured_channel_stops_at_main(self):
        self.channels[1].size = 0
        self.assertEqual(self.record(), 1)
        self.assert_startup_failed()

    def test_missing_channel_stops_at_main(self):
        self.rtt.up_channels.pop()
        self.assertEqual(self.record(), 1)
        self.assert_startup_failed()

    def test_rtt_error_leaves_main_stopped_and_breakpoint_removed(self):
        self.rtt.start.side_effect = RuntimeError('control block unavailable')
        with self.assertRaisesRegex(RuntimeError, 'control block unavailable'):
            self.record()
        self.assert_startup_failed()

    def test_worker_uses_duration_selected_for_recording(self):
        elf = self.files[0].parent / 'firmware.elf'
        elf.write_bytes(b'firmware')
        config = configparser.ConfigParser()
        config.read_dict({'general': {'measure_seconds': '99'}})
        gui = types.SimpleNamespace(recordingFolder=str(elf.parent), recordingSeconds=0.125)
        with patch.multiple(recorder.HelperFunctions, create=True,
                            getCwd=Mock(return_value=str(elf.parent)), makeFolder=Mock(),
                            readConfig=Mock(return_value=config),
                            getElfFilePath=Mock(return_value=str(elf))), \
                patch.object(recorder, 'record_data', return_value=0) as record:
            self.assertEqual(recorder.rtt_thread(gui), 0)
        self.assertEqual(record.call_args.args[4], 0.125)

    def test_failed_capture_with_saved_elf_does_not_auto_load(self):
        # Load the real callback without constructing any Tk widgets.
        spec = importlib.util.spec_from_file_location('gui_under_test', ROOT / 'src/RT-Trace.py')
        gui_module = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(ROOT / 'src'), *sys.path]):
            spec.loader.exec_module(gui_module)
        saved = self.files[0].parent / 'firmware.elf'
        saved.write_bytes(b'firmware')
        gui = types.SimpleNamespace(recordingFolder=str(saved.parent),
                                    btn_recordTrace=Mock(), updateTraceViewOptions=Mock(),
                                    selectTrace=[saved.parent.name], opt_selectTrace=Mock(),
                                    load_function=Mock())
        gui_module.TraceApp.recordingFinished(gui, 1)
        gui.btn_recordTrace.configure.assert_called_once_with(state='normal', text='Record Trace')
        gui.updateTraceViewOptions.assert_called_once()
        gui.load_function.assert_not_called()


class CommandLineTests(unittest.TestCase):
    """main(): program resolution, checks before flashing, folders and the steps after recording."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        sys.path.insert(0, str(ROOT / 'src'))
        self.addCleanup(sys.path.remove, str(ROOT / 'src'))
        import HelperFunctions
        self.helper = HelperFunctions
        self.project = self.temp / 'projects' / 'Demo'
        (self.project / 'build').mkdir(parents=True)
        self.elf = self.project / 'build' / 'Demo.elf'
        self.elf.write_bytes(b'elf')
        config = configparser.ConfigParser()
        config.read_dict({'general': {'measure_seconds': '1.5', 'frequency': '2000000', 'probe': 'ABC'}})
        patches = [patch.object(recorder, 'HelperFunctions', HelperFunctions),
                   patch.object(HelperFunctions, 'readConfig', return_value=config),
                   patch.object(HelperFunctions, 'getProjectPath', return_value=str(self.temp / 'projects')),
                   patch.object(HelperFunctions, 'getCwd', return_value=str(self.temp / 'src')),
                   patch.object(HelperFunctions, 'picoTraceProblem', return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def main(self, *argv, record=None):
        out = io.StringIO()
        with patch.object(recorder, 'record_data', side_effect=record or (lambda *a: 0)) as mock, redirect_stdout(out):
            try:
                code = recorder.main(list(argv))
            except SystemExit as exit:
                code = exit.code
        return code, mock, out.getvalue()

    def test_program_resolution(self):
        self.assertEqual(recorder.resolve_program(str(self.project)), (str(self.elf), 'Demo'))
        self.assertEqual(recorder.resolve_program('Demo'), (str(self.elf), 'Demo'))       # Name in project_path
        self.assertEqual(recorder.resolve_program(str(self.elf)), (str(self.elf), 'Demo'))
        with self.assertRaisesRegex(ValueError, 'not an ELF file'):
            recorder.resolve_program('Missing')

    def test_default_folder_duration_and_settings_from_config(self):
        code, record, out = self.main('Demo')
        self.assertEqual(code, 0)
        elf, probe, target, frequency, seconds, *files = record.call_args.args
        self.assertEqual((elf, probe, target, frequency, seconds), (str(self.elf), 'ABC', 'rp2350', 2000000, 1.5))
        folder = Path(files[0]).parent
        self.assertEqual(folder.parent, self.temp / 'data' / 'Pico2_FreeRTOS')
        self.assertTrue(folder.name.startswith('Demo_'))
        self.assertEqual([Path(f).name for f in files], ['raw_buffer0.txt', 'raw_buffer1.txt'])
        self.assertIn('=> RECORDED', out)

    def test_options_override_the_config(self):
        code, record, _ = self.main(str(self.elf), '0.25', '-o', str(self.temp / 'out'), '--probe', 'XYZ',
                                    '--frequency', '0x100000', '--target', 'rp2040')
        self.assertEqual(code, 0)
        self.assertEqual(record.call_args.args[1:5], ('XYZ', 'rp2040', 0x100000, 0.25))
        self.assertEqual(record.call_args.args[5], str(self.temp / 'out' / 'raw_buffer0.txt'))
        _, record, _ = self.main('Demo', '-n', 'Run')
        self.assertTrue(Path(record.call_args.args[5]).parent.name.startswith('Run_'))

    def test_checks_before_flashing(self):
        with patch.object(self.helper, 'picoTraceProblem', return_value=('ERROR no tracing', 'hint')):
            code, record, out = self.main('Demo', '1', '-o', str(self.temp / 'out'))
        self.assertEqual(code, 1)
        record.assert_not_called()
        self.assertFalse((self.temp / 'out').exists())
        self.assertIn('=> ERROR NO TRACING hint', out)

        existing = self.temp / 'existing'
        existing.mkdir()
        (existing / 'raw_buffer0.txt').write_bytes(b'old')
        code, record, out = self.main('Demo', '1', '-o', str(existing))
        self.assertEqual(code, 1)
        record.assert_not_called()
        self.assertEqual((existing / 'raw_buffer0.txt').read_bytes(), b'old')

        for argv in (('Missing', '1'), ('Demo', '0'), ('Demo', '1', '--analyze', 'nothing')):
            with patch('sys.stderr', io.StringIO()):
                code, record, _ = self.main(*argv)
            self.assertEqual(code, 2, argv)
            record.assert_not_called()

    def test_recording_errors(self):
        def fail(*args):
            raise RuntimeError('probe lost')
        code, _, out = self.main('Demo', '1', '-o', str(self.temp / 'out'), record=fail)
        self.assertEqual(code, 1)
        self.assertIn('=> ERROR RECORDING FAILED RuntimeError: probe lost', out)
        self.assertTrue((self.temp / 'out' / 'record_error.log').is_file())

        def interrupt(*args):
            raise KeyboardInterrupt
        self.assertEqual(self.main('Demo', '1', '-o', str(self.temp / 'out2'), record=interrupt)[0], 130)
        code, _, out = self.main('Demo', '1', '--csv', record=lambda *a: 1)
        self.assertEqual(code, 1)
        self.assertNotIn('CSV', out)

    def test_csv_and_analyses_after_recording(self):
        def capture(elf, probe, target, frequency, seconds, *files):      # The example trace instead of a target
            for core, path in enumerate(files):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes((ROOT / 'examples/freertos_smp' / f'raw_buffer{core}.txt').read_bytes())
            return 0
        folder = self.temp / 'rec'
        code, _, out = self.main('Demo', '1', '-o', str(folder), '--csv', '--analyze', 'deadline_misses',
                                 record=capture)
        self.assertEqual(code, 0)
        self.assertTrue((folder / 'events.txt').is_file())
        self.assertTrue((folder / 'Control.csv').is_file())
        self.assertIn('=> DEADLINE MISSES 1 late job(s)', out)
        self.assertTrue((folder / 'analysis' / 'deadline_misses.csv').is_file())

        folder = self.temp / 'rec2'
        code, _, out = self.main('Demo', '1', '-o', str(folder), '--analyze', record=capture)
        self.assertEqual(code, 0)
        self.assertIn('=> TASK STATISTICS', out)
        self.assertFalse((folder / 'Control.csv').exists())


if __name__ == '__main__':
    unittest.main()
