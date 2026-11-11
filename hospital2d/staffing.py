# staffing.py - part of the Hospital (sim.py): The staff: hiring and sending home (the admin tablet, and the Boss calling in staff on call), how many of each.
from collections import Counter
import config
from staff import Cleaner, Doctor, Receptionist, Security, TriageNurse, WardNurse
from typing import TYPE_CHECKING, Any


JOBS: dict[str, Any] = {c.__name__: c for c in (Doctor, WardNurse, TriageNurse, Receptionist, Security, Cleaner)}  # (the admin tablet's names)


class StaffingMixin:
    """The staff: hiring and sending home (the admin tablet, and the Boss calling in staff on call), how many of each."""
    if TYPE_CHECKING:  # the rest of the Hospital (sim.py) is reached through self
        def __getattr__(self, name: str) -> Any: ...

    def working(self, job):
        with self.actors_lock:
            return [a for a in self.actors if type(a) is job and not a.quitting]

    def places(self, job):
        """Desks, rooms or seats a job needs one of each (None: no limit)."""
        lay = self.lay
        return {Doctor: lay.rooms_of('doctor'), Receptionist: lay.reception_desks,
                TriageNurse: lay.triage_desks}.get(job)

    def hire(self, job: Any, on_call=False):
        """A new member of staff walks in from the street to its place (on_call: the Boss called it in)."""
        lay, rng = self.lay, self.rng
        places = self.places(job)
        if places is not None:
            taken = {getattr(a, 'room', None) if job is Doctor else a.seat for a in self.working(job)}
            free = [p for p in places if (p if job is Doctor else p[0] if job is Receptionist else p[1]) not in taken]
            if not free:
                return self.log(f'Admin: no free place for another {job.__name__}')
            args = (free[0],) if job is Doctor else free[0]
            a = job(self, *args, rng=rng)
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
        return {name: (len(self.working(job)), None if (places := self.places(job)) is None else len(places))
                for name, job in JOBS.items()}
