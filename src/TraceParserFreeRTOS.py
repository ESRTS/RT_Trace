from threading import Thread
from pathlib import Path
import io
from TraceTask import *
import os
import HelperFunctions
import configparser
import sys
import numpy as np
import DataExport

"""
Set to true to print events read from the trace buffer (print_events in the config).
"""
enable_entry_print = False

"""
RTT streams that end within this time of the end of the recording ran until the end (in us).
"""
RTT_END_WINDOW_US = 100000

"""
All supported trace event id's.
"""
TRACE_IDLE                      = 1
TRACE_TASK_START_EXEC           = 2
TRACE_TASK_STOP_EXEC            = 3
TRACE_TASK_START_READY          = 4
TRACE_TASK_STOP_READY           = 5
TRACE_TASK_CREATE               = 6
TRACE_START                     = 7
TRACE_STOP                      = 8
TRACE_DELAY_UNTIL               = 9
TRACE_ISR_ENTER                 = 10
TRACE_ISR_EXIT                  = 11
TRACE_ISR_EXIT_TO_SCHEDULER     = 12
TRACE_DELAY                     = 13
TRACE_TIME_ZERO                 = 14
TRACE_EVT_GROUP_WAIT            = 15
TRACE_EVT_GROUP_SYNC            = 16
TRACE_MUTEX_CREATE              = 18
TRACE_MUTEX_TAKE                = 19
TRACE_MUTEX_GIVE                = 20 
TRACE_JLDJOB_FINISHED           = 21
TRACE_EVENTS_LOST               = 22    # Payload: number of events the target could not write since the last written event

# Those events are not in the original trace and are created during parsing
#TRACE_IDLE_START                = 23
#TRACE_IDLE_STOP                 = 24

eventMap = {
    TRACE_IDLE : "TRACE_IDLE",
    TRACE_TASK_START_EXEC : "TRACE_TASK_START_EXEC",
    TRACE_TASK_STOP_EXEC : "TRACE_TASK_STOP_EXEC",
    TRACE_TASK_START_READY : "TRACE_TASK_START_READY",
    TRACE_TASK_STOP_READY : "TRACE_TASK_STOP_READY",
    TRACE_TASK_CREATE : "TRACE_TASK_CREATE",
    TRACE_START : "TRACE_START",
    TRACE_STOP : "TRACE_STOP",
    TRACE_DELAY_UNTIL : "TRACE_DELAY_UNTIL",
    TRACE_ISR_ENTER : "TRACE_ISR_ENTER",
    TRACE_ISR_EXIT : "TRACE_ISR_EXIT",
    TRACE_ISR_EXIT_TO_SCHEDULER : "TRACE_ISR_EXIT_TO_SCHEDULER",
    TRACE_DELAY : "TRACE_DELAY",
    TRACE_TIME_ZERO : "TRACE_TIME_ZERO",
    TRACE_EVT_GROUP_WAIT: "TRACE_EVT_GROUP_WAIT",
    TRACE_EVT_GROUP_SYNC: "TRACE_EVT_GROUP_SYNC",
    TRACE_MUTEX_CREATE: "TRACE_MUTEX_CREATE",
    TRACE_MUTEX_TAKE: "TRACE_MUTEX_TAKE",
    TRACE_MUTEX_GIVE: "TRACE_MUTEX_GIVE",
    TRACE_JLDJOB_FINISHED: "TRACE_JLDJOB_FINISHED",
    TRACE_EVENTS_LOST: "TRACE_EVENTS_LOST"
}

"""
Word after the last event in SRAM/PSRAM trace buffers.
"""
END_MARKER = 0xaabbccdd

"""
Limits used to detect corrupt words in the event stream. A time delta above MAX_DELTA_US is
treated as corruption: with IRQ tracing the tick ISR alone gives an event every few ms.
Task names are at most MAX_NAME_WORDS words (configMAX_TASK_NAME_LEN is 16 on the Pico).
"""
MAX_DELTA_US = 5000000
MAX_NAME_WORDS = 16

"""
Task ID we use for the scheduler
"""
schedulerId = 100

"""
To assign different colors to tasks we use an index into the taskColors array and increment the index
every time a color is assigned to a task. The index wraps around if it reaches the end.
"""
taskColorIndex = 0
taskColors = [(100, 237, 157), (100, 143, 237), (212, 237, 76), (237, 123, 100), (141, 100, 237), (226, 149, 120), (0, 109, 119)]

def getTaskColor(taskId):
    """
    Function returns the task colors for the trace. The colors are selected in sequence from the list, wrapping around when the end of the list is reached. 
    """
    global taskColorIndex
    
    if taskId < schedulerId:
        c = (203, 255, 168)
    elif schedulerId <= taskId <= schedulerId + 100:
        c = (61, 61, 61)
    else:
        c = taskColors[taskColorIndex]
        taskColorIndex = (taskColorIndex + 1) % len(taskColors)

    colorString = '#%02X%02X%02X' % (c[0],c[1],c[2])
    return colorString

def parseTraceFiles(gui, numCores):
    """
    Main function that is called from the GUI to read the trace files from the target device.
    To not block the GUI, this is done in a separate thread.
    """
    global taskColorIndex
    global enable_event_print

    #enable_event_print = gui.printEvents_var.get()

    taskColorIndex = 0      # Reset the task color index, so we always start with the same task color assignments.
    HelperFunctions.startWorker(gui, lambda g: parser_thread(g, numCores), gui.loadingFinished)

def parser_thread(gui, numCores):
    """
    Thread to parse the trace buffers. The trace events are then converted to tasks, jobs and execution segments.
    Errors are reported as one line in the output, with the traceback in parse_error.log in the recording folder.
    """
    folder = gui.viewingFolder
    mode = HelperFunctions.getTargetName(gui)
    try:
        return loadRecording(gui, numCores)
    except TraceInconsistency as e:
        HelperFunctions.reportError("Trace data inconsistent", e, folder, "parse_error.log",
            "Events were probably lost or corrupted (" + mode + "). Set strict = False in the config to load the trace with repairs.",
            context=describeRecording(folder, mode), showType=False)
    except Exception as e:      # Any other parser failure: one line for the user, the traceback in the log
        HelperFunctions.reportError("Parsing failed", e, folder, "parse_error.log",
            "This is a bug in RT-Trace or a damaged recording (" + mode + "). Please report it with the recording folder.",
            context=describeRecording(folder, mode))
    return None

