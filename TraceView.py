import customtkinter

class TraceView(customtkinter.CTkCanvas):
    """
    Class to implement a widget that displays the execution trace.
    """
    def __init__(self, master):
        super().__init__(master, scrollregion = (0,0,100,1000))

        self.create_line(500, 25, 850, 25)
        self.create_line(55, 85, 155, 85, 105, 180, 55, 85)
        self.create_oval(10, 10, 20, 20, fill="red")
        self.create_oval(1400, 1400, 220, 220, fill="blue")
        self.ctk_textbox_scrollbar = customtkinter.CTkScrollbar(self, command=self.yview)
        self.ctk_textbox_scrollbar.place(relx=1,rely=0,relheight=1,anchor='ne')
        self.configure(yscrollcommand=self.ctk_textbox_scrollbar.set)

        self.draw()

    def draw(self):
        print("Draw...")