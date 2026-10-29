# patients.py - A patient's whole visit, as one script running in its own thread:
#   street -> reception line -> registration -> wait for triage -> triage -> waiting room -> doctor
#   (-> surgery) -> street.  While it waits its patience runs down and its condition may get worse; when the
#   patience is gone it reacts by temperament: passive leaves, medium complains at reception, aggressive fights.
import queue

import config
from actors import Actor
from world import Task

BASE_ROOMS = frozenset({'street', 'reception', 'waiting'})  # where a patient may walk on its own
# When two calls arrive at once, the more important one wins and the other is declined
CALL_RANK = {'surgery': 4, 'exam': 4, 'consult': 3, 'triage': 2, 'register': 1, 'complaint': 1}


class Patient(Actor):
    role = 'patient'
    pace = (8, 20, 0.25)

    def __init__(self, h, candidates, rng, info, triaged=False):
        super().__init__(h, candidates, rng, name=info['name'], look=info['look'])
        self.reason, self.base_urgency = info['reason'], info['urgency']
        self.needs_surgery, self.temperament = info['surgery'], info['temperament']
        self.rank = self.base_urgency if triaged else None  # urgency once triaged: 0 Green, 1 Yellow, 2 Red
        self.patience, self.patience_loss = 100.0, self.rng.uniform(*config.PATIENCE_LOSS)
        self.stage, self.allowed = 'arriving', BASE_ROOMS
        self.calls = queue.Queue()  # Summons from staff
        self.gone = self.complaining = False
        self.waiting_room, self.waiting_since, self.triaged_at = None, None, None
        self.triaged = triaged
        self.provoke = None  # 'fight' / 'vomit': ordered from the admin tablet (done by its own thread)

    def label(self):
        return f'{self.name} ({config.URGENCY_NAMES[self.rank]})' if self.rank is not None else self.name

    def avoid(self):
        return self.h.room_kinds - self.allowed

    def no_go(self):
        return self.lay.staff_only  # never behind a counter

    def summon(self, summons):
        if summons.stretcher:
            self.h.fetch_stretcher(self, summons)
        self.calls.put(summons)

    # ---- the visit ------------------------------------------------------------------------------------------
    def life(self):
        if not self.triaged:
            self.queue_at_reception()
            self.wait_for_triage()
        while True:
            self.go_wait()
            call = self.wait_for_doctor()
            if call is None:  # gave up or was thrown out
                return self.leave()
            result = self.answer(call)
            while result in ('exam', 'surgery'):
                result = self.answer(self.next_call('waiting for surgery' if result == 'surgery' else 'waiting'))
            if result == 'done':
                return self.leave()
            # call cancelled, or sent back because the doctor had an emergency: back to waiting, same place in queue

    def queue_at_reception(self, complaint=False):
        """Gets in line and answers the receptionist. A complaint is cut short if a doctor calls meanwhile: then
        that call is returned."""
        self.complaining = complaint
        self.stage, self.allowed = ('in line to complain' if complaint else 'in line at reception'), BASE_ROOMS
        if complaint:
            self.say('?', 30)
        self.h.line.join(self)
        while True:
            call = self.poll()
            if call is not None and call.kind not in ('register', 'complaint'):
                self.h.line.leave(self)
                self.complaining, self.bubble = False, None
                return call
            if call is not None:
                break
            place = self.h.line.place(self)
            if place is not None:  # walk to its place (it moves up as the line advances)
                spots, outside = self.lay.line_spots, self.lay.street_line
                spot = spots[place] if place < len(spots) else outside[min(place - len(spots), len(outside) - 1)]
                if (self.tx, self.ty) != spot and not self.path:
                    self.set_path(self.route(spot))
            self.step(self.tick())
        result = self.answer(call)
        self.complaining, self.bubble = False, None
        if result != 'ok' and not complaint:  # the receptionist gave up on it: back in line
            return self.queue_at_reception()
        return None

    def wait_for_triage(self):
        self.stage, self.allowed = 'waiting for triage', BASE_ROOMS
        while True:
            call = self.poll()
            if call is not None and call.kind == 'triage' and self.answer(call) == 'ok':
                self.triaged, self.triaged_at = True, self.h.clock()
                return
            if not self.path and (self.tx, self.ty) not in self.lay.lobby:
                free = [t for t in self.lay.lobby if t not in self.world.occupied]
                if free:
                    self.set_path(self.route(self.rng.choice(free)))
            self.step(self.tick())

    def go_wait(self):
        """Into (or back to) its waiting room, on a free seat if there is one."""
        room = self.waiting_room or self.lay.rooms_of('waiting')[0]
        if room.kind == 'emergency':
            return self.go_to_bed(room)
        seats = self.lay.waiting_seats[room]
        self.area = seats * 3 + self.lay.free_tiles(room)  # mostly sitting, sometimes standing around
        self.stage, self.allowed = 'going to the waiting room', BASE_ROOMS | {'triage', 'corridor', 'doctor', 'emergency'}
        while True:
            free = [t for t in seats if t not in self.world.occupied] or \
                   [t for t in self.lay.free_tiles(room) if t not in self.world.occupied]
            if not free:
                self.tick()
            elif self.walk_to(min(free, key=lambda t: abs(t[0] - self.tx) + abs(t[1] - self.ty))):
                break
        self.allowed = BASE_ROOMS

    def go_to_bed(self, room):
        """Red: lies on a free bed in Emergency until the stretcher comes."""
        beds = self.lay.emergency_beds[room]
        self.area = []
        self.stage, self.allowed = 'going to Emergency', BASE_ROOMS | {'triage', 'corridor', 'emergency', 'doctor'}
        while True:
            free = [b for b in beds if b[0] not in self.world.occupied or self.world.occupied[b[0]] is self]
            if not free:  # Emergency is full: the waiting room, like everyone else
                self.waiting_room = self.lay.rooms_of('waiting')[0]
                return self.go_wait()
            if self.walk_to(free[0][0]):
                break
        self.allowed = BASE_ROOMS | {'emergency'}
        bed = next(b for b in beds if b[0] == (self.tx, self.ty))
        self.lie_on(bed[1])

    def wait_for_doctor(self):
        """Waits for a doctor's call while patience runs down. Returns the call, or None if it's leaving."""
        self.stage = 'waiting for a doctor'
        if self.waiting_since is None:
            self.waiting_since = self.h.clock()
        if self.triaged_at is None:
            self.triaged_at = self.h.clock()
        while True:
            call = self.poll()
            if call is not None:
                if call.kind in ('consult', 'surgery'):
                    return call
                call.cancel()
            self.waiting_upkeep(self.tick())
            if self.patience <= 0:
                reaction = self.out_of_patience()
                if reaction == 'left':
                    return None
                if reaction is not None:  # a doctor called while it was complaining
                    return reaction
                self.go_wait()
                self.stage = 'waiting for a doctor'

    def waiting_upkeep(self, dt):
        if self.provoke == 'vomit':
            self.provoke = None
            self.vomit()
        elif self.provoke == 'fight':
            self.provoke, self.temperament, self.patience = None, 'aggressive', 0.0
        self.wander(dt)
        self.patience = max(0.0, self.patience - self.patience_loss * dt)
        now = self.h.clock()
        if self.rank < 2 and now - self.waiting_since > config.ESCALATE_AFTER_S[self.rank]:
            self.rank += 1  # the consultation task reads this, so the dispatcher sees it straight away
            self.waiting_since = now
            self.h.count('got_worse')
            self.talk('worse')
            self.h.log(f'{self.name} is getting worse: now {config.URGENCY_NAMES[self.rank]}')
        if not self.path and self.rng.random() < config.VOMIT_CHANCE_PER_S[self.rank] * dt:
            self.vomit()

    def vomit(self):
        x, y = self.tx, self.ty
        for tile in ((x, y + 1), (x - 1, y), (x + 1, y), (x, y - 1)):
            if not self.lay.walkable(*tile) or tile in self.lay.seats:
                continue
            with self.world.lock:
                if tile in self.world.occupied or tile in self.world.messes:
                    continue
                self.world.messes[tile] = self.h.clock()
            self.say('~', 4)
            self.talk('vomit')
            for other in self.h.patients():  # the people around react
                if other is not self and abs(other.tx - x) + abs(other.ty - y) <= 4 and self.rng.random() < 0.6:
                    other.talk('eww', 2.5)
            self.patience = max(0.0, self.patience - 10)
            self.h.count('vomits')
            self.h.log(f'{self.label()} threw up')
            self.h.dispatcher.submit(Task('clean', 'cleaner', 1, tile=tile, created=self.h.clock()))
            return

    def out_of_patience(self):
        """'left', a doctor's call that came during a complaint, or None (calmed down, keeps waiting)."""
        self.get_up()
        if self.temperament == 'passive':
            self.talk('give_up')
            self.h.count('gave_up')
            self.h.log(f'{self.label()} gives up and leaves')
            return 'left'
        if self.temperament == 'medium':
            self.h.log(f'{self.label()} goes to complain at reception')
            return self.queue_at_reception(complaint=True)
        return self.fight()

    def fight(self):
        rivals = [a for a in self.h.patients() if a is not self and a.stage == 'waiting for a doctor'
                  and abs(a.tx - self.tx) + abs(a.ty - self.ty) <= 6]
        rival = min(rivals, key=lambda a: abs(a.tx - self.tx) + abs(a.ty - self.ty), default=None)
        self.stage = 'fighting'
        self.say('!', 60)
        self.talk('insult', 4)
        if rival is not None:
            rival.say('!', 6)
            rival.talk('victim', 3)
            rival.patience = max(0.0, rival.patience - 15)
        self.h.count('fights')
        self.h.log(f'{self.label()} starts a fight' + (f' with {rival.name}' if rival else ''))
        task = Task('fight', 'security', 2, tile=(self.tx, self.ty), created=self.h.clock())
        task.target = self
        self.h.dispatcher.submit(task)
        if not self.wait_until(task.done.is_set, timeout=40):  # nobody came: it calms down by itself
            self.h.dispatcher.cancel(task)
        self.bubble = None
        if task.result == 'expelled':
            self.h.count('expelled')
            self.h.log(f'{self.label()} is thrown out by security')
            return 'left'
        self.patience = 60.0
        self.stage = 'waiting for a doctor'
        return None

    def leave(self):
        self.gone = True
        self.get_up()
        if self.task is not None:
            self.h.dispatcher.cancel(self.task)
        self.h.line.leave(self)
        self.stage, self.allowed = 'leaving', BASE_ROOMS | {'corridor', 'doctor', 'surgery'}
        self.walk_out()  # corridor exits for whoever saw a doctor
        # the thread ends here; the hospital removes it

    # ---- calls from staff -----------------------------------------------------------------------------------
    def poll(self):
        """The most important call waiting for it (any others are declined), or None."""
        calls = []
        while True:
            try:
                calls.append(self.calls.get_nowait())
            except queue.Empty:
                break
        calls = [c for c in calls if not c.cancelled]
        if not calls:
            return None
        best = max(calls, key=lambda c: CALL_RANK[c.kind])
        for c in calls:
            if c is not best:
                c.cancel()
        return best

    def next_call(self, stage):
        self.stage = stage
        while True:
            call = self.poll()
            if call is not None:
                return call
            self.tick()

    def answer(self, call):
        """Goes where staff called it, says it's there and waits until they're done. Returns their result, or
        None if the call was cancelled."""
        here = self.lay.room_at[self.ty][self.tx]
        self.stage, self.allowed = call.stage, BASE_ROOMS | call.allow | ({here.kind} if here else set())
        try:
            return self._answer(call)
        finally:
            self.get_up()
            self.on_stretcher, self.speed = False, 1.0

    def _answer(self, call):
        if call.stretcher:
            t = call.transfer
            self.stage = 'waiting for the stretcher'
            if not self.wait_until(lambda: t.at_patient.is_set() or t.aborted()) or t.aborted():
                return None
            self.lying = True  # it stays lying: the nurses slide it from the bed onto the stretcher (on_stretcher)
            t.mounted.set()
            self.stage = f'on a stretcher, {call.stage}'
            self.allowed |= {'corridor', 'doctor', 'surgery', 'emergency'}
            while not t.arrived.is_set():  # the nurses move it (and its tile) until it's there
                self.tick()
            if t.failed or call.cancelled:
                return None
        while (self.tx, self.ty) != call.tile:
            if call.cancelled:
                return None
            if not self.path:
                self.set_path(self.route(call.tile) or self.route(call.tile, around_people=True))
            self.step(self.tick())
        self.settle()
        self.on_stretcher = False
        if call.lie:
            self.lie_on(call.lie)
        call.arrived.set()
        while not call.done.is_set():
            if call.cancelled:
                return None
            self.tick()
        return call.result

    # ---- what the UI sees -----------------------------------------------------------------------------------
    def card(self):
        urgency = config.URGENCY_NAMES[self.rank] if self.rank is not None else 'not triaged yet'
        return [('Stage', self.stage), ('Reason', self.reason), ('Urgency', urgency),
                ('Patience', f'{self.patience:.0f}/100'), ('Temper', self.temperament)]

    def snapshot(self):
        s = super().snapshot()
        s['mark'] = self.rank
        s['card'] = s['card'][1:]  # 'Doing' says nothing for a patient: 'Stage' does
        return s
