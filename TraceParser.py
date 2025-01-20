from threading import Thread
from pathlib import Path
import io
from TraceTask import *
import os
import FileHelper
import configparser
import sys

parserOld = False

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
    TRACE_TIME_ZERO : "TRACE_TIME_ZERO"
}
"""
ISR's have specific task id's (the ISR id). Those are not registered in the trace itself.
They are hardcoded here. This should be done better to support several platforms where the ISR id's
might be different.
"""
#systickCore0Id = 15
#systickCore1Id = 42
#schedulerCore0ID = 0
#schedulerCore1ID = 1
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
    taskColorIndex = 0      # Reset the task color index, so we always start with the same task color assignments.
    thread = Thread(target = parser_thread, args = (gui, numCores))
    thread.start()

def parser_thread(gui, numCores):
    """
    Thread to parse the trace buffers. The trace events are then converted to tasks, jobs and execution segments.
    """
    bufferPaths = []
    
    # Get the tick id for each core from the config file.
    configName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')    # Get the configuration name
    config = configparser.ConfigParser()
    config.read(FileHelper.getConfigFilePath())
    tickIds = [int(x) for x in config.get(configName,'tickId').split(",")]

    for c in range(0,numCores):

        cwd = FileHelper.getCwd()

        filename = os.path.join(cwd, 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'raw_buffer' + str(c))

        bufferPaths.append(Path(filename + ".txt"))
        if not bufferPaths[-1].is_file():
            print("Error: File " + str(bufferPaths[-1]) + " does not exist!")

    allBuffers = []
    for buffer in bufferPaths:
        fh = open(buffer, "rb")
        traceBuffer = bytearray(fh.read())
        allBuffers.append(traceBuffer)
        print("Loaded trace buffer: " + str(buffer))

    cwd = FileHelper.getCwd()
    eventFilePath = os.path.join(cwd, 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'events.txt')

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
    print("Parsing Files!")

    events = []
    parseTraceEvents(events, buffers)       # Parse the raw events from the trace files of each core

    allTasks = extractTraceInfo(events, eventFilePath, tickIds)     # Parse all trace tasks from the event trace (afterwards we have trace tasks, jobs and execution segments). 
    tasks = []
    print("Found trace data for tasks:")

    for task in allTasks:                   # Some tasks might be created in the trace but never execute. We exclue those here. 
        if len(task.jobs) != 0:
            tasks.append(task)

    for task in tasks:                      # Print a list with parsed tasks and the number of jobs they have in the trace.
        print("\t" + str(task))
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
        eventFile.write('\tts: ' + "%06.3f" % (evt.get('ts')/1000) + "ms\t" + eventMap.get(evt.get('type')) + ":  " + str(evt) + "\n")

    smParser(traceStart, sortedEvents, tasks, len(tickIds))

    eventFile.close()
    print("Wrote event file to: " + eventFilePath)

    return tasks
    
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
        print("Reading events of core " + str(coreId))
        bufferEvents.append([])

        parser = EventParser(buffer)

        #parser.printBuffer()
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
            #print("[t=" + str(self.time) + "us] TRACE_IDLE" + " Core: " + str(coreId))
            evt = {'type':TRACE_IDLE, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_TASK_START_EXEC:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_START_EXEC  -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_START_EXEC, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_STOP_EXEC:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_STOP_EXEC   -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_STOP_EXEC, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_START_READY:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_START_READY -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_START_READY, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_STOP_READY:
            taskId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_TASK_STOP_READY  -> taskId: " + str(taskId) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_STOP_READY, 'ts':self.time, 'core':coreId, 'taskId':taskId}

        elif eventId == TRACE_TASK_CREATE:
            taskId = self.readInteger()
            strLen = self.readInteger()
            priority = self.readInteger()
            name = self.readBytes(strLen * 4).decode('UTF-8')
            #print("[t=" + str(self.time) + "us] TRACE_TASK_CREATE      -> Task: " + name + " ID: " + str(taskId) + " with priority: " + str(priority) + " Core: " + str(coreId))
            evt = {'type':TRACE_TASK_CREATE, 'ts':self.time, 'core':coreId, 'taskId':taskId, 'name':name, 'priority':priority}

        elif eventId == TRACE_START:
            #print("[t=" + str(self.time) + "us] TRACE_START" + " Core: " + str(coreId))
            evt = {'type':TRACE_START, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_STOP:
            #print("[t=" + str(self.time) + "us] TRACE_STOP" + " Core: " + str(coreId))
            evt = {'type':TRACE_STOP, 'ts':self.time, 'core':coreId}

        elif eventId == TRACE_DELAY_UNTIL:
            timeToWake = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_DELAY_UNTIL      -> timeToWake: " + str(timeToWake) + " ms" + " Core: " + str(coreId))
            evt = {'type':TRACE_DELAY_UNTIL, 'ts':self.time, 'core':coreId, 'timeToWake':timeToWake}

        elif eventId == TRACE_ISR_ENTER:
            irqId = self.readInteger()
            #print("[t=" + str(self.time) + "us] TRACE_ISR_ENTER        -> irqId: " + str(irqId) + " Core: " + str(coreId))
            evt = {'type':TRACE_ISR_ENTER, 'ts':self.time, 'core':coreId, 'irqId':irqId}

        elif eventId == TRACE_ISR_EXIT:
            #print("[t=" + str(self.time) + "us] TRACE_ISR_EXIT" + " Core: " + str(coreId)) 
            evt = {'type':TRACE_ISR_EXIT, 'ts':self.time, 'core':coreId} 

        elif eventId == TRACE_ISR_EXIT_TO_SCHEDULER:
            #print("[t=" + str(self.time) + "us] TRACE_ISR_EXIT_TO_SCHEDULER" + " Core: " + str(coreId)) 
            evt = {'type':TRACE_ISR_EXIT_TO_SCHEDULER, 'ts':self.time, 'core':coreId} 

        elif eventId == TRACE_DELAY:
            delayTime = self.readInteger()
            evt = {'type':TRACE_DELAY, 'ts':self.time, 'core':coreId, 'delayTime':delayTime} 
        
        elif eventId == TRACE_TIME_ZERO:
            evt = {'type':TRACE_TIME_ZERO, 'ts':self.time, 'core':coreId} 

        else:
            #print("ERROR Unknown Event!")
            evt = None

        return evt


def smParser(traceStart, sortedEvents, allTasks, numCores):
    '''
    This function implements a state machine tho extract the task execution from the recorded trace events.
    '''

    # States used in the parser state machine
    STATE_IDLE = 0
    STATE_TASK = 1
    STATE_IRQ = 2
    STATE_SCHEDULER = 3

    state = []          # Current state of each core
    idleTask = []       # Idle task of each core
    scheduler = []      # Scheuler of each core
    tick = []           # Tick of each core
    running = []        # Currently running task of each core
    beforeIsr = []      # Task active before the ISR
    
    tasks = []          # List of all 'normal' tasks

    ###############################################################################################
    # Setup the tasks representing system states of each core
    ###############################################################################################
    if numCores == 1:
        idleTask.append(findTaskByName(allTasks, "IDLE"))
        scheduler.append(findTaskByName(allTasks, "Scheduler"))
        tick.append(findTaskByName(allTasks, "Tick"))
        running.append(None)
        state.append(STATE_SCHEDULER)   # The first state is the scheduler
        #calledDelayU.append([])
        beforeIsr.append(None)
        assert idleTask[0] is not None and scheduler[0] is not None and tick[0] is not None
    else:
        for core in range(0, numCores):
            idleTask.append(findTaskByName(allTasks, "IDLE" + str(core)))
            scheduler.append(findTaskByName(allTasks, "Scheduler Core " + str(core)))
            tick.append(findTaskByName(allTasks, "Tick Core " + str(core)))
            running.append(None)
            beforeIsr.append(None)
            #calledDelayU.append([])

            if core == 0:
                state.append(STATE_SCHEDULER)   # The first state of core 0 is the scheduler
            else:
                state.append(STATE_SCHEDULER)        # All other cores start in idle

            assert idleTask[core] is not None and scheduler[core] is not None and tick[core] is not None

    for task in allTasks:
        # Create a list with all remaining 'normal' tasks
        if task not in idleTask and task not in scheduler and task not in tick:
            tasks.append(task)

    # Set the initial state. Core 0 runs the scheduler and other cores are initially idle.
    for core in range(0, numCores):
        #if core == 0:
            scheduler[core].newJob(0, None)
            scheduler[core].startExec(0, core, ExecutionType.EXECUTE)
        #else:
        #    idleTask[core].newJob(0, None)
        #    idleTask[core].startExec(0, core, ExecutionType.EXECUTE)

    ###############################################################################################
    # Simulate the execution based on the recorded events to extract the task information
    ###############################################################################################

    for evt in sortedEvents:
        core = evt.get('core')
        type = evt.get('type')
        ts = evt.get('ts') - traceStart

        #STATE_IDLE####################################################################################
        # The idle task is executing.
        if state[core] == STATE_IDLE:
            if type == TRACE_ISR_ENTER:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_IDLE - Evt: TRACE_ISR_ENTER] Stopping Idle Task, starting ISR " + str(evt.get('irqId')) + ".")
                idleTask[core].stopExec(ts)                                                                        # Finish the execution of the idle task
                idleTask[core].finishJob()
                
                id = evt.get('irqId')                                                                              # Start Execution of the IRQ
                #if tick[core].id == id:
                tick[core].newJob(ts, None)
                tick[core].startExec(ts, core, ExecutionType.EXECUTE)
                beforeIsr[core] = idleTask[core]
                #else:
                #    print("Unexpected IRQ ID on core " + str(core) + ", Evt:" + str(type) + ", IrqId: " + str(id), file=sys.stderr)

                state[core] = STATE_IRQ                                                                            # Set next state
            elif type == TRACE_TASK_START_EXEC:
                # This seems to happen only once in the SMP version of FreeRTOS at startup.
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_IDLE - Evt: TRACE_TASK_START_EXEC] Stopping Idle Task, starting task " + findTaskById(tasks, evt.get('taskId')).name + ".")
                idleTask[core].stopExec(ts)                                                                        # Finish the execution of the idle task
                idleTask[core].finishJob()

                running[core] = findTaskById(tasks, evt.get('taskId'))                                              # Start the execution of the task
                running[core].startExec(ts, core, ExecutionType.EXECUTE)
                state[core] = STATE_TASK                                                                            # Set next state to TASK
            else:
                print("Unexpected event in state STATE_IDLE: " + str(type), file=sys.stderr)
        #STATE_TASK####################################################################################
        # One of the 'normal' tasks are executing
        elif state[core] == STATE_TASK:
            if type == TRACE_DELAY_UNTIL:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_TASK - Evt: TRACE_DELAY_UNTIL] Setting delay until flag for task " + running[core].name + ".")
                running[core].delayUntil = True                                                                    # Set the flag to indicate that this job is about to finish
                running[core].setCurrentJobDeadline(running[core].currentJob.releaseTime + evt.get('timeToWake'))  # Now we can also set the job's deadline by adding the time of the next release to its release time 
                # Remain in the same state
            elif type == TRACE_DELAY:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_TASK - Evt: TRACE_DELAY] Setting delay until flag for task " + running[core].name + ".")
                running[core].delayUntil = True  
                # Remain in the same state
            elif type == TRACE_ISR_ENTER:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_TASK - Evt: TRACE_ISR_ENTER] Stopping task " + running[core].name + " and starting tick.")
                running[core].stopExec(ts)                                                                         # Stop the execution of the task
                beforeIsr[core] = running[core]

                id = evt.get('irqId')                                                                              # Start Execution of the IRQ
                #if tick[core].id == id:
                tick[core].newJob(ts, None)
                tick[core].startExec(ts, core, ExecutionType.EXECUTE)
                #else:
                #    print("Unexpected IRQ ID on core " + str(core) + ": " + str(id) + " at t=" + str(ts), file=sys.stderr)

                state[core] = STATE_IRQ # Set next state to IRQ
            elif type == TRACE_TASK_STOP_EXEC:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_TASK - Evt: TRACE_TASK_STOP_EXEC] Stopping task " + running[core].name + " delayUntilFlag: " + str(running[core].delayUntil))
                running[core].stopExec(ts)                                                                          # Stop the execution of the task
                if running[core].delayUntil is True:                                                                # If the delayUntil flag is set, finish the job
                    running[core].finishJob()
                elif running[core].name == "Tmr Svc":                                                               # The timer service task of freeRTOS is a special task and seen as one job per execution
                    running[core].finishJob()
                elif running[core].name == "LET Manager":
                    running[core].finishJob()
                running[core] = None                                                                                # No task is executing currently

                scheduler[core].newJob(ts, None)                                                                    # Start the execution of the scheduler on this core
                scheduler[core].startExec(ts, core, ExecutionType.EXECUTE
                                          )
                state[core] = STATE_SCHEDULER # Set next state to SCHEDULER
            elif type == TRACE_TASK_START_READY:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_TASK - Evt: TRACE_TASK_START_READY] Release job of task " + findTaskById(tasks, evt.get('taskId')).name + ".")
                task = findTaskById(tasks, evt.get('taskId'))
                task.newJob(ts, None)                                                                               # Release a new job for this task
                # Remain in the same state
            elif type == TRACE_TIME_ZERO:
                pass # Nothing to do here...
            else:
                print("Unexpected event in state STATE_TASK: " + str(type), file=sys.stderr)
        #STATE_IRQ#####################################################################################
        # An interrupt is executing
        elif state[core] == STATE_IRQ:
            if type == TRACE_TASK_START_READY:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_IRQ - Evt: TRACE_TASK_START_READY] Release job of task " + findTaskById(tasks, evt.get('taskId')).name + ".")
                task = findTaskById(tasks, evt.get('taskId'))
                task.newJob(ts, None)                                                                               # Release a new job for this task
                # Remain in the same state
            elif type == TRACE_ISR_EXIT_TO_SCHEDULER:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_IRQ - Evt: TRACE_ISR_EXIT_TO_SCHEDULER] Stop tick task and start scheduler.")
                tick[core].stopExec(ts)                                                                             # Finish the execution of the ISR. (For now we assume there is only the tick ISR!)
                tick[core].finishJob()

                scheduler[core].newJob(ts, None)                                                                    # Start the execution of the scheduler
                scheduler[core].startExec(ts, core, ExecutionType.EXECUTE)
                state[core] = STATE_SCHEDULER # Set next state to SCHEDULER
            elif type == TRACE_ISR_EXIT:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_IRQ - Evt: TRACE_ISR_EXIT] Stop tick task and start task " + beforeIsr[core].name + ".")
                tick[core].stopExec(ts)                                                                             # Finish the execution of the ISR. (For now we assume there is only the tick ISR!)
                tick[core].finishJob()

                if beforeIsr[core] is running[core]:
                    running[core].startExec(ts, core, ExecutionType.EXECUTE)                                        # If there is a running task on the core, the IRQ preempted it. Hence we have to resume the task execution
                    state[core] = STATE_TASK                                                                        # Set next state to TASK
                elif beforeIsr[core] is idleTask[core]:
                    beforeIsr[core].newJob(ts, None)                                                                # Otherwise, the idle task was running before. Each execution of the idle task is its own job.
                    beforeIsr[core].startExec(ts, core, ExecutionType.EXECUTE) 
                    state[core] = STATE_IDLE                                                                        # Set next state to IDLE
                else:
                    print("Unexpected task before ISR: " + beforeIsr[core].name, file=sys.stderr)
                beforeIsr[core] = None
                
            else:
                print("Unexpected event in state STATE_IRQ: " + str(type), file=sys.stderr)
        #STATE_SCHEDULER###############################################################################
        # The scheduler is currently executing
        elif state[core] == STATE_SCHEDULER:
            if type == TRACE_IDLE:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_SCHEDULER - Evt: TRACE_IDLE] Stop scheduler and start idle task.")
                # Start the idle task
                scheduler[core].stopExec(ts)                                                                        # Finish the execution of the scheduler on this core
                scheduler[core].finishJob()

                idleTask[core].newJob(ts, None)                                                                     # Start the execution of the idle task on this core
                idleTask[core].startExec(ts, core, ExecutionType.EXECUTE)
                
                state[core] = STATE_IDLE                                                                            # Set next state to IDLE
            elif type == TRACE_TASK_STOP_EXEC:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_SCHEDULER - Evt: TRACE_TASK_STOP_EXEC] Remove the task " + running[core].name + " as running task.")

                running[core] = None                                                                                # No task running on the core. The task execution is already stopped when transitioning into the scheduler state or before
                # Remain in the same state
            elif type == TRACE_TASK_START_READY:
                
                task = findTaskById(tasks, evt.get('taskId'))
                if task is not None:
                    print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_SCHEDULER - Evt: TRACE_TASK_START_READY] Release job of task " + findTaskById(tasks, evt.get('taskId')).name + ".")
                    task.newJob(ts, None)                                                                           # Start a new job for this task
                # Remain in the same state
            elif type == TRACE_TASK_START_EXEC:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_SCHEDULER - Evt: TRACE_TASK_START_EXEC] Stop the execution of the scheduler and start execution of task " + findTaskById(tasks, evt.get('taskId')).name + ".")
                scheduler[core].stopExec(ts)                                                                        # Finish the execution oft he scheduler on this core
                scheduler[core].finishJob()

                running[core] = findTaskById(tasks, evt.get('taskId'))                                              # Start the execution of the task
                running[core].startExec(ts, core, ExecutionType.EXECUTE)
                state[core] = STATE_TASK                                                                            # Set next state to TASK
            elif type == TRACE_TASK_CREATE:
                print("[ts=" + str(ts) + " - Core: " + str(core) + "-  State: STATE_SCHEDULER - Evt: TRACE_TASK_CREATE] .")
                # Nothing to do here...
                pass
            else:
                print("Unexpected event in state STATE_SCHEDULER: " + str(type), file=sys.stderr)
        ###############################################################################################
        else:
            print("Unknown state...")

    # Terminate any started job. Otherwise it is not shown in the trace.
    for task in tasks:
        if task.currentJob is not None:
            if task.currentJob.activeInterval is not None:
                task.stopExec(ts)
            task.finishJobIncomplete()

if __name__ == "__main__":
    """
    Debugging.
    """
    parser_thread(None)