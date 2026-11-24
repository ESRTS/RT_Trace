"""
Time units of the trace model.

All times in the trace model (TraceTask, TraceJob, TraceInterval), the CSV export and the analyses are
integer nanoseconds. Parsers convert from the unit of their recording (FreeRTOS wire format and simulator
traces: microseconds; Linux trace.txt: microseconds with fraction; Nsight Systems: nanoseconds).
Use these constants instead of literal factors, e.g. 5 * MS or time / US.
"""
NS = 1
US = 1_000
MS = 1_000_000
S = 1_000_000_000


def to_ns(value, unit):
    """value in unit (US, MS, ...) as integer nanoseconds; None stays None."""
    return None if value is None else round(value * unit)


def ms(time_ns):
    """Nanoseconds as float milliseconds; None stays None."""
    return None if time_ns is None else time_ns / MS
