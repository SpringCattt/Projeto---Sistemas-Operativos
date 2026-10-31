# vehicles.py - Cars from the city. Each one is an actor with its own thread: it comes in from where a road leaves the
# map (the rest of the city), drives along the lanes block by block (Layout.drive_path), parks in a bay of the
# parking lot, and goes back to the city.
import threading

import config
from actors import Actor
from layout import STEP

FACE = {'n': 'up', 's': 'down', 'e': 'right', 'w': 'left'}
BACK = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
BLOCK = lambda b: [(b[0] + i, b[1] + j) for i in (0, 1) for j in (0, 1)]  # a block's four tiles


class Car(Actor):
    """A patient's own car (info, with a free bay: it waits there until its patient comes back), a taxi (info, no
    bay free: it leaves its patient at a drop-off bay and goes), or a car that comes to take someone home (pickup:
    it waits at a drop-off bay until they're in). Bays are shared: Hospital.claim_bay / free_bay. On the way it
    takes up the block of road it's on, so cars queue instead of driving through each other, and whoever walks on
    the lot's lane is asked to step aside."""
    role = 'car'

    def __init__(self, h, rng, info=None, bay=None, pickup=None):
        start = rng.choice(h.lay.city_in)
        taxi = bay is None and pickup is None
        super().__init__(h, [start], rng, look=0 if taxi else 1 + rng.randrange(config.CAR_COLORS),
                         name='Taxi' if taxi else f'Car taking {pickup.name} home' if pickup else f"{info['name']}'s car")
        with self.world.lock:  # (it takes up blocks of road as it drives: drive)
            if self.world.occupied.get((self.tx, self.ty)) is self:
                del self.world.occupied[(self.tx, self.ty)]
        dx, dy = STEP[self.lay.drive[start]]
        self.block, self.held = start, []
        self.entering = None  # the crossing it's about to go into (the cars on its left give way to it)
        self.px, self.py = start[0] + 1 - 1.5 * dx, start[1] + 1 - 1.5 * dy  # just off the map, coming in
        self.facing = FACE[self.lay.drive[start]]
        self.info, self.bay, self.pickup = info, bay, pickup
        self.passenger = None  # the patient it brought
        self.back = threading.Event()  # whoever it waits for is in
        self.parked = threading.Event()
        self.bringing = info is not None  # (the day doesn't end while a car is still bringing someone)
        self.doing = 'coming from the city'

    def label(self):
        return self.name

    def give_way(self, blocked_tile=None):  # (a car doesn't step aside: whoever is in its way does)
        pass

    def life(self):
        lay = self.lay
        if self.bay is None:  # a taxi or a car taking someone home: a drop-off bay, once one is free (it waits in town)
            self.doing, self.hidden = 'waiting in town for a drop-off bay', True
            while self.bay is None:
                self.tick()
                # (one taking someone home may park in any bay: it has to wait there)
                self.bay = self.h.claim_bay('dropoff') or (self.pickup and self.h.claim_bay('parking'))
            self.hidden = False
        self.doing = 'coming from the city'
        route = [self.block] + lay.drive_path(self.block, [self.bay['lane']])
        self.drive(route[:-1])
        self.doing = 'parking'
        self.take(self.bay['tiles'], keep=self.held)  # the bay first, from a block back: whoever is in it gets out
        self.drive(route[-1:])  # by the lane in front (it would shut them in), then in front of it
        self.glide(self.bay['stop'], self.bay['nose'], config.CAR_SPEED / 3)
        self.release(keep=self.bay['tiles'])
        self.tx, self.ty = self.bay['tiles'][0]
        self.parked.set()
        if self.info is not None:  # out, onto the lane beside it once nobody's there
            doors = [self.bay['door'], (self.bay['door'][0] + 1, self.bay['door'][1])]
            self.wait_until(lambda: any(t not in self.world.occupied for t in doors))
            self.passenger = self.h.out_of_car(self.info, [t for t in doors if t not in self.world.occupied] or doors, self)
            self.bringing = False
        if self.passenger is not None and self.name == 'Taxi':
            self.passenger.car = None  # (a taxi doesn't wait: its passenger goes home on foot)
        else:
            self.doing = f'waiting for {(self.pickup or self.passenger).name}'
            self.wait_until(self.back.is_set)
        self.doing = 'leaving'
        lane = self.bay['lane']
        self.take(BLOCK(lane), keep=self.bay['tiles'])
        self.glide((lane[0] + 1, lane[1] + 1), self.facing, config.CAR_SPEED / 3)  # backs out
        self.release(keep=self.held[-4:])
        self.h.free_bay(self.bay)
        self.block, self.tx, self.ty = lane, lane[0], lane[1]
        self.drive(lay.drive_path(lane, lay.city_out))
        self.doing = 'back to the city'
        dx, dy = STEP[lay.drive[self.block]]
        self.release()
        self.glide((self.block[0] + 1 + 1.5 * dx, self.block[1] + 1 + 1.5 * dy))  # off the map

    def drive(self, blocks):
        """Along the blocks of road, each once it's free. A car holds the block it's on and the one it just left (it's
        longer than a block: that keeps the next one off its tail). A crossing it takes whole, its way across and the
        block after it, all at once or not at all: a car never waits inside one while holding part of it, so two
        cars crossing each other's way can't each wait for the other for ever."""
        i = 0
        while i < len(blocks):
            n = 1
            while self.lay.drive[blocks[i + n - 1]] == 'x' and i + n < len(blocks):
                n += 1
            run, before = blocks[i:i + n], self.block
            if self.lay.drive[run[0]] == 'x':  # a crossing: priority to the right (Portugal), then in
                self.entering, end = run[0], self.h.clock() + config.YIELD_MAX_S
                if self.car_on_right(run[0]):
                    was, self.doing = self.doing, 'giving way to the car on its right'
                    self.wait_until(lambda: not self.car_on_right(run[0]) or self.h.clock() > end)
                    self.doing = was
            self.take([t for b in run for t in BLOCK(b)], keep=self.held)
            for b in run:
                self.glide((b[0] + 1, b[1] + 1))
                self.block, self.tx, self.ty = b, b[0], b[1]
            self.release(keep=BLOCK(run[-1]) + BLOCK(run[-2] if n > 1 else before))
            self.entering = None
            i += n

    def car_on_right(self, crossing):
        """Another car about to go into the same crossing, coming from this one's right (it goes first)."""
        way = self.lay.drive[self.block]
        if way == 'x':
            return False
        dx, dy = STEP[way]
        right = (dy, -dx)  # (y grows downwards: going east, the one on its right comes up from the south)
        with self.h.actors_lock:
            cars = [a for a in self.h.actors if a is not self and getattr(a, 'entering', None) is not None]
        return any(self.lay.drive[c.block] != 'x' and STEP[self.lay.drive[c.block]] == right
                   and abs(c.entering[0] - crossing[0]) + abs(c.entering[1] - crossing[1]) <= 4 for c in cars)

    def take(self, tiles, keep=()):
        """Takes up `tiles` (as well as `keep`) once nobody else is on them: cars wait their turn, and wait for
        whoever walks there to pass (people go first)."""
        def free():
            with self.world.lock:
                there = [self.world.occupied[t] for t in tiles if self.world.occupied.get(t) not in (None, self)]
                if not there:
                    for t in tiles:
                        self.world.occupied[t] = self
                    return True
            return False
        self.wait_until(free)
        self.held = list(keep) + [t for t in tiles if t not in keep]

    def release(self, keep=()):
        """Leaves every tile it holds but `keep`."""
        with self.world.lock:
            for t in self.held:
                if t not in keep and self.world.occupied.get(t) is self:
                    del self.world.occupied[t]
        self.held = [t for t in self.held if t in keep]

    def glide(self, to, facing=None, speed=None):
        """Smoothly to `to` (in tiles), nose where it goes (or `facing`, when backing up or parking)."""
        dx, dy = to[0] - self.px, to[1] - self.py
        if dx or dy:
            self.facing = facing or (('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up'))
        while (self.px, self.py) != to:
            step = (speed or config.CAR_SPEED) * self.tick(config.ACTOR_HZ)
            dx, dy = to[0] - self.px, to[1] - self.py
            d = (dx * dx + dy * dy) ** 0.5
            self.px, self.py = to if d <= step else (self.px + dx / d * step, self.py + dy / d * step)
            self.anim_t += step
