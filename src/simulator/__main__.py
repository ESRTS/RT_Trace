"""
Command-line entry point: simulate a task set and write the JSON-lines trace.

    python src/simulator taskset.json 0.2                  # from the repository root
    python src/simulator taskset.json 0.2 -o out/trace.jsonl
    python -m simulator taskset.json 0.2                   # with src on the Python path
"""
import argparse
import math
import os
import sys

if not __package__:                         # Run as a folder (python src/simulator): make the package importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    return 0


if __name__ == '__main__':
    sys.exit(main())
