from threading import Thread
from pathlib import Path
import io
from TraceTask import *
import os
import HelperFunctions
import configparser
import sys

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
TRACE_EVT_GROUP_SYNC            =16

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
    TRACE_EVT_GROUP_SYNC: "TRACE_EVT_GROUP_SYNC"
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

    executionParser(sortedEvents, tasks, tickIds)
   #->  smParser(traceStart, sortedEvents, tasks, len(tickIds))

    eventFile.close()
    HelperFunctions.printState("Wrote event file to: ", info=eventFilePath)

    return tasks

def executionParser(sortedEvents, tasks, tickIds):

    for task in tasks:

        if "idle" in task.name.lower():
            parseIdleTask(sortedEvents, task)
        elif 100 <= task.id <= len(tickIds) + 100:    # scheduler IDs
            parseScheduler(sortedEvents, task)
        elif task.id in tickIds:
            parseIrq(sortedEvents, task)
        else:
            parseTask(sortedEvents, task)

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

def parseTask(sortedEvents, task):
    """
    Parses the execution of a single task.
    """
    
    finishJob = False
    startExecCore = None

    for evt in sortedEvents:
        type = evt.get('type')
        taskId = evt.get('taskId')
        core = evt.get('core')
        ts = evt.get('ts')

        if task.id == taskId:
            if type == TRACE_TASK_START_READY:
                if task.currentJob == None: #If the job was blocked it might get the ready event again.
                    #print(f"New Job released at {ts}")
                    task.newJob(ts, None)
            elif type == TRACE_TASK_START_EXEC:
                #print(f"Start execution at {ts} on core {core}")
                task.startExec(ts, core, ExecutionType.EXECUTE)
                startExecCore = core
            elif type == TRACE_TASK_STOP_EXEC:
                #print(f"Stop execution at {ts}")
                if task.currentJob.activeInterval is not None:
                    task.stopExec(ts)
                startExecCore = None
                if finishJob == True:
                    finishJob = False
                    #print(f"Finish job at {ts}")
                    task.finishJob()
            elif type == TRACE_EVT_GROUP_SYNC or type == TRACE_EVT_GROUP_WAIT:
                finishJob = True
        if type == TRACE_ISR_ENTER:
            if startExecCore == core:
                #print(f"Stop execution due to ISR at {ts}")
                task.stopExec(ts)
        elif type == TRACE_DELAY_UNTIL:
            if startExecCore == core:
                #print(f"Delay Until called at {ts}")
                finishJob = True
        elif type == TRACE_DELAY:
            if startExecCore == core:
                #print(f"Delay called at {ts}")
                finishJob = True
    
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
    minTs = -1
    for evts in bufferEvents:
        if len(evts) > 0 :
            if minTs == -1:
                minTs = evts[-1].get('ts')
            elif minTs > evts[-1].get('ts'):
                minTs = evts[-1].get('ts')

    # From each buffer, add all events to 'events' that appear up to t=minTs
    for evts in bufferEvents:
        for evt in evts:
            if evt.get('ts') <= minTs:
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
            timeToWake = self.readInteger()
            if timeToWake is None:
                return None
            entryPrint("[t=" + str(self.time) + "us] TRACE_DELAY_UNTIL      -> timeToWake: " + str(timeToWake) + " ms" + " Core: " + str(coreId))
            evt = {'type':TRACE_DELAY_UNTIL, 'ts':self.time, 'core':coreId, 'timeToWake':timeToWake}

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
        else:
            #print("ERROR Unknown Event!")
            evt = None

        return evt
    
def entryPrint(*args, **kwargs):
    global enable_entry_print
    if enable_entry_print:
        return print(*args, **kwargs)
    
if __name__ == "__main__":
    """
    Debugging.
    """
    parser_thread(None, 2)