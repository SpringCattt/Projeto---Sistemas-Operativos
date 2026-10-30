# staff.py - The hospital staff.
#   Desk staff serve a queue:   Receptionist (the reception line), TriageNurse (the triage queue).
#   Task staff get work from the Dispatcher (world.py): Doctor (consultations, surgery), Security (fights),
#   Cleaner (messes). WardNurse just walks the medical wing for now.
import queue

import config
from actors import Actor, Interrupted, OffShift, Stopped
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
        self.given, self.checked = 0, 0.0
        self.doing = 'assigning tasks'

    def life(self):
        d = self.h.dispatcher
        while not self.h.stop.is_set():
            with d.cond:
                d.cond.wait(timeout=1 / config.ACTOR_HZ)
                given = d.assign()
            self.given += len(given)
            if self.h.on_call and self.h.clock() - self.checked > 5:  # every 5 s: enough staff in?
                self.checked = self.h.clock()
                self.h.staffing()
            for task, actor in given:
                if task.urgency == 2 and self.h.clock() > self.speech_until:
                    self.talk('boss_red', name=actor.first_name())
            self.ticks += 1
            self.planned += 1 / config.ACTOR_HZ
        raise Stopped

    def card(self):
        d = self.h.dispatcher
        busy = sum(1 for a in d.staff if a.task is not None)
        error = f'{d.error_sum / d.learned:.0f} s' if d.learned else '-'
        return [('Open tasks', len(d.open)), ('Tasks given', self.given), ('Staff busy', f'{busy}/{len(d.staff)}'),
                ('Learned from', f'{d.learned} tasks'), ('Guess off by', error),
                ('Called in', sum(1 for a in d.staff if a.on_call))]

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
        self.seat, self.front = seat, front  # where the patient stands, across the counter

    def life(self):
        while True:  # consumer of the reception line
            if self.quitting:
                raise OffShift
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
        self.room, self.seat, self.chair = room, seat, chair

    def life(self):
        while True:  # consumer of the triage queue
            if self.quitting:
                raise OffShift
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
            completed = False
            try:
                if not task.cancelled:
                    self.handle(task)
                    completed = True
            except Interrupted:
                task.interruptions += 1
                self.h.count('interruptions')
                self.h.log(f'{self.label()} leaves {task.label()} for something more urgent')
                self.h.dispatcher.submit(task)
            finally:
                self.handling, self.doing, self.speed, self.action, self.pushing = None, 'idle', 1.0, None, None
                self.h.dispatcher.finished(self, task, completed)

    def next_task(self):
        while True:
            if self.quitting:
                raise OffShift
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
        if emergency and p.needs_surgery and self.h.theatres.rooms:  # straight to the theatre on a stretcher
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
        if p.needs_surgery and self.h.theatres.rooms:
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


class Cart:
    """The empty stretcher: it takes up a tile like a person does, but has no thread (two nurses move it)."""
    speed, path, anim_t, driver = 0.0, (), 0.0, None

    def __init__(self, tile):
        self.tx, self.ty = tile
        self.px, self.py, self.facing = tile[0] + 0.5, tile[1] + 0.5, 'down'


def neighbours(tile):
    x, y = tile
    return [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]


PATIENT_NEVER = ('bar', 'control', 'storage', 'rest', 'disinfection')  # a stretcher with someone on it neither


