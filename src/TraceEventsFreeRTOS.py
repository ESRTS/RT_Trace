"""Stage 1: target format-3 RTT bytes -> validated, timestamped Python events.

No GUI, TraceTask, or job inference dependencies. Files raw_buffer<N>.txt are
binary despite their extension. Times are absolute shared-timer microseconds;
Recording.epoch is the exact application origin, if recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import heapq
import json
from pathlib import Path
import struct
from typing import Any

SYNC_MAGIC = 0x33545452
SYNC_BYTES = struct.pack('<I', SYNC_MAGIC)
FORMAT_VERSION = 3
UINT32_MASK = (1 << 32) - 1
EPOCH_UNSET = (1 << 64) - 1


class EventType(IntEnum):
    GAP = -1
    SYNC = 0
    SWITCH_IN = 1
    SWITCH_OUT = 2
    READY = 3
    TASK_CREATE = 4
    TASK_DELETE = 5
    PRIORITY = 6
    SUSPEND = 7
    RESUME = 8
    TICK = 9
    TICK_JUMP = 10
    SYSCALL_ENTER = 11
    SYSCALL_RETURN = 12
    BLOCK = 13
    WAKE_TARGET = 14
    OBJECT_CREATE = 15
    OBJECT_DELETE = 16
    QUEUE_RECEIVED = 17
    QUEUE_SENT = 18
    NOTIFY_TAKEN = 19
    NOTIFY_WAITED = 20
    EVENT_WAIT_END = 21
    EVENT_SYNC_END = 22
    RECURSIVE_TAKEN = 23
    RECURSIVE_GIVEN = 24
    TIME_ZERO = 25
    END = 26
    IRQ_ENTER = 27
    IRQ_EXIT = 28
    IRQ_EXIT_SCHEDULE = 29
    AFFINITY = 30
    IDLE_TASK = 31


class Syscall(IntEnum):
    DELAY_UNTIL = 1
    DELAY = 2
    QUEUE_RECEIVE = 3
    SEMAPHORE_TAKE = 4
    MUTEX_TAKE = 5
    NOTIFY_TAKE = 6
    NOTIFY_WAIT = 7
    EVENT_WAIT = 8
    EVENT_SYNC = 9
    MUTEX_RECURSIVE = 10
    STREAM_SEND = 11
    STREAM_RECEIVE = 12


# Fixed payload schemas, in wire order. TASK_CREATE has a bounded byte suffix.
_FIELDS = {
    EventType.SWITCH_IN: (), EventType.SWITCH_OUT: (),
    EventType.READY: ('tick',),
    EventType.TASK_CREATE: ('handle', 'priority', 'name_length'),
    EventType.TASK_DELETE: (), EventType.PRIORITY: ('priority',),
    EventType.SUSPEND: (), EventType.RESUME: (),
    EventType.TICK: ('tick', 'suspended'), EventType.TICK_JUMP: ('tick', 'jump'),
    EventType.SYSCALL_ENTER: ('call', 'object', 'timeout', 'arg0', 'arg1', 'caller'),
    EventType.SYSCALL_RETURN: ('call', 'result'),
    EventType.BLOCK: ('call', 'object'), EventType.WAKE_TARGET: ('tick',),
    EventType.OBJECT_CREATE: ('object', 'object_type', 'caller'),
    EventType.OBJECT_DELETE: ('object',),
    EventType.QUEUE_RECEIVED: ('object', 'object_type'),
    EventType.QUEUE_SENT: ('object', 'object_type'),
    EventType.NOTIFY_TAKEN: ('index', 'value'),
    EventType.NOTIFY_WAITED: ('index', 'received'),
    EventType.EVENT_WAIT_END: ('object', 'timeout'),
    EventType.EVENT_SYNC_END: ('object', 'timeout'),
    EventType.RECURSIVE_TAKEN: ('object', 'depth'),
    EventType.RECURSIVE_GIVEN: ('object', 'depth'),
    EventType.TIME_ZERO: ('epoch_low', 'epoch_high'), EventType.END: (),
    EventType.IRQ_ENTER: ('irq',), EventType.IRQ_EXIT: (),
    EventType.IRQ_EXIT_SCHEDULE: (), EventType.AFFINITY: ('mask',),
    EventType.IDLE_TASK: ('idle_core',),
}


@dataclass(frozen=True)
class Event:
    kind: EventType
    timestamp: int
    core: int
    sequence: int | None = None
    task_id: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    offset: int = 0


@dataclass
class Stream:
    core: int
    events: list[Event] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    session: int | None = None
    core_count: int = 0
    tick_bits: int = 0
    tick_hz: int = 0
    irq_enabled: bool = False
    ended: bool = False
    dropped: int = 0
    truncated: bool = False

    @property
    def end_time(self):
        return self.events[-1].timestamp if self.events else None


@dataclass
class Recording:
    events: list[Event]
    streams: list[Stream]
    epoch: int | None
    warnings: list[str] = field(default_factory=list)


class DecodeError(ValueError):
    def __init__(self, core, offset, message):
        super().__init__(f'Core {core}, byte {offset}: {message}')
        self.core, self.offset = core, offset


class _Truncated(Exception):
    pass


def _words(raw, offset, count):
    if offset + count * 4 > len(raw):
        raise _Truncated()
    return struct.unpack_from('<' + 'I' * count, raw, offset)


def _ordinary(raw, offset, core, timestamp, sequence):
    start = offset
    header, = _words(raw, offset, 1)
    offset += 4
    try:
        kind = EventType(header & 255)
        fields = _FIELDS[kind]
    except (ValueError, KeyError):
        raise DecodeError(core, start, f'invalid event type {header & 255}')
    task_id = (header >> 8) & 255
    if task_id == 255:
        task_id, = _words(raw, offset, 1)
        offset += 4
        if task_id < 255:
            raise DecodeError(core, start, 'invalid extended task ID')
    values = _words(raw, offset, len(fields))
    offset += 4 * len(fields)
    data = dict(zip(fields, values))
    if kind == EventType.TASK_CREATE:
        length = data['name_length']
        if length > 64:
            raise DecodeError(core, start, f'task name length {length} exceeds wire limit 64')
        padded = (length + 3) & ~3
        if offset + padded > len(raw):
            raise _Truncated()
        data['name'] = raw[offset:offset + length].decode('utf-8', errors='replace')
        offset += padded
    elif kind == EventType.SYSCALL_ENTER:
        if data['call'] not in Syscall._value2member_map_:
            raise DecodeError(core, start, f"invalid syscall kind {data['call']}")
    elif kind == EventType.TIME_ZERO:
        data['epoch'] = data.pop('epoch_low') | (data.pop('epoch_high') << 32)
    elif kind == EventType.IRQ_ENTER and not 2 <= data['irq'] <= 511:
        raise DecodeError(core, start, f"invalid exception number {data['irq']}")
    return Event(kind, timestamp + (header >> 16), core, sequence, task_id, data, start), offset


def _sync(raw, offset, core):
    words = _words(raw, offset, 12)
    flags = words[1]
    version, source_core = flags & 255, (flags >> 8) & 255
    core_count, tick_bits = (flags >> 16) & 127, flags >> 24
    if version != FORMAT_VERSION:
        raise DecodeError(core, offset, f'unsupported trace format {version}')
    if source_core != core or not 1 <= core_count <= 127 or core >= core_count:
        raise DecodeError(core, offset, 'invalid core metadata')
    if tick_bits not in (16, 32) or not 0 < words[11] <= 1000000:
        raise DecodeError(core, offset, 'invalid tick width/frequency')
    timestamp = words[2] | (words[3] << 32)
    # A SYNC is committed together with its following complete zero-delta event.
    event, end = _ordinary(raw, offset + 48, core, timestamp, words[4])
    if event.timestamp != timestamp:
        raise DecodeError(core, offset, 'event following SYNC has nonzero delta')
    epoch = words[6] | (words[7] << 32)
    metadata = dict(core_count=core_count, tick_bits=tick_bits, tick_hz=words[11],
                    irq_enabled=bool(flags & (1 << 23)), dropped=words[5],
                    epoch=None if epoch == EPOCH_UNSET else epoch,
                    session=words[8] | (words[9] << 32), running_task=words[10])
    return Event(EventType.SYNC, timestamp, core, words[4], data=metadata, offset=offset), event, end


def decode_stream(raw: bytes, core: int, strict=False) -> Stream:
    """Decode one core. Recovery skips to a validated SYNC, never a guessed event."""
    raw = bytes(raw)
    stream = Stream(core)
    offset, timestamp, sequence = 0, None, None
    previous_drops = 0
    recovery_start = None
    while offset < len(raw):
        start = offset
        try:
            if raw[offset:offset + 4] == SYNC_BYTES:
                sync, event, end = _sync(raw, offset, core)
                data = sync.data
                metadata = (data['session'], data['core_count'], data['tick_bits'],
                            data['tick_hz'], data['irq_enabled'])
                old = (stream.session, stream.core_count, stream.tick_bits, stream.tick_hz, stream.irq_enabled)
                if stream.session is not None and metadata != old:
                    raise DecodeError(core, offset, 'session/configuration changed within a stream')
                if timestamp is not None and sync.timestamp < timestamp:
                    raise DecodeError(core, offset, 'timestamp moved backwards')
                lost = (data['dropped'] - previous_drops) & UINT32_MASK
                missing = ((sync.sequence - sequence) & UINT32_MASK) if sequence is not None else sync.sequence
                if sequence is not None and missing != lost and recovery_start is None:
                    stream.issues.append(f'Core {core}: sequence gap {missing} differs from target drops {lost}')
                if lost or missing or recovery_start is not None:
                    if strict:
                        raise DecodeError(core, offset, f'event loss: {lost} target drops, {missing} missing sequence entries')
                    stream.events.append(Event(EventType.GAP, sync.timestamp, core, data={
                        'start': timestamp if timestamp is not None else sync.timestamp,
                        'end': sync.timestamp, 'dropped': lost, 'missing': missing,
                        'corrupt_bytes': offset - recovery_start if recovery_start is not None else 0,
                    }, offset=offset))
                stream.session, stream.core_count, stream.tick_bits, stream.tick_hz, stream.irq_enabled = metadata
                stream.dropped += lost
                previous_drops = data['dropped']
                stream.events.extend((sync, event))
                timestamp, sequence = event.timestamp, (event.sequence + 1) & UINT32_MASK
                offset, recovery_start = end, None
            else:
                if timestamp is None or recovery_start is not None:
                    raise DecodeError(core, offset, 'expected a synchronization record')
                event, offset = _ordinary(raw, offset, core, timestamp, sequence)
                stream.events.append(event)
                timestamp, sequence = event.timestamp, (sequence + 1) & UINT32_MASK
            if stream.events[-1].kind == EventType.END:
                stream.ended = True
                if offset < len(raw):
                    raise DecodeError(core, offset, 'data follows END')
        except _Truncated:
            stream.truncated = True
            message = f'Core {core}, byte {start}: truncated trailing record'
            if strict:
                raise DecodeError(core, start, 'truncated trailing record')
            stream.issues.append(message)
            break
        except DecodeError as error:
            # Unsupported versions and changed sessions are not corruption to repair.
            if strict or 'unsupported trace format' in str(error) or 'session/configuration changed' in str(error) or stream.ended:
                raise
            stream.issues.append(str(error))
            if recovery_start is None:
                recovery_start = start
            candidate = raw.find(SYNC_BYTES, start + 1)
            # Validate recovery candidates; a task name can contain the magic bytes.
            while candidate >= 0:
                try:
                    _sync(raw, candidate, core)
                    break
                except (DecodeError, _Truncated):
                    candidate = raw.find(SYNC_BYTES, candidate + 1)
            if candidate < 0:
                stream.truncated = True
                break
            offset = candidate
    if raw and stream.session is None:
        raise DecodeError(core, 0, 'no complete format-3 synchronization/event pair')
    gaps = [e for e in stream.events if e.kind == EventType.GAP]
    if gaps:
        unavailable = sum(e.data['missing'] for e in gaps)
        stream.issues.append(f'Core {core}: {stream.dropped} target drops, {unavailable} unavailable sequence entries in {len(gaps)} gaps')
    if stream.events and not stream.ended:
        stream.issues.append(f'Core {core}: no END marker; coverage ends at its last decoded event')
    return stream


# Head-of-stream priorities resolve common equal-us causality without reordering
# events within a core. Remaining cross-core ties have no measured total order.
_PRIORITY = {EventType.GAP: 0, EventType.SYNC: 1, EventType.TASK_CREATE: 2,
             EventType.OBJECT_CREATE: 2, EventType.IDLE_TASK: 2,
             EventType.READY: 3, EventType.SWITCH_OUT: 4, EventType.SWITCH_IN: 8}


def merge_streams(streams):
    heap = []
    def push(core, index):
        event = streams[core].events[index]
        heapq.heappush(heap, (event.timestamp, _PRIORITY.get(event.kind, 5), core, index))
    for core, stream in enumerate(streams):
        if stream.events:
            push(core, 0)
    result = []
    while heap:
        _, _, core, index = heapq.heappop(heap)
        result.append(streams[core].events[index])
        if index + 1 < len(streams[core].events):
            push(core, index + 1)
    return result


def decode_buffers(buffers, strict=False) -> Recording:
    streams = [decode_stream(raw, core, strict) for core, raw in enumerate(buffers)]
    populated = [stream for stream in streams if stream.session is not None]
    if not populated:
        raise ValueError('The recording contains no events')
    configs = {(s.session, s.core_count, s.tick_bits, s.tick_hz, s.irq_enabled) for s in populated}
    if len(configs) != 1:
        raise ValueError('Core streams belong to different sessions/configurations')
    if any(s.core_count != len(streams) for s in populated):
        raise ValueError('Number of supplied buffers does not match the recorded core count')
    events = merge_streams(streams)
    epochs = {e.data['epoch'] for e in events if e.kind in (EventType.SYNC, EventType.TIME_ZERO) and e.data.get('epoch') is not None}
    if len(epochs) > 1:
        raise ValueError('Conflicting application time-zero timestamps')
    warnings = [message for stream in streams for message in stream.issues]
    if not populated[0].irq_enabled:
        warnings.append('IRQ tracing was disabled: task/scheduler CPU intervals include untraced IRQ time')
    for stream in streams:
        if not stream.events:
            warnings.append(f'Core {stream.core}: empty stream; coverage is unknown')
    return Recording(events, streams, next(iter(epochs), None), warnings)


def read_recording(folder, num_cores=2, strict=False) -> Recording:
    paths = [Path(folder) / f'raw_buffer{core}.txt' for core in range(num_cores)]
    return decode_buffers([path.read_bytes() for path in paths], strict)


def write_events(recording, path):
    """Save inspectable JSON lines; Python callers can use Recording.events directly."""
    with Path(path).open('w', encoding='utf-8') as output:
        for event in recording.events:
            output.write(json.dumps(dict(kind=event.kind.name, timestamp=event.timestamp,
                                         core=event.core, sequence=event.sequence,
                                         task_id=event.task_id, offset=event.offset,
                                         data=event.data), ensure_ascii=False) + '\n')
