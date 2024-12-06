import subprocess
import telnetlib
from threading import Thread
from time import sleep
import os
import sys
from pathlib import Path
import FileHelper
import configparser

"""
A global variable that is used to indicate if an error was reported in openocd.
Openocd is called as subprocess and its stdout and stderr are processed as separate threads.
If they encounter a line that includes 'Error', the same line is set as errorMsg.
Usually this happens if the target is not connected. We check this variable in the pico_thread 
before trying to connect to openocd via telnet.
"""
errorMsg = None

def loadSTM32L476TraceBuffers(gui):

    thread = Thread(target = pico_thread, args = (gui,))
    thread.start()

def pico_thread(gui):
    
    traceBuffer = readTraceBuffers()

    if traceBuffer is not None:
    
        print("Size trace buffer: " + str(len(traceBuffer[0])) + "b")

        cwd = FileHelper.getCwd()

        targetPath = os.path.join(cwd, 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'))
        print("targetPath: " + targetPath)
        Path(targetPath).mkdir(parents=True, exist_ok=True)
        
        filename1 = os.path.join(cwd, 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_'), 'raw_buffer0')

        # Parse the trace buffers
        writeFile(traceBuffer[0], filename1)
    
        # Print the trace buffer content
        # line = ""
        # count = 0
        # for b in traceBuffer[0]:
        #     count = count + 1
        #     line = line + '{:02x}'.format(b)
        #     if count % 4 == 0:
        #         line = line + " "
        #     if count == 16:
        #         print(line)
        #         line = ""
        #         count = 0
        # if line != "":
        #     print(line)

    else:
        print("Could not read trace buffers!", file=sys.stderr)

    # Enable the buttons and update the GUI
    if gui is not None:
        gui.btn_recordTrace.configure(state="normal")
        gui.update()

def writeFile(data, filename):
    """
    This function writes binary data to a file. Used to store the raw trace buffer we read from the target.
    """
    # Open file in binary write mode
    binary_file = open(filename + ".txt", "wb")
 
    # Write bytes to file
    binary_file.write(data)
 
    # Close file
    binary_file.close()

    print("Created File: " + filename + ".txt")

def textRedirectErrThread(output):
    """
    A thread that is used to print the stderr from the subprocess running openocd.
    If an error occurs, indicated by 'Error' in one of the lines, errorMsg is set with
    the occuring line. This indicates to pico_thread that something went wrong.
    """
    global errorMsg

    while output.poll() is None: # check whether process is still running
            msg = output.stderr.readline().strip() # read a line from the process output
            if msg:
                if 'Error' in msg:
                    errorMsg = msg
                print(msg)

def textRedirectOutThread(output):
    """
    A thread that is used to print the stdout from the subprocess running openocd.
    """
    
    while output.poll() is None: # check whether process is still running
            msg = output.stdout.readline().strip() # read a line from the process output
            if msg:
                print(msg)

def readTraceBuffers():
    """
    This function reads the trace buffer from both cores.
    Openocd is used to access the memory on the target device, as it's own subprocess.
    Telnet is then used to control the debug session of openocd. 
    Two additional threads are used to receive and print stdout and stderr from the 
    openocd subprocess. A global variable errorMsg is set in the thread that receives 
    openocd stderr to indicate that something went wrong before starting the telnet session.
    """
    global errorMsg
    
    # Read the configuration from the ini-file
    config = configparser.ConfigParser()
    config.read(FileHelper.getConfigFilePath())
    size = config.get('STM_FreeRTOS','bufferSize', fallback = '4000')
    print("Size is: " + str(size))
    buffer0 = config.get('STM_FreeRTOS','buffer0', fallback = '0x10000000') 
    openocdPath = config.get('STM_FreeRTOS','openocd_path', fallback = '/usr/local/bin') 

    traceBuffer = []

    my_env = os.environ.copy()
    my_env["PATH"] = f"{my_env['PATH']}:{openocdPath}" # This is not final, a config file for the openocd path should be added
    
    debugger = subprocess.Popen([r"openocd",
            "-f", r"board/st_nucleo_l4.cfg",
            "-c", "telnet_port 4444",
            "-c", "adapter speed 4000"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=1, text=True, env=my_env)
    
    # A thread to read stderr and print it in the textbox
    stderrThread = Thread(target = textRedirectErrThread, args = (debugger,))
    stderrThread.start()
    
    # A thread to read stout and print it in the textbox
    stdoutThread = Thread(target = textRedirectOutThread, args = (debugger,))
    stdoutThread.start()
    
    sleep(0.25) # Avoid connecting with telnet before openocd is ready

    if errorMsg is not None:
        print("Error in openocd", file=sys.stderr)
        debugger.terminate()
        return None

    try:
        tel = telnetlib.Telnet('localhost', 4444)
    except:
        print("Telnet could not connect to openocd on port 4444", file=sys.stderr)
        debugger.terminate()
        return None
    
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

    tel.close()
    debugger.terminate()

    return traceBuffer

def cmd(tel, command):
    """
    Helper function to send a command to the target.
    """
    tel.write((f"{command}\n").encode())
    return read_data(tel)
    
def read_data(tel):
    """
    Helper function to read a response from telnet.
    """
    return tel.read_until(b">", 5)
    
if __name__ == "__main__":
    """
    Debugging.
    """
    loadPico2TraceBuffers(None)
