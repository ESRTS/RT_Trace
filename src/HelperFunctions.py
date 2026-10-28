import sys
import os
from pathlib import Path
import platform
from datetime import datetime
import configparser
from threading import Thread

def getCwd():
    """
    Get the folder of the RT-Trace sources. Config, data and output folders are located relative to it.
    In the packaged macOS app this is the folder of the executable (RT-Trace.app/Contents/MacOS).
    """
    if platform.system() == "Darwin" and getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    else:
        #return os.getcwd()
        return Path(__file__).resolve().parent
    
def getConfigFilePath():
    """
    Get the path to the configuration file.
    """
    cwd = getCwd()

    ''' Set default values for the GUI '''
    system = platform.system()
    if system == "Windows":
        return os.path.abspath(os.path.join(os.path.dirname( cwd ), 'Resources', 'config.ini'))
    elif system == "Darwin":
        return os.path.abspath(os.path.join(os.path.dirname( cwd ), 'Resources', 'config.ini'))
    else:  # Linux
        devPath = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'Resources', 'config.ini'))
        if Path(devPath).exists():
            return devPath
        
        packagePath = os.path.abspath(os.path.join(os.path.dirname( cwd ), '_internal', 'config.ini'))
        if Path(packagePath).exists():
            return packagePath

        assert False, "Could not find config.ini at " + devPath + " or at " + packagePath

def getLocalConfigFilePath():
    """
    Get the path to the optional local configuration file. It sits next to config.ini, is not
    tracked by git and overrides any value of config.ini, so machine specific settings
    (project folder, SSH host, ...) do not have to be committed.
    """
    return os.path.join(os.path.dirname(getConfigFilePath()), 'config.local.ini')

def readConfig():
    """
    Read config.ini and, if it exists, config.local.ini on top of it.
    """
    config = configparser.ConfigParser()
    config.read([getConfigFilePath(), getLocalConfigFilePath()])
    return config

def startWorker(gui, worker, onFinished):
    """
    Run worker(gui) in a background thread so the GUI stays responsive.
    Tk is not thread-safe, so the worker must not touch any widget. Instead, onFinished(result)
    is executed in the GUI thread once the worker is done, also if the worker raised an exception.
    """
    def run():
        result = None
        try:
            result = worker(gui)
        finally:
            if gui is not None:
                gui.runInMainThread(lambda: onFinished(result))

    Thread(target = run, daemon = True).start()

def printState(state: str, info=""):
    
    inner = "\r\n=> " + state.upper() + " " + info
    print( inner ) 

def printHeader(header:str):
    width = 70

    assert len(header) <= width - 4, "Header string too long for the configured width of the text in printHeader()" + header

    # Plain ASCII, box drawing characters are not available in the fonts Tk uses on every platform.
    inner = "| " + header.upper().ljust(width - 4) + " |"
    top = "\r\n+" + "-" * (width - 2) + "+"
    bottom = "+" + "-" * (width - 2) + "+"

    print( "\n".join([top, inner, bottom]) )  

def hexdump(buffer: bytes, base_addr: int = 0, width: int = 16):
        for offset in range(0, len(buffer), width):
            chunk = buffer[offset:offset + width]

            # Address
            addr = base_addr + offset

            # Hex part
            hex_bytes = ' '.join(f'{b:02x}' for b in chunk)
            hex_bytes = hex_bytes.ljust(width * 3 - 1)

            # ASCII part (optional but useful)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)

            print(f"0x{addr:08x}  {hex_bytes}  |{ascii_part}|") 

def getTimeString():
    """
    Returns a time string. 
    """
    now = datetime.now()
    formatted = now.strftime("%Y-%m-%d_%H-%M-%S")
    
    return formatted

def getTargetName(gui):
    '''
    Get the name of the currently selected target.
    '''
    return gui.targets[gui.selectedTarget].get('name').replace(' ', '_')

