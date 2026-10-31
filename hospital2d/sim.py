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
from layout import load_layout
from patients import Patient
from staff import Ambulance, Bartender, Boss, Cart, Cleaner, Doctor, Receptionist, Security, TriageNurse, WardNurse
from vehicles import Car

SPEEDS = (0.25, 0.5, 1, 2, 4, 8)  # what the admin tablet offers
BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'boss_brain.json')  # written by learn.py
JOBS = {c.__name__: c for c in (Doctor, WardNurse, TriageNurse, Receptionist, Security, Cleaner)}
from world import Dispatcher, Elevator, Line, Task, Theatres, Transfer, World


class Hospital:
    def __init__(self, seed=None, n_doctors=None, n_surgeries=None, n_patients=None, log_out=None, brain=None,
                 on_call=False):
        """brain: file with what the Boss learned (learn.py), to start from instead of config's weights.
        on_call: the Boss calls in staff on call when work piles up (config.ON_CALL); their doctor's rooms start empty.
        n_doctors, n_surgeries: use only the first n doctor's rooms / operating rooms of the map (small tests)."""
        self.lay = load_layout()
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
        self.theatres = Theatres(self.lay.rooms_of('surgery')[:n_surgeries])
        self.lifts = [Elevator(stops) for stops in self.lay.lifts]
        self.ambulance_bay = threading.Semaphore(1)  # one ambulance there at a time
        self.bays_lock = threading.Lock()  # the parking lot's free bays, by kind: one car in each at a time
        self.free_bays = {kind: list(bays) for kind, bays in self.lay.bays.items()}
        self.bed_claims = set()  # Emergency beds the paramedics are taking someone to (world.lock)
        self.disinfection = {r: threading.Semaphore(max(1, len(spots))) for r, spots in self.lay.sink_spots.items()}
        self.log_out = log_out  # Queue to the log process (or None)
        self.events, self.events_lock = deque(maxlen=10), threading.Lock()
        self.calls = deque(maxlen=20)  # doctors' calls: (time, id, name, first name, room, waiting room); events_lock
        self.stats, self.stats_lock = Counter(), threading.Lock()
        self.waits = defaultdict(list)  # urgency at triage -> seconds from triage to the doctor
        self.errors = []
        self.surge_until = -1.0
        # The day: its number, when it started, doors closed (after midnight), its report once over (paused)
        self.day, self.day_start, self.closed, self.day_over = 0, 0.0, False, None
        self.day_stats = Counter()  # self.stats when the day started (today = stats - day_stats)
        self.credit = Counter()  # today: staff label -> what it brought in (shares the bonus out)
        self.sim_hz, self.keep_up, self._hz_mark = 0.0, 1.0, (time.perf_counter(), 0, 0.0)
        self._retired_ticks, self._retired_planned = 0, 0.0  # counted by actors that already left
        self.actors, self.actors_lock, self.threads = [], threading.Lock(), []
        self.running = False

        lay, rng = self.lay, self.rng
        for spot in lay.stretcher_bay[:config.STRETCHERS]:  # parked before anyone is placed: they take a tile
            self.world.occupied[spot] = Cart(spot)
        for spot in lay.wheelchair_spots:
            self.world.occupied[spot] = Cart(spot, 'wheelchair')
        staff = [Receptionist(self, seat, front, rng) for seat, front in lay.reception_desks]
        staff += [TriageNurse(self, room, seat, chair, rng) for room, seat, chair in lay.triage_desks]
        spread = lay.bar_spots[::max(1, len(lay.bar_spots) // config.BARTENDERS)][:config.BARTENDERS]
        staff += [Bartender(self, spot, front, rng) for spot, front in spread]
        rooms = lay.rooms_of('doctor')[:n_doctors]
        if on_call:  # the farthest rooms wait for the doctors on call
            rooms = rooms[:max(1, len(rooms) - config.ON_CALL.get('Doctor', 0))]
        doctors = [Doctor(self, room, rng) for room in rooms]
        self.on_call, self.last_call, self.quiet_since = on_call, {}, {}
        # Where idle staff wander: never on the stairs, nor beside the parked stretchers (the lane to take them in
        # and out: someone standing in a corner there gets shut in when a stretcher is parked next to it)
        idle_ok = lambda t: t not in lay.seats and t not in lay.links and t not in lay.stretcher_lane
        medical = [t for k in ('corridor', 'surgery', 'disinfection', 'storage')
                   for r in lay.rooms_of(k) for t in lay.free_tiles(r) if idle_ok(t)]
        public = [t for k in ('waiting', 'reception') for r in lay.rooms_of(k) for t in lay.free_tiles(r)
                  if t not in lay.seats and t not in lay.staff_only]
        everywhere = [t for r in lay.rooms if r.kind not in ('street', 'control')
                      for t in lay.free_tiles(r) if idle_ok(t)]
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
        for i, lift in enumerate(self.lifts):
            self._spawn(self.run_lift, (lift,), f'elevator-{i}')
        self._spawn(self.run_day, (), 'day')

    def run_day(self):
        """The day's own thread: pays the staff by the hour, closes the doors at midnight, and ends the day when the
        last patient is gone (the world then stays paused on the day's report until next_day)."""
        last = self.clock()
        while not self.stop.wait(0.1):
            now = self.clock()
            with self.actors_lock:
                staff = [a for a in self.actors if a.role != 'patient']
            hourly = sum(config.WAGE.get(a.role, 0) * (config.ON_CALL_PAY if a.on_call else 1) for a in staff)
            hour = (now - self.day_start) / config.HOUR_S
            overtime = hour >= config.DAY_HOURS  # past midnight: everyone still here is on overtime
            paid = hourly * (now - last) / config.HOUR_S * (config.OVERTIME_PAY if overtime else 1)
            self.count('wages', paid)
            if overtime:
                self.count('overtime', paid)
            last = now
            if not self.closed and hour >= config.CLOSE_HOUR:
                self.closed = True
                self.log(f'{config.CLOSE_HOUR}:00: no more patients today. The ones inside are seen to by midnight')
            bringing = any(a.role == 'ambulance' or getattr(a, 'bringing', False) for a in self.actors)  # (an
            # ambulance or a car still on its way with someone)
            if self.closed and self.day_over is None and not self.patients() and not bringing:
                self.end_day()

    def today(self):
        """Today's numbers so far (stats since the day started)."""
        with self.stats_lock:
            today = Counter(self.stats)
            today.subtract(self.day_stats)
        return today

    def end_day(self):
        """The day's report, the bonus shared out, and the world paused until next_day."""
        today = self.today()
        with self.stats_lock:
            credit = dict(self.credit)
        profit = today['income'] - today['wages']
        pool = config.BONUS_SHARE * max(0.0, profit)
        total = sum(credit.values())
        bonus = sorted(((pool * c / total, who) for who, c in credit.items()), reverse=True) if total else []
        late = max(0.0, (self.clock() - self.day_start) / config.HOUR_S - config.DAY_HOURS) * 60  # game minutes
        self.day_over = {'day': self.day, 'income': today['income'], 'wages': today['wages'], 'profit': profit,
                         'overtime': today['overtime'], 'overtime_min': late,
                         'bonus': pool, 'treated': today['treated'], 'gave_up': today['gave_up'],
                         'expelled': today['expelled'], 'surgeries': today['surgeries'],
                         'called_in': today['called_in'], 'staff_bonus': bonus}
        self.set_speed(paused=True)
        self.log(f'End of day {self.day}: earned {today["income"]:.0f}, wages {today["wages"]:.0f}, '
                 f'profit {profit:.0f}, staff bonus {pool:.0f}')

    def next_day(self):
        """After the report: the next day starts (from 00:00, doors open)."""
        if self.day_over is None:
            return
        with self.stats_lock:
            self.day_stats, self.credit = Counter(self.stats), Counter()
        self.day, self.day_start, self.closed, self.day_over = self.day + 1, self.clock(), False, None
        self.set_speed(paused=False)
        self.log(f'Day {self.day} starts')

    def run_lift(self, lift):
        """An elevator's own thread: to wherever it's needed (Elevator.target), one of its floors at a time."""
        floors = sorted(lift.stops)
        while not self.stop.is_set():
            with lift.cond:
                target = lift.target()
                if target is None or target == lift.floor:
                    if not lift.open:
                        lift.open = True
                        lift.cond.notify_all()
                    lift.cond.wait(0.05)
                    continue
                lift.open = False
                nxt = floors[floors.index(lift.floor) + (1 if target > lift.floor else -1)]
            self.sleep(config.LIFT_FLOOR_S * abs(nxt - lift.floor))
            with lift.cond:
                lift.floor = nxt
                lift.cond.notify_all()

    def sleep(self, seconds):
        """`seconds` of simulated time (longer while paused or slowed down; cut short by shutdown)."""
        end = self.clock() + seconds
        while self.clock() < end and not self.stop.wait(0.02):
            pass

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
        """A patient walks in from the street (sent by the arrivals process); none after midnight."""
        if self.closed:
            return None
        self.count('arrived')
        if info['urgency'] == 2 and self.lay.ambulance_spot is not None:  # Red: it can't walk, an ambulance brings it
            self.log(f'An ambulance is bringing {info["name"]}')
            a = Ambulance(self, info, self.rng)
            with self.actors_lock:
                self.actors.append(a)
            if self.running:
                self._spawn(a.run, (), f'ambulance-{a.id}')
            return a
        if info['urgency'] == 2:  # (a map with no ambulance: straight onto a bed in Emergency)
            return self.add_waiting(info)
        if self.lay.city_in and self.lay.bays['dropoff']:  # by car: its own if a bay is free, or else a taxi
            car = Car(self, self.rng, info, self.claim_bay('parking'))
            with self.actors_lock:
                self.actors.append(car)
            if self.running:
                self._spawn(car.run, (), f'car-{car.id}')
            return car
        return self.out_of_car(info, None, None)  # (a map with no city: it walks in from the street)

    def out_of_car(self, info, tiles, car):
        """The patient a car brought, getting out on one of `tiles` (it goes back to that car when it leaves)."""
        p = Patient(self, tiles or self.lay.street_edge, self.rng, info)
        p.car = car
        with self.actors_lock:
            self.actors.append(p)
        if self.running:
            self._spawn(p.run, (), f'patient-{p.id}')
        return p

    def discharge(self, p):
        """A cured Red patient can't walk out: a car comes to take it home, and a nurse wheels it there (a task)."""
        p.home = threading.Event()
        car = Car(self, self.rng, pickup=p)
        with self.actors_lock:
            self.actors.append(car)
        if self.running:
            self._spawn(car.run, (), f'car-{car.id}')
        task = Task('wheelchair', 'nurse', 0, tile=(p.tx, p.ty), created=self.clock())
        task.discharged, task.car = p, car
        self.dispatcher.submit(task)
        self.log(f'{p.label()} is going home: a wheelchair, and a car on its way')

    def give_back_theatre(self, p):
        """The operating room a cured Red patient stayed in (Doctor.operate), once it's out of it."""
        room, p.theatre = getattr(p, 'theatre', None), None
        if room is not None:
            self.theatres.give_back(room)

    def claim_bay(self, kind):
        """A free bay of the parking lot ('parking' or 'dropoff'), now the claimer's; None if they're all taken."""
        with self.bays_lock:
            return self.free_bays[kind].pop(0) if self.free_bays[kind] else None

    def free_bay(self, bay):
        with self.bays_lock:
            self.free_bays[bay['kind']].append(bay)

    def announce(self, p, room):
        """A doctor calls p from its waiting room: its name and where to go come up on that room's call screen and
        the loudspeaker says it. Returns when p may go: once it has heard it (ANNOUNCE_S)."""
        now = self.clock()
        with self.events_lock:
            self.calls.append((now, p.id, p.name, p.first_name(), room.name, p.waiting_room.name))
        self.log(f'Loudspeaker: {p.name}, to {room.name}')
        return now + config.ANNOUNCE_S

    def lift_state(self, e):
        """(floor, doors open for someone, going up 1 / down -1 / standing 0): what its indicator and doors show."""
        with e.cond:
            target = e.target()
            going = 0 if target is None or target == e.floor else (1 if target > e.floor else -1)
            return e.floor, e.open and e.rider is not None, going

    def carried_in(self, info, tile):
        """A Red patient out of an ambulance, lying on the paramedics' stretcher at `tile`: its thread waits until
        they've put it on a bed (delivered)."""
        p = Patient(self, [tile], self.rng, info, triaged=True)
        p.lying = p.on_stretcher = True
        p.delivered, p.stage = threading.Event(), 'brought in by ambulance'
        with self.actors_lock:
            self.actors.append(p)
        if self.running:
            self._spawn(p.run, (), f'patient-{p.id}')
        return p

    def claim_bed(self, p):
        """A free bed in Emergency for the paramedics to take p to: (room, the tile beside it, its middle), kept for
        them until release_bed; None if they're all taken."""
        near = self.lay.distances_from((p.tx, p.ty))
        with self.world.lock:
            free = [(near.get(side, 10 ** 6), room, side, middle) for room, beds in self.lay.emergency_beds.items()
                    for side, middle in beds if side not in self.world.occupied and side not in self.bed_claims]
            if not free:
                return None
            _, room, side, middle = min(free, key=lambda f: (f[0], f[2]))
            self.bed_claims.add(side)
            return room, side, middle

    def release_bed(self, side):
        with self.world.lock:
            self.bed_claims.discard(side)

    def delivered(self, p, room):
        """p is on a bed in Emergency: from now on it waits there for a doctor (and the stretcher to surgery)."""
        p.waiting_room, p.triaged_at = room, self.clock()
        self.queue_for_doctor(p)
        self.log(f'The paramedics leave {p.label()} in {room.name}')
        p.delivered.set()

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
        if kind == 'next_day':
            self.next_day()
        elif self.day_over is not None and kind == 'pause':
            self.log('The day is over: start the next one')
        elif kind == 'pause':
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
        if on_call:
            self.count('called_in')
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
            call_in_at, send_home_after, max_on_call, rush_from = d.policy[2:6]  # (learned: learn.py)
            if (now - self.day_start) / config.HOUR_S >= rush_from:  # racing the clock to be done by midnight
                call_in_at /= 2
            most = round(most * max_on_call)
            if waiting[job.role] >= free[job.role] + call_in_at:
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
                if called and now - self.quiet_since[name] > send_home_after:
                    self.quiet_since[name] = now
                    self.fire(job, on_call=True)

    def staff_counts(self):
        return {name: (len(self.working(job)), None if self.places(job) is None else len(self.places(job)))
                for name, job in JOBS.items()}

    def surge(self, seconds):
        if self.closed:
            return
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
                p.waited = self.clock() - p.triaged_at
                self.waits[p.base_urgency].append(p.waited)
            p.triaged_at = None  # only the first time counts (not after an interruption)

    def treated(self, p, surgery=False):
        """p pays: by its urgency, less the longer it waited for a doctor (config.PRICE). The payment is credited to
        whoever did each step for it (config.SHARE): that shares the day's bonus out."""
        p.cured = True  # (the UI shows it over its head until it's gone)
        u, on_time = p.base_urgency, config.ON_TIME_S[p.base_urgency]
        late = max(0.0, p.waited - on_time) / (2 * on_time)  # 0 on time, 1 at three times that
        pay = (config.PRICE[u] + (config.SURGERY_PRICE if surgery else 0)) \
            * max(config.LATE_PAYS, 1 - (1 - config.LATE_PAYS) * late)
        shares = {step: share for step, share in config.SHARE.items() if p.helpers.get(step)}
        shares['doctor'] = shares.get('doctor', 0) + 1 - sum(shares.values())  # the steps nobody did
        with self.stats_lock:
            self.stats['treated'] += 1
            self.stats['income'] += pay
            for step, share in shares.items():
                who = p.helpers.get(step, [])
                for a in who:
                    self.credit[a.label()] += pay * share / len(who)

    def bar_sale(self, bartender):
        """A coffee or a snack sold at the bar: income, credited to whoever sold it."""
        with self.stats_lock:
            self.stats['income'] += config.BAR_PRICE
            self.stats['bar_sales'] += 1
            self.credit[bartender.label()] += config.BAR_PRICE

    def credit_for(self, actor, kind):
        """Security and cleaners: what a fight stopped or a mess cleaned counts for the bonus (config.CREDIT)."""
        with self.stats_lock:
            self.credit[actor.label()] += config.CREDIT[kind]

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
            pushed = {id(a.pushing) for a in actors if getattr(a, 'pushing', None) is not None}
            # every stretcher on the floor that nobody is pushing (the nurses walking to one: it stays where it is)
            parked = [(c.px, c.py, c.facing, c.kind) for c in self.world.occupied.values()
                      if isinstance(c, Cart) and id(c) not in pushed]
        with self.stats_lock:
            stats = dict(self.stats)
        with self.events_lock:
            events, calls = list(self.events), list(self.calls)
        return {'actors': [a.snapshot() for a in actors], 'messes': messes, 'parked': parked, 'events': events,
                'stats': stats,
                'time': self.clock(), 'surge': self.clock() < self.surge_until, 'sim_hz': self.sim_hz,
                'speed': self.rate, 'paused': self.paused, 'staff': self.staff_counts(),
                'keep_up': self.keep_up,
                'day': self.day, 'hour': (self.clock() - self.day_start) / config.HOUR_S, 'closed': self.closed,
                'lifts': [self.lift_state(e) for e in self.lifts], 'calls': calls,
                'day_over': self.day_over, 'today': {k: v for k, v in self.today().items() if k in ('income', 'wages')},
                'line': len(self.line), 'triage': self.triage_queue.qsize(),
                'patients': sum(1 for a in actors if a.role == 'patient')}

    def summary(self):
        s = self.stats
        lines = [f'Simulated time: {self.clock():.0f} s',
                 f'Days: {self.day + 1}  arrived: {s["arrived"]}  surges: {s["surges"]}',
                 f'Earned: {s["income"]:.0f}  wages: {s["wages"]:.0f}  profit: {s["income"] - s["wages"]:.0f}',
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


# Shared memory between the simulation and the UI: a sequence number, how many rows there are, one ROW per actor.
# A seqlock instead of a lock: the writer (only the simulation's main loop) makes the number odd while it writes and
# even again when it's done; a reader copies the rows and keeps the copy only if the number was the same even one
# before and after (else it was being written: it copies again). The writer never waits for a reader, and the
# number is also the version: a reader that already has it skips the copy.
# shortcut: relies on x86 keeping stores in order (and loads in order); on ARM it would need memory barriers
SEQ, COUNT = struct.Struct('<Q'), struct.Struct('<i')
MAX_ROWS = 5000  # shortcut: actors past this many aren't drawn; make it bigger if a hospital ever gets there


def new_positions():
    """The shared block (no lock: see the seqlock above)."""
    return mp.RawArray('B', SEQ.size + COUNT.size + MAX_ROWS * ROW.size)


def write_positions(shared, actors):
    """Packed first; then the number goes odd, the copy into shared memory (microseconds), the number even."""
    rows = [a.numbers() for a in actors[:MAX_ROWS]]
    data = COUNT.pack(len(rows)) + b''.join(ROW.pack(*r) for r in rows)
    block = memoryview(shared).cast('B')
    seq, = SEQ.unpack_from(block)
    SEQ.pack_into(block, 0, seq + 1)  # odd: being written
    block[SEQ.size:SEQ.size + len(data)] = data
    SEQ.pack_into(block, 0, seq + 2)  # even: done


def read_positions(shared, seen=None, tries=1000):
    """(version, {actor id: its ROW}), or (version, None) if it's still `seen` (nothing new) or a whole copy
    couldn't be had in `tries` (a writer that died half-way would otherwise hang the reader)."""
    block = memoryview(shared).cast('B')
    for _ in range(tries):
        seq, = SEQ.unpack_from(block)
        if seq == seen:
            return seq, None
        if seq % 2:  # being written right now
            continue
        n = min(COUNT.unpack_from(block, SEQ.size)[0], MAX_ROWS)
        start = SEQ.size + COUNT.size
        data = block[start:start + n * ROW.size].tobytes()
        if SEQ.unpack_from(block)[0] == seq:  # nobody wrote meanwhile: a consistent copy
            return seq, {r[0]: r for r in ROW.iter_unpack(data)}
    return seen, None


def run_simulation(snapshots, commands, arrivals, log_out, seed=None, speed=None, positions=None, open_=None):
    """Process body. snapshots: Queue -> UI (the words: names, speech, cards, stats). positions: shared memory with
    the UI (where everyone is: write_positions). commands: Queue <- UI ('quit', or an admin tablet order).
    arrivals: Queue <- arrivals process. log_out: Queue -> log process. speed: shared Value with the arrivals
    process (how fast simulated time runs, 0 = paused). open_: shared Event, set while the doors are open (the
    arrivals process sleeps on it at night)."""
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
            # What the arrivals process must know: how fast time runs (the end of a day pauses it too) and whether
            # the doors are open (only written when it changes)
            if speed is not None and speed.value != h.speed:
                speed.value = h.speed
            if open_ is not None and open_.is_set() == h.closed:
                open_.clear() if h.closed else open_.set()
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
