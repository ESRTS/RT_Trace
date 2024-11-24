import customtkinter
import math

class TraceView(customtkinter.CTkCanvas):
    """
    Class to implement a widget that displays the execution trace.
    """

    def __init__(self, master):
        super().__init__(master, scrollregion = (0,0,100,500))
        
        """
        Variables that indicate a value in pixel have the ending '_px'
        Variables that indicate a value in time ticks have the ending '_tks'
        """
        # --> Variables to alter the dimensions of different elements in the trace <--
        self.borderX_px = 30            # Border that is added to the left and right of the plot
        self.borderY_px = 30            # Border that is added on top and bottom
        self.legend_px = 90             # Size that is reserved for the legend (left of the trace)
        self.scaleFactor = 1            # Factor to scale the complete view (not used)
        self.taskTimelineHeight_px = 40 # Describes the height of one task's timeline
        self.taskHeight_px = 25         # Describes the height of one task
        self.releaseArrowWidth_px = 2   # Stroke width of release arrow
        self.releaseArrowLength_px = 10 # Length of the release arrow
        self.releaseArrowD_px = 4       # D parameter of release arrow
        self.releaseArrowH_px = 4       # H parameter of release arrow
        self.maxTicks = 30              # Maximum number of tick marks plotted in view

        # --> Internal variables. No manual configuration needed! <--
        self.sizeX_px = 0               # Width of the canvas
        self.view_tks = 0               # Length of the visible interval in time ticks
        self.tickScale = 1              # Tick scale 0 = us, 1 = ms, 2 = s
        self.leftBound_tks = 0          # Smallest time value of the visible trace
        self.rightBound_tks = 50000     # Largest time value of the visible trace (50 ms)
        self.oldLeftBound_tks = 0       # Last value of leftBound_tks before the view was updated 
        self.oldRightBound_tks = 0      # Last value of rightBound_tks before the view was updated
        self.zoomFactor = 1.2           # Factor used to compute zoom areas
        self.zoomMax = 50               # Maximum zoom level 50us
        self.moveView = False           # Flag to indicate that the view is moved
        self.moveInitialX = 0           # Initial X-position if the view is moved

        self.ctk_textbox_scrollbar = customtkinter.CTkScrollbar(self, command=self.yview)
        self.ctk_textbox_scrollbar.place(relx=1,rely=0,relheight=1,anchor='ne')
        self.configure(yscrollcommand=self.ctk_textbox_scrollbar.set)

        self.tasks = None

        self.canvasItems = []           # Used to store all canvas items that are updated with a new view, so we can delete those easily

        self.draw()

        

    def setTasks(self, tasks):
        """
        Function adds the tasks to the view.
        """
        self.tasks = tasks

        # Find the maximum time to display in ticks
        self.rightBound_tks = 0
        for task in self.tasks:
            if task.jobs[-1].getFinishTime() > self.rightBound_tks:
                self.rightBound_tks = task.jobs[-1].getFinishTime()

        # we add one ms to the right bound to not finish the trace with the last event
        self.rightBound_tks = self.rightBound_tks + 1000 

    def draw(self):
        """
        Function draws the trace view of all tasks. 
        """
        print("Draw...")

        if self.tasks is None:
            return
        #if self.tasks is not None:
        
        # In case the view was updated, make sure to delete all canvas items first
        for item in self.canvasItems:
            self.delete(item)

        self.sizeX_px  = int(self.winfo_width())

        # Compute the length of the visible view in ticks
        self.view_tks = self.rightBound_tks - self.leftBound_tks

        windowHeight = len(self.tasks) * self.taskTimelineHeight_px + self.borderY_px

        startY = self.borderY_px / 2

        # Draw the tick marks for the current view on the canvas
        self.drawTicks()

        for task in self.tasks:
            taskPos = self.taskTimelineHeight_px * (self.tasks.index(task) + 1) - 1 - self.taskHeight_px
            self.paintTask(task, taskPos)

        for task in self.tasks:
            self.canvasItems.append(self.create_line(self.borderX_px + self.legend_px, (self.taskTimelineHeight_px * (self.tasks.index(task) + 1)) - 1, self.sizeX_px - self.borderX_px, (self.taskTimelineHeight_px * (self.tasks.index(task) + 1)) - 1))

            #left vertical boundary
            self.canvasItems.append(self.create_line(self.plotXOffset(), 0, self.plotXOffset(), (self.taskTimelineHeight_px * (self.tasks.index(task) + 1)) - 1))
			
            self.canvasItems.append(self.create_line(self.sizeX_px - self.borderX_px, 0, self.sizeX_px - self.borderX_px, (self.taskTimelineHeight_px * (self.tasks.index(task) + 1)) - 1))

        self.paintLegend()
        
    def paintLegend(self):
        """
        Function draws the task labels on the canvas.
        """

        for task in self.tasks:
            self.canvasItems.append(self.create_text(self.legend_px + (self.borderX_px / 2), (self.taskTimelineHeight_px * (self.tasks.index(task) + 1)) - (self.taskTimelineHeight_px / 2), anchor=customtkinter.E,text=task.name))

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

        windowStart = self.tickToPixel(self.leftBound_tks)
        windowStop = self.tickToPixel(self.rightBound_tks)

        if start_px < windowStart:
            start_px = windowStart
        elif finish_px > windowStop:
            finish_px = windowStop
		
        if finish_px - start_px >= 0:

            # Plot the ready marking
            if task.id > 100:   # the ISR and scheduler events have an ID < 100. For those we don't have jobs so we don't draw the grey background either.
                self.canvasItems.append(self.create_rectangle(start_px, y, start_px + (finish_px - start_px), y + self.taskHeight_px, fill='#DDDDDD'))

            # Plot the execution
            self.drawJobsSection(task, job, y, start_px, finish_px, self.tickToPixel(job.releaseTime), self.tickToPixel(job.getFinishTime()))

        if self.leftBound_tks <= job.releaseTime and self.rightBound_tks >= job.releaseTime:
            #releaseArrowLength_px
            #drawArrowLine(g2d, rel, y, rel, y - RELEASE_ARROW_LENGTH, RELEASE_ARROW_D, RELEASE_AWWOR_h);
            rel_px = self.tickToPixel(job.releaseTime)
            self.canvasItems.append(self.create_line(rel_px, y, rel_px, y - self.releaseArrowLength_px, arrow=customtkinter.LAST, arrowshape=(self.releaseArrowH_px, self.releaseArrowH_px, self.releaseArrowD_px / 2), width=self.releaseArrowWidth_px))

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
                self.canvasItems.append(self.create_rectangle(startInterval_px, y, startInterval_px + execeWidth_px, y + self.taskHeight_px, fill = task.taskColor))
    
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
        if self.leftBound_tks < self.oldLeftBound_tks:  # The new left boundary has a smaller time than the old boundary ("left bound moved to the left")
            stop = False

            while stop is not True:
                if task.leftIndex == 0:
                    stop = True
                else:   
                    job = task.jobs[task.leftIndex - 1] # Get the next smaller job (we can get index -1 since we made sure above that the index is not 0)

                    if job.getFinishTime() <= self.leftBound_tks:   # If this job finishes outside the visible view we stop and do not include it in the view
                        stop = True
                    else:
                        task.leftIndex = task.leftIndex - 1 # Since the job finished within the view it's index is included. 

        elif self.leftBound_tks > self.oldLeftBound_tks:    # The new left boundary is larger than the old boundary ("left boundary was moved to the right")
            stop = False

            while stop is not True:
                if task.leftIndex is maxIndex: # If the last job is already outside the view we can stop
                    stop = True
                else:
                    job = task.jobs[task.leftIndex] # Get the job on the current boundary

                    if job.getFinishTime() > self.leftBound_tks:    # If the job is in the visible view, we can stop.
                        stop = True
                    else:
                        task.leftIndex = task.leftIndex + 1 # Since the job was not in the view, the next job is checked

        # RIGHT BOUND CHECK
        if self.rightBound_tks > self.oldRightBound_tks: # The new right boundary is larger than the old right boundary ("right boundary was moved to the right")
            stop = False

            while stop is not True:
                if task.rightIndex is maxIndex: # If the last job is already in view we can stop
                    stop = True
                else:
                    job = task.jobs[task.rightIndex + 1] # Get the next larger job

                    if job.releaseTime > self.rightBound_tks:   # If the release of this job is outside the view, we found the first job that is not visible anymore
                        stop = True
                    else:
                        task.rightIndex = task.rightIndex + 1   # Since the job was not outside the view we can set the right index accordingly

        elif self.rightBound_tks < self.oldRightBound_tks:  # The new right boundary is smaller than the old boundary ("right boundary was moved to the left")
            stop = False

            while stop is not True:
                if task.rightIndex <= 0:    # If the view is to the left of the first job, stop (i.e. even the first job appears later then the right boundary)
                    stop = True
                else:
                    job = task.jobs[task.rightIndex] # Get the currently oldest visible job 

                    if job.releaseTime < self.rightBound_tks:  # Since the right boundary was moved earlier we can stop as soon as the job with rightIndex is in view again
                        stop = True
                    else:
                        task.rightIndex = task.rightIndex - 1   # Since the job was not in view we check the job before that

    def drawTicks(self):
        """
        Draws the tick lines based on the current view.
        """
        minSizeTick = self.view_tks / self.maxTicks

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

        firstTick = self.leftBound_tks
        firstTick = math.ceil(self.leftBound_tks / (self.tickScale * subdivider))
        tick = firstTick * (self.tickScale * subdivider)

        drawNextTick = True
        while drawNextTick:

            pos = self.tickToPixel(tick)

            # Draw the ticks
            self.canvasItems.append(self.create_line(pos, 0, pos, self.taskTimelineHeight_px * (len(self.tasks)), fill="lightgrey"))
            self.canvasItems.append(self.create_line(pos, self.taskTimelineHeight_px * (len(self.tasks)) - 1, pos, self.taskTimelineHeight_px * (len(self.tasks)) + 5))
            
            # Draw timestring
            self.canvasItems.append(self.create_text(pos, self.taskTimelineHeight_px * (len(self.tasks)) + 12, anchor=customtkinter.N, text=self.getTimeString(tick)))

            tick = tick + (self.tickScale * subdivider) # Increment tick
            if tick >= self.rightBound_tks:
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
        return self.borderX_px + self.legend_px
    
    def tickToPixel(self, tick):
        """
        This function converts a tick time value into it's respective pixel x-coordinate on the canvas.
        """
        plotWidth = self.sizeX_px - self.borderX_px - self.borderX_px - self.legend_px
        plotXOffset = self.borderX_px + self.legend_px

        viewLength = self.rightBound_tks - self.leftBound_tks
        posLength = tick - self.leftBound_tks

        tmp = (plotWidth * posLength) / viewLength

        return round(tmp) + plotXOffset
    
    def pixelToTime(self, pixel):
        """
        This function converts a pixel x-coordinate and converts it into it's respective tick time value.
        """
        viewLength = self.rightBound_tks - self.leftBound_tks
        plotWidth = self.sizeX_px - self.borderX_px - self.borderX_px - self.legend_px

        return (pixel * viewLength) / (plotWidth)
    
    def zoom(self, direction):
        """
        Function is used to handle zoom events. 
        Depending on the zoom direction the new left and right boundaries are calculated. 
        """

        left_tks = self.leftBound_tks
        right_tks = self.rightBound_tks

        if direction > 0: # Zoom in
            left_tks = left_tks * self.zoomFactor
            right_tks = right_tks / self.zoomFactor
        elif direction < 0:  # Zoom out
            left_tks = left_tks / self.zoomFactor
            right_tks = right_tks * self.zoomFactor

        if right_tks - left_tks > self.zoomMax:
            self.leftBound_tks = left_tks
            self.rightBound_tks = right_tks

        self.draw()

    def mouseDragHandler(self, event):
        """
        Function to handle mouse drag events. If the button was pressed before, this means 
        the view is moved. The function then calculates the new view boundaries and repaints the view. 
        """

        if self.moveView is True:
            delta = self.moveInitialX - event.x
            self.moveInitialX = event.x

            div_tks = self.pixelToTime(delta)

            self.leftBound_tks = self.leftBound_tks + div_tks
            self.rightBound_tks = self.rightBound_tks + div_tks

            self.draw()

    def buttonPressed(self, event):
        """
        Function starts the sequence to move the view.
        """
        self.moveView = True
        self.moveInitialX = event.x

    def buttonReleased(self, event):
        """
        Function stops the sequence to move the view.
        """
        self.moveView = False