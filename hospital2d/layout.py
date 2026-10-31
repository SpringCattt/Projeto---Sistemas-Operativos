# layout.py - The hospital's plan (floors, rooms, doors, furniture, stairs, elevator), read from the Tiled maps in maps/
import functools
import heapq
import glob
import json
import os
import re
from collections import deque
from dataclasses import dataclass

import config

EMPTY, WALL, FLOOR, DOOR = 0, 1, 2, 3

MAPS = os.path.join(os.path.dirname(__file__), 'maps')  # the floors, drawn in the Tiled map editor (see README)
# The city's roads (outside floor 0): a lane each way, each lane 2 tiles wide and one way.
# Cars drive on 2 x 2 blocks of tiles (x and y even); the top-left tile of a block says which way: n, s, e, w, or x
# (a crossing: any way). The parking lot's lanes ('lot_') are roads too; people walk over them only on a crossing
DRIVE = {f'road_{way}_{part}': way for way in 'nsew' for part in ('edge', 'mid')}
DRIVE.update({'road_x': 'x', 'lot_e': 'e', 'lot_n': 'n', 'lot_s': 's', 'lot_x': 'x', 'lot_cross': 'e'})
STEP = {'n': (0, -2), 's': (0, 2), 'e': (2, 0), 'w': (-2, 0)}  # (from one block to the next)
GROUND = {'wall': WALL, 'door': DOOR, 'floor_grass': EMPTY,  # what a ground tile is; any other one is floor
          'road_l': EMPTY, 'road_r': EMPTY}  # (the ambulance's road: nobody walks on it)
GROUND.update({name: EMPTY for name in DRIVE if name != 'lot_cross'})  # (people cross the lot's lane only at
GROUND.update({name: EMPTY for name in ('lot_bay_l', 'lot_bay', 'lot_bay_r')})  # its zebra crossings; bays: cars')
STAIRS = 'stairs'  # a stairs tile goes to the stairs tile at the same place on the floor above and below
FLOOR_GAP = 4  # the floors lie side by side in one grid (grass between them: nobody walks across)
MARKERS = {'parking', 'dropoff'}  # where cars park (2 x 4 tiles; 'dropoff': taxis, and cars taking someone home)
# Hung on the wall: on a room's top row they're drawn up on the wall above it, and don't take up the floor below
WALL_MOUNTED = {'speaker', 'big_tv', 'call_screen', 'painting', 'painting_2', 'notice_board', 'first_aid',
                'xray_viewer', 'fire_extinguisher', 'clock', 'poster', 'eye_chart'}
SEATS = {'chair', 'chair_up', 'office_chair', 'bench', 'sofa'}  # furniture people sit on (every tile of a bench)
# Just for looks: left out wherever it would leave a way through only one tile wide (see Layout)
DECOR = {'plant', 'sanitizer', 'trash_bin', 'cabinet', 'biohazard', 'iv_stand', 'bp_monitor', 'eye_chart', 'poster',
         'clock', 'tv', 'vending', 'soda', 'snacks', 'fridge', 'bedside', 'armchair', 'coffee', 'map_stand',
         'exit_sign', 'ultrasound', 'monitor', 'tray', 'water_cooler', 'magazine_rack', 'fire_extinguisher', 'painting',
         'painting_2', 'notice_board', 'xray_viewer', 'big_plant', 'big_plant_2', 'coat_rack', 'first_aid',
         'sofa_beige', 'sofa_corner', 'sofa_grey', 'chaise', 'bookcase', 'grandfather_clock', 'wardrobe', 'dresser',
         'sideboard', 'pc_desk', 'desk_white', 'workbench', 'folding_screen',
         'console_curved', 'lab_desk', 'holo_table', 'monitor_wall', 'sci_device', 'scan_bed', 'robot_desk',
         'sleep_pod', 'command_desk', 'command_desk_2', 'energy_core', 'gaming_chair', 'tv_back'}
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


