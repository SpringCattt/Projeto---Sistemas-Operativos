# actors.py - Hospital actors. Phase 1: they only wander their area; decisions (AI) arrive in phase 3.
import itertools
import math
import os

import config
from art import character

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
WALK_CYCLE = [0, 1, 2, 1]  # sheet columns: step, idle, step, idle
_looks = {}  # sheet index -> frames, shared by every actor using it
URGENCIES = ['Green', 'Yellow', 'Red']


class Actor:
    _ids = itertools.count(1)

    def __init__(self, role, area, rng):
        self.id = next(Actor._ids)
        self.role = role
        self.name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        self.area = area  # tiles it may wander to
        self.tx, self.ty = rng.choice(area)
        self.px, self.py = self.tx + 0.5, self.ty + 0.5  # continuous position, in tiles
        self.path = []
        self.pause = rng.uniform(0, PACE[role][1])
        self.anim_t = 0.0
        self.fatigue = 0
        if role == 'patient':
            self.called = False  # set when a doctor calls them; only then may they use the corridor
            self.patience = 100
            self.urgency = rng.choices(URGENCIES, weights=[0.6, 0.3, 0.1])[0]
        look = rng.choice(PATIENT_LOOKS if role == 'patient' else STAFF_LOOKS[role])
        if look not in _looks:
            _looks[look] = character(look)
        self.frames = _looks[look]
        self.facing = 'down'

    def update(self, dt, lay, rng):
        if self.path:
            nx, ny = self.path[0]
            dx, dy = nx + 0.5 - self.px, ny + 0.5 - self.py
            dist, step = math.hypot(dx, dy), config.ACTOR_SPEED * dt
            if dist > 1e-6:
                self.facing = ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')
            if dist <= step:
                self.px, self.py, self.tx, self.ty = nx + 0.5, ny + 0.5, nx, ny
                self.path.pop(0)
            else:
                self.px += dx / dist * step
                self.py += dy / dist * step
            self.anim_t += dt
            return
        self.pause -= dt
        if self.pause <= 0:
            pmin, pmax, chance = PACE[self.role]
            self.pause = rng.uniform(pmin, pmax)
            if rng.random() < chance:
                self.path = lay.path((self.tx, self.ty), rng.choice(self.area), self.avoid())

    def avoid(self):
        return ('corridor',) if self.role == 'patient' and not self.called else ()

    def frame(self):
        col = WALK_CYCLE[int(self.anim_t * 6) % 4] if self.path else 1
        return self.frames[self.facing][col]

    def info(self, lay):
        room = lay.room_at[self.ty][self.tx]
        lines = [('Name', self.name), ('ID', self.id), ('Role', ROLE_LABEL[self.role]),
                 ('State', 'walking' if self.path else 'idle'), ('Room', room.name if room else 'doorway')]
        # shortcut: patience/urgency/fatigue don't change yet; they come alive in phases 2 and 3
        if self.role == 'patient':
            lines += [('Urgency', self.urgency), ('Patience', f'{self.patience}/100')]
        else:
            lines += [('Fatigue', f'{self.fatigue}/100'), ('Busy', 'no')]
        return lines


def create_actors(lay, rng):
    def free(*kinds):
        return [t for r in lay.rooms if r.kind in kinds for t in lay.free_tiles(r)]

    rec = lay.rooms_of('reception')[0]
    desk_seats = [(f.x, f.y) for f in lay.furniture if f.kind == 'office_chair' and rec.contains(f.x, f.y)]
    chairs = [(f.x, f.y) for f in lay.furniture
              if f.kind == 'chair' and lay.room_at[f.y][f.x].kind == 'waiting']
    everywhere = free('waiting', 'bar', 'reception', 'corridor', 'doctor', 'surgery', 'disinfection', 'storage', 'rest')

    actors = [Actor('doctor', lay.free_tiles(r), rng) for r in lay.rooms_of('doctor')]
    actors += [Actor('nurse', free('corridor', 'surgery', 'disinfection', 'storage'), rng)
               for _ in range(config.N_NURSES)]
    actors += [Actor('receptionist', [seat], rng) for seat in desk_seats]  # one per desk, always seated
    actors += [Actor('security', free('waiting'), rng) for _ in range(config.N_SECURITY)]
    actors += [Actor('cleaner', everywhere, rng) for _ in range(config.N_CLEANERS)]
    # Patients prefer chairs (weight 3) but get up now and then
    actors += [Actor('patient', chairs * 3 + free('waiting'), rng) for _ in range(config.N_PATIENTS)]
    return actors
