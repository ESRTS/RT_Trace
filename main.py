import customtkinter
import sys
from TraceView import TraceView
from PicoTrace import loadTraceBuffers
from TraceParser import parseTraceFiles
from pathlib import Path
import subprocess
import os

"""
ID for each target selection
"""
PICO2_FREERTOS = 0
LINUX = 1
QNX = 2

"""
A map to link the target ID to the target name
"""
target_map = {
    PICO2_FREERTOS : 'Pico2 FreeRTOS',
    LINUX : 'Linux',
    QNX: 'QNX'
}

"""
A list with all targets that are supported at the moment.
"""
supported_targets = [PICO2_FREERTOS]

class TraceApp(customtkinter.CTk):
    """
    Main class of the RT-Trace app. 
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        ''' Set the default size of the GUI window and give it a name. '''
        self.geometry(str(self.winfo_screenwidth()) + "x500")
        self.minsize(800, 400)
        self.title("RT-Trace View")

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure((0, 1, 2, 3), weight=1)

        ''' Create a frame for each main GUI area. '''
        self.sidebar_frame = customtkinter.CTkFrame(self, width=140, corner_radius=10)
        self.sidebar_frame.grid(row=0, column=0, rowspan=4, sticky="nsew", padx=5, pady=5)
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        ''' Option to select the trace source. '''
        self.selectValues = []
        for target in target_map:
            if len(self.selectValues) == 0:
                self.selectedTarget = target    # Select the first target in the list
            self.selectValues.append(target_map.get(target)) # Create a list with all target names for the option menu
        self.opt_selectSource = customtkinter.CTkOptionMenu(self.sidebar_frame, values=self.selectValues, command=self.selectTraceSource)
        self.opt_selectSource.grid(row=0, column=0, padx=20, pady=(20, 5), sticky="ew")
        

        ''' Button to start recording a new trace from a target. '''
        self.btn_recordTrace = customtkinter.CTkButton(self.sidebar_frame, text="Record Trace", command=self.button_record_function)
        self.btn_recordTrace.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        ''' Button to load an existing trace. '''
        self.btn_loadTrace = customtkinter.CTkButton(self.sidebar_frame, text="Load Trace", command=self.load_function)
        self.btn_loadTrace.grid(row=2, column=0, padx=20, pady=5, sticky="ew")

        ''' Button to save the current trace. '''
        self.btn_saveTrace = customtkinter.CTkButton(self.sidebar_frame, text="Save Trace", command=self.save_image_function)
        self.btn_saveTrace.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        ''' Textbox to display stdout. '''
        self.textbox = customtkinter.CTkTextbox(self, corner_radius=10)
        self.textbox.grid(row=3, column=1, rowspan=1, columnspan=1, sticky="nswe", padx=5, pady=5)
        
        ''' Redirect stdout and stderr to the textbox. '''
        sys.stdout = TextRedirector(self.textbox, "stdout")
        #sys.stderr = TextRedirector(self.textbox, "stderr")

        ''' Execution Trace Widget. '''
        self.traceView = TraceView(self)
        self.traceView.grid(row=0, column=1, rowspan=3, columnspan=1, sticky="nswe", padx=5, pady=5)

        ''' Bind the key handler function. '''
        self.bind("<Key>", self.keyHandler)

        ''' Bind the handler functions to move the trace. '''
        self.traceView.bind("<B1-Motion>", self.traceView.mouseDragHandler)
        self.traceView.bind("<ButtonPress>", self.traceView.buttonPressed)
        self.traceView.bind("<ButtonRelease>", self.traceView.buttonReleased)

        ''' Bind the function to handle window resize events. '''
        self.traceView.bind("<Configure>", self.resize_window_function)

    def resize_window_function(self, event):
        """
        Function is called to handle window resize events.
        """
        
        self.traceView.draw()

    def keyHandler(self, event):
        """
        Key-handler function. Used to control the trace view.
        """
        #print(event.char, event.keysym, event.keycode)
        if event.keycode == 2063660802: # Left arrow
            print("LEFT")
        elif event.keycode == 2080438019: # Right arrow
            print("RIGHT")
        elif event.keycode == 2113992448: # Up arrow
            self.traceView.zoom(1)
        elif event.keycode == 2097215233: # Down arrow
            self.traceView.zoom(-1)

    def button_record_function(self):
        """
        Callback that ius called if the button "Record Trace" is clicked.
        """
        if self.selectedTarget in supported_targets:    # Make sure only to load from supported targets
            self.btn_recordTrace.configure(state="disabled")
            self.update()
            print("Reading the trace buffer for each core. Might take a few seconds...")
            loadTraceBuffers(self)   
        else:
            print("Reading the trace buffer from target " + target_map.get(self.selectedTarget) + " is not yet supported.")

    def load_function(self):
        """
        Callback that ius called if the button "Load Trace" is clicked.
        """
        if self.selectedTarget in supported_targets:    # Make sure only to load from supported targets
            self.btn_loadTrace.configure(state="disabled")
            self.update()
            print("Loading trace from files...")
            parseTraceFiles(self)
        else:
            print("Loading a trace from target " + target_map.get(self.selectedTarget) + " is not yet supported.")

    def selectTraceSource(self, traceSource: str):
        """
        Callback that ius called if a new target is selected from the option menu.
        """
        for target in target_map:
            if target_map.get(target) is traceSource:
                self.selectedTarget = target

        self.textbox.configure(state=customtkinter.NORMAL)  # Reset the textbox if a different target is selected
        self.textbox.delete(1.0, 'end')
        self.textbox.configure(state=customtkinter.DISABLED)

        self.traceView.setTasks(None)
        self.traceView.draw()

        if self.selectedTarget == PICO2_FREERTOS:
            print("To load the trace buffer, openocd needs to be in the path.")
        else:
            print("Target " + target_map.get(self.selectedTarget) + " is not yet supported!")

    def save_image_function(self):
        """
        Function generates a PDF of the current trace view.
        """
        self.traceView.postscript(file="tmp.ps", colormode="color")                 # Generate the postscript of the trace canvas
        process = subprocess.Popen(["ps2pdf", "-dEPSCrop", "tmp.ps", "trace.pdf"])  # Convert the postscript file to PDF (requires ps2pdf)
        streamdata = process.communicate()[0]                                       # Get the return code of the process
        rc = process.returncode
        if rc == 0:
            print("Saved trace as trace.pdf.")
        else:
            print("Cound not generate PDF file.")
        os.remove("tmp.ps")                                                         # Delete the temporary postscript file

class TextRedirector(object):
    """
    Class to redirect stdout and stderr to a textbox widget.
    """
    def __init__(self, widget, tag="stdout"):
        self.widget = widget
        self.tag = tag

    def write(self, string):
        self.widget.configure(state="normal")
        self.widget.insert("end", string, (self.tag,))  # Add the string to the textbox
        self.widget.configure(state="disabled")
        self.widget.see("end")                          # Scroll the view to the end

    def flush(self):    # Needed to have the interface of a file-like object
        pass    

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