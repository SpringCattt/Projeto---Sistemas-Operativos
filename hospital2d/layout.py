# layout.py - Builds the hospital floor plan (rooms, doors and furniture) from config.py
import heapq
from dataclasses import dataclass

import config

EMPTY, WALL, FLOOR, DOOR = 0, 1, 2, 3

# Size (width, height) of each medical-wing room type, walls included
SIZES = {
    'doctor': (8, 8), 'surgery': (10, 9), 'disinfection': (6, 9),
    'storage': (14, 9), 'rest': (12, 9),
}
MARGIN = 3  # grass tiles around the building
SEATS = {'chair', 'office_chair', 'bench'}  # furniture people sit on (every tile of a bench is a seat)


@dataclass
class Room:
    kind: str
    name: str  # shown on screen
    x: int
    y: int
    w: int
    h: int
    walls: bool = True
    flipped: bool = False  # door on top instead of bottom (furniture is mirrored)

    @property
    def interior(self):  # (x0, y0, x1, y1) inclusive
        a = 1 if self.walls else 0
        return self.x + a, self.y + a, self.x + self.w - 1 - a, self.y + self.h - 1 - a

    def contains(self, x, y):
        x0, y0, x1, y1 = self.interior
        return x0 <= x <= x1 and y0 <= y <= y1


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
        for r in rooms:
            for y in range(r.y, r.y + r.h):
                for x in range(r.x, r.x + r.w):
                    if r.contains(x, y):
                        self.tiles[y][x], self.room_at[y][x] = FLOOR, r
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
                          if all(r.contains(*t) and t not in near_door for t in f.tiles())]
        self.solid = {t for f in self.furniture if f.solid for t in f.tiles()}

        self.seats = {t for f in self.furniture if f.kind in SEATS for t in f.tiles()}

        # Behind a counter (reception, bar) is staff only: patients never walk there
        self.staff_only = set()
        for r in rooms:
            counter_rows = [f.y for f in self.furniture if f.kind == 'counter' and r.contains(f.x, f.y)]
            if counter_rows:
                x0, y0, x1, _ = r.interior
                self.staff_only |= {(x, y) for y in range(y0, min(counter_rows)) for x in range(x0, x1 + 1)}

    def walkable(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height \
            and self.tiles[y][x] in (FLOOR, DOOR) and (x, y) not in self.solid

    def rooms_of(self, kind):
        return [r for r in self.rooms if r.kind == kind]

    def free_tiles(self, room):
        x0, y0, x1, y1 = room.interior
        return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if self.walkable(x, y)]

    def path(self, start, goal, avoid=(), blocked=frozenset()):
        """4-direction A*. Returns the tiles up to goal (start excluded), or [] if unreachable.
        avoid: room kinds the walker may not step into (e.g. patients and the corridor).
        blocked: tiles it may not step on (staff-only areas, other people)."""
        def ok(x, y):
            if not self.walkable(x, y) or (x, y) in blocked:
                return False
            room = self.room_at[y][x]  # None on door tiles
            return room is None or room.kind not in avoid

        if not ok(*goal):
            return []
        open_, came_from, cost = [(0, start)], {start: None}, {start: 0}
        while open_:
            _, cur = heapq.heappop(open_)
            if cur == goal:
                break
            x, y = cur
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if ok(*nb) and nb not in cost:
                    cost[nb] = cost[cur] + 1
                    came_from[nb] = cur
                    heapq.heappush(open_, (cost[nb] + abs(nb[0] - goal[0]) + abs(nb[1] - goal[1]), nb))
        if goal not in came_from:
            return []
        route = []
        while goal != start:
            route.append(goal)
            goal = came_from[goal]
        return route[::-1]


def door_top(r):
    return r.x + r.w // 2 - 1, r.y, 2, 'h'


def door_bottom(r):
    return r.x + r.w // 2 - 1, r.y + r.h - 1, 2, 'h'


def door_between(a, b):
    """Door in the shared vertical wall (b is to the right of a)."""
    y0, y1 = max(a.y, b.y) + 1, min(a.y + a.h, b.y + b.h) - 2
    return b.x, (y0 + y1) // 2, 2, 'v'


