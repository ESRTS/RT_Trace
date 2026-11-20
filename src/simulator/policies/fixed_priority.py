"""Preemptive fixed-priority scheduling on one or more cores, restricted by task affinity."""
from ..model import TaskSetError
from .base import SchedulingPolicy


class FixedPriority(SchedulingPolicy):
    """
    Jobs are served in this order:
      1. higher priority value first,
      2. among equal priorities, a running job before a waiting one (equal priorities never preempt),
      3. then FIFO by release time, then by the task order in the task set.
    Each job in turn takes a free core that its affinity allows: its current core if possible,
    otherwise an idle core, otherwise the core running the lowest-priority job.
    Global scheduling (no affinity) and partitioned scheduling (one core each) are special cases.
    """
    name = 'fp'

    def validate(self, taskset):
        missing = [task.name for task in taskset.tasks if task.priority is None]
        if missing:
            raise TaskSetError(f"scheduler 'fp' needs a 'priority' for every task, missing for: {', '.join(missing)}")

    @staticmethod
    def order(job):
        return (-job.task.priority, job.core is None, job.release, job.task.id, job.id)

    def select(self, ready, cores, now):
        running = {job.core: job for job in ready if job.core is not None}
        assignment = {}
        for job in sorted(ready, key=self.order):
            free = [core for core in range(cores) if core not in assignment and job.task.allowed(core)]
            if not free:
                continue
            if job.core in free:
                core = job.core
            else:
                # Prefer an idle core, then the core whose current job ranks lowest (least disruption).
                core = max(free, key=lambda c: (c not in running,
                                                self.order(running[c]) if c in running else ()))
            assignment[core] = job
            if len(assignment) == cores:
                break
        return assignment
