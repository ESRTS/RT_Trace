"""Stage 2: format-3 events -> TraceTask jobs and CPU/mutex intervals.

Job boundaries are syscall assumptions, configured on the host. Binary decoding
is exclusively in TraceEventsFreeRTOS. GUI imports are lazy for headless use.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
import fnmatch
import json
from pathlib import Path

import TraceTask as model
from TraceTask import TraceTask, TraceInconsistency, ExecutionType
from TraceEventsFreeRTOS import (EventType as E, Syscall, Recording,
                                decode_buffers, write_events)


_DEFAULT_BOUNDARIES = frozenset((Syscall.DELAY_UNTIL, Syscall.DELAY, Syscall.QUEUE_RECEIVE,
                                Syscall.SEMAPHORE_TAKE, Syscall.NOTIFY_TAKE,
                                Syscall.NOTIFY_WAIT, Syscall.EVENT_WAIT, Syscall.EVENT_SYNC))
_INTERNAL_CALLERS = ('vPortLockInternal*', 'vPortSyncInternal*', 'xStreamBuffer*',
                     'prvTimerTask', 'prvProcessTimer*')
_COLORS = ('#64ED9D', '#648FED', '#D4ED4C', '#ED7B64', '#8D64ED', '#E29578', '#006D77')


@dataclass
class JobPolicy:
    """Optional job_policy.json: task_boundaries, ignored_objects, ignored_callers.

    task_boundaries maps task names or numeric IDs to lists of Syscall names.
    caller_names maps addresses to symbols for tests/tools; elf_path can resolve
    actual callers without adding application annotations.
    """
    task_boundaries: dict = field(default_factory=dict)
    ignored_objects: set = field(default_factory=set)
    ignored_callers: tuple = _INTERNAL_CALLERS
    caller_names: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        unknown = set(data) - {'task_boundaries', 'ignored_objects', 'ignored_callers', 'caller_names'}
        if unknown:
            raise ValueError(f'Unknown job policy options: {sorted(unknown)}')
        boundaries = {str(task): frozenset(Syscall[name] for name in names)
                      for task, names in data.get('task_boundaries', {}).items()}
        number = lambda value: int(value, 0) if isinstance(value, str) else int(value)
        return cls(boundaries, {number(x) for x in data.get('ignored_objects', [])},
                   _INTERNAL_CALLERS + tuple(data.get('ignored_callers', [])),
                   {number(address): name for address, name in data.get('caller_names', {}).items()})

    def boundary(self, event, task, caller_name):
        data = event.data
        call = Syscall(data['call'])
        allowed = self.task_boundaries.get(str(task.id), self.task_boundaries.get(task.name, _DEFAULT_BOUNDARIES))
        if call not in allowed or data['object'] in self.ignored_objects:
            return False
        if any(fnmatch.fnmatchcase(caller_name, pattern) for pattern in self.ignored_callers):
            return False
        if call == Syscall.DELAY_UNTIL:
            return data['arg1'] > 0
        return data['timeout'] > 0


class _Symbols:
    def __init__(self, path=None, names=None):
        self.names, self.ranges, self.starts = names or {}, [], []
        if path is not None:
            from elftools.elf.elffile import ELFFile
            with Path(path).open('rb') as source:
                elf = ELFFile(source)
                table = elf.get_section_by_name('.symtab')
                if table is not None:
                    self.ranges = sorted((int(s['st_value']) & ~1, int(s['st_size']), s.name)
                                         for s in table.iter_symbols()
                                         if s['st_info']['type'] == 'STT_FUNC' and s['st_size'])
            self.starts = [address for address, _, _ in self.ranges]

    def name(self, address):
        if address in self.names:
            return self.names[address]
        pc = (address & ~1) - 1  # caller is the instruction AFTER the Thumb call
        index = bisect_right(self.starts, pc) - 1
        if index >= 0:
            start, size, name = self.ranges[index]
            if pc < start + size:
                return name
        return ''


@dataclass
class _Call:
    entry: object
    boundary: bool
    parent: object = None
    returned: object = None
    ready: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    wakes: list = field(default_factory=list)
    operation: object = None
    uncertain: bool = False

    @property
    def kind(self):
        return Syscall(self.entry.data['call'])

    @property
    def valid(self):
        return self.returned is not None or bool(self.blocks or self.wakes) or self.operation is not None


class _TickClock:
    def __init__(self, recording, origin):
        stream = next(s for s in recording.streams if s.session is not None)
        self.modulus, self.hz = 1 << stream.tick_bits, stream.tick_hz
        samples = [(e.timestamp - origin, (e.data['tick'] + 1) % self.modulus, False)
                   for e in recording.events if e.kind == E.TICK and not e.data['suspended']]
        samples += [(e.timestamp - origin, e.data['tick'], True)
                    for e in recording.events if e.kind == E.TICK_JUMP]
        if not samples:
            samples = [(e.timestamp - origin, e.data['tick'], True)
                       for e in recording.events if e.kind == E.READY]
        self.samples, self.exact = [], {}
        previous = None
        for timestamp, raw, estimated in sorted(samples):
            if previous is None:
                count = raw
            else:
                last_ts, last_count = previous
                expected = last_count + (timestamp - last_ts) * self.hz / 1000000
                count = raw + round((expected - raw) / self.modulus) * self.modulus
            self.samples.append((timestamp, count, estimated))
            previous = timestamp, count
            self.exact.setdefault(count, (timestamp, estimated))
        self.times = [sample[0] for sample in self.samples]

    def time_for(self, raw, reference, direction='nearest'):
        if not self.samples:
            return None, True
        i = bisect_left(self.times, reference)
        i = min(range(max(0, i - 1), min(len(self.samples), i + 1)),
                key=lambda n: abs(self.times[n] - reference))
        timestamp, count, estimated = self.samples[i]
        # Resolve wrap relative to the call time, not the nearest stored anchor:
        # a past deadline may still be AFTER an older, sparse anchor.
        reference_count = count + (reference - timestamp) * self.hz // 1000000
        delta = (raw - reference_count) % self.modulus
        if direction == 'past':
            if delta:
                delta -= self.modulus
        elif direction == 'nearest' and delta > self.modulus // 2:
            delta -= self.modulus
        target = reference_count + delta
        if target in self.exact:
            return self.exact[target]
        return round(timestamp + (target - count) * 1000000 / self.hz), True


@dataclass
class _Core:
    time: int | None = None
    running: int = 0
    scheduler: object = None
    irqs: list = field(default_factory=list)
    known: bool = True
    ended: bool = False


class _Converter:
    def __init__(self, recording, policy, strict, elf_path):
        self.recording, self.policy, self.strict = recording, policy, strict
        self.origin = recording.epoch if recording.epoch is not None else recording.events[0].timestamp
        self.end = max(e.timestamp for e in recording.events) - self.origin
        self.report = {'warnings': list(recording.warnings), 'eventsLost': sum(s.dropped for s in recording.streams),
                       'gaps': [], 'origin': self.origin, 'epochKnown': recording.epoch is not None,
                       'policy': 'syscall-default-v1'}
        if recording.epoch is None:
            self.report['warnings'].append('No traceTimeZero(): using the first decoded event as a provisional origin')
        self.symbols = _Symbols(elf_path, policy.caller_names)
        self.tasks, self.windows, self.window_starts = {}, {}, {}
        self.calls, self.synthetic, self.held, self.mutex_spans = {}, {}, {}, []
        self.mutex_letters = {}
        self.task_events = {}
        self.cores = {s.core: _Core() for s in recording.streams}
        self.clock = _TickClock(recording, self.origin)
        model.resetRepairs(strict)

    def issue(self, message, event=None, structural=False):
        if structural and self.strict:
            raise TraceInconsistency(message, None if event is None else self.ts(event),
                                     None if event is None else event.core)
        self.report['warnings'].append(message)

    def ts(self, event):
        return event.timestamp - self.origin

    def task(self, ident):
        if ident not in self.tasks:
            task = TraceTask(ident, f'Task_{ident}', None, _COLORS[(ident - 1) % len(_COLORS)])
            task.kind, task.traceFormat, task.releaseKnown = 'task', 3, False
            task.mutexAttemptsExplicit = True
            task.irqTimeIncluded = not next(s.irq_enabled for s in self.recording.streams if s.session is not None)
            self.tasks[ident] = task
        return self.tasks[ident]

    def mutex_letter(self, handle):
        if handle not in self.mutex_letters:
            number, letter = len(self.mutex_letters), ''
            while True:
                letter = chr(65 + number % 26) + letter
                number = number // 26 - 1
                if number < 0:
                    break
            self.mutex_letters[handle] = letter
        return self.mutex_letters[handle]

    def prepare(self):
        # Metadata can precede execution on another core at the same timestamp.
        for event in self.recording.events:
            if event.task_id:
                self.task(event.task_id)
                self.task_events.setdefault(event.task_id, []).append(event)
            if event.kind == E.TASK_CREATE:
                task = self.task(event.task_id)
                task.name = event.data['name'] or f'Task_{task.id}'
                task.priority, task.handle = event.data['priority'], event.data['handle']
            elif event.kind == E.IDLE_TASK:
                task = self.task(event.task_id)
                task.kind, task.idleCore = 'idle', event.data['idle_core']
            elif event.kind == E.OBJECT_CREATE and event.data['object_type'] in (1, 4):
                self.mutex_letter(event.data['object'])
        names = set()
        for task in self.tasks.values():
            name, number = task.name, 2
            while task.name in names:
                task.name = f'{name} #{number}'
                number += 1
            names.add(task.name)
        stacks = {}
        for event in self.recording.events:
            if event.kind == E.GAP:
                for stack in stacks.values():
                    for call in stack:
                        call.uncertain = True
                    stack.clear()
                continue
            if not event.task_id:
                continue
            stack = stacks.setdefault(event.task_id, [])
            if event.kind == E.SYSCALL_ENTER:
                selected = self.policy.boundary(event, self.task(event.task_id), self.symbols.name(event.data['caller']))
                selected = selected and not any(call.boundary for call in stack)
                call = _Call(event, selected, parent=stack[-1] if stack else None)
                self.calls.setdefault(event.task_id, []).append(call)
                stack.append(call)
            elif event.kind == E.SYSCALL_RETURN:
                def matches(call):
                    return event.data['call'] == call.kind or (call.kind == Syscall.MUTEX_TAKE and event.data['call'] == Syscall.SEMAPHORE_TAKE)
                if stack and matches(stack[-1]):
                    stack.pop().returned = event
                else:
                    self.issue(f'Task {event.task_id}: syscall return without matching entry', event, True)
            elif event.kind == E.READY:
                for call in stack:
                    call.ready.append(event)
            elif event.kind == E.BLOCK:
                for call in stack:
                    call.blocks.append(event)
            elif event.kind == E.WAKE_TARGET:
                for call in stack:
                    call.wakes.append(event)
            elif stack and event.kind in (E.QUEUE_RECEIVED, E.RECURSIVE_TAKEN, E.NOTIFY_TAKEN, E.NOTIFY_WAITED,
                                           E.EVENT_WAIT_END, E.EVENT_SYNC_END):
                call = stack[-1]
                expected = {E.QUEUE_RECEIVED: (Syscall.QUEUE_RECEIVE, Syscall.SEMAPHORE_TAKE, Syscall.MUTEX_TAKE),
                            E.NOTIFY_TAKEN: (Syscall.NOTIFY_TAKE,), E.NOTIFY_WAITED: (Syscall.NOTIFY_WAIT,),
                            E.RECURSIVE_TAKEN: (Syscall.MUTEX_RECURSIVE,),
                            E.EVENT_WAIT_END: (Syscall.EVENT_WAIT,), E.EVENT_SYNC_END: (Syscall.EVENT_SYNC,)}
                obj = event.data.get('object', event.data.get('index'))
                if call.kind in expected[event.kind] and obj == call.entry.data['object']:
                    # Keep the nested semaphore's earlier acquisition point.
                    if call.operation is None or event.kind != E.RECURSIVE_TAKEN:
                        call.operation = event
                    if (event.kind == E.QUEUE_RECEIVED and call.parent is not None
                            and call.parent.kind == Syscall.MUTEX_RECURSIVE
                            and obj == call.parent.entry.data['object']):
                        call.parent.operation = event
        if not self.symbols.ranges and not self.symbols.names:
            if any(call.entry.data['caller'] for calls in self.calls.values() for call in calls):
                self.issue('No caller symbols: SDK/internal wait exclusion requires an ELF or host policy overrides')
        for task in self.tasks.values():
            self.build_jobs(task)

    def release(self, call):
        entry, data = self.ts(call.entry), call.entry.data
        if call.kind in (Syscall.DELAY, Syscall.DELAY_UNTIL):
            if call.kind == Syscall.DELAY_UNTIL:
                raw = (data['arg0'] + data['arg1']) % self.clock.modulus
                direction = 'future' if call.wakes or call.blocks or (call.returned and call.returned.data['result']) else 'past'
            elif call.wakes:
                raw, direction = call.wakes[-1].data['tick'], 'future'
            else:
                return (self.ts(call.ready[-1]), True) if call.ready else (None, True)
            release, estimated = self.clock.time_for(raw, entry, direction)
            # An aborted delay can return before its scheduled wake tick.
            if call.ready and release is not None and self.ts(call.ready[-1]) < release:
                return self.ts(call.ready[-1]), True
            return release, estimated
        if call.blocks:
            if call.ready:
                return self.ts(call.ready[-1]), call.returned is None
            return None, True
        if call.operation is not None:
            return self.ts(call.operation), call.operation.kind in (E.EVENT_WAIT_END, E.EVENT_SYNC_END)
        return (self.ts(call.returned), True) if call.returned is not None else (None, True)

    def build_jobs(self, task):
        events = self.task_events.get(task.id, [])
        first_exec = next((e for e in events if e.kind == E.SWITCH_IN), None)
        ready = next((e for e in events if e.kind == E.READY), None)
        if ready is None and first_exec is None:
            return
        release_known = ready is not None and (first_exec is None or ready.timestamp <= first_exec.timestamp)
        release = self.ts(ready if release_known else first_exec)
        task.releaseKnown = release_known
        task.newJob(release, None)
        current, service_start = task.currentJob, release
        current.releaseKnown = release_known
        current.uncertain = not release_known
        windows = []
        deleted = next((self.ts(e) for e in events if e.kind == E.TASK_DELETE), None)
        for call in self.calls.get(task.id, []):
            if not call.boundary or task.kind == 'idle':
                continue
            if call.kind == Syscall.DELAY_UNTIL:
                task.delayUntil = True
                task.period = call.entry.data['arg1'] * 1000000 / self.clock.hz
            completion = self.ts(call.entry)
            if deleted is not None and completion > deleted:
                break
            if current is None:
                task.newJob(completion, None)
                current, service_start = task.currentJob, completion
                current.releaseKnown, current.uncertain = False, True
            current.boundary = call.kind.name
            current.completionTime = completion if call.valid else None
            current.observedUntil = completion
            current.uncertain |= not call.valid or call.uncertain
            current.incomplete = not call.valid or not current.releaseKnown
            windows.append((service_start, completion, current))
            task.finishJob()
            next_release, estimated = self.release(call)
            current.deadline, current.deadlineEstimated = next_release, estimated
            if next_release is not None and next_release <= self.end:
                task.newJob(next_release, None)
                current, service_start = task.currentJob, max(completion, next_release)
                current.releaseKnown = True
                current.releaseEstimated = estimated
                current.uncertain = estimated or call.uncertain
                current.trigger = ('timeout' if call.returned is not None and not call.returned.data['result']
                                   and call.kind not in (Syscall.DELAY, Syscall.DELAY_UNTIL) else call.kind.name)
            else:
                current = None
        if current is not None:
            stop = deleted if deleted is not None else self.end
            current.observedUntil = stop
            windows.append((service_start, stop, current))
            current.incomplete = True
            if deleted is not None:
                current.aborted = True
            task.finishJobIncomplete()
        self.windows[task.id] = windows
        self.window_starts[task.id] = [start for start, _, _ in windows]
        task.deadlinesExplicit = True

    def row(self, kind, core, irq=None):
        key = kind, core, irq
        if key not in self.synthetic:
            ident = -1 - len(self.synthetic)
            name = f'IRQ_{irq}_C{core}' if kind == 'irq' else f'{kind.capitalize()} Core {core}'
            row = TraceTask(ident, name, None, '#CBFFA8' if kind == 'irq' else '#3D3D3D')
            row.kind, row.traceFormat = kind, 3
            self.synthetic[key] = row
        return self.synthetic[key]

    def start_row(self, row, timestamp):
        row.newJob(timestamp, None)
        row.currentJob.releaseKnown = True
        return row.currentJob

    def finish_row(self, job, timestamp, incomplete=False):
        job.observedUntil = timestamp
        if not incomplete:
            job.completionTime = timestamp
        job.incomplete = incomplete
        if job.task.currentJob is job:
            if incomplete:
                job.task.finishJobIncomplete()
            else:
                job.task.finishJob(timestamp)

    @staticmethod
    def interval(job, start, stop, core):
        if stop <= start:
            return
        if job.execIntervals and job.execIntervals[-1].stop == start and job.execIntervals[-1].core == core:
            job.execIntervals[-1].stop = stop
        else:
            job.startExec(start, core, ExecutionType.EXECUTE)
            job.stopExec(stop)

    def runtime(self, kind, core, start, stop):
        if stop <= start:
            return
        row = self.row(kind, core)
        if row.jobs and row.jobs[-1].getFinishTime() == start:
            job = row.jobs[-1]
            job.completionTime = stop
        else:
            job = self.start_row(row, start)
            self.finish_row(job, stop)
        self.interval(job, start, stop, core)

    def user_interval(self, ident, start, stop, core):
        windows = self.windows.get(ident, [])
        index = max(0, bisect_right(self.window_starts.get(ident, []), start) - 1)
        cursor = start
        while index < len(windows):
            begin, end, job = windows[index]
            index += 1
            if begin >= stop:
                break
            if end <= cursor:
                continue
            self.runtime('runtime', core, cursor, min(begin, stop))
            left, right = max(cursor, begin), min(stop, end)
            self.interval(job, left, right, core)
            cursor = max(cursor, right)
            if cursor >= stop:
                break
        self.runtime('runtime', core, cursor, stop)

    def advance(self, core, timestamp):
        state = self.cores[core]
        if state.time is not None and timestamp > state.time:
            if not state.known:
                self.runtime('unknown', core, state.time, timestamp)
            elif state.irqs:
                self.interval(state.irqs[-1][1], state.time, timestamp, core)
            elif state.scheduler is not None:
                self.interval(state.scheduler, state.time, timestamp, core)
            elif state.running:
                self.user_interval(state.running, state.time, timestamp, core)
        state.time = timestamp

    def close_mutex(self, handle, timestamp, uncertain=False):
        held = self.held.pop(handle, None)
        if held is not None:
            ident, start, _ = held
            self.mutex_spans.append((ident, handle, start, max(start, timestamp), uncertain))

    def build_mutex_attempts(self):
        for ident, calls in self.calls.items():
            for call in calls:
                if call.kind not in (Syscall.MUTEX_TAKE, Syscall.MUTEX_RECURSIVE):
                    continue
                handle = call.entry.data['object']
                if (call.parent is not None and call.parent.kind == Syscall.MUTEX_RECURSIVE
                        and handle == call.parent.entry.data['object']):
                    continue  # One marker per public call, not implementation calls.
                granted = call.operation is not None
                success = bool(call.returned.data['result']) if call.returned is not None else (True if granted else None)
                if granted and success is False:
                    self.issue(f'Mutex 0x{handle:X}: conflicting acquisition and return result', call.returned, True)
                    success = None
                self.task(ident).mutexAttempt(
                    self.ts(call.entry), handle, self.mutex_letter(handle), success,
                    acquiredAt=self.ts(call.operation) if granted else None,
                    resultTime=self.ts(call.returned) if call.returned is not None else None,
                    recursive=call.kind == Syscall.MUTEX_RECURSIVE,
                    uncertain=call.uncertain or success is None)

    def consume(self, event):
        state, timestamp = self.cores[event.core], self.ts(event)
        kind, data = event.kind, event.data
        if kind == E.GAP:
            if self.strict:
                self.issue('Loss prevents exact reconstruction', event, True)
            start = data['start'] - self.origin
            for _, job in state.irqs:
                self.finish_row(job, start, True)
            state.irqs.clear()
            if state.scheduler:
                self.finish_row(state.scheduler, start, True)
                state.scheduler = None
            state.known = False
            self.report['gaps'].append(dict(data, core=event.core, start=start, end=timestamp))
            for task in self.tasks.values():
                for job in task.jobs:
                    if job.releaseTime <= timestamp and job.getFinishTime() >= start:
                        job.uncertain = True
            for handle in list(self.held):
                self.close_mutex(handle, start, True)
        self.advance(event.core, timestamp)
        if kind == E.SYNC:
            if state.running == 0 or not state.known:
                state.running = data['running_task']
        elif kind == E.SWITCH_OUT:
            if state.running and state.running != event.task_id:
                self.issue(f'Core {event.core}: switch-out does not match running task', event, True)
            state.running = 0
            if state.scheduler is None:
                state.scheduler = self.start_row(self.row('scheduler', event.core), timestamp)
        elif kind == E.SWITCH_IN:
            for other_core, other in self.cores.items():
                if other_core != event.core and other.running == event.task_id and other.known and not other.ended:
                    self.issue(f'Task {event.task_id} appears to run on two cores', event, True)
                    other.known = False
            if state.scheduler is not None:
                self.finish_row(state.scheduler, timestamp)
                state.scheduler = None
            state.running, state.known = event.task_id, True
        elif kind == E.IRQ_ENTER:
            if any(irq == data['irq'] for irq, _ in state.irqs):
                self.issue(f'Core {event.core}: IRQ {data["irq"]} entered twice', event, True)
                for _, job in state.irqs:
                    self.finish_row(job, timestamp, True)
                state.irqs.clear()
            job = self.start_row(self.row('irq', event.core, data['irq']), timestamp)
            state.irqs.append((data['irq'], job))
        elif kind in (E.IRQ_EXIT, E.IRQ_EXIT_SCHEDULE):
            if state.irqs:
                self.finish_row(state.irqs.pop()[1], timestamp)
            else:
                self.issue(f'Core {event.core}: IRQ exit without entry', event, state.known)
            # EXIT_SCHEDULE requests PendSV; it is not the context-switch start.
        elif kind == E.END:
            state.ended = True
        elif kind == E.PRIORITY:
            self.task(event.task_id).priority = data['priority']
        elif kind == E.AFFINITY:
            self.task(event.task_id).affinity = data['mask']
        elif kind == E.QUEUE_RECEIVED and data['object_type'] in (1, 4):
            if event.task_id:
                handle = data['object']
                if handle in self.held:
                    self.issue(f'Mutex 0x{handle:X}: acquired while already held', event, True)
                    self.close_mutex(handle, timestamp, True)
                self.held[handle] = event.task_id, timestamp, 1
        elif kind == E.QUEUE_SENT and data['object_type'] in (1, 4):
            self.close_mutex(data['object'], timestamp)
        elif kind == E.RECURSIVE_TAKEN:
            handle = data['object']
            if handle in self.held:
                ident, start, _ = self.held[handle]
                self.held[handle] = ident, start, data['depth']
            elif event.task_id:
                self.held[handle] = event.task_id, timestamp, data['depth']
        elif kind == E.RECURSIVE_GIVEN:
            handle = data['object']
            # Depth 1 is still owned here: the kernel next calls queue send.
            # Its successful QUEUE_SENT is the final release point.
            if data['depth'] > 1 and handle in self.held:
                ident, start, _ = self.held[handle]
                self.held[handle] = ident, start, data['depth'] - 1
        elif kind == E.OBJECT_DELETE:
            self.close_mutex(data['object'], timestamp, True)

    def finish(self):
        self.prepare()
        for event in self.recording.events:
            self.consume(event)
        for core, state in self.cores.items():
            timestamp = state.time
            if timestamp is None:
                continue
            for _, job in state.irqs:
                self.finish_row(job, timestamp, True)
            if state.scheduler is not None:
                self.finish_row(state.scheduler, timestamp, True)
        for handle in list(self.held):
            self.close_mutex(handle, self.end, True)
        self.build_mutex_attempts()
        for ident, handle, start, stop, uncertain in self.mutex_spans:
            letter = self.mutex_letter(handle)
            for begin, end, job in self.windows.get(ident, []):
                left, right = max(start, begin), min(stop, end)
                if right <= left:
                    continue
                job.mutexTake(left, handle, letter)
                job.mutexGive(right, handle)
                job.mutexAccess[-1].uncertain = uncertain
                job.mutexAccess[-1].continued = left != start
                job.mutexAccess[-1].released = right == stop and not uncertain
        tasks = list(self.tasks.values()) + list(self.synthetic.values())
        tasks = [task for task in tasks if task.jobs]
        return tasks


lastReport = {}


def events_to_tasks(recording: Recording, policy=None, strict=False, elf_path=None):
    """Convert stage-1 output, preserving raw CPU coverage separately from jobs."""
    global lastReport
    converter = _Converter(recording, policy or JobPolicy(), strict, elf_path)
    lastReport = converter.report
    return converter.finish()


def parser(buffers, event_file_path=None, *, strict=False, policy=None, elf_path=None):
    """Decode format-3 buffers and convert their events to TraceTask objects."""
    recording = decode_buffers(buffers, strict)
    if event_file_path is not None:
        write_events(recording, event_file_path)
    return events_to_tasks(recording, policy, strict, elf_path)


def loadRecording(gui, numCores):
    import HelperFunctions
    import DataExport
    folder = Path(gui.viewingFolder)
    config = HelperFunctions.readConfig()
    buffers = [(folder / f'raw_buffer{core}.txt').read_bytes() for core in range(numCores)]
    policy_path = folder / 'job_policy.json'
    policy = JobPolicy.from_dict(json.loads(policy_path.read_text())) if policy_path.exists() else JobPolicy()
    elf = next((p for p in (folder / 'firmware.elf', folder / 'trace.elf') if p.exists()), None)
    tasks = parser(buffers, folder / 'events.txt',
                   strict=config.getboolean('general', 'strict', fallback=False),
                   policy=policy, elf_path=elf)
    for warning in lastReport.get('warnings', []):
        HelperFunctions.printState('Warning', info=warning)
    DataExport.exportData(gui, tasks, overwrite=True)
    return tasks


def parser_thread(gui, numCores):
    import HelperFunctions
    try:
        return loadRecording(gui, numCores)
    except Exception as error:
        HelperFunctions.reportError('Parsing failed', error, gui.viewingFolder, 'parse_error.log',
                                    'Check the recording and job_policy.json; details are in parse_error.log.')
        return None


def parseTraceFiles(gui, numCores):
    import HelperFunctions
    HelperFunctions.startWorker(gui, lambda g: parser_thread(g, numCores), gui.loadingFinished)
