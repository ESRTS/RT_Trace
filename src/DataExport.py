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
    maxName = len('Task')
    for task in tasks:
        if len(task.name) > maxName:
            maxName = len(task.name)

    HelperFunctions.printState("Tasks", info="(times in ms, the maximum over all jobs)")

    lastActivity = getLastActivity(tasks)

    print(f"\n  {'Task':<{maxName}}  {'Response':>10}  {'Execution':>10}  {'Utilization':>11}")
    print("  " + "-" * (maxName + 37))
    for task in tasks:
        rt = task.getMaxResponseTime() / 1000
        wcet = task.getMaxExecutionTime() / 1000
        util = task.getUtilization(lastActivity) * 100
        print(f"  {task.name:<{maxName}}  {rt:>10.3f}  {wcet:>10.3f}  {util:>10.2f}%")


