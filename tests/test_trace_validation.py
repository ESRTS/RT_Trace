"""Validate real ELF symbol tables without opening a GUI or using a toolchain."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('trace_init', '__pre_init_trace_init',
            'trace_state_core0', 'trace_state_core1', '_SEGGER_RTT')


def load_helpers():
    # Font imports are unrelated to project validation; ELF parsing is real.
    tk = types.ModuleType('tkinter')
    tk.font = types.ModuleType('tkinter.font')
    spec = importlib.util.spec_from_file_location('helpers_under_test', ROOT / 'src/HelperFunctions.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'tkinter': tk, 'tkinter.font': tk.font}):
        spec.loader.exec_module(module)
    return module


helper = load_helpers()


def write_elf(path, defined=(), undefined=(), stripped=False):
    """Minimal ELF32/Arm executable with explicit defined/undefined symbols."""
    data = bytearray(52)  # ELF header is filled after the section layout.

    def append(payload):
        data.extend(b'\0' * (-len(data) % 4))
        offset = len(data)
        data.extend(payload)
        return offset

    text = b'\x00\xbf\x70\x47' * max(1, len(defined))
    text_offset = append(text)
    strings, name_offsets = bytearray(b'\0'), {}
    for name in (*defined, *undefined):
        if name not in name_offsets:
            name_offsets[name] = len(strings)
            strings.extend(name.encode() + b'\0')
    symbols = bytearray(16)  # Mandatory null symbol.
    for index, name in enumerate(defined):
        symbols.extend(struct.pack('<IIIBBH', name_offsets[name], 0x10000000 + index * 4,
                                   4, 0x10, 0, 1))
    for name in undefined:
        symbols.extend(struct.pack('<IIIBBH', name_offsets[name], 0, 0, 0x10, 0, 0))
    symbol_offset = append(symbols)
    string_offset = append(strings)
    section_names = b'\0.text\0.symtab\0.strtab\0.shstrtab\0'
    names_offset = append(section_names)
    sections = [(0,) * 10,
                (section_names.index(b'.text'), 1, 6, 0x10000000,
                 text_offset, len(text), 0, 0, 4, 0)]
    if not stripped:
        sections.extend([
            (section_names.index(b'.symtab'), 2, 0, 0,
             symbol_offset, len(symbols), 3, 1, 4, 16),
            (section_names.index(b'.strtab'), 3, 0, 0,
             string_offset, len(strings), 0, 0, 1, 0),
        ])
    sections.append((section_names.index(b'.shstrtab'), 3, 0, 0,
                     names_offset, len(section_names), 0, 0, 1, 0))
    section_offset = append(b''.join(struct.pack('<10I', *section) for section in sections))
    ident = b'\x7fELF\x01\x01\x01' + b'\0' * 9
    data[:52] = struct.pack('<16sHHIIIIIHHHHHH', ident, 2, 40, 1,
                           0x10000001, 0, section_offset, 0,
                           52, 0, 0, 40, len(sections), len(sections) - 1)
    path.write_bytes(data)


class TraceValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / 'demo'
        (self.project / 'build').mkdir(parents=True)
        self.elf = self.project / 'build/demo.elf'
        self.gui = types.SimpleNamespace(selectedProgram='demo')
        project_patch = patch.object(helper, 'getProjectPath', return_value=str(self.root))
        project_patch.start()
        self.addCleanup(project_patch.stop)
        report_patch = patch.object(helper, 'printState')
        self.report = report_patch.start()
        self.addCleanup(report_patch.stop)

    def message(self):
        return self.report.call_args.kwargs['info']

    def test_traced_elf_without_trace_config_is_accepted(self):
        write_elf(self.elf, REQUIRED)
        self.assertFalse((self.project / 'traceConfig.h').exists())
        self.assertTrue(helper.validatePicoTrace(self.gui, quiet=True))
        self.report.assert_not_called()
        self.assertTrue(helper.validatePicoTrace(self.gui))
        self.assertIn('RT-Trace RTT', self.message())

    def test_cmake_and_old_config_cannot_qualify_plain_rtt(self):
        write_elf(self.elf, ('_SEGGER_RTT',))
        (self.project / 'CMakeLists.txt').write_text('rt_trace_enable(demo)\n')
        (self.project / 'traceConfig.h').write_text('#define TRACE_RTT\n')
        self.assertFalse(helper.validatePicoTrace(self.gui, quiet=True))
        self.assertIn('trace_init', self.message())
        self.assertIn('trace_state_core0', self.message())

    def test_undefined_trace_symbols_are_rejected(self):
        write_elf(self.elf, REQUIRED[:-1], undefined=('_SEGGER_RTT',))
        self.assertFalse(helper.validatePicoTrace(self.gui))
        self.assertIn('_SEGGER_RTT', self.message())

    def test_initializer_registration_is_required(self):
        write_elf(self.elf, tuple(name for name in REQUIRED if name != '__pre_init_trace_init'))
        self.assertFalse(helper.validatePicoTrace(self.gui))
        self.assertIn('__pre_init_trace_init', self.message())

    def test_stripped_elf_reports_symbol_table_requirement(self):
        write_elf(self.elf, REQUIRED, stripped=True)
        self.assertFalse(helper.validatePicoTrace(self.gui))
        self.assertIn('preserve the ELF symbol table', self.message())

    def test_missing_elf_reports_build_requirement(self):
        self.assertFalse(helper.validatePicoTrace(self.gui))
        self.assertIn('build the program first', self.message())

    def test_invalid_elf_is_reported_without_raising(self):
        self.elf.write_bytes(b'not an ELF file')
        self.assertFalse(helper.validatePicoTrace(self.gui))
        self.assertEqual(self.report.call_args.args[0], 'ERROR invalid ELF')

    def test_no_selected_program_is_reported(self):
        self.gui.selectedProgram = None
        self.assertFalse(helper.validatePicoTrace(self.gui))
        self.assertEqual(self.report.call_args.args[0], 'ERROR no program')


if __name__ == '__main__':
    unittest.main()