def describeRecording(folder, mode):
    """
    Context for error logs: platform, files and what the parser found so far.
    """
    lines = ["Recording: " + str(folder), "Platform: " + str(mode)]
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.startswith("raw_buffer"):
            lines.append(name + ": " + str(os.path.getsize(os.path.join(folder, name))) + " bytes")
    report = lastReport or {}
    lines.append("Lost events: " + str(report.get('eventsLost', 0)) + ", corrupt stream positions: " + str(len(report.get('desyncs', [])))
                 + ", repairs: " + str(sum(repairs.values())))
    for w in report.get('warnings', []):
        lines.append("Warning: " + w)
    return "\n".join(lines)

def loadRecording(gui, numCores):
    bufferPaths = []
    
    # Get the tick id for each core from the config file.
    if gui is not None:
        #configName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')    # Get the configuration name
        configName = "general"
        config = HelperFunctions.readConfig()
        tickIds = [int(x) for x in config.get(configName,'tickId').split(",")]
        global enable_entry_print
        enable_entry_print = config.getboolean(configName, 'print_events', fallback=False)
        strict = config.getboolean(configName, 'strict', fallback=False)
        fixedBuffers = not HelperFunctions.getTargetName(gui).endswith('RTT')    # RTT streams the events, so there is no fixed buffer that can fill up.
    else:
        tickIds = [15, 42]
        fixedBuffers = True
        strict = False

    for c in range(0,numCores):
        filename = os.path.abspath(os.path.join(gui.viewingFolder, 'raw_buffer' + str(c)))

        bufferPaths.append(Path(filename + ".txt"))
        if not bufferPaths[-1].is_file():
            HelperFunctions.printState("ERROR missing file", info=str(bufferPaths[-1]) + " does not exist. Record the trace again or select another recording.")
            return None

    allBuffers = []
    core = 0
    for buffer in bufferPaths:
        with open(buffer, "rb") as fh:
            traceBuffer = bytearray(fh.read())
        allBuffers.append(traceBuffer)
        
        HelperFunctions.printState("Loaded", info=buffer.name + " (" + str(len(traceBuffer)) + " bytes)")
        if config.getboolean(configName, 'print_hexdump', fallback=False):
            HelperFunctions.hexdump(traceBuffer, base_addr=int(config.get(configName, "buffer"+str(core), fallback="0x00000000"),16))
        core = core + 1

    if sum(len(b) for b in allBuffers) == 0:
        HelperFunctions.printState("ERROR empty recording", info="The trace buffers in " + str(gui.viewingFolder) + " are empty. Check that the program calls trace_init() and record again.")
        return None

    eventFilePath = os.path.abspath(os.path.join(gui.viewingFolder, 'events.txt'))
    tasks = parser(allBuffers, eventFilePath, tickIds, numCores, fixedBuffers, strict)    # Parse the content of the trace buffers

    # If this was called from the GUI, enable the buttons and update the GUI
    if gui is not None:
        DataExport.exportData(gui, tasks)

    return tasks

"""
What the last call of parser() found besides the tasks: repaired inconsistencies, lost events,
skipped corrupt stream positions and warnings. Used by the tests and the summary in the output.
"""
lastReport = {}

def newReport():
    return {'repairs': {}, 'eventsLost': 0, 'lostGaps': 0, 'desyncs': [], 'warnings': []}

def warn(report, text):
    report['warnings'].append(text)
    HelperFunctions.printState("Warning", info=text)

def parser(buffers, eventFilePath, tickIds, numCores, fixedBuffers=True, strict=False):
    """
    Function parses a variable number of trace buffers.
    Trace events are then converted to tasks, jobs and execution segments.
    The function returns an array with all trace tasks.
    fixedBuffers is False for streamed traces (RTT), which are not cut where a buffer is full.
    With strict, inconsistent trace data raises TraceInconsistency instead of being repaired.
    """
    global lastReport
    HelperFunctions.printHeader("parsing files")

    report = newReport()
    lastReport = report
    resetRepairs(strict)

    events = []
    parseTraceEvents(events, buffers, fixedBuffers, report)       # Parse the raw events from the trace files of each core

    allTasks = []
    allTasks = extractTraceInfo(events, eventFilePath, tickIds, numCores, report)     # Parse all trace tasks from the event trace (afterwards we have trace tasks, jobs and execution segments). 
    tasks = []
    
    for task in allTasks:                   # Some tasks might be created in the trace but never execute. We exclue those here. 
        if len(task.jobs) != 0:
            tasks.append(task)

    summariseRepairs(report)
    return tasks

def summariseRepairs(report):
    """
    Prints one line per kind of repaired inconsistency, with its first occurrence.
    """
    report['repairs'] = dict(repairs)
    total = sum(repairs.values())
    if total == 0:
        return
    hint = "events were lost or corrupted" if report['eventsLost'] > 0 or len(report['desyncs']) > 0 or report.get('droppedSuspected') \
        else "events were probably lost"
    warn(report, "Repaired " + str(total) + " inconsistencies in the trace data (" + hint + "). The schedule around them may be inaccurate:")
    for key, count in repairs.most_common():
        HelperFunctions.printState("  " + str(count) + "x", info=key + " (first " + repairExamples[key] + ")")

