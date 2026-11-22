from ..base import Analysis, Result, Table


class Preemptions(Analysis):
    name = 'preemptions'
    title = 'Preemptions and migrations'
    description = 'How often the jobs of each task were interrupted and moved to another core'

    def run(self, ctx):
        rows = []
        for task in ctx.task_rows():
            jobs = ctx.completed_jobs(task)
            preemptions = [sum(1 for a, b in zip(job.execIntervals, job.execIntervals[1:]) if b.start > a.stop)
                           for job in jobs]     # Back-to-back intervals (e.g. other phases) are no interruption
            migrations = [sum(1 for a, b in zip(job.execIntervals, job.execIntervals[1:]) if a.core != b.core)
                          for job in jobs]
            rows.append([task.name, len(jobs), sum(preemptions), max(preemptions, default=0),
                         sum(migrations), max(migrations, default=0)])
        return Result(headline='(completed jobs only)',
                      lines=['A preemption is a pause in the execution of a job, by another task or an ISR, before it completes.'],
                      tables=[Table(['Task', 'Jobs', 'Preemptions', 'Max per job', 'Migrations', 'Max per job'],
                                    rows, csv='preemptions.csv')])
