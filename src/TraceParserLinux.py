from threading import Thread
from pathlib import Path
import io
from TraceTask import *
import os
import FileHelper
import configparser
import sys

"""
Set to True to print state machine events when parsing execution.
"""
enable_parsing_print = False

"""
Flag to enable or disable the print output when parsing events from the eBPF trace file.
"""
enable_entry_print = False

"""
To assign different colors to tasks we use an index into the taskColors array and increment the index
every time a color is assigned to a task. The index wraps around if it reaches the end.
"""
taskColorIndex = 0
taskColors = [(100, 237, 157), (100, 143, 237), (212, 237, 76), (237, 123, 100), (141, 100, 237)]

"""
Scheduling events that are logged
"""
EXECED                      = 1
SCHED_IN                    = 2
SCHED_OUT                   = 3
SLEEP_CALL                  = 4
WAKING                      = 5
WAKE                        = 6
WAKE_NEW                    = 7
FORKED                      = 8
WAIT                        = 9
ID_USER_START_EVENT	        = 100
ID_USER_END_EVENT			= 200
ID_USER_RELEASE_EVENT		= 300
ID_USER_REGISTER_PERIOD		= 501
ID_USER_REGISTER_NAME 		= 503
ID_USER_REGISTER_PRIORITY	= 505

"""
Helper map to get back to the event string from event IDs
"""
eventMap = {
    EXECED : "EXECED",
    SCHED_IN : "SCHED_IN",
    SCHED_OUT : "SCHED_OUT",
    SLEEP_CALL : "SLEEP_CALL",
    WAKING : "WAKING",
    WAKE : "WAKE",
    WAKE_NEW : "WAKE_NEW",
    FORKED : "FORKED",
    WAIT : "WAIT",
    ID_USER_START_EVENT : "ID_USER_START_EVENT",
    ID_USER_END_EVENT : "ID_USER_END_EVENT",
    ID_USER_RELEASE_EVENT : "ID_USER_RELEASE_EVENT",
    ID_USER_REGISTER_PERIOD : "ID_USER_REGISTER_PERIOD",
    ID_USER_REGISTER_NAME : "ID_USER_REGISTER_NAME",
    ID_USER_REGISTER_PRIORITY : "ID_USER_REGISTER_PRIORITY",
}

