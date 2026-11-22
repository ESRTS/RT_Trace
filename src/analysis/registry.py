"""
Finds the analyses: the built-in ones and plugins, which are .py files in folders outside the repository.

A plugin file defines one or more subclasses of analysis.Analysis. Files are imported again on every
discover(), so edits of a plugin take effect without restarting RT-Trace. Files starting with '_' are skipped.
"""
import importlib.util
import inspect
import os
import re

from .base import Analysis
from .builtin import BUILTIN

DEFAULT = ('statistics',)       # Run after every trace load


def discover(plugin_folders=()):
    """Returns ({name: Analysis subclass} with the built-in ones first, [error messages of the plugins])."""
    analyses = {analysis.name: analysis for analysis in BUILTIN}
    errors = []
    for folder in plugin_folders:
        if not os.path.isdir(folder):
            errors.append(f'plugin folder not found: {folder}')
            continue
        for filename in sorted(os.listdir(folder)):
            if not filename.endswith('.py') or filename.startswith('_'):
                continue
            path = os.path.join(folder, filename)
            try:
                found = load_plugin(path)
            except Exception as error:
                errors.append(f'{path}: {type(error).__name__}: {error}')
                continue
            if not found:
                errors.append(f'{path}: defines no subclass of analysis.Analysis with a name')
            for analysis in found:
                if analysis.name in analyses:
                    errors.append(f"{path}: the name '{analysis.name}' is already used, the analysis is ignored")
                else:
                    analyses[analysis.name] = analysis
    return analyses, errors


def load_plugin(path):
    """Import a plugin file and return the Analysis subclasses defined in it."""
    stem = re.sub(r'\W', '_', os.path.splitext(os.path.basename(path))[0])
    moduleName = f'rt_trace_plugin_{stem}_{abs(hash(os.path.abspath(path)))}'
    spec = importlib.util.spec_from_file_location(moduleName, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [obj for obj in vars(module).values()
            if inspect.isclass(obj) and issubclass(obj, Analysis) and obj is not Analysis
            and obj.__module__ == moduleName and obj.name]


def plugin_folders(config, base):
    """
    The folders of [analysis] plugin_folders in config (a ConfigParser). Several folders are separated by
    ';' or new lines; relative ones are resolved against base (the RT_Trace folder) and '~' is expanded.
    """
    value = config.get('analysis', 'plugin_folders', fallback='')
    folders = [part.strip() for part in re.split(r'[;\n]', value) if part.strip()]
    return [os.path.abspath(os.path.join(base, os.path.expanduser(folder))) for folder in folders]
