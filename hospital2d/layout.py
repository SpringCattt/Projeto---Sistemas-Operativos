# layout.py - Builds the hospital floor plan (rooms, doors and furniture) from config.py
import functools
import heapq
from collections import deque
from dataclasses import dataclass

import config

EMPTY, WALL, FLOOR, DOOR = 0, 1, 2, 3

# Size (width, height) of each medical-wing room type, walls included
SIZES = {
    'doctor': (8, 8), 'surgery': (10, 9), 'disinfection': (6, 9),
    'storage': (14, 9), 'rest': (12, 9),
}
MARGIN = 3  # grass tiles around the building
SEATS = {'chair', 'office_chair', 'bench', 'sofa'}  # furniture people sit on (every tile of a bench is a seat)
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
TURN = 0.001  # extra cost of a turn when planning a route


@dataclass(eq=False)  # rooms are compared by identity (and used as dict keys)
class Room:
    kind: str
    name: str  # shown on screen
    x: int
    y: int
    w: int
    h: int
    walls: bool = True
    flipped: bool = False  # door on top instead of bottom (furniture is mirrored)
    partner: 'Room' = None  # triage -> its waiting room; surgery -> the disinfection room doctors enter through
    round: bool = False  # a circle inside its box (the control room); its walls follow the circle

    @property
    def interior(self):  # (x0, y0, x1, y1) inclusive (for a round room: the box around the circle)
        a = 1 if self.walls else 0
        return self.x + a, self.y + a, self.x + self.w - 1 - a, self.y + self.h - 1 - a

    def contains(self, x, y):
        x0, y0, x1, y1 = self.interior
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        if not self.round:
            return True
        cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + 0.1
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


@dataclass
class Furniture:
    kind: str
    x: int
    y: int
    solid: bool = True
    w: int = 1
    h: int = 1

    def tiles(self):
        return [(x, y) for y in range(self.y, self.y + self.h) for x in range(self.x, self.x + self.w)]


