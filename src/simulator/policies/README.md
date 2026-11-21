# Scheduling policies

A policy decides which ready job runs on which core. The engine handles time,
releases, execution, migrations and the trace. A policy only returns decisions.

## Add a policy

Existing policies: `fp` ([fixed_priority.py](fixed_priority.py)) and `edf` ([edf.py](edf.py)).

1. Create `policies/<name>.py` with a subclass of `SchedulingPolicy` ([base.py](base.py)).
   If the policy only ranks jobs, define `order()`. The default `select()` then assigns
   cores in that order, honouring affinity and avoiding needless migrations. For example,
   rate monotonic:

   ```python
   from .base import SchedulingPolicy

   class RateMonotonic(SchedulingPolicy):
       name = 'rm'                           # Value of "scheduler" in taskset.json

       def order(self, job):                 # Smaller keys run first
           return (job.task.period, job.core is None, job.release, job.task.id, job.id)
   ```

   `job.core is None` ranks a running job before a waiting one with the same key, so ties
   never preempt. Override `select()` only if the policy can't be expressed as a ranking.

2. Register it in [\_\_init\_\_.py](__init__.py):

   ```python
   POLICIES = {policy.name: policy for policy in (FixedPriority, EarliestDeadlineFirst, RateMonotonic)}
   ```

3. Select it in a task set with `"scheduler": "rm"`.

## Contract

- `select(ready, cores, now)` is called at every release and completion.
  - `ready` holds the released, unfinished jobs that may run. Jobs still waiting for a
    predecessor (precedence constraints) are left out by the engine.
  - `job.core` is the core a job runs on now, or `None` if it is waiting.
  - Return `{core: job}`. Cores that are left out stay idle.
  - Each job may appear at most once, and only on a core its task allows (`job.task.allowed(core)`).
  - The engine rejects invalid assignments.
- To keep a running job in place, return it on its current core. Moving it to another core counts as a migration.
- `validate(taskset)` (optional) raises `TaskSetError` if the task set lacks a parameter the policy needs, such as `priority`.
- `on_release(job, now)` and `on_complete(job, now)` (optional) are hooks for policies that keep their own state.
- Useful job fields: `task`, `id`, `release`, `deadline` (absolute) and `remaining`.
  Useful task fields: `priority`, `period`, `wcet`, `phase` and `affinity`.

## Test

Add cases with hand-computed schedules to [tests/test_simulator.py](../../../tests/test_simulator.py). Use its `run()` and `intervals()` helpers.
