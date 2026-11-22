"""
Command-line entry point: run analyses on a recording folder.

    python src/analysis data/Simulator/SimEDFLarge_2026-11-22_15-40-09            # the default analyses
    python src/analysis <recording> deadline_misses response_times                # selected analyses
    python src/analysis <recording> --all                                         # all analyses
    python src/analysis --list                                                    # list the analyses
    python src/analysis <recording> my_analysis --plugins ~/rt-trace-plugins      # with a plugin folder

<recording> is a recording folder (simulator, Pico 2 FreeRTOS or Linux) or a simulator trace file.
Plugin folders come from [analysis] plugin_folders in Resources/config.ini and config.local.ini, plus --plugins.
"""
import argparse
import configparser
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
if os.path.dirname(os.path.abspath(__file__)) in map(os.path.abspath, sys.path):   # Run as a folder: python src/analysis
    sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.dirname(os.path.abspath(__file__))]
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import analysis


def read_config():
    config = configparser.ConfigParser()
    resources = os.path.join(ROOT, 'Resources')
    config.read([os.path.join(resources, 'config.ini'), os.path.join(resources, 'config.local.ini')])
    return config


def available_analyses(plugins=(), config=None):
    """{name: analysis class} with the plugin folders of the config and plugins; plugin errors go to stderr."""
    config = config or read_config()
    folders = analysis.plugin_folders(config, ROOT) + [os.path.abspath(os.path.expanduser(p)) for p in plugins]
    available, errors = analysis.discover(folders)
    for error in errors:
        print(f'warning: plugin: {error}', file=sys.stderr)
    return available


def analyze(recording, names=(), plugins=(), run_all=False, taskset=None):
    """
    Run analyses on a recording folder or simulator trace file and print the results. names empty: the
    default analyses. taskset replaces taskset.json of the folder. Returns the exit code: 0, 1 (an analysis
    failed or the recording cannot be read) or 2 (unknown analysis).
    """
    config = read_config()
    available = available_analyses(plugins, config)
    names = list(available) if run_all else (list(names) or list(analysis.DEFAULT))
    unknown = [name for name in names if name not in available]
    if unknown:
        print(f"error: unknown analysis: {', '.join(unknown)} (see python src/analysis --list)", file=sys.stderr)
        return 2
    try:
        ctx = analysis.load_recording(recording,
                                      strict=config.getboolean('general', 'strict', fallback=False),
                                      user_events=config.getboolean('RPI_Linux', 'user_events', fallback=True))
    except analysis.RecordingError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    if taskset is not None:
        ctx.taskset = taskset
    print(f'{ctx.platform}: {len(ctx.tasks)} rows, {ctx.cores} core(s), {ctx.trace_end / 1000:g} ms -> {ctx.folder}')
    for warning in ctx.warnings:
        print(f'warning: {warning}', file=sys.stderr)
    return run_named(ctx, names, available=available)


def run_named(ctx, names=(), plugins=(), run_all=False, available=None):
    """
    Run analyses by name on an AnalysisContext (names empty: the default analyses) and print the results.
    Returns the exit code: 0, 1 (an analysis failed) or 2 (unknown analysis).
    """
    available = available or available_analyses(plugins)
    names = list(available) if run_all else (list(names) or list(analysis.DEFAULT))
    unknown = [name for name in names if name not in available]
    if unknown:
        print(f"error: unknown analysis: {', '.join(unknown)} (see python src/analysis --list)", file=sys.stderr)
        return 2
    ok = True
    for name in names:
        print()
        ok = analysis.run_analysis(available[name], ctx) is not None and ok
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog='analysis', description='Run RT-Trace analyses on a recording.')
    parser.add_argument('recording', nargs='?', help='recording folder, or a simulator trace file (.jsonl)')
    parser.add_argument('analyses', nargs='*', metavar='analysis',
                        help=f"analyses to run (default: {', '.join(analysis.DEFAULT)})")
    parser.add_argument('--all', action='store_true', help='run all analyses, including the plugins')
    parser.add_argument('--list', action='store_true', help='list the available analyses and exit')
    parser.add_argument('--plugins', action='append', default=[], metavar='DIR',
                        help='additional folder with analysis plugins (repeatable)')
    args = parser.parse_args(argv)

    if args.list:
        available = available_analyses(args.plugins)
        width = max(len(name) for name in available)
        for name, analysis_class in available.items():
            print(f'{name:<{width}}  {analysis_class.title}: {analysis_class.description}')
        return 0
    if args.recording is None:
        parser.error('the recording folder is required (or use --list)')
    return analyze(args.recording, args.analyses, args.plugins, args.all)


if __name__ == '__main__':
    sys.exit(main())