class Layout:
    def __init__(self, rooms, doors, width, height):
        self.rooms, self.width, self.height = rooms, width, height
        self.tiles = [[EMPTY] * width for _ in range(height)]
        self.room_at = [[None] * width for _ in range(height)]
        for r in sorted(rooms, key=lambda r: r.round):  # round rooms last: they sit inside another room
            for y in range(r.y, r.y + r.h):
                for x in range(r.x, r.x + r.w):
                    if r.contains(x, y):
                        self.tiles[y][x], self.room_at[y][x] = FLOOR, r
                    elif r.round:  # its wall: the tiles touching the circle from outside
                        if any(r.contains(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                            self.tiles[y][x], self.room_at[y][x] = WALL, None
                    elif self.tiles[y][x] != FLOOR:  # a shared wall never erases the neighbour's floor
                        self.tiles[y][x] = WALL
        self.doors = {}  # (x, y) -> 'h' (door in a horizontal wall) or 'v'
        for x, y, door_width, axis in doors:
            for i in range(door_width):
                t = (x + i, y) if axis == 'h' else (x, y + i)
                self.tiles[t[1]][t[0]] = DOOR
                self.doors[t] = axis

        # Furniture: nothing may touch a door, or it would block the way
        near_door = {(x + dx, y + dy) for x, y in self.doors for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
        self.furniture = [f for r in rooms for f in furnish(r)
                          if all(self.room_at[t[1]][t[0]] is r and t not in near_door for t in f.tiles())]
        self.solid = {t for f in self.furniture if f.solid for t in f.tiles()}

        self.seats = {t for f in self.furniture if f.kind in SEATS for t in f.tiles()}

        # Behind a counter (reception, bar) is staff only: patients never walk there
        self.staff_only = set()
        for r in rooms:
            counter_rows = [f.y for f in self.furniture if f.kind == 'counter' and r.contains(f.x, f.y)]
            if counter_rows:
                x0, y0, x1, y1 = r.interior
                behind = range(max(counter_rows) + 1, y1 + 1) if r.flipped else range(y0, min(counter_rows))
                self.staff_only |= {(x, y) for y in behind for x in range(x0, x1 + 1)}
        self._find_spots(near_door)

    def _find_spots(self, near_door):
        """Named places the actors use: desks, the reception line, where to wait, where to operate..."""
        def furniture_in(room, kind):
            return [f for f in self.furniture if f.kind == kind and room.contains(f.x, f.y)]

        rec = self.rooms_of('reception')[0]
        x0, y0, x1, y1 = rec.interior
        # Each receptionist's seat, and the tile in front of the counter where the patient stands
        self.reception_desks = [((f.x, f.y), (f.x, f.y + 2)) for f in furniture_in(rec, 'office_chair')]
        fronts = {front for _, front in self.reception_desks}
        # The line: a snake over the 3 middle columns, starting just below the counter
        cx = (x0 + x1) // 2
        self.line_spots = []
        for i, x in enumerate((cx, cx + 1, cx - 1)):
            ys = range(y0 + 4, y1) if i % 2 == 0 else range(y1 - 1, y0 + 3, -1)
            self.line_spots += [(x, y) for y in ys if self.walkable(x, y) and (x, y) not in near_door]
        # Registered patients wait to be called to triage anywhere else on the patients' side
        taken = fronts | set(self.line_spots) | self.seats
        self.lobby = [t for t in self.free_tiles(rec) if t[1] >= y0 + 3 and t not in taken and t not in near_door]
        # Outside: the closest tiles to the entrance hold the overflow of the line; people appear/leave further away
        street = self.rooms_of('street')[0]
        ex, ey = rec.x + rec.w // 2, rec.y + rec.h
        by_distance = sorted(self.free_tiles(street), key=lambda t: abs(t[0] - ex) + abs(t[1] - ey))
        self.street_line, self.street_edge = by_distance[:20], by_distance[20:]

        self.triage_desks = [(r, (f.x, f.y), (f.x + 1, f.y + 2))  # (room, nurse seat, patient chair)
                             for r in self.rooms_of('triage') for f in furniture_in(r, 'office_chair')]
        self.doctor_desks = {}  # room -> (doctor seat, patient chair)
        for r in self.rooms_of('doctor'):
            seats, chairs = furniture_in(r, 'office_chair'), furniture_in(r, 'chair')
            if seats and chairs:
                self.doctor_desks[r] = ((seats[0].x, seats[0].y), (chairs[0].x, chairs[0].y))
        self.surgery_spots = {}  # room -> (patient tile, doctor tile), on each side of the operating table
        for r in self.rooms_of('surgery'):
            t = furniture_in(r, 'operating_table')[0]
            mid = t.y + t.h // 2

            def beside(x):
                return next((p for p in ((x, mid), (x, mid - 1), (x, mid + 1)) if self.walkable(*p)), None)
            self.surgery_spots[r] = (beside(t.x - 1), beside(t.x + t.w))
        self.sink_spots = {}  # disinfection room -> tiles in front of its sinks
        for r in self.rooms_of('disinfection'):
            fronts = {(x, y + d) for f in furniture_in(r, 'sink') for x, y in f.tiles() for d in (1, -1)}
            self.sink_spots[r] = sorted(t for t in fronts if r.contains(*t) and self.walkable(*t))
        self.waiting_seats = {r: [t for t in self.free_tiles(r) if t in self.seats] for r in self.rooms_of('waiting')}
        self.control_seat = next((f.x, f.y) for r in self.rooms_of('control') for f in furniture_in(r, 'office_chair'))
        self.rest_spots = [t for r in self.rooms_of('rest') for t in self.free_tiles(r) if t in self.seats]

    def walkable(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height \
            and self.tiles[y][x] in (FLOOR, DOOR) and (x, y) not in self.solid

    def rooms_of(self, kind):
        return [r for r in self.rooms if r.kind == kind]

    def free_tiles(self, room):
        x0, y0, x1, y1 = room.interior
        return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
                if self.room_at[y][x] is room and self.walkable(x, y)]

    def walk_distance(self, a, b):
        """Tiles to walk from a to b, around walls and furniture (not people)."""
        d = self._distances_from(a).get(b)
        return d if d is not None else 10 * (abs(a[0] - b[0]) + abs(a[1] - b[1]))

    @functools.lru_cache(maxsize=512)
    def _distances_from(self, start):  # breadth-first search over the whole plan, cached per start tile
        dist, todo = {start: 0}, deque([start])
        while todo:
            x, y = todo.popleft()
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb not in dist and self.walkable(*nb):
                    dist[nb] = dist[(x, y)] + 1
                    todo.append(nb)
        return dist

    def path(self, start, goal, avoid=(), blocked=frozenset()):
        """4-direction A*: the shortest route, and of all the shortest ones the one with fewest turns, so people walk
        straight instead of zig-zagging. Returns the tiles up to goal (start excluded), or [] if unreachable.
        avoid: room kinds the walker may not step into (e.g. patients and the corridor).
        blocked: tiles it may not step on (staff-only areas, other people)."""
        def ok(x, y):
            if not self.walkable(x, y) or (x, y) in blocked:
                return False
            room = self.room_at[y][x]  # None on door tiles
            return room is None or room.kind not in avoid

        if start == goal or not ok(*goal):
            return []
        gx, gy = goal
        # The state is (tile, direction it came from), so a turn can cost a little: TURN, far less than a step,
        # never makes a route longer, it only picks the straightest of the shortest ones
        open_ = [(abs(start[0] - gx) + abs(start[1] - gy), 0, 0.0, start, -1)]
        best, came = {(start, -1): 0.0}, {}
        while open_:
            _, _, g, cur, d = heapq.heappop(open_)
            if cur == goal:
                route, key = [], (cur, d)
                while key[0] != start:
                    route.append(key[0])
                    key = came[key]
                return route[::-1]
            if g > best[(cur, d)]:
                continue
            for nd, (ddx, ddy) in enumerate(DIRECTIONS):
                nb = (cur[0] + ddx, cur[1] + ddy)
                if not ok(*nb):
                    continue
                ng = g + 1 + (TURN if d not in (-1, nd) else 0.0)
                if ng < best.get((nb, nd), float('inf')):
                    best[(nb, nd)] = ng
                    came[(nb, nd)] = (cur, d)
                    h = abs(nb[0] - gx) + abs(nb[1] - gy)
                    heapq.heappush(open_, (ng + h, h, ng, nb, nd))
        return []


def door_top(r):
    return r.x + r.w // 2 - 1, r.y, 2, 'h'


def door_bottom(r):
    return r.x + r.w // 2 - 1, r.y + r.h - 1, 2, 'h'


def door_between(a, b):
    """Door in the shared vertical wall (b is to the right of a)."""
    y0, y1 = max(a.y, b.y) + 1, min(a.y + a.h, b.y + b.h) - 2
    return b.x, (y0 + y1) // 2, 2, 'v'


def surgery_blocks(n):
    """Groups of surgery | disinfection | surgery (each disinfection room serves the surgeries beside it); with an
    odd number, the last group is disinfection | surgery. Each group is placed as one block."""
    blocks, s, d = [], 0, 0
    for i in range(0, n, 2):
        d += 1
        if i + 1 < n:
            blocks.append([('surgery', f'Surgery {s + 1}'), ('disinfection', f'Disinfection {d}'),
                           ('surgery', f'Surgery {s + 2}')])
            s += 2
        else:
            blocks.append([('disinfection', f'Disinfection {d}'), ('surgery', f'Surgery {s + 1}')])
            s += 1
    return blocks


def round_doors(r):
    """A door on each side of a round room (north, south, west, east), where the circle meets the axes."""
    xs = (r.x + r.w // 2 - 1, r.x + r.w // 2)
    ys = (r.y + r.h // 2 - 1, r.y + r.h // 2)
    column = [y for y in range(r.y, r.y + r.h) if r.contains(xs[0], y)]
    row = [x for x in range(r.x, r.x + r.w) if r.contains(x, ys[0])]
    return [(xs[0], min(column) - 1, 2, 'h'), (xs[0], max(column) + 1, 2, 'h'),
            (min(row) - 1, ys[0], 2, 'v'), (max(row) + 1, ys[0], 2, 'v')]


def build_layout(n_doctors=config.MAX_DOCTORS, n_surgeries=config.N_SURGERIES):
    """The hospital is built around its centre: the Main Hall, with the dispatcher's round Control Room in the
    middle. The main corridor runs through the hall (y 0..4) and out to both sides. Under the hall, the base
    block (always the same): street -> reception -> triage -> waiting rooms, with the bar. Every other room is
    added around the centre, each to whichever side is shortest so far: on top of the hall, or along the
    corridor (above or below it, left or right), so the hospital grows evenly around the control room."""
    # shortcut: one floor; when floors arrive, a building becomes a list of these
    w1 = Room('waiting', 'Waiting Room 1', 0, 4, 20, 14)
    t1 = Room('triage', 'Triage 1', 9, 17, 11, 6)
    bar = Room('bar', 'Bar / Cafe', 19, 4, 16, 8, flipped=True)  # door on top: counter at the back
    rec = Room('reception', 'Entrance and Reception', 19, 11, 16, 12)
    t2 = Room('triage', 'Triage 2', 34, 17, 11, 6)
    w2 = Room('waiting', 'Waiting Room 2', 34, 4, 20, 14)
    street = Room('street', 'Street', 15, 23, 24, 4, walls=False)
    t1.partner, t2.partner = w1, w2
    hall = Room('corridor', 'Main Hall', 12, -16, 30, 21)  # its bottom rows are the main corridor
    control = Room('control', 'Control Room', 21, -12, 12, 12, round=True)
    rooms = [w1, t1, bar, rec, t2, w2, street, hall, control]
    doors = [door_top(w1), door_top(bar), door_top(w2),  # to the corridor / hall
             door_between(t1, rec), door_between(rec, t2),  # reception -> triage
             door_top(t1), door_top(t2),  # triage -> waiting room
             (rec.x, rec.y + 4, 2, 'v'), (rec.x + rec.w - 1, rec.y + 4, 2, 'v'),  # waiting room -> reception (way out)
             (rec.x + rec.w // 2 - 1, rec.y + rec.h - 1, 3, 'h'),  # big main entrance
             (hall.x, 1, 3, 'v'), (hall.x + hall.w - 1, 1, 3, 'v')]  # the corridor goes through the hall
    doors += round_doors(control)

    # Where new rooms go. pos: x of the wall the next room leans on; step: +1 grows right, -1 left;
    # wall: y the rooms rest on (top rows: above it, door at the bottom) or hang from (bottom rows)
    rows = [
        {'pos': hall.x, 'step': 1, 'wall': hall.y, 'top': True, 'limit': hall.x + hall.w - 1},  # on top of the hall
        {'pos': hall.x + hall.w - 1, 'step': 1, 'wall': 0, 'top': True, 'limit': None},  # corridor, right, above
        {'pos': hall.x, 'step': -1, 'wall': 0, 'top': True, 'limit': None},  # corridor, left, above
        {'pos': w2.x + w2.w - 1, 'step': 1, 'wall': 4, 'top': False, 'limit': None},  # corridor, right, below
        {'pos': w1.x, 'step': -1, 'wall': 4, 'top': False, 'limit': None},  # corridor, left, below
    ]
    for row in rows:
        row['used'] = 0

    # Doctors' rooms first, so they're the closest to the centre; then surgery, storage and rest
    blocks = [[('doctor', f'Doctor\'s Room {i + 1}')] for i in range(n_doctors)]
    blocks += surgery_blocks(n_surgeries) + [[('storage', 'Storage')], [('rest', 'Rest')]]
    for block in blocks:
        width = sum(SIZES[kind][0] for kind, _ in block) - (len(block) - 1)  # neighbours share a wall
        fits = [r for r in rows if r['limit'] is None or r['pos'] + width - 1 <= r['limit']]
        row = min(fits, key=lambda r: r['used'])
        row['used'] += width
        prev = None
        for kind, name in block:
            w, h = SIZES[kind]
            x = row['pos'] if row['step'] > 0 else row['pos'] - w + 1
            y = row['wall'] - h + 1 if row['top'] else row['wall']
            r = Room(kind, name, x, y, w, h, flipped=not row['top'])
            rooms.append(r)
            row['pos'] = x + w - 1 if row['step'] > 0 else x
            # Surgery has a corridor door (patients); doctors come in through disinfection
            doors.append(door_top(r) if r.flipped else door_bottom(r))
            if prev and 'disinfection' in (prev.kind, r.kind):
                doors.append(door_between(prev, r) if row['step'] > 0 else door_between(r, prev))
                surgery, disinfection = (prev, r) if r.kind == 'disinfection' else (r, prev)
                surgery.partner = disinfection
            prev = r

    # The corridor's two arms reach as far as the rooms on each side
    left = min([0] + [r.x for r in rooms if r.x < hall.x and r.y < 23])
    right = max([w2.x + w2.w - 1] + [r.x + r.w - 1 for r in rooms if r.x > hall.x])
    rooms.append(Room('corridor', 'Corridor', left, 0, hall.x - left + 1, 5))
    rooms.append(Room('corridor', 'Corridor', hall.x + hall.w - 1, 0, right - hall.x - hall.w + 2, 5))

    # Shift everything to positive coordinates, with a grass margin
    min_x, min_y = min(r.x for r in rooms), min(r.y for r in rooms)
    dx, dy = MARGIN - min_x, MARGIN - min_y
    for r in rooms:
        r.x, r.y = r.x + dx, r.y + dy
    doors = [(x + dx, y + dy, l, a) for x, y, l, a in doors]
    width = max(r.x + r.w for r in rooms) + MARGIN
    height = max(r.y + r.h for r in rooms) + MARGIN
    return Layout(rooms, doors, width, height)


def furnish(r):
    """Furniture for each room type, placed as if the door were at the bottom."""
    x0, y0, x1, y1 = r.interior
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    k, f = r.kind, []
    if k == 'waiting':
        for y in range(y0 + 3, y1 - 1, 3):
            f += [Furniture('chair', x, y, False) for x in range(x0 + 2, x1 - 1) if x != cx]
        f += [Furniture('tv', cx, y0), Furniture('vending', x1, y0), Furniture('soda', x1 - 1, y0),
              Furniture('plant', x0, y0), Furniture('plant', x1, y1), Furniture('clock', cx - 3, y0),
              Furniture('poster', cx + 3, y0), Furniture('sanitizer', x0, y0 + 2), Furniture('trash_bin', x0, y1),
              Furniture('bench', x0 + 2, y1, False, w=3), Furniture('bench', x1 - 4, y1, False, w=3)]
    elif k == 'reception':
        f += [Furniture('counter', x, y0 + 2) for x in range(x0 + 2, x1 - 1)]
        n = config.N_RECEPTIONISTS
        for i in range(n):  # one seat + monitor per receptionist
            x = x0 + 2 + (2 * i + 1) * (x1 - x0 - 3) // (2 * n)
            f += [Furniture('office_chair', x, y0 + 1, False), Furniture('pc', x, y0 + 2)]
        f += [Furniture('bell', cx, y0 + 2), Furniture('plant', x0, y1), Furniture('plant', x1, y1),
              Furniture('trash_bin', x1, y0 + 5), Furniture('clock', cx, y0), Furniture('cabinet', x0, y0),
              Furniture('cabinet', x1, y0), Furniture('map_stand', x0 + 1, y1 - 1, w=2), Furniture('sanitizer', x0, y0 + 5),
              Furniture('exit_sign', cx - 1, y1)]
    elif k == 'triage':
        # booths: nurse chair, desk with pc, patient chair (left booth first, right one if there are 2)
        for x in [x0 + 1, x1 - 2][:config.TRIAGE_DESKS_PER_ROOM]:
            f += [Furniture('office_chair', x, y0, False), Furniture('desk', x, y0 + 1, w=2),
                  Furniture('pc', x, y0 + 1), Furniture('chair', x + 1, y0 + 2, False)]
    elif k == 'bar':
        f += [Furniture('counter', x, y0 + 1) for x in range(x0 + 2, x1 - 1)]
        # back wall items side by side, so the lane behind the counter stays open from both ends
        f += [Furniture('coffee', x0 + 3, y0), Furniture('fridge', x0 + 4, y0), Furniture('snacks', x0 + 5, y0),
              Furniture('soda', x0 + 6, y0), Furniture('cake', x0 + 5, y0 + 1), Furniture('donuts', x0 + 3, y0 + 1),
              Furniture('plant', x0, y1), Furniture('plant', x1, y1), Furniture('trash_bin', x1, y0 + 2)]
        for x in range(x0 + 2, x1 - 1, 4):
            f += [Furniture('table', x, y1 - 1), Furniture('chair', x - 1, y1 - 1, False), Furniture('chair', x + 1, y1 - 1, False)]
    elif k == 'control':
        # the dispatcher's desk in the middle of the round room, facing the south door, screens all around
        f += [Furniture('office_chair', cx, cy, False), Furniture('desk', cx, cy + 1, w=2), Furniture('pc', cx, cy + 1),
              Furniture('books', cx + 1, cy + 1), Furniture('tv', cx - 2, y0 + 1), Furniture('tv', cx + 3, y0 + 1),
              Furniture('cabinet', x0 + 1, y1 - 2), Furniture('cabinet', x1 - 1, y1 - 2),
              Furniture('plant', x0 + 1, y0 + 2), Furniture('plant', x1 - 1, y0 + 2)]
    elif k == 'doctor':
        f += [Furniture('desk', cx - 1, y0 + 1, w=2), Furniture('pc', cx - 1, y0 + 1), Furniture('office_chair', cx - 1, y0, False),
              Furniture('chair', cx, y0 + 2, False), Furniture('exam_bed', x1, y0 + 2, h=2), Furniture('plant', x0, y0),
              Furniture('books', cx, y0 + 1), Furniture('eye_chart', x1, y0), Furniture('bp_monitor', x1, y0 + 4),
              Furniture('cabinet', x0, y0 + 3), Furniture('sanitizer', x0, y0 + 4), Furniture('trash_bin', x1, y1)]
    elif k == 'surgery':
        f += [Furniture('operating_table', cx, cy - 1, w=2, h=3), Furniture('monitor', cx - 2, cy - 1),
              Furniture('monitor', cx + 3, cy), Furniture('iv_stand', cx + 2, cy - 1), Furniture('tray', cx - 1, cy + 2),
              Furniture('ultrasound', x0, y0, w=2), Furniture('cabinet', x1, y0), Furniture('cabinet', x1 - 1, y0),
              Furniture('biohazard', x1, y1), Furniture('sanitizer', x0, y1)]
    elif k == 'disinfection':
        f += [Furniture('sink', x, y0, w=2) for x in range(x0, x1, 2)]
        f += [Furniture('cabinet', x0, y0 + 2), Furniture('biohazard', x1, y1), Furniture('sanitizer', x0, y1)]
    elif k == 'storage':
        f += [Furniture('shelf', x, y0, h=5) for x in range(x0 + 1, x1, 3)]
        f += [Furniture('fridge', x1, y0), Furniture('fridge', x1, y0 + 1), Furniture('biohazard', x0, y1)]
    elif k == 'rest':
        f += [Furniture('sofa', x0 + 1, y0, False, w=3), Furniture('sofa', x0 + 5, y0, False, w=3), Furniture('table', x0 + 3, y0 + 2),
              Furniture('coffee', x1, y0), Furniture('bed', x1 - 1, y0 + 2, h=2), Furniture('bed', x1, y0 + 2, h=2),
              Furniture('plant', x0, y1), Furniture('bedside', x1 - 2, y0 + 2), Furniture('armchair', x0, y0 + 3),
              Furniture('fridge', x1 - 1, y0), Furniture('clock', x0 + 4, y0), Furniture('tv', x0 + 2, y1)]
    elif k == 'corridor':
        f += [Furniture('plant', x, y) for x in range(x0 + 6, x1, 12) for y in (y0, y1)]
        f += [Furniture('sanitizer', x, y0) for x in range(x0 + 12, x1, 12)]
        f += [Furniture('bench', x, y1, False, w=3) for x in range(x0 + 1, x1 - 3, 24)]
    if r.flipped:
        for item in f:
            item.y = y0 + y1 - (item.y + item.h - 1)
    return f
