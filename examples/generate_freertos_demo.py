"""Generate a deterministic format-3 demo; --install makes it selectable in RT-Trace."""
from pathlib import Path
import argparse
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from TraceEventsFreeRTOS import EventType as E, Syscall as S, read_recording
from TraceParserFreeRTOS import events_to_tasks

ORIGIN = 1000000
END = 42000
MUTEX_A, MUTEX_B = 0x20002000, 0x20002100
IDLE0, IDLE1, CONTROL, WORKER = 1, 2, 3, 4


class Core:
    def __init__(self, number):
        self.number, self.events = number, []

    def add(self, time, kind, task=0, *words):
        self.events.append((time, kind, task, struct.pack('<' + 'I' * len(words), *words)))

    def create(self, time, task, name, priority):
        name = name.encode('utf-8')
        self.events.append((time, E.TASK_CREATE, task,
                            struct.pack('<III', 0x20000100 + task * 256, priority, len(name))
                            + name + bytes(-len(name) % 4)))

    def enter(self, time, task, call, timeout=0, obj=0, a=0, b=0):
        self.add(time, E.SYSCALL_ENTER, task, call, obj, timeout, a, b, 0)

    def returned(self, time, task, call, result=1):
        self.add(time, E.SYSCALL_RETURN, task, call, result)

    def switch(self, time, old, new):
        self.add(time, E.SWITCH_OUT, old)
        self.add(time + 10, E.SWITCH_IN, new)

    def irq(self, start, stop, number=42, schedule=False):
        self.add(start, E.IRQ_ENTER, 0, number)
        self.add(stop, E.IRQ_EXIT_SCHEDULE if schedule else E.IRQ_EXIT)

    def encode(self):
        output = bytearray()
        previous, running, since_sync = None, 0, 0
        for sequence, (time, kind, task, payload) in enumerate(sorted(self.events, key=lambda e: e[0])):
            timestamp = ORIGIN + time
            if kind == E.SWITCH_IN:
                running = task
            if (previous is None or timestamp - previous > 65535 or since_sync >= 256
                    or kind in (E.TIME_ZERO, E.END)):
                epoch = ORIGIN if time >= 0 else (1 << 64) - 1
                flags = 3 | self.number << 8 | 2 << 16 | 1 << 23 | 32 << 24
                header = [0x33545452, flags, timestamp, 0, sequence, 0,
                          epoch & 0xffffffff, epoch >> 32, ORIGIN - 10000, 0, running, 1000]
                output += struct.pack('<12I', *header)
                previous, since_sync = timestamp, 0
            word = (timestamp - previous) << 16 | min(task, 255) << 8 | kind
            output += struct.pack('<I', word)
            if task >= 255:
                output += struct.pack('<I', task)
            output += payload
            previous, since_sync = timestamp, since_sync + 1
            if kind == E.SWITCH_OUT:
                running = 0
        return output