class WardNurse(TaskStaff):
    """Walks the medical wing. Two of them bring Red patients from Emergency on a stretcher (world.Transfer):
    nurse, stretcher, nurse in a row of three tiles that always move together, like a train (drive)."""
    role = 'nurse'
    pace = (2, 5, 0.8)

    def handle(self, task):
        t = task.transfer
        self.h.dispatcher.set_preemptable(task, False)  # nobody leaves a stretcher half-way
        self.speed = config.STRETCHER_SPEED
        try:
            if not self.reach_storage(t):
                t.failed = True
                return
            t.check_in(self)
            self.doing = 'waiting for a partner with the stretcher'
            if not self.wait_until(lambda: len(t.ready) >= 2 or t.aborted(), timeout=120):
                t.failed = True
            if t.aborted():
                return
            self.doing = f'stretcher for {t.patient.name}'
            if t.lead is self:
                self.lead(t)
            else:
                self.carried(t)
        finally:
            if t.lead is self:
                t.done.set()  # the one behind is free to go

    def reach_storage(self, t):
        end = self.h.clock() + 90
        spots = set(self.lay.stretcher_spots)
        while (self.tx, self.ty) not in spots:  # a free spot in the open part of storage
            if t.aborted() or self.h.clock() > end:
                return False
            free = [x for x in self.lay.stretcher_spots if x not in self.world.occupied]
            near = self.lay.distances_from((self.tx, self.ty))
            if free:
                self.walk_to(min(free, key=lambda x: near.get(x, 10 ** 6)))
            else:
                self.tick()
        return True

    def carried(self, t):
        """The nurse at the back: stays put while the train forms around it, then the lead moves it."""
        if not self.wait_until(lambda: t.back_spot or t.aborted(), timeout=60) or t.aborted():
            return
        if not self.go(t.back_spot, timeout=40):
            t.failed = True
            return
        t.formed.set()
        while not t.done.is_set():
            self.tick()

    def lead(self, t):
        p, s = t.patient, t.summons
        cart = stretcher = self.form(t)  # (cart: in the train right now; stretcher: the one it took, always)
        back = t.ready[1]
        if cart is None:
            t.failed = True
            self.driver = back.driver = None
            return
        self.pushing, self.doing = cart, f'stretcher for {p.name}'
        train = [self, cart, back]
        for m in train:  # a train from now on: whoever walks gives way to it
            m.driver = (self, train)
        try:
            if not self.wait_until(lambda: t.formed.is_set() or t.failed, timeout=60) or t.failed:
                return
            # The empty stretcher goes next to the patient, who climbs on
            # The empty stretcher goes right next to the bed the patient is lying on, who slides across onto it
            if not self.drive(train, *self.stop_beside(self.bed((p.px, p.py)), train)) or t.aborted():
                return
            t.at_patient.set()
            if not self.wait_until(lambda: t.mounted.is_set() or t.aborted(), timeout=30) or t.aborted():
                return
            self.swap(train, cart, p)
            self.pushing = None
            self.h.log(f'{self.label()} and {back.label()} take {p.label()} on a stretcher')
            target = self.bed(s.lie) if s.lie else [s.tile]  # next to the exam bed / operating table
            delivered = self.drive(train, *self.stop_beside(target, train, with_patient=True, keep_free=s.tile),
                                   with_patient=True)
            if delivered and self.world.occupied.get(s.tile, p) is p:
                self.swap(train, p, cart, to=s.tile, onto=s.lie)  # straight onto the bed / table
            else:  # couldn't get there: it gets off where it is, and the doctor will call it again
                t.failed = True
                s.cancel()
                cart = None
            self.pushing = cart
            t.arrived.set()
            self.tire(config.FATIGUE_PER_TASK['stretcher'])
            if cart is not None:  # the stretcher back to its place at the back of storage
                self.park(train, cart)
        finally:
            t.arrived.set()
            self.pushing = None
            for m in (self, back, p, stretcher):
                m.driver = None
            with self.world.lock:
                cart = stretcher
                if (cart.tx, cart.ty) not in self.lay.stretcher_bay or self.world.occupied.get((cart.tx, cart.ty)) is not cart:
                    # shortcut: one that couldn't be pushed back (or was left with a patient) reappears in storage;
                    # a third nurse fetching it would be the real thing
                    if self.world.occupied.get((cart.tx, cart.ty)) is cart:
                        del self.world.occupied[(cart.tx, cart.ty)]
                    spot = next((m for m in self.lay.stretcher_bay if m not in self.world.occupied), None)
                    if spot:
                        cart.tx, cart.ty, cart.px, cart.py = spot[0], spot[1], spot[0] + 0.5, spot[1] + 0.5
                        cart.facing = 'down'
                        self.world.occupied[spot] = cart
                if t.back_spot and self.world.occupied.get(t.back_spot) is back and \
                        (back.tx, back.ty) != t.back_spot:
                    del self.world.occupied[t.back_spot]

    def form(self, t):
        """Takes a stretcher parked at the back of storage: this nurse on one side of it, the partner on another
        (they both walk there). Returns the stretcher, or None if none is free in time (all out with patients)."""
        back = t.ready[1]
        end = self.h.clock() + 90
        self.doing = 'getting a stretcher'
        while self.h.clock() < end and not t.aborted():
            near, far = self.lay.distances_from((self.tx, self.ty)), self.lay.distances_from((back.tx, back.ty))
            with self.world.lock:
                occ = self.world.occupied
                free = lambda x, who: self.lay.walkable(*x) and occ.get(x) in (None, who)
                options = sorted(((near.get(x, 10 ** 6) + far.get(k, 10 ** 6), c, x, k)
                                 for c in occ.values() if isinstance(c, Cart) and c.driver is None
                                 for x in neighbours((c.tx, c.ty)) if free(x, self)
                                  for k in neighbours((c.tx, c.ty)) if k != x and free(k, back)), key=lambda o: o[0])
                if options:
                    _, cart, x, k = options[0]
                    cart.driver = (self, [self, cart, back])  # claimed: no other pair takes it
                    occ.setdefault(k, back)
                    t.back_spot = k
            if not options:
                self.tick()  # all out: wait for one to come back
                continue
            if self.go(x, timeout=30):
                return cart
            cart.driver = None  # couldn't get to it: this transfer fails (the doctor will call again)
            return None
        return None

    def park(self, train, cart):
        """Back to the back wall of storage: the front goes past a free parking spot so the stretcher (the middle)
        stops on it, where it stays."""
        near = self.lay.distances_from((train[0].tx, train[0].ty))
        for _ in range(len(self.lay.stretcher_bay)):
            with self.world.lock:  # pick a free spot and claim it, in one step
                spots = [m for m in self.lay.stretcher_bay if m not in self.world.bay_claims
                         and self.world.occupied.get(m) in (None, *train)]
                if not spots:
                    return False
                m = min(spots, key=lambda m: near.get(m, 10 ** 6))
                self.world.bay_claims.add(m)
            try:
                sides = [g for g in ((m[0] - 1, m[1]), (m[0] + 1, m[1]))  # along the wall: it pulls it in sideways
                         if self.lay.walkable(*g) and self.world.occupied.get(g) in (None, *train)]
                if sides and self.drive(train, m, sides, timeout=60) and (cart.tx, cart.ty) == m:
                    return True
            finally:
                with self.world.lock:
                    self.world.bay_claims.discard(m)
        return False

    def bed(self, spot):
        """The tiles of the bed / table someone lies on with its middle at `spot`."""
        t = (int(spot[0]), int(spot[1]))
        return next((f.tiles() for f in self.lay.furniture if t in f.tiles()), [t])

    def stop_beside(self, tiles, train, with_patient=False, keep_free=None):
        """Where the front has to go so the middle of the train (the stretcher) ends up right next to `tiles` (a
        bed): through a free tile M beside it, and one more step to G. Returns (M, G); (a tile, None) if there's
        no such room. keep_free: a tile the stretcher mustn't stop on (where the patient will be)."""
        def ok(x):
            if not self.lay.walkable(*x) or x in self.world.messes or self.world.occupied.get(x) not in (None, *train) \
                    or x == keep_free or x in tiles:
                return False
            room = self.lay.room_at[x[1]][x[0]]
            return not with_patient or (x not in self.lay.staff_only and (room is None or room.kind not in PATIENT_NEVER))
        near = self.lay.distances_from((train[0].tx, train[0].ty))
        options = [(near.get(m, 10 ** 6), m, g) for m in {n for t in tiles for n in neighbours(t)} if ok(m)
                   for g in neighbours(m) if ok(g)]
        if options:
            _, m, g = min(options)
            return m, g
        return (keep_free or tiles[0]), None

    def drive(self, train, goal, last=None, with_patient=False, timeout=180, yielding=False):
        """Moves the train (front, middle, back) until its front reaches `goal`, then `last` if given. Each step,
        the front takes the next tile and the other two step into the tiles the one ahead of them just left, all
        at the same time: the three can never come apart. It can also turn round (the back becomes the front).
        In the way: someone standing is asked to step aside; when two trains meet head on (at a door, say) the
        one that ranks lower backs off to let the other through, or they'd wait for each other forever."""
        end = self.h.clock() + timeout
        blocked_for, path = 0.0, None
        while True:
            if path is None:
                path = self.plan(train, goal, last, with_patient, around=blocked_for > 0)
                if path is None:
                    return False
            if not path:
                return True
            if self.h.clock() > end:
                return False
            front = train[0]
            with self.world.lock:
                free = self.world.occupied.setdefault(path[0], front) is front
            if not free:  # someone in the way: a moment, then round them
                blocked_for += self.tick()
                if len(path) == 1 and path[0] != goal and blocked_for > 1:  # the extra step only lines it up: skip it
                    return True
                other = self.world.occupied.get(path[0])
                other_train = getattr(other, 'driver', None)
                if other_train is None and other is not None and blocked_for > 0.3:
                    other.make_way = True
                elif other_train and other_train[0] is not self and not yielding and blocked_for > 1 \
                        and self.rank(train) < other_train[0].rank(other_train[1]):
                    self.back_off(train, path[0], with_patient)
                    blocked_for, path = 0.0, None
                    continue
                if blocked_for > 0.5:
                    path = None
                continue
            blocked_for = 0.0
            self.shift(train, path.pop(0))

    def rank(self, train):
        """When two trains meet, the lower one gives way: the empty one, or else the one led by the newer nurse."""
        return any(m.role == 'patient' for m in train if isinstance(m, Actor)), -self.id

    def back_off(self, train, conflict, with_patient):
        """Gets out of the way: to the closest free spot a few tiles from where the trains met, then a moment."""
        mine = {(m.tx, m.ty) for m in train}
        around = self.lay.distances_from(conflict)
        near = self.lay.distances_from((train[0].tx, train[0].ty))
        with self.world.lock:
            taken = set(self.world.occupied) - mine

        def ok(x):
            room = self.lay.room_at[x[1]][x[0]]
            return x not in taken and (not with_patient or (x not in self.lay.staff_only and
                                                            (room is None or room.kind not in PATIENT_NEVER)))
        spots = [x for x, d in around.items() if 4 <= d <= 10 and ok(x) and x in near]
        if spots:
            self.drive(train, min(spots, key=near.get), with_patient=with_patient, timeout=20, yielding=True)
        self.wait(1.5)

    def plan(self, train, goal, last, with_patient, around):
        avoid = PATIENT_NEVER if with_patient else ()
        blocked = set(self.lay.staff_only) if with_patient else set()
        with self.world.lock:
            blocked |= self.world.messes.keys()
            if around:
                blocked |= {x for x, a in self.world.occupied.items()
                            if a not in train and abs(x[0] - self.tx) + abs(x[1] - self.ty) <= 8}
        options = []
        for order in (train, train[::-1]):  # forwards, or turned round
            start = (order[0].tx, order[0].ty)
            r = [] if start == goal else self.lay.path(start, goal, avoid, blocked | {(m.tx, m.ty) for m in order[1:]})
            if r or start == goal:
                # one more step past the goal: any of `last` except back where it came from (its own stretcher)
                came = r[-2] if len(r) >= 2 else (order[1].tx, order[1].ty)
                extra = next((x for x in (last if isinstance(last, list) else [last] if last else [])
                              if x != came and x not in {(m.tx, m.ty) for m in order}), None)
                options.append((len(r) + (order is not train), r + ([extra] if extra else []), order))
        if not options:
            return self.plan(train, goal, last, with_patient, False) if around else None
        _, path, order = min(options, key=lambda o: o[0])
        train[:] = order
        return path

    def shift(self, train, nxt):
        """One step of the whole train, moving the three together tick by tick."""
        targets = [nxt, (train[0].tx, train[0].ty), (train[1].tx, train[1].ty)]
        starts = [(m.px, m.py) for m in train]
        for m, (x, y) in zip(train, targets):
            m.driver = (self, train)
            dx, dy = x - m.tx, y - m.ty
            if dx or dy:
                m.facing = ('right' if dx > 0 else 'left') if dx else ('down' if dy > 0 else 'up')
            m.path = [(x, y)]
        done = 0.0
        while done < 1:
            dt = self.tick()
            done = min(1.0, done + config.ACTOR_SPEED * self.speed * dt)
            for m, (sx, sy), (x, y) in zip(train, starts, targets):
                m.px, m.py = sx + (x + 0.5 - sx) * done, sy + (y + 0.5 - sy) * done
                m.anim_t += dt * self.speed
        with self.world.lock:
            occ = self.world.occupied
            last = (train[2].tx, train[2].ty)
            if occ.get(last) is train[2]:
                del occ[last]
            for m, x in zip(train, targets):
                occ[x] = m
                m.tx, m.ty = x
        for m in train:
            m.path = []

    def swap(self, train, old, new, to=None, onto=None):
        """`new` takes the place of `old` in the middle of the train (the patient slides from its bed onto the
        stretcher: old is the stretcher), or `old` gets off: its tile becomes `to` and it slides across onto the
        bed / table whose middle is `onto` (or onto `to` itself), while `new` (the stretcher) stays."""
        mid = (old.tx, old.ty)
        with self.world.lock:
            occ = self.world.occupied
            if to is None:  # getting on: from its own tile to the stretcher's
                if occ.get((new.tx, new.ty)) is new:
                    del occ[(new.tx, new.ty)]
                occ[mid] = new
                new.tx, new.ty = mid
                goal = (old.px, old.py)
                new.facing = old.facing
                mover = new
            else:  # getting off: from the stretcher to `to`
                occ[to] = old
                occ[mid] = new
                old.tx, old.ty = to
                new.tx, new.ty, new.px, new.py, new.facing = mid[0], mid[1], old.px, old.py, old.facing
                goal = (onto[0], onto[1] - 0.1) if onto else (to[0] + 0.5, to[1] + 0.5)  # as Actor.lie_on
                mover = old
                old.on_stretcher = False
        train[1] = new
        while (mover.px, mover.py) != goal:  # it climbs on / off
            dx, dy = goal[0] - mover.px, goal[1] - mover.py
            d, stride = (dx * dx + dy * dy) ** 0.5, 2.0 * self.tick()
            mover.px, mover.py = goal if d <= stride else (mover.px + dx / d * stride, mover.py + dy / d * stride)
        if to is None:
            new.on_stretcher = True
