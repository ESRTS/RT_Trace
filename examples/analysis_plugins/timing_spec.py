"""
Example analysis plugin with an input file: two analyses that compare a trace with a specification.

Both read timing_spec.json from the recording folder, each its own element of it (an example file is next
to this one; copy it into the recording folder and adapt it). The file is written by hand, so its times are
integer microseconds; the analyses convert them to the nanoseconds of the trace (US from timeunits):

    {"periods": {"Control": 5000, "Sensor": 8000},
     "budgets": {"Control": 1500, "Sensor": 2500}}

    python src/analysis <recording> period_check exec_budget --plugins examples/analysis_plugins

Without the file, or without the element, the runner skips the analysis and says which file it needs.
"""
from analysis import Analysis, Input, Result, Table, ms
from timeunits import US


def spec_rows(ctx, spec):
    """(task, specified value in ns) for every task of the spec found in the trace, and the names not found."""
    tasks = {task.name: task for task in ctx.task_rows()}
    found = [(tasks[name], value * US) for name, value in spec.items() if name in tasks]
    lines = [f"not in the trace: {', '.join(name for name in spec if name not in tasks)}"] \
        if len(found) < len(spec) else []
    return found, lines


class PeriodCheck(Analysis):
    name = 'period_check'
    title = 'Specified vs. observed periods'
    description = 'Time between consecutive releases of each task compared with its specified period'
    inputs = {'periods': Input('timing_spec.json', key='periods', description='period per task in us')}

    def run(self, ctx):
        found, lines = spec_rows(ctx, ctx.input('periods'))
        rows = []
        for task, period in found:
            releases = sorted(job.releaseTime for job in task.jobs if job.releaseTime is not None)
            gaps = [b - a for a, b in zip(releases, releases[1:])]
            if not gaps:
                rows.append([task.name, ms(period), len(releases), None, None, None])
                continue
            rows.append([task.name, ms(period), len(releases), ms(min(gaps)), ms(max(gaps)),
                         ms(max(abs(gap - period) for gap in gaps))])
        return Result(headline='(ms)', lines=lines, tables=[
            Table(['Task', 'Period', 'Releases', 'Min gap', 'Max gap', 'Max deviation'], rows,
                  formats=[None, '.3f', None, '.3f', '.3f', '.3f'], csv='period_check.csv')])


class ExecBudget(Analysis):
    name = 'exec_budget'
    title = 'Execution time budgets'
    description = 'Completed jobs that executed longer than the budget of their task'
    inputs = {'budgets': Input('timing_spec.json', key='budgets', description='execution time budget per task in us')}

    def run(self, ctx):
        found, lines = spec_rows(ctx, ctx.input('budgets'))
        rows = []
        for task, budget in found:
            times = [job.getExecutionTime() for job in ctx.completed_jobs(task)]
            over = [time for time in times if time > budget]
            rows.append([task.name, ms(budget), len(times), len(over), ms(max(times)) if times else None])
        overruns = sum(row[3] for row in rows)
        return Result(headline=f'{overruns} job(s) over budget (ms, completed jobs only)', lines=lines, tables=[
            Table(['Task', 'Budget', 'Jobs', 'Over budget', 'Max execution'], rows,
                  formats=[None, '.3f', None, None, '.3f'], csv='exec_budget.csv')])
