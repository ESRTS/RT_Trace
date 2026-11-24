"""
Scrollable visibility and order controls for the rows of a loaded trace.

A sort menu orders all rows (TraceRowOrder); the arrows beside each row move it by one place. The trace view, and
with it the PDF export, shows the checked rows in this order. Hidden rows keep their place. Rows a parser marks with
hiddenByDefault (the threads Nsight Systems adds to a Jetson trace) start unchecked.
"""
import sys
import tkinter as tk
import customtkinter

import TraceRowOrder

CUSTOM_ORDER = 'Custom order'       # Shown in the sort menu after a row was moved by hand


class _Row:
    """The widgets of one trace row in the selector."""

    def __init__(self, selector, task):
        self.task = task
        self.variable = tk.BooleanVar(master=selector, value=not getattr(task, 'hiddenByDefault', False))
        arrow = dict(width=20, height=20, corner_radius=4, border_spacing=0)
        self.up = customtkinter.CTkButton(selector, text="▲", command=lambda: selector.move(self, -1), **arrow)
        self.down = customtkinter.CTkButton(selector, text="▼", command=lambda: selector.move(self, 1), **arrow)
        self.checkbox = customtkinter.CTkCheckBox(
            selector, text=task.name, variable=self.variable,
            command=selector.selectionChanged, checkbox_width=18, checkbox_height=18, width=100)


class TraceRowSelector(customtkinter.CTkScrollableFrame):
    def __init__(self, master, command, **kwargs):
        super().__init__(master, label_text="Show in trace", **kwargs)
        self.command = command
        self.recorded = []          # Rows in the order of the parser
        self.rows = []              # _Row in display order
        self.orderMenu = None
        self.grid_columnconfigure(2, weight=1)
        # CTk 5.2 handles MouseWheel; Tk 8 on X11 sends Button-4/5 instead.
        if sys.platform.startswith('linux'):
            self.bind_all('<Button-4>', self.scrollRows, add='+')
            self.bind_all('<Button-5>', self.scrollRows, add='+')
        self.setTasks(None)

    def setTasks(self, tasks):
        """New rows (a trace was loaded): in the recorded order, checked unless the parser hides them by default."""
        for child in self.winfo_children():
            child.destroy()
        self.rows = []
        self.recorded = list(tasks or [])
        self.orderMenu = None
        self._parent_canvas.yview_moveto(0)
        if not tasks:
            customtkinter.CTkLabel(self, text="Load a trace to select rows.",
                                   wraplength=150).grid(row=0, column=0, columnspan=3, padx=5, pady=5)
            return
        orders = TraceRowOrder.available_orders(self.recorded)
        self.orderMenu = customtkinter.CTkOptionMenu(self, values=orders, command=self.sortRows,
                                                     width=140, height=24, dynamic_resizing=False)
        self.orderMenu.set(orders[0])
        self.orderMenu.grid(row=0, column=0, columnspan=3, sticky="ew", padx=5, pady=(5, 2))
        self.rows = [_Row(self, task) for task in self.recorded]
        self.placeRows()

    def placeRows(self):
        """Grids the rows in display order; the first row cannot move up, the last one not down."""
        for index, row in enumerate(self.rows, start=1):
            row.up.grid(row=index, column=0, padx=(5, 1), pady=3)
            row.down.grid(row=index, column=1, padx=(1, 4), pady=3)
            row.checkbox.grid(row=index, column=2, sticky="w", pady=3)
            row.up.configure(state="disabled" if index == 1 else "normal")
            row.down.configure(state="disabled" if index == len(self.rows) else "normal")

    def order(self):
        """All rows (hidden ones too) in display order."""
        return [row.task for row in self.rows]

    def sortRows(self, label):
        """Callback of the sort menu: orders all rows, keeping which ones are checked."""
        rows = {id(row.task): row for row in self.rows}
        self.rows = [rows[id(task)] for task in TraceRowOrder.ORDERS[label](self.recorded)]
        self.placeRows()
        self.selectionChanged()

    def move(self, row, step):
        """Moves a row one place up (-1) or down (+1)."""
        index = self.rows.index(row)
        other = index + step
        if not 0 <= other < len(self.rows):
            return
        self.rows[index], self.rows[other] = self.rows[other], self.rows[index]
        self.placeRows()
        self.orderMenu.set(CUSTOM_ORDER)    # Selecting a menu entry again applies that order
        self.selectionChanged()

    def visibleTasks(self):
        """The checked rows in display order."""
        return [row.task for row in self.rows if row.variable.get()]

    def selectionChanged(self):
        self.command(self.visibleTasks())

    def scrollRows(self, event):
        if self.check_if_master_is_canvas(event.widget):
            self._parent_canvas.yview_scroll(-1 if event.num == 4 else 1, 'units')
