"""
Event-driven simulation engine: advances time, releases jobs, accounts execution and writes the trace.
Which job runs where is decided by the scheduling policy at every scheduling point.
"""
from .model import Job, TaskSetError


class Simulator:
    def __init__(self, taskset, policy, writer):
        policy.validate(taskset)
        self.taskset, self.policy, self.writer = taskset, policy, writer
        self.now = 0
        self.ready = []                                     # Released, unfinished jobs (running or waiting)
        self.next_release = {task.id: task.phase for task in taskset.tasks}
        self.job_count = {task.id: 0 for task in taskset.tasks}
        self.completed = {task.id: set() for task in taskset.tasks}     # Completed job ids, for precedence
        self.constraints = {task.id: [] for task in taskset.tasks}       # Precedence constraints by successor
        for constraint in taskset.precedence:
            self.constraints[constraint.succ_task].append(constraint)

    @staticmethod
    def execution_time(task):
        """Execution time of a new job. Always the WCET for now; bcet is reserved for variable times."""
        return task.wcet

    def run(self, horizon):
        """
        Simulate until horizon: jobs released before the horizon, completions up to and including it.
        Jobs that are unfinished at the horizon stay open in the trace.
        """
        if horizon <= 0:
            raise ValueError('the simulation horizon must be positive')
        self.writer.header(self.taskset, horizon)
        while True:
            self.release_jobs()
            self.dispatch()
            following = min(self.next_release.values())
            running = [job for job in self.ready if job.core is not None]
            if running:
                following = min(following, self.now + min(job.remaining for job in running))
            self.advance(min(following, horizon))
            self.complete_jobs()
            if self.now >= horizon:
                break
        self.writer.end(horizon)

    def release_jobs(self):
        for task in self.taskset.tasks:
            while self.next_release[task.id] <= self.now:
                release = self.next_release[task.id]
                job = Job(task, self.job_count[task.id], release, release + task.deadline,
                          self.execution_time(task))
                self.job_count[task.id] += 1
                self.next_release[task.id] = release + task.period
                self.ready.append(job)
                self.writer.release(self.now, job)
                self.policy.on_release(job, self.now)

    def eligible(self, job):
        """A job may run once all its predecessor jobs have completed."""
        for constraint in self.constraints[job.task.id]:
            predecessor = constraint.predecessor_of(job.id)
            if predecessor is not None and predecessor not in self.completed[constraint.pred_task]:
                return False
        return True

    def dispatch(self):
        candidates = [job for job in self.ready if job.core is not None or self.eligible(job)]
        assignment = self.policy.select(candidates, self.taskset.cores, self.now)
        self.check(assignment, candidates)
        target = {job: core for core, job in assignment.items()}
        # Stop every job that changes core or loses its core first, then start the new ones.
        for job in self.ready:
            if job.core is not None and target.get(job) != job.core:
                self.writer.preempt(self.now, job, job.core)
                job.core = None
        for core in sorted(assignment):
            job = assignment[core]
            if job.core is None:
                job.core = core
                self.writer.start(self.now, job, core)

    def check(self, assignment, candidates):
        jobs = list(assignment.values())
        if len(set(map(id, jobs))) != len(jobs):
            raise TaskSetError(f"policy '{self.policy.name}' assigned a job to two cores")
        for core, job in assignment.items():
            if not 0 <= core < self.taskset.cores:
                raise TaskSetError(f"policy '{self.policy.name}' used core {core}, which does not exist")
            if job not in candidates:
                raise TaskSetError(f"policy '{self.policy.name}' scheduled {job}, which is not ready")
            if not job.task.allowed(core):
                raise TaskSetError(f"policy '{self.policy.name}' put {job} on core {core}, outside its affinity")

    def advance(self, until):
        for job in self.ready:
            if job.core is not None:
                job.remaining -= until - self.now
        self.now = until

    def complete_jobs(self):
        for job in [job for job in self.ready if job.core is not None and job.remaining == 0]:
            self.writer.complete(self.now, job, job.core)
            job.core = None
            self.ready.remove(job)
            self.completed[job.task.id].add(job.id)
            self.policy.on_complete(job, self.now)
