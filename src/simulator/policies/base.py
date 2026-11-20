"""Interface between the simulation engine and a scheduling policy."""


class SchedulingPolicy:
    """
    A policy decides at every scheduling point which ready job runs on which core.
    The engine owns time, job state and the trace; a policy only returns decisions.
    """
    name = ''

    def validate(self, taskset):
        """Raise TaskSetError if the task set lacks parameters this policy needs."""

    def on_release(self, job, now):
        """Called after a job is released, before the next select()."""

    def on_complete(self, job, now):
        """Called after a job completed, before the next select()."""

    def select(self, ready, cores, now):
        """
        ready: all released, unfinished jobs; job.core is the core a job runs on now (or None).
        Return {core: job} for the cores that shall run a job; missing cores idle.
        A job may appear at most once and only on a core its task allows.
        """
        raise NotImplementedError
