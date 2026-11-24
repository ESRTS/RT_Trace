"""
Orders of the trace rows for the view (and the PDF export). The parsers decide the recorded order; these
functions only rearrange a list of rows. Every order is stable: rows that compare equal keep their recorded order.
"""

# Row kinds of the parsers, in the order "By kind" shows them. Rows without a kind (Linux) are tasks.
KIND_ORDER = ['task', 'gpu', 'thread', 'idle', 'irq', 'scheduler', 'runtime', 'unknown']


def kind(row):
    return getattr(row, 'kind', 'task')


def by_kind(rows):
    rank = {name: index for index, name in enumerate(KIND_ORDER)}
    return sorted(rows, key=lambda row: rank.get(kind(row), len(KIND_ORDER)))


def by_priority(rows):
    """Highest priority first; rows without a priority (interrupts, GPU streams, threads) follow in recorded order."""
    return sorted(rows, key=lambda row: (row.priority is None, -(row.priority or 0)))


def by_name(rows):
    return sorted(rows, key=lambda row: row.name.casefold())


def gpu_owner(row, rows):
    """The row of `rows` that launched most of the GPU work of the stream `row` (ties: the earlier row), or None."""
    counts = {}
    for job in row.jobs:
        launcher = getattr(job, 'launchedBy', None)
        if launcher is not None:
            counts[id(launcher)] = counts.get(id(launcher), 0) + 1
    candidates = [candidate for candidate in rows if id(candidate) in counts]
    return max(candidates, key=lambda candidate: counts[id(candidate)], default=None)


def tasks_with_gpu_streams(rows):
    """Recorded order, with each GPU stream row directly below the task that launches (most of) its work."""
    rows = list(rows)
    owners = {id(row): gpu_owner(row, rows) for row in rows if kind(row) == 'gpu'}
    streams = {}
    for row in rows:
        owner = owners.get(id(row))
        if owner is not None:
            streams.setdefault(id(owner), []).append(row)
    ordered = []
    for row in rows:
        if owners.get(id(row)) is not None:
            continue                            # Placed below its owner
        ordered.append(row)
        ordered.extend(streams.get(id(row), []))
    return ordered


def has_gpu_rows(rows):
    return any(kind(row) == 'gpu' for row in rows)


# Menu label -> order function. The first entry is the order of the parser.
ORDERS = {
    'As recorded': list,
    'By priority': by_priority,
    'By name': by_name,
    'By kind': by_kind,
    'Tasks with GPU streams': tasks_with_gpu_streams,
}


def available_orders(rows):
    """Menu labels that make sense for these rows (the GPU grouping only for traces with GPU rows)."""
    return [label for label in ORDERS if label != 'Tasks with GPU streams' or has_gpu_rows(rows)]
