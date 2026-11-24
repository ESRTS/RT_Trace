"""
The plugin interface: an Analysis reads an AnalysisContext and returns a Result.

Analyses only compute. They must not print, import GUI modules or modify the tasks: the same tasks are
drawn by the trace view while an analysis runs. The runner (report.py) prints the result and writes its
tables as CSV files, so an analysis works the same in the GUI and on the command line.
All times in the trace model are integer nanoseconds (timeunits.py).
"""
import copy
import csv
from dataclasses import dataclass, field
import json
import os

from timeunits import MS

ANALYSIS_FOLDER = 'analysis'        # Sub-folder of the recording that receives the files of the analyses


@dataclass(frozen=True)
class Input:
    """
    A file in the recording folder that an analysis reads, declared in Analysis.inputs.

    file        path relative to the recording folder, e.g. 'timing_spec.json'
    key         for JSON files: the top-level element the analysis uses. Several analyses can share one file
                and each take their own element of it.
    required    a missing required file (or key) skips the analysis with a message saying what is missing
    description shown by --list

    ctx.input(name) returns the content: JSON as parsed (only the element under key, if given), CSV as a
    list of dicts (one per row, keys from the header line), any other file as text.
    """
    file: str
    key: str = None
    required: bool = True
    description: str = ''

    def label(self):
        return self.file + (f" (element '{self.key}')" if self.key else '')


class InputError(Exception):
    """An input file exists but cannot be read; the message names the file and, if known, the line."""


class Analysis:
    """Base class of all analyses. Subclasses set name and title and implement run()."""
    name = None             # Unique identifier, used on the command line: 'deadline_misses'
    title = None            # Shown in the GUI dropdown and as the heading of the output
    description = ''        # One line, shown by --list
    inputs = {}             # {name: Input}: files of the recording folder that the analysis reads

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
    folder      the recording folder; input files are read from it (input()), files go to output_path()
    platform    'Simulator', 'Pico2 FreeRTOS' or 'RPI Linux' (the platform names of the GUI), None if unknown
    cores       number of cores
    taskset     the simulator TaskSet (from taskset.json in the recording folder, times converted to ns), else None
    trace_end   end of the observed time in ns (the simulation horizon, or the last activity in the trace)
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
        self._inputs = {}               # Inputs declared by the analysis that runs now (set by the runner)
        self._contents = {}             # Parsed input files by path, shared by the analyses of this context

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

    def input_path(self, name):
        """Absolute path of the declared input name in the recording folder (the file may not exist)."""
        return os.path.abspath(os.path.join(self.folder, self._declared(name).file))

    def input(self, name):
        """
        The content of the declared input name (see Input), or None if an optional input is missing.
        Every call returns a fresh copy, so an analysis can modify it without affecting others.
        """
        return copy.deepcopy(self._lookup(name))

    def missing_inputs(self):
        """
        The required inputs of the running analysis that are missing, as text ('timing_spec.json');
        raises InputError if an input file cannot be read.
        """
        return [declared.label() for name, declared in self._inputs.items()
                if self._lookup(name) is None and declared.required]

    def _declared(self, name):
        if name not in self._inputs:
            raise KeyError(f"'{name}' is not an input of this analysis (declared: {', '.join(self._inputs) or 'none'})")
        return self._inputs[name]

    def _lookup(self, name):
        """The content of an input (shared, not copied), None if its file or key is missing."""
        declared = self._declared(name)
        path = self.input_path(name)
        content = self._read(path)
        if content is None or declared.key is None:
            return content
        if not isinstance(content, dict):
            raise InputError(f"{path}: expected a JSON object with the element '{declared.key}'")
        return content.get(declared.key)

    def _read(self, path):
        """Parsed content of an input file, None if it does not exist. Each file is parsed once per context."""
        if path not in self._contents:
            self._contents[path] = read_input_file(path) if os.path.isfile(path) else None
        return self._contents[path]

    def output_path(self, filename):
        """Path for a file of the analysis in <recording>/analysis/. The runner reports the files to the user."""
        folder = os.path.join(self.folder, ANALYSIS_FOLDER)
        os.makedirs(folder, exist_ok=True)
        path = os.path.abspath(os.path.join(folder, filename))
        if path not in self.files:
            self.files.append(path)
        return path


def read_input_file(path):
    """JSON (with a top-level object), CSV (list of dicts) or text; InputError names the file and line."""
    name = os.path.basename(path)
    try:
        with open(path, newline='', encoding='utf-8-sig') as file:
            if name.lower().endswith('.json'):
                try:
                    return json.load(file)
                except json.JSONDecodeError as error:
                    raise InputError(f'{path}: line {error.lineno}, column {error.colno}: {error.msg}') from None
            if name.lower().endswith('.csv'):
                reader = csv.DictReader(file)
                try:
                    return [dict(row) for row in reader]
                except csv.Error as error:
                    raise InputError(f'{path}: line {reader.line_num}: {error}') from None
            return file.read()
    except (OSError, UnicodeDecodeError) as error:
        raise InputError(f'{path}: {error}') from None


def input_problem(analysis_class):
    """None if the inputs declaration of analysis_class is valid, otherwise what is wrong with it."""
    inputs = analysis_class.inputs
    if not isinstance(inputs, dict):
        return 'inputs must be a dict {name: Input}'
    for name, declared in inputs.items():
        if not isinstance(declared, Input):
            return f"input '{name}' is not an analysis.Input"
        path = os.path.normpath(declared.file)
        if not declared.file or os.path.isabs(declared.file) or path.startswith(os.pardir):
            return f"input '{name}': {declared.file!r} must be a path inside the recording folder"
        if declared.key is not None and not declared.file.lower().endswith('.json'):
            return f"input '{name}': key is only possible for JSON files"
    return None


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


def ms(time_ns):
    """Nanoseconds as float milliseconds, None stays None."""
    return None if time_ns is None else time_ns / MS
