import subprocess
import telnetlib
from threading import Thread
from time import sleep
import os
from pathlib import Path

def loadPico2TraceBuffers(gui, size=2000):

    thread = Thread(target = pico_thread, args = (size, gui))
    thread.start()

def pico_thread(size, gui):
    
    traceBuffer = readTraceBuffers(size)

    print("Size trace buffer core 0: " + str(len(traceBuffer[0])) + "b")
    print("Size trace buffer core 1: " + str(len(traceBuffer[1])) + "b")

    Path("data").mkdir(parents=True, exist_ok=True)

    filename1 = os.path.join('data', 'raw_buffer0')
    filename2 = os.path.join('data', 'raw_buffer1')

    # Parse the trace buffers
    writeFile(traceBuffer[0], filename1)
    writeFile(traceBuffer[1], filename2)
    
    # Draw the execution trace

    # Enable the buttons and update the GUI
    gui.btn_recordTrace.configure(state="normal")
    gui.update()

def writeFile(data, filename):
    # Open file in binary write mode
    binary_file = open(filename + ".txt", "wb")
 
    # Write bytes to file
    binary_file.write(data)
 
    # Close file
    binary_file.close()

    print("Created File: " + filename + ".txt")

def readTraceBuffers(size):

    buffer0 = "0x2008001c"  # SRAM8_BASE
    buffer1 = "0x2008100c"  # SRAM9_BASE
    traceBuffer = []

    debugger = subprocess.Popen([r"openocd",
            "-f", r"interface/cmsis-dap.cfg",
            "-f", r"target/rp2350.cfg",
            "-c", "telnet_port 4444"])#, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    #while True:
    #    line = myStdout.readline()
    #    if not line:
    #        break
    #    print(line.rstrip(), flush=True)

    sleep(0.25)

    tel = telnetlib.Telnet('localhost', 4444)

    read_data(tel)  # Read the string "Open On-Chip Debugger"

    sleep(0.25)
    tmpCmd = "read_memory " + buffer0 + " " + str(8) + " " + str(size)
    #print("Send Command: " + tmpCmd)
    tmpCore0 = cmd(tel, tmpCmd).decode("utf-8")

    lines = tmpCore0.splitlines()
    data = lines[1].replace('0x', '')
    elements = data.split(' ')
    dataStr = ""
    for e in elements:
        if len(e) == 1:
            dataStr = dataStr + '0' + e
        else:
            dataStr = dataStr + e

    dataCore0 = bytearray.fromhex(dataStr)
    traceBuffer.append(dataCore0)

    tmpCmd = "read_memory " + buffer1 + " " + str(8) + " " + str(size)
    #print("Send Command: " + tmpCmd)
    tmpCore1 = cmd(tel, tmpCmd).decode("utf-8")

    lines = tmpCore1.splitlines()
    data = lines[1].replace('0x', '')
    elements = data.split(' ')
    dataStr = ""
    for e in elements:
        if len(e) == 1:
            dataStr = dataStr + '0' + e
        else:
            dataStr = dataStr + e

    dataCore1 = bytearray.fromhex(dataStr)
    traceBuffer.append(dataCore1)

    tel.close()
    debugger.terminate()

    return traceBuffer

def cmd(tel, command):
    tel.write((f"{command}\n").encode())
    return read_data(tel)
    
def read_data(tel):
    return tel.read_until(b">", 5)
    
    