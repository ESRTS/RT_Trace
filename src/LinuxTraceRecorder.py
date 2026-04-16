import subprocess
from threading import Thread
from pathlib import PurePosixPath
import HelperFunctions
import configparser
import os

def loadLinuxraceBuffers(gui):
    thread = Thread(target = recorderThread, args = (gui,))
    thread.start()

def recorderThread(gui):
    configName = "RPI_Linux"
    # Read the configuration from the ini-file
    config = configparser.ConfigParser()
    config.read(HelperFunctions.getConfigFilePath())
    remoteHost = config.get(configName,'target', fallback = None)
    targetApplicationPath = config.get(configName,'target_path', fallback = None)
    remoteBasePath = config.get(configName,'recorderBase_path', fallback = None)
    scriptName = config.get(configName,'recorder_filename', fallback = None)
    traceName = config.get(configName,'trace_filename', fallback = None)
    
    configError = False

    if remoteHost is None:
        print("[ERROR] 'target' for configuration 'RPI_Linux' not set in config.ini!")
        configError = True

    if targetApplicationPath is None:
        print("[ERROR] 'target_path' for configuration 'RPI_Linux' not set in config.ini!")
        configError = True

    if remoteBasePath is None:
        print("[ERROR] 'recorderBase_path' for configuration 'RPI_Linux' not set in config.ini!")
        configError = True

    if scriptName is None:
        print("[ERROR] 'recorder_filename' for configuration 'RPI_Linux' not set in config.ini!")
        configError = True

    if traceName is None:
        print("[ERROR] 'trace_filename' for configuration 'RPI_Linux' not set in config.ini!")
        configError = True

    if configError is False:
        remoteScript = PurePosixPath(remoteBasePath, scriptName)
        remoteTrace = PurePosixPath(remoteBasePath, traceName)
        #localTrace = os.path.join('data', 'RPI_Linux')

        print(remoteScript)
        print(remoteTrace)

        # Check if the remote script to record the trace events exists on the target platform
        print("Check if remote script is available at " + str(remoteScript) + " on host " + remoteHost)
        check = subprocess.run(
            ["ssh", remoteHost, f"test -f {remoteScript}"],
        )

        if check.returncode == 0:
            print(str(remoteScript) + " exists on " + remoteHost)

            cmd = f"sudo chrt -f 50  python3 -u {remoteScript} -- {targetApplicationPath}"

            print("CMD: " + cmd)
            proc = subprocess.Popen(
                ["ssh", remoteHost, cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True  # line-buffered text mode
            )

            for line in proc.stdout:
                print("[REMOTE] " + line, end="", flush=True)  # live output from remote script

            proc.wait()

            if proc.returncode == 0:

                targetPath = HelperFunctions.getRecordingFolderName(gui)
                #cwd = HelperFunctions.getCwd()
                #targetPath = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_')))

                os.makedirs(targetPath, exist_ok=True)  

                # Copy the generated file back
                result = subprocess.run(
                    ["scp", f"{remoteHost}:{remoteTrace}", str(targetPath)],
                    check=True
                )
                
                if result.returncode == 0:
                    HelperFunctions.printState("Downloaded: ", info= traceName + " to " + str(targetPath))
                else:
                    HelperFunctions.printState("[ERROR] Could not download the file", info= traceName)
            else:
                print("[ERROR] Something went wrong...")

        else:
            print(str(remoteScript) + " does not exists on " + remoteHost)
            print("Make sure https://github.com/matthiasthomasbecker/bpfTrace exists on the target platform!")


    # Enable the buttons and update the GUI
    if gui is not None:
        gui.btn_recordTrace.configure(state="normal")
        # Update trace view option menu
        gui.updateTraceViewOptions()
        gui.update()