def getRecordedTraces(gui):
    """
    Gets the recorded trace folders of the current platform configuration. Sorted by the folder creation time, most recent first.
    """
    recordedTraces = []

    folderName = os.path.abspath(os.path.join(os.path.dirname( getCwd() ), 'data', gui.targets[gui.selectedTarget].get('name').replace(' ', '_')))

    path = Path(folderName)

    if path.exists() and path.is_dir():

        recordedTraces = [p for p in path.iterdir() if p.is_dir()]

        # Sort by creation time (most recent first)
        recordedTraces.sort(key=lambda p: p.stat().st_ctime, reverse=True)

        # Return only folder names
        return [p.name for p in recordedTraces]
    else:
        return []


def getRecordingFolderName(gui):
    """ Helper to create the absolute path for this experiment configuration. """
    targetName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')
    measurementName = f"{gui.txt_traceName.get()}_{getTimeString()}"
    return os.path.abspath(os.path.join(os.path.dirname( getCwd() ), 'data', targetName, measurementName))
    
def getViewingFolderName(gui):
    """ Helper to create the absolute path for the trace visualization. """
    targetName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')
    measurementName = gui.opt_selectTrace.get()
    return os.path.abspath(os.path.join(os.path.dirname( getCwd() ), 'data', targetName, measurementName))

def getOutputPath(gui):
    """ Helper to create the absolute path for the trace visualization. """
    targetName = gui.targets[gui.selectedTarget].get('name').replace(' ', '_')
    measurementName = gui.opt_selectTrace.get()
    return os.path.abspath(os.path.join(os.path.dirname( getCwd() ), 'output', targetName, measurementName))

def makeFolder(pathstring: str):
    """ Wrapper to create a folder. """
    path = Path(pathstring)
    path.mkdir(parents=True, exist_ok=True)

def get_subfolders(path):
    return [p.name for p in Path(path).iterdir() if p.is_dir()]

def getProjectPath():
    """
    Get the folder that contains the programs that can be traced (project_path in the config).
    A relative path is resolved against the RT_Trace folder, so it does not matter from where RT-Trace
    is started. Earlier versions resolved it against the current working directory, which is still
    used if the path only exists there.
    """
    projectPath = os.path.expanduser(readConfig().get('general','project_path'))
    rtTraceFolder = os.path.dirname(os.path.dirname(getConfigFilePath()))   # <RT_Trace>/Resources/config.ini
    resolved = os.path.abspath(os.path.join(rtTraceFolder, projectPath))
    if not os.path.isdir(resolved) and os.path.isdir(os.path.abspath(projectPath)):
        return os.path.abspath(projectPath)
    return resolved

def getPossiblePrograms():
    """ 
    The function searches all projects found in the project folder. 
    The assumption is that each folder is a project. 
    """
    programPath = getProjectPath()

    if not Path(programPath).exists():
        return []
    
    return get_subfolders(programPath)

def validateProgramFolder(gui, selectedFolder) -> bool:

    traceConfigFilePath = getTraceConfigPath(gui)
    elfFilePath = getElfFilePath(gui)

    file = Path(elfFilePath)

    if not file.exists():
        return False
    else:
        return True

def getTraceConfigPath(gui):

    programPath = getProjectPath()
    traceConfigFilePath = os.path.abspath(os.path.join(programPath, gui.selectedProgram, "traceConfig.h"))

    return traceConfigFilePath

def getElfFilePath(gui):

    programPath = getProjectPath()
    elfFilePath = os.path.abspath(os.path.join(programPath, gui.selectedProgram, "build", gui.selectedProgram + ".elf"))

    return elfFilePath

def validatePicoRtt(gui):
    traceConfigFilePath = getTraceConfigPath(gui)
    elfFilePath = getElfFilePath(gui)

    confFile = Path(traceConfigFilePath)
    elfFile = Path(elfFilePath)
    
    if not elfFile.exists():
        printState("ERROR ELF not found", info=elfFilePath)
        return False
    else:
        printState("ELF", info=elfFilePath)

        if not confFile.exists():
            printState("ERROR trace config not found", info=traceConfigFilePath)
            return False
        else:
            printState("Trace Config", info=traceConfigFilePath)
            return True