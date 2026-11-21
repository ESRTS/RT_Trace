"""Preemptive fixed-priority scheduling on one or more cores, restricted by task affinity."""
from ..model import TaskSetError
from .base import SchedulingPolicy


class FixedPriority(SchedulingPolicy):
    """
    Jobs are served in this order:
      1. higher priority value first,
      2. among equal priorities, a running job before a waiting one (equal priorities never preempt),
      3. then FIFO by release time, then by the task order in the task set.
    Cores are assigned by base.assign_ranked().
    """
    name = 'fp'

    def validate(self, taskset):
        missing = [task.name for task in taskset.tasks if task.priority is None]
        if missing:
            raise TaskSetError(f"scheduler 'fp' needs a 'priority' for every task, missing for: {', '.join(missing)}")

    def order(self, job):
        return (-job.task.priority, job.core is None, job.release, job.task.id, job.id)
