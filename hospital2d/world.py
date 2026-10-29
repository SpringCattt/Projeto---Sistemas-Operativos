# world.py - Everything the actor threads share inside the simulation process, and how they synchronise.
#
#   World.lock        mutex      who stands on each tile, and the messes on the floor
#   Line              monitor    the reception line (FIFO with places you can see), Condition wait/notify
#   triage queue      queue      registered patients waiting for a triage nurse (producer-consumer)
#   Dispatcher        monitor    open tasks and who is doing what; hands each task to the best actor
#   Summons           events     rendezvous between a member of staff and a patient (arrived / done)
#   Transfer          events     two nurses meet at storage and bring a stretcher (at_patient / mounted / arrived)
#   Theatres          semaphore  how many operating rooms are free (+ a lock for which ones)
#   disinfection      semaphore  one per disinfection room: as many doctors at once as it has sinks
#
# Deadlock rule: a thread never holds two of these locks at the same time.
import itertools
import threading
from collections import deque

import config


class World:
    """Tiles and floor. Every read-modify-write of `occupied` or `messes` happens holding `lock`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.occupied = {}  # tile -> actor standing on it (or stepping into it): nobody walks through anybody
        self.messes = {}  # tile -> sim time it appeared (vomit): people walk around it until it's cleaned


class Line:
    """The reception line. Patients see their place and walk to it; receptionists take whoever is first."""

    def __init__(self):
        self.cond = threading.Condition()
        self.people = []

    def join(self, patient):
        with self.cond:
            self.people.append(patient)
            self.cond.notify()

    def leave(self, patient):
        with self.cond:
            if patient in self.people:
                self.people.remove(patient)

    def place(self, patient):
        with self.cond:
            return self.people.index(patient) if patient in self.people else None

    def take(self, timeout):
        """First in line, or None if nobody comes within `timeout` seconds."""
        with self.cond:
            if not self.cond.wait_for(lambda: self.people, timeout):
                return None
            return self.people.pop(0)

    def __len__(self):
        with self.cond:
            return len(self.people)


class Summons:
    """A member of staff calls a patient to a tile. The patient sets `arrived` when it gets there; the staff
    member sets `done` (with a `result`) when finished. Either side may `cancel` it."""

    def __init__(self, by, tile, kind, allow=(), stage='', lie=None, stretcher=False):
        self.by, self.tile, self.kind = by, tile, kind
        self.lie = lie  # where to lie down once there (a bed, in tiles), or None
        self.stretcher = stretcher  # brought there on a stretcher (Red patients in Emergency): see Transfer
        self.transfer = None
        self.allow = frozenset(allow)  # extra room kinds the patient may walk through to get there
        self.stage = stage
        self.arrived, self.done = threading.Event(), threading.Event()
        self.cancelled = False
        self.result = None

    def cancel(self):
        self.cancelled = True

    def finish(self, result):
        self.result = result
        self.done.set()


class Transfer:
    """Two nurses take a stretcher from storage to a patient and push it to `summons.tile`: nurse, stretcher,
    nurse on three tiles in a row that always move together. The first nurse to reach storage leads and moves the
    three of them (staff.WardNurse.drive); the other one takes its place behind the stretcher and is carried along."""

    def __init__(self, patient, summons):
        self.patient, self.summons = patient, summons
        self.cond = threading.Condition()
        self.ready, self.lead = [], None  # nurses waiting at storage
        self.back_spot = None  # where the second nurse stands, behind the stretcher
        self.formed, self.at_patient, self.mounted, self.arrived, self.done = (threading.Event() for _ in range(5))
        self.failed = False

    def check_in(self, nurse):
        with self.cond:
            self.ready.append(nurse)
            if self.lead is None:
                self.lead = nurse
            self.cond.notify_all()

    def aborted(self):
        return self.failed or self.summons.cancelled or self.summons.done.is_set() or self.patient.gone


class Task:
    """Something a role has to deal with: a consultation (doctor), a fight (security), a mess (cleaner)."""
    _ids = itertools.count(1)

    def __init__(self, kind, role, urgency, tile=None, patient=None, created=0.0):
        self.id = next(Task._ids)
        self.kind, self.role, self._urgency, self.tile, self.patient = kind, role, urgency, tile, patient
        self.created = created
        self.preemptable = True  # a surgery, once started, is not
        self.cancelled = False
        self.done = threading.Event()
        self.interruptions = 0
        self.target, self.result = None, None  # fights: who started it, and how security ended it

    @property
    def urgency(self):  # a patient's condition can get worse while they wait
        return self.patient.rank if self.patient is not None else self._urgency

    def where(self):
        return (self.patient.tx, self.patient.ty) if self.patient is not None else self.tile

    def label(self):
        who = f' {self.patient.name}' if self.patient is not None else ''
        return f'{self.kind}{who} ({config.URGENCY_NAMES[self.urgency]})'


class Dispatcher:
    """The 'AI' that decides who does what. For every open task, most urgent first, it scores each actor of
    the right role and gives the task to the lowest score:

        score = walking distance to the task * W_DISTANCE + fatigue * W_FATIGUE
                (+ W_BUSY if busy, + W_RESTING if resting)

    An actor busy with something only gets interrupted when the new task is at least PREEMPT_GAP urgency
    levels above it (so they don't keep jumping between similar tasks), and never during a surgery.
    Holding `cond` protects `open` and every actor's `task`. The loop that calls assign() is the thread of the
    dispatcher in person, sitting in the control room (staff.Boss)."""

    def __init__(self, clock, distance=None):
        self.clock = clock
        # tiles to walk between two tiles (the plan's real distance, around walls); straight-line if not given
        self.distance = distance or (lambda a, b: abs(a[0] - b[0]) + abs(a[1] - b[1]))
        self.cond = threading.Condition()
        self.open = []
        self.staff = []  # actors that take tasks from here (doctors, security, cleaners)
        self.decisions = deque(maxlen=12)  # (time, text): what it decided and why, shown on the Boss's card

    def submit(self, task):
        with self.cond:
            if not task.cancelled:
                self.open.append(task)
                self.cond.notify()

    def cancel(self, task):
        with self.cond:
            task.cancelled = True
            if task in self.open:
                self.open.remove(task)

    def finished(self, actor, task):
        """The actor is done with `task` (or dropped it): it may get another one."""
        with self.cond:
            if actor.task is task:
                actor.task = None
            self.cond.notify()

    def set_preemptable(self, task, value):
        with self.cond:
            task.preemptable = value

    def assign(self):
        """Hands out what it can. Call it holding self.cond. Returns [(task, actor)] it gave."""
        given = []
        for task in sorted(self.open, key=lambda t: (-t.urgency, t.created)):
            scored = sorted((s, a.id, a) for a in self.staff if (s := self.score(a, task)) is not None)
            if not scored:
                continue
            best, _, actor = scored[0]
            why = f'{task.label()} -> {actor.name}: {best:.0f}'
            if len(scored) > 1:
                why += f' (next {scored[1][2].name}: {scored[1][0]:.0f})'
            if actor.task is not None:
                why += f', interrupting {actor.task.label()}'
            self.decisions.append((self.clock(), why))
            self.open.remove(task)
            actor.task = task  # if it was busy, it notices the inbox, drops its task and that one is re-submitted
            actor.inbox.put(task)
            given.append((task, actor))
        return given

    def score(self, actor, task):
        if actor.role != task.role or not actor.inbox.empty():
            return None
        busy = actor.task
        if busy is not None and (not busy.preemptable or task.urgency - busy.urgency < config.PREEMPT_GAP):
            return None
        distance = self.distance(task.where(), (actor.tx, actor.ty))
        score = distance * config.W_DISTANCE + actor.fatigue * config.W_FATIGUE
        if busy is not None:
            score += config.W_BUSY
        if actor.resting:
            score += config.W_RESTING
        return score


class Theatres:
    """Operating rooms: a counting semaphore says how many are free, a lock guards which ones."""

    def __init__(self, rooms):
        self.free = list(rooms)
        self.sem = threading.Semaphore(len(rooms))
        self.lock = threading.Lock()

    def try_take(self, timeout):
        if not self.sem.acquire(timeout=timeout):
            return None
        with self.lock:
            return self.free.pop()

    def give_back(self, room):
        with self.lock:
            self.free.append(room)
        self.sem.release()
