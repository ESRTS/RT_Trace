"""
GUI glue of the simulator target. A simulator "program" is a folder in the project folder
that contains a taskset.json. Recording runs the simulation instead of a hardware capture.
"""
import os
import shutil

import HelperFunctions
from simulator import TaskSetError, get_policy, load_taskset, simulate
from simulator import trace_format as fmt

TASKSET_FILE = 'taskset.json'
MAX_VIEW_CORES = 6      # TraceView has one color per core for six cores


def isTaskSetFolder(path):
    """programCheckFunc: a folder with a taskset.json is a simulator program."""
    return os.path.isfile(os.path.join(path, TASKSET_FILE))


def getTaskSetPath(gui):
    return os.path.join(HelperFunctions.getProjectPath(), gui.selectedProgram, TASKSET_FILE)


def checkTaskSet(path):
    """Load and validate a task set for simulation. Raises TaskSetError."""
    taskset = load_taskset(path)
    get_policy(taskset.scheduler).validate(taskset)
    if taskset.cores > MAX_VIEW_CORES:
        raise TaskSetError(f'{taskset.cores} cores, the trace view shows at most {MAX_VIEW_CORES}')
    return taskset


def validateTaskSet(gui, quiet=False):
    """pathValidationFunc: check the selected task set before simulating."""
    if not gui.selectedProgram:
        HelperFunctions.printState("ERROR no task set", info="Select a folder with a " + TASKSET_FILE + " first")
        return False
    path = getTaskSetPath(gui)
    try:
        taskset = checkTaskSet(path)
    except (TaskSetError, OSError) as error:
        HelperFunctions.printState("ERROR invalid task set", info=f"{gui.selectedProgram}/{TASKSET_FILE}: {error}")
        return False
    if not quiet:
        HelperFunctions.printState("Task set", info=f"{gui.selectedProgram}: {len(taskset.tasks)} tasks, "
                                   f"{taskset.cores} core(s), scheduler '{taskset.scheduler}', "
                                   f"utilization {taskset.utilization():.2f}"
                                   + (f", {len(taskset.precedence)} precedence constraint(s)" if taskset.precedence else ""))
    return True


def runSimulation(gui):
    """recordTraceFunc: simulate the selected task set in a background thread."""
    HelperFunctions.startWorker(gui, simulation_thread, gui.recordingFinished)


def simulation_thread(gui):
    folder = gui.recordingFolder
    taskset_path = getTaskSetPath(gui)
    seconds = getattr(gui, 'recordingSeconds', None)
    if seconds is None:
        seconds = HelperFunctions.parseRecordingDuration(
            HelperFunctions.readConfig().get('general', 'measure_seconds', fallback='2'))
    horizon = max(1, round(seconds * 1_000_000))
    try:
        taskset = checkTaskSet(taskset_path)
        HelperFunctions.makeFolder(folder)
        shutil.copyfile(taskset_path, os.path.join(folder, TASKSET_FILE))    # Keep the input with the trace
        simulate(taskset, os.path.join(folder, fmt.TRACE_FILE), horizon)
    except Exception as error:
        HelperFunctions.reportError("Simulation failed", error, folder, "simulation_error.log",
                                    "Check " + TASKSET_FILE + ".", context="Task set: " + taskset_path)
        return 1
    HelperFunctions.printState("Simulation done", info=f"{seconds:g} s simulated, written to {fmt.TRACE_FILE}")
    return 0