def load_layout(folder=MAPS):
    """Every floor<N>.tmj in `folder` (floor0 = ground floor), side by side in one grid: floor N starts at
    x = N * stride. Tiles, rooms and routes work the same on every floor; the stairs and the elevator join them."""
    paths = sorted(glob.glob(os.path.join(folder, 'floor*.tmj')), key=lambda p: int(re.findall(r'\d+', p)[-1]))
    floors = [read_floor(p) for p in paths]
    stride = max(len(f[0][0]) for f in floors) + FLOOR_GAP
    height = max(len(f[0]) for f in floors)
    ground = [['custom/floor_grass'] * stride * len(floors) for _ in range(height)]
    rooms, furniture, staff_only, stretchers, scenery = [], [], set(), [], []
    for n, (g, rs, fs, so, st, sc) in enumerate(floors):
        dx = n * stride
        for y, row in enumerate(g):
            ground[y][dx:dx + len(row)] = row
        for r in rs:
            r.x += dx
        for f in fs:
            f.x += dx
        rooms += rs
        furniture += fs
        staff_only |= {(x + dx, y) for x, y in so}
        stretchers += [(x + dx, y) for x, y in st]
        scenery += [(name, x + dx * config.TILE, y) for name, x, y in sc]
    return Layout(ground, rooms, furniture, staff_only, stretchers, stride, scenery)


def read_floor(path):
    """One floor drawn in Tiled. Three layers: 'ground' (what each tile looks like, and so what it is: wall, door,
    grass = outside, anything else = floor), 'rooms' (a rectangle over each room, walls included: its name, its
    kind as the class, an ellipse for a round one; property partner = the room it works with, walls = false for
    the street; and 'staff_only' rectangles: patients never walk there) and 'furniture' (pieces from the hospital
    tileset; their size on the floor is the tile's w and h, or the piece's own; 'stretcher' pieces are where the
    stretchers park; an 'elevator' opens downwards, at the same place on every floor it stops at; 'parking' and
    'dropoff' pieces are bays of the parking lot, in front of a lane). And, if there is one, 'scenery': pieces that
    are only drawn, anywhere, never in anyone's way (the city: buildings, lamps, trees...)."""
    T = config.TILE
    with open(path, encoding='utf-8') as f:
        m = json.load(f)
    (ts,) = m['tilesets']
    with open(os.path.join(os.path.dirname(path), ts['source']), encoding='utf-8') as f:
        tiles = {ts['firstgid'] + t['id']: t for t in json.load(f)['tiles']}
    props = lambda o: {p['name']: p['value'] for p in o.get('properties', [])}
    layers = {layer['name']: layer for layer in m['layers']}
    missing = {'ground', 'rooms', 'furniture'} - layers.keys()
    if missing:
        raise ValueError(f'{path}: no layer called {", ".join(sorted(missing))}')
    w, h = m['width'], m['height']
    # (Tiled keeps flips in the top bits of a tile number; the image path is from the tileset: ../assets/X.png)
    names = [tiles[g & 0x0FFFFFFF]['image'].split('assets/', 1)[1][:-4] if g else 'custom/floor_grass'
             for g in layers['ground']['data']]
    ground = [names[y * w:(y + 1) * w] for y in range(h)]

    rooms, staff_only, by_name = [], set(), {}
    for o in layers['rooms']['objects']:
        kind = o.get('type') or o.get('class') or ''
        x, y, rw, rh = (round(o[k] / T) for k in ('x', 'y', 'width', 'height'))
        if kind == 'staff_only':
            staff_only |= {(i, j) for j in range(y, y + rh) for i in range(x, x + rw)}
            continue
        if not kind:
            raise ValueError(f'{path}: room "{o["name"]}" has no class (its kind: waiting, doctor, storage...)')
        r = Room(kind, o['name'], x, y, rw, rh, props(o).get('walls', True), round=o.get('ellipse', False))
        rooms.append(r)
        by_name[r.name] = (r, props(o).get('partner'))
    for r, partner in by_name.values():
        if partner:
            if partner not in by_name:
                raise ValueError(f'{path}: room "{r.name}" works with "{partner}", and there is no such room')
            r.partner = by_name[partner][0]

    furniture, stretchers = [], []
    for o in layers['furniture']['objects']:
        p = {**props(tiles.get(o.get('gid', 0) & 0x0FFFFFFF, {})), **props(o)}  # (the piece's own size wins)
        if 'kind' not in p:
            raise ValueError(f'{path}: object {o["id"]} on the furniture layer is not a piece from the hospital tileset')
        fw, fh = p.get('w', 1), p.get('h', 1)
        x, y = round(o['x'] / T), round(o['y'] / T) - fh  # (x, y in Tiled: the bottom-left corner)
        if p['kind'] == 'stretcher':
            stretchers.append((x, y))
        else:
            furniture.append(Furniture(p['kind'], x, y, p['kind'] not in SEATS | WALL_MOUNTED | MARKERS, fw, fh))
    image_of = lambda o: tiles[o['gid'] & 0x0FFFFFFF]
    scenery = [(image_of(o)['image'].split('assets/', 1)[1][:-4], round(o['x']), round(o['y']) - image_of(o)['imageheight'])
               for o in layers['scenery']['objects']] if 'scenery' in layers else []  # (image, x, y of its top-left)
    return ground, rooms, furniture, staff_only, stretchers, scenery


