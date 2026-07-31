from threading import Thread
from pathlib import Path
import io
from TraceTask import *
import os
import HelperFunctions
import configparser
import sys
import numpy as np

"""
Set to true to print events read from the trace buffer.
"""
enable_entry_print = True

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

# Those events are not in the original trace and are created during parsing
TRACE_IDLE_START                = 20
TRACE_IDLE_STOP                 = 21

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
    TRACE_MUTEX_GIVE: "TRACE_MUTEX_GIVE"
}

"""
Task ID we use for the scheduler
"""
schedulerId = 100

"""
To assign different colors to tasks we use an index into the taskColors array and increment the index
every time a color is assigned to a task. The index wraps around if it reaches the end.
"""
taskColorIndex = 0
taskColors = [(100, 237, 157), (100, 143, 237), (212, 237, 76), (237, 123, 100), (141, 100, 237)]

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
    thread = Thread(target = parser_thread, args = (gui, numCores))
    thread.start()

def parser_thread(gui, numCores):
    """
    Thread to parse the trace buffers. The trace events are then converted to tasks, jobs and execution segments.
    """
    bufferPaths = []
    
    # Get the tick id for each core from the config file.
    if gui is not None:
        configName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')    # Get the configuration name
        config = configparser.ConfigParser()
        config.read(HelperFunctions.getConfigFilePath())
        tickIds = [int(x) for x in config.get(configName,'tickId').split(",")]
    else:
        tickIds = [15, 42]

    for c in range(0,numCores):
        filename = os.path.abspath(os.path.join(HelperFunctions.getViewingFolderName(gui), 'raw_buffer' + str(c)))

        bufferPaths.append(Path(filename + ".txt"))
        if not bufferPaths[-1].is_file():
            print("Error: File " + str(bufferPaths[-1]) + " does not exist!")

    allBuffers = []
    core = 0
    for buffer in bufferPaths:
        fh = open(buffer, "rb")
        traceBuffer = bytearray(fh.read())
        allBuffers.append(traceBuffer)
        
        HelperFunctions.printState("Loaded trace buffer: ", info=str(buffer))
        HelperFunctions.hexdump(traceBuffer, base_addr=int(config.get(configName, "buffer"+str(core), fallback="0x00000000"),16))
        core = core + 1

    eventFilePath = os.path.abspath(os.path.join(HelperFunctions.getViewingFolderName(gui), 'events.txt'))
    tasks = parser(allBuffers, eventFilePath, tickIds)    # Parse the content of the trace buffers

    # If this was called from the GUI, enable the buttons and update the GUI
    if gui is not None:
        gui.btn_loadTrace.configure(state="normal")
        gui.traceView.setTasks(tasks)
        gui.traceView.draw()
        gui.update()

def parser(buffers, eventFilePath, tickIds):
    """
    Function parses a variable number of trace buffers.
    Trace events are then converted to tasks, jobs and execution segments.
    The function returns an array with all trace tasks.
    """
    HelperFunctions.printHeader("parsing files")

    events = []
    parseTraceEvents(events, buffers)       # Parse the raw events from the trace files of each core

    allTasks = []
    allTasks = extractTraceInfo(events, eventFilePath, tickIds)     # Parse all trace tasks from the event trace (afterwards we have trace tasks, jobs and execution segments). 
    tasks = []
    
    for task in allTasks:                   # Some tasks might be created in the trace but never execute. We exclue those here. 
        if len(task.jobs) != 0:
            tasks.append(task)

    HelperFunctions.printState("Found trace data for tasks:")
    for task in tasks:                      # Print a list with parsed tasks and the number of jobs they have in the trace.
        print("   " + str(task))
        #task.printAll()

    return tasks

