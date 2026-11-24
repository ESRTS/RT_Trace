'''
Module is used to export the collected task information for external processing. 
For each task, one CSV-file is exported that lists the following parameters for each job. 
Column  Data
--------------------
0       Job ID
1       Release Time
2       Start Time
3       Finish Time
4       Execution Time
5       Absolute deadline, empty if unknown
6       DeadlineEstimated, empty if the deadline is unknown:
          0 = the parser determined the deadline from traced events (simulator: from the task set),
          1 = its time is estimated, or it is the next job's release because the parser sets no deadlines
All times are in nanoseconds (timeunits.py). The deadline is the one the trace view uses (see jobDeadline()).

exportCsvFiles() only writes files and needs no GUI modules, so tools such as the simulator's
command line can use it. exportData() also runs the default analyses (src/analysis), which print the
task statistics.
'''
from pathlib import Path
from TraceTask import *
import os
import re
import csv

def jobDeadline(task, index):
    """
    Returns (absolute deadline, estimated) of task.jobs[index], the same deadline the trace view uses.
    Parsers that set deadlines (format 3, simulator) mark that with task.deadlinesExplicit; otherwise
    the next job's release is used, which the parser does not know for sure, so it counts as estimated.
    (None, None) if there is no deadline.
    """
    job = task.jobs[index]
    if getattr(task, 'deadlinesExplicit', False):
        if job.deadline is None:
            return None, None
        return job.deadline, bool(job.deadlineEstimated)
    if index + 1 < len(task.jobs):
        return task.jobs[index + 1].releaseTime, True
    return None, None

def createDataFile(taskDataFile, task):
    """
    Export the data of all task jobs to a CSV-file.
    """

    with open(taskDataFile, "w", newline="") as csvFile:
        writer = csv.writer(csvFile)
        writer.writerow(["JobId", "ReleaseTime_ns", "StartTime_ns", "FinishTime_ns", "ExecutionTime_ns",
                         "Deadline_ns", "DeadlineEstimated"])

        for index, job in enumerate(task.jobs):
            jobData = [job.id, job.releaseTime, job.getStartTime(), job.getFinishTime(), job.getExecutionTime()]
            if not job.incomplete and not job.uncertain and all(element is not None for element in jobData):
                deadline, estimated = jobDeadline(task, index)     # May be unknown, the row is kept anyway
                writer.writerow(jobData + ["" if deadline is None else deadline,
                                           "" if estimated is None else int(estimated)])

def csvFileName(name):
    """A row name as file name on every system: whitespace and the characters Windows forbids become '_'."""
    return re.sub(r'[<>:"/\\|?*\s]', '_', name)

def exportCsvFiles(destFolder, tasks, overwrite=False):
    """
    Export one CSV file per task into destFolder. Skip existing files unless explicitly regenerated.
    Returns the paths of the files written.
    """
    written = []
    for task in tasks:
        taskDataFile = os.path.join(destFolder, f"{csvFileName(task.name)}.csv")
        if overwrite or not Path(taskDataFile).is_file():
            createDataFile(taskDataFile, task)
            written.append(taskDataFile)
    return written

def exportData(gui, tasks, overwrite=False):
    """
    Export the CSV files into the folder of the loaded trace, then run the default analyses (the task
    statistics), which print into the GUI output box.
    """
    exportCsvFiles(gui.viewingFolder, tasks, overwrite)
    import analysis
    target = gui.targets[gui.selectedTarget] if hasattr(gui, 'targets') else {}
    analysis.run_defaults(analysis.context_for_tasks(tasks, gui.viewingFolder, target.get('name'),
                                                     target.get('numCores')))
