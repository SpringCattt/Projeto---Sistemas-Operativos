# staff.py - The hospital staff.
#   Desk staff serve a queue:   Receptionist (the reception line), TriageNurse (the triage queue).
#   Task staff get work from the Dispatcher (world.py): Doctor (consultations, surgery), Security (fights),
#   Cleaner (messes). WardNurse just walks the medical wing for now.
import queue

import config
from actors import Actor, Interrupted, Stopped
from world import Summons


class Staff(Actor):
    """Everyone on the payroll: fatigue goes up with work and down with rest; tired people work slower."""

    def __init__(self, h, candidates, rng):
        super().__init__(h, candidates, rng)
        self.inbox = queue.Queue()  # tasks from the dispatcher
        self.handling = None  # the task being done right now

    def upkeep(self, dt):
        if self.handling is None:
            rate = config.RECOVER_RESTING_PER_S if self.resting else config.RECOVER_PER_S
            self.fatigue = max(0.0, self.fatigue - rate * dt)
        elif self.handling.preemptable and not self.inbox.empty():
            raise Interrupted  # the dispatcher only gives a busy actor a task when it's clearly more urgent

    def tire(self, amount):
        self.fatigue = min(100.0, self.fatigue + amount)

    def work(self, seconds):
        self.wait(seconds * (1 + self.fatigue / 200))

    def card(self):
        return [('Fatigue', f'{self.fatigue:.0f}/100'), ('Resting', 'yes' if self.resting else 'no')]

    def call(self, patient, summons, timeout=90):
        """Calls a patient and waits until it arrives. False (call cancelled) if it doesn't come or leaves."""
        patient.summon(summons)
        ok = self.wait_until(lambda: summons.arrived.is_set() or summons.cancelled or patient.gone, timeout)
        if not ok or summons.cancelled or patient.gone:
            summons.cancel()
            return False
        return True


class Boss(Actor):
    """The dispatcher in person. Sits in the control room for the whole shift; its thread is the one that runs
    the dispatcher (world.Dispatcher.assign): it wakes up whenever a task is submitted or someone finishes one
    (Condition.notify), and at least ACTOR_HZ times a second because waiting tasks get more urgent."""
    role = 'dispatcher'

    def __init__(self, h, seat, rng):
        super().__init__(h, [seat], rng)
        self.given = 0
        self.doing = 'assigning tasks'

    def life(self):
        d = self.h.dispatcher
        while not self.h.stop.is_set():
            with d.cond:
                d.cond.wait(timeout=1 / config.ACTOR_HZ)
                self.given += d.assign()
            self.ticks += 1
            self.planned += 1 / config.ACTOR_HZ
        raise Stopped

    def card(self):
        d = self.h.dispatcher
        busy = sum(1 for a in d.staff if a.task is not None)
        return [('Open tasks', len(d.open)), ('Tasks given', self.given), ('Staff busy', f'{busy}/{len(d.staff)}')]


class Receptionist(Staff):
    role = 'receptionist'

    def __init__(self, h, seat, front, rng):
        super().__init__(h, [seat], rng)
        self.front = front  # where the patient stands, across the counter

    def life(self):
        while True:  # consumer of the reception line
            patient = self.h.line.take(timeout=1 / config.ACTOR_HZ)
            if patient is None:
                self.tick()
            else:
                self.serve(patient)

    def serve(self, p):
        complaint = p.complaining
        s = Summons(self, self.front, 'complaint' if complaint else 'register', stage='at the counter')
        if not self.call(p, s):
            return
        self.doing = f'{"listening to" if complaint else "registering"} {p.name}'
        self.work(self.rng.uniform(*(config.COMPLAINT_S if complaint else config.REGISTER_S)))
        if complaint:
            p.patience = 60.0
            self.h.log(f'{self.label()} calms {p.label()} down')
            self.h.count('complaints')
            self.tire(config.FATIGUE_PER_TASK['complaint'])
        else:
            self.h.log(f'{self.label()} registers {p.name}: "{p.reason}"')
            self.h.triage_queue.put(p)  # producer of the triage queue
            self.tire(config.FATIGUE_PER_TASK['register'])
        s.finish('ok')
        self.doing = 'idle'


