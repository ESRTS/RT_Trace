"""
Scheduling simulator for RT-Trace. It reads a task set (taskset.json) and writes a trace in the
simulator trace format (trace_format.py), which TraceParserSim converts for the visualization.
The package has no GUI dependencies.
"""
from .engine import Simulator
from .model import TaskSet, TaskSetError, load_taskset, parse_taskset
from .policies import POLICIES, get_policy
from .trace_writer import TraceWriter


def simulate(taskset, out_path, horizon):
    """Simulate taskset (a TaskSet or a path to taskset.json) for [0, horizon] us and write the trace."""
    if not isinstance(taskset, TaskSet):
        taskset = load_taskset(taskset)
    with open(out_path, 'w') as stream:
        Simulator(taskset, get_policy(taskset.scheduler), TraceWriter(stream)).run(horizon)
    return taskset