def getTaskColor(taskId):
    """
    Function returns the task colors for the trace. The colors are selected in sequence from the list, wrapping around when the end of the list is reached. 
    """
    global taskColorIndex
    
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
    Thread used to parse and convert the trace information into task execution.
    As dedicated thread to not block the GUI while parsing.
    """
    # Get the tick id for each core from the config file.
    if gui == None:
        configName = None
    else:
        configName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')    # Get the configuration name

    config = configparser.ConfigParser()
    config.read(FileHelper.getConfigFilePath())
    #tickIds = [int(x) for x in config.get(configName,'tickId').split(",")]

    cwd = FileHelper.getCwd()

    if gui == None:
        filename = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'parsing', 'events_multi.txt'))
    else:
        filename = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'trace.txt'))

    cwd = FileHelper.getCwd()
    #eventFilePath = os.path.join(cwd, 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'events.txt')
    if gui == None:
        eventFilePath = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'parsing', 'parsedEvents.txt'))
    else:
        eventFilePath = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'events.txt'))

    tasks = parser(filename, eventFilePath)    # Parse the content of the trace buffers

    # If this was called from the GUI, enable the buttons and update the GUI
    if gui is not None:
        gui.btn_loadTrace.configure(state="normal")
        gui.traceView.setTasks(tasks)
        gui.traceView.draw()
        gui.update()

def parser(buffers, eventFilePath):
    """
    Function parses the trace file to internal events.
    Trace events are then converted to tasks, jobs and execution segments.
    The function returns an array with all trace tasks.
    """
    print("Parsing Files!")

    events = []
    parseTraceEvents(events, buffers)       # Parse the raw events from the trace files of each core

    events = sorted(events, key=lambda e: e['ts'])

    with open(eventFilePath, "w") as f:
        for evt in events:
            entryPrint(evt)
            f.write("ts: " + str(evt['ts']) + " " + eventMap.get(evt['type']) + " " + str(evt) + "\r\n")
 
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

def parseTraceEvents(events, filename): 
    """
    Method parses the trace file produced by the eBPF logger and converts it to 
    internal trace events that can be processed to obtain the task execution information.
    """   
    minTime = None
    
    print("Reading events of file " + filename)

    file = open(filename)

    for line in file:
        
        parts = line.split()

        ts = float(parts[0])    # in ns
        event = getEvtId(parts[1])
        tid = int(parts[2].split('=')[1])
        cpu = int(parts[3].split('=')[1])
        
        """
        Events are not necessarily delivered in order. The event with type EXECED markes the start,
        and then later adjust all timestamps relative to this one.
        """
        if event == EXECED:
            minTime = ts

        evt = {
            'ts': ts,
            'type': event,
            'taskId': tid,
            'core': cpu
        }

        if evt is None:
            break
        #events.append(evt)
        events.append(evt)

    """
    Correct all timestamps to start at t=0
    """
    for evt in events:
        evt['ts'] -= minTime

def extractTraceInfo(events):
    """
    Method used to convert the individual trace events into tasks, jobs and execution segments.
    """

    tasks = []

    # Find all task IDs
    taskIds = []
    for evt in events:
        taskId = evt['taskId']
        if taskId not in taskIds:
            taskIds.append(taskId)

    # For each task ID a trace task is created
    for id in taskIds:
        tmpTask = TraceTask(id, "Task_" + (str(id)), 0, getTaskColor(id))
        tasks.append(tmpTask)

    # The events are individual for each task, i.e. start, stop, sleep and wakeup. 
    # Hence, we can parse the execution for each task separately. 
    # This can be done more efficiently to scale better, but as proof of concept this is enough.

    for task in tasks:
        parsingPrint("=== THREAD ID: " + str(task.id) + " ===")
        for evt in events:
            """
            EXECED -> Start of the traced program.
            SCHED_IN -> Task starts to run on the CPU
            SCHED_OUT -> Task is removed from the CPU
            SLEEP_CALL -> Task signals to sleep (user-level, not sleeping yet!)
            WAKING -> Something is trying to wake the task
            WAKE -> The task is now in the run-queue
            WAKE_NEW -> A forked thread is in run-queue for the first time
            FORKED -> A new thread is created
            """
            if evt['taskId'] == task.id:
                if evt['ts'] >= 0:
                    if evt['type'] == EXECED:
                        # Marks the start of the traced program. We assume the thread is running.
                        parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " START_OF_TRACE -> EVECED, TASK_ID: " + str(evt['taskId']))
                        task.newJob(evt['ts'], None)
                        task.startExec(evt['ts'], evt['core'], ExecutionType.EXECUTE)

                    elif evt['type'] == SCHED_IN:
                        # There should always be a job with SCHED_IN 
                        parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " SCHED_IN, TASK_ID: " + str(evt['taskId']))
                        task.startExec(evt['ts'], evt['core'], ExecutionType.EXECUTE)

                    elif evt['type'] == SCHED_OUT:
                        if task.delayUntil is True: 
                            parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " SCHED_OUT, FINISHED JOB, TASK_ID: " + str(evt['taskId']))
                            task.stopExec(evt['ts'])  
                            task.finishJob()
                            task.delayUntil = False
                        else:
                            parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " SCHED_OUT, PREEMPTED, TASK_ID: " + str(evt['taskId']))
                            task.stopExec(evt['ts'])  
                            
                    elif evt['type'] == SLEEP_CALL:
                        parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " SLEEP_CALL, TASK_ID: " + str(evt['taskId']))
                        task.delayUntil = True
                        
                    elif evt['type'] == WAKING:
                        # We don't do anything with this event for now.
                        pass
                    elif evt['type'] == WAKE:
                        
                        if task.currentJob is not None:
                            parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " WAKE (TASK WAS NOT SLEEPING!), TASK_ID: " + str(evt['taskId']))
                            # It can happen that the task is not going to sleep after a sleep call (e.g. if the absolute sleep time has already passed.)
                            # In those cases, we have to finish the previous job and start the new job directly.
                            task.stopExec(evt['ts'])  
                            task.finishJob()
                            task.delayUntil = False
                            task.newJob(evt['ts'], None)
                            task.startExec(evt['ts'], evt['core'], ExecutionType.EXECUTE)
                        else:
                            parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " WAKE, TASK_ID: " + str(evt['taskId']))
                            # For normal cases we only need to release the next job.
                            task.newJob(evt['ts'], None)
                    elif evt['type'] == WAKE_NEW:
                        parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " WAKE_NEW, First job of TASK_ID: " + str(evt['taskId']))
                        task.newJob(evt['ts'], None)
                    elif evt['type'] == WAIT:
                        parsingPrint("ts=" + str(evt['ts']) + " - Core: " + str(evt['core']) + " WAIT, TASK_ID: " + str(evt['taskId']))
                        # This is a simplification. If a task is blocked on something (i.e. waiting) we start a new job. 
                        task.delayUntil = True

    return tasks

def getEvtId(evtString):
    """
    Mapping function to get the event type from the event string.
    """
    if evtString == "EXECED":
        return EXECED
    elif evtString == "SCHED_IN":
        return SCHED_IN
    elif evtString == "SCHED_OUT":
        return SCHED_OUT
    elif evtString == "SLEEP_CALL":
        return SLEEP_CALL
    elif evtString == "WAKING":
        return WAKING
    elif evtString == "WAKE":
        return WAKE
    elif evtString == "WAKE_NEW":
        return WAKE_NEW
    elif evtString == "FORKED":
        return FORKED
    elif evtString == "WAIT":
        return WAIT
    elif evtString == "ID_USER_START_EVENT":
        return ID_USER_START_EVENT
    elif evtString == "ID_USER_END_EVENT":
        return ID_USER_END_EVENT
    elif evtString == "ID_USER_RELEASE_EVENT":
        return ID_USER_RELEASE_EVENT
    elif evtString == "ID_USER_REGISTER_PERIOD":
        return ID_USER_REGISTER_PERIOD
    elif evtString == "ID_USER_REGISTER_NAME":
        return ID_USER_REGISTER_NAME
    elif evtString == "ID_USER_REGISTER_PRIORITY":
        return ID_USER_REGISTER_PRIORITY
    else:
        print("UNSUPPORTED TYPE: " + evtString)
        return None

def entryPrint(*args, **kwargs):
    """
    Helper function to have print output that can be globally disabled.
    """
    global enable_entry_print
    if enable_entry_print:
        return print(*args, **kwargs)

def parsingPrint(*args, **kwargs):
    global enable_parsing_print
    if enable_parsing_print:
        return print(*args, **kwargs)
    
if __name__ == '__main__':
    parseTraceFiles(None, 4)