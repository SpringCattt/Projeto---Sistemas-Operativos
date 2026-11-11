# arrivals_flow.py - part of the Hospital (sim.py): Patients coming and going: by car, taxi or ambulance, the beds in Emergency, the parking bays, the wheelchair home.
import threading
from patients import Patient
from staff import Ambulance
from vehicles import Car
from world import Task, Transfer
from typing import Any


class PatientFlowMixin:
    """Patients coming and going: by car, taxi or ambulance, the beds in Emergency, the parking bays, the wheelchair home."""
    # The rest of the Hospital (sim.py) it uses: declared for the type checker only (no value: nothing at run time)
    _spawn: Any
    actors: Any
    actors_lock: Any
    bays_lock: Any
    bed_claims: Any
    clock: Any
    closed: Any
    count: Any
    dispatcher: Any
    free_bays: Any
    lay: Any
    log: Any
    patients: Any
    rng: Any
    running: Any
    stats_lock: Any
    theatres: Any
    waits: Any
    world: Any

    def admit(self, info) -> Any:
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
