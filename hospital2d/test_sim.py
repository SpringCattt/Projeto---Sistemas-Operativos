# test_sim.py - The simulation with its real threads (no UI, no other processes), sped up 8x: python test_sim.py
# Takes about 1.5 minutes.
import contextlib
import queue
import time
from types import SimpleNamespace

import config

config.TIME_SCALE = 8

from arrivals import new_patient  # noqa: E402 (after the speed-up)
from sim import JOBS, Hospital  # noqa: E402
from world import Dispatcher, Line, Task, Theatres  # noqa: E402

PATIENT_ONLY_NOT = {'bar', 'control', 'storage', 'rest', 'disinfection'}  # rooms a patient never enters


@contextlib.contextmanager
def settings(**values):
    old = {k: getattr(config, k) for k in values}
    for k, v in values.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(config, k, v)


def info(urgency=0, surgery=False, temperament='passive'):
    return {'name': f'Test {temperament}', 'look': 0, 'reason': 'Test', 'urgency': urgency, 'surgery': surgery,
            'temperament': temperament}


def until(predicate, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def stop(h):
    h.shutdown()
    assert not h.errors, h.errors[0][1]
    assert not any(t.is_alive() for t in h.threads), 'a thread did not stop'


def test_dispatcher():
    """Closest and least tired wins; a busy actor is only interrupted for something clearly more urgent."""
    d = Dispatcher(clock=lambda: 0)

    def actor(i, x, fatigue=0):
        return SimpleNamespace(id=i, name=f'Dr {i}', role='doctor', tx=x, ty=0, fatigue=fatigue, resting=False, task=None,
                               inbox=queue.Queue())

    def consult(rank):
        return Task('consult', 'doctor', rank, patient=SimpleNamespace(name='P', rank=rank, patience=100, tx=0, ty=0))

    def assign(task):
        d.submit(task)
        with d.cond:
            d.assign()
        for a in d.staff:  # they pick it up straight away
            while not a.inbox.empty():
                a.inbox.get_nowait()

    near, far, tired = actor(1, 2), actor(2, 30), actor(3, 1, fatigue=100)
    d.staff = [near, far, tired]
    green, yellow, yellow2, red = consult(0), consult(1), consult(1), consult(2)
    assign(green)
    assert near.task is green  # 2 tiles beats 1 tile with 100 fatigue (+50)
    assign(yellow)
    assert far.task is yellow  # 30 tiles beats 1 tile with 100 fatigue (1 + 50): the tired one is spared
    tired.task = consult(1)
    assign(yellow2)
    assert yellow2 in d.open  # everyone busy, and Yellow is not 2 levels above anything
    assign(red)
    assert near.task is red  # Red pre-empts the Green consultation (gap 2), not the Yellow ones


def test_priority():
    """Who goes first: most urgent by default; with weight on impatience, whoever is about to give up."""
    d = Dispatcher(clock=lambda: 0)
    calm = Task('consult', 'doctor', 0, patient=SimpleNamespace(name='Calm', rank=1, patience=100, tx=0, ty=0))
    fed_up = Task('consult', 'doctor', 0, patient=SimpleNamespace(name='Fed up', rank=0, patience=5, tx=0, ty=0))
    assert d.priority(calm) > d.priority(fed_up)  # Yellow before Green
    d.policy = [10.0, 0.0, 30.0]
    assert d.priority(fed_up) > d.priority(calm)


def test_learning():
    """The dispatcher's guesses get better: here a task really takes 20 s plus 1 s per tile."""
    now = [0.0]
    d = Dispatcher(clock=lambda: now[0])
    a = SimpleNamespace(id=1, name='Dr 1', role='doctor', tx=0, ty=0, fatigue=0.0, resting=False, task=None,
                        inbox=queue.Queue())
    d.staff = [a]
    errors = []
    for i in range(300):
        a.tx = i % 40
        task = Task('consult', 'doctor', 0, tile=(0, 0))
        d.submit(task)
        with d.cond:
            d.assign()
        a.inbox.get_nowait()
        real = 20 + a.tx
        errors.append(abs(real - task.guess[2]))
        now[0] += real
        d.finished(a, task, completed=True)
    assert sum(errors[-20:]) / 20 < sum(errors[:20]) / 20 / 4, (errors[:5], errors[-5:])
    assert d.learned == 300


def test_line_and_theatres():
    line = Line()
    a, b = object(), object()
    line.join(a)
    line.join(b)
    assert line.place(b) == 1 and line.take(0.1) is a and line.place(b) == 0
    assert line.take(0.1) is b and line.take(0.05) is None
    th = Theatres(['A', 'B'])
    x, y = th.try_take(0.05), th.try_take(0.05)
    assert {x, y} == {'A', 'B'} and th.try_take(0.05) is None  # the semaphore is at 0
    th.give_back(x)
    assert th.try_take(0.05) == x


def test_flow_and_rules():
    """Patients arrive, register, get triaged and see doctors; nobody shares a tile, patients stay out of
    staff areas."""
    h = Hospital(seed=3, n_doctors=4, n_surgeries=2, n_patients=8)
    h.start()
    rng = h.rng
    end, last_arrival = time.time() + 25, 0.0
    try:
        while time.time() < end:
            if time.time() - last_arrival > 1.5:
                h.admit(new_patient(rng))
                last_arrival = time.time()
            with h.world.lock:  # tiles only change holding this lock, so this is a consistent picture
                with h.actors_lock:
                    actors = list(h.actors)
                tiles = [(a.tx, a.ty) for a in actors if a.role != 'patient' or not a.gone]
                assert len(set(tiles)) == len(tiles), 'two actors on one tile'
                for a in actors:
                    if a.role == 'patient':
                        room = h.lay.room_at[a.ty][a.tx]
                        assert (a.tx, a.ty) not in h.lay.staff_only, f'{a.name} behind a counter'
                        assert room is None or room.kind not in PATIENT_ONLY_NOT, f'{a.name} in {room.name}'
            time.sleep(0.01)
    finally:
        stop(h)
    events = ' '.join(text for _, text in h.events) + ' ' + h.summary()
    assert h.stats['treated'] >= 5, h.summary()
    assert h.stats['arrived'] >= 10


def test_preemption():
    """A doctor in a long Green consultation drops it for a Red patient; the Green one goes back to waiting."""
    with settings(CONSULT_S=[(400, 400), (400, 400), (8, 8)]):
        h = Hospital(seed=4, n_doctors=1, n_surgeries=0, n_patients=0)
        green = h.add_waiting(info(0))
        h.start()
        try:
            assert until(lambda: green.stage.startswith('going to') or 'sees' in str(list(h.events)), 30)
            assert until(lambda: any('sees' in t for _, t in h.events), 30), 'the Green consultation never started'
            red = h.add_waiting(info(2))
            assert until(lambda: h.stats['interruptions'] >= 1, 20), 'the doctor was not interrupted'
            assert until(lambda: h.waits.get(2), 30), 'the Red patient was not seen'
            assert until(lambda: green.stage in ('going to the waiting room', 'waiting for a doctor')
                         or green.stage.startswith('going to'), 10)
            assert not red.gone or h.stats['treated'] >= 1
        finally:
            stop(h)


def test_surgery():
    """A Red patient who needs surgery: the doctor takes a theatre, washes in its disinfection room and goes in
    from there (never straight from the corridor)."""
    h = Hospital(seed=5, n_doctors=1, n_surgeries=1, n_patients=0)
    h.add_waiting(info(2, surgery=True))
    doctor = h.dispatcher.staff[0]
    rooms = []
    h.start()
    try:
        end = time.time() + 60
        while time.time() < end and not h.stats['surgeries']:
            room = h.lay.room_at[doctor.ty][doctor.tx]
            if room is not None and (not rooms or rooms[-1] != room.kind):
                rooms.append(room.kind)
            time.sleep(0.005)
    finally:
        stop(h)
    assert h.stats['surgeries'] == 1, h.summary()
    assert 'surgery' in rooms, rooms
    for i, kind in enumerate(rooms):
        if kind == 'surgery':
            assert rooms[i - 1] == 'disinfection', f'entered surgery from {rooms[i - 1]}: {rooms}'


def test_stretcher():
    """A Red patient waits lying in Emergency; two nurses bring it on a stretcher to the doctor's exam bed."""
    h = Hospital(seed=7, n_doctors=1, n_surgeries=0, n_patients=0)
    p = h.add_waiting(info(2))
    rode, nurses = set(), [a for a in h.dispatcher.staff if a.role == 'nurse']
    h.start()
    try:
        end = time.time() + 60
        while time.time() < end and not h.stats['treated']:
            with h.world.lock:  # the three are always on tiles next to each other, and drawn together
                if p.on_stretcher:
                    room = h.lay.room_at[p.ty][p.tx]
                    rode.add(room.kind if room else 'door')
                if p.on_stretcher and not p.path:  # between two steps of the train
                    pushing = [n for n in nurses if n.task is not None and n.task.kind == 'stretcher']
                    assert len(pushing) == 2, 'two nurses push the stretcher'
                    for n in pushing:
                        assert abs(n.tx - p.tx) + abs(n.ty - p.ty) == 1, 'the stretcher came apart'
                        assert abs(n.px - p.px) + abs(n.py - p.py) <= 1.01, 'the stretcher came apart (drawn)'
            time.sleep(0.003)
    finally:
        stop(h)
    assert h.stats['treated'] == 1, h.summary()
    assert any('on a stretcher' in t for _, t in h.events) and 'corridor' in rode, rode


def test_ambulance():
    """Someone who needs surgery arrives by ambulance: through Emergency's own door, straight to a bed there."""
    h = Hospital(seed=10, n_doctors=1, n_surgeries=1, n_patients=0)
    p = h.admit(info(2, surgery=True))
    rooms = set()
    h.start()
    try:
        assert until(lambda: rooms.add(getattr(h.lay.room_at[p.ty][p.tx], 'kind', 'door')) or p.lying, 30)
    finally:
        stop(h)
    assert p.rank == 2 and rooms <= {'street', 'emergency', 'door'}, rooms


def test_on_call():
    """Work piles up: the Boss calls in doctors on call (rooms waiting for them); when it's quiet, it sends them home."""
    with settings(CALL_EVERY_S=10, SEND_HOME_AFTER_S=20, CONSULT_S=[(4, 4)] * 3):
        h = Hospital(seed=11, n_doctors=4, n_surgeries=0, n_patients=0, on_call=True)
        doctors = lambda: len(h.working(JOBS['Doctor']))
        assert doctors() == 1
        for _ in range(8):
            h.add_waiting(info(0))
        h.start()
        try:
            assert until(lambda: doctors() >= 3, 30), 'nobody was called in'
            assert any('calls in' in t for _, t in h.events)
            assert until(lambda: doctors() == 1, 90), 'the doctors on call never went home'
        finally:
            stop(h)


def test_admin():
    """The admin tablet's orders: pause stops the clock, speed changes it, staff come and go, patients appear."""
    h = Hospital(seed=8, n_doctors=2, n_surgeries=0, n_patients=0)
    h.start()
    try:
        h.command(('pause',))
        t0 = h.clock()
        time.sleep(0.3)
        assert h.clock() == t0 and h.speed == 0
        h.command(('pause',))
        h.command(('speed', -1))
        assert h.rate == 4 and h.speed == 4
        h.command(('patient', 2, False, 3, True))
        assert sum(1 for p in h.patients() if p.rank == 2) == 3
        doctors = len(h.working(JOBS['Doctor']))
        h.command(('fire', 'Doctor'))
        assert len(h.working(JOBS['Doctor'])) == doctors - 1
        h.command(('hire', 'Doctor'))
        h.command(('hire', 'Doctor'))  # no third room: refused
        assert len(h.working(JOBS['Doctor'])) == doctors
        h.command(('hire', 'Cleaner'))
        new = h.actors[-1]
        assert until(lambda: (new.tx, new.ty) == new.commute or new.doing != 'idle', 30), 'the new cleaner never came in'
        assert until(lambda: h.stats['treated'] >= 1, 40), h.summary()
    finally:
        stop(h)


def test_overtaking():
    """Someone running doesn't queue behind someone walking the same way: it goes round them."""
    h = Hospital(seed=9, n_doctors=1, n_surgeries=0, n_patients=0)  # not started: we move them by hand
    walker, runner = [a for a in h.dispatcher.staff if a.role == 'nurse'][:2]
    corridor = max((r for r in h.lay.rooms if r.name == 'Corridor'), key=lambda r: r.w)
    x0, y0, x1, y1 = corridor.interior
    y = (y0 + y1) // 2
    with h.world.lock:
        for a, x in ((runner, x0 + 1), (walker, x0 + 3)):
            del h.world.occupied[(a.tx, a.ty)]
            a.tx, a.ty, a.px, a.py = x, y, x + 0.5, y + 0.5
            h.world.occupied[(x, y)] = a
    walker.set_path(walker.route((x1, y)))
    runner.speed = config.RUN_SPEED[2]
    runner.set_path(runner.route((x1 - 2, y)))
    assert runner.path and len(set(runner.path) - {(x, y) for x in range(x0, x1 + 1)}) == 0  # straight behind it
    dt, t = 1 / 30, 0.0
    while runner.path and t < 30:
        walker.step(dt)
        runner.step(dt)
        t += dt
    assert not runner.path and t < 7, f'the runner took {t:.1f} s: it queued behind the walker'


def test_reactions():
    """Out of patience: passive leaves, medium complains at reception, aggressive starts a fight that security
    ends. Waiting patients get worse and throw up; a cleaner cleans it."""
    with settings(CONSULT_S=[(900, 900)] * 3, PATIENCE_LOSS=(25, 25), ESCALATE_AFTER_S=[8, 400],
                  VOMIT_CHANCE_PER_S=[0.0, 0.5, 0.0]):
        h = Hospital(seed=6, n_doctors=1, n_surgeries=0, n_patients=0)
        h.add_waiting(info(1, temperament='medium'))  # keeps the only doctor busy (900 s)
        for temper in ('passive', 'medium', 'aggressive'):
            h.add_waiting(info(0, temperament=temper))
        h.start()
        try:
            for key in ('gave_up', 'complaints', 'fights', 'got_worse', 'vomits'):
                assert until(lambda: h.stats[key] >= 1, 40), f'no {key}: {h.summary()}'
            assert until(lambda: any('cleaned' in t for _, t in h.events), 40), 'nobody cleaned the mess'
            assert until(lambda: any(w in t for _, t in h.events for w in ('steps in', 'thrown out')), 40)
        finally:
            stop(h)


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            t0 = time.time()
            test()
            print(f'{name}: ok ({time.time() - t0:.0f} s)')
