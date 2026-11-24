"""
Example analysis plugin: how busy each core was, in total and over time.

Use it by adding this folder to config.local.ini,

    [analysis]
    plugin_folders = examples/analysis_plugins

or on the command line: python src/analysis <recording> core_load --plugins examples/analysis_plugins
"""
import csv

from analysis import Analysis, Result, Table, ms

WINDOWS = 20        # Number of time windows of the load over time


class CoreLoad(Analysis):
    name = 'core_load'
    title = 'Core load'
    description = 'Busy time of each core (tasks and ISRs), in total and in time windows'

    def check(self, ctx):
        if ctx.trace_end <= 0:
            return 'the trace has no execution'
        return None

    def run(self, ctx):
        window = ctx.trace_end / WINDOWS
        tasks = [0] * ctx.cores
        irqs = [0] * ctx.cores
        load = [[0] * WINDOWS for _ in range(ctx.cores)]      # Busy time per core and window

        for row in ctx.tasks:
            kind = getattr(row, 'kind', 'task')
            if kind not in ('task', 'irq'):     # Idle and scheduler rows are no load
                continue
            for job in row.jobs:
                for interval in job.execIntervals:
                    if not isinstance(interval.core, int) or interval.core >= ctx.cores:
                        continue
                    start, stop = max(0, interval.start), min(interval.stop, ctx.trace_end)
                    if stop <= start:
                        continue
                    (irqs if kind == 'irq' else tasks)[interval.core] += stop - start
                    for index in range(int(start // window), min(WINDOWS, int(stop // window) + 1)):
                        overlap = min(stop, (index + 1) * window) - max(start, index * window)
                        load[interval.core][index] += max(0, overlap)

        # A file of its own: output_path() puts it into <recording>/analysis/ and the runner reports it
        with open(ctx.output_path('core_load_windows.csv'), 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['WindowStart_ns', 'WindowEnd_ns'] + [f'Core{core}' for core in range(ctx.cores)])
            for index in range(WINDOWS):
                writer.writerow([round(index * window), round((index + 1) * window)] +
                                [round(load[core][index] / window, 4) for core in range(ctx.cores)])

        rows = [[core, ms(tasks[core]), ms(irqs[core]), (tasks[core] + irqs[core]) / ctx.trace_end,
                 max(load[core]) / window] for core in range(ctx.cores)]
        return Result(headline=f'(over {ms(ctx.trace_end):g} ms, {WINDOWS} windows)', tables=[
            Table(['Core', 'Tasks (ms)', 'ISRs (ms)', 'Load', 'Peak window'], rows,
                  formats=[None, '.3f', '.3f', '.1%', '.1%'], csv='core_load.csv')])
