# actors.py - Hospital actors. Each one runs in its own thread inside the simulation process (sim.py).
# Phase 1 behaviour: they only wander their area; decisions (AI) arrive in phase 3.
# No pygame here: the simulation process never draws, it only sends snapshots to the UI process.
import itertools
import math
import os
import random
import threading
import time

import config

DATA = os.path.join(os.path.dirname(__file__), 'data')


def read_names(filename):
    """One name per line; blank lines and lines starting with # are skipped."""
    with open(os.path.join(DATA, filename), encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


FIRST_NAMES = read_names('first_names.txt')
LAST_NAMES = read_names('last_names.txt')

ROLE_LABEL = {'doctor': 'Doctor', 'nurse': 'Nurse', 'receptionist': 'Receptionist',
              'security': 'Security', 'cleaner': 'Cleaner', 'patient': 'Patient'}
# (min pause, max pause in seconds, chance of walking when the pause ends)
PACE = {'doctor': (3, 8, 0.5), 'nurse': (2, 5, 0.8), 'receptionist': (4, 10, 0.0),  # receptionists stay seated
        'security': (2, 6, 0.8), 'cleaner': (2, 5, 0.9), 'patient': (5, 15, 0.3)}

# Which character sheets (assets/third_party/characters/NNN.png) each role may look like
STAFF_LOOKS = {'doctor': [17, 21, 23], 'nurse': [2, 13, 22], 'receptionist': [4, 14, 18],
               'security': [6, 7], 'cleaner': [24, 25]}
# 30-39 are fantasy (angel, zombie, devils...) so they stay out of the hospital for now
PATIENT_LOOKS = [i for i in range(30) if all(i not in v for v in STAFF_LOOKS.values())]
URGENCIES = ['Green', 'Yellow', 'Red']


class World:
    """What the actor threads share. Every read-modify-write of `occupied` happens holding `lock`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.occupied = {}  # tile -> actor standing on it (or stepping into it): nobody walks through anybody


class Actor:
    _ids = itertools.count(1)

    def __init__(self, role, area, rng, world):
        self.id = next(Actor._ids)
        self.role = role
        self.rng = random.Random(rng.random())  # its own generator, since it runs in its own thread
        self.name = f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"
        self.area = area  # tiles it may wander to
        self.world = world
        # Actors are created before any thread starts, so no lock is needed here.
        # shortcut: if the area is already full they share a tile; fine until crowds get that big
        self.tx, self.ty = self.rng.choice([t for t in area if t not in world.occupied] or area)
        world.occupied[(self.tx, self.ty)] = self
        self.px, self.py = self.tx + 0.5, self.ty + 0.5  # continuous position, in tiles
        self.path = []
        self.blocked_for, self.give_way_after = 0.0, self.rng.uniform(0.4, 1.2)
        self.pause = self.rng.uniform(0, PACE[role][1])
        self.anim_t = 0.0
        self.fatigue = 0
        if role == 'patient':
            self.called = False  # set when a doctor calls them; only then may they use the corridor
            self.patience = 100
            self.urgency = self.rng.choices(URGENCIES, weights=[0.6, 0.3, 0.1])[0]
        self.look = self.rng.choice(PATIENT_LOOKS if role == 'patient' else STAFF_LOOKS[role])
        self.facing = 'down'

    def run(self, lay, stop):
        """Thread body: keep updating until the simulation sets `stop`."""
        last = time.perf_counter()
        while not stop.wait(1 / config.ACTOR_HZ):
            now = time.perf_counter()
            self.update(min(now - last, 0.1), lay)
            last = now

    def update(self, dt, lay):
        w = self.world
        if self.path:
            nxt = self.path[0]
            with w.lock:  # check and reserve in one step, so two threads can never take the same tile
                free = w.occupied.setdefault(nxt, self) is self
            if not free:  # someone is on the next tile: wait, then go around
                self.blocked_for += dt
                if self.blocked_for > self.give_way_after:
                    self.blocked_for, self.give_way_after = 0.0, self.rng.uniform(0.4, 1.2)
                    self.path = self.route(lay, self.path[-1], around_people=True)
                    if not self.path:  # no way around: stay here
                        self.settle(lay)
                return
            self.blocked_for = 0.0
            nx, ny = nxt
            dx, dy = nx + 0.5 - self.px, ny + 0.5 - self.py
            dist, step = math.hypot(dx, dy), config.ACTOR_SPEED * dt
            if dist > 1e-6:
                self.facing = ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')
            if dist <= step:
                with w.lock:  # leave the old tile and stand on the new one atomically
                    if w.occupied.get((self.tx, self.ty)) is self:
                        del w.occupied[(self.tx, self.ty)]
                    self.tx, self.ty = nx, ny
                self.px, self.py = nx + 0.5, ny + 0.5
                self.path.pop(0)
                if not self.path:
                    self.settle(lay)
            else:
                self.px += dx / dist * step
                self.py += dy / dist * step
            self.anim_t += dt
            return
        self.pause -= dt
        if self.pause <= 0:
            pmin, pmax, chance = PACE[self.role]
            self.pause = self.rng.uniform(pmin, pmax)
            goal = self.rng.choice(self.area)
            if self.rng.random() < chance and goal not in w.occupied:  # only a hint: stepping in re-checks
                self.path = self.route(lay, goal)

    def settle(self, lay):
        if (self.tx, self.ty) in lay.seats:
            self.facing = 'down'  # everyone sits facing down

    def route(self, lay, goal, around_people=False):
        blocked = lay.staff_only if self.role == 'patient' else frozenset()  # never behind a counter
        if around_people:
            with self.world.lock:
                blocked = blocked | {t for t, a in self.world.occupied.items() if a is not self}
        return lay.path((self.tx, self.ty), goal, self.avoid(), blocked)

    def avoid(self):
        # shortcut: the triage step of phase 2 will open 'triage' to the patient being triaged
        return ('corridor', 'triage') if self.role == 'patient' and not self.called else ()

    def state(self, lay):
        if self.path:
            return 'walking'
        return 'seated' if (self.tx, self.ty) in lay.seats else 'standing'

    def snapshot(self, lay):
        """Plain data for the UI process, which can't see this object (separate memory)."""
        room = lay.room_at[self.ty][self.tx]
        # shortcut: patience/urgency/fatigue don't change yet; they come alive in phases 2 and 3
        if self.role == 'patient':
            extra = [('Urgency', self.urgency), ('Patience', f'{self.patience}/100')]
        else:
            extra = [('Fatigue', f'{self.fatigue}/100'), ('Busy', 'no')]
        return {'id': self.id, 'role': self.role, 'name': self.name, 'look': self.look,
                'px': self.px, 'py': self.py, 'tx': self.tx, 'ty': self.ty, 'facing': self.facing,
                'anim_t': self.anim_t, 'path': list(self.path), 'state': self.state(lay),
                'room': room.name if room else 'doorway', 'extra': extra}


def create_actors(lay, rng):
    def free(*kinds):
        return [t for r in lay.rooms if r.kind in kinds for t in lay.free_tiles(r)]

    def desk_seats(kind):
        return [(f.x, f.y) for f in lay.furniture if f.kind == 'office_chair' and lay.room_at[f.y][f.x].kind == kind]

    waiting_seats = [t for t in lay.seats if lay.room_at[t[1]][t[0]].kind == 'waiting']
    everywhere = free('waiting', 'triage', 'bar', 'reception', 'corridor', 'doctor', 'surgery', 'disinfection',
                      'storage', 'rest')
    world = World()

    # Seated staff first, so their chairs are taken before anyone else spawns; one per desk, never get up
    actors = [Actor('receptionist', [seat], rng, world) for seat in desk_seats('reception')]
    actors += [Actor('nurse', [seat], rng, world) for seat in desk_seats('triage')]
    actors += [Actor('doctor', lay.free_tiles(r), rng, world) for r in lay.rooms_of('doctor')]
    actors += [Actor('nurse', free('corridor', 'surgery', 'disinfection', 'storage'), rng, world)
               for _ in range(config.N_NURSES)]
    actors += [Actor('security', free('waiting'), rng, world) for _ in range(config.N_SECURITY)]
    actors += [Actor('cleaner', everywhere, rng, world) for _ in range(config.N_CLEANERS)]
    # Patients prefer seats (weight 3) but get up now and then
    actors += [Actor('patient', waiting_seats * 3 + free('waiting'), rng, world) for _ in range(config.N_PATIENTS)]
    return actors