def extractTraceInfo(events, eventFilePath, tickIds):
    """ 
    Extract trace information from the raw trace events. So we have information on task-level.
    """
    tasks = []

    traceStart = None

    # Check if there is a TRACE_TIME_ZERO. If so, set trace start (i.e. t=0) to the first tick before the event.
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

    # Create tasks to represent the scheduler, tick ISR for each core.
    if len(tickIds) == 1:
        # Exclude the core in the name if there is only one core
        tasks.append(TraceTask(tickIds[0], "Tick", None, getTaskColor(tickIds[0])))
        tasks.append(TraceTask(schedulerId, "Scheduler", None, getTaskColor(schedulerId)))
    else:
        coreId = 0
        for id in tickIds:
            tasks.append(TraceTask(id, "Tick Core " + str(coreId), None, getTaskColor(id)))
            tasks.append(TraceTask(schedulerId + coreId, "Scheduler Core " + str(coreId), None, getTaskColor(schedulerId + coreId)))
            coreId = coreId + 1

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

    # Generate ISR Enter events
    for evt in events:
        if (evt.get('type') == TRACE_ISR_EXIT) or (evt.get('type') is TRACE_ISR_EXIT_TO_SCHEDULER):
            #if prevIrqEvt in allEvents:  # Always belongs to the last ISR event on this core
            if (prevIrqEvt.get('type') == TRACE_ISR_EXIT) or (prevIrqEvt.get('type') == TRACE_ISR_EXIT_TO_SCHEDULER):
                    # in rare cases it seems the ISR_ENTER event is missing/not generated. In this case, we 
                    # detect this here and create such an event with the timestamp of the previous task event 
                allEvents.append({'type':TRACE_ISR_ENTER, 'ts':prevTaskEvt.get('ts')-1, 'core':prevTaskEvt.get('core'), 'irqId':tickIds[prevTaskEvt.get('core')]})#prevIrqEvt.get('irqId')})
                            
        allEvents.append(evt)
                    
        # Remember the previous task or ISR events.
        if (evt.get('type') is TRACE_TASK_START_EXEC)or (evt.get('type') is TRACE_TASK_START_READY):# or (evt.get('type') is TRACE_TASK_STOP_EXEC) or (evt.get('type') is TRACE_TASK_START_READY) or (evt.get('type') is TRACE_TASK_STOP_READY):
            prevTaskEvt = evt
        elif (evt.get('type') is TRACE_ISR_ENTER) or (evt.get('type') is TRACE_ISR_EXIT) or (evt.get('type') is TRACE_ISR_EXIT_TO_SCHEDULER):
            prevIrqEvt = evt

    sortedEvents = sorted(allEvents, key=lambda d: d['ts'])    # Sort all events of this task by timestamp. Since timestamps on cores are synchronised this can be done. Attention, if the platform does not support this!

    for evt in sortedEvents:
        if traceStart is not None:
            evt['ts'] = evt['ts'] - traceStart
        eventFile.write('\tts: ' + "%06.3f" % (evt.get('ts')/1000) + "ms\t" + eventMap.get(evt.get('type')) + ":  " + str(evt) + "\n")

    executionParser(sortedEvents, tasks, tickIds, mutex_id_to_letter)
   #->  smParser(traceStart, sortedEvents, tasks, len(tickIds))

    eventFile.close()
    HelperFunctions.printState("Wrote event file to: ", info=eventFilePath)

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

def executionParser(sortedEvents, tasks, tickIds, mutex_id_to_letter):

    # This hardcodes that there are 2 cores, should be generalized!
    core1Flag = False

    for task in tasks:

        if "idle" in task.name.lower():
            parseIdleTask(sortedEvents, task)
        elif 100 <= task.id <= len(tickIds) + 100:    # scheduler IDs
            parseScheduler(sortedEvents, task)
        elif task.id in tickIds:
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

    coreId = int(task.name[len("IDLE"):])

    for evt in sortedEvents:
        type = evt.get('type')
        core = evt.get('core')
        ts = evt.get('ts')

        if type == TRACE_IDLE and coreId == core:
            #print(f"IDLE started at t={ts}")
            task.newJob(ts, None)
            task.startExec(ts, core, ExecutionType.EXECUTE)
        
        if task.currentJob is not None:
            if type == TRACE_ISR_ENTER or type == TRACE_TASK_START_EXEC:
                if core == coreId:
                    #print(f"IDLE finished at t={ts}")
                    task.stopExec(ts)
                    task.finishJob()

    if task.currentJob is not None:
        task.stopExec(ts)
        task.finishJob()

