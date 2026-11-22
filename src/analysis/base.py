"""
The plugin interface: an Analysis reads an AnalysisContext and returns a Result.

Analyses only compute. They must not print, import GUI modules or modify the tasks: the same tasks are
drawn by the trace view while an analysis runs. The runner (report.py) prints the result and writes its
tables as CSV files, so an analysis works the same in the GUI and on the command line.
All times in the trace model are integer microseconds.
"""
from dataclasses import dataclass, field
import os

ANALYSIS_FOLDER = 'analysis'        # Sub-folder of the recording that receives the files of the analyses


class Analysis:
    """Base class of all analyses. Subclasses set name and title and implement run()."""
    name = None             # Unique identifier, used on the command line: 'deadline_misses'
    title = None            # Shown in the GUI dropdown and as the heading of the output
    description = ''        # One line, shown by --list

    def check(self, ctx):
        """None if the analysis can run on this trace, otherwise the reason why not (shown to the user)."""
        return None

    def run(self, ctx):
        """Analyse ctx and return a Result."""
        raise NotImplementedError


@dataclass
class Table:
    """
    A table of the result. The runner prints it aligned; with csv set, it is also written to
    <recording>/analysis/<csv>. Values keep their unit, formats only change how they are printed.
    """
    header: list
    rows: list
    title: str = None
    formats: list = None    # One format spec per column ('.3f', '.1%', ...), None or '' prints str(value)
    csv: str = None         # File name, e.g. 'deadline_misses.csv'
    max_rows: int = None    # Print only the first max_rows rows (the CSV file has all of them)


@dataclass
class Result:
    headline: str = ''                              # Printed next to the title
    lines: list = field(default_factory=list)       # Text printed below the title
    tables: list = field(default_factory=list)      # Table objects


class AnalysisContext:
    """
    Everything an analysis may use about one loaded trace.

    tasks       all rows of the trace (TraceTask), as in the trace view, including IRQ, idle and scheduler rows
    folder      the recording folder; files go to output_path()
    platform    'Simulator', 'Pico2 FreeRTOS' or 'RPI Linux' (the platform names of the GUI), None if unknown
    cores       number of cores
    taskset     the simulator TaskSet (from taskset.json in the recording folder), else None
    trace_end   end of the observed time in us (the simulation horizon, or the last activity in the trace)
    warnings    warnings of the parser
    """

    def __init__(self, tasks, folder, platform=None, cores=None, taskset=None, trace_end=None, warnings=()):
        self.tasks = list(tasks)
        self.folder = folder
        self.platform = platform
        self.taskset = taskset
        self.warnings = list(warnings)
        self.trace_end = trace_end if trace_end is not None else last_activity(self.tasks)
        if cores is None:
            cores = taskset.cores if taskset is not None else used_cores(self.tasks)
        self.cores = cores
        self.files = []                 # Files written through output_path()
        self._indices = {}

    def task_rows(self):
        """The rows that are tasks: without the IRQ, idle and scheduler rows of FreeRTOS traces."""
        return [task for task in self.tasks if getattr(task, 'kind', 'task') == 'task']

    def completed_jobs(self, task):
        """Jobs that completed in the trace with trustworthy boundaries, the jobs the statistics and CSVs use."""
        return [job for job in task.jobs
                if not job.incomplete and not job.uncertain and job.releaseTime is not None
                and job.getFinishTime() is not None and job.getStartTime() is not None]

    def deadline(self, job):
        """(absolute deadline, estimated) of a job, the deadline the trace view and the CSVs use; (None, None) if unknown."""
        from DataExport import jobDeadline
        task = job.task
        if task.id not in self._indices:
            self._indices[task.id] = {id(j): index for index, j in enumerate(task.jobs)}
        return jobDeadline(task, self._indices[task.id][id(job)])

    def output_path(self, filename):
        """Path for a file of the analysis in <recording>/analysis/. The runner reports the files to the user."""
        folder = os.path.join(self.folder, ANALYSIS_FOLDER)
        os.makedirs(folder, exist_ok=True)
        path = os.path.abspath(os.path.join(folder, filename))
        if path not in self.files:
            self.files.append(path)
        return path


def last_activity(tasks):
    """End of the last execution interval, or of the observed extent of an unfinished job."""
    end = 0
    for task in tasks:
        for job in task.jobs:
            if job.execIntervals:
                end = max(end, job.execIntervals[-1].stop)
            if job.observedUntil is not None:
                end = max(end, job.observedUntil)
    return end


def used_cores(tasks):
    """Number of cores, from the highest core that executed anything."""
    cores = [interval.core for task in tasks for job in task.jobs for interval in job.execIntervals
             if isinstance(interval.core, int)]
    return max(cores) + 1 if cores else 1


def ms(time_us):
    """Microseconds as float milliseconds, None stays None."""
    return None if time_us is None else time_us / 1000
