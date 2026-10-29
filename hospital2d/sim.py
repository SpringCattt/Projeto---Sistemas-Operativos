# sim.py - The simulation process. A Hospital holds the plan, the shared structures (world.py) and the actors,
# each running in its own thread, plus one dispatcher thread. The process talks to the others only through queues:
#   arrivals process -> new patients and surges      UI process <- snapshots, -> commands ('quit')
#   log process      <- events and the final summary
import queue
import random
import threading
import time
from collections import Counter, defaultdict, deque

import config
from arrivals import new_patient
from layout import build_layout
from patients import Patient
from staff import Boss, Cleaner, Doctor, Receptionist, Security, TriageNurse, WardNurse
from world import Dispatcher, Line, Task, Theatres, World


class Hospital:
    def __init__(self, seed=None, n_doctors=None, n_surgeries=None, n_patients=None, log_out=None):
        self.lay = build_layout(n_doctors or config.MAX_DOCTORS,
                                config.N_SURGERIES if n_surgeries is None else n_surgeries)
        self.room_kinds = frozenset(r.kind for r in self.lay.rooms)
        self.rng = random.Random(seed)
        self.world = World()
        self.stop = threading.Event()
        self.started = time.perf_counter()
        self.line = Line()
        self.triage_queue = queue.Queue()
        self.dispatcher = Dispatcher(self.clock, self.lay.walk_distance)
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
        staff = [Receptionist(self, seat, front, rng) for seat, front in lay.reception_desks]
        staff += [TriageNurse(self, room, seat, chair, rng) for room, seat, chair in lay.triage_desks]
        doctors = [Doctor(self, room, rng) for room in lay.rooms_of('doctor')]
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
        self.dispatcher.staff = doctors + security + cleaners
        boss = Boss(self, lay.control_seat, rng)
        self.actors = [boss] + staff + doctors + nurses + security + cleaners

        # The hospital doesn't start empty: some patients are already triaged and waiting
        for _ in range(config.N_PATIENTS if n_patients is None else n_patients):
            self.add_waiting(new_patient(rng))

    # ---- running ------------------------------------------------------------------------------------------------
    def clock(self):
        """Simulated seconds since the start."""
        return (time.perf_counter() - self.started) * config.TIME_SCALE

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
        if len(self.patients()) >= config.MAX_PATIENTS:
            self.count('turned_away')
            self.log(f'{info["name"]} is turned away: the hospital is full')
            return None
        p = Patient(self, self.lay.street_edge, self.rng, info)
        with self.actors_lock:
            self.actors.append(p)
        if self.running:
            self._spawn(p.run, (), f'patient-{p.id}')
        return p

    def add_waiting(self, info, room=None):
        """A patient that is already triaged, sitting in a waiting room (the start of the day, and tests)."""
        rooms = self.lay.rooms_of('waiting')
        room = room or rooms[len(self.patients()) % len(rooms)]
        p = Patient(self, self.lay.waiting_seats[room] or self.lay.free_tiles(room), self.rng, info, triaged=True)
        p.waiting_room = room
        self.queue_for_doctor(p)
        with self.actors_lock:
            self.actors.append(p)
        if self.running:
            self._spawn(p.run, (), f'patient-{p.id}')
        return p

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
        with self.stats_lock:
            stats = dict(self.stats)
        with self.events_lock:
            events = list(self.events)
        return {'actors': [a.snapshot() for a in actors], 'messes': messes, 'events': events, 'stats': stats,
                'time': self.clock(), 'surge': self.clock() < self.surge_until, 'sim_hz': self.sim_hz,
                'keep_up': self.keep_up,
                'line': len(self.line), 'triage': self.triage_queue.qsize(),
                'patients': sum(1 for a in actors if a.role == 'patient')}

    def summary(self):
        s = self.stats
        lines = [f'Simulated time: {self.clock():.0f} s',
                 f'Arrived: {s["arrived"]}  turned away: {s["turned_away"]}  surges: {s["surges"]}',
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


def run_simulation(snapshots, commands, arrivals, log_out, seed=None):
    """Process body. snapshots: Queue -> UI. commands: Queue <- UI ('quit'). arrivals: Queue <- arrivals process.
    log_out: Queue -> log process."""
    snapshots.cancel_join_thread()  # on exit, don't wait for the UI to read snapshots nobody needs any more
    h = Hospital(seed, log_out=log_out)
    h.log('The hospital opens')
    h.start()
    last_measure = time.perf_counter()
    try:
        while True:
            try:  # the timeout paces the snapshots
                if commands.get(timeout=1 / config.SNAPSHOT_HZ) == 'quit':
                    break
            except queue.Empty:
                pass
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
            try:  # if the UI is behind, skip this one: it only ever wants the latest
                snapshots.put_nowait(h.snapshot())
            except queue.Full:
                pass
    finally:
        log_out.put(('summary', h.shutdown()))
        log_out.put(None)