def parseTask(sortedEvents, task, mutex_id_to_letter, tickId):
    """
    Parses the execution of a single task.
    """
    
    finishJob = False       # Flag to indicate that the job is about to finish.
    deadlineMiss = False    # Flag to indicate that a deadline was missed.
    startExecCore = None    # Task started to execute on this core.
    lastExecTask = None     # Keep track of the last task started on the core.
    tickTs = []             # We keep track if tick timestamps to be able to handle deadline misses.
    missedDeadlineAt = None # Record the tick at which the release should have happened after a deadline miss.
    tickTs.append(0)        # By default the first tick appears at t=0

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
                    if finishJob == True:
                        finishJob = False
                        #print(f"Finish job at {ts}")
                        task.finishJob()

                        if deadlineMiss == True:
                            deadlineMiss = False
                            releaseTs = tickTs[missedDeadlineAt]    # Get the timestamp of the tick where the task should have been released.
                            task.newJob(releaseTs, None)            # Release the job at the intended tick time.

        if type == TRACE_EVT_GROUP_SYNC or type == TRACE_EVT_GROUP_WAIT:
            if task.id == taskId:
                finishJob = True

        elif type == TRACE_ISR_ENTER:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Stop execution due to ISR at {ts}")
                    task.stopExec(ts)
            irqId = evt.get('irqId')
            if irqId == tickId:
                tickTs.append(ts)
        elif type == TRACE_ISR_EXIT:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Stop execution due to ISR at {ts}")
                    task.startExec(ts, core, ExecutionType.EXECUTE)

        elif type == TRACE_DELAY_UNTIL:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Delay Until called at {ts}")
                    finishJob = True
                    if evt.get('deadlineMiss') == True:
                        missedDeadlineAt = evt.get('timeToWake')
                        deadlineMiss = True
        
        elif type == TRACE_DELAY:
            if startExecCore == core:
                if lastExecTask == task.id:
                    #print(f"Delay called at {ts}")
                    finishJob = True

        elif type == TRACE_MUTEX_TAKE:
            if startExecCore == core:
                if lastExecTask == task.id:
                    letterId = map_mutex_id(mutex_id_to_letter, evt.get('mutexId'))
                    task.mutexTake(ts, evt.get('mutexId'), letterId)

        elif type == TRACE_MUTEX_GIVE:
            if startExecCore == core:
                if lastExecTask == task.id:
                    task.mutexGive(ts, evt.get('mutexId'))
            
            
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
    
    for evt in sortedEvents:
        type = evt.get('type')

        if type in [TRACE_ISR_EXIT, TRACE_ISR_EXIT_TO_SCHEDULER, TRACE_ISR_ENTER]:
            core = evt.get('core')
            ts = evt.get('ts')
            irqId = evt.get('irqId')

            if irqId == irqTask.id:

                if type == TRACE_ISR_ENTER:
                    irqTask.newJob(ts, None)
                    irqTask.startExec(ts, core, ExecutionType.EXECUTE)
                    enterCore = core

            if type == TRACE_ISR_EXIT or type == TRACE_ISR_EXIT_TO_SCHEDULER:
                if core == enterCore:
                    irqTask.stopExec(ts)
                    irqTask.finishJob()
                    enterCore = None
    
def parseTraceEvents(events, buffers):
    """
    This function converts the trace buffer of the traget into processable trace events.
    As buffers of different cores can contain events up to different timestamps, this function
    gets the events of each core's trace buffer up to the earliest timestamp of the last event on 
    any core. Otherwise, trace data can be inconsistent (for example under FreeRTOS new task instances 
    become ready on core 0, even if they are mapped to a different core.)
    """
    coreId = 0

    bufferEvents = []
    
    for buffer in buffers:
        HelperFunctions.printState("Reading events of core " + str(coreId))
        bufferEvents.append([])

        parser = EventParser(buffer)

        while True:
            evt = parser.read_event(coreId)
            #print(evt)
            if evt is None:
                break
            #events.append(evt)
            bufferEvents[-1].append(evt)
        coreId = coreId + 1

    # Find timestamp last timestamp in any of the buffers

    # Commented out since core 0 has no events if there is no task and time slizing is off.
    # minTs = -1
    # for evts in bufferEvents:
    #     if len(evts) > 0 :
    #         if minTs == -1:
    #             minTs = evts[-1].get('ts')
    #         elif minTs > evts[-1].get('ts'):
    #             minTs = evts[-1].get('ts')

    # From each buffer, add all events to 'events' that appear up to t=minTs
    for evts in bufferEvents:
        for evt in evts:
#            if evt.get('ts') <= minTs:
            events.append(evt)

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
        self.buffer = file = io.BytesIO(inBuffer)
        self.time = 0
        self.bytesRead = 0   

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

    def read_event(self, coreId):
        """
        Function reads the next event of the trace buffer.
        """
        b = self.readBytes(2)
        if b is None:
            return None
        
        #print("Time       ", "".join([f"\\x{byte:02x}" for byte in b]))
        deltaTime = int.from_bytes(b, byteorder='little', signed=False)
        
        b = b = self.readBytes(2)
        if b is None:
            return None
        
        eventId = int.from_bytes(b, byteorder='little', signed=False)
        #print("Identifyer ", "".join([f"\\x{byte:02x}" for byte in b]), " -> ", eventId)
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
            if strLen is None:
                return None
            priority = self.readInteger()
            if priority is None:
                return None
            name = self.readBytes(strLen * 4).decode('UTF-8')
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
        else:
            #print("ERROR Unknown Event!")
            evt = None

        return evt

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

    ticks = np.asarray(tickTimes, dtype=np.int64)

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