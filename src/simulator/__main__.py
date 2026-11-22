"""
Command-line entry point: simulate a task set and write the JSON-lines trace.

    python src/simulator taskset.json 0.2                  # from the repository root
    python src/simulator taskset.json 0.2 -o out/trace.jsonl
    python src/simulator taskset.json 0.2 --csv            # also one CSV per task next to the trace
    python src/simulator taskset.json 0.2 --analyze        # also the default analyses (see src/analysis)
    python src/simulator taskset.json 0.2 --analyze deadline_misses response_times
    python -m simulator taskset.json 0.2                   # with src on the Python path
"""
import argparse
import math
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:                     # Run as a folder (python src/simulator): make src importable
    sys.path.insert(0, SRC)

from simulator import TaskSetError, get_policy, load_taskset, simulate
from simulator import trace_format as fmt


def seconds(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not a number') from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('the duration must be a positive number of seconds')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='simulator', description='Simulate a task set and write an RT-Trace simulator trace.')
    parser.add_argument('taskset', help='task set file (taskset.json)')
    parser.add_argument('duration', type=seconds, help='simulated time in seconds, e.g. 0.2')
    parser.add_argument('-o', '--output', default=fmt.TRACE_FILE,
                        help=f'trace file to write (default: {fmt.TRACE_FILE} in the current directory)')
    parser.add_argument('--csv', action='store_true',
                        help='also write one CSV file per task (as the GUI does) into the folder of the trace')
    parser.add_argument('--analyze', nargs='*', metavar='ANALYSIS',
                        help='then run analyses on the trace (default: the task statistics; see python src/analysis --list); '
                             'files go to the folder analysis/ next to the trace')
    args = parser.parse_args(argv)

    horizon = max(1, round(args.duration * 1_000_000))
    try:
        taskset = load_taskset(args.taskset)
        get_policy(taskset.scheduler).validate(taskset)
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        simulate(taskset, args.output, horizon)
    except (TaskSetError, OSError) as error:
        print(f'error: {args.taskset}: {error}', file=sys.stderr)
        return 1
    print(f"{len(taskset.tasks)} tasks, {taskset.cores} core(s), scheduler '{taskset.scheduler}', "
          f"{args.duration:g} s simulated -> {args.output}")
    if args.csv and export_csv(args.output):
        return 1
    if args.analyze is not None:
        from analysis.__main__ import analyze
        print()
        return analyze(args.output, args.analyze, taskset=taskset)
    return 0


def export_csv(trace_path):
    """Write the per-task CSV files of a trace into its folder, with the same code as the GUI."""
    import DataExport
    from TraceParserSim import SimTraceError, read_trace
    try:
        _, tasks, _ = read_trace(trace_path)
        written = DataExport.exportCsvFiles(os.path.dirname(os.path.abspath(trace_path)), tasks, overwrite=True)
    except (SimTraceError, OSError) as error:
        print(f'error: CSV export failed: {error}', file=sys.stderr)
        return 1
    print(f'{len(written)} CSV file(s) -> {os.path.dirname(os.path.abspath(trace_path))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
