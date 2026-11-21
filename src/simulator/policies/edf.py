"""Preemptive earliest deadline first (EDF) on one or more cores, restricted by task affinity."""
from .base import SchedulingPolicy


class EarliestDeadlineFirst(SchedulingPolicy):
    """
    Jobs are served in this order:
      1. earlier absolute deadline first (a late job keeps its deadline, so it stays most urgent),
      2. among equal deadlines, a running job before a waiting one (equal deadlines never preempt),
      3. then FIFO by release time, then by the task order in the task set.
    'priority' is ignored. Cores are assigned by base.assign_ranked().
    """
    name = 'edf'

    def order(self, job):
        return (job.deadline, job.core is None, job.release, job.task.id, job.id)