def extractTraceInfo(events, eventFilePath, tickIds, numCores, report=None):
    """ 
    Extract trace information from the raw trace events. So we have information on task-level.
    """
    if report is None:
        report = newReport()
    tasks = []

    traceStart = None

    '''
    There are several ways how the reference time can be set for the trace. 
    TRACE_START -> Sets t=0 to the timestamp of the event.
    TRACE_TIME_ZERO -> Sets t=0 to the timestamp of the first tick before this event. 
    else -> Timegrid is positioned to match the ticks best. 
    '''

    # Check if there is a TRACE_START event. If so, set the t=0 to this timestamp. 
    for evt in events:
        if evt.get('type') == TRACE_START:
            traceStart = evt.get('ts')

    # Check if there is a TRACE_TIME_ZERO. If so, set trace start (i.e. t=0) to the first tick before the event.
    if traceStart is None:
        for evt in events:
            if evt.get('type') == TRACE_TIME_ZERO:
                core = evt.get('core')
                
                index = events.index(evt)
                tmpList = events[0:index]
                for tve in reversed(tmpList):
                    if tve.get('type') == TRACE_ISR_ENTER:
                        if tve.get('irqId') == 15:
                            traceStart = tve.get('ts')
                            break
                break

    if traceStart is None:
        traceStart = getTickStart(events, tickIds[0])

    checkTickIntervals(events, tickIds[0], report)

    # Create tasks to represent the scheduler, tick ISR for each core.
    if numCores == 1:
        # Exclude the core in the name if there is only one core
        #tasks.append(TraceTask(tickIds[0], "Tick", None, getTaskColor(tickIds[0])))
        tasks.append(TraceTask(schedulerId, "Scheduler", None, getTaskColor(schedulerId)))
    else:
        coreId = 0
        for id in range(0,numCores):
            #tasks.append(TraceTask(tickIds[id], "Tick Core " + str(coreId), None, getTaskColor(id)))
            tasks.append(TraceTask(schedulerId + coreId, "Scheduler Core " + str(coreId), None, getTaskColor(schedulerId + coreId)))
            coreId = coreId + 1

    for irq in range(0,len(tickIds)):
        for i in range(0,numCores):
            id = irq + (len(tickIds)*i)# tickIds[irq] + (len(tickIds)*i)
            tasks.append(TraceTask(id, "IRQ_" + str(tickIds[irq]) + "_C" + str(i), None, getTaskColor(id)))

    # All other tasks are parsed from the trace events. 
    for evt in events:
        if evt.get('type') is TRACE_TASK_CREATE:    # Parse all task create events and create trace tasks for each.
            id = evt.get('taskId')
            prio = evt.get('priority')
            #name = evt.get('name').split('\\')[0]
            name = evt.get('name').split('\x00', 1)[0]
            tmpTask = TraceTask(id, name, prio, getTaskColor(id))
            tasks.append(tmpTask)
        if evt.get('type') is TRACE_TASK_START_READY:   # We set the trace time t=0 to the first task ready event (if no TRACE_TIME_ZERO event was found).
            if traceStart is None:
                traceStart = evt.get('ts')  # By convention we set the start of the first task to t=0

    # Parse all mutex create events to map each mutex ID to a letter (max. 26 mutexes).
    mutex_id_to_letter: dict[int, str] = {}
    for evt in events:
        if evt.get('type') == TRACE_MUTEX_CREATE:
            id = evt.get('mutexId')
            map_mutex_id(mutex_id_to_letter, id)

    eventFile = open(eventFilePath, 'w')
    
    # Prepare all events sorted by time to be processed by the state machine parser.
    allEvents = []

    # Add ISR enter events that were lost. An ISR_EXIT whose core has no open ISR lost its ISR_ENTER
    # (or the trace started inside the ISR). The ISR is short, so the enter is placed right before the
    # exit, after the previous event of that core. The IRQ is assumed to be the last one seen on the core.
    # Nested ISRs are tracked as a stack of IRQ ids per core. An IRQ cannot interrupt itself, so an
    # ISR_ENTER of an IRQ that is on the stack means its ISR_EXIT was lost (parseIrq repairs that).
    lastEvtOnCore = {}
    isrStack = {}
    lastIrqOnCore = {}
    for evt in events:
        type = evt.get('type')
        core = evt.get('core')
        stack = isrStack.setdefault(core, [])
        if type == TRACE_ISR_EXIT or type == TRACE_ISR_EXIT_TO_SCHEDULER:
            if len(stack) == 0:
                prevTs = lastEvtOnCore[core].get('ts') if core in lastEvtOnCore else evt.get('ts') - 1
                irqId = lastIrqOnCore.get(core, tickIds[0])
                ts = max(prevTs, evt.get('ts') - 1)
                inconsistent("IRQ_" + str(irqId) + "_C" + str(core), ts, core, "exit without enter (enter added)")
                allEvents.append({'type':TRACE_ISR_ENTER, 'ts':ts, 'core':core, 'irqId':irqId, 'added':True})
            else:
                stack.pop()
        elif type == TRACE_ISR_ENTER:
            irqId = evt.get('irqId')
            if irqId in stack:
                del stack[stack.index(irqId):]
            stack.append(irqId)
            lastIrqOnCore[core] = irqId

        allEvents.append(evt)
        lastEvtOnCore[core] = evt

    sortedEvents = sorted(allEvents, key=lambda d: d['ts'])    # Sort all events of this task by timestamp. Since timestamps on cores are synchronised this can be done. Attention, if the platform does not support this!

    for evt in sortedEvents:
        if traceStart is not None:
            evt['ts'] = evt['ts'] - traceStart
        eventFile.write('\tts: ' + "%06.3f" % (evt.get('ts')/1000) + "ms\t" + eventMap.get(evt.get('type'), str(evt.get('type'))) + ":  " + str(evt) + "\n")

    executionParser(sortedEvents, tasks, tickIds, mutex_id_to_letter, numCores)
   #->  smParser(traceStart, sortedEvents, tasks, len(tickIds))

    eventFile.close()
    HelperFunctions.printState("Wrote events to", info=eventFilePath)

    return tasks

def map_mutex_id(mutex_map, mutex_id: int) -> str | None:
    """
    Function maps a mutex id to letters A to Z. If 26 IDs are already mapped, the function returns NULL.
    """
    # Validate as an unsigned 32-bit integer.
    if not isinstance(mutex_id, int):
        raise TypeError("mutex_id must be an integer")

    # Return the existing mapping if this ID was seen before.
    if mutex_id in mutex_map:
        return mutex_map[mutex_id]

    # All 26 letters are already assigned.
    if len(mutex_map) >= 26:
        return None

    letter = chr(ord("A") + len(mutex_map))
    mutex_map[mutex_id] = letter
    return letter

