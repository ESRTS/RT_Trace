"""
Runs analyses and presents their results: printed (the GUI redirects stdout into its output box) and
tables with a csv name written into <recording>/analysis/.
"""
import csv
import sys
import traceback

from .base import InputError, Result


def print_state(state, info=''):
    """Same format as HelperFunctions.printState, which the GUI output box highlights."""
    print('=> ' + state.upper(), end='')        # Separate writes, so the output box highlights only the state
    print(' ' + info)


def cell(value, spec):
    if value is None:
        return '-'
    if spec:
        try:
            return format(value, spec)
        except (TypeError, ValueError):
            pass
    return str(value)


def format_table(table):
    """The table as aligned text lines: the first column left aligned, the others right aligned."""
    specs = list(table.formats or []) + [None] * len(table.header)
    rows = table.rows if table.max_rows is None else table.rows[:table.max_rows]
    text = [[str(h) for h in table.header]] + [[cell(v, specs[i]) for i, v in enumerate(row)] for row in rows]
    widths = [max(len(row[i]) for row in text) for i in range(len(table.header))]

    def line(row):
        return '  ' + '  '.join(value.ljust(widths[i]) if i == 0 else value.rjust(widths[i])
                                for i, value in enumerate(row))

    lines = []
    if table.title:
        lines.append(f'  {table.title}')
    lines.append(line(text[0]))
    lines.append('  ' + '-' * (sum(widths) + 2 * (len(widths) - 1)))
    lines.extend(line(row) for row in text[1:])
    hidden = len(table.rows) - len(rows)
    if hidden > 0:
        lines.append(f'  ... {hidden} more row(s)' + (f' in {table.csv}' if table.csv else ''))
    return lines


def write_csv(path, table):
    with open(path, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(table.header)
        writer.writerows([['' if value is None else value for value in row] for row in table.rows])


def run_analysis(analysis_class, ctx):
    """
    Run one analysis on ctx and print its result. Errors of the analysis are reported, never raised,
    so a broken plugin cannot stop the trace from loading. Returns the Result, or None if the analysis failed.
    Missing required inputs skip the analysis like check() does; an unreadable input file is a failure.
    """
    title = analysis_class.title or analysis_class.name
    ctx.files = []
    ctx._inputs = dict(analysis_class.inputs or {})
    try:
        missing = ctx.missing_inputs()
        if missing:
            reason = f"needs {', '.join(missing)} in {ctx.folder}"
        else:
            analysis = analysis_class()
            reason = analysis.check(ctx)
        if reason:
            print_state(f'{title} skipped', info=str(reason))
            return Result(headline=str(reason))     # Not applicable is no failure
        result = analysis.run(ctx)
        if not isinstance(result, Result):
            raise TypeError(f'run() returned {type(result).__name__}, expected analysis.Result')
        for table in result.tables:
            if table.csv:
                write_csv(ctx.output_path(table.csv), table)
    except InputError as error:                     # A bad input file is no bug of the analysis: no traceback
        print_state(f'ERROR analysis {analysis_class.name} input', info=str(error))
        return None
    except Exception as error:
        print_state(f'ERROR analysis {analysis_class.name} failed', info=f'{type(error).__name__}: {error}')
        print(traceback.format_exc(), file=sys.stderr, end='')
        return None

    print_state(title, info=result.headline)
    for line in result.lines:
        print(f'  {line}')
    for table in result.tables:
        print()
        for line in format_table(table):
            print(line)
    for path in ctx.files:
        print_state('Saved', info=path)
    return result


def run_analyses(analysis_classes, ctx):
    """Run several analyses one after the other. True if none of them failed."""
    results = [run_analysis(analysis_class, ctx) for analysis_class in analysis_classes]
    return all(result is not None for result in results)
