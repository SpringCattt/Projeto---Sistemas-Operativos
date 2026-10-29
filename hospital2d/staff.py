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


def about(task):
    """Id of the person a task is about (the patient, or whoever started a fight), if any."""
    who = task.patient or task.target
    return who.id if who is not None else None


class Boss(Actor):
    """The dispatcher in person. Sits in the control room for the whole shift; its thread is the one that runs
    the dispatcher (world.Dispatcher.assign): it wakes up whenever a task is submitted or someone finishes one
    (Condition.notify), and at least ACTOR_HZ times a second because waiting tasks get more urgent."""
    role = 'dispatcher'

    def __init__(self, h, seat, rng):
        super().__init__(h, [seat], rng, name='Boss')
        self.given = 0
        self.doing = 'assigning tasks'

    def life(self):
        d = self.h.dispatcher
        while not self.h.stop.is_set():
            with d.cond:
                d.cond.wait(timeout=1 / config.ACTOR_HZ)
                given = d.assign()
            self.given += len(given)
            for task, actor in given:
                if task.urgency == 2 and self.h.clock() > self.speech_until:
                    self.talk('boss_red', name=actor.first_name())
            self.ticks += 1
            self.planned += 1 / config.ACTOR_HZ
        raise Stopped

    def card(self):
        d = self.h.dispatcher
        busy = sum(1 for a in d.staff if a.task is not None)
        return [('Open tasks', len(d.open)), ('Tasks given', self.given), ('Staff busy', f'{busy}/{len(d.staff)}')]

    def snapshot(self):
        """Adds what the dispatcher is doing: each task being done (who, where), the open ones, its last decisions."""
        s = super().snapshot()
        d = self.h.dispatcher
        with d.cond:
            s['boss'] = {  # (who is doing it, who it's about or None, tile, urgency, label)
                'working': [(a.id, about(a.task), a.task.where(), a.task.urgency, a.task.label())
                            for a in d.staff if a.task],
                'open': [(about(t), t.where(), t.urgency, t.label()) for t in d.open],
                'decisions': list(d.decisions),
            }
        return s


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
        seconds = self.rng.uniform(*(config.COMPLAINT_S if complaint else config.REGISTER_S))
        if complaint:
            p.talk('complain')
            self.wait(1.5)
            self.talk('reception_calm')
        else:
            self.talk('reception_hello')
            self.wait(1.5)
            p.talk('patient_reason', reason=p.reason.lower())
        self.action = None if complaint else 'typing'  # the patient's name and id into the computer
        self.work(max(0.5, seconds - 1.5))
        self.action = None
        if complaint:
            p.patience = 60.0
            self.h.log(f'{self.label()} calms {p.label()} down')
            self.h.count('complaints')
            self.tire(config.FATIGUE_PER_TASK['complaint'])
        else:
            self.h.log(f'{self.label()} registers {p.name}: "{p.reason}"')
            self.talk('reception_done')
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
        self.talk('nurse_hello')
        self.wait(1.5)
        p.talk('patient_reason', reason=p.reason.lower())
        self.work(max(0.5, self.rng.uniform(*config.TRIAGE_S) - 1.5))
        p.rank = p.base_urgency  # the urgency comes from what the patient told reception
        emergency = self.lay.rooms_of('emergency')
        p.waiting_room = emergency[0] if p.rank == 2 and emergency else self.room.partner
        self.talk('nurse_result', color=config.URGENCY_NAMES[p.rank])
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
            here, there = self.lay.room_at[self.ty][self.tx], self.lay.room_at[task.where()[1]][task.where()[0]]
            if here is not there:  # off to another room: runs, faster the more urgent
                self.speed = config.RUN_SPEED[task.urgency]
            try:
                if not task.cancelled:
                    self.handle(task)
            except Interrupted:
                task.interruptions += 1
                self.h.count('interruptions')
                self.h.log(f'{self.label()} leaves {task.label()} for something more urgent')
                self.h.dispatcher.submit(task)
            finally:
                self.handling, self.doing, self.speed, self.action, self.pushing = None, 'idle', 1.0, None, None
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
                self.talk('back')
                self.h.log(f'{self.label()} is back at work')
        elif self.fatigue >= config.REST_AT and self.lay.rest_spots:
            free = [t for t in self.lay.rest_spots if t not in self.world.occupied]
            if free:
                self.resting, self.bubble, self.doing = True, 'z', 'resting'
                self.talk('rest')
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
        emergency = p.waiting_room is not None and p.waiting_room.kind == 'emergency'
        bed = self.lay.exam_spots.get(self.room)
        if emergency and p.needs_surgery and self.lay.surgery_spots:  # straight to the theatre on a stretcher
            self.h.consult_started(p)
            self.talk('doctor_surgery')
            return self.operate(task, p)
        self.go(self.seat)
        if emergency and bed:  # the nurses bring it on a stretcher, straight onto the exam bed
            s = Summons(self, bed[0], 'consult', allow={'corridor', 'doctor'}, stage=f'going to {self.room.name}',
                        lie=bed[1], stretcher=True)
        else:
            s = Summons(self, self.chair, 'consult', allow={'corridor', 'doctor'}, stage=f'going to {self.room.name}')
        self.talk('doctor_call', name=p.first_name())
        try:
            if not self.call(p, s, timeout=240 if s.stretcher else 120):
                if not p.gone:
                    self.h.dispatcher.submit(task)  # didn't come in time: try again later
                return
            self.h.log(f'{self.label()} sees {p.label()}')
            self.h.consult_started(p)
            self.talk('doctor_hello')
            seconds = self.rng.uniform(*config.CONSULT_S[p.rank])
            if bed and not s.stretcher:  # a chat at the desk, then onto the exam bed
                self.work(seconds / 2)
                exam = Summons(self, bed[0], 'exam', allow={'doctor'}, stage='on the exam bed', lie=bed[1])
                s.finish('exam')
                s = exam
                if not self.call(p, s, timeout=30):
                    return self.h.dispatcher.submit(task)
                beside = self.next_to(bed[0])
                if beside:
                    self.go(beside, timeout=10)
                seconds /= 2
            self.work(seconds)
        except Interrupted:
            self.talk('doctor_emergency')
            if s.arrived.is_set():
                p.talk('patient_dropped')
                s.finish('back')  # back to the waiting room; the task goes back to the dispatcher
            else:
                s.cancel()
            raise
        self.tire(config.FATIGUE_PER_TASK['consult'][p.rank])
        if self.seat:
            self.go(self.seat, timeout=10)
        if p.needs_surgery and self.lay.surgery_spots:
            self.talk('doctor_surgery')
            p.talk('patient_surgery')
            p.say('?', 4)
            s.finish('surgery')
            self.operate(task, p)
        else:
            p.talk('patient_thanks')
            p.say('<3', 4)
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
            s = Summons(self, patient_spot, 'surgery', allow={'corridor', 'surgery'}, stage=f'going to {room.name}',
                        lie=self.lay.table_spots[room],
                        stretcher=p.waiting_room is not None and p.waiting_room.kind == 'emergency')
            p.summon(s)
            self.extra_avoid = ('corridor',)  # from disinfection straight into the theatre
            self.go(doctor_spot, timeout=120)
            self.extra_avoid = ()
            if self.wait_until(lambda: s.arrived.is_set() or p.gone, timeout=300) and not p.gone:
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
                self.talk('security_calm')
                self.work(self.rng.uniform(*config.CALM_DOWN_S))
                task.result = 'expelled' if self.rng.random() < config.EXPEL_CHANCE else 'calmed'
                if task.result == 'expelled':
                    self.talk('security_out')
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
        self.talk('cleaner')
        self.facing, self.action = 'down', 'mopping'
        self.work(self.rng.uniform(*config.CLEAN_S))
        with self.world.lock:
            self.world.messes.pop(task.tile, None)
        self.h.log(f'{self.label()} cleaned up the mess')
        self.tire(config.FATIGUE_PER_TASK['clean'])