def surgery_block(n):
    # surgery | disinfection | surgery: each disinfection room serves the surgeries beside it
    block, s, d = [], 0, 0
    for i in range(0, n, 2):
        d += 1
        if i + 1 < n:
            block += [('surgery', f'Surgery {s + 1}'), ('disinfection', f'Disinfection {d}'), ('surgery', f'Surgery {s + 2}')]
            s += 2
        else:
            block += [('disinfection', f'Disinfection {d}'), ('surgery', f'Surgery {s + 1}')]
            s += 1
    return block


def build_layout(n_doctors=config.MAX_DOCTORS, n_surgeries=config.N_SURGERIES):
    # The main corridor spans y 0..4. Top-row rooms rest on it from above and bottom-row rooms
    # hang from it. Each new room goes to the shorter row.
    # shortcut: one floor and one corridor; when floors/wings arrive, a building becomes a list of these.
    # Base block: street -> reception -> triage -> waiting room. Bar above reception; one small triage
    # room under each waiting room, opening onto the patients' side of reception (never behind the counter).
    w1 = Room('waiting', 'Waiting Room 1', 0, 4, 20, 14)
    t1 = Room('triage', 'Triage 1', 9, 17, 11, 6)
    bar = Room('bar', 'Bar / Cafe', 19, 4, 16, 8)
    rec = Room('reception', 'Entrance and Reception', 19, 11, 16, 12)
    t2 = Room('triage', 'Triage 2', 34, 17, 11, 6)
    w2 = Room('waiting', 'Waiting Room 2', 34, 4, 20, 14)
    street = Room('street', 'Street', 15, 23, 24, 4, walls=False)
    rooms = [w1, t1, bar, rec, t2, w2, street]
    doors = [door_top(w1), door_top(bar), door_top(w2),  # to the corridor
             door_between(t1, rec), door_between(rec, t2),  # reception -> triage
             door_top(t1), door_top(t2),  # triage -> waiting room
             (rec.x, rec.y + 4, 2, 'v'), (rec.x + rec.w - 1, rec.y + 4, 2, 'v'),  # waiting room -> reception (way out)
             (rec.x + rec.w // 2 - 1, rec.y + rec.h - 1, 3, 'h')]  # big main entrance
    end = {'top': 0, 'bottom': w2.x + w2.w - 1}  # x of the right wall of each row's last room

    blocks = [surgery_block(n_surgeries), [('storage', 'Storage')], [('rest', 'Rest')]]
    blocks += [[('doctor', f'Doctor\'s Room {i + 1}')] for i in range(n_doctors)]
    for block in blocks:
        row = min(end, key=end.get)
        prev = None
        for kind, name in block:
            w, h = SIZES[kind]
            r = Room(kind, name, end[row], 1 - h if row == 'top' else 4, w, h, flipped=row == 'bottom')
            rooms.append(r)
            end[row] = r.x + w - 1
            # Surgery has a corridor door (patients); doctors come in through disinfection
            doors.append(door_top(r) if r.flipped else door_bottom(r))
            if prev and 'disinfection' in (prev.kind, r.kind):
                doors.append(door_between(prev, r))
            prev = r
    rooms.append(Room('corridor', 'Corridor', 0, 0, max(end.values()) + 1, 5))

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
              Furniture('soda', x0 + 6, y0), Furniture('cake', x0 + 5, y0 + 1), Furniture('donuts', x0 + 8, y0 + 1),
              Furniture('plant', x0, y1), Furniture('plant', x1, y1), Furniture('trash_bin', x1, y0 + 2)]
        for x in range(x0 + 2, x1 - 1, 4):
            f += [Furniture('table', x, y1 - 1), Furniture('chair', x - 1, y1 - 1, False), Furniture('chair', x + 1, y1 - 1, False)]
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
        f += [Furniture('sofa', x0 + 1, y0, w=3), Furniture('sofa', x0 + 5, y0, w=3), Furniture('table', x0 + 3, y0 + 2),
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