class TriageNurse(Staff):
    role = 'nurse'

    def __init__(self, h, room, seat, chair, rng):
        super().__init__(h, [seat], rng)
        self.room, self.chair = room, chair

    def life(self):
        while True:  # consumer of the triage queue
            try:
                patient = self.h.triage_queue.get(timeout=1 / config.ACTOR_HZ)
            except queue.Empty:
                self.tick()
                continue
            if not patient.gone:
                self.triage(patient)

    def triage(self, p):
        s = Summons(self, self.chair, 'triage', allow={'triage'}, stage=f'going to {self.room.name}')
        if not self.call(p, s):
            if not p.gone:
                self.h.triage_queue.put(p)  # it'll be called again
            return
        self.doing = f'triaging {p.name}'
        self.work(self.rng.uniform(*config.TRIAGE_S))
        p.rank = p.base_urgency  # the urgency comes from what the patient told reception
        p.waiting_room = self.room.partner
        self.h.log(f'{self.label()} triages {p.label()}: {p.reason}')
        self.h.queue_for_doctor(p)
        self.tire(config.FATIGUE_PER_TASK['triage'])
        s.finish('ok')
        self.doing = 'idle'


class TaskStaff(Staff):
    """Idles (wandering, or resting when tired) until the dispatcher hands it a task."""

    def life(self):
        while True:
            task = self.next_task()
            self.resting, self.bubble = False, None
            self.handling, self.doing = task, task.label()
            try:
                if not task.cancelled:
                    self.handle(task)
            except Interrupted:
                task.interruptions += 1
                self.h.count('interruptions')
                self.h.log(f'{self.label()} leaves {task.label()} for something more urgent')
                self.h.dispatcher.submit(task)
            finally:
                self.handling, self.doing = None, 'idle'
                self.h.dispatcher.finished(self, task)

    def next_task(self):
        while True:
            try:
                return self.inbox.get_nowait()
            except queue.Empty:
                self.idle(self.tick())

    def idle(self, dt):
        if self.path:
            self.step(dt)
        elif self.resting:
            if self.fatigue <= config.RESTED_AT:
                self.resting, self.bubble, self.doing = False, None, 'idle'
                self.h.log(f'{self.label()} is back at work')
        elif self.fatigue >= config.REST_AT and self.lay.rest_spots:
            free = [t for t in self.lay.rest_spots if t not in self.world.occupied]
            if free:
                self.resting, self.bubble, self.doing = True, 'z', 'resting'
                self.h.log(f'{self.label()} is exhausted and goes to rest')
                self.set_path(self.route(self.rng.choice(free)))
        else:
            self.wander(dt)

    def handle(self, task):
        raise NotImplementedError

    def next_to(self, tile):
        """A free walkable tile next to `tile`, the closest to this actor (or None)."""
        x, y = tile
        options = [t for t in ((x, y + 1), (x - 1, y), (x + 1, y), (x, y - 1))
                   if self.lay.walkable(*t) and self.world.occupied.get(t, self) is self]
        return min(options, key=lambda t: abs(t[0] - self.tx) + abs(t[1] - self.ty), default=None)


