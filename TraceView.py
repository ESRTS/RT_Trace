import customtkinter
import math

class TraceView(customtkinter.CTkCanvas):
    """
    Class to implement a widget that displays the execution trace.
    """

    def __init__(self, master):
        super().__init__(master, scrollregion = (0,0,100,500))
        
        self.borderX = 30            # Border that is added to the left and right of the plot
        self.borderY = 30            # Border that is added on top and bottom
        self.legend = 90             # Size that is reserved for the legend (left of the trace)
        self.scaleFactor = 1         # Factor to scale the complete view (not used)
        self.taskTimelineHight = 40  # Describes the height of one task's timeline
        self.taskHeight = 25         # Describes the height of one task
        self.releaseArrowWidth = 2   # Stroke width of release arrow
        self.releaseArrowLength = 10 # Length of the release arrow
        self.releaseArrowD = 4       # D parameter of release arrow
        self.releaseArrowH = 4       # H parameter of release arrow
        self.sizeX = 0               # Width of the canvas
        self.maxTicks = 30           # Maximum number of ticks plotted in view
        self.view = 0                # Length of the visible interval
        self.tickScale = 1           # Tick scale 0 = us, 1 = ms, 2 = s
        self.leftBound = 0           # Smallest time value of the visible trace
        self.rightBound = 50000      # Largest time value of the visible trace (50 ms)

        self.ctk_textbox_scrollbar = customtkinter.CTkScrollbar(self, command=self.yview)
        self.ctk_textbox_scrollbar.place(relx=1,rely=0,relheight=1,anchor='ne')
        self.configure(yscrollcommand=self.ctk_textbox_scrollbar.set)

        self.tasks = None

        self.draw()
        self.sizeX  = int(self.winfo_width())

    def setTasks(self, tasks):
        self.tasks = tasks

    def draw(self):
        print("Draw...")

        if self.tasks is None:
            return
        #if self.tasks is not None:
        
        self.sizeX  = int(self.winfo_width())

        # Find the maximum time to display
        for task in self.tasks:
            if task.jobs[-1].getFinishTime() > self.view:
                self.view = task.jobs[-1].getFinishTime()

        #    self.create_line(500, 25, 850, 25)
        #    self.create_line(55, 85, 155, 85, 105, 180, 55, 85)
        #    self.create_oval(10, 10, 20, 20, fill="red")
        #    self.create_oval(1400, 1400, 220, 220, fill="blue")

        windowHeight = len(self.tasks) * self.taskTimelineHight + self.borderY

        startY = self.borderY / 2

        self.drawTicks()

        for task in self.tasks:
            self.create_line(self.borderX + self.legend, (self.taskTimelineHight * self.tasks.index(task)) - 1, self.sizeX - self.borderX, (self.taskTimelineHight * self.tasks.index(task)) - 1)

            #left vertical boundary
            self.create_line(self.plotXOffset(), 0, self.plotXOffset(), (self.taskTimelineHight * self.tasks.index(task)) - 1)
			#g2d.drawLine(plotXOffset(), 0, plotXOffset(), MIN_TIMELINE_HEIGHT * visibleTasks.size() - 1);
			
            self.create_line(self.sizeX - self.borderX, 0, self.sizeX - self.borderX, (self.taskTimelineHight * self.tasks.index(task)) - 1)
            #g2d.drawLine(WINDOW_X - borderX, 0, WINDOW_X - borderX, MIN_TIMELINE_HEIGHT * visibleTasks.size() - 1);

        

    def drawTicks(self):
        """
        Draws the tick lines based on the current view.
        """
        minSizeTick = self.view / self.maxTicks

        b = minSizeTick / 1
        subdivisor = 1

        if b < 1:
            self.tickScale = 1
            subdivider = 1
        elif b < 10:
            self.tickScale = 1
            subdivider = 10
        elif b < 100:
            self.tickScale = 1
            subdivider = 100
        elif b < 1000:
            self.tickScale = 1000
            subdivider = 1
        elif b < 10000:
            self.tickScale = 1000
            subdivider = 10
        elif b < 100000:
            self.tickScale = 1000
            subdivider = 100
        elif b < 1000000:
            self.tickScale = 1000000
            subdivider = 1
        elif b < 10000000:
            self.tickScale = 1000000
            subdivider = 10
        elif b < 100000000:
            self.tickScale = 1000000
            subdivider = 100

        firstTick = self.leftBound
        firstTick = math.ceil(self.leftBound / (self.tickScale * subdivider))
        tick = firstTick * (self.tickScale * subdivider)

        drawNextTick = True
        while drawNextTick:

            pos = self.tickToPixel(tick)

            # Draw the ticks
            self.create_line(pos, 0, pos, self.taskTimelineHight * (len(self.tasks) - 1), fill="lightgrey")
            self.create_line(pos, self.taskTimelineHight * (len(self.tasks) - 1) - 1, pos, self.taskTimelineHight * (len(self.tasks) - 1) + 5)
            
            # Draw timestring
            self.create_text(pos, self.taskTimelineHight * (len(self.tasks) - 1) + 12, anchor=customtkinter.N, text="test")

            tick = tick + (self.tickScale * subdivider) # Increment tick
            if tick >= self.rightBound:
                drawNextTick = False

    def plotXOffset(self):
        return self.borderX + self.legend 
    
    def tickToPixel(self, tick):

        plotWidth = self.sizeX - self.borderX - self.borderX - self.legend
        plotXOffset = self.borderX + self.legend

        viewLength = self.rightBound - self.leftBound
        posLength = tick - self.leftBound

        tmp = (plotWidth * posLength) / viewLength

        return round(tmp) + plotXOffset
    
    def pixelToTime(self, pixel):

        viewLength = self.rightBound - self.leftBound
        plotWidth = self.sizeX - self.borderX - self.borderX - self.legend

        return (pixel * viewLength) / (plotWidth)