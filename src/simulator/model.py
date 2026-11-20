"""
Task-set model of the simulator: loading and validating taskset.json, plus the job state.

All times are integer microseconds. A larger priority value means a higher priority (as in FreeRTOS).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

DEFAULT_SCHEDULER = 'fp'


class TaskSetError(ValueError):
    """The task set is malformed; the message names the offending entry."""


@dataclass(frozen=True)
class Task:
    id: int                                 # 1-based, in the order of the JSON file (also the FIFO tie-breaker)
    name: str
    period: int
    wcet: int
    deadline: int                           # Relative deadline
    priority: int | None = None
    phase: int = 0                          # Release time of the first job
    bcet: int | None = None                 # Accepted and validated; jobs currently always run for wcet
    affinity: tuple[int, ...] | None = None # Allowed cores, None means all cores

    def allowed(self, core: int) -> bool:
        return self.affinity is None or core in self.affinity


@dataclass(frozen=True)
class TaskSet:
    cores: int
    scheduler: str
    tasks: tuple[Task, ...]

    def utilization(self) -> float:
        return sum(task.wcet / task.period for task in self.tasks)


@dataclass(eq=False)
class Job:
    task: Task
    id: int                                 # 0-based per task, in release order
    release: int
    deadline: int                           # Absolute deadline
    remaining: int                          # Execution time still needed
    core: int | None = None                 # Core the job currently runs on, None if not running

    def __repr__(self):
        return f'{self.task.name}-{self.id}'


def load_taskset(path) -> TaskSet:
    """Read and validate a taskset.json file."""
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as error:
        raise TaskSetError(f'{Path(path).name}: invalid JSON ({error})') from None
    return parse_taskset(data)


def parse_taskset(data) -> TaskSet:
    """Validate the decoded JSON of a task set and build the model."""
    if not isinstance(data, dict):
        raise TaskSetError('the task set must be a JSON object')
    cores = _integer(data, 'cores', 'task set', minimum=1)
    scheduler = data.get('scheduler', DEFAULT_SCHEDULER)
    if not isinstance(scheduler, str) or not scheduler:
        raise TaskSetError("'scheduler' must be a non-empty string")
    entries = data.get('tasks')
    if not isinstance(entries, list) or not entries:
        raise TaskSetError("'tasks' must be a non-empty list")

    tasks, names = [], set()
    for index, entry in enumerate(entries):
        where = f'task {index + 1}'
        if not isinstance(entry, dict):
            raise TaskSetError(f'{where}: must be a JSON object')
        name = entry.get('name')
        if not isinstance(name, str) or not name.strip():
            raise TaskSetError(f"{where}: 'name' must be a non-empty string")
        where = f"task '{name}'"
        if name in names:
            raise TaskSetError(f'{where}: duplicate name')
        names.add(name)

        period = _integer(entry, 'period', where, minimum=1)
        wcet = _integer(entry, 'wcet', where, minimum=1)
        deadline = _integer(entry, 'deadline', where, minimum=1, default=period)
        phase = _integer(entry, 'phase', where, minimum=0, default=0)
        bcet = _integer(entry, 'bcet', where, minimum=1, default=None)
        if bcet is not None and bcet > wcet:
            raise TaskSetError(f"{where}: 'bcet' ({bcet}) is larger than 'wcet' ({wcet})")
        priority = _integer(entry, 'priority', where, default=None)

        affinity = entry.get('affinity')
        if affinity is not None:
            if (not isinstance(affinity, list) or not affinity
                    or not all(isinstance(c, int) and not isinstance(c, bool) for c in affinity)):
                raise TaskSetError(f"{where}: 'affinity' must be a non-empty list of core numbers or null")
            if len(set(affinity)) != len(affinity):
                raise TaskSetError(f"{where}: 'affinity' lists a core twice")
            invalid = [c for c in affinity if not 0 <= c < cores]
            if invalid:
                raise TaskSetError(f"{where}: 'affinity' core {invalid[0]} does not exist (cores 0..{cores - 1})")
            affinity = tuple(sorted(affinity))

        tasks.append(Task(id=index + 1, name=name, period=period, wcet=wcet, deadline=deadline,
                          priority=priority, phase=phase, bcet=bcet, affinity=affinity))
    return TaskSet(cores=cores, scheduler=scheduler, tasks=tuple(tasks))


_MISSING = object()


def _integer(data, key, where, minimum=None, default=_MISSING):
    value = data.get(key, _MISSING)
    if value is _MISSING or value is None:
        if default is _MISSING:
            raise TaskSetError(f"{where}: '{key}' is missing")
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise TaskSetError(f"{where}: '{key}' must be an integer (times are in microseconds)")
    if minimum is not None and value < minimum:
        raise TaskSetError(f"{where}: '{key}' must be at least {minimum}")
    return value