class WardNurse(TaskStaff):
    """Walks the medical wing. Two of them bring Red patients from Emergency on a stretcher (world.Transfer)."""
    role = 'nurse'
    pace = (2, 5, 0.8)

    def handle(self, task):
        t = task.transfer
        self.speed = config.STRETCHER_SPEED
        end = self.h.clock() + 90
        while getattr(self.lay.room_at[self.ty][self.tx], 'kind', None) != 'storage':  # any free spot in storage
            if t.aborted() or self.h.clock() > end:
                return
            free = [x for x in self.lay.stretcher_spots if x not in self.world.occupied]
            near = self.lay.distances_from((self.tx, self.ty))
            if free:
                self.walk_to(min(free, key=lambda x: near.get(x, 10 ** 6)))
            else:
                self.tick()
        t.check_in(self)
        self.doing = 'waiting for a partner with the stretcher'
        if not self.wait_until(lambda: len(t.ready) >= 2 or t.aborted(), timeout=120) or t.aborted():
            return
        p, s = t.patient, t.summons
        self.doing = f'stretcher for {p.name}'
        if t.lead is not self:  # behind: the empty stretcher between them, then the patient on it
            self.follow(lambda: p if t.mounted.is_set() else t.lead, lambda: t.arrived.is_set() or t.aborted(),
                        gap=lambda: 1 if t.mounted.is_set() else 2)
            return
        self.pushing = next(n for n in t.ready if n is not self)
        if not self.go(self.next_to((p.tx, p.ty)) or (p.tx, p.ty), timeout=90):
            return s.cancel()
        self.pushing = None
        t.at_patient.set()
        if not self.wait_until(lambda: t.mounted.is_set() or t.aborted(), timeout=30) or t.aborted():
            return
        p.speed = self.speed
        self.h.log(f'{self.label()} and {t.ready[1].label()} take {p.label()} on a stretcher')
        self.go(self.next_to(s.tile) or s.tile, timeout=120)
        t.arrived.set()
        aside = self.next_to((self.tx, self.ty))  # out of the way, so the patient can get onto the bed
        if aside:
            self.walk_to(aside)
        self.tire(config.FATIGUE_PER_TASK['stretcher'])
