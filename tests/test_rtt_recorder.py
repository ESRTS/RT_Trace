"""Exercise the recorder's real control flow with a simulated target and clock."""
from contextlib import redirect_stdout
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

    def reset_and_halt(self):
        self.events.append('reset')
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


class RecorderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.files = [Path(temp.name) / f'raw_buffer{core}.txt' for core in (0, 1)]
        self.events = []
        self.core = Core(self.events, self.files)
        self.core1 = Mock()
        self.core1.halt.side_effect = lambda: self.events.append('halt1')
        self.target = types.SimpleNamespace(cores={0: self.core, 1: self.core1})
        self.session = Mock()
        self.session.board.target = self.target
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.channels = []
        for index in (0, 1):
            channel = Mock(size=1536)
            # A poll consumes the first chunk; a final post-halt read gets tail.
            channel.read.side_effect = [f'run{index}'.encode(), b'', f'tail{index}'.encode()]
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
        self.assertNotIn(MAIN, self.core.breakpoints)
        self.events.append('rtt_attach')
        return self.rtt

    def record(self):
        with redirect_stdout(io.StringIO()):
            return recorder.record_data('firmware.elf', None, 'rp2350', 4000000, 0.002,
                                        *(str(path) for path in self.files))

    def assert_startup_failed(self):
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.assertFalse(self.core.breakpoints)
        self.assertEqual(self.core.resumes, 1)
        self.assertFalse(any(path.exists() for path in self.files))

    def test_attach_after_initialization_and_prepare_files_before_resume(self):
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.core.resumes, 2)
        self.assertEqual([e for e in self.events if isinstance(e, tuple) and e[0] == 'breakpoint'],
                         [('breakpoint', MAIN)])
        self.assertLess(self.events.index('reset'), self.events.index(('breakpoint', MAIN)))
        self.assertLess(self.events.index('initialized'), self.events.index('rtt_attach'))
        self.assertLess(self.events.index('rtt_start'), self.events.index(('resume', 2)))
        self.assertEqual(self.files[0].read_bytes(), b'run0tail0')
        self.assertEqual(self.files[1].read_bytes(), b'run1tail1')
        self.assertEqual(self.core.state, recorder.Target.State.HALTED)
        self.core1.halt.assert_called_once()

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


if __name__ == '__main__':
    unittest.main()
