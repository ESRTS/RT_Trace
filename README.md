# RT-Trace View

A tool to visualize scheduling traces. Several trace sources can be configured. If the trace source is a real target platform the trace can be loaded from the traget directly (if supported).

## Supported Platforms

### Raspberry Pi Pico2 with FreeRTOS SMP

In our trace implementation on the Pico2, each core loggs trace events to its own trace buffer. The source for the timestamp on each core is the same, which allows for an easy combination of events from both trace buffers. 

#### Supported Features
* <b>Recording</b> the trace buffers from the target device (one for each core). This requires ```openocd``` and ```telnet``` to be on the path.
* <b>Loading</b> the trace. This parses the trace buffers to an internal, per task, data model. The trace is the visuallized in the GUI. 
* <b>Save</b> the trace as PDF. The current view of the trace is exported to a PDF.
  
## Generate Application
Use pyinstaller to generate the packaged application. To generate the application for Windows this must be executed on under Windows. 
Executables for Linux and OSX can be created on OSX directly. 

```$pyinstaller main.py --noconsole --icon ./test.icns --onefile```