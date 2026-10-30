# actors.py - What every actor has in common. Each actor runs its own script (`life`) in its own thread inside
# the simulation process: plain sequential code ("walk there, wait for this, do that") that advances one tick at a
# time and blocks on the shared structures in world.py. No pygame here: the simulation never draws.
import itertools
import math
import os
import random
import struct
import time
import traceback

import config
import dialogue

DATA = os.path.join(os.path.dirname(__file__), 'data')


def read_names(filename):
    """One name per line; blank lines and lines starting with # are skipped."""
    with open(os.path.join(DATA, filename), encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


FIRST_NAMES = read_names('first_names.txt')
LAST_NAMES = read_names('last_names.txt')

# What the UI needs 60 times a second, as one fixed-size row per actor in shared memory (see sim.write_positions):
# id, px, py, tx, ty, facing, anim_t, lying, on a stretcher, action, the stretcher it pushes (x, y, facing; -1 = none),
# hidden (in the elevator)
ROW = struct.Struct('<iffhhbf??bffb?')
FACINGS = ('down', 'left', 'right', 'up')
ACTIONS = (None, 'typing', 'mopping', 'siren', 'open')  # ('open': an ambulance unloading)

ROLE_LABEL = {'doctor': 'Doctor', 'nurse': 'Nurse', 'receptionist': 'Receptionist', 'security': 'Security',
              'cleaner': 'Cleaner', 'patient': 'Patient', 'dispatcher': 'Dispatcher', 'paramedic': 'Paramedic',
              'ambulance': 'Ambulance'}
# Which character sheets (assets/third_party/characters/NNN.png) each role may look like
STAFF_LOOKS = {'doctor': [17, 21, 23], 'nurse': [2, 13, 22], 'receptionist': [4, 14, 18],
               'security': [6, 7], 'cleaner': [24, 25], 'dispatcher': [5],
               'paramedic': [9, 10, 11]}  # (red and white: the ambulance crew)  # 5: bald, shades: the boss
# 30-39 are fantasy (angel, zombie, devils...) so they stay out of the hospital for now
PATIENT_LOOKS = [i for i in range(30) if all(i not in v for v in STAFF_LOOKS.values())]


STEP_ASIDE_AFTER = 3.0  # simulated seconds blocked before giving way
AROUND_RADIUS = 8  # going round people: only those this close count (the far ones will have moved by then)
LOOK_AHEAD = 4  # tiles a runner looks ahead for someone slower in its way


class Stopped(Exception):
    """The simulation is shutting down: unwinds whatever the actor was doing."""


class Interrupted(Exception):
    """A clearly more urgent task arrived while doing a pre-emptable one."""


class OffShift(Exception):
    """Sent home from the admin tablet: it walks out and its thread ends."""


class Actor:
    _ids = itertools.count(1)
    role = None
    pace = (3, 8, 0.5)  # idle wandering: (min pause, max pause in seconds, chance of walking when it ends)

    def __init__(self, h, candidates, rng, name=None, look=None):
        """h: the Hospital. candidates: tiles it may appear on (the first free one is taken)."""
        self.h, self.lay, self.world = h, h.lay, h.world
        self.id = next(Actor._ids)
        self.rng = random.Random(rng.random())  # its own generator, since it runs in its own thread
        self.name = name or f'{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}'
        self.look = look if look is not None else self.rng.choice(STAFF_LOOKS[self.role])
        candidates = list(candidates)
        self.rng.shuffle(candidates)
        with self.world.lock:
            # shortcut: if every candidate is taken they share a tile; only happens in absurdly full rooms
            self.tx, self.ty = next((t for t in candidates if t not in self.world.occupied), candidates[0])
            self.world.occupied[(self.tx, self.ty)] = self
        self.px, self.py = self.tx + 0.5, self.ty + 0.5  # continuous position, in tiles
        self.path, self.reserved = [], None
        self.blocked_for, self.give_way_after = 0.0, self.rng.uniform(0.4, 1.2)
        self.stuck_for = 0.0  # blocked without moving, in total: after a while it steps aside (see step)
        self.facing, self.anim_t = 'down', 0.0
        self.pause = self.rng.uniform(0, self.pace[1])
        self.area = []  # where it wanders when idle
        self.ticks, self.planned, self._last, self._next = 0, 0.0, None, None
        self.fatigue, self.resting = 0.0, False
        self.task = None  # what it's committed to (set by the dispatcher for doctors, security and cleaners)
        self.doing = 'idle'  # shown on the info card
        self.bubble, self.bubble_until = None, 0.0  # icon over the head: '!' fight, '?' surprise, '~' sick, 'z' rest,
        # '<3' thankful
        self.speech, self.speech_until = None, 0.0  # what it's saying (a speech bubble with text)
        self.speed = 1.0  # x ACTOR_SPEED (running)
        self.lying = self.on_stretcher = False
        self.action = None  # animation while working: 'typing', 'mopping'
        self.pushing = None  # the empty stretcher, while it drives one (staff.WardNurse)
        self.commute, self.quitting = None, False  # hired / sent home (admin tablet, or the Boss)
        self.on_call = False  # called in by the Boss when work piled up (Hospital.staffing)
        self.detour_after = 0.0  # sim time: after a detour that found nothing better, it doesn't look for a while
        self.make_way = False  # a stretcher needs its tile: it steps aside at its next tick (unless it can't)
        self.yield_until, self.aside = 0.0, None  # making way for a stretcher: its step aside, then stays put a moment
        self.driver = None  # (nurse, train) while it's part of a stretcher train (staff.WardNurse.drive)
        self.hidden = False  # inside the elevator: not drawn

    def label(self):
        return f'{ROLE_LABEL[self.role]} {self.name}'

    # ---- thread ---------------------------------------------------------------------------------------------
    def run(self):
        """Thread body."""
        self._last = time.perf_counter()
        try:
            try:
                if self.commute:  # just hired: walks in from the street first
                    self.go(self.commute, timeout=240)
                self.life()
            except OffShift:
                self.doing = 'going home'
                self.walk_out()
        except Stopped:
            pass
        except Exception:
            self.h.error(self, traceback.format_exc())
        finally:
            self.h.retire(self)

    def life(self):
        raise NotImplementedError

    # ---- time -----------------------------------------------------------------------------------------------
    def tick(self):
        """Waits one tick (1/ACTOR_HZ s) and returns how much simulated time passed. Raises Stopped at shutdown,
        and whatever upkeep() raises (Interrupted)."""
        # Wait until the next deadline rather than a fixed time: Windows rounds waits up to ~15.6 ms, and this way
        # a long wait is followed by a shorter one, so the average stays at ACTOR_HZ
        period = 1 / (config.ACTOR_HZ if self.path else config.IDLE_HZ)
        self._next = (self._next or self._last) + period
        delay = self._next - time.perf_counter()
        if delay < -period:  # far behind (overloaded): don't try to catch up in a burst
            self._next, delay = time.perf_counter(), 0.0
        if self.h.stop.wait(max(0.0, delay)):
            raise Stopped
        now = time.perf_counter()
        dt = min(now - self._last, 0.1) * self.h.speed  # 0 while the world is paused
        self._last = now
        self.ticks += 1
        self.planned += period  # if it keeps up, this grows as fast as the clock (see Hospital.measure)
        if self.bubble and self.bubble_until and self.h.clock() > self.bubble_until:
            self.bubble, self.bubble_until = None, 0.0
        self.upkeep(dt)
        if self.make_way:
            self.make_way = False
            if not self.lying and self.driver is None and (self.tx, self.ty) not in self.lay.seats:
                self.give_way()
        return dt

    def upkeep(self, dt):
        """Per-tick housekeeping of each role (fatigue, interruptions...)."""

    def wait(self, seconds):
        """Stays put for `seconds` of simulated time."""
        end = self.h.clock() + seconds
        while self.h.clock() < end:
            self.step(self.tick())

    def wait_until(self, condition, timeout=None):
        """Ticks until condition() holds (True) or `timeout` simulated seconds pass (False)."""
        end = None if timeout is None else self.h.clock() + timeout
        while not condition():
            if end is not None and self.h.clock() >= end:
                return False
            self.step(self.tick())
        return True

    def say(self, bubble, seconds):
        self.bubble, self.bubble_until = bubble, self.h.clock() + seconds

    def talk(self, situation, seconds=3.0, **values):
        """Says a random line for that situation (data/dialogue.txt) in a speech bubble."""
        text = dialogue.pick(self.rng, situation, **values)
        if text:
            self.speech, self.speech_until = text, self.h.clock() + seconds

    def first_name(self):
        return self.name.split()[0]

    # ---- movement -------------------------------------------------------------------------------------------
    def avoid(self):
        """Room kinds it may not walk through."""
        return ()

    def no_go(self):
        """Tiles it may not walk on."""
        return frozenset()

    def route(self, goal, around_people=False, limit=None):
        """around_people: also around everyone close by (they'd be in the way), not just walls and furniture."""
        blocked = set(self.no_go())
        with self.world.lock:
            if self.role != 'cleaner':  # everyone walks around a mess, except whoever comes to clean it
                blocked |= self.world.messes.keys()
            if around_people:
                blocked |= {t for t, a in self.world.occupied.items() if a is not self and t != goal
                            and abs(t[0] - self.tx) + abs(t[1] - self.ty) <= AROUND_RADIUS}
        return self.lay.path((self.tx, self.ty), goal, self.avoid(), blocked, limit)

    def in_my_way(self, other):
        """Not worth waiting for: standing still, or slower than me."""
        return not getattr(other, 'path', None) or getattr(other, 'speed', 0) < self.speed

    def detour(self, extra):
        """Takes a way round the people nearby if it's at most `extra` tiles longer than the current one.
        The search gives up early (a route that long can't be far off), and after a failed one it waits a second
        before looking again: a crowd would otherwise have everyone searching every tick."""
        if not self.path or self.h.clock() < self.detour_after:
            return
        r = self.route(self.path[-1], around_people=True, limit=len(self.path) + extra)
        if r and len(r) <= len(self.path) + extra:
            self.set_path(r)
        else:
            self.detour_after = self.h.clock() + 1.0

    def set_path(self, path):
        """Follow `path` from now on. If it was half-way into a tile the new path doesn't use, it gives that tile
        back and first steps back to the centre of its own."""
        with self.world.lock:
            if self.reserved and (not path or path[0] != self.reserved):
                if self.world.occupied.get(self.reserved) is self:
                    del self.world.occupied[self.reserved]
                self.reserved = None
                if (self.px, self.py) != (self.tx + 0.5, self.ty + 0.5):
                    path = [(self.tx, self.ty)] + path
        self.path = path

    def step(self, dt):
        """One tick along self.path: reserve the next tile (or wait / go around whoever is on it) and move."""
        if not self.path:
            return
        if self.path != self.aside and not self.reserved and self.h.clock() < self.yield_until:
            return  # letting a stretcher by
        w = self.world
        nxt = self.path[0]
        with w.lock:  # check and reserve in one step, so two threads can never take the same tile
            free = w.occupied.setdefault(nxt, self) is self
            if free and nxt != (self.tx, self.ty):
                self.reserved = nxt
        if not free:  # someone is there: wait a moment (no time at all if it's in my way), then go round
            other = w.occupied.get(nxt)
            self.blocked_for += dt
            self.stuck_for += dt
            if self.stuck_for > 1 and hasattr(other, 'make_way') and not other.path:
                other.make_way = True  # someone just standing in the way: "excuse me" (it steps aside if it can)
            if getattr(other, 'driver', None) is not None and self.driver is None:
                self.give_way(nxt)
            elif self.stuck_for > STEP_ASIDE_AFTER:
                self.step_aside(nxt)
            elif self.blocked_for > (0.1 if self.speed > 1 and self.in_my_way(other) else self.give_way_after):
                self.blocked_for, self.give_way_after = 0.0, self.rng.uniform(0.4, 1.2)
                self.detour(extra=12)
            return
        self.blocked_for = self.stuck_for = 0.0
        nx, ny = nxt
        dx, dy = nx + 0.5 - self.px, ny + 0.5 - self.py
        if abs(nx - self.tx) + abs(ny - self.ty) > 1:  # the stairs: straight to the same place a floor up or down
            dx = dy = 0.0
        dist, stride = math.hypot(dx, dy), config.ACTOR_SPEED * self.speed * dt
        if dist > 1e-6:
            self.facing = ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')
        if dist <= stride:
            with w.lock:  # leave the old tile and stand on the new one atomically
                if nxt != (self.tx, self.ty) and w.occupied.get((self.tx, self.ty)) is self:
                    del w.occupied[(self.tx, self.ty)]
                self.tx, self.ty, self.reserved = nx, ny, None
            self.px, self.py = nx + 0.5, ny + 0.5
            self.path.pop(0)
            if not self.path:
                self.settle()
            elif self.speed > 1:  # running: overtakes whoever slower is just ahead instead of catching up on them
                with w.lock:
                    ahead = [w.occupied.get(t) for t in self.path[:LOOK_AHEAD]]
                if any(a is not None and a is not self and self.in_my_way(a) for a in ahead):
                    self.detour(extra=4)
        else:
            self.px += dx / dist * stride
            self.py += dy / dist * stride
        self.anim_t += dt * self.speed

    def give_way(self, blocked_tile=None):
        """A stretcher always goes first: out of its way (even mid-route) and stay there a moment."""
        self.step_aside(blocked_tile)
        self.aside, self.yield_until = list(self.path), self.h.clock() + 1.5

    def step_aside(self, blocked_tile):
        """Two actors can block each other forever in a narrow spot (each waiting for the other's tile: a
        deadlock). Whoever has waited too long gives way: it moves to a free tile next to it, and its caller works
        out a new route from there."""
        x, y = self.tx, self.ty
        no_go, avoid = self.no_go(), self.avoid()
        with self.world.lock:
            options = [t for t in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                       if t != blocked_tile and self.lay.walkable(*t) and t not in self.world.occupied
                       and t not in no_go and t not in self.world.messes
                       and (self.lay.room_at[t[1]][t[0]] is None or self.lay.room_at[t[1]][t[0]].kind not in avoid)]
        self.stuck_for = 0.0
        self.set_path([self.rng.choice(options)] if options else [])

    def walk_to(self, goal, around_people=False):
        """Walks to `goal` tick by tick. False if it can't get there right now (blocked or unreachable)."""
        if (self.tx, self.ty) == goal and not self.reserved:
            self.path = []
            self.settle()
            return True
        self.set_path(self.route(goal, around_people))
        if not self.path:
            self.tick()  # let time pass, so callers can simply try again
            return False
        while self.path:
            self.step(self.tick())
        return (self.tx, self.ty) == goal

    def go(self, goal, timeout=60):
        """Keeps trying to reach `goal` for up to `timeout` simulated seconds."""
        end = self.h.clock() + timeout
        while not self.walk_to(goal, around_people=self.h.clock() > end - timeout / 2):
            if self.h.clock() > end:
                return False
        return True

    def lie_on(self, spot):
        """Climbs onto a bed / table centred at `spot` (in tiles); the tile it stands on stays its own."""
        self.lying = True
        self.slide((spot[0], spot[1] - 0.1))

    def get_up(self):
        if self.lying:
            self.slide((self.tx + 0.5, self.ty + 0.5))
            self.lying = False

    def slide(self, to, speed=2.0):
        while (self.px, self.py) != to:
            dx, dy = to[0] - self.px, to[1] - self.py
            d, stride = math.hypot(dx, dy), speed * self.tick()
            self.px, self.py = to if d <= stride else (self.px + dx / d * stride, self.py + dy / d * stride)

    def walk_out(self, timeout=120):
        """Leaves by the nearest way out (the street in front, or the exits at both ends of the corridor)."""
        end = self.h.clock() + timeout
        while self.h.clock() < end:
            free = [t for t in self.lay.exits if t not in self.world.occupied]
            near = self.lay.distances_from((self.tx, self.ty))
            if not free:
                self.tick()
            elif self.walk_to(min(free, key=lambda t: near.get(t, 10 ** 6))):
                return

    def settle(self):
        if (self.tx, self.ty) in self.lay.seats:
            self.facing = 'down'  # everyone sits facing down

    def wander(self, dt):
        """One tick of idling: keep walking, or now and then pick somewhere in `area` to go."""
        if self.path:
            self.step(dt)
            return
        self.pause -= dt
        if self.pause <= 0:
            pmin, pmax, chance = self.pace
            self.pause = self.rng.uniform(pmin, pmax)
            goal = self.rng.choice(self.area) if self.area else None
            if goal and self.rng.random() < chance and goal not in self.world.occupied:
                self.set_path(self.route(goal))

    def state(self):
        if self.lying:
            return 'on a stretcher' if self.on_stretcher else 'lying'
        if self.path:
            return 'running' if self.speed > 1 else 'walking'
        return 'seated' if (self.tx, self.ty) in self.lay.seats else 'standing'

    # ---- what the UI sees -----------------------------------------------------------------------------------
    def card(self):
        """Extra lines for the info card."""
        return []

    def snapshot(self):
        """The words for the UI process, which can't see this object (separate memory); where it is and how it's
        drawn go through shared memory instead (numbers)."""
        room = self.lay.room_at[self.ty][self.tx]
        return {'id': self.id, 'role': self.role, 'name': self.name, 'look': self.look,
                'path': list(self.path), 'state': self.state(),
                'room': room.name if room else 'doorway', 'bubble': self.bubble, 'mark': None,
                'speech': self.speech if self.h.clock() < self.speech_until else None,
                'card': [('Doing', self.doing)] + self.card()}

    def numbers(self):
        """Its row in shared memory (ROW)."""
        c = self.pushing
        return (self.id, self.px, self.py, self.tx, self.ty, FACINGS.index(self.facing), self.anim_t, self.lying,
                self.on_stretcher, ACTIONS.index(self.action), c.px if c else 0.0, c.py if c else 0.0,
                FACINGS.index(c.facing) if c else -1, self.hidden)
