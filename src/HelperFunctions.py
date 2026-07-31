import sys
import os
from pathlib import Path
import platform
from datetime import datetime
import configparser

def getCwd():
    """
    Get the current working directory, this is different on OSX in the app version.  
    """
    if platform.system() == "Darwin":
        return os.path.sep.join(sys.argv[0].split(os.path.sep)[:-1])
    else:
        return os.getcwd()
    
def getConfigFilePath():
    """
    Get the path to the configuration file.
    """
    cwd = getCwd()

    return os.path.abspath(os.path.join(os.path.dirname( cwd ), 'Resources', 'config.ini'))

def printState(state: str, info=""):
    
    inner = "\r\n=> " + state.upper() + " " + info
    print( inner ) 

def printHeader(header:str):
    width = 70

    assert len(header) <= width - 4, "Header string too long for the configured width of the text in printHeader()" + header

    inner = "┃ " + header.upper().ljust(width - 4) + " ┃" 
    top = "\r\n┏" + "━" * (width - 2) + "┓"
    bottom = "┗" + "━" * (width - 2) + "┛"

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
    formatted = now.strftime("%Y-%m-%d_%H:%M:%S")
    
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

def getPossiblePrograms():
    """ 
    The function searches all projects found in the project folder. 
    The assumption is that each folder is a project. 
    """
    config = configparser.ConfigParser()
    config.read(getConfigFilePath())
    programPath = config.get('general','project_path')

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

    config = configparser.ConfigParser()
    config.read(getConfigFilePath())
    programPath = config.get('general','project_path')
    traceConfigFilePath = os.path.abspath(os.path.join(programPath, gui.selectedProgram, "traceConfig.h"))

    return traceConfigFilePath

def getElfFilePath(gui):

    config = configparser.ConfigParser()
    config.read(getConfigFilePath())
    programPath = config.get('general','project_path')
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