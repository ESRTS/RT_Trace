from threading import Thread
from pathlib import Path
import io
from TraceTask import *
import os

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
    TRACE_ISR_EXIT : "TRACE_ISR_EXIT"
}
"""
ISR's have specific task id's (the ISR id). Those are not registered in the trace itself.
They are hardcoded here. This should be done better to support several platforms where the ISR id's
might be different.
"""
systickCore0Id = 15
systickCore1Id = 42
schedulerId = 0

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
    
    if taskId == systickCore0Id:
        c = (203, 255, 168)
    elif taskId == systickCore1Id:
        c = (255, 82, 82)
    elif taskId == schedulerId:
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
    thread = Thread(target = parser_thread, args = (gui, numCores))
    thread.start()

def parser_thread(gui, numCores):
    """
    Thread to parse the trace buffers. The trace events are then converted to tasks, jobs and execution segments.
    """
    bufferPaths = []
    for c in range(0,numCores):

        filename = os.path.join('data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'raw_buffer' + str(c))

        bufferPaths.append(Path(filename + ".txt"))
        if not bufferPaths[-1].is_file():
            print("Error: File " + str(bufferPaths[-1]) + " does not exist!")

    allBuffers = []
    for buffer in bufferPaths:
        fh = open(buffer, "rb")
        traceBuffer = bytearray(fh.read())
        allBuffers.append(traceBuffer)
        print("Loaded trace buffer: " + str(buffer))

    tasks = parser(allBuffers)    # Parse the content of the trace buffers

    # If this was called from the GUI, enable the buttons and update the GUI
    if gui is not None:
        gui.btn_loadTrace.configure(state="normal")
        gui.traceView.setTasks(tasks)
        gui.traceView.draw()
        gui.update()

def parser(buffers):
    """
    Function parses a variable number of trace buffers.
    Trace events are then converted to tasks, jobs and execution segments.
    The function returns an array with all trace tasks.
    """
    print("Parsing Files!")

    events = []
    parseTraceEvents(events, buffers)       # Parse the raw events from the trace files of each core

    allTasks = extractTraceInfo(events)     # Parse all trace tasks from the event trace (afterwards we have trace tasks, jobs and execution segments). 
    tasks = []
    print("Found trace data for tasks:")

    for task in allTasks:                   # Some tasks might be created in the trace but never execute. We exclue those here. 
        if len(task.jobs) != 0:
            tasks.append(task)

    for task in tasks:                      # Print a list with parsed tasks and the number of jobs they have in the trace.
        print("\t" + str(task))
        #task.printAll()

    return tasks

def extractTraceInfo(events):
    """ 
    Extract trace information from the raw trace events. So we have information on task-level.
    """
    tasks = []

    # Create three tasks to represent the scheduler, tick ISR, doorbell ISR.
    tasks.append(TraceTask(schedulerId, "Scheduler", None, getTaskColor(schedulerId)))
    tasks.append(TraceTask(systickCore0Id, "Tick Core 1", None, getTaskColor(systickCore0Id)))
    tasks.append(TraceTask(systickCore1Id, "Tick Core 2", None, getTaskColor(systickCore1Id)))

    traceStart = None

    # All other tasks are parsed from the trace events. 
    for evt in events:
        if evt.get('type') is TRACE_TASK_CREATE:    # Parse all task create events and create trace tasks for each.
            id = evt.get('taskId')
            prio = evt.get('priority')
            #name = evt.get('name').split('\\')[0]
            name = evt.get('name').split('\x00', 1)[0]
            tmpTask = TraceTask(id, name, prio, getTaskColor(id))
            tasks.append(tmpTask)
        if evt.get('type') is TRACE_TASK_START_READY:   # We set the trace time t=0 to the first task ready event.
            if traceStart is None:
                traceStart = evt.get('ts')  # By convention we set the start of the first task to t=0

    eventFileName = os.path.join('data', 'events.txt')
    eventFile = open(eventFileName, 'w')
    
    for task in tasks:
        """ 
        For each task separately, get all events that belong to this task.
        Then those are parsed separately and converted to jobs and execution segments.
        """
        
        taskEvts = []
        prevTaskEvt = None
        prevIrqEvt = None

        for evt in events:  # Search all execution segments for this task. We do this sequentially on task level, it could be done nicer for all tasksk at once.

            # Only handle events of this task
            if evt.get('taskId') == task.id or evt.get('irqId') == task.id:
                if evt.get('type') is not TRACE_TASK_CREATE:    # The task create event was already parsed and handled above
                    taskEvts.append(evt)
            #elif evt.get('type') == TRACE_DELAY_UNTIL: #or evt.get('type') == TRACE_ISR_EXIT
            
            # Some events have no ID to match them to tasks. Those always belong to the same task that had the previous event.
            if evt.get('type') == TRACE_DELAY_UNTIL:
                if prevTaskEvt in taskEvts: # Always belongs to the last task event on this core
                    taskEvts.append(evt)

            if evt.get('type') == TRACE_ISR_EXIT:
                if prevIrqEvt in taskEvts:  # Always belongs to the last ISR event on this core
                    if prevIrqEvt.get('type') is TRACE_ISR_EXIT:
                        # in rare cases it seems the ISR_ENTER event is missing/not generated. In this case, we 
                        # detect this here and create such an event with the timestamp of the previous task event
                        taskEvts.append({'type':TRACE_ISR_ENTER, 'ts':prevTaskEvt.get('ts'), 'core':prevTaskEvt.get('coreId'), 'irqId':prevIrqEvt.get('irqId')})
                    
                    taskEvts.append(evt)

            # Remember the previous task or ISR events.
            if (evt.get('type') is TRACE_TASK_START_EXEC) or (evt.get('type') is TRACE_TASK_STOP_EXEC) or (evt.get('type') is TRACE_TASK_START_READY) or (evt.get('type') is TRACE_TASK_STOP_READY):
                prevTaskEvt = evt
            elif (evt.get('type') is TRACE_ISR_ENTER) or (evt.get('type') is TRACE_ISR_EXIT):
                prevIrqEvt = evt

        sortedTaskEvts = sorted(taskEvts, key=lambda d: d['ts'])    # Sort all events of this task by timestamp. Since timestamps on cores are synchronised this can be done. Attention, if the platform does not support this!

        parseTaskExecution(traceStart, task, sortedTaskEvts, eventFile)    # After all task events are parsed, the trace tasks are created here.

    eventFile.close()
    print("Wrote event file to: " + eventFileName)

    return tasks

def parseTaskExecution(traceStart, task, events, eventFile):
    """ 
    The function gets a list of events related to this specific task.
    Based on this, the jobs and execution segments are extracted. 
    """

    jobFinishes = False
    timeToWake = 0

    

    #print("Parsing task: " + str(task))
    eventFile.write("Task: " + task.name + "\n")

    for evt in events:
        #print('\t' + str(evt))
        eventFile.write('\tts: ' + "%06.3f" % (evt.get('ts')/1000) + "ms\t" + eventMap.get(evt.get('type')) + ":  " + str(evt) + "\n")

        ts = evt.get('ts') - traceStart

        if evt.get('type') is TRACE_TASK_START_READY:   # Event ID: 4
            if task.currentJob is None:
                task.newJob(ts, None)    # If there is no active job, create a new one. I.e. this is a task release.
                jobFinishes = False

        elif evt.get('type') is TRACE_TASK_START_EXEC:  # Event ID: 2
            if task.currentJob is None:
                task.newJob(ts, None)
            task.startExec(ts, evt.get('core'), ExecutionType.EXECUTE)   # Start a new execution segment.

        elif evt.get('type') is TRACE_DELAY_UNTIL:      # Event ID: 9
            # DelayUntil is used to create periodic tasks. I.e. the statement tells us that the job finishes with the next TRACE_TASK_STOP_READY statement. 
            # We can also set the job deadline now, assuming the deadline is equal to the release of the next job.
            jobFinishes = True  # Mark that the job finishes with the next TRACE_TASK_STOP_READY event
            timeToWake = evt.get('timeToWake') * 1000   # to get it in ts granularity
        elif evt.get('type') is TRACE_TASK_STOP_EXEC:  # Event ID: 3
            task.stopExec(ts)   # Stop the current execution segment.

            if jobFinishes is True:
                initialStart = 0    # the task delay until event has the wakeup time relative to the initial start as parameter.
                if len (task.jobs) > 0:
                    initialStart = task.jobs[0].releaseTime
                else:
                    initialStart = task.currentJob.releaseTime

                task.setCurrentJobDeadline(initialStart + timeToWake)
                task.finishJob()

        elif evt.get('type') is TRACE_ISR_ENTER:
            if task.currentJob is None:
                task.newJob(ts, None)   # an ISR has no jobs in this sense, so we see every execution as a single job.
            task.startExec(ts, evt.get('core'), ExecutionType.EXECUTE)

        elif evt.get('type') is TRACE_ISR_EXIT:
            task.stopExec(ts)
            task.finishJob()    # Each stop execution event of an ISR is also the end of the job.


    
def parseTraceEvents(events, buffers):
    """
    This function converts the trace buffer of the traget into processable trace events.
    """
    coreId = 0

    for buffer in buffers:
        print("Reading events of core " + str(coreId))

        parser = EventParser(buffer)

        #parser.printBuffer()
        while True:
            evt = parser.read_event(coreId)
            if evt is None:
                break
            events.append(evt)

        coreId = coreId + 1

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
        return int.from_bytes(b, byteorder='little', signed=False)

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
            #print("[t=" + str(self.time) + "us] TRACE_IDLE")
            evt = {'type':TRACE_IDLE, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_TASK_START_EXEC:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_START_EXEC  -> taskId: " + str(taskId))
            evt = {'type':TRACE_TASK_START_EXEC, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_STOP_EXEC:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_STOP_EXEC   -> taskId: " + str(taskId))
            evt = {'type':TRACE_TASK_STOP_EXEC, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_START_READY:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_START_READY -> taskId: " + str(taskId))
            evt = {'type':TRACE_TASK_START_READY, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_STOP_READY:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_STOP_READY  -> taskId: " + str(taskId))
            evt = {'type':TRACE_TASK_STOP_READY, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_CREATE:

            taskId = self.readInteger()
            strLen = self.readInteger()
            priority = self.readInteger()
            name = self.readBytes(strLen * 4).decode('UTF-8')
            #print("[t=" + str(self.time) + "us] TRACE_TASK_CREATE      -> Task: " + name + " ID: " + str(taskId) + " with priority: " + str(priority))
            evt = {'type':TRACE_TASK_CREATE, 'ts':self.time, 'core':coreId, 'taskId':taskId, 'name':name, 'priority':priority}

        elif eventId == TRACE_START:
            #print("[t=" + str(self.time) + "us] TRACE_START")
            evt = {'type':TRACE_START, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_STOP:
            #print("[t=" + str(self.time) + "us] TRACE_STOP")
            evt = {'type':TRACE_STOP, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_DELAY_UNTIL:
            timeToWake = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_DELAY_UNTIL      -> timeToWake: " + str(timeToWake) + " ms")
            evt = {'type':TRACE_DELAY_UNTIL, 'ts':self.time, 'core':coreId, 'timeToWake':timeToWake}

        elif eventId == TRACE_ISR_ENTER:
            irqId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_ISR_ENTER        -> irqId: " + str(irqId))
            evt = {'type':TRACE_ISR_ENTER, 'ts':self.time, 'core':coreId, 'irqId':irqId}

        elif eventId == TRACE_ISR_EXIT:
            #print("[t=" + str(self.time) + "us] TRACE_ISR_EXIT") 
            evt = {'type':TRACE_ISR_EXIT, 'ts':self.time, 'core':coreId} 

        else:
            #print("ERROR Unknown Event!")
            evt = None

        return evt

if __name__ == "__main__":
    """
    Debugging.
    """
    parser_thread(None)