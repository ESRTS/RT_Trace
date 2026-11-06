"""Synthetic RTT bytes exercise both parser stages without GUI dependencies."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from TraceEventsFreeRTOS import (EventType as E, Syscall as S, DecodeError, decode_buffers,
                                decode_stream, read_recording, write_events)
import TraceParserFreeRTOS as parser_module
from TraceParserFreeRTOS import JobPolicy, events_to_tasks
from TraceTask import TraceInconsistency


class Wire:
    def __init__(self, core=0, cores=1, epoch=1000000, bits=32, hz=1000, irq=True):
        self.core, self.cores, self.epoch = core, cores, epoch
        self.bits, self.hz, self.irq = bits, hz, irq
        self.data = bytearray()
        self.time, self.sequence, self.drops, self.pending = None, 0, 0, False

    def sync(self, timestamp, running=0, missing=0):
        self.sequence += missing
        self.drops += missing
        epoch = (1 << 64) - 1 if self.epoch is None else self.epoch
        flags = 3 | self.core << 8 | self.cores << 16 | int(self.irq) << 23 | self.bits << 24
        words = [0x33545452, flags, timestamp & 0xffffffff, timestamp >> 32,
                 self.sequence & 0xffffffff, self.drops & 0xffffffff,
                 epoch & 0xffffffff, epoch >> 32, 1234, 0, running, self.hz]
        self.data += struct.pack('<12I', *words)
        self.time, self.pending = timestamp, True
        return self

    def add(self, kind, timestamp, task=0, *payload):
        timestamp += 1000000
        if self.time is None or timestamp - self.time > 65535:
            self.sync(timestamp, running=task if kind == E.SWITCH_IN else 0)
        delta = timestamp - self.time
        header = delta << 16 | (task if task < 255 else 255) << 8 | int(kind)
        words = [header] + ([task] if task >= 255 else []) + list(payload)
        self.data += struct.pack('<' + 'I' * len(words), *words)
        self.time, self.pending = timestamp, False
        self.sequence = (self.sequence + 1) & 0xffffffff
        return self

    def create(self, timestamp, task, name='worker'):
        encoded = name.encode('utf-8')
        self.add(E.TASK_CREATE, timestamp, task, 0x20000000 + task * 256, 3, len(encoded))
        self.data += encoded + b'\0' * (-len(encoded) % 4)
        return self

    def enter(self, timestamp, task, call, timeout=1, obj=0x20001000, a=0, b=0, caller=0):
        return self.add(E.SYSCALL_ENTER, timestamp, task, call, obj, timeout, a, b, caller)

    def returned(self, timestamp, task, call, result=1):
        return self.add(E.SYSCALL_RETURN, timestamp, task, call, result)

    def boot(self, task=7):
        return self.create(0, task).add(E.READY, 0, task, 0).add(E.SWITCH_IN, 0, task)

    def end(self, timestamp):
        return self.add(E.END, timestamp)


def recording(*wires, strict=True):
    return decode_buffers([wire.data for wire in wires], strict)


def task_named(tasks, name='worker'):
    return next(task for task in tasks if task.name == name)


def intervals(task):
    return [(i.start, i.stop, i.core) for job in task.jobs for i in job.execIntervals]


class DecoderTests(unittest.TestCase):
    def test_file_and_intermediate_representation(self):
        wire = Wire().boot(300).end(20)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'raw_buffer0.txt').write_bytes(wire.data)
            decoded = read_recording(root, 1, True)
            write_events(decoded, root / 'events.txt')
            lines = [json.loads(line) for line in (root / 'events.txt').read_text().splitlines()]
        self.assertEqual(decoded.events[1].task_id, 300)
        self.assertEqual(decoded.events[1].data['name'], 'worker')
        self.assertEqual(lines[0]['kind'], 'SYNC')
        self.assertEqual(decoded.epoch, 1000000)
        self.assertTrue(decoded.streams[0].ended)

    def test_long_gap_and_full_64_bit_clock(self):
        wire = Wire(epoch=(1 << 40) + 37)
        wire.sync(1 << 40).add(E.TIME_ZERO, (1 << 40) - 1000000, 0, 37, 256)
        wire.add(E.END, (1 << 40) + 20000000 - 1000000)
        decoded = recording(wire)
        self.assertEqual(decoded.events[-1].timestamp, (1 << 40) + 20000000)
        self.assertEqual(decoded.epoch, (1 << 40) + 37)

    def test_lost_epoch_repeated_in_sync(self):
        wire = Wire().boot()
        wire.sync(1000100, 7, missing=2).add(E.SWITCH_IN, 100, 7).end(110)
        decoded = recording(wire, strict=False)
        self.assertEqual(decoded.epoch, 1000000)
        self.assertEqual(decoded.streams[0].dropped, 2)
        self.assertEqual([e.kind for e in decoded.events].count(E.GAP), 1)
        with self.assertRaises(DecodeError):
            recording(wire)

    def test_corruption_recovers_only_at_sync(self):
        wire = Wire().boot()
        wire.data += b'\xfe\xff\x12\x34'
        wire.sequence += 1
        wire.sync(1000100, 7).add(E.SWITCH_IN, 100, 7).end(120)
        decoded = recording(wire, strict=False)
        gap = next(e for e in decoded.events if e.kind == E.GAP)
        self.assertEqual(gap.data['corrupt_bytes'], 4)
        self.assertEqual(decoded.events[-1].kind, E.END)

    def test_sync_and_event_commit_together(self):
        wire = Wire().boot()
        wire.sync(1000100)
        decoded = decode_stream(wire.data, 0)
        self.assertTrue(decoded.truncated)
        self.assertEqual(decoded.end_time, 1000000)
        with self.assertRaises(DecodeError):
            decode_stream(wire.data, 0, True)

    def test_bounded_names_and_truncated_payload(self):
        for length in (0, 4, 32, 64):
            wire = Wire().create(0, 7, 'x' * length).end(1)
            decoded = recording(wire)
            self.assertEqual(decoded.events[1].data['name'], 'x' * length)
        wire = Wire().create(0, 7, 'x' * 65)
        with self.assertRaises(DecodeError):
            recording(wire)
        wire = Wire().boot().add(E.SYSCALL_ENTER, 10, 7, S.QUEUE_RECEIVE)
        decoded = decode_stream(wire.data, 0)
        self.assertTrue(decoded.truncated)
        self.assertEqual(decoded.events[-1].kind, E.SWITCH_IN)

    def test_reject_incompatible_streams(self):
        first = Wire(0, 2).boot().end(10)
        second = Wire(1, 2, hz=100).add(E.END, 10)
        with self.assertRaises(ValueError):
            recording(first, second)
        wire = Wire().boot()
        struct.pack_into('<I', wire.data, 4, 4 | 1 << 16 | 32 << 24)
        with self.assertRaisesRegex(DecodeError, 'unsupported'):
            recording(wire, strict=False)

    def test_sequence_wrap(self):
        wire = Wire()
        wire.sequence = 0xffffffff
        wire.sync(1000000).add(E.SWITCH_IN, 0, 300)
        wire.sync(1000001).add(E.END, 1)
        decoded = recording(wire, strict=False)
        self.assertEqual(decoded.events[-1].sequence, 0)
        self.assertEqual(decoded.streams[0].dropped, 0)

    def test_unknown_epoch_is_not_tick_aligned(self):
        wire = Wire(epoch=None).boot().end(123)
        decoded = recording(wire)
        self.assertIsNone(decoded.epoch)
        tasks = events_to_tasks(decoded)
        self.assertEqual(task_named(tasks).jobs[0].releaseTime, 0)
        self.assertFalse(parser_module.lastReport['epochKnown'])


class ConversionTests(unittest.TestCase):
    def test_periodic_delay_and_explicit_deadline(self):
        wire = Wire().boot()
        wire.enter(100, 7, S.DELAY_UNTIL, a=0, b=5)
        wire.add(E.WAKE_TARGET, 101, 7, 5).add(E.SWITCH_OUT, 110, 7)
        for tick in range(1, 6):
            wire.add(E.TICK, tick * 1000, 0, tick - 1, 0)
        wire.add(E.READY, 5005, 7, 5).add(E.SWITCH_IN, 5020, 7)
        wire.returned(5030, 7, S.DELAY_UNTIL).end(5100)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(len(task.jobs), 2)
        self.assertEqual((task.jobs[0].completionTime, task.jobs[0].deadline), (100, 5000))
        self.assertEqual(task.jobs[1].releaseTime, 5000)
        self.assertEqual(task.jobs[0].getExecutionTime(), 100)
        self.assertTrue(task.jobs[1].incomplete)

    def test_late_delay_without_context_switch(self):
        wire = Wire().boot().add(E.TICK, 1000, 0, 0, 0).add(E.TICK, 2000, 0, 1, 0)
        wire.enter(2500, 7, S.DELAY_UNTIL, a=0, b=2).returned(2501, 7, S.DELAY_UNTIL, 0).end(3000)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[0].deadline, 2000)
        self.assertEqual(task.jobs[0].getFinishTime(), 2500)
        self.assertEqual(task.jobs[1].releaseTime, 2000)
        self.assertEqual(task.jobs[1].execIntervals[0].start, 2500)

    def test_relative_delay_uses_actual_kernel_wake_tick(self):
        wire = Wire().boot().enter(10, 7, S.DELAY, timeout=5)
        wire.add(E.TICK, 1000, 0, 0, 0).add(E.WAKE_TARGET, 1100, 7, 6)
        wire.add(E.SWITCH_OUT, 1101, 7).add(E.TICK, 6000, 0, 5, 0)
        wire.add(E.READY, 6001, 7, 6).add(E.SWITCH_IN, 6010, 7)
        wire.returned(6011, 7, S.DELAY, 0).end(6020)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[1].releaseTime, 6000)

    def test_sparse_ticks_do_not_move_late_deadline_to_previous_wrap(self):
        wire = Wire().boot().add(E.TICK, 1000, 0, 0, 0)
        wire.enter(10000, 7, S.DELAY_UNTIL, a=0, b=5)
        wire.returned(10001, 7, S.DELAY_UNTIL, 0).end(11000)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[0].deadline, 5000)
        self.assertTrue(task.jobs[0].deadlineEstimated)
        self.assertEqual(task.jobs[1].releaseTime, 5000)

    def test_three_cores_and_empty_stream(self):
        core0 = Wire(0, 3).create(0, 7).add(E.READY, 0, 7, 0).end(30)
        core2 = Wire(2, 3).add(E.SWITCH_IN, 10, 7).end(30)
        decoded = decode_buffers([core0.data, b'', core2.data], True)
        task = task_named(events_to_tasks(decoded))
        self.assertEqual(intervals(task), [(10, 30, 2)])
        self.assertTrue(any('empty stream' in warning for warning in decoded.warnings))

    def test_delay_zero_and_mutex_wait_do_not_split(self):
        wire = Wire().boot().enter(2, 7, S.DELAY, timeout=0).returned(3, 7, S.DELAY, 0)
        wire.enter(4, 7, S.MUTEX_TAKE).add(E.BLOCK, 5, 7, S.QUEUE_RECEIVE, 0x20001000)
        wire.add(E.SWITCH_OUT, 6, 7).add(E.READY, 10, 7, 0).add(E.SWITCH_IN, 11, 7)
        wire.add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 1).returned(13, 7, S.SEMAPHORE_TAKE).end(20)
        tasks = events_to_tasks(recording(wire))
        self.assertEqual(len(task_named(tasks).jobs), 1)
        self.assertEqual(intervals(task_named(tasks)), [(0, 6, 0), (11, 20, 0)])

    def test_immediate_wait_is_boundary_even_without_ready(self):
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE)
        wire.add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 0).returned(15, 7, S.QUEUE_RECEIVE).end(20)
        tasks = events_to_tasks(recording(wire))
        task = task_named(tasks)
        self.assertEqual(len(task.jobs), 2)
        self.assertEqual((task.jobs[0].completionTime, task.jobs[1].releaseTime), (10, 12))
        self.assertEqual(sum(j.getExecutionTime() for j in task.jobs), 18)
        runtime = next(t for t in tasks if t.kind == 'runtime')
        self.assertEqual(intervals(runtime), [(10, 12, 0)])

    def test_zero_timeout_polling(self):
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE, timeout=0)
        wire.add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 0).returned(15, 7, S.QUEUE_RECEIVE).end(20)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(len(task.jobs), 1)
        self.assertEqual(task.jobs[0].getExecutionTime(), 20)

    def test_timeout_release_precedes_dispatch(self):
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE, timeout=5)
        wire.add(E.BLOCK, 11, 7, S.QUEUE_RECEIVE, 0x20001000).add(E.SWITCH_OUT, 12, 7)
        wire.add(E.READY, 100, 7, 1).add(E.SWITCH_IN, 110, 7).returned(112, 7, S.QUEUE_RECEIVE, 0).end(120)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[1].releaseTime, 100)
        self.assertEqual(task.jobs[1].trigger, 'timeout')

    def test_reblocked_queue_wait_uses_final_ready(self):
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE)
        wire.add(E.BLOCK, 11, 7, S.QUEUE_RECEIVE, 0x20001000).add(E.SWITCH_OUT, 12, 7)
        wire.add(E.READY, 20, 7, 0).add(E.SWITCH_IN, 21, 7)
        wire.add(E.BLOCK, 22, 7, S.QUEUE_RECEIVE, 0x20001000).add(E.SWITCH_OUT, 23, 7)
        wire.add(E.READY, 30, 7, 0).add(E.SWITCH_IN, 31, 7)
        wire.add(E.QUEUE_RECEIVED, 32, 7, 0x20001000, 0).returned(33, 7, S.QUEUE_RECEIVE).end(40)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[1].releaseTime, 30)
        self.assertEqual(intervals(task), [(0, 10, 0), (31, 40, 0)])

    def test_nested_irqs_and_scheduler_time(self):
        wire = Wire().boot().add(E.IRQ_ENTER, 10, 0, 15).add(E.IRQ_ENTER, 12, 0, 16)
        wire.add(E.IRQ_EXIT, 14).add(E.IRQ_EXIT_SCHEDULE, 18).add(E.SWITCH_OUT, 20, 7).end(25)
        tasks = events_to_tasks(recording(wire))
        self.assertEqual(intervals(task_named(tasks)), [(0, 10, 0), (18, 20, 0)])
        self.assertEqual(intervals(task_named(tasks, 'IRQ_15_C0')), [(10, 12, 0), (14, 18, 0)])
        self.assertEqual(intervals(task_named(tasks, 'IRQ_16_C0')), [(12, 14, 0)])
        self.assertEqual(intervals(task_named(tasks, 'Scheduler Core 0')), [(20, 25, 0)])

    def test_migration_and_equal_timestamp_ordering(self):
        core0 = Wire(0, 2).boot().enter(10, 7, S.NOTIFY_TAKE, obj=1)
        core0.add(E.BLOCK, 11, 7, S.NOTIFY_TAKE, 1).add(E.SWITCH_OUT, 12, 7)
        core0.add(E.READY, 20, 7, 0).end(40)
        core1 = Wire(1, 2).add(E.SWITCH_IN, 20, 7)
        core1.add(E.NOTIFY_TAKEN, 22, 7, 1, 9).returned(23, 7, S.NOTIFY_TAKE, 9).end(40)
        task = task_named(events_to_tasks(recording(core0, core1)))
        self.assertEqual(len(task.jobs), 2)  # nine coalesced notifications, one wait resolution
        self.assertEqual(task.jobs[1].releaseTime, 20)
        self.assertEqual(intervals(task), [(0, 10, 0), (20, 40, 1)])

    def test_mutex_history_and_recursive_depth(self):
        wire = Wire().boot()
        for t, kind in ((2, E.QUEUE_RECEIVED), (4, E.QUEUE_SENT), (6, E.QUEUE_RECEIVED), (8, E.QUEUE_SENT)):
            wire.add(kind, t, 7, 0x20001000, 1)
        wire.add(E.QUEUE_RECEIVED, 10, 7, 0x20002000, 4)
        wire.add(E.RECURSIVE_TAKEN, 11, 7, 0x20002000, 1).add(E.RECURSIVE_TAKEN, 12, 7, 0x20002000, 2)
        wire.add(E.RECURSIVE_GIVEN, 13, 7, 0x20002000, 2).add(E.RECURSIVE_GIVEN, 14, 7, 0x20002000, 1)
        wire.add(E.QUEUE_SENT, 15, 7, 0x20002000, 4).end(20)
        job = task_named(events_to_tasks(recording(wire))).jobs[0]
        self.assertEqual([(m.start, m.stop) for m in job.mutexAccess], [(2, 4), (6, 8), (10, 15)])

    def test_mutex_attempt_precedes_grant_and_failure_preserves_ownership(self):
        wire = Wire().boot().enter(10, 7, S.MUTEX_TAKE, timeout=5)
        wire.add(E.BLOCK, 11, 7, S.QUEUE_RECEIVE, 0x20001000).add(E.SWITCH_OUT, 12, 7)
        wire.add(E.READY, 50, 7, 0).add(E.SWITCH_IN, 51, 7)
        wire.add(E.QUEUE_RECEIVED, 55, 7, 0x20001000, 1).returned(56, 7, S.SEMAPHORE_TAKE)
        wire.enter(60, 7, S.MUTEX_TAKE, timeout=0).returned(61, 7, S.SEMAPHORE_TAKE, 0)
        wire.add(E.QUEUE_SENT, 70, 7, 0x20001000, 1).end(80)
        task = task_named(events_to_tasks(recording(wire), strict=True))
        self.assertEqual([(a.start, a.success, a.acquiredAt, a.resultTime) for a in task.mutexAttempts],
                         [(10, True, 55, 56), (60, False, None, 61)])
        self.assertEqual(task.mutexAttemptTimes, [10, 60])
        self.assertEqual([(a.start, a.stop) for a in task.jobs[0].mutexAccess], [(55, 70)])
        self.assertTrue(task.jobs[0].mutexAccess[0].released)
        self.assertEqual({a.letter for a in task.mutexAttempts}, {'A'})

    def test_mutex_timeout_is_a_failed_attempt_not_an_acquisition(self):
        wire = Wire().boot().enter(10, 7, S.MUTEX_TAKE, timeout=5)
        wire.add(E.BLOCK, 11, 7, S.QUEUE_RECEIVE, 0x20001000).add(E.SWITCH_OUT, 12, 7)
        wire.add(E.READY, 30, 7, 1).add(E.SWITCH_IN, 31, 7)
        wire.returned(32, 7, S.SEMAPHORE_TAKE, 0).end(40)
        task = task_named(events_to_tasks(recording(wire), strict=True))
        self.assertEqual(len(task.jobs), 1)
        self.assertEqual(task.jobs[0].mutexAccess, [])
        self.assertEqual((task.mutexAttempts[0].start, task.mutexAttempts[0].success), (10, False))

    def test_recursive_attempts_exclude_internal_semaphore_calls(self):
        wire = Wire().boot().enter(10, 7, S.MUTEX_RECURSIVE)
        wire.enter(11, 7, S.MUTEX_TAKE).add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 4)
        wire.returned(13, 7, S.SEMAPHORE_TAKE).add(E.RECURSIVE_TAKEN, 14, 7, 0x20001000, 1)
        wire.returned(15, 7, S.MUTEX_RECURSIVE).enter(20, 7, S.MUTEX_RECURSIVE)
        wire.add(E.RECURSIVE_TAKEN, 21, 7, 0x20001000, 2).returned(22, 7, S.MUTEX_RECURSIVE)
        wire.add(E.RECURSIVE_GIVEN, 23, 7, 0x20001000, 2).add(E.RECURSIVE_GIVEN, 24, 7, 0x20001000, 1)
        wire.add(E.QUEUE_SENT, 25, 7, 0x20001000, 4).end(30)
        task = task_named(events_to_tasks(recording(wire), strict=True))
        self.assertEqual([(a.start, a.acquiredAt, a.success) for a in task.mutexAttempts],
                         [(10, 12, True), (20, 21, True)])
        self.assertTrue(all(a.recursive for a in task.mutexAttempts))
        self.assertEqual([(a.start, a.stop) for a in task.jobs[0].mutexAccess], [(12, 25)])

    def test_failed_recursive_call_has_one_failed_marker(self):
        wire = Wire().boot().enter(10, 7, S.MUTEX_RECURSIVE)
        wire.enter(11, 7, S.MUTEX_TAKE).returned(20, 7, S.SEMAPHORE_TAKE, 0)
        wire.returned(21, 7, S.MUTEX_RECURSIVE, 0).end(30)
        task = task_named(events_to_tasks(recording(wire), strict=True))
        self.assertEqual([(a.start, a.success) for a in task.mutexAttempts], [(10, False)])
        self.assertEqual(task.jobs[0].mutexAccess, [])

    def test_missing_mutex_result_remains_unknown(self):
        wire = Wire().boot().enter(10, 7, S.MUTEX_TAKE)
        wire.add(E.BLOCK, 11, 7, S.QUEUE_RECEIVE, 0x20001000).add(E.SWITCH_OUT, 12, 7).end(20)
        task = task_named(events_to_tasks(recording(wire), strict=True))
        self.assertIsNone(task.mutexAttempts[0].success)
        self.assertTrue(task.mutexAttempts[0].uncertain)

    def test_grant_without_return_proves_success_but_not_release(self):
        wire = Wire().boot().enter(10, 7, S.MUTEX_TAKE)
        wire.add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 1).end(20)
        task = task_named(events_to_tasks(recording(wire), strict=True))
        self.assertTrue(task.mutexAttempts[0].success)
        self.assertEqual(task.mutexAttempts[0].acquiredAt, 12)
        self.assertIsNone(task.mutexAttempts[0].resultTime)
        self.assertFalse(task.jobs[0].mutexAccess[0].released)

    def test_policy_and_internal_caller_exclusion(self):
        wire = Wire().boot().enter(10, 7, S.EVENT_WAIT, caller=0x1001)
        wire.add(E.EVENT_WAIT_END, 12, 7, 0x20001000, 0).returned(13, 7, S.EVENT_WAIT).end(20)
        policy = JobPolicy.from_dict({'caller_names': {'0x1001': 'vPortLockInternalSpinUnlockWithWait'}})
        self.assertEqual(len(task_named(events_to_tasks(recording(wire), policy)).jobs), 1)
        policy = JobPolicy.from_dict({'task_boundaries': {'worker': ['DELAY_UNTIL']}})
        self.assertEqual(len(task_named(events_to_tasks(recording(wire), policy)).jobs), 1)
        self.assertEqual(len(task_named(events_to_tasks(recording(wire))).jobs), 2)

    def test_loss_marks_unknown_cpu_and_uncertain_jobs(self):
        wire = Wire().boot().add(E.PRIORITY, 10, 7, 4)
        wire.sync(1000100, running=7, missing=3).add(E.PRIORITY, 100, 7, 5)
        wire.add(E.SWITCH_IN, 110, 7).end(120)
        tasks = events_to_tasks(recording(wire, strict=False))
        self.assertEqual(intervals(task_named(tasks)), [(0, 10, 0), (110, 120, 0)])
        self.assertEqual(intervals(task_named(tasks, 'Unknown Core 0')), [(10, 110, 0)])
        self.assertTrue(task_named(tasks).jobs[0].uncertain)
        self.assertEqual(parser_module.lastReport['eventsLost'], 3)

    def test_idle_identification_does_not_depend_on_name(self):
        wire = Wire().boot().add(E.IDLE_TASK, 0, 7, 0)
        wire.enter(10, 7, S.DELAY, timeout=1).returned(11, 7, S.DELAY, 0).end(20)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.kind, 'idle')
        self.assertEqual(len(task.jobs), 1)

    def test_tick_wrap_and_non_1khz(self):
        wire = Wire(bits=16, hz=100).boot()
        wire.add(E.TICK, 100, 0, 65534, 0).enter(200, 7, S.DELAY_UNTIL, a=65534, b=2)
        wire.add(E.WAKE_TARGET, 201, 7, 0).add(E.SWITCH_OUT, 202, 7)
        wire.add(E.TICK, 10100, 0, 65535, 0).add(E.READY, 10101, 7, 0)
        wire.add(E.SWITCH_IN, 10102, 7).returned(10103, 7, S.DELAY_UNTIL).end(10200)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[0].deadline, 10100)
        self.assertEqual(task.jobs[1].releaseTime, 10100)

    def test_open_job_and_deleted_task_are_incomplete(self):
        wire = Wire().boot().add(E.TASK_DELETE, 10, 7).end(20)
        job = task_named(events_to_tasks(recording(wire))).jobs[0]
        self.assertTrue(job.incomplete)
        self.assertTrue(job.aborted)
        self.assertEqual(job.getFinishTime(), 10)

    def test_parser_rejects_other_formats(self):
        for raw in (b'old-format', b'old-name-RTT3\0\0\0\0'):
            with self.subTest(raw=raw), self.assertRaisesRegex(DecodeError, 'format-3'):
                parser_module.parser([raw])

    def test_parser_wrapper_reads_format3_and_writes_events(self):
        wire = Wire().boot().end(20)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'events.txt'
            tasks = parser_module.parser([wire.data], output, strict=True)
            self.assertEqual(task_named(tasks).jobs[0].getExecutionTime(), 20)
            self.assertTrue(output.exists())
            self.assertEqual(json.loads(output.read_text().splitlines()[0])['kind'], 'SYNC')

    def test_gap_does_not_keep_a_lost_call_on_the_stack(self):
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE)
        wire.add(E.BLOCK, 11, 7, S.QUEUE_RECEIVE, 0x20001000)
        wire.sync(1000100, running=7, missing=3).add(E.SWITCH_IN, 100, 7)
        wire.enter(110, 7, S.NOTIFY_TAKE, obj=1).add(E.NOTIFY_TAKEN, 112, 7, 1, 3)
        wire.returned(113, 7, S.NOTIFY_TAKE, 3).end(120)
        task = task_named(events_to_tasks(recording(wire, strict=False)))
        self.assertIn('NOTIFY_TAKE', [job.boundary for job in task.jobs])

    def test_exact_epoch_and_no_recording_end_padding(self):
        wire = Wire(epoch=1000037).boot().end(50)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.jobs[0].releaseTime, -37)
        self.assertEqual(intervals(task), [(-37, 13, 0)])

    def test_missing_irq_entry_strict_mode(self):
        wire = Wire().boot().add(E.IRQ_EXIT, 10).end(20)
        with self.assertRaises(TraceInconsistency):
            events_to_tasks(recording(wire), strict=True)

    def test_gui_loader_uses_both_stages(self):
        import configparser
        helper = types.ModuleType('HelperFunctions')
        config = configparser.ConfigParser()
        config.read_dict({'general': {'strict': 'True'}})
        helper.readConfig = lambda: config
        helper.printState = lambda *args, **kwargs: None
        exported = []
        export = types.ModuleType('DataExport')
        export.exportData = lambda gui, tasks, **kwargs: exported.extend(tasks)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'raw_buffer0.txt').write_bytes(Wire().boot().end(20).data)
            gui = types.SimpleNamespace(viewingFolder=directory)
            with patch.dict(sys.modules, {'HelperFunctions': helper, 'DataExport': export}):
                tasks = parser_module.loadRecording(gui, 1)
            self.assertEqual(tasks, exported)
            self.assertTrue((root / 'events.txt').exists())

    def test_completion_statistics_exclude_incomplete_jobs(self):
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE)
        wire.add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 0).returned(15, 7, S.QUEUE_RECEIVE).end(100)
        task = task_named(events_to_tasks(recording(wire)))
        self.assertEqual(task.getMaxExecutionTime(), 10)
        self.assertEqual(task.getMaxResponseTime(), 10)

    def test_csv_excludes_incomplete_and_uncertain_jobs(self):
        import csv
        import contextlib
        import io
        import importlib.util
        wire = Wire().boot().enter(10, 7, S.QUEUE_RECEIVE)
        wire.add(E.QUEUE_RECEIVED, 12, 7, 0x20001000, 0).returned(15, 7, S.QUEUE_RECEIVE).end(100)
        task = task_named(events_to_tasks(recording(wire)))
        source = Path(__file__).resolve().parents[1] / 'src/DataExport.py'
        spec = importlib.util.spec_from_file_location('export_under_test', source)
        module = importlib.util.module_from_spec(spec)
        helper = types.ModuleType('HelperFunctions')
        helper.printState = lambda *args, **kwargs: None
        with patch.dict(sys.modules, {'HelperFunctions': helper}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'worker.csv'
            module.createDataFile(output, task)
            with output.open() as file:
                self.assertEqual(len(list(csv.reader(file))), 2)
            task.jobs[0].uncertain = True
            module.createDataFile(output, task)
            with output.open() as file:
                self.assertEqual(len(list(csv.reader(file))), 1)
            task.jobs[0].uncertain = False
            with contextlib.redirect_stdout(io.StringIO()):
                module.exportData(types.SimpleNamespace(viewingFolder=directory), [task], overwrite=True)
            with output.open() as file:
                self.assertEqual(len(list(csv.reader(file))), 2)


if __name__ == '__main__':
    unittest.main()
