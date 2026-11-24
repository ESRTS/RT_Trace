# Jetson Thor example

`thor_test.sqlite` is an Nsight Systems 2025.6.1 trace (SQLite export) of the
GpuTasksExample: four periodic SCHED_FIFO tasks on a Jetson Thor, two of them
launch a `spin_kernel` on their own CUDA stream and wait for it.

| Task | Period | Priority | Work |
| --- | --- | --- | --- |
| Task_CpuA | 20 ms | 98 | 4 ms CPU |
| Task_GpuB | 40 ms | 97 | 4 ms CPU, 8 ms kernel |
| Task_GpuA | 50 ms | 96 | 6 ms CPU, 10 ms kernel |
| Task_CpuB | 100 ms | 95 | 30 ms CPU |

Recorded with `--trace=cuda,nvtx,osrt --sample=none --cpuctxsw=process-tree`.
Copy the file into `data/Jetson_Thor/<recording>/` and load it in RT-Trace, or
analyze it directly: `python src/analysis examples/jetsonThor/thor_test.sqlite --all`
(this writes its CSV files into `examples/jetsonThor/analysis/`).
