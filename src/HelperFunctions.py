import sys
import os
from pathlib import Path
import platform
from datetime import datetime
import configparser
import subprocess
import tempfile
import traceback
import tkinter.font
from threading import Thread
from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError

# Defined symbols exported by target/trace.c and the SDK's RTT implementation.
_PICO_TRACE_SYMBOLS = ('trace_init', '__pre_init_trace_init',
                       'trace_state_core0', 'trace_state_core1', '_SEGGER_RTT')

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

def getUiScale():
    """
    Returns the UI scale: ui_scale from the config, or "auto". With "auto", Linux follows the desktop
    scaling (Xft.dpi, else GDK_SCALE), customtkinter already does this on macOS and Windows.
    """
    value = readConfig().get("general", "ui_scale", fallback="auto").strip().lower()
    if value != "auto":
        try:
            return min(max(float(value), 0.5), 4.0)
        except ValueError:
            print("Ignoring invalid ui_scale in the config: " + value, file=sys.stderr)

    if not sys.platform.startswith("linux"):
        return 1.0

    scale = 1.0
    try:
        resources = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=2).stdout
        for line in resources.splitlines():
            if line.startswith("Xft.dpi:"):
                scale = float(line.split(":", 1)[1]) / 96
    except (OSError, ValueError, subprocess.TimeoutExpired):     # No xrdb, or no X resources
        pass
    if scale == 1.0:
        try:
            scale = float(os.environ.get("GDK_SCALE", "1")) * float(os.environ.get("GDK_DPI_SCALE", "1"))
        except ValueError:
            pass
    return min(max(scale, 0.5), 4.0)

def scaleTkFonts(root, scale):
    """
    Scales Tk's named fonts, which the trace view and the menus use. customtkinter widgets are scaled separately.
    Negative sizes are pixels. Positive sizes are points, which Tk already converts with its own DPI.
    """
    pointScale = scale * 96 / (72 * float(root.tk.call("tk", "scaling")))
    for name in ["TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"]:
        font = tkinter.font.nametofont(name, root=root)
        size = font.cget("size")
        font.configure(size=round(size * (scale if size < 0 else pointScale)))

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
        except Exception as e:      # Last resort: the workers report their own errors, but no traceback may reach the GUI log
            reportError("Unexpected error", e, None, "rt-trace_error.log", "Please report this together with the log file.")
        finally:
            if gui is not None:
                gui.runInMainThread(lambda: onFinished(result))

    Thread(target = run, daemon = True).start()

def printState(state: str, info=""):
    
    print("=> " + state.upper(), end="")    # Separate writes, so the output box highlights only the state
    print(" " + info)

def writeErrorLog(folder, fileName, exc, context=""):
    """
    Writes the traceback of exc to fileName in folder (or the temp folder) and returns the path, or None.
    """
    for base in ([folder] if folder else []) + [tempfile.gettempdir()]:
        try:
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, fileName)
            with open(path, "w") as f:
                f.write(datetime.now().isoformat(timespec="seconds") + "\n")
                if context:
                    f.write(context.rstrip() + "\n")
                f.write("\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            return path
        except OSError:
            continue
    return None

def reportError(what, exc, folder, logName, hint="", context="", showType=True):
    """
    Prints one error line for exc with a hint, and writes the full traceback to a log file.
    showType adds the exception type, for errors that are not meant for the user.
    """
    path = writeErrorLog(folder, logName, exc, context)
    message = str(exc) if str(exc) else type(exc).__name__
    if showType and type(exc).__name__ not in message:
        message = type(exc).__name__ + ": " + message
    info = message.rstrip(".") + "."
    if hint:
        info += " " + hint
    if path:
        info += " Details: " + path
    printState("ERROR " + what, info=info)

def recordHint(exc):
    """
    A hint for an error that the debug probe or pyOCD raised while recording.
    """
    text = (type(exc).__name__ + " " + str(exc)).lower()
    if any(k in text for k in ("no probe", "usb", "libusb", "access denied", "permission", "busy", "unable to open")):
        return ("Connect the debug probe, close other programs that use it (OpenOCD, the VS Code debugger, another RT-Trace) "
                "and on Linux install the udev rules (see the ES-Lab-Kit README).")
    if any(k in text for k in ("timeout", "transfer", "fault", "swd", "dap")):
        return "The probe lost the connection to the RP2350. Check the SWD cable and the board power, or lower frequency in the config."
    if "elf" in text:
        return "Build the program first."
    return "Check the probe connection and that the program runs (see the details)."

def recordContext(gui, elf):
    return "Program: " + str(elf) + "\nPlatform: " + getTargetName(gui) + "\nRecording: " + str(gui.recordingFolder)

NO_PROBE_HINT = ("No debug probe found. Connect the debug probe to USB and to the SWD pins of the lab kit, "
                 "close other programs that use it, and on Linux install the udev rules (see the ES-Lab-Kit README).")

def printHeader(header:str):
    print("\n=== " + header.upper() + " ===")

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

projectPathOverride = None     # Set by the --project-path option, used instead of project_path for this run

def getProjectPath():
    """
    Get the folder that contains the programs that can be traced (project_path in the config).
    A relative path is resolved against the RT_Trace folder, so it does not matter from where RT-Trace
    is started. Earlier versions resolved it against the current working directory, which is still
    used if the path only exists there.
    """
    if projectPathOverride is not None:
        return projectPathOverride
    projectPath = os.path.expanduser(readConfig().get('general','project_path'))
    rtTraceFolder = os.path.dirname(os.path.dirname(getConfigFilePath()))   # <RT_Trace>/Resources/config.ini
    resolved = os.path.abspath(os.path.join(rtTraceFolder, projectPath))
    if not os.path.isdir(resolved) and os.path.isdir(os.path.abspath(projectPath)):
        return os.path.abspath(projectPath)
    return resolved

def getPossiblePrograms():
    """ 
    The function searches all projects found in the project folder. 
    Each folder with a CMakeLists.txt is a project.
    """
    programPath = getProjectPath()

    if not Path(programPath).exists():
        return []
    
    return sorted(f for f in get_subfolders(programPath) if isProjectFolder(os.path.join(programPath, f)))

def isProjectFolder(path):
    """ A folder with a CMakeLists.txt is a project. """
    return os.path.isfile(os.path.join(path, "CMakeLists.txt"))

def askProjectFolder(parent):
    """
    Opens the native folder picker and returns the selected folder, or None if it was cancelled.
    On Linux, Tk only has its own dialog, so the desktop's picker is used through zenity or kdialog if one is installed.
    """
    initialDir = getProjectPath() if os.path.isdir(getProjectPath()) else os.path.expanduser("~")
    title = "Select a project, or a folder with projects"
    if platform.system() == "Linux":
        for cmd in (["zenity", "--file-selection", "--directory", "--title=" + title, "--filename=" + os.path.join(initialDir, "")],
                    ["kdialog", "--getexistingdirectory", initialDir, "--title", title]):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
            except FileNotFoundError:
                continue                                    # Picker not installed, try the next one
            folder = result.stdout.strip()
            return folder if result.returncode == 0 and folder else None
    from tkinter import filedialog
    folder = filedialog.askdirectory(parent=parent, initialdir=initialDir, title=title, mustexist=True)
    return folder if folder else None

def folderIcon(color="#FFFFFF"):
    """
    Returns a folder icon for the project folder button, drawn so that no image file is needed.
    """
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 10, 28, 24), radius=4, fill=color)     # Tab
    draw.rounded_rectangle((4, 16, 60, 54), radius=6, fill=color)     # Body
    return image

