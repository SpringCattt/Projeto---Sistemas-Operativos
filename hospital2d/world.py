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
import json
import os
import threading
from collections import deque

import config


class World:
    """Tiles and floor. Every read-modify-write of `occupied` or `messes` happens holding `lock`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.occupied = {}  # tile -> actor standing on it (or stepping into it): nobody walks through anybody
        self.messes = {}  # tile -> sim time it appeared (vomit): people walk around it until it's cleaned
        self.bay_claims = set()  # stretcher parking spots a train is on its way to (so two don't pick the same)


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
        self.ready_at = 0.0  # called on the loudspeaker: it only goes from then (Hospital.announce)

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
        self.guess = None  # (actor, features, seconds it should take, when) while someone is on it
        self.team = None  # tasks that are handed out together or not at all (the two nurses of a stretcher)

    @property
    def urgency(self):  # a patient's condition can get worse while they wait
        return self.patient.rank if self.patient is not None else self._urgency

    def where(self):
        return (self.patient.tx, self.patient.ty) if self.patient is not None else self.tile

    def label(self):
        who = f' {self.patient.name}' if self.patient is not None else ''
        return f'{self.kind}{who} ({config.URGENCY_NAMES[self.urgency]})'


FEATURES = ('base', 'distance', 'fatigue', 'busy', 'resting')
SCALES = (1, 20, 100, 1, 1)  # typical size of each feature: so all of them learn at a similar pace
LEARN_RATE = 0.05


class Dispatcher:
    """The 'AI' that decides who does what. For every open task, most urgent first, it guesses how long each
    actor of the right role would take to get it done, and gives it to the quickest:

        seconds = base + distance to the task * w_distance + fatigue * w_fatigue
                  (+ w_busy if busy, + w_resting if resting)        (one set of weights per kind of task)

    It learns from its mistakes: when the actor finishes, it compares the real time with its guess and moves each
    weight a little in the direction that would have made the guess better (least mean squares, normalised).
    It starts from config's W_* and what it learns can be saved and loaded (learn.py).

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
        self.brain = {}  # task kind -> weights (FEATURES)
        self.policy = start_policy()  # who goes first (priority), and when to call in / send home staff
        self.learned, self.error_sum = 0, 0.0  # tasks it learned from, and how wrong it was in total (seconds)

    def weights(self, kind):
        return self.brain.setdefault(kind, [0.0, config.W_DISTANCE, config.W_FATIGUE, config.W_BUSY, config.W_RESTING])

    def load(self, path):
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                saved = json.load(f)
            self.brain, self.learned, self.error_sum = saved['brain'], saved['learned'], saved['error_sum']
            if len(saved.get('policy', [])) == len(self.policy):  # (an older brain had other things in it: not used)
                self.policy = saved['policy']

    def save(self, path):
        with self.cond:
            data = {'brain': self.brain, 'learned': self.learned, 'error_sum': self.error_sum, 'policy': self.policy}
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
        os.replace(path + '.tmp', path)  # never a half-written file, even if it's stopped right then

    def submit(self, *tasks):
        with self.cond:  # all at once: the dispatcher never sees half of a team
            self.open += [t for t in tasks if not t.cancelled]
            self.cond.notify()

    def cancel(self, task):
        with self.cond:
            task.cancelled = True
            if task in self.open:
                self.open.remove(task)

    def finished(self, actor, task, completed=False):
        """The actor is done with `task` (or dropped it): it may get another one. If it got it done, the dispatcher
        learns from how long that really took compared with its guess."""
        with self.cond:
            if actor.task is task:
                actor.task = None
            if completed and task.guess and task.guess[0] is actor:
                _, x, guess, since = task.guess
                error = (self.clock() - since) - guess
                w = self.weights(task.kind)
                z = [v / s for v, s in zip(x, SCALES)]
                step = LEARN_RATE * error / (1 + sum(v * v for v in z))
                for i in range(len(w)):
                    w[i] += step * z[i] / SCALES[i]
                    if i:  # being far, tired, busy or resting never makes anyone quicker
                        w[i] = max(0.0, w[i])
                self.learned += 1
                self.error_sum += abs(error)
            if task.guess and task.guess[0] is actor:  # (if it was handed to someone else meanwhile, that one's)
                task.guess = None
            self.cond.notify()

    def set_preemptable(self, task, value):
        with self.cond:
            task.preemptable = value

    def assign(self):
        """Hands out what it can. Call it holding self.cond. Returns [(task, actor)] it gave."""
        given = []
        for first in sorted(self.open, key=lambda t: (-t.urgency, -self.priority(t), t.created)):
            team = first.team or [first]
            if first not in self.open or any(t not in self.open for t in team):
                continue
            # A team gets all its people at once or nobody: handing out half of it would leave someone holding a
            # nurse and waiting for a partner who's busy in another half-handed team (hold and wait: deadlock)
            picks, taken = [], set()
            for task in team:
                scored = sorted((s, a.id, a) for a in self.staff if a.id not in taken
                                and (s := self.score(a, task)) is not None)
                if not scored:
                    break
                picks.append((task, scored))
                taken.add(scored[0][1])
            if len(picks) < len(team):
                continue
            for task, scored in picks:
                given.append(self.give(task, scored))
        return given

    def give(self, task, scored):
        """Hands `task` to the best of `scored` [(score, id, actor)]. Call it holding self.cond."""
        best, _, actor = scored[0]
        why = f'{task.label()} -> {actor.name}: {best:.0f}'
        if len(scored) > 1:
            why += f' (next {scored[1][2].name}: {scored[1][0]:.0f})'
        if actor.task is not None:
            why += f', interrupting {actor.task.label()}'
        self.decisions.append((self.clock(), why))
        self.open.remove(task)
        task.guess = (actor, self.features(actor, task), best, self.clock())
        actor.task = task  # if it was busy, it notices the inbox, drops its task and that one is re-submitted
        actor.inbox.put(task)
        return task, actor

    def priority(self, task):
        """Who goes first within the same urgency (the most urgent always go first, like real triage): how long it
        has been waiting and, for a patient, how close it is to running out of patience. learn.py tunes the
        weights (config.PRIORITY_START)."""
        w_waited, w_impatience = self.policy[:2]
        impatience = 1 - task.patient.patience / 100 if task.patient is not None else 0.0
        return w_waited * (self.clock() - task.created) / 60 + w_impatience * impatience

    def score(self, actor, task):
        if actor.role != task.role or not actor.inbox.empty():
            return None
        busy = actor.task
        if busy is not None and (not busy.preemptable or task.urgency - busy.urgency < config.PREEMPT_GAP):
            return None
        return sum(w * v for w, v in zip(self.weights(task.kind), self.features(actor, task)))

    def features(self, actor, task):
        return (1.0, self.distance(task.where(), (actor.tx, actor.ty)), actor.fatigue,
                float(actor.task is not None), float(actor.resting))


