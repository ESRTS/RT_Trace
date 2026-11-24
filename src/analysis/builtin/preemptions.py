from ..base import Analysis, Result, Table


class Preemptions(Analysis):
    name = 'preemptions'
    title = 'Preemptions and migrations'
    description = 'How often the jobs of each task were interrupted and moved to another core'

    def run(self, ctx):
        rows = []
        for task in ctx.task_rows():
            jobs = ctx.completed_jobs(task)
            preemptions = [sum(1 for a, b in zip(job.execIntervals, job.execIntervals[1:]) if is_preemption(job, a, b))
                           for job in jobs]
            migrations = [sum(1 for a, b in zip(job.execIntervals, job.execIntervals[1:]) if a.core != b.core)
                          for job in jobs]
            rows.append([task.name, len(jobs), sum(preemptions), max(preemptions, default=0),
                         sum(migrations), max(migrations, default=0)])
        return Result(headline='(completed jobs only)',
                      lines=['A preemption is a pause in the execution of a job, by another task or an ISR, before it completes. '
                             'Pauses while the job waits for the GPU (Jetson) are no preemption.'],
                      tables=[Table(['Task', 'Jobs', 'Preemptions', 'Max per job', 'Migrations', 'Max per job'],
                                    rows, csv='preemptions.csv')])


def is_preemption(job, before, after):
    """
    True if the pause between two execution intervals of a job is a preemption. Back-to-back intervals
    (e.g. other phases) are no interruption, nor is a pause in which the job waits for the GPU (job.gpuWaits).
    """
    if after.start <= before.stop:
        return False
    return not any(start < after.start and before.stop < stop for start, stop in getattr(job, 'gpuWaits', ()))
