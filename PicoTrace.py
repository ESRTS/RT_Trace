import subprocess
import telnetlib
from threading import Thread
from time import sleep

def loadTraceBuffers(gui, size=4000):

    thread = Thread(target = pico_thread, args = (size, gui))
    thread.start()

def pico_thread(size, gui):
    
    traceBuffer = readTraceBuffers(size)

    print("Size trace buffer core 0: " + str(len(traceBuffer[0])) + "b")
    print("Size trace buffer core 1: " + str(len(traceBuffer[1])) + "b")

    # Parse the trace buffers

    # Draw the execution trace

    # Enable the buttons and update the GUI
    gui.btn_recordTrace.configure(state="normal")
    gui.update()

def readTraceBuffers(size):

    buffer0 = "0x20080000"  # SRAM8_BASE
    buffer1 = "0x20081000"  # SRAM9_BASE
    traceBuffer = []

    debugger = subprocess.Popen([r"openocd",
            "-f", r"interface/cmsis-dap.cfg",
            "-f", r"target/rp2350.cfg",
            "-c", "telnet_port 4444"])
    
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
    
    