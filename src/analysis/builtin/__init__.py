"""The analyses that come with RT-Trace."""
from .statistics import TaskStatistics
from .deadline_misses import DeadlineMisses
from .response_times import ResponseTimes
from .preemptions import Preemptions

BUILTIN = (TaskStatistics, DeadlineMisses, ResponseTimes, Preemptions)
