#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
import os
from pathlib import Path
import shutil
from threading import Thread
import HelperFunctions
import configparser

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.debug.elf.symbols import ELFSymbolProvider
from pyocd.debug.rtt import RTTControlBlock
from pyocd.flash.file_programmer import FileProgrammer

#configName = "Pico2_FreeRTOS_RTT"

def loadPico2RttTraceBuffers(gui):
    
    HelperFunctions.startWorker(gui, rtt_thread, gui.recordingFinished)

def rtt_thread(gui):
    """
    Thread to flash the target and record the RTT buffers.
    """
    #global configName

    configName = "general"

    cwd = HelperFunctions.getCwd()

    folderName = gui.recordingFolder
    HelperFunctions.makeFolder(folderName)
    filename1 = os.path.abspath(os.path.join(folderName, 'raw_buffer0.txt'))
    filename2 = os.path.abspath(os.path.join(folderName, 'raw_buffer1.txt'))
    
    config = HelperFunctions.readConfig()
    elf = HelperFunctions.getElfFilePath(gui)#config.get(configName,'elf', fallback = None)
    probe, target, frequency = recorder_settings(config)
    measure_seconds = getattr(gui, 'recordingSeconds', None)
    if measure_seconds is None:
        measure_seconds = HelperFunctions.parseRecordingDuration(
            config.get(configName, 'measure_seconds', fallback='2'))

    if elf is None or not os.path.exists(elf):
        HelperFunctions.printState("ERROR ELF not found", info=str(elf) + " does not exist. Build the program first.")
        return 1
    
    # Record the trace from the target platform
    try:
        return record_data(elf, probe, target, frequency, measure_seconds, filename1, filename2)
    except Exception as e:      # One line for the user, the traceback in record_error.log
        HelperFunctions.reportError("Recording failed", e, folderName, "record_error.log",
                                    HelperFunctions.recordHint(e), context=HelperFunctions.recordContext(gui, elf))
        return 1

def parse_int(value: str) -> int:
    return int(value, 0)