def build():
    c0, c1 = Core(0), Core(1)
    for offset, (task, name, priority) in enumerate(((IDLE0, 'IDLE0', 0), (IDLE1, 'IDLE1', 0),
                                                   (CONTROL, 'Control', 3), (WORKER, 'Worker', 2))):
        c0.create(-1200 + offset * 10, task, name, priority)
    c0.add(-900, E.READY, IDLE0, 0)
    c0.add(-890, E.READY, IDLE1, 0)
    c0.add(-800, E.IDLE_TASK, IDLE0, 0)
    c0.add(-790, E.IDLE_TASK, IDLE1, 1)
    c0.add(-700, E.OBJECT_CREATE, 0, MUTEX_A, 1, 0)
    c0.add(-690, E.OBJECT_CREATE, 0, MUTEX_B, 4, 0)
    c0.add(-500, E.SWITCH_IN, IDLE0)
    c1.add(-500, E.SWITCH_IN, IDLE1)
    c0.add(0, E.TIME_ZERO, IDLE0, ORIGIN, 0)
    c0.add(0, E.READY, CONTROL, 0)
    c0.add(0, E.READY, WORKER, 0)
    c0.switch(10, IDLE0, CONTROL)
    c1.switch(30, IDLE1, WORKER)

    # Tick timestamps are exactly on the application's millisecond grid.
    for tick in range(1, END // 1000):
        time = tick * 1000
        c0.add(time - 4, E.IRQ_ENTER, 0, 15)
        c0.add(time, E.TICK, 0, tick - 1, 0)
        c0.add(time + 8, E.IRQ_EXIT_SCHEDULE if tick in (10, 20, 40) else E.IRQ_EXIT)

    # Control: releases 0/10/20/30/40 ms; job at 20 ms completes at 33.5 ms.
    for finish, previous, next_tick in ((3100, 0, 10), (13500, 10, 20),
                                        (33500, 20, 30), (36100, 30, 40)):
        c0.enter(finish, CONTROL, S.DELAY_UNTIL, a=previous, b=10)
        if finish < next_tick * 1000:
            c0.add(finish + 1, E.WAKE_TARGET, CONTROL, next_tick)
            c0.switch(finish + 10, CONTROL, IDLE0)
            c0.add(next_tick * 1000 + 2, E.READY, CONTROL, next_tick)
            c0.switch(next_tick * 1000 + 10, IDLE0, CONTROL)
            c0.returned(next_tick * 1000 + 24, CONTROL, S.DELAY_UNTIL)
        else:
            c0.returned(finish + 8, CONTROL, S.DELAY_UNTIL, 0)

    for take, give in ((500, 1100), (10500, 13100), (20500, 23500)):
        c0.enter(take, CONTROL, S.MUTEX_TAKE, timeout=10, obj=MUTEX_A, a=1)
        c0.add(take + 10, E.QUEUE_RECEIVED, CONTROL, MUTEX_A, 1)
        c0.returned(take + 15, CONTROL, S.SEMAPHORE_TAKE)
        c0.add(give, E.QUEUE_SENT, CONTROL, MUTEX_A, 1)

    # Worker waits for notifications; one notification value is coalesced (3).
    for core, wait, wake, value in ((c1, 200, 5000, 1), (c1, 7000, 12000, 1),
                                    (c1, 15100, 22000, 3), (c1, 25100, 32000, 1),
                                    (c0, 39050, 41000, 1)):
        core.enter(wait, WORKER, S.NOTIFY_TAKE, timeout=0xffffffff, a=1)
        core.add(wait + 2, E.BLOCK, WORKER, S.NOTIFY_TAKE, 0)
        core.switch(wait + 10, WORKER, IDLE1 if core is c1 else IDLE0)
        c0.add(wake + 2, E.READY, WORKER, wake // 1000)
        c1.irq(wake + 4, wake + 10, schedule=True)
        c1.switch(wake + 12, IDLE1, WORKER)
        c1.add(wake + 22, E.NOTIFY_TAKEN, WORKER, 0, value)
        c1.returned(wake + 24, WORKER, S.NOTIFY_TAKE, value)

    # Worker blocks on A while Control holds it; this remains within one job.
    c1.enter(12500, WORKER, S.MUTEX_TAKE, timeout=10, obj=MUTEX_A, a=1)
    c1.add(12502, E.BLOCK, WORKER, S.QUEUE_RECEIVE, MUTEX_A)
    c1.switch(12510, WORKER, IDLE1)
    c0.add(13101, E.READY, WORKER, 13)
    c1.irq(13104, 13110, schedule=True)
    c1.switch(13112, IDLE1, WORKER)
    c1.add(13122, E.QUEUE_RECEIVED, WORKER, MUTEX_A, 1)
    c1.returned(13124, WORKER, S.SEMAPHORE_TAKE)
    c1.add(14100, E.QUEUE_SENT, WORKER, MUTEX_A, 1)

    # A zero-time take fails while Control holds A (20.51..23.5 ms).
    c1.enter(22300, WORKER, S.MUTEX_TAKE, timeout=0, obj=MUTEX_A, a=1)
    c1.returned(22308, WORKER, S.SEMAPHORE_TAKE, 0)

    # Recursive mutex B: nested implementation calls and two-level ownership.
    c1.enter(22500, WORKER, S.MUTEX_RECURSIVE, timeout=10, obj=MUTEX_B)
    c1.enter(22501, WORKER, S.MUTEX_TAKE, timeout=10, obj=MUTEX_B, a=4)
    c1.add(22502, E.QUEUE_RECEIVED, WORKER, MUTEX_B, 4)
    c1.returned(22503, WORKER, S.SEMAPHORE_TAKE)
    c1.add(22504, E.RECURSIVE_TAKEN, WORKER, MUTEX_B, 1)
    c1.returned(22505, WORKER, S.MUTEX_RECURSIVE)
    c1.enter(22600, WORKER, S.MUTEX_RECURSIVE, timeout=10, obj=MUTEX_B)
    c1.add(22601, E.RECURSIVE_TAKEN, WORKER, MUTEX_B, 2)
    c1.returned(22602, WORKER, S.MUTEX_RECURSIVE)
    c1.add(23010, E.RECURSIVE_GIVEN, WORKER, MUTEX_B, 2)
    c1.add(23100, E.RECURSIVE_GIVEN, WORKER, MUTEX_B, 1)
    c1.add(23101, E.QUEUE_SENT, WORKER, MUTEX_B, 4)

    # Nested interrupts on core 1; Worker later migrates to core 0 mid-job.
    c1.irq(18000, 18012, 42)
    c1.irq(18003, 18006, 45)
    c1.irq(36996, 37008, schedule=True)
    c1.switch(37010, WORKER, IDLE1)
    c0.switch(37010, IDLE0, WORKER)
    c0.add(END, E.END)
    c1.add(END, E.END)
    return c0.encode(), c1.encode()


def validate(folder):
    recording = read_recording(folder, num_cores=2, strict=True)
    tasks = events_to_tasks(recording, strict=True)
    control = next(task for task in tasks if task.name == 'Control')
    worker = next(task for task in tasks if task.name == 'Worker')
    misses = [job for job in control.jobs if job.deadline is not None
              and job.completionTime is not None and job.completionTime > job.deadline]
    assert len(misses) == 1 and misses[0].deadline == 30000 and misses[0].completionTime == 33500
    assert {interval.core for job in worker.jobs for interval in job.execIntervals} == {0, 1}
    assert all(stream.ended and not stream.dropped for stream in recording.streams)
    coverage = sum(max(0, i.stop - max(0, i.start)) for task in tasks
                   for job in task.jobs for i in job.execIntervals)
    assert coverage == 2 * END, coverage
    assert any(task.kind == 'irq' and task.name == 'IRQ_45_C1' for task in tasks)
    failed = [attempt for attempt in worker.mutexAttempts if attempt.success is False]
    assert len(failed) == 1 and failed[0].start == 22300
    blocked = next(attempt for attempt in worker.mutexAttempts if attempt.start == 12500)
    assert blocked.success and blocked.acquiredAt == 13122
    print(f'Validated {len(recording.events)} events, {len(tasks)} rows; one deadline miss, one failed mutex take, no loss.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='copy into the GUI recording folder')
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent / 'freertos_smp'
    folder.mkdir(parents=True, exist_ok=True)
    for core, raw in enumerate(build()):
        (folder / f'raw_buffer{core}.txt').write_bytes(raw)
    validate(folder)
    print(folder)
    if args.install:
        destination = ROOT / 'data/Pico2_FreeRTOS_RTT/Example_format3'
        destination.mkdir(parents=True, exist_ok=True)
        for source in folder.iterdir():
            if source.is_file():
                shutil.copy2(source, destination / source.name)
        print(f'GUI recording installed: {destination}')


if __name__ == '__main__':
    main()