class Layout:
    def __init__(self, ground, rooms, furniture, staff_only, stretchers, stride=None, scenery=()):
        """ground: the image of each tile ([y][x], from the assets folder), which also says what it is (GROUND).
        stride: how far apart the floors are in x (load_layout). The rest as read_floor reads it from the maps."""
        self.ground, self.rooms, self.width, self.height = ground, rooms, len(ground[0]), len(ground)
        self.scenery = scenery
        self.stride = stride or self.width
        self.floors = -(-self.width // self.stride)
        # Stairs: each stairs tile leads to the one at the same place on the floor above / below, if there is one
        stairs = {(x, y) for y, row in enumerate(ground) for x, name in enumerate(row) if name.endswith('/' + STAIRS)}
        self.links = {t: [u for u in ((t[0] - self.stride, t[1]), (t[0] + self.stride, t[1])) if u in stairs]
                      for t in stairs}
        # cached per plan (a cache on the method would keep every plan ever built alive: learn.py builds hundreds)
        self.distances_from = functools.lru_cache(maxsize=512)(self._distances_from)
        self.tiles = [[GROUND.get(name.rsplit('/', 1)[-1], FLOOR) for name in row] for row in ground]
        inside = lambda x, y: 0 <= x < self.width and 0 <= y < self.height
        self.room_at = [[None] * self.width for _ in range(self.height)]
        for r in sorted(rooms, key=lambda r: r.round):  # round rooms last: they sit inside another room
            for y in range(r.y, r.y + r.h):
                for x in range(r.x, r.x + r.w):
                    if not inside(x, y):
                        continue
                    if r.contains(x, y):
                        self.room_at[y][x] = r
                    elif r.round and any(r.contains(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                        self.room_at[y][x] = None  # its wall: the tiles touching the circle from outside
        self.doors = {(x, y) for y in range(self.height) for x in range(self.width) if self.tiles[y][x] == DOOR}

        # Furniture: all of a piece in one room, and nothing may touch a door, or it would block the way
        near_door = {(x + dx, y + dy) for x, y in self.doors for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
        def placed(f):
            rooms_under = {self.room_at[y][x] if inside(x, y) else None for x, y in f.tiles()}
            return len(rooms_under) == 1 and None not in rooms_under and not near_door & set(f.tiles())
        self.furniture = [f for f in furniture if placed(f)]
        self.solid = {t for f in self.furniture if f.solid for t in f.tiles()}
        while True:  # one at a time: once one goes, the one across the way may not squeeze anything any more
            squeezing = next((f for f in self.furniture if f.kind in DECOR and f.solid and self.squeezes(f)), None)
            if squeezing is None:
                break
            self.furniture.remove(squeezing)
            self.solid = {t for f in self.furniture if f.solid for t in f.tiles()}

        self.seats = {t for f in self.furniture if f.kind in SEATS for t in f.tiles()}
        # which way whoever sits there faces: down, except on a chair turned the other way (towards a nurse, a
        # doctor, the waiting room's TV)
        self.seat_facing = {t: 'up' for f in self.furniture if f.kind in ('chair_up', 'bench') for t in f.tiles()}
        tables = {t for f in self.furniture if f.kind in ('desk', 'counter', 'boss_desk') for t in f.tiles()}
        for x, y in self.seats:  # at a desk or counter: towards it (rooms drawn upside down have it above)
            if (x, y - 1) in tables or (x, y + 1) in tables:
                self.seat_facing[(x, y)] = 'up' if (x, y - 1) in tables else 'down'

        self.staff_only = staff_only  # behind the counters (reception, bar): patients never walk there
        self._find_spots(near_door, stretchers)

    def squeezes(self, f):
        """Does `f` leave a floor tile next to it with something solid (or a wall) right across?"""
        mine = f.tiles()
        for x, y in mine:
            for dx, dy in DIRECTIONS:
                n, across = (x + dx, y + dy), (x + 2 * dx, y + 2 * dy)
                if n not in mine and self.walkable(*n) and n not in self.doors and not self.walkable(*across):
                    return True
        return False

    def _find_spots(self, near_door, stretchers):
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
        # Ways out: the far side of the street in front, and the exits at both ends of the corridor
        self.exits = self.street_edge + [t for r in self.rooms if r.name == 'Exit' for t in self.free_tiles(r)]
        self.ambulance_bay = [t for r in self.rooms if r.name == 'Ambulance Bay' for t in self.free_tiles(r)]

        self.triage_desks = [(r, (f.x, f.y), (f.x + 1, f.y + 2))  # (room, nurse seat, patient chair)
                             for r in self.rooms_of('triage') for f in furniture_in(r, 'office_chair')]
        self.doctor_desks = {}  # room -> (doctor seat, patient chair)
        for r in self.rooms_of('doctor'):
            seats, chairs = furniture_in(r, 'office_chair'), furniture_in(r, 'chair') + furniture_in(r, 'chair_up')
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
        def bed_spot(f):  # (tile beside a bed or table, where someone lying on it is drawn)
            side = next((t for x in (f.x + f.w, f.x - 1) for t in ((x, f.y + f.h - 1), (x, f.y))
                         if self.walkable(*t)), None)
            return side, (f.x + f.w / 2, f.y + f.h / 2)
        self.exam_spots = {r: bed_spot(furniture_in(r, 'exam_bed')[0]) for r in self.rooms_of('doctor')
                           if furniture_in(r, 'exam_bed')}
        self.table_spots = {r: bed_spot(furniture_in(r, 'operating_table')[0])[1] for r in self.rooms_of('surgery')}
        self.emergency_beds = {r: [b for b in map(bed_spot, furniture_in(r, 'bed')) if b[0]]
                               for r in self.rooms_of('emergency')}
        # Stretchers park where the map has them; the nurses meet anywhere else in storage, but not right next to
        # them either: that's the lane to push them in and out
        self.stretcher_bay = [t for t in stretchers if self.walkable(*t)]
        self.stretcher_lane = {(x + dx, y + dy) for x, y in stretchers for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        self.stretcher_spots = [t for r in self.rooms_of('storage') for t in self.free_tiles(r)
                                if t not in self.stretcher_lane]
        # The wheelchairs (cured Red patients leave in one) are kept in the last of those
        n = config.WHEELCHAIRS
        self.wheelchair_spots, self.stretcher_spots = self.stretcher_spots[-n:], self.stretcher_spots[:-n]
        # Elevators: the same box at the same place on each floor it serves. It opens downwards, onto a column of
        # three tiles where a stretcher train (nurse, stretcher, nurse) waits to get in and comes out
        shafts = {}
        for f in self.furniture:
            if f.kind == 'elevator':
                shafts.setdefault((f.x % self.stride, f.y), {})[self.floor_of(f.x)] = (
                    [(f.x, f.y + f.h + i) for i in range(3)], (f.x + f.w / 2, f.y + f.h / 2), f)
        self.lifts = list(shafts.values())  # each one: {floor: (the column in front of it, the middle of its box,
        #                                                       the piece)}
        # Where ambulances park (the 'ambulance' piece: they come down the road from the top edge of the map), and the
        # three tiles along its right side where the paramedics take the patient out (the first one: the front)
        self.ambulance_spot = next((f for f in self.furniture if f.kind == 'ambulance'), None)
        s = self.ambulance_spot
        self.unload_column = [(s.x + s.w, s.y + s.h - 1 - i) for i in range(3)] if s else []
        self.waiting_seats = {r: [t for t in self.free_tiles(r) if t in self.seats] for r in self.rooms_of('waiting')}
        self.control_seat = next((f.x, f.y) for r in self.rooms_of('control') for f in furniture_in(r, 'office_chair'))
        self.rest_spots = [t for r in self.rooms_of('rest') for t in self.free_tiles(r) if t in self.seats]
        # The bar: where a bartender stands behind the counter, and the tile across it where a customer stands
        self.bar_spots = [((f.x, f.y + d), (f.x, f.y - d)) for r in self.rooms_of('bar') for f in furniture_in(r, 'counter')
                          for d in (-1, 1) if (f.x, f.y + d) in self.staff_only and self.walkable(f.x, f.y + d)
                          and self.walkable(f.x, f.y - d) and (f.x, f.y - d) not in self.staff_only]

        # The city's roads, by blocks (DRIVE); cars come in where a lane enters floor 0 from beyond its edge (the
        # rest of the city), and leave where one goes out
        self.drive = {(x, y): DRIVE[self.ground[y][x].rsplit('/', 1)[-1]] for y in range(0, self.height - 1, 2)
                      for x in range(0, self.width - 1, 2) if self.ground[y][x].rsplit('/', 1)[-1] in DRIVE}
        beyond = lambda x, y: not (0 <= x < self.stride - FLOOR_GAP and 0 <= y < self.height)
        self.city_in = [b for b, way in self.drive.items() if way in STEP and beyond(b[0] - STEP[way][0], b[1] - STEP[way][1])]
        self.city_out = [b for b, way in self.drive.items() if way in STEP and beyond(b[0] + STEP[way][0], b[1] + STEP[way][1])]
        # The parking lot's bays: where a car stops (the middle of the bay), the block of lane it drives in from, the
        # way its nose points once in, and the tile beside it where its people get out and back in
        self.bays = {kind: [] for kind in MARKERS}
        for f in self.furniture:
            lane = next(((f.x, y) for y in (f.y + f.h, f.y - 2) if (f.x, y) in self.drive), None) if f.kind in MARKERS else None
            if lane:
                below = lane[1] > f.y  # (the lane below the bay: it drives in upwards)
                self.bays[f.kind].append({'kind': f.kind, 'tiles': f.tiles(), 'lane': lane, 'stop': (f.x + f.w / 2, f.y + f.h / 2),
                                          'nose': 'up' if below else 'down',
                                          'door': (f.x, f.y - 1) if below else (f.x, f.y + f.h)})  # (the pavement at
                # its nose: people get out there, never onto the lane)

    def walkable(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height \
            and self.tiles[y][x] in (FLOOR, DOOR) and (x, y) not in self.solid

    def drive_path(self, start, goals):
        """The blocks a car drives through from block `start` to the nearest of `goals` (start excluded), each step
        the way both blocks let it go; [] if it can't get there."""
        goals, came, todo = set(goals), {start: None}, deque([start])
        while todo:
            b = todo.popleft()
            if b in goals:
                route = []
                while b != start:
                    route.append(b)
                    b = came[b]
                return route[::-1]
            for way, (dx, dy) in STEP.items():
                n = (b[0] + dx, b[1] + dy)
                if n not in came and self.drive[b] in (way, 'x') and self.drive.get(n) in (way, 'x'):
                    came[n] = b
                    todo.append(n)
        return []

    def floor_of(self, x):
        """Which floor a tile (or a position) is on, from its x."""
        return int(x) // self.stride

    def rooms_of(self, kind):
        return [r for r in self.rooms if r.kind == kind]

    def free_tiles(self, room):
        x0, y0, x1, y1 = room.interior
        return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
                if self.room_at[y][x] is room and self.walkable(x, y)]

    def walk_distance(self, a, b):
        """Tiles to walk from a to b, around walls and furniture (not people)."""
        d = self.distances_from(a).get(b)
        return d if d is not None else 10 * (abs(a[0] - b[0]) + abs(a[1] - b[1]))

    def _distances_from(self, start):
        """Walking distance from `start` to every tile: breadth-first search over the plan, cached per start."""
        dist, todo = {start: 0}, deque([start])
        while todo:
            x, y = todo.popleft()
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1), *self.links.get((x, y), ())):
                if nb not in dist and self.walkable(*nb):
                    dist[nb] = dist[(x, y)] + 1
                    todo.append(nb)
        return dist

    def guess(self, a, b):
        """A lower bound of the walk from a to b: on the same floor, straight lines; to another one, a step per
        floor on the stairs (which keep x, y). Never more than the real walk, so A* still finds the shortest."""
        s = self.stride
        return abs(a[0] % s - b[0] % s) + abs(a[1] - b[1]) + abs(a[0] // s - b[0] // s)

    def path(self, start, goal, avoid=(), blocked=frozenset(), limit=None, stairs=True):
        """4-direction A*: the shortest route, and of all the shortest ones the one with fewest turns, so people walk
        straight instead of zig-zagging. Returns the tiles up to goal (start excluded), or [] if unreachable.
        avoid: room kinds the walker may not step into (e.g. patients and the corridor).
        blocked: tiles it may not step on (staff-only areas, other people).
        limit: only routes up to this many tiles (gives up much sooner when there's no such route).
        stairs: may take the stairs to another floor (a stretcher can't: it takes the elevator)."""
        def ok(x, y):
            if not self.walkable(x, y) or (x, y) in blocked or (x, y) in self.seats and (x, y) != goal:
                return False  # (a seat only as where it's going: nobody walks over the chairs)
            room = self.room_at[y][x]  # None on door tiles
            return room is None or room.kind not in avoid

        if start == goal or not ok(*goal):
            return []
        # The state is (tile, direction it came from), so a turn can cost a little: TURN, far less than a step,
        # never makes a route longer, it only picks the straightest of the shortest ones
        open_ = [(self.guess(start, goal), 0, 0.0, start, -1)]
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
            if limit is not None and g + self.guess(cur, goal) > limit + 0.5:  # (turns cost a bit)
                return []  # everything left is longer than that
            steps = [((cur[0] + ddx, cur[1] + ddy), nd) for nd, (ddx, ddy) in enumerate(DIRECTIONS)]
            if stairs:  # up or down: as if it went straight on (no turn)
                steps += [(nb, d) for nb in self.links.get(cur, ())]
            for nb, nd in steps:
                if not ok(*nb):
                    continue
                ng = g + 1 + (TURN if d not in (-1, nd) else 0.0)
                if ng < best.get((nb, nd), float('inf')):
                    best[(nb, nd)] = ng
                    came[(nb, nd)] = (cur, d)
                    h = self.guess(nb, goal)
                    heapq.heappush(open_, (ng + h, h, ng, nb, nd))
        return []