def setProjectPath(projectPath):
    """
    Saves project_path in config.local.ini. Other lines of the file, including comments, are kept.
    """
    global projectPathOverride
    projectPathOverride = None      # A folder picked in the GUI replaces the --project-path option
    localConfig = getLocalConfigFilePath()
    lines = Path(localConfig).read_text().splitlines() if Path(localConfig).exists() else []
    entry = "project_path = " + projectPath

    section = None
    general = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            if section == "general" and general is None:
                general = i
        elif section == "general" and stripped.split("=")[0].strip() == "project_path":
            lines[i] = entry
            break
    else:
        if general is None:
            lines += ([""] if lines else []) + ["[general]", entry]
        else:
            lines.insert(general + 1, entry)

    Path(localConfig).write_text("\n".join(lines) + "\n")

def getElfFilePath(gui):

    programPath = getProjectPath()
    elfFilePath = os.path.abspath(os.path.join(programPath, gui.selectedProgram, "build", gui.selectedProgram + ".elf"))

    return elfFilePath

def getMissingElfSymbols(elfFilePath, names):
    """Return required symbols that have no definition in the built ELF."""
    with open(elfFilePath, "rb") as f:
        symtab = ELFFile(f).get_section_by_name(".symtab")
        return [name for name in names if symtab is None or not any(
            symbol['st_shndx'] != 'SHN_UNDEF'
            for symbol in (symtab.get_symbol_by_name(name) or []))]

def getElfSymbol(elfFilePath, name):
    """
    Returns (address, size in bytes) of a symbol in an ELF file, or None if it is not there.
    """
    try:
        with open(elfFilePath, "rb") as f:
            symtab = ELFFile(f).get_section_by_name(".symtab")
            if symtab is None:
                return None
            symbols = symtab.get_symbol_by_name(name)
            if not symbols:
                return None
            return symbols[0]["st_value"], symbols[0]["st_size"]
    except (OSError, ELFError):
        return None

def noTracingHint(elfFilePath, missing=('trace_init',)):
    """
    Explains what to do if a program was built without RT-Trace support.
    """
    return (os.path.basename(elfFilePath) + " is missing RT-Trace symbols: " + ", ".join(missing) + ". "
            "Build with rt_trace_enable(firmware), include trace.h at the end of FreeRTOSConfig.h, "
            "and preserve the ELF symbol table (see target/README.md).")

def validatePicoTrace(gui, quiet=False):
    """
    Check the selected firmware for linked RT-Trace support before recording.
    Source configuration/CMake text is not evidence of the contents of this ELF.
    quiet suppresses success output; failures are always reported.
    """
    if not gui.selectedProgram:
        printState("ERROR no program", info="Select a program first")
        return False
    elfFilePath = getElfFilePath(gui)
    programPath = os.path.join(getProjectPath(), gui.selectedProgram)

    if not Path(elfFilePath).is_file():
        printState("ERROR ELF not found", info=os.path.relpath(elfFilePath, programPath) + " in " + programPath + ", build the program first")
        return False

    try:
        missing = getMissingElfSymbols(elfFilePath, _PICO_TRACE_SYMBOLS)
    except (OSError, ELFError) as error:
        printState("ERROR invalid ELF", info=os.path.basename(elfFilePath) + ": " + str(error) + ". Rebuild the firmware.")
        return False
    if missing:
        printState("ERROR no tracing", info=noTracingHint(elfFilePath, missing))
        return False

    if not quiet:
        printState("Program", info=gui.selectedProgram + " (RT-Trace RTT, " + os.path.relpath(elfFilePath, programPath) + ")")
    return True
