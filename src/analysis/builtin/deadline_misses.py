from ..base import Analysis, Result, Table, ms


class DeadlineMisses(Analysis):
    name = 'deadline_misses'
    title = 'Deadline misses'
    description = 'Late jobs per task, with their lateness; the list of late jobs is saved as CSV'

    def run(self, ctx):
        summary, late, unknown = [], [], 0
        for task in ctx.task_rows():
            checked = misses = estimated = 0
            worst = None
            for job in ctx.completed_jobs(task):
                deadline, isEstimated = ctx.deadline(job)
                if deadline is None:
                    unknown += 1
                    continue
                checked += 1
                lateness = job.getFinishTime() - deadline
                if lateness > 0:
                    misses += 1
                    estimated += bool(isEstimated)
                    worst = lateness if worst is None else max(worst, lateness)
                    late.append([task.name, job.id, job.releaseTime, deadline, job.getFinishTime(), lateness,
                                 int(bool(isEstimated))])
            unfinished = 0          # Still running at the end of the trace, but already past the deadline
            for job in task.jobs:
                if job.incomplete and not job.uncertain:
                    deadline = ctx.deadline(job)[0]
                    unfinished += deadline is not None and deadline < ctx.trace_end
            summary.append([task.name, checked, misses, ms(worst), estimated, unfinished])

        total = sum(row[2] for row in summary)
        unfinished = sum(row[5] for row in summary)
        lines = []
        if unfinished:
            lines.append(f'{unfinished} unfinished job(s) passed their deadline before the trace ended.')
        if unknown:
            lines.append(f'{unknown} completed job(s) without a known deadline are not checked.')
        if any(row[4] for row in summary):
            lines.append('Estimated: misses whose deadline the parser estimated.')
        tables = [Table(['Task', 'Jobs', 'Misses', 'Worst lateness (ms)', 'Estimated', 'Unfinished late'],
                        summary, formats=[None, None, None, '.3f'])]
        if late:
            tables.append(Table(['Task', 'JobId', 'ReleaseTime_us', 'Deadline_us', 'FinishTime_us', 'Lateness_us',
                                 'DeadlineEstimated'], late, title='Late jobs (times in us)', csv='deadline_misses.csv',
                                max_rows=20))
        return Result(headline=f'{total} late job(s)', lines=lines, tables=tables)
