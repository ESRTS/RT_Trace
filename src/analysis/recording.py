"""
Builds the AnalysisContext of a recording folder: from tasks the GUI already parsed (context_for_tasks),
or by parsing the folder itself, for the command line (load_recording).
"""
import dataclasses
import json
import os

from .base import AnalysisContext

SIMULATOR, FREERTOS, LINUX, JETSON = 'Simulator', 'Pico2 FreeRTOS', 'RPI Linux', 'Jetson Thor'   # Names of the GUI


class RecordingError(Exception):
    """The folder is no recording, or it cannot be parsed."""


def simulator_info(folder):
    """(TaskSet or None, header of sim_trace.jsonl or {}, warnings) of a simulator recording."""
    from simulator import TaskSetError, load_taskset
    from simulator import trace_format as fmt
    warnings, taskset, header = [], None, {}
    path = os.path.join(folder, 'taskset.json')
    if os.path.isfile(path):
        try:
            taskset = load_taskset(path)
        except (TaskSetError, OSError) as error:
            warnings.append(f'taskset.json is not used: {error}')
    try:
        with open(os.path.join(folder, fmt.TRACE_FILE)) as file:
            header = json.loads(file.readline())
    except (OSError, ValueError):
        pass
    return taskset, header if isinstance(header, dict) else {}, warnings


def taskset_in_ns(taskset):
    """The simulator TaskSet (microseconds, as in taskset.json) with its times in nanoseconds, like the trace."""
    from timeunits import US
    if taskset is None:
        return None
    scale = lambda value: None if value is None else value * US
    tasks = tuple(dataclasses.replace(task, period=scale(task.period), wcet=scale(task.wcet),
                                      deadline=scale(task.deadline), phase=scale(task.phase), bcet=scale(task.bcet))
                  for task in taskset.tasks)
    return dataclasses.replace(taskset, tasks=tasks)


def context_for_tasks(tasks, folder, platform=None, cores=None, warnings=(), header=None):
    """The context of tasks that were parsed from the recording folder (header: of the simulator trace)."""
    taskset, trace_end, warnings = None, None, list(warnings)
    if platform == SIMULATOR or os.path.isfile(os.path.join(folder, 'sim_trace.jsonl')):
        taskset, folderHeader, extra = simulator_info(folder)
        header = header or folderHeader
        warnings += extra
        cores = header.get('cores', cores)
        from TraceParserSim import horizon_ns
        trace_end = horizon_ns(header)
    return AnalysisContext(tasks, folder, platform, cores, taskset_in_ns(taskset), trace_end, warnings)


def load_recording(folder, strict=False, user_events=True):
    """
    Parse a recording folder (simulator, Pico 2 FreeRTOS, Linux or Jetson), a simulator trace file or an
    Nsight Systems SQLite export, and return its AnalysisContext.
    The GUI's parsers are used, without writing CSV files. strict and user_events (Linux) are the
    settings of the same name in config.ini.
    """
    folder = os.path.abspath(folder)
    simTrace = os.path.join(folder, 'sim_trace.jsonl')
    sqlite = None
    if os.path.isfile(folder) and folder.endswith('.jsonl'):    # A simulator trace with another name (simulator -o)
        simTrace, folder = folder, os.path.dirname(folder)
    elif os.path.isfile(folder) and folder.endswith('.sqlite'):  # An Nsight Systems export
        sqlite, folder = folder, os.path.dirname(folder)
    if not os.path.isdir(folder):
        raise RecordingError(f'not a recording folder: {folder}')
    try:
        if os.path.isfile(simTrace):
            from TraceParserSim import read_trace
            header, tasks, warnings = read_trace(simTrace)
            return context_for_tasks(tasks, folder, SIMULATOR, header.get('cores'), warnings, header)
        if os.path.isfile(os.path.join(folder, 'raw_buffer0.txt')):
            from TraceParserFreeRTOS import readRecording
            cores = 0
            while os.path.isfile(os.path.join(folder, f'raw_buffer{cores}.txt')):
                cores += 1
            tasks, warnings = readRecording(folder, cores, strict=strict, writeEvents=False)
            return context_for_tasks(tasks, folder, FREERTOS, cores, warnings)
        if os.path.isfile(os.path.join(folder, 'trace.txt')):
            from TraceParserLinux import parser
            tasks = parser(os.path.join(folder, 'trace.txt'), os.path.join(folder, 'events.txt'), user_events)
            return context_for_tasks(tasks, folder, LINUX)
        if sqlite is not None or any(name.endswith('.sqlite') for name in os.listdir(folder)):
            from TraceParserJetson import find_export, read_trace
            tasks, info, warnings = read_trace(sqlite or find_export(folder))
            return context_for_tasks(tasks, folder, JETSON, info['cpus'] or None, warnings)
    except RecordingError:
        raise
    except Exception as error:
        raise RecordingError(f'cannot parse {folder}: {type(error).__name__}: {error}') from error
    raise RecordingError(f'no recording in {folder}: expected sim_trace.jsonl, raw_buffer0.txt, trace.txt or a .sqlite export')