def wait_for_halt(core, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if core.get_state() == Target.State.HALTED:
            return True
        time.sleep(0.01)
    return False


def wait_for_pc(core, expected_addr: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    expected = expected_addr & ~1

    while time.monotonic() < deadline:
        if core.get_state() == Target.State.HALTED:
            pc = core.read_core_register("pc") & ~1
            if pc != expected:
                print(f"halted at 0x{pc:08X}, but waiting for 0x{expected_addr:08X}")
            return pc == expected
        time.sleep(0.01)

    return False


def halt_cores(cores):
    """Stop all producers before inspecting or draining shared trace state."""
    for core in cores:
        core.halt()
    for number, core in enumerate(cores):
        if not wait_for_halt(core):
            raise RuntimeError(f"Core {number} did not halt within 5 s")


def resume_cores(cores):
    # Release core 1 first, then core 0. The RP2350 shared timer can remain
    # paused while either core is debug-halted, including during SDK startup.
    for core in reversed(cores):
        core.resume()


def record_data(elf, probe, target, frequency, measure_seconds, filename1, filename2):
    # Archive before connecting/flashing, then use these exact bytes for both
    # programming and symbols, even if the project is rebuilt during capture.
    saved_elf = Path(filename1).resolve().parent / 'firmware.elf'
    saved_elf.parent.mkdir(parents=True, exist_ok=True)
    if not saved_elf.exists() or not os.path.samefile(elf, saved_elf):
        shutil.copyfile(elf, saved_elf)
    elf = str(saved_elf)

    session = ConnectHelper.session_with_chosen_probe(
        blocking=False,             # Without a probe, return None (reported below) instead of waiting forever
        unique_id=probe,
        options={
            "target_override": target,
            "frequency": frequency,
            "connect_mode": "halt",
            "resume_on_disconnect": False,
        },
    )

    if session is None:
        HelperFunctions.printState("ERROR no debug probe", info=HelperFunctions.NO_PROBE_HINT)
        return 1

    with session:
        target = session.board.target
        if 0 not in target.cores or 1 not in target.cores:
            HelperFunctions.printState("ERROR cores missing", info="RP2350 tracing requires debug access to both cores. Close other debugger sessions and check the pyOCD target configuration.")
            return 1
        cores = tuple(target.cores[number] for number in sorted(target.cores))
        core = target.cores[0]

        print(f"Programming {os.path.basename(elf)} ...")
        FileProgrammer(session).program(elf)
        print("Programming complete.")

        target.elf = elf
        symbol_provider = ELFSymbolProvider(target.elf)

        main_addr = symbol_provider.get_symbol_value("main")
        hook_addr = symbol_provider.get_symbol_value("trace_init")
        rtt_addr = symbol_provider.get_symbol_value("_SEGGER_RTT")

        if main_addr is None:
            HelperFunctions.printState("ERROR not a program", info=os.path.basename(elf) + " has no main(). Select the ELF of a program.")
            return 1
        if hook_addr is None:
            HelperFunctions.printState("ERROR no tracing", info=HelperFunctions.noTracingHint(elf))
            return 1
        if rtt_addr is None:
            HelperFunctions.printState("ERROR no RTT", info=os.path.basename(elf) + " has no RTT control block (_SEGGER_RTT). Build the firmware with the RT-Trace CMake integration (see target/README.md).")
            return 1
        
        print(f"main() at 0x{main_addr:08X}")
        print(f"trace_init() at 0x{hook_addr:08X}")
        print(f"_SEGGER_RTT at 0x{rtt_addr:08X}")

        print("Reset and halt.")
        # RP2350 SYSRESETREQ is a per-core warm reset. Reset both explicitly:
        # otherwise core 1 can resume stale firmware after flashing overwrites
        # SRAM, and core 0 then waits forever for its boot-ROM launch handshake.
        for producer in reversed(cores):
            producer.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)
        halt_cores(cores)
        # The SDK runs trace_init() before main. Breaking at the initializer's
        # entry would stop it before it can configure the RTT control block.
        print("Setting breakpoint for main()")
        if not core.set_breakpoint(main_addr):
            HelperFunctions.printState("ERROR breakpoint", info="Could not set a breakpoint at main(). Close other debugger sessions and try again.")
            return 1
        try:
            print("Resume both cores and wait for main() ...")
            resume_cores(cores)
            if not wait_for_pc(core, main_addr, timeout=10.0):
                HelperFunctions.printState("ERROR program did not start", info="The program did not reach main() within 10 s. Check pre-main initialization and faults with the debugger.")
                return 1
        finally:
            # A timeout may leave either core running. Stop both while inspecting
            # RTT, and remove the temporary breakpoint on failure too.
            try:
                halt_cores(cores)
            finally:
                core.remove_breakpoint(main_addr)
        print("Hit main(); pre-main trace initialization has completed.")

        rtt = RTTControlBlock.from_target(target, address=rtt_addr)
        rtt.start()

        if len(rtt.up_channels) < 3:
            HelperFunctions.printState("ERROR RTT channels missing", info="The program has " + str(len(rtt.up_channels)) + " RTT up channels, the trace needs 3. Build it again with the current RT_Trace target code.")
            return 1
        print("Read information for RTT channel 1 and 2.")
        ch1 = rtt.up_channels[1]
        ch2 = rtt.up_channels[2]
        if ch1.size <= 0 or ch2.size <= 0:
            HelperFunctions.printState("ERROR RTT channels not initialized", info="Trace channels 1/2 are not configured at main(). Check the pre-main trace initializer and avoid resetting RTT from other code.")
            return 1

        with open(filename1, "wb") as f1, open(filename2, "wb") as f2:
            try:
                # Prepare the host files while main is still stopped so early
                # application events can be read immediately after resuming.
                print(f"Resume and start Trace Record for {measure_seconds} seconds")
                resume_cores(cores)
                deadline = time.monotonic() + measure_seconds
                while time.monotonic() < deadline:
                    data1 = ch1.read()
                    if data1:
                        #print(data1.decode("ascii", errors="replace"))
                        f1.write(data1)
                        f1.flush()

                    data2 = ch2.read()
                    if data2:
                        #print(data2.decode("ascii", errors="replace"))
                        f2.write(data2)
                        f2.flush()

                    time.sleep(0.001)
            finally:
                print("Finished trace recording, halting both cores.")
                halt_cores(cores)
                # Once both producers are halted, read the bytes that arrived
                # after the final polling read before closing the files.
                f1.write(ch1.read())
                f2.write(ch2.read())

        print(f"Wrote RTT channel 1 to {filename1}")
        print(f"Wrote RTT channel 2 to {filename2}")
        return 0

PLATFORM = 'Pico2 FreeRTOS'     # The platform name of the GUI; recordings go to data/Pico2_FreeRTOS/
NUM_CORES = 2


def resolve_program(program):
    """
    (ELF path, program name) of a project folder (build/<name>.elf, as in the GUI), the name of a project
    in project_path, or an ELF file. The ELF itself is checked later.
    """
    path = os.path.abspath(os.path.expanduser(program))
    if os.path.isfile(path):
        return path, Path(path).stem
    candidates = [path]
    if not os.path.isabs(os.path.expanduser(program)):
        candidates.append(os.path.join(HelperFunctions.getProjectPath(), program))
    for folder in candidates:
        if os.path.isdir(folder):
            name = os.path.basename(os.path.normpath(folder))
            return os.path.join(folder, 'build', name + '.elf'), name
    raise ValueError(f"{program}: not an ELF file, a project folder or a project in {HelperFunctions.getProjectPath()}")


def recorder_settings(config, probe=None, target=None, frequency=None):
    """(probe, target, frequency) from the [general] settings of the config, unless given."""
    return (probe or config.get('general', 'probe', fallback=None),
            target or config.get('general', 'target', fallback='rp2350'),
            frequency or int(config.get('general', 'frequency', fallback='4_000_000')))


def postprocess(folder, csv, analyses, config):
    """
    As loading in the GUI: parse the recording, write events.txt and the per-task CSVs (csv), then run
    analyses (None: no analyses, empty: the default analyses). Returns the exit code.
    """
    import DataExport
    from TraceParserFreeRTOS import readRecording, reportedWarnings
    try:
        tasks, warnings = readRecording(folder, NUM_CORES, writeEvents=csv,
                                        strict=config.getboolean('general', 'strict', fallback=False))
    except Exception as error:
        HelperFunctions.reportError('Parsing failed', error, folder, 'parse_error.log',
                                    'Check the recording and job_policy.json; details are in parse_error.log.')
        return 1
    for warning in reportedWarnings(warnings):
        HelperFunctions.printState('Warning', info=warning)
    if csv:
        written = DataExport.exportCsvFiles(folder, tasks, overwrite=True)
        print(f'{len(written)} CSV file(s) and events.txt -> {folder}')
    if analyses is not None:
        import analysis
        from analysis.__main__ import run_named
        return run_named(analysis.context_for_tasks(tasks, folder, PLATFORM, NUM_CORES, warnings), analyses)
    return 0


def main(argv=None) -> int:
    """
    Command line: flash a program, record its trace for some seconds and store it as a recording.

        python src/RttTraceRecorder.py MyProject 2                  # project in project_path (config)
        python src/RttTraceRecorder.py path/to/MyProject 0.5 --csv  # project folder; also events.txt and CSVs
        python src/RttTraceRecorder.py build/MyProject.elf 2 -o out/rec --analyze deadline_misses
    """
    ap = argparse.ArgumentParser(prog='RttTraceRecorder',
                                 description='Flash a Pico 2 FreeRTOS program and record its RT-Trace trace over RTT.')
    ap.add_argument('program', help='project folder (with build/<name>.elf), the name of a project in project_path, '
                                    'or an ELF file')
    ap.add_argument('duration', nargs='?', type=duration_arg,
                    help='recording time in seconds (default: measure_seconds of the config)')
    ap.add_argument('-o', '--output', help='recording folder (default: data/Pico2_FreeRTOS/<name>_<timestamp>, '
                                           'listed under View Trace in the GUI)')
    ap.add_argument('-n', '--name', help='recording name in the default folder (default: the program name)')
    ap.add_argument('--csv', action='store_true', help='then write events.txt and one CSV file per task, as loading in the GUI does')
    ap.add_argument('--analyze', nargs='*', metavar='ANALYSIS',
                    help='then run analyses (default: the task statistics; see python src/analysis --list)')
    ap.add_argument('--probe', help='debug probe unique ID (default: probe of the config, or the only probe)')
    ap.add_argument('--target', help='pyOCD target (default: target of the config, rp2350)')
    ap.add_argument('--frequency', type=parse_int, help='SWD frequency in Hz (default: frequency of the config)')
    args = ap.parse_args(argv)

    config = HelperFunctions.readConfig()
    try:
        elf, name = resolve_program(args.program)
    except ValueError as error:
        ap.error(str(error))
    seconds = args.duration
    if seconds is None:
        try:
            seconds = HelperFunctions.parseRecordingDuration(config.get('general', 'measure_seconds', fallback='2'))
        except ValueError as error:
            ap.error(f'measure_seconds in the config: {error}')
    if args.analyze:                    # Unknown analyses fail before the target is flashed
        from analysis.__main__ import available_analyses
        unknown = [a for a in args.analyze if a not in available_analyses()]
        if unknown:
            ap.error(f"unknown analysis: {', '.join(unknown)} (see python src/analysis --list)")

    problem = HelperFunctions.picoTraceProblem(elf)
    if problem is not None:             # Checked before a folder is created or the probe is touched
        HelperFunctions.printState(problem[0], info=problem[1])
        return 1
    folder = os.path.abspath(args.output) if args.output else HelperFunctions.newRecordingFolder(PLATFORM, args.name or name)
    if os.path.exists(os.path.join(folder, 'raw_buffer0.txt')):
        HelperFunctions.printState('ERROR recording exists', info=folder + ' already contains a recording, choose another folder')
        return 1

    probe, target, frequency = recorder_settings(config, args.probe, args.target, args.frequency)
    HelperFunctions.printState('Recording', info=f'{name} for {seconds:g} s -> {folder}')
    files = [os.path.join(folder, f'raw_buffer{core}.txt') for core in range(NUM_CORES)]
    try:
        result = record_data(elf, probe, target, frequency, seconds, *files)
    except KeyboardInterrupt:
        HelperFunctions.printState('ERROR recording interrupted', info=f'the partial recording is in {folder}')
        return 130
    except Exception as error:          # One line for the user, the traceback in record_error.log
        HelperFunctions.reportError('Recording failed', error, folder, 'record_error.log', HelperFunctions.recordHint(error),
                                    context=f'Program: {elf}\nPlatform: {PLATFORM}\nRecording: {folder}')
        return 1
    if result != 0:
        return result
    HelperFunctions.printState('Recorded', info=folder)
    if args.csv or args.analyze is not None:
        return postprocess(folder, args.csv, args.analyze, config)
    return 0


def duration_arg(value):
    try:
        return HelperFunctions.parseRecordingDuration(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


if __name__ == "__main__":
    raise SystemExit(main())