def executionParser(sortedEvents, tasks, tickIds, mutex_id_to_letter, numCores):

    # This hardcodes that there are 2 cores, should be generalized!
    core1Flag = False

    for task in tasks:

        if "idle" in task.name.lower():
            parseIdleTask(sortedEvents, task)
        elif 100 <= task.id <= len(tickIds) + 100:    # scheduler IDs
            parseScheduler(sortedEvents, task)
        elif task.id <= len(tickIds) * numCores: #in tickIds:
            parseIrq(sortedEvents, task)
        else:
            parseTask(sortedEvents, task, mutex_id_to_letter, tickIds[0])
            for job in task.jobs:
                for execInterval in job.execIntervals:
                    if execInterval.core == 1:
                        core1Flag = True

    if core1Flag == False:  # No user task executes on core 1
        # Remove scheduler core 1 and tick core 1 from the data (since they don't affect the schedule on core 0 and there are no user tasks on core 1)
        toRemove = []
        for task in tasks:
            if "idle1" in task.name.lower():
                toRemove.append(task)
            elif task.id == tickIds[1]:
                toRemove.append(task)
            elif task.id == schedulerId + 1:
                toRemove.append(task)
        tasks[:] = [x for x in tasks if x not in toRemove]
        
def parseScheduler(sortedEvents, schedulerTask):

    coreId = schedulerTask.id - 100   # For the scheduler task, the task id is equal to the core id

    for evt in sortedEvents:
        type = evt.get('type')
        core = evt.get('core')
        ts = evt.get('ts')

        if core == coreId:
            if type == TRACE_ISR_EXIT_TO_SCHEDULER:
                if schedulerTask.currentJob is None:    # Otherwise the scheduler is already running
                    schedulerTask.newJob(ts, None)
                    schedulerTask.startExec(ts, core, ExecutionType.EXECUTE)
            elif type == TRACE_TASK_STOP_EXEC:
                # Here we need to distinguish if this was triggered by a task that finished executing between ticks or not.
                if schedulerTask.currentJob is not None:
                    pass    # Do nothing since the scheduler is already running.
                else:
                    schedulerTask.newJob(ts, None)
                    schedulerTask.startExec(ts, core, ExecutionType.EXECUTE)
            elif type == TRACE_IDLE or type == TRACE_TASK_START_EXEC:
                if schedulerTask.currentJob is not None:
                    schedulerTask.stopExec(ts)
                    schedulerTask.finishJob()

def parseIdleTask(sortedEvents, task):

    coreSuffix = task.name[len("IDLE"):]
    coreId = int(coreSuffix) if coreSuffix.isdigit() else 0    # FreeRTOS names the idle task "IDLE" without a core number on a single core
    lastExecTask = None

    for evt in sortedEvents:
        type = evt.get('type')
        core = evt.get('core')
        ts = evt.get('ts')

        if type == TRACE_IDLE and coreId == core:
            # IDLE is a state: it runs until another task starts or an ISR interrupts it. With preemption
            # off, the idle task yields to itself and older firmware emits TRACE_IDLE on every yield.
            if task.currentJob is None:
                task.newJob(ts, None)
                task.startExec(ts, core, ExecutionType.EXECUTE)
            elif task.currentJob.activeInterval is None:
                task.startExec(ts, core, ExecutionType.EXECUTE)

        if task.currentJob is not None:
            if type == TRACE_TASK_START_EXEC:
                if core == coreId:
                    #print(f"IDLE finished at t={ts}")
                    task.stopExec(ts)   
                    task.finishJob()
                    lastExecTask = None

        if task.currentJob is not None:
            if type == TRACE_ISR_ENTER:
                if core == coreId:
                    #print(f"IDLE finished at t={ts}")
                    task.stopExec(ts)
                    task.finishJob()
                    lastExecTask = task

        if type == TRACE_ISR_EXIT:
             if core == coreId:
                 if lastExecTask == task:
                     task.newJob(ts, None)
                     task.startExec(ts, core, ExecutionType.EXECUTE)
                 lastExecTask = None

        if type == TRACE_TASK_START_EXEC:
            if core == coreId:
                lastExecTask = None

    if task.currentJob is not None:
        task.stopExec(ts)
        task.finishJob()

def nextTaskEvent(sortedEvents, i, taskId):
    """
    Returns the type of the next START_READY or START_EXEC event of the task after index i, or None.
    """
    for j in range(i + 1, len(sortedEvents)):
        followingEvent = sortedEvents[j]
        if followingEvent.get('taskId') == taskId and followingEvent.get('type') in (TRACE_TASK_START_READY, TRACE_TASK_START_EXEC):
            return followingEvent.get('type')
    return None

def releaseAtTick(tickTs, tick, ts):
    """
    Timestamp of the tick with the given tick count, as a release time for a job that starts at ts.
    tickTs[n] is the tick ISR that increments the tick count to n. If that tick is not in the trace
    (the trace starts late or ticks were lost), the last tick before ts is used.
    """
    if tick is not None and 0 <= tick < len(tickTs) and tickTs[tick] <= ts:
        release = tickTs[tick]
    else:
        release = max([t for t in tickTs[-2:] if t <= ts], default=ts)
    return max(release, 0)

