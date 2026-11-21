"""
Task-set model of the simulator: loading and validating taskset.json, plus the job state.

All times are integer microseconds. A larger priority value means a higher priority (as in FreeRTOS).
"""
from __future__ import annotations

from dataclasses import dataclass
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
class Precedence:
    """
    For k = 0, 1, 2, ...: job pred_first + k * pred_every of the predecessor task must complete
    before job succ_first + k * succ_every of the successor task may start.
    The successor job is still released at its nominal time; it only waits to start.
    """
    pred_task: int                          # Task ids
    pred_first: int
    pred_every: int
    succ_task: int
    succ_first: int
    succ_every: int

    def predecessor_of(self, succ_job: int) -> int | None:
        """The predecessor job id that successor job succ_job waits for, or None if it is not affected."""
        k, rest = divmod(succ_job - self.succ_first, self.succ_every)
        if succ_job < self.succ_first or rest:
            return None
        return self.pred_first + k * self.pred_every


@dataclass(frozen=True)
class TaskSet:
    cores: int
    scheduler: str
    tasks: tuple[Task, ...]
    precedence: tuple[Precedence, ...] = ()

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
    precedence = _parse_precedence(data.get('precedence', []), {task.name: task.id for task in tasks})
    return TaskSet(cores=cores, scheduler=scheduler, tasks=tuple(tasks), precedence=precedence)


def _parse_precedence(entries, ids):
    """Validate the precedence constraints and reject cycles between tasks."""
    if not isinstance(entries, list):
        raise TaskSetError("'precedence' must be a list")
    constraints = []
    for index, entry in enumerate(entries):
        where = f'precedence {index + 1}'
        if not isinstance(entry, dict):
            raise TaskSetError(f'{where}: must be a JSON object')
        sides = []
        for role in ('predecessor', 'successor'):
            side = entry.get(role)
            if not isinstance(side, dict):
                raise TaskSetError(f"{where}: '{role}' must be an object with 'task', 'first_job' and 'every'")
            name = side.get('task')
            if name not in ids:
                raise TaskSetError(f"{where}: {role} task {name!r} is not defined")
            sides.append((ids[name], _integer(side, 'first_job', f'{where} {role}', minimum=0, default=0),
                          _integer(side, 'every', f'{where} {role}', minimum=1, default=1)))
        if sides[0][0] == sides[1][0]:
            raise TaskSetError(f"{where}: predecessor and successor are the same task "
                               f"'{entry['predecessor']['task']}'")
        constraints.append(Precedence(*sides[0], *sides[1]))

    names = {ident: name for name, ident in ids.items()}
    cycle = _find_cycle({(c.pred_task, c.succ_task) for c in constraints})
    if cycle:
        raise TaskSetError('precedence constraints form a cycle: ' + ' -> '.join(names[i] for i in cycle))
    return tuple(constraints)


def _find_cycle(edges):
    """Return the task ids of one cycle in the directed graph, or None."""
    successors = {}
    for pred, succ in edges:
        successors.setdefault(pred, []).append(succ)
    state, path = {}, []                    # state: 1 = on the current path, 2 = done

    def visit(node):
        state[node] = 1
        path.append(node)
        for succ in sorted(successors.get(node, [])):
            if state.get(succ) == 1:
                return path[path.index(succ):] + [succ]
            if succ not in state:
                cycle = visit(succ)
                if cycle:
                    return cycle
        state[node] = 2
        path.pop()
        return None

    for node in sorted(successors):
        if node not in state:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


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
