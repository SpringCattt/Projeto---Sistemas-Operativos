# sim.py - The simulation process. A Hospital holds the plan, the shared structures (world.py) and the actors,
# each running in its own thread, plus one dispatcher thread. The process talks to the others through queues, and to the UI also through shared memory:
#   arrivals process -> new patients and surges      UI process <- positions (shared memory), words, -> commands
#   log process      <- events and the final summary
import multiprocessing as mp
import os
import queue
import random
import struct
import threading
import time
from collections import Counter, defaultdict, deque

import config
from actors import ROW
from arrivals import new_patient
from layout import build_layout
from patients import Patient
from staff import Boss, Cart, Cleaner, Doctor, Receptionist, Security, TriageNurse, WardNurse

SPEEDS = (0.25, 0.5, 1, 2, 4, 8)  # what the admin tablet offers
BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'boss_brain.json')  # written by learn.py
JOBS = {c.__name__: c for c in (Doctor, WardNurse, TriageNurse, Receptionist, Security, Cleaner)}
from world import Dispatcher, Line, Task, Theatres, Transfer, World


class Hospital:
    def __init__(self, seed=None, n_doctors=None, n_surgeries=None, n_patients=None, log_out=None, brain=None,
                 on_call=False):
        """brain: file with what the Boss learned (learn.py), to start from instead of config's weights.
        on_call: the Boss calls in staff on call when work piles up (config.ON_CALL); their doctor's rooms start empty."""
        self.lay = build_layout(n_doctors or config.MAX_DOCTORS,
                                config.N_SURGERIES if n_surgeries is None else n_surgeries)
        self.room_kinds = frozenset(r.kind for r in self.lay.rooms)
        self.rng = random.Random(seed)
        self.world = World()
        self.stop = threading.Event()
        self.rate, self.paused = config.TIME_SCALE, False
        self._clock = (time.perf_counter(), 0.0, self.rate)  # (real time, simulated time, speed) at the last change
        self.line = Line()
        self.triage_queue = queue.Queue()
        self.dispatcher = Dispatcher(self.clock, self.lay.walk_distance)
        if brain:
            self.dispatcher.load(brain)
        self.theatres = Theatres(self.lay.rooms_of('surgery'))
        self.disinfection = {r: threading.Semaphore(max(1, len(spots))) for r, spots in self.lay.sink_spots.items()}
        self.log_out = log_out  # Queue to the log process (or None)
        self.events, self.events_lock = deque(maxlen=10), threading.Lock()
        self.stats, self.stats_lock = Counter(), threading.Lock()
        self.waits = defaultdict(list)  # urgency at triage -> seconds from triage to the doctor
        self.errors = []
        self.surge_until = -1.0
        self.sim_hz, self.keep_up, self._hz_mark = 0.0, 1.0, (time.perf_counter(), 0, 0.0)
        self._retired_ticks, self._retired_planned = 0, 0.0  # counted by actors that already left
        self.actors, self.actors_lock, self.threads = [], threading.Lock(), []
        self.running = False

        lay, rng = self.lay, self.rng
        for spot in lay.stretcher_bay[:config.STRETCHERS]:  # parked before anyone is placed: they take a tile
            self.world.occupied[spot] = Cart(spot)
        staff = [Receptionist(self, seat, front, rng) for seat, front in lay.reception_desks]
        staff += [TriageNurse(self, room, seat, chair, rng) for room, seat, chair in lay.triage_desks]
        rooms = lay.rooms_of('doctor')
        if on_call:  # the farthest rooms wait for the doctors on call
            rooms = rooms[:max(1, len(rooms) - config.ON_CALL.get('Doctor', 0))]
        doctors = [Doctor(self, room, rng) for room in rooms]
        self.on_call, self.last_call, self.quiet_since = on_call, {}, {}
        medical = [t for k in ('corridor', 'surgery', 'disinfection', 'storage')
                   for r in lay.rooms_of(k) for t in lay.free_tiles(r) if t not in lay.seats]
        public = [t for k in ('waiting', 'reception') for r in lay.rooms_of(k) for t in lay.free_tiles(r)
                  if t not in lay.seats and t not in lay.staff_only]
        everywhere = [t for r in lay.rooms if r.kind not in ('street', 'control')
                      for t in lay.free_tiles(r) if t not in lay.seats]
        nurses = [WardNurse(self, medical, rng) for _ in range(config.N_NURSES)]
        security = [Security(self, public, rng) for _ in range(config.N_SECURITY)]
        cleaners = [Cleaner(self, everywhere, rng) for _ in range(config.N_CLEANERS)]
        for group, area in ((nurses, medical), (security, public), (cleaners, everywhere)):
            for a in group:
                a.area = area
        self.dispatcher.staff = doctors + security + cleaners + nurses
        self.areas = {WardNurse: medical, Security: public, Cleaner: everywhere}
        boss = Boss(self, lay.control_seat, rng)
        self.actors = [boss] + staff + doctors + nurses + security + cleaners

        # The hospital doesn't start empty: some patients are already triaged and waiting
        for _ in range(config.N_PATIENTS if n_patients is None else n_patients):
            self.add_waiting(new_patient(rng))

    # ---- running ------------------------------------------------------------------------------------------------
    def clock(self):
        """Simulated seconds since the start. Stops while paused; runs faster or slower with the speed."""
        real, sim, speed = self._clock
        return sim + (time.perf_counter() - real) * speed

    @property
    def speed(self):
        """Simulated seconds per real second (0 while paused)."""
        return self._clock[2]

    def set_speed(self, rate=None, paused=None):
        self.rate = self.rate if rate is None else rate
        self.paused = self.paused if paused is None else paused
        self._clock = (time.perf_counter(), self.clock(), 0.0 if self.paused else self.rate)  # one assignment: atomic

    def start(self):
        self.running = True
        with self.actors_lock:
            for a in self.actors:
                self._spawn(a.run, (), f'{a.role}-{a.id}')

    def _spawn(self, target, args, name):
        t = threading.Thread(target=target, args=args, name=name, daemon=True)
        self.threads.append(t)
        t.start()

    def shutdown(self, timeout=5):
        """Stops every thread and returns the summary."""
        self.stop.set()
        with self.dispatcher.cond:
            self.dispatcher.cond.notify_all()
        end = time.perf_counter() + timeout
        for t in self.threads:
            t.join(max(0.0, end - time.perf_counter()))
        self.running = False
        return self.summary()

    # ---- patients coming and going ------------------------------------------------------------------------------
    def admit(self, info):
        """A patient walks in from the street (sent by the arrivals process)."""
        self.count('arrived')
        if info['surgery'] and self.lay.ambulance_bay:  # by ambulance: in through Emergency's own door, triaged
            self.log(f'{info["name"]} arrives by ambulance')
            return self.add_waiting(info, arrive=self.lay.ambulance_bay)
        p = Patient(self, self.lay.street_edge, self.rng, info)
        with self.actors_lock:
            self.actors.append(p)
        if self.running:
            self._spawn(p.run, (), f'patient-{p.id}')
        return p

    def add_waiting(self, info, room=None, arrive=None):
        """A patient that is already triaged, sitting in a waiting room (the start of the day, and tests), or Red in
        Emergency. arrive: tiles it appears on and walks from (the ambulance bay) instead of already being there."""
        rooms = self.lay.rooms_of('waiting')
        for er, beds in self.lay.emergency_beds.items():  # Red: a free bed in Emergency, if there is one
            if room is None and info['urgency'] == 2 and any(side not in self.world.occupied for side, _ in beds):
                room = er
        room = room or rooms[len(self.patients()) % len(rooms)]
        spots = [side for side, _ in self.lay.emergency_beds.get(room, [])] or self.lay.waiting_seats.get(room)
        p = Patient(self, arrive or spots or self.lay.free_tiles(room), self.rng, info, triaged=True)
        p.waiting_room = room
        self.queue_for_doctor(p)
        with self.actors_lock:
            self.actors.append(p)
        if self.running:
            self._spawn(p.run, (), f'patient-{p.id}')
        return p

    # ---- the admin tablet ---------------------------------------------------------------------------------------
    def command(self, cmd):
        """An order from the admin tablet (UI process)."""
        kind, *args = cmd
        if kind == 'pause':
            self.set_speed(paused=not self.paused)
            self.log('Admin: ' + ('pause' if self.paused else 'resume'))
        elif kind == 'speed':
            i = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - self.rate))
            self.set_speed(rate=SPEEDS[max(0, min(len(SPEEDS) - 1, i + args[0]))])
            self.log(f'Admin: speed x{self.rate:g}')
        elif kind == 'patient':  # urgency, surgery, how many, already triaged
            urgency, surgery, n, triaged = args
            for _ in range(n):
                info = new_patient(self.rng, urgency, surgery)
                self.add_waiting(info) if triaged else self.admit(info)
            self.log(f'Admin: {n} {config.URGENCY_NAMES[urgency]} patient(s)' + (' (surgery)' if surgery else ''))
        elif kind == 'surge':
            self.surge(config.SURGE_SECONDS)
            for _ in range(8):
                self.admit(new_patient(self.rng))
        elif kind in ('fight', 'vomit'):
            waiting = [p for p in self.patients() if p.stage == 'waiting for a doctor' and not p.provoke]
            if waiting:
                self.rng.choice(waiting).provoke = kind
            else:
                self.log(f'Admin: nobody waiting who could {kind}')
        elif kind == 'hire':
            self.hire(JOBS[args[0]])
        elif kind == 'fire':
            self.fire(JOBS[args[0]])

    def working(self, job):
        with self.actors_lock:
            return [a for a in self.actors if type(a) is job and not a.quitting]

    def places(self, job):
        """Desks, rooms or seats a job needs one of each (None: no limit)."""
        lay = self.lay
        return {Doctor: lay.rooms_of('doctor'), Receptionist: lay.reception_desks,
                TriageNurse: lay.triage_desks}.get(job)

    def hire(self, job, on_call=False):
        """A new member of staff walks in from the street to its place (on_call: the Boss called it in)."""
        lay, rng = self.lay, self.rng
        places = self.places(job)
        if places is not None:
            taken = {getattr(a, 'room', None) if job is Doctor else a.seat for a in self.working(job)}
            free = [p for p in places if (p if job is Doctor else p[0] if job is Receptionist else p[1]) not in taken]
            if not free:
                return self.log(f'Admin: no free place for another {job.__name__}')
            a = job(self, free[0], rng) if job is Doctor else job(self, *free[0], rng)
            home = a.seat or (a.tx, a.ty) if job is Doctor else a.seat
        else:
            a = job(self, self.areas[job], rng)
            a.area, home = self.areas[job], (a.tx, a.ty)
        door = [t for r in lay.rooms if r.name == 'Exit' for t in lay.free_tiles(r) if t not in self.world.occupied]
        with self.world.lock:  # it appears at the door, not at its place
            if self.world.occupied.get((a.tx, a.ty)) is a:
                del self.world.occupied[(a.tx, a.ty)]
            a.tx, a.ty = rng.choice(door) if door else lay.street_edge[0]
            a.px, a.py = a.tx + 0.5, a.ty + 0.5
            self.world.occupied.setdefault((a.tx, a.ty), a)
        a.commute = home
        with self.actors_lock:
            self.actors.append(a)
        if job in (Doctor, WardNurse, Security, Cleaner):  # they get their work from the dispatcher
            with self.dispatcher.cond:
                self.dispatcher.staff.append(a)
        if self.running:
            self._spawn(a.run, (), f'{a.role}-{a.id}')
        a.on_call = on_call
        self.log(f'Boss calls in {a.label()} (on call)' if on_call else f'Admin: {a.label()} starts a shift')

    def fire(self, job, on_call=False):
        """Sends one home (an idle one if possible): it finishes what it's doing and walks out."""
        staff = [a for a in self.working(job) if a.on_call or not on_call]  # on_call: only those called in
        if not staff:
            return
        a = min(staff, key=lambda a: (a.task is not None, a.doing != 'idle'))
        a.quitting = True
        with self.dispatcher.cond:
            if a in self.dispatcher.staff:
                self.dispatcher.staff.remove(a)  # no more tasks for it
        self.log(f'Boss sends {a.label()} home: it\'s quiet again' if on_call else f'Admin: {a.label()} goes home')

    def staffing(self):
        """The Boss calls in staff on call when tasks pile up for a role, and sends them home once it has been
        quiet for a while, like a real shift (config.ON_CALL). Called by the Boss's thread."""
        now, d = self.clock(), self.dispatcher
        with d.cond:
            waiting = Counter(t.role for t in d.open)
            free = Counter(a.role for a in d.staff if a.task is None and not a.resting)
        for name, most in config.ON_CALL.items():
            job = JOBS[name]
            called = [a for a in self.working(job) if a.on_call]
            places = self.places(job)
            if waiting[job.role] >= free[job.role] + config.CALL_IN_AT:
                self.quiet_since[name] = None
                room = places is None or len(self.working(job)) < len(places)
                if room and len(called) < most and now - self.last_call.get(name, -10 ** 9) > config.CALL_EVERY_S:
                    self.last_call[name] = now
                    self.hire(job, on_call=True)
            elif waiting[job.role]:
                self.quiet_since[name] = None
            else:
                if self.quiet_since.get(name) is None:
                    self.quiet_since[name] = now
                if called and now - self.quiet_since[name] > config.SEND_HOME_AFTER_S:
                    self.quiet_since[name] = now
                    self.fire(job, on_call=True)

    def staff_counts(self):
        return {name: (len(self.working(job)), None if self.places(job) is None else len(self.places(job)))
                for name, job in JOBS.items()}

    def surge(self, seconds):
        self.surge_until = self.clock() + seconds
        self.count('surges')
        for p in self.rng.sample(self.patients(), min(6, len(self.patients()))):
            p.talk('surge')
            p.say('?', 3)
        self.log(f'SURGE! Lots of patients arriving for the next {seconds:.0f} s')

    def queue_for_doctor(self, p):
        p.task = Task('consult', 'doctor', p.rank, patient=p, created=self.clock())
        self.dispatcher.submit(p.task)

    def fetch_stretcher(self, p, summons):
        """Two nurses (two tasks for the dispatcher) bring `p` to `summons.tile` on a stretcher."""
        summons.transfer = Transfer(p, summons)
        team = [Task('stretcher', 'nurse', 2, patient=p, created=self.clock()) for _ in range(2)]
        for task in team:
            task.transfer, task.team = summons.transfer, team
        self.dispatcher.submit(*team)

    def consult_started(self, p):
        if p.triaged_at is not None:
            with self.stats_lock:
                self.waits[p.base_urgency].append(self.clock() - p.triaged_at)
            p.triaged_at = None  # only the first time counts (not after an interruption)

    def treated(self, p):
        self.count('treated')

    def retire(self, actor):
        """An actor's thread ended: it leaves the map."""
        with self.world.lock:
            for tile in [t for t, a in self.world.occupied.items() if a is actor]:
                del self.world.occupied[tile]
        with self.actors_lock:
            if actor in self.actors and not self.stop.is_set():
                self.actors.remove(actor)
                # keep what it counted, or measure() would see the totals drop when someone leaves
                self._retired_ticks += actor.ticks
                self._retired_planned += actor.planned

    def patients(self):
        with self.actors_lock:
            return [a for a in self.actors if a.role == 'patient' and not a.gone]

    # ---- bookkeeping --------------------------------------------------------------------------------------------
    def count(self, key, n=1):
        with self.stats_lock:
            self.stats[key] += n

    def log(self, text):
        t = self.clock()
        with self.events_lock:
            self.events.append((t, text))
        if self.log_out is not None:
            self.log_out.put(('event', (t, text)))

    def error(self, actor, trace):
        self.errors.append((actor.label(), trace))
        self.log(f'ERROR in {actor.label()}: {trace.strip().splitlines()[-1]}')

    def measure(self):
        """Since the last call: updates per second each actor thread really got (sim_hz), and how well the threads
        keep up with the rate they ask for (keep_up: 1.0 = every tick on time; below 0.8 the hospital is too big)."""
        with self.actors_lock:
            actors = list(self.actors)
            ticks = self._retired_ticks + sum(a.ticks for a in actors)
            planned = self._retired_planned + sum(a.planned for a in actors)
        now = time.perf_counter()
        then, ticks_before, planned_before = self._hz_mark
        if actors and now > then:
            self.sim_hz = max(0.0, (ticks - ticks_before) / (now - then) / len(actors))
            self.keep_up = max(0.0, min(1.0, (planned - planned_before) / (now - then) / len(actors)))
        self._hz_mark = (now, ticks, planned)

    def snapshot(self):
        with self.actors_lock:
            actors = list(self.actors)
        with self.world.lock:
            messes = list(self.world.messes)
            parked = [(c.px, c.py, c.facing) for c in self.world.occupied.values()
                      if isinstance(c, Cart) and c.driver is None]
        with self.stats_lock:
            stats = dict(self.stats)
        with self.events_lock:
            events = list(self.events)
        return {'actors': [a.snapshot() for a in actors], 'messes': messes, 'parked': parked, 'events': events,
                'stats': stats,
                'time': self.clock(), 'surge': self.clock() < self.surge_until, 'sim_hz': self.sim_hz,
                'speed': self.rate, 'paused': self.paused, 'staff': self.staff_counts(),
                'keep_up': self.keep_up,
                'line': len(self.line), 'triage': self.triage_queue.qsize(),
                'patients': sum(1 for a in actors if a.role == 'patient')}

    def summary(self):
        s = self.stats
        lines = [f'Simulated time: {self.clock():.0f} s',
                 f'Arrived: {s["arrived"]}  surges: {s["surges"]}',
                 f'Treated: {s["treated"]}  surgeries: {s["surgeries"]}  gave up: {s["gave_up"]}  '
                 f'thrown out: {s["expelled"]}',
                 f'Complaints: {s["complaints"]}  fights: {s["fights"]}  vomits: {s["vomits"]}  '
                 f'got worse: {s["got_worse"]}',
                 f'Doctors interrupted for an emergency: {s["interruptions"]}']
        for rank, waits in sorted(self.waits.items()):
            lines.append(f'Average wait to see a doctor, {config.URGENCY_NAMES[rank]}: '
                         f'{sum(waits) / len(waits):.0f} s ({len(waits)} patients)')
        lines.append(f'Actor threads keeping up: {100 * self.keep_up:.0f} % ({self.sim_hz:.1f} updates/s each)')
        if self.errors:
            lines.append(f'ERRORS: {len(self.errors)}')
        return '\n'.join(lines)


