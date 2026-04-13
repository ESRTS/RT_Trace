import customtkinter
import sys
from TraceView import TraceView
from PicoTrace import loadPico2TraceBuffers
from PicoTrace import loadPico2TraceBuffersPSRAM
from L476Trace import loadSTM32L476TraceBuffers
from LinuxTraceRecorder import loadLinuxraceBuffers
from TraceParser import parseTraceFiles
from TraceParserLinux import parseTraceFiles as linuxParseTraceFiles
from pathlib import Path
import subprocess
import os
from datetime import datetime
import FileHelper
import configparser
import queue

class TraceApp(customtkinter.CTk):
    """
    Main class of the RT-Trace app. 
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        """
        Here all available targets are configures. If the flag 'implemented' is False, they are not supported yet.
        'requirement_str' can be used to inform the user of any additional prerequisites, this is displayed in the textbox once the target is selected. 
        'recordTraceFunc' is a target specific function that loads the trace buffer
        """
        self.targets = [
            {'name': 'Pico2 FreeRTOS', 'numCores': 2, 'implemented': True, 'requirement_str' : 'To load the trace buffer, openocd and telnet need to be on the path.', 'recordTraceFunc' : loadPico2TraceBuffers},
            {'name': 'Pico2 FreeRTOS PSRAM', 'numCores': 2, 'implemented': True, 'requirement_str' : 'To load the trace buffer, openocd and telnet need to be on the path.', 'recordTraceFunc' : loadPico2TraceBuffersPSRAM},
            #{'name': 'STM FreeRTOS', 'numCores': 1, 'implemented': True, 'requirement_str' : 'To load the trace buffer, openocd and telnet needs to be on the path.', 'recordTraceFunc' : loadSTM32L476TraceBuffers},
            #{'name': 'RPI QNX', 'numCores': 4, 'implemented': False, 'requirement_str' : 'To load the trace buffer, telnet needs to be on the path.', 'recordTraceFunc' : None},
            {'name': 'RPI Linux', 'numCores': 4, 'implemented': True, 'requirement_str' : 'Experimental...', 'recordTraceFunc' : loadLinuxraceBuffers}
        ]

        ''' Get the path for ps2pdf. '''
        config = configparser.ConfigParser()
        config.read(FileHelper.getConfigFilePath())
        self.ps2pdf_path = config.get('general','ps2pdf_path', fallback = '/usr/local/bin')

        ''' Set the default size of the GUI window and give it a name. '''
        self.windowSizeY = 600
        self.windowSizeX = self.winfo_screenwidth()
        self.maxScreenSizeY = self.winfo_screenheight()

        self.geometry("{}x{}".format(self.windowSizeX, self.windowSizeY))
        self.minsize(800, 400)
        self.title("RT-Trace")

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure((0, 1, 2, 3, 4, 5, 6, 7, 8), weight=1)

        ''' Create a frame for each main GUI area. '''
        self.sidebar_frame = customtkinter.CTkFrame(self, width=140, corner_radius=10)
        self.sidebar_frame.grid(row=0, column=0, rowspan=9, sticky="nsew", padx=5, pady=5)
        self.sidebar_frame.grid_rowconfigure(9, weight=1)

        ''' Label for the platform selection. '''
        self.lbl_selectPlatform = customtkinter.CTkLabel(self.sidebar_frame, text="Platform Selection:", font=customtkinter.CTkFont(size=15, weight="bold"))
        self.lbl_selectPlatform.grid(row=0, column=0, padx=(0, 0), pady=(10, 0))

        ''' Option to select the trace source. '''
        self.selectValues = []
        for target in self.targets:
            if len(self.selectValues) == 0:
                self.selectedTarget = self.targets.index(target)     # Select the first target in the list
            self.selectValues.append(target.get('name'))        # Create a list with all target names for the option menu

        self.opt_selectSource = customtkinter.CTkOptionMenu(self.sidebar_frame, values=self.selectValues, command=self.selectTraceSource)
        self.opt_selectSource.grid(row=1, column=0, padx=20, pady=(0, 5), sticky="ew")
        
        ''' Label for the trace recorder section. '''
        self.lbl_recording = customtkinter.CTkLabel(self.sidebar_frame, text="Record Trace", font=customtkinter.CTkFont(size=15, weight="bold"), anchor="w")
        self.lbl_recording.grid(row=2, column=0, padx=(0, 0), pady=(10, 0))

        ''' Button to start recording a new trace from a target. '''
        self.btn_recordTrace = customtkinter.CTkButton(self.sidebar_frame, text="Record Trace", command=self.button_record_function)
        self.btn_recordTrace.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        ''' Entry to specify the trace name. '''
        self.txt_traceName = customtkinter.CTkEntry(self.sidebar_frame, placeholder_text=self.targets[self.selectedTarget].get('name').replace(' ', '_'))
        self.txt_traceName.grid(row=4, column=0, padx=20, pady=5, sticky="ew")
        
        ''' Label for the trace visualization section. '''
        self.lbl_view = customtkinter.CTkLabel(self.sidebar_frame, text="View Trace", font=customtkinter.CTkFont(size=15, weight="bold"), anchor="w")
        self.lbl_view.grid(row=5, column=0, padx=(0, 0), pady=(10, 0))

        ''' Option to select which of the recorded traces to display. '''
        self.selectTrace = []
        self.selectTrace.append('Test')
        self.selectTrace.append('Test_2')
        self.opt_selectTrace = customtkinter.CTkOptionMenu(self.sidebar_frame, values=self.selectTrace, command=self.selectRecordedTrace)
        self.opt_selectTrace.grid(row=6, column=0, padx=20, pady=(0, 5), sticky="ew")

        ''' Button to load an existing trace. '''
        self.btn_loadTrace = customtkinter.CTkButton(self.sidebar_frame, text="Load Trace", command=self.load_function)
        self.btn_loadTrace.grid(row=7, column=0, padx=20, pady=5, sticky="ew")

        ''' Button to save the current trace. '''
        self.btn_saveTrace = customtkinter.CTkButton(self.sidebar_frame, text="Save PDF", command=self.save_image_function)
        self.btn_saveTrace.grid(row=8, column=0, padx=20, pady=5, sticky="ew")

        ''' Textbox to display stdout. '''
        font = customtkinter.CTkFont(family="DejaVu Sans Mono", size=14)
        self.textbox = customtkinter.CTkTextbox(self, corner_radius=10, font=font)
        self.textbox.grid(row=7, column=1, rowspan=2, columnspan=1, sticky="nswe", padx=5, pady=5)
        
        ''' Redirect stdout and stderr to the textbox. '''
        self.textbox.tag_config('stderr', foreground="red")
        self.textbox.tag_config('stdout', foreground="black")

        sys.stdout = TextRedirector(self.textbox, "stdout")
        sys.stderr = TextRedirector(self.textbox, "stderr")

        ''' Execution Trace Widget. '''
        self.traceView = TraceView(self)
        self.traceView.grid(row=0, column=1, rowspan=7, columnspan=1, sticky="nswe", padx=5, pady=5)

        ''' Print Events Switch '''
       #self.printEvents_var = customtkinter.BooleanVar(value=False)
       # self.switch = customtkinter.CTkSwitch(self.sidebar_frame, text="Print Events", command=self.printEventsSwitch_event,
       #                          variable=self.printEvents_var, onvalue=True, offvalue=False)
       # self.switch.grid(row=4, column=0, padx=20, pady=5, sticky="ew")

        ''' Show System Tasks Switch '''
       # self.showSysTasks_var = customtkinter.BooleanVar(value=True)
       # self.switch = customtkinter.CTkSwitch(self.sidebar_frame, text="Show System Tasks", command=self.showSystemTasks_event,
       #                          variable=self.showSysTasks_var, onvalue=True, offvalue=False)
       # self.switch.grid(row=5, column=0, padx=20, pady=5, sticky="ew")

        ''' Bind the key handler function. '''
        self.bind("<Key>", self.keyHandler)

        ''' Bind the handler functions to move the trace. '''
        self.traceView.bind("<B1-Motion>", self.traceView.mouseDragHandler)
        self.traceView.bind("<ButtonPress>", self.traceView.buttonPressed)
        self.traceView.bind("<ButtonRelease>", self.traceView.buttonReleased)

        ''' Bind the function to handle window resize events. '''
        self.traceView.bind("<Configure>", self.resize_window_function)

        ''' Print the requirement string for the initially selected target. '''
        print(self.targets[self.selectedTarget].get('requirement_str'))

    def printEventsSwitch_event(self):
        pass

    def showSystemTasks_event(self):
        pass

    def resize_window_function(self, event):
        """
        Function is called to handle window resize events.
        """
        
        self.traceView.draw()

    def keyHandler(self, event):
        """
        Key-handler function. Used to control the trace view.
        """
        if event.keycode == 2113992448: # Up arrow
            self.traceView.zoom(1)
        elif event.keycode == 2097215233: # Down arrow
            self.traceView.zoom(-1)

    def disableAllButtons(self):
        """
        Function disables all buttons. 
        """
        self.btn_loadTrace.configure(state="disabled")
        self.btn_recordTrace.configure(state="disabled")
        self.btn_saveTrace.configure(state="disabled")
        self.update()

    def enableAllButtons(self):
        """
        Function enables all buttons.
        """
        self.btn_loadTrace.configure(state="enabled")
        self.btn_recordTrace.configure(state="enabled")
        self.btn_saveTrace.configure(state="enabled")
        self.update()

    def button_record_function(self):
        """
        Callback that ius called if the button "Record Trace" is clicked.
        """
        self.btn_recordTrace.configure(state="disabled")
        self.update()
        FileHelper.printHeader("recording trace")
        self.targets[self.selectedTarget].get('recordTraceFunc')(self)   # Call the target specific function to load the trace buffers
        

    def load_function(self):
        """
        Callback that is called if the button "Load Trace" is clicked.
        """
        self.btn_loadTrace.configure(state="disabled")
        self.update()
        FileHelper.printHeader("Loading trace from files")
        if self.targets[self.selectedTarget].get('name') != 'RPI Linux':
            parseTraceFiles(self, self.targets[self.selectedTarget].get('numCores'))
        else:
            linuxParseTraceFiles(self, self.targets[self.selectedTarget].get('numCores'))

    def selectTraceSource(self, traceSource: str):
        """
        Callback that is called if a new target is selected from the option menu.
        """
        for target in self.targets:
            if target.get('name') is traceSource:
                self.selectedTarget = self.targets.index(target)

        self.textbox.configure(state=customtkinter.NORMAL)  # Reset the textbox if a different target is selected
        self.textbox.delete(1.0, 'end')
        self.textbox.configure(state=customtkinter.DISABLED)

        self.traceView.setTasks(None)
        self.traceView.draw()

        if self.targets[self.selectedTarget].get('implemented') is True:    # Make sure only to load from supported targets
            print(self.targets[self.selectedTarget].get('requirement_str'))
            self.enableAllButtons()
        else:
            FileHelper.printState("Target not yet supported", info = self.targets[self.selectedTarget].get('name'))
            self.disableAllButtons()

    def selectRecordedTrace(self, recordedTrace: str):
        """
        Callback that is called if a new recorded trace is selected from the option menu. 
        """
        print("Now selected: " + recordedTrace)

    def save_image_function(self):
        """
        Function generates a PDF of the current trace view.
        """
        FileHelper.printHeader("export trace to pdf")
        if self.traceView.tasks is not None:    # Only save the trace as PDF if some tasks are loaded

            now = datetime.now()

            cwd = FileHelper.getCwd()
            targetPath = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'output'))
            Path(targetPath).mkdir(parents=True, exist_ok=True)
            pdfFilename = self.targets[self.selectedTarget].get('name') + "_Trace_" + now.strftime("%d_%m_%Y_%H_%M_%S")
            pdfFilename = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'output', pdfFilename + ".pdf"))

            psFilename = os.path.abspath(os.path.join(os.path.dirname( cwd ), 'output', "tmp.ps"))

            self.traceView.postscript(file=psFilename, colormode="color")                 # Generate the postscript of the trace canvas

            my_env = os.environ.copy()
            my_env["PATH"] = f"{my_env['PATH']}:{self.ps2pdf_path}" # This is not final, a config file for the openocd path should be added
            process = subprocess.Popen(["ps2pdf", "-dEPSCrop", psFilename, pdfFilename], env=my_env)  # Convert the postscript file to PDF (requires ps2pdf)
            streamdata = process.communicate()[0]                                       # Get the return code of the process
            rc = process.returncode
            if rc == 0:
                FileHelper.printState("Saved trace as ", info = pdfFilename)
            else:
                print("Cound not generate PDF file.", file=sys.stderr)
            os.remove(psFilename)                                                         # Delete the temporary postscript file
        else:
            FileHelper.printState("No trace loaded!")

class TextRedirector(object):
    """
    Class to redirect stdout and stderr to a textbox widget.
    """
    def __init__(self, widget, tag="stdout"):
        self.widget = widget
        self.tag = tag

        self.queue = queue.Queue()
        self._scheduled = False

    def write(self, string):
        #self.widget.configure(state="normal")
        #self.widget.insert("end", string, (self.tag,))  # Add the string to the textbox
        #self.widget.configure(state="disabled")
        #self.widget.see("end")                          # Scroll the view to the end

        if string:
            self.queue.put(string)
            if not self._scheduled:
                self._scheduled = True
                self.widget.after(30, self._flush)

    def flush(self):    # Needed to have the interface of a file-like object
        pass   
    
    def _flush(self):
        self.widget.configure(state="normal")

        while not self.queue.empty():
            self.widget.insert("end", self.queue.get_nowait(), (self.tag,))

        self.widget.configure(state="disabled")
        self.widget.see("end")
        self._scheduled = False

def main():
    """
    Main Function of the program.
    """
    customtkinter.set_appearance_mode("light")      # Modes: system (default), light, dark
    customtkinter.set_default_color_theme("blue")   # Themes: blue (default), dark-blue, green

    app = TraceApp()                                # Create the app instance
    app.mainloop()                                  # Start the main loop of the GUI

if __name__ == "__main__":

    main()