class Doctor(TaskStaff):
    role = 'doctor'
    pace = (3, 8, 0.4)

    def __init__(self, h, room, rng):
        self.seat, self.chair = h.lay.doctor_desks.get(room, (None, None))
        super().__init__(h, [self.seat] if self.seat else h.lay.free_tiles(room), rng)
        self.room = room
        self.area = [t for t in h.lay.free_tiles(room) if t not in h.lay.seats] + ([self.seat] if self.seat else [])
        self.extra_avoid = ()

    def avoid(self):
        return self.extra_avoid

    def handle(self, task):
        if task.kind == 'consult':
            self.consult(task)

    def consult(self, task):
        p = task.patient
        if p.gone or self.chair is None:
            return
        self.go(self.seat)
        s = Summons(self, self.chair, 'consult', allow={'corridor', 'doctor'}, stage=f'going to {self.room.name}')
        try:
            if not self.call(p, s, timeout=120):
                if not p.gone:
                    self.h.dispatcher.submit(task)  # didn't come in time: try again later
                return
            self.h.log(f'{self.label()} sees {p.label()}')
            self.h.consult_started(p)
            self.work(self.rng.uniform(*config.CONSULT_S[p.rank]))
        except Interrupted:
            if s.arrived.is_set():
                s.finish('back')  # back to the waiting room; the task goes back to the dispatcher
            else:
                s.cancel()
            raise
        self.tire(config.FATIGUE_PER_TASK['consult'][p.rank])
        if p.needs_surgery and self.lay.surgery_spots:
            s.finish('surgery')
            self.operate(task, p)
        else:
            s.finish('done')
            self.h.treated(p)

    def operate(self, task, p):
        """Surgery can't be interrupted. Take a free operating room (semaphore), wash in its disinfection room
        (semaphore: as many at once as sinks) and only then go in, straight from disinfection."""
        self.h.dispatcher.set_preemptable(task, False)
        self.doing = f'surgery: {p.name}'
        room = None
        while room is None:
            room = self.h.theatres.try_take(timeout=1 / config.ACTOR_HZ)
            if room is None:
                self.tick()
        try:
            self.h.log(f'{self.label()} takes {p.label()} to {room.name}')
            self.disinfect(room.partner)
            self.doing = f'surgery: {p.name}'
            patient_spot, doctor_spot = self.lay.surgery_spots[room]
            s = Summons(self, patient_spot, 'surgery', allow={'corridor', 'surgery'}, stage=f'going to {room.name}')
            p.summon(s)
            self.extra_avoid = ('corridor',)  # from disinfection straight into the theatre
            self.go(doctor_spot, timeout=120)
            self.extra_avoid = ()
            if self.wait_until(lambda: s.arrived.is_set() or p.gone, timeout=180) and not p.gone:
                self.work(self.rng.uniform(*config.SURGERY_S))
                self.tire(config.FATIGUE_PER_TASK['surgery'])
                self.h.count('surgeries')
                self.h.log(f'{self.label()} finished the surgery of {p.label()}')
                self.h.treated(p)
            s.finish('done')
        finally:
            self.extra_avoid = ()
            self.h.theatres.give_back(room)

    def disinfect(self, room):
        sem = self.h.disinfection[room]
        while not sem.acquire(timeout=1 / config.ACTOR_HZ):
            self.tick()
        try:
            self.extra_avoid = ('surgery',)  # in through the disinfection door, never through the theatre
            free = [t for t in self.lay.sink_spots[room] if t not in self.world.occupied] or self.lay.sink_spots[room]
            self.go(self.rng.choice(free))
            self.doing = f'disinfecting in {room.name}'
            self.wait(self.rng.uniform(*config.DISINFECT_S))
        finally:
            sem.release()


class Security(TaskStaff):
    role = 'security'
    pace = (2, 6, 0.8)

    def handle(self, task):  # a fight
        target = task.target
        end = self.h.clock() + 60
        while not (target.gone or task.done.is_set() or task.cancelled) and self.h.clock() < end:
            if abs(self.tx - target.tx) + abs(self.ty - target.ty) <= 1:
                self.h.log(f'{self.label()} steps in')
                self.work(self.rng.uniform(*config.CALM_DOWN_S))
                task.result = 'expelled' if self.rng.random() < config.EXPEL_CHANCE else 'calmed'
                task.done.set()
                self.tire(config.FATIGUE_PER_TASK['fight'])
                return
            spot = self.next_to((target.tx, target.ty))
            if spot is None or not self.walk_to(spot):
                self.tick()


class Cleaner(TaskStaff):
    role = 'cleaner'
    pace = (2, 5, 0.9)

    def handle(self, task):  # a mess on the floor
        if not self.go(task.tile, timeout=60):
            spot = self.next_to(task.tile)
            if spot is None or not self.go(spot, timeout=30):
                self.h.dispatcher.submit(task)  # couldn't get there: someone else (or later)
                return
        self.work(self.rng.uniform(*config.CLEAN_S))
        with self.world.lock:
            self.world.messes.pop(task.tile, None)
        self.h.log(f'{self.label()} cleaned up the mess')
        self.tire(config.FATIGUE_PER_TASK['clean'])


class WardNurse(TaskStaff):
    role = 'nurse'
    pace = (2, 5, 0.8)