# Shared memory between the simulation and the UI: how many rows there are, then one ROW per actor
COUNT = struct.Struct('<i')
MAX_ROWS = 5000  # shortcut: actors past this many aren't drawn; make it bigger if a hospital ever gets there


def new_positions():
    """The shared block. mp.Array comes with its own lock: whoever writes or reads takes it first, so the UI never
    sees a half-written block (one actor's new x with its old y)."""
    return mp.Array('B', COUNT.size + MAX_ROWS * ROW.size)


def write_positions(shared, actors):
    """Packed outside the lock; under it, only the copy into shared memory (microseconds)."""
    rows = [a.numbers() for a in actors[:MAX_ROWS]]
    data = COUNT.pack(len(rows)) + b''.join(ROW.pack(*r) for r in rows)
    with shared.get_lock():
        memoryview(shared.get_obj()).cast('B')[:len(data)] = data


def read_positions(shared):
    """{actor id: its ROW}. Copied out under the lock, unpacked after it."""
    with shared.get_lock():
        block = memoryview(shared.get_obj()).cast('B')
        n, = COUNT.unpack_from(block)
        data = block[COUNT.size:COUNT.size + n * ROW.size].tobytes()
    return {r[0]: r for r in ROW.iter_unpack(data)}


def run_simulation(snapshots, commands, arrivals, log_out, seed=None, speed=None, positions=None):
    """Process body. snapshots: Queue -> UI (the words: names, speech, cards, stats). positions: shared memory with
    the UI (where everyone is: write_positions). commands: Queue <- UI ('quit', or an admin tablet order).
    arrivals: Queue <- arrivals process. log_out: Queue -> log process. speed: shared Value with the arrivals
    process (how fast simulated time runs, 0 = paused)."""
    snapshots.cancel_join_thread()  # on exit, don't wait for the UI to read snapshots nobody needs any more
    h = Hospital(seed, log_out=log_out, brain=BRAIN, on_call=True)
    h.log('The hospital opens')
    h.start()
    quit_, poke = threading.Event(), threading.Event()  # poke: an order was carried out, send a snapshot now

    def listen():
        """Its own thread, always blocked on the queue: an order from the admin tablet is carried out the moment it
        arrives, like an interrupt, whatever the rest of the process is doing (it doesn't wait for a snapshot)."""
        while True:
            cmd = commands.get()
            if cmd == 'quit':
                quit_.set()
                return poke.set()
            h.command(cmd)
            if speed is not None:
                speed.value = h.speed
            poke.set()

    threading.Thread(target=listen, name='admin-orders', daemon=True).start()
    last_measure = last_words = time.perf_counter()
    try:
        while True:
            poked = poke.wait(1 / config.SNAPSHOT_HZ)  # paces the updates (or sooner, to show an order straight away)
            poke.clear()
            if quit_.is_set():
                break
            while True:
                try:
                    kind, payload = arrivals.get_nowait()
                except queue.Empty:
                    break
                if kind == 'patient':
                    h.admit(payload)
                else:
                    h.surge(payload)
            if time.perf_counter() - last_measure >= 1:
                h.measure()
                last_measure = time.perf_counter()
            if positions is not None:
                with h.actors_lock:
                    actors = list(h.actors)
                write_positions(positions, actors)
            if poked or time.perf_counter() - last_words >= 1 / config.WORDS_HZ:
                last_words = time.perf_counter()
                try:  # if the UI is behind, skip this one: it only ever wants the latest
                    snapshots.put_nowait(h.snapshot())
                except queue.Full:
                    pass
    finally:
        log_out.put(('summary', h.shutdown()))
        log_out.put(None)
