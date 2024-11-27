from enum import Enum

class ExecutionType(Enum):
    """
    Enum to describe the different phases of the AER model.
    """
    READ = 1
    EXECUTE = 2
    WRITE = 3

class TraceInterval():
    """
    The class implements an execution interval of a job in the trace. 
    """
    def __init__(self, startTime, core, type):
        self.type = type
        self.core = core
        self.start = startTime

    def stop(self, stopTime):
        self.stop = stopTime

    def __str__(self):
        return str(self.type) + ": Core " + str(self.core) + ", [" + str(self.start) + ", " + str(self.stop) + "], len=" + str(self.stop - self.start) + ""

class TraceJob():
    """
    The class implements a job of a task in the trace.
    """
    def __init__(self, task, id, releaseTime, deadline):
        self.task = task                    # Stores the task this job belongs to
        self.id = id                        # Job id
        self.execIntervals = []             # Stores all execution intervals
        self.releaseTime = releaseTime      # Stores the release time of the job
        self.deadline = deadline            # Stores the deadline of the job
        self.activeInterval = None          # Stores the current execution interval (i.e. this interval is not complete)
    
    def __str__(self) -> str:
        return self.task.name + "-" + str(self.id)

    def printInfo(self):
        if self.deadline is not None:
            print("\tJob-" + str(self.id) + " Release: " + str(self.releaseTime) + " Abs. Deadline: " + str(self.deadline) + " Rel. Deadline: " + str(self.deadline - self.releaseTime))
        else:
            print("\tJob-" + str(self.id) + " Release: " + str(self.releaseTime) + " Abs. Deadline: - Rel. Deadline: -")

        for seg in self.execIntervals:
            print("\t\t" + str(seg))

    def getStartTime(self):
        """
        Returns the start time of the job, or None if there was no execution.
        """
        if len(self.execIntervals) > 0:
            return self.execIntervals[0].start
        else:
            return None

    def getFinishTime(self):
        """
        Returns the finish time of the job or None if there was no execution.
        """
        if len(self.execIntervals) > 0:
            return self.execIntervals[-1].stop
        else:
            return None

    def startExec(self, ts, core, type):
        """
        Start an execution interval for this job.
        """
        assert self.activeInterval == None

        self.activeInterval = TraceInterval(ts, core, type)

    def stopExec(self, ts):
        """
        Stop an execution interval for this job.
        """
        assert self.activeInterval != None

        self.activeInterval.stop(ts)
        self.execIntervals.append(self.activeInterval)
        self.activeInterval = None

class TraceTask():
    """
    The class implements a task used to store trace information.
    """

    def __init__(self, id, name, priority, color):
        self.id = id                # Task ID
        self.name = name            # Task name
        self.priority = priority    # Task priority
        self.jobs = []              # Jobs of the task
        self.currentJob = None

        # Used for the visualization only
        self.taskColor = color      # Color of the task in the trace
        self.leftIndex = 0          # Index of the first job in the visible field
        self.rightIndex = 0         # Index of the last job in the visible

    def __str__(self) -> str:
        return self.name + " (" + str(len(self.jobs)) + " jobs)"

    def printInfo(self):
        if self.priority == None:
            print("Task: " + self.name + " ID: " + str(self.id) + " Priority: -")
        else:
            print("Task: " + self.name + " ID: " + str(self.id) + " Priority: " + str(self.priority))

    def printAll(self):
        self.printInfo()

        for job in self.jobs:
            job.printInfo()

    def newJob(self, releaseTime, deadline):
        """
        Create a new job for this task. 
        """
        assert self.currentJob == None

        self.currentJob = TraceJob(self, len(self.jobs), releaseTime, deadline)

    def setCurrentJobDeadline(self, deadline):
        """
        Set the deadline of the current job.
        """
        assert self.currentJob != None

        self.currentJob.deadline = deadline

    def startExec(self, ts, core, type):
        """
        Task starts to execute at time ts.
        """
        assert self.currentJob != None, "No current job! " + str(self)

        self.currentJob.startExec(ts, core, type)

    def stopExec(self, ts):
        """
        Task stops to execute at time ts.
        """
        assert self.currentJob != None
        self.currentJob.stopExec(ts)

    def finishJob(self):
        """
        Finish this job.
        """
        assert self.currentJob != None
        self.jobs.append(self.currentJob)
        self.currentJob = None

    def getMaxResponseTime(self):
        """
        Returns the maximum (observed) response time of all jobs of this task
        """
        maxRt = None
        for j in self.jobs:
            rt = (j.getFinishTime() - j.releaseTime)
            if maxRt == None or maxRt < rt:
                maxRt = rt
        return maxRt

if __name__ == "__main__":
    """
    Debugging.
    """
    print("TraceTask Test")

    task = TraceTask("TestTask", "#121212")
    task.newJob(0, 100)
    task.startExec(1, 1, ExecutionType.READ)
    task.stopExec(10)
    task.finishJob()
    task.newJob(100, 200)
    task.startExec(150, 2, ExecutionType.READ)
    task.stopExec(166)
    task.finishJob()

    print(task, " max RT = " + str(task.getMaxResponseTime()))
    for j in task.jobs:
        print("\t", j)
        for i in j.execIntervals:
            print("\t\t", i)