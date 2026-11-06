Synthetic 42 ms, two-core format-3 recording. The `.txt` files contain binary data.

- Control: 10 ms nominal period; one deadline miss at 30 ms, completion at 33.5 ms.
- Worker: notification releases, mutex contention, a failed take at 22.3 ms, recursive mutex use, and migration between cores.
- Idle tasks, scheduler intervals, nested IRQs, and incomplete final jobs.

Install/regenerate with `python3 examples/generate_freertos_demo.py --install`.

In RT-Trace, select **Pico2 FreeRTOS RTT**, choose **Example_format3**, then
**Load Trace**. Reselect the platform to refresh an already-open application.
No hardware or firmware project is required to load it.

Take markers appear at call entry: black = success, red/crossed = failure,
gray = outcome missing. White markers indicate actual releases.
