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
        self.windowStart = 0
        self.windowStop = 0
        self.oldWindowStart = 0
        self.oldWindowStop = 0

        self.ctk_textbox_scrollbar = customtkinter.CTkScrollbar(self, command=self.yview)
        self.ctk_textbox_scrollbar.place(relx=1,rely=0,relheight=1,anchor='ne')
        self.configure(yscrollcommand=self.ctk_textbox_scrollbar.set)

        self.tasks = None

        self.draw()
        self.sizeX  = int(self.winfo_width())

    def setTasks(self, tasks):
        """
        Function adds the tasks to the view.
        """
        self.tasks = tasks

    def draw(self):
        """
        Function draws the trace view of all tasks. 
        """
        print("Draw...")

        if self.tasks is None:
            return
        #if self.tasks is not None:
        
        self.sizeX  = int(self.winfo_width())

        # Find the maximum time to display
        for task in self.tasks:
            if task.jobs[-1].getFinishTime() > self.view:
                self.view = task.jobs[-1].getFinishTime()

        windowHeight = len(self.tasks) * self.taskTimelineHight + self.borderY

        startY = self.borderY / 2

        self.drawTicks()

        self.oldWindowStart = self.windowStart
        self.oldWindowStop = self.windowStop
        self.windowStart = self.tickToPixel(self.leftBound)
        self.windowStop = self.tickToPixel(self.rightBound)

        for task in self.tasks:
            taskPos = self.taskTimelineHight * (self.tasks.index(task) + 1) - 1 - self.taskHeight
            self.paintTask(task, taskPos)

        for task in self.tasks:
            self.create_line(self.borderX + self.legend, (self.taskTimelineHight * (self.tasks.index(task) + 1)) - 1, self.sizeX - self.borderX, (self.taskTimelineHight * (self.tasks.index(task) + 1)) - 1)

            #left vertical boundary
            self.create_line(self.plotXOffset(), 0, self.plotXOffset(), (self.taskTimelineHight * (self.tasks.index(task) + 1)) - 1)
			
            self.create_line(self.sizeX - self.borderX, 0, self.sizeX - self.borderX, (self.taskTimelineHight * (self.tasks.index(task) + 1)) - 1)

        self.paintLegend()
        
    def paintLegend(self):
        """
        Function draws the task labels on the canvas.
        """

        for task in self.tasks:
            self.create_text(self.legend + (self.borderX / 2), (self.taskTimelineHight * (self.tasks.index(task) + 1)) - (self.taskTimelineHight / 2), anchor=customtkinter.E,text=task.name)

    def paintTask(self, task, y):
        """
        Function prints all jobs of the task that are in view, i.e. in the visible timewindow.
        """
        self.updateVisibleJobs(task)    # update the left and right index of the task that are in view right now

        for index in range(task.leftIndex, task.rightIndex + 1):
            job = task.jobs[index]
            self.paintJob(task, job, y)

    def paintJob(self, task, job, y):
        """
        Function prints the job to the canvas. 
        """
        start_px = self.tickToPixel(job.getStartTime())
        finish_px = self.tickToPixel(job.getFinishTime())

        if start_px < self.windowStart:
            start_px = self.windowStart
        elif finish_px > self.windowStop:
            finish_px = self.windowStop
		
        if finish_px - start_px >= 0:

            # Plot the ready marking
            if task.id > 100:   # the ISR and scheduler events have an ID < 100. For those we don't have jobs so we don't draw the grey background either.
                self.create_rectangle(start_px, y, start_px + (finish_px - start_px), y + self.taskHeight, fill='#DDDDDD')

            # Plot the execution
            self.drawJobsSection(task, job, y, start_px, finish_px, self.tickToPixel(job.releaseTime), self.tickToPixel(job.getFinishTime()))

    def drawJobsSection(self, task, job, y, start_px, stop_px, sectionStart_px, sectionStop_px):
        """
        Function draws all execution intervals of the job that are in the visible part of the plot.
        """
        
        if sectionStart_px > start_px:
            start_px = sectionStart_px
        if sectionStop_px > stop_px:
            stop_px = sectionStop_px

        for interval in job.execIntervals:
            startInterval_px = self.tickToPixel(interval.start)
            stopInterval_px = self.tickToPixel(interval.stop)

            if startInterval_px < sectionStart_px:
                startInterval_px = sectionStart_px
            if stopInterval_px > sectionStop_px:
                stopInterval_px = sectionStop_px

            if stopInterval_px - startInterval_px >= 0:
                execeWidth_px = stopInterval_px - startInterval_px
                if execeWidth_px < 1:
                    execeWidth_px = 1  # Minimum with of an execution segment on the trace is 1px
                
                # Draw the execution on the trace
                self.create_rectangle(startInterval_px, y, startInterval_px + execeWidth_px, y + self.taskHeight, fill = task.taskColor)
    
    def updateVisibleJobs(self, task):
        """
        Each task has a variable to indicate the minimum and maximum index of jobs that are in the current view. 
        This is used to more efficiently update the view if there are a large number of task jobs. 
        This function is called once the visible view changes and updates the minimum and maximum visible job index of each task.
        """
        maxIndex = len(task.jobs) - 1

        # Here we prepare the index accoring to the currently visible window. 
        # This allows us to have a more efficient draw update since we don't need to look at all jobs of the task.

        # LEFT BOUND CHECK
        if self.windowStart < self.oldWindowStart:
            stop = False

            while stop is not True:
                if task.leftIndex == 0:
                    stop = True
                else:   
                    job = task.jobs[task.leftIndex - 1] # Get the next smaller job

                    if job.getFinishTime() <= self.windowStart:
                        stop = True
                    else:
                        task.leftIndex = task.leftIndex - 1

        elif self.windowStart > self.oldWindowStart:
            stop = False

            while stop is not True:
                if task.leftIndex is maxIndex:
                    stop = True
                else:
                    job = task.jobs[task.leftIndex] # Get the next smaller job

                    if job.getFinishTime() > self.windowStart:
                        stop = True
                    else:
                        task.leftIndex = task.leftIndex + 1

        # RIGHT BOUND CHECK
        if self.windowStop > self.oldWindowStop:
            stop = False

            while stop is not True:
                if task.rightIndex is maxIndex:
                    stop = True
                else:
                    job = task.jobs[task.rightIndex + 1] # Get the next larger job

                    if job.releaseTime <= self.windowStop:
                        stop = True
                    else:
                        task.rightIndex = task.rightIndex + 1

        elif self.windowStop < self.oldWindowStop:
            stop = False

            while stop is not True:
                if task.rightIndex <= 0:
                    stop = True
                else:
                    job = task.jobs[task.rightIndex] # Get the next larger job

                    if job.releaseTime <= self.windowStop:
                        stop = True
                    else:
                        task.rightIndex = task.rightIndex - 1

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
            self.create_line(pos, 0, pos, self.taskTimelineHight * (len(self.tasks)), fill="lightgrey")
            self.create_line(pos, self.taskTimelineHight * (len(self.tasks)) - 1, pos, self.taskTimelineHight * (len(self.tasks)) + 5)
            
            # Draw timestring
            self.create_text(pos, self.taskTimelineHight * (len(self.tasks)) + 12, anchor=customtkinter.N, text=self.getTimeString(tick))

            tick = tick + (self.tickScale * subdivider) # Increment tick
            if tick >= self.rightBound:
                drawNextTick = False

    def getTimeString(self, tick):
        """
        Depending on the zoom-level the visible ticks can have different units.
        This function returns the correct time string for the x-axis tick-labels based on
        the current tickScale factor.
        """
        tmp = tick / self.tickScale

        if self.tickScale == 1:
            return str(int(tmp)) + " us"
        elif self.tickScale == 1000:
            return str(int(tmp)) + " ms"
        elif self.tickScale == 1000000:
            return str(int(tmp)) + " s"
        return None
    
    def plotXOffset(self):
        """
        Returns the start pixel of the main plot area.
        """
        return self.borderX + self.legend 
    
    def tickToPixel(self, tick):
        """
        This function converts a tick time value into it's respective pixel x-coordinate on the canvas.
        """
        plotWidth = self.sizeX - self.borderX - self.borderX - self.legend
        plotXOffset = self.borderX + self.legend

        viewLength = self.rightBound - self.leftBound
        posLength = tick - self.leftBound

        tmp = (plotWidth * posLength) / viewLength

        return round(tmp) + plotXOffset
    
    def pixelToTime(self, pixel):
        """
        This function converts a pixel x-coordinate and converts it into it's respective tick time value.
        """
        viewLength = self.rightBound - self.leftBound
        plotWidth = self.sizeX - self.borderX - self.borderX - self.legend

        return (pixel * viewLength) / (plotWidth)