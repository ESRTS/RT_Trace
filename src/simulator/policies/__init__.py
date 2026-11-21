"""Registry of the scheduling policies, selected by the 'scheduler' field of taskset.json."""
from ..model import TaskSetError
from .base import SchedulingPolicy
from .edf import EarliestDeadlineFirst
from .fixed_priority import FixedPriority

POLICIES = {policy.name: policy for policy in (FixedPriority, EarliestDeadlineFirst)}


def get_policy(name) -> SchedulingPolicy:
    try:
        return POLICIES[name]()
    except KeyError:
        raise TaskSetError(f"unknown scheduler '{name}', available: {', '.join(sorted(POLICIES))}") from None
