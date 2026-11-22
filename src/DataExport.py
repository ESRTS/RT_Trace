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
All times are in microseconds. The deadline is the one the trace view uses (see jobDeadline()).

exportCsvFiles() only writes files and needs no GUI modules, so tools such as the simulator's
command line can use it. printStatistics() prints to the GUI output box and imports HelperFunctions.
'''
from pathlib import Path
from TraceTask import *
import os
import csv

def getLastActivity(tasks):
    """
    Get the last activity of any of the tasks to compute the utilization time.
    """
    maxActivity = 0

    for task in tasks:
        for job in task.jobs:
            if len(job.execIntervals) > 0:
                lastActivity = job.execIntervals[-1].stop
                if maxActivity < lastActivity:
                    maxActivity = lastActivity
    return maxActivity


def msString(time_us):
    """
    Formats a time in us as ms, or "-" if there is none (no job of the task finished in the trace).
    """
    return "-" if time_us is None else f"{time_us / 1000:.3f}"

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
        writer.writerow(["JobId", "ReleaseTime_us", "StartTime_us", "FinishTime_us", "ExecutionTime_us",
                         "Deadline_us", "DeadlineEstimated"])

        for index, job in enumerate(task.jobs):
            jobData = [job.id, job.releaseTime, job.getStartTime(), job.getFinishTime(), job.getExecutionTime()]
            if not job.incomplete and not job.uncertain and all(element is not None for element in jobData):
                deadline, estimated = jobDeadline(task, index)     # May be unknown, the row is kept anyway
                writer.writerow(jobData + ["" if deadline is None else deadline,
                                           "" if estimated is None else int(estimated)])

def exportCsvFiles(destFolder, tasks, overwrite=False):
    """
    Export one CSV file per task into destFolder. Skip existing files unless explicitly regenerated.
    Returns the paths of the files written.
    """
    written = []
    for task in tasks:
        taskDataFile = os.path.join(destFolder, f"{task.name.replace(' ', '_')}.csv")
        if overwrite or not Path(taskDataFile).is_file():
            createDataFile(taskDataFile, task)
            written.append(taskDataFile)
    return written

def exportData(gui, tasks, overwrite=False):
    """
    Export the CSV files into the folder of the loaded trace, then print the statistics in the GUI.
    """
    exportCsvFiles(gui.viewingFolder, tasks, overwrite)
    printStatistics(tasks)

def printStatistics(tasks):
    """
    Print the observed parameters of each task in the GUI output box.
    """
    import HelperFunctions      # GUI module, imported here so the CSV export works without it

    maxName = len('Task')
    for task in tasks:
        if len(task.name) > maxName:
            maxName = len(task.name)

    HelperFunctions.printState("Tasks", info="(times in ms, the maximum over all jobs)")

    lastActivity = getLastActivity(tasks)

    print(f"\n  {'Task':<{maxName}}  {'Response':>10}  {'Execution':>10}  {'Utilization':>11}")
    print("  " + "-" * (maxName + 37))
    for task in tasks:
        rt = msString(task.getMaxResponseTime())
        wcet = msString(task.getMaxExecutionTime())
        util = task.getUtilization(lastActivity) * 100
        print(f"  {task.name:<{maxName}}  {rt:>10}  {wcet:>10}  {util:>10.2f}%")
