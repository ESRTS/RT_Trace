# RT-Trace View

A tool to visualize scheduling traces. Several trace sources can be configured. If the trace source is a real target platform the trace can be loaded from the traget directly (if supported).

## Supported Features
* <b>Recording</b> the trace buffers from the target device (one for each core). RT-Trace flashes the program and reads the buffers through pyOCD, no other tools are needed.
All measurements are stored in a ```data``` folder. Each supported platform has its own sub-folder with separate folders for each measurement (as some platforms generate several files for one measurement). The name of each measurement can be set in the GUI, a date/time string will be appended to be able to distinguish different measurements. 
* <b>Loading</b> the trace. This parses the trace buffers to an internal, per task, data model. The trace is the visuallized in the GUI. A drop-down menu is used to select the measurement to be analyzed out of all measurements available for the selected target platform.
* <b>Navigating</b> the trace. Ctrl+mouse wheel or the Up/Down keys zoom, the mouse wheel or dragging moves the view in time, and Shift+mouse wheel scrolls the tasks if they don't fit into the window.
* <b>Save</b> the trace as PDF. The current view of the trace is exported to a PDF.

## Generate Application
Use pyinstaller to generate the packaged application. Install the build requirements first with `pip install -r requirements-build.txt`. To generate the application for Windows this must be executed on under Windows.
Executables for Linux and OSX can be created on OSX directly.

```$ ./createGui.sh ```

## Supported Platforms

### Raspberry Pi Pico2 with FreeRTOS SMP

In our trace implementation on the Pico2, each core loggs trace events to its own trace buffer. The source for the timestamp on each core is the same, which allows for an easy combination of events from both trace buffers. 

Three versions are supported. In the default configuration, the trace buffer of each core is stored in a dedicated SRAM bank. 

If an external PSRAM is available, the trace buffer can be stored there as well, which allows for longer traces to be recoreded (at the expense of larger impact of tracing on the system).

The third version streams the trace events over SEGGER RTT while the target runs, so the trace length is not limited by a buffer.

RT-Trace reads `PICO_USE_PSRAM` and `TRACE_RTT` from the program's `traceConfig.h` and only records if the selected platform (SRAM, PSRAM or RTT) matches.

### Nucleo-L476RG with FreeRTOS

#### Required Source Code Modifications
<b>File:</b> ```port.c```

Tracing calls must be added to the tick handler.
```
void xPortSysTickHandler( void )
{
	/* The SysTick runs at the lowest interrupt priority, so when this interrupt
	executes all interrupts must be unmasked.  There is therefore no need to
	save and then restore the interrupt mask value as its value is already
	known. */
	portDISABLE_INTERRUPTS();
	{
		traceISR_ENTER();
		/* Increment the RTOS tick. */
		if( xTaskIncrementTick() != pdFALSE )
		{
			traceISR_EXIT_TO_SCHEDULER();
			/* A context switch is required.  Context switching is performed in
			the PendSV interrupt.  Pend the PendSV interrupt. */
			portNVIC_INT_CTRL_REG = portNVIC_PENDSVSET_BIT;
		} else {
			traceISR_EXIT();
		}
	}
	portENABLE_INTERRUPTS();
}
```

<b>File:</b> ```portmacro.h```
Substitute the definition of ```portEND_SWITCHING_ISR``` with:
```
#define portEND_SWITCHING_ISR( xSwitchRequired ) { if( xSwitchRequired != pdFALSE ) { traceISR_EXIT_TO_SCHEDULER(); portYIELD() } else { traceISR_EXIT(); } }
```

<b>File:</b> ```task.h```

```traceENTER_xTaskDelayUntil``` is not called in the FreeRTOS-version supplied with STM32CubeIDE. This call needs to be added at the beginning of the function ```vTaskDelayUntil```.

```
void vTaskDelayUntil( TickType_t * const pxPreviousWakeTime, const TickType_t xTimeIncrement )
	{
	TickType_t xTimeToWake;
	BaseType_t xAlreadyYielded, xShouldDelay = pdFALSE;

		traceENTER_xTaskDelayUntil( pxPreviousWakeTime, xTimeIncrement );

		configASSERT( pxPreviousWakeTime );
        ...
```

## Instrumenting the Source Code

FreeRTOS provides a number of [Trace Hook Macros](https://freertos.org/Documentation/02-Kernel/02-Kernel-features/09-RTOS-trace-feature). 
RT-Trace provides a definition for those macros needed to trace the execution of the tasks. 
In order to include the RT-Trace macros and underlying trace buffering system, they need to be included in the build.

* Add the files in the folder ```target``` (i.e. ```trace.c``` and ```trace.h```) to your project.
* Create a file ```traceConfig.h``` and add it to your project.

The file ```traceConfig.h``` should have the following content and is adjusted based on the used platform and configuration:

```
#ifndef TRACECONFIG
#define TRACECONFIG

/**
 * Set the platform that is used.
 */
//#define TRACE_STM32L476RG
#define TRACE_PICO2

/**
 * Enable if the board has PSRAM and this should be used
 */
//#define PICO_USE_PSRAM

/**
 * Enable to use Segger RTT channels for the trace data instead of the 
 * local buffers. 
 */
//#define TRACE_RTT

/**
 * Enable this to trace IRQ enter/exit
 */
#define TRACE_ENABLE_IRQ_TRACE

/** 
 * Enable this to trace idle events
 */
#define TRACE_ENABLE_IDLE_TRACE

#endif /* TRACECONFIG */
```

## RT-Trace Configuration File

RT-Trace reads its settings from `Resources/config.ini`. If `Resources/config.local.ini` exists, its values override the ones in `config.ini`. It is not tracked by git, so machine-specific settings belong there. Copy `config.local.ini.example` to start, and only add the keys you want to change.

The `[general]` section holds the settings for all targets:

```
[general]
# Path to the project folder. Absolute, or relative to the RT_Trace folder.
project_path = ../../Software/Projects
# Size of the user interface. auto follows the desktop scaling, or set a factor such as 1.5
ui_scale = auto
# SWD frequency
frequency = 4_000_000
# Duration of the trace collection in seconds. The target is halted afterwards
measure_seconds = 2
# ISR ID and name of the FreeRTOS tick ISR on each core
tickId = 15,16,42,45,41
irqName = SysTick,Tick,Bell,Timer,FIFO
# Print a hexdump of each raw trace buffer and every event when loading a trace
print_hexdump = True
print_events = True
```

Each target has a dedicated section in the configuration file. For example, the RP2350 target with the trace buffers in SRAM is configured as follows. The addresses and the size must match `TRACE_BUFFER_SIZE` in `target/trace.h`.
```
[Pico2_FreeRTOS_SRAM]
# Address of the trace buffers on the target
buffer0 = 0x2008001c
buffer1 = 0x2008100c
# Length of the trace buffers (each), in byte
bufferSize = 2000
```
