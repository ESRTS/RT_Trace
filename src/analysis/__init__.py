"""
Analysis plugins: evaluate a trace after it was loaded or simulated.

    from analysis import Analysis, Result, Table

See README.md in this folder for how to write an analysis and where to put it.
"""
from .base import ANALYSIS_FOLDER, Analysis, AnalysisContext, Result, Table, ms
from .builtin import BUILTIN
from .registry import DEFAULT, discover, plugin_folders
from .report import run_analyses, run_analysis
from .recording import RecordingError, context_for_tasks, load_recording


def run_defaults(ctx):
    """Run the analyses that follow every trace load (the task statistics)."""
    analyses = {analysis.name: analysis for analysis in BUILTIN}
    return run_analyses([analyses[name] for name in DEFAULT], ctx)
