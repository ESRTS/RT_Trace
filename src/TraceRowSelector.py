"""Scrollable visibility controls for the rows of a loaded trace."""
import sys
import tkinter as tk
import customtkinter


class TraceRowSelector(customtkinter.CTkScrollableFrame):
    def __init__(self, master, command, **kwargs):
        super().__init__(master, label_text="Show in trace", **kwargs)
        self.command = command
        self.taskVariables = []
        self.grid_columnconfigure(0, weight=1)
        # CTk 5.2 handles MouseWheel; Tk 8 on X11 sends Button-4/5 instead.
        if sys.platform.startswith('linux'):
            self.bind_all('<Button-4>', self.scrollRows, add='+')
            self.bind_all('<Button-5>', self.scrollRows, add='+')
        self.setTasks(None)

    def setTasks(self, tasks):
        for child in self.winfo_children():
            child.destroy()
        self.taskVariables.clear()
        self._parent_canvas.yview_moveto(0)
        if not tasks:
            customtkinter.CTkLabel(self, text="Load a trace to select rows.",
                                  wraplength=150).grid(row=0, column=0, padx=5, pady=5)
            return
        for row, task in enumerate(tasks):
            variable = tk.BooleanVar(master=self, value=True)
            checkbox = customtkinter.CTkCheckBox(
                self, text=task.name, variable=variable,
                command=self.selectionChanged, checkbox_width=18, checkbox_height=18,
                width=140)
            checkbox.grid(row=row, column=0, sticky="w", padx=5, pady=5)
            self.taskVariables.append((task, variable))

    def selectionChanged(self):
        self.command([task for task, variable in self.taskVariables if variable.get()])

    def scrollRows(self, event):
        if self.check_if_master_is_canvas(event.widget):
            self._parent_canvas.yview_scroll(-1 if event.num == 4 else 1, 'units')