def start_policy():
    """The Boss's policy before it learns anything: [w_waited, w_impatience] (who goes first within a colour),
    then staff on call: when to call them in (CALL_IN_AT tasks more than free staff), when to send them home (after
    SEND_HOME_AFTER_S with nothing waiting), how many it may call (MAX_ON_CALL of ON_CALL) and from what hour it
    hurries to be done by midnight (RUSH_FROM_HOUR)."""
    return [*config.PRIORITY_START, config.CALL_IN_AT, config.SEND_HOME_AFTER_S, config.MAX_ON_CALL,
            config.RUSH_FROM_HOUR]


class Elevator:
    """An elevator, shared by the stretcher trains (a stretcher fills it: one train at a time). A monitor: a train
    calls it to its floor once it's there, by the door, and waits its turn: first come (there) first served; the car, with its own thread
    (Hospital.run_lift), goes to whoever's turn it is, waits with the doors open until the train is in, takes it to
    its floor and waits again until it's out. The train holds it from its turn until it's out, so two trains never
    meet at its door (one waiting to get in where the other has to get out)."""

    def __init__(self, stops):
        self.stops = stops  # {floor: (the column in front of it, the middle of its box)} (Layout.lifts)
        self.cond = threading.Condition()
        self.floor = min(stops)
        self.queue = []  # [(train lead, its floor)], in the order they called
        self.rider, self.to = None, None  # whose turn it is (getting in, inside or getting out), and to where
        self.open = True  # doors open: stopped at self.floor

    def call(self, rider, floor):
        with self.cond:
            if all(r is not rider for r, _ in self.queue):
                self.queue.append((rider, floor))
                self.cond.notify_all()

    def take(self, rider):
        """True once it's the rider's turn and the car is at its floor with the doors open: the rider's until it
        leaves."""
        with self.cond:
            if self.rider is None and self.queue and self.queue[0][0] is rider and self.open                     and self.floor == self.queue[0][1]:
                self.queue.pop(0)
                self.rider = rider
                return True
            return False

    def send(self, floor):
        with self.cond:
            self.to = floor
            self.cond.notify_all()

    def there(self, floor):
        with self.cond:
            return self.open and self.floor == floor

    def leave(self, rider):
        """Out (or giving up its place in the queue)."""
        with self.cond:
            self.queue = [(r, f) for r, f in self.queue if r is not rider]
            if self.rider is rider:
                self.rider = self.to = None
            self.cond.notify_all()

    def target(self):
        """(cond held) Where the car should be now, or None: stay."""
        if self.rider is not None:
            return self.to
        return self.queue[0][1] if self.queue else None


class Theatres:
    """Operating rooms: a counting semaphore says how many are free, a lock guards which ones."""

    def __init__(self, rooms):
        self.rooms, self.free = list(rooms), list(rooms)
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
