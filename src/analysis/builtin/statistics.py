from ..base import Analysis, Result, Table, ms


class TaskStatistics(Analysis):
    """The statistics that are printed after a trace is loaded."""
    name = 'statistics'
    title = 'Task statistics'
    description = 'Maximum response and execution time and the utilization of each row'

    def run(self, ctx):
        end = last_execution(ctx.tasks)
        rows = [[task.name, ms(task.getMaxResponseTime()), ms(task.getMaxExecutionTime()),
                 task.getUtilization(end)] for task in ctx.tasks]
        return Result(headline='(times in ms, the maximum over all jobs)', tables=[
            Table(['Task', 'Response', 'Execution', 'Utilization'], rows, formats=[None, '.3f', '.3f', '.2%'])])


def last_execution(tasks):
    """End of the last execution interval, the interval the utilization refers to."""
    return max((job.execIntervals[-1].stop for task in tasks for job in task.jobs if job.execIntervals), default=0)