def parseTask(sortedEvents, task, mutex_id_to_letter, tickId):
    """
    Parses the execution of a single task.
    """
    
    finishJob = False           # Flag to indicate that the job is about to finish.
    finishJobOnEventGroup = False   # The finish flag comes from an event group wait/sync, which is traced on entry, before the task blocks.
    delayKind = None            # TRACE_DELAY_UNTIL or TRACE_DELAY if the finish flag comes from a delay
    delayWake = None            # Tick count to wake up at of the last vTaskDelayUntil
    deadlineMiss = False        # Flag to indicate that a deadline was missed.
    startExecCore = None        # Task started to execute on this core.
    lastExecTask = None         # Keep track of the last task started on the core.
    isrDepth = {}               # Nesting depth of ISRs on each core
    pausedByIsr = False         # The task was running when the outermost ISR started
    jldJobNextRelDelta = None   # Used for JLD jobs only, indicates that the next job is released automatically relative to the previous release.
    tickTs = []                 # We keep track if tick timestamps to be able to handle deadline misses.
    tickTs.append(0)            # By default the first tick appears at t=0

    for i, evt in enumerate(sortedEvents):
        type = evt.get('type')
        taskId = evt.get('taskId')
        core = evt.get('core')
        ts = evt.get('ts')

        next_evt = sortedEvents[i + 1] if i + 1 < len(sortedEvents) else None

        if type == TRACE_TASK_START_READY:
            if task.id == taskId:
                if task.currentJob == None: #If the job was blocked it might get the ready event again.
                    #print(f"New Job released at {ts}")
                    if ts < 0:
                        ts = 0
                    task.newJob(ts, None)

        if type == TRACE_TASK_START_EXEC:
            isrDepth[core] = 0      # Context switches run outside of ISRs
            #if task.currentJob is not None: # If there is an active job, and this event if from the same core, remember the task id
            if core == startExecCore:
                lastExecTask = taskId

            if task.id == taskId:
                #print(f"Start execution at {ts} on core {core}")

                # if task.currentJob is not None:
                #     #if task.currentJob.activeInterval is not None:
                #     if next_evt.get('type') == TRACE_DEADLINE_MISS and next_evt.get('taskId') == task.id: 
                #         task.newJob(ts, None)

                task.startExec(ts, core, ExecutionType.EXECUTE)
                startExecCore = core
                lastExecTask = taskId

        if type == TRACE_TASK_STOP_EXEC:
            if task.id == taskId:
                #print(f"Stop execution at {ts}")
                if task.currentJob is not None:
                    if task.currentJob.activeInterval is not None:
                        task.stopExec(ts)
                    startExecCore = None
                    nextOwn = nextTaskEvent(sortedEvents, i, task.id) if finishJob else None
                    if finishJob and finishJobOnEventGroup:
                        # The task may be preempted (or not block at all) after the wait/sync. The job only ends
                        # if the task becomes ready again before it executes next.
                        if nextOwn is not None:
                            finishJob = nextOwn == TRACE_TASK_START_READY
                        finishJobOnEventGroup = False
                    if finishJob == True:
                        finishJob = False
                        #print(f"Finish job at {ts}")
                        task.finishJob(ts, core)

                        # A delay that does not block (the wake time has passed, or vTaskDelay(0)) only yields.
                        # The task stays ready, so its next job starts without a READY event.
                        if delayKind == TRACE_DELAY_UNTIL and (nextOwn == TRACE_TASK_START_EXEC or (nextOwn is None and deadlineMiss)):
                            task.newJob(releaseAtTick(tickTs, delayWake, ts), None, core)     # Released at the tick it waited for
                        elif delayKind == TRACE_DELAY and nextOwn == TRACE_TASK_START_EXEC:
                            task.newJob(ts, None, core)
                        deadlineMiss = False
                        delayKind = None

                        if jldJobNextRelDelta != None:
                            """
                            This is only called for the custom release mechanism used in the JLD implementation, since this is not tick-based and tasks might not become ready at their periodic release times. 
                            """
                            lastJob = task.jobs[-1] # Get the last job
                            task.newJob(lastJob.releaseTime + jldJobNextRelDelta, None) 
                            jldJobNextRelDelta = None

        if type == TRACE_EVT_GROUP_SYNC or type == TRACE_EVT_GROUP_WAIT:
            if task.id == taskId:
                finishJob = True
                finishJobOnEventGroup = True
                delayKind = None

        elif type == TRACE_ISR_ENTER:
            # The task pauses for the outermost ISR on its core and resumes when that ISR returns to it.
            # After an exit to the scheduler the task stays paused until the next context switch, also
            # if another ISR runs before it.
            depth = isrDepth.get(core, 0)
            isrDepth[core] = depth + 1
            if depth == 0 and startExecCore == core and lastExecTask == task.id:
                pausedByIsr = task.currentJob is not None and task.currentJob.activeInterval is not None
                if pausedByIsr:
                    #print(f"Stop execution due to ISR at {ts}")
                    task.stopExec(ts)
            irqId = evt.get('irqId')
            if irqId == tickId:
                tickTs.append(ts)
        elif type == TRACE_ISR_EXIT or type == TRACE_ISR_EXIT_TO_SCHEDULER:
            isrDepth[core] = max(0, isrDepth.get(core, 0) - 1)
            if type == TRACE_ISR_EXIT and isrDepth[core] == 0 and pausedByIsr and startExecCore == core and lastExecTask == task.id:
                task.startExec(ts, core, ExecutionType.EXECUTE)
                pausedByIsr = False

        elif type == TRACE_DELAY_UNTIL:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Delay Until called at {ts}")
                    finishJob = True
                    finishJobOnEventGroup = False
                    delayKind = TRACE_DELAY_UNTIL
                    delayWake = evt.get('timeToWake')
                    deadlineMiss = evt.get('deadlineMiss') == True
        
        elif type == TRACE_DELAY:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Delay called at {ts}")
                    finishJob = True
                    finishJobOnEventGroup = False
                    delayKind = TRACE_DELAY

        elif type == TRACE_MUTEX_TAKE:
            if startExecCore == core:
                if lastExecTask == task.id:
                    letterId = map_mutex_id(mutex_id_to_letter, evt.get('mutexId'))
                    task.mutexTake(ts, evt.get('mutexId'), letterId)

        elif type == TRACE_MUTEX_GIVE:
            if startExecCore == core:
                if lastExecTask == task.id:
                    task.mutexGive(ts, evt.get('mutexId'))

        elif type == TRACE_JLDJOB_FINISHED:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Delay Until called at {ts}")
                    finishJob = True
                    finishJobOnEventGroup = False
                    jldJobNextRelDelta = evt.get('period_us')
            
    # In case there are unfinished jobs, we handle them here.
    if task.currentJob is not None:
        if task.currentJob.activeInterval is not None:
            task.stopExec(ts)
        task.finishJob()

