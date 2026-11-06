# Stage 1: RTT bytes → events

Source: [TraceEventsFreeRTOS.py](../src/TraceEventsFreeRTOS.py).
Contract producer: [target](01-target.md); consumer: [stage 2](03-task-builder.md).
Standard-library-only; independent of GUI, `TraceTask` and job policy.

**API/data.**

- `read_recording(folder, num_cores=2, strict=False)` reads binary
  `raw_buffer<N>.txt`; file N corresponds to RTT channel N+1.
- `decode_buffers(buffers, strict=False)` returns `Recording(events, streams,
  epoch, warnings)`. `decode_stream(raw, core, strict=False)` decodes one core.
- `Event(kind, timestamp, core, sequence, task_id, data, offset)` carries named
  payload fields. Times remain absolute shared-timer microseconds; `epoch` is the
  application origin or `None`. `offset` locates the record in its core's bytes.
- `Stream` retains session/configuration, events, diagnostics, cumulative drops,
  END/truncation status and last decoded timestamp. `write_events` exports JSONL;
  stage 2 consumes `Recording` directly, without rereading that export.

**Decode/order.** `_ordinary` uses `EventType`, `Syscall` and `_FIELDS` for payload
decoding, full task IDs and bounded/padded task names. `_sync` validates format,
core/tick metadata and the following complete zero-delta event before accepting
the pair. Ordinary sequence numbers are inferred; SYNC supplies their anchors.
Unsigned sequence/drop arithmetic wraps modulo 2³².

`decode_buffers` requires matching session/configuration, buffer/core counts and
consistent epochs. `merge_streams` heap-merges by timestamp using only each
stream's next event, preserving per-core order. Equal-time priorities favor
metadata/READY/switch-out before switch-in; remaining cross-core ties do not
establish measured causality. The implementation loads complete files/events
into memory.

**Loss/errors.** Strict mode raises `DecodeError` for malformed, truncated or
lost records. Permissive mode retains diagnostics and searches only for a
validated SYNC/event pair; it never guesses an ordinary record boundary. Recovery
or missing sequence/drop counts generate synthetic `GAP(start, end, dropped,
missing, corrupt_bytes)` events. SYNC is also exposed as an event. Unsupported
versions, session/configuration changes, cross-stream disagreement and data after
END are fatal. Partial trailing records are discarded in permissive mode. Empty
cores and missing END produce warnings; coverage never extends beyond decoded
events. Conflicting epochs are fatal; missing epoch stays unknown.

**Change together.** Wire changes affect `_sync`, `_ordinary`, enums and `_FIELDS`
plus [trace.h](../target/trace.h). Named fields, units, SYNC/GAP semantics and
ordering are stage-2 interfaces. Keep job inference out of this module. Validate
with `DecoderTests` in [parser tests](../tests/test_parser_freertos.py), including
recovery, truncation, wrap and incompatible streams.
