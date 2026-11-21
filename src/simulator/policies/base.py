"""Interface between the simulation engine and a scheduling policy."""


class SchedulingPolicy:
    """
    A policy decides at every scheduling point which ready job runs on which core.
    The engine owns time, job state and the trace; a policy only returns decisions.

    Most policies only rank jobs: they define order() and use the default select().
    Policies that cannot be expressed as a ranking override select() instead.
    """
    name = ''

    def validate(self, taskset):
        """Raise TaskSetError if the task set lacks parameters this policy needs."""

    def on_release(self, job, now):
        """Called after a job is released, before the next select()."""

    def on_complete(self, job, now):
        """Called after a job completed, before the next select()."""

    def order(self, job):
        """Sort key of a job; smaller keys run first. Used by the default select()."""
        raise NotImplementedError

    def select(self, ready, cores, now):
        """
        ready: released, unfinished jobs that may run (their predecessors have completed);
        job.core is the core a job runs on now (or None).
        Return {core: job} for the cores that shall run a job; missing cores idle.
        A job may appear at most once and only on a core its task allows.
        """
        return assign_ranked(ready, cores, self.order)


def assign_ranked(ready, cores, order):
    """
    Assign jobs in the order given by the sort key. Each job in turn takes a free core that its
    affinity allows: its current core if possible, otherwise an idle core, otherwise the core whose
    running job ranks lowest (least disruption). Without affinity this is global scheduling;
    with one allowed core per task it is partitioned scheduling.
    """
    running = {job.core: job for job in ready if job.core is not None}
    assignment = {}
    for job in sorted(ready, key=order):
        free = [core for core in range(cores) if core not in assignment and job.task.allowed(core)]
        if not free:
            continue
        if job.core in free:
            core = job.core
        else:
            core = max(free, key=lambda c: (c not in running, order(running[c]) if c in running else ()))
        assignment[core] = job
        if len(assignment) == cores:
            break
    return assignment