def parseIrq(sortedEvents, irqTask):
    """
    This function parses the execution of a specific IRQ.
    We assume that each IRQ-job runs to completion. In case an IRQ is interrupted by
    a higher-priority IRQ, the execution is shown as multiple jobs.
    """

    enterCore = None
    afterEnterTs = None     # Timestamp of the first event on the core after the open ISR_ENTER

    # For each IRQ, we have one task on each core. We need to get the core and the IRQ number from the task name. 
    irqTaskCore = int(irqTask.name.rsplit("_C", 1)[1])
    irq_number = int(irqTask.name.split("_")[1])

    for evt in sortedEvents:
        type = evt.get('type')
        core = evt.get('core')
        if core != irqTaskCore:
            continue
        ts = evt.get('ts')

        if type == TRACE_ISR_ENTER and evt.get('irqId') == irq_number:
            if irqTask.currentJob is not None:
                # The ISR_EXIT of the previous run was lost. ISRs are short, so it ended before the next event on its core.
                inconsistent(irqTask.name, ts, core, "entered again without exit (exit added)")
                endTs = afterEnterTs if afterEnterTs is not None else ts
                irqTask.stopExec(endTs)
                irqTask.finishJob(endTs, core)
            irqTask.newJob(ts, None, core)
            irqTask.startExec(ts, core, ExecutionType.EXECUTE)
            enterCore = core
            afterEnterTs = None
            continue

        if enterCore is not None and afterEnterTs is None:
            afterEnterTs = ts

        if type == TRACE_ISR_EXIT or type == TRACE_ISR_EXIT_TO_SCHEDULER:
            if core == enterCore:
                irqTask.stopExec(ts)
                irqTask.finishJob(ts, core)
                enterCore = None

def parseTraceEvents(events, buffers, fixedBuffers=True, report=None):
    """
    This function converts the trace buffer of the traget into processable trace events.
    As buffers of different cores can contain events up to different timestamps, this function
    gets the events of each core's trace buffer up to the earliest timestamp of the last event on 
    any core. Otherwise, trace data can be inconsistent (for example under FreeRTOS new task instances 
    become ready on core 0, even if they are mapped to a different core.)
    """
    if report is None:
        report = newReport()
    coreId = 0

    bufferEvents = []
    fullBuffers = []
    
    for buffer in buffers:
        bufferEvents.append([])

        parser = EventParser(buffer)

        lost = 0
        lostGaps = 0
        while True:
            evt = parser.read_event(coreId)
            if evt is None:
                break
            if evt.get('type') == TRACE_EVENTS_LOST:
                lost += evt.get('count')
                lostGaps += 1
            bufferEvents[-1].append(evt)
        HelperFunctions.printState("Core " + str(coreId), info=str(len(bufferEvents[-1])) + " events")
        for offset, words in parser.desyncs:
            report['desyncs'].append((coreId, offset, words))
        if len(parser.desyncs) > 0:
            skipped = sum(w for _, w in parser.desyncs)
            first = parser.desyncs[0][0]
            warn(report, "Core " + str(coreId) + ": skipped " + str(skipped) + " corrupt words at " + str(len(parser.desyncs))
                 + " positions in the event stream (first at byte " + str(first) + "). Events there are lost.")
        if lost > 0:
            report['eventsLost'] += lost
            report['lostGaps'] += lostGaps
            warn(report, "Core " + str(coreId) + ": the target lost " + str(lost) + " events in " + str(lostGaps)
                 + " gaps because the RTT buffer was full. Fewer events (IDLE trace, printf, fast IRQs) or the SRAM mode avoid this.")
        if parser.isFull() and len(bufferEvents[-1]) > 0:
            fullBuffers.append(coreId)
        coreId = coreId + 1

    # Streamed (RTT) recordings have no buffer that fills up, but the streams of the cores are read one after
    # the other, so they end at slightly different times. Treat the streams that run until the end of the
    # recording like full buffers. A core whose last event is much earlier was idle, not cut off.
    if not fixedBuffers:
        lastTs = {c: evts[-1].get('ts') for c, evts in enumerate(bufferEvents) if len(evts) > 0}
        if len(lastTs) > 0:
            recordingEnd = max(lastTs.values())
            fullBuffers = [c for c, ts in lastTs.items() if ts >= recordingEnd - RTT_END_WINDOW_US]

    # A core whose buffer filled up stops recording, while the other cores go on. Events of the other cores
    # after that point lack their counterparts (for example, the tick on core 0 makes the tasks of core 1 ready),
    # so the trace is cut where the first full buffer ends.
    endTs = None
    if len(fullBuffers) > 0:
        endCore = min(fullBuffers, key=lambda c: bufferEvents[c][-1].get('ts'))
        endTs = bufferEvents[endCore][-1].get('ts')

    dropped = 0
    for evts in bufferEvents:
        for evt in evts:
            if endTs is None or evt.get('ts') <= endTs:
                events.append(evt)
            else:
                dropped = dropped + 1

    if dropped > 0:
        HelperFunctions.printState("Trace cut", info="at " + "%.1f" % (endTs / 1000) + " ms, where the recording of core " + str(endCore) + " ends. " + str(dropped) + " later events of the other cores are ignored")

