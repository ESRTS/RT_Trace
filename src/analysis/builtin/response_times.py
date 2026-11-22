from ..base import Analysis, Result, Table, ms


class ResponseTimes(Analysis):
    name = 'response_times'
    title = 'Response times'
    description = 'Minimum, average and maximum response time, response jitter and start delay per task'

    def run(self, ctx):
        rows = []
        for task in ctx.task_rows():
            jobs = ctx.completed_jobs(task)
            if not jobs:
                rows.append([task.name, 0] + [None] * 5)
                continue
            responses = [job.getFinishTime() - job.releaseTime for job in jobs]
            delays = [job.getStartTime() - job.releaseTime for job in jobs]
            rows.append([task.name, len(jobs), ms(min(responses)), ms(sum(responses) / len(responses)),
                         ms(max(responses)), ms(max(responses) - min(responses)), ms(max(delays))])
        return Result(headline='(times in ms, completed jobs only)', tables=[
            Table(['Task', 'Jobs', 'Min', 'Avg', 'Max', 'Jitter', 'Max start delay'], rows,
                  formats=[None, None] + ['.3f'] * 5, csv='response_times.csv')])
