'''
Module is used to export the collected task information for external processing. 
For each task, one CSV-file is exported that lists the following parameters for each job. 
Column  Data
--------------------
0       Release Time
1       Start Time
2       Finish Time
3       Execution Time
'''
from threading import Thread
from pathlib import Path
import HelperFunctions
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


def getTimestring(ts, before, after):
    """
    Helper to create a timestring based on the magnitude of the timestamp.
    """
    unit = ""
    value = 0.0
    if ts > 1000000:    # Value in seconds
        unit = "s "
        value = ts / 1000000
    elif ts > 1000:     # Value in ms
        unit = "ms"
        value = ts / 1000
    else:               # Value in nss
        unit = "us" 
        value = ts

    return f"{value:0{before + 1 + after}.{after}f}{unit}"

def createDataFile(taskDataFile, task):
    """
    Export the data of all task jobs to a CSV-file.
    """

    with open(taskDataFile, "w", newline="") as csvFile:
        writer = csv.writer(csvFile)
        writer.writerow(["JobId", "ReleaseTime_us", "StartTime_us", "FinishTime_us", "ExecutionTime_us"])

        for job in task.jobs:
            jobData = [job.id, job.releaseTime, job.getStartTime(), job.getFinishTime(), job.getExecutionTime()]
            if all(element is not None for element in jobData): # Only include complete jobs
                writer.writerow(jobData)

def exportData(gui, tasks):

    """
    First export data for each task and job in task specific files. If the file already exists, this is skipped.
    """
    destFolder = gui.viewingFolder

    for task in tasks:
        taskDataFile = os.path.join(destFolder, f"{task.name.replace(' ', '_')}.csv")
        if not Path(taskDataFile).is_file():
            createDataFile(taskDataFile, task)

    """
    Second, some observed parameters are printed in the GUI textbox.
    """
    maxName = 0
    for task in tasks:
        if len(task.name) > maxName:
            maxName = len(task.name)

    HelperFunctions.printState("Found trace data for tasks:")

    lastActivity = getLastActivity(tasks)

    print(f"\n   Task Name:{' ' * (maxName - len('Task Name'))}| Response Time | WCET        | Utilization")
    print(f"{'='*(3+maxName+46)}")
    for task in tasks:
        rt = task.getMaxResponseTime()
        wcet = task.getMaxExecutionTime()
        util = task.getUtilization(lastActivity) * 100
        print(f"   {task.name}{' ' * (maxName - len(task.name))} |   {getTimestring(rt, 4, 4)} | {getTimestring(wcet, 4, 4)} | {util: {7}.{3}f}%")