class EventParser:
    """
    This class describes the event parser. It is used to extract all trace events from a raw buffer.
    """
    
    def __init__(self, inBuffer):
        """
        Initialization of the event parser.
        The object gets a trace buffer in bytearray format as argument.
        """
        self.maxBytes = len(inBuffer)
        self.raw = inBuffer
        self.buffer = file = io.BytesIO(inBuffer)
        self.time = 0
        self.bytesRead = 0   
        self.ended = False      # The end marker of an SRAM/PSRAM buffer was read
        self.desyncs = []       # [byte offset, number of skipped words] of each corrupt stretch

    def isFull(self):
        """
        True if the events reach the end of the buffer, so the target ran out of buffer space.
        The largest event (task create with its name) is shorter than 40 bytes.
        """
        return self.maxBytes - self.bytesRead < 40

    def printBuffer(self):
        """
        A helper function to print the content of the trace buffer in hex-format to stdout.
        """
        print("".join([f"\\x{byte:02x}" for byte in self.buffer.read(4)]))
        for i in range(1, 100, 1):
            print("".join([f"\\x{byte:02x}" for byte in self.buffer.read(16)]))
        print("done")

    def readBytes(self, len):
        """
        Function reads one byte from the trace buffer
        """
        if (self.maxBytes - self.bytesRead) >= len:
            b = self.buffer.read(len)
            self.bytesRead = self.bytesRead + len
            #print("".join([f"\\x{byte:02x}" for byte in b]))
            return b
        else:
            return None
    
    def readInteger(self):
        """
        Function reads an integer (4 bytes) from the trace buffer.
        """
        b = self.readBytes(4)

        if b is not None:
            return int.from_bytes(b, byteorder='little', signed=False)
        else:
            return None

    def seek(self, pos):
        self.buffer.seek(pos)
        self.bytesRead = pos

    def skipWord(self, pos):
        """
        Skips the corrupt word at byte pos and continues with the next word.
        Consecutive skipped words count as one corrupt stretch.
        """
        if len(self.desyncs) > 0 and self.desyncs[-1][0] + 4 * self.desyncs[-1][1] == pos:
            self.desyncs[-1][1] += 1
        else:
            self.desyncs.append([pos, 1])
        self.seek(pos + 4)

    def read_event(self, coreId):
        """
        Function reads the next event of the trace buffer. Returns None at the end of the data.
        A word that is not a valid event header (corrupted or misaligned data) is skipped, and the
        parser continues with the next word, so one damaged event does not cut the trace.
        """
        while not self.ended:
            start = self.bytesRead
            b = self.readBytes(4)
            if b is None:
                return None
            word = int.from_bytes(b, byteorder='little', signed=False)
            if word == END_MARKER:
                self.ended = True
                return None
            eventId = word >> 24
            deltaTime = word & 0xffffff
            if eventId in eventMap and deltaTime <= MAX_DELTA_US:
                time = self.time
                evt = self.decodeEvent(coreId, eventId, deltaTime)
                if evt is not None and evt != "invalid":
                    return evt
                if evt is None:
                    return None     # The data ends inside the event
                self.time = time
            elif word == 0 and not any(self.raw[start:]):
                return None         # Zero padding up to the end of the buffer
            self.skipWord(start)
        return None

    def decodeEvent(self, coreId, eventId, deltaTime):
        """
        Reads the payload of an event. Returns the event, None if the data ends inside it,
        or "invalid" if the payload can't be valid.
        """
        self.time = self.time + deltaTime # Compute the current timestamp in absolute time
        
        if eventId == TRACE_IDLE:
            entryPrint("[t=" + str(self.time) + "us] TRACE_IDLE" + " Core: " + str(coreId))
            evt = {'type':TRACE_IDLE, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_TASK_START_EXEC:
            taskId = self.readInteger()
            if taskId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_TASK_START_EXEC  -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_START_EXEC, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_STOP_EXEC:
            taskId = self.readInteger()
            if taskId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_TASK_STOP_EXEC   -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_STOP_EXEC, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_START_READY:
            taskId = self.readInteger()
            if taskId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_TASK_START_READY -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_START_READY, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_STOP_READY:
            taskId = self.readInteger()
            if taskId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_TASK_STOP_READY  -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_STOP_READY, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_CREATE:
            taskId = self.readInteger()
            if taskId is None:
                return None
            strLen = self.readInteger()
            if strLen is not None and strLen > MAX_NAME_WORDS:
                return "invalid"
            if strLen is None:
                return None
            priority = self.readInteger()
            if priority is None:
                return None
            name = self.readBytes(strLen * 4)
            if name is None:
                return None
            name = name.decode('UTF-8', errors='replace')
            entryPrint("[t=" + str(self.time) + "us] TRACE_TASK_CREATE      -> Task: " + name + " ID: " + str(taskId) + " with priority: " + str(priority) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_CREATE, 'ts':self.time, 'core':coreId, 'taskId':taskId, 'name':name, 'priority':priority}

        elif eventId == TRACE_START:
            entryPrint("[t=" + str(self.time) + "us] TRACE_START" + " Core: " + str(coreId))
            evt = {'type':TRACE_START, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_STOP:
            entryPrint("[t=" + str(self.time) + "us] TRACE_STOP" + " Core: " + str(coreId))
            evt = {'type':TRACE_STOP, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_DELAY_UNTIL:
            rawValue = self.readInteger()

            deadlineMiss = bool(rawValue & (1 << 31))
            timeToWake = rawValue & ((1 << 31) - 1)

            if timeToWake is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_DELAY_UNTIL      -> timeToWake: " + str(timeToWake) + " ms Deadline Miss: " + str(deadlineMiss) + " Core: " + str(coreId))
            evt = {'type':TRACE_DELAY_UNTIL, 'ts':self.time, 'core':coreId, 'timeToWake':timeToWake, 'deadlineMiss':deadlineMiss}

        elif eventId == TRACE_ISR_ENTER:
            irqId = self.readInteger()
            if irqId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_ISR_ENTER        -> irqId: " + str(irqId) + " Core: " + str(coreId))
            evt = {'type':TRACE_ISR_ENTER, 'ts':self.time, 'core':coreId, 'irqId':irqId}

        elif eventId == TRACE_ISR_EXIT:
            entryPrint("[t=" + str(self.time) + "us] TRACE_ISR_EXIT" + " Core: " + str(coreId)) 
            evt = {'type':TRACE_ISR_EXIT, 'ts':self.time, 'core':coreId} 

        elif eventId == TRACE_ISR_EXIT_TO_SCHEDULER:
            entryPrint("[t=" + str(self.time) + "us] TRACE_ISR_EXIT_TO_SCHEDULER" + " Core: " + str(coreId)) 
            evt = {'type':TRACE_ISR_EXIT_TO_SCHEDULER, 'ts':self.time, 'core':coreId} 

        elif eventId == TRACE_DELAY:
            delayTime = self.readInteger()
            if delayTime is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_DELAY.           -> delayTime: " + str(delayTime) + " ms Core: " + str(coreId)) 
            evt = {'type':TRACE_DELAY, 'ts':self.time, 'core':coreId, 'delayTime':delayTime} 
        
        elif eventId == TRACE_TIME_ZERO:
            entryPrint("[t=" + str(self.time) + "us] TRACE_TIME_ZERO" + " Core: " + str(coreId)) 
            evt = {'type':TRACE_TIME_ZERO, 'ts':self.time, 'core':coreId} 
        elif eventId == TRACE_EVT_GROUP_WAIT:
            taskId = self.readInteger()
            if taskId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_EVT_GROUP_WAIT -> taskId: " + str(taskId) + " Core: " + str(coreId)) 
            evt = {'type':TRACE_EVT_GROUP_WAIT, 'ts':self.time, 'core':coreId, 'taskId':taskId} 
        elif eventId == TRACE_EVT_GROUP_SYNC:
            taskId = self.readInteger()
            if taskId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_EVT_GROUP_SYNC -> taskId: " + str(taskId) + " Core: " + str(coreId)) 
            evt = {'type':TRACE_EVT_GROUP_SYNC, 'ts':self.time, 'core':coreId, 'taskId':taskId}
        elif eventId == TRACE_MUTEX_CREATE:
            mutexId = self.readInteger()
            if mutexId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_MUTEX_CREATE -> mutexId: " + str(mutexId) + " Core: " + str(coreId)) 
            evt = {'type':TRACE_MUTEX_CREATE, 'ts':self.time, 'core':coreId, 'mutexId':mutexId}
        elif eventId == TRACE_MUTEX_TAKE:
            mutexId = self.readInteger()
            if mutexId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_MUTEX_TAKE -> mutexId: " + str(mutexId) + " Core: " + str(coreId)) 
            evt = {'type':TRACE_MUTEX_TAKE, 'ts':self.time, 'core':coreId, 'mutexId':mutexId}
        elif eventId == TRACE_MUTEX_GIVE:
            mutexId = self.readInteger()
            if mutexId is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_MUTEX_GIVE -> mutexId: " + str(mutexId) + " Core: " + str(coreId)) 
            evt = {'type':TRACE_MUTEX_GIVE, 'ts':self.time, 'core':coreId, 'mutexId':mutexId}
        elif eventId == TRACE_EVENTS_LOST:
            count = self.readInteger()
            if count is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_EVENTS_LOST -> count: " + str(count) + " Core: " + str(coreId))
            evt = {'type':TRACE_EVENTS_LOST, 'ts':self.time, 'core':coreId, 'count':count}
        elif eventId == TRACE_JLDJOB_FINISHED:
            period_us = self.readInteger()
            if period_us is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_JLDJOB_FINISHED.           -> period_us: " + str(period_us) + " us Core: " + str(coreId)) 
            evt = {'type':TRACE_JLDJOB_FINISHED, 'ts':self.time, 'core':coreId, 'period_us':period_us} 
        else:
            evt = "invalid"

        return evt

def checkTickIntervals(events, irqCore0, report):
    """
    Warns if the tick ISR on core 0 is irregular. The firmware before the lost-events marker
    (TRACE_EVENTS_LOST) dropped events silently when the RTT buffer was full. The time delta of a
    dropped event is lost as well, so the trace gets shorter than the recording and ticks come
    too early. Recordings with the marker report lost events in parseTraceEvents instead.
    """
    if report['eventsLost'] > 0:
        return
    ticks = [e.get('ts') for e in events if e.get('type') == TRACE_ISR_ENTER and e.get('irqId') == irqCore0 and e.get('core') == 0]
    if len(ticks) < 10:
        return
    intervals = np.diff(np.asarray(ticks, dtype=np.int64))
    nominal = float(np.median(intervals))
    if nominal <= 0:
        return
    irregular = int(np.sum(np.abs(intervals - nominal) > 0.25 * nominal))
    if irregular > max(3, 0.01 * len(intervals)):
        report['droppedSuspected'] = True
        warn(report, str(irregular) + " of " + str(len(intervals)) + " tick intervals on core 0 differ from " + "%.0f" % nominal
             + " us by more than 25 %. Events were probably dropped or reordered by the target, so times in this trace are not reliable."
             + " Target code before the lost-events marker does this when the RTT buffer is full or an ISR interrupts a trace hook. Rebuild the program with the current RT_Trace target code.")

def getTickStart(events, irqCore0):
    startTime = 0   # Timestamp of the first tick
    tolerance_us = 50

    # Get an array of all tick times on core 0
    tickTimes = []
    for evt in events:
        type = evt.get('type')
        if type == TRACE_ISR_ENTER:
            irqId = evt.get('irqId')
            if irqId == irqCore0:
                ts = evt.get('ts')
                tickTimes.append(ts)

    if len(tickTimes) == 0:
        return None     # No tick in the trace, the caller falls back to the first task release

    # The phase is clear after a few ticks. Limit them, as the error matrix below has 1000 entries per tick.
    ticks = np.asarray(tickTimes[:2000], dtype=np.int64)

    period_us = 1000 # We like to align ticks with a 1ms grid
    phases = np.arange(period_us)
    residues = ticks % period_us

    # Signed circular distance from every residue to every possible phase (-500 to 499)
    errors = ( (residues[:, None] - phases[None, :] + period_us // 2) % period_us - period_us // 2)
    abs_errors = np.abs(errors)

    # Maximise the number of aligned ticks
    inlier_counts = np.sum(abs_errors <= tolerance_us, axis = 0)

    # Minimize clipped error to reduce influence of startup outliers
    clipper_loss = np.sum(np.minimum(abs_errors, tolerance_us), axis=0)

    order = np.lexsort((clipper_loss, -inlier_counts))
    phase_us = int(phases[order[0]])

    # Any origin congruent to phase_us modulo 1000 gives the same alignment.
    # Select the grid point immediately before or equal to the first tick.
    # -1000 since we like t=0 which has no IRQ. 
    t0_us = phase_us + period_us * ((ticks[0] - phase_us) // period_us) - 1000

    return int(t0_us)

def entryPrint(*args, **kwargs):
    global enable_entry_print
    if enable_entry_print:
        return print(*args, **kwargs)
    
if __name__ == "__main__":
    """
    Debugging.
    """
    parser_thread(None, 2)