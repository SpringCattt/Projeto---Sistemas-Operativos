# test_sim.py - The simulation with its real threads (no UI, no other processes), sped up 8x: python test_sim.py
# Takes about 1.5 minutes.
import contextlib
import multiprocessing as mp
import queue
import threading
import time
from types import SimpleNamespace

import config

config.TIME_SCALE = 8

from arrivals import new_patient, run_arrivals  # noqa: E402 (after the speed-up)
from sim import JOBS, Hospital, new_positions, read_positions, write_positions  # noqa: E402
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


def test_team():
    """The two nurses of a stretcher are handed out together or not at all (no one waiting for a partner who's busy)."""
    d = Dispatcher(clock=lambda: 0)
    nurse = lambda i: SimpleNamespace(id=i, name=f'N{i}', role='nurse', tx=i, ty=0, fatigue=0.0, resting=False,
                                      task=None, inbox=queue.Queue())
    d.staff = [nurse(1)]
    team = [Task('stretcher', 'nurse', 2, tile=(0, 0)) for _ in range(2)]
    for t in team:
        t.team = team
    d.submit(*team)
    with d.cond:
        assert d.assign() == [] and len(d.open) == 2  # one nurse free: nobody goes
    d.staff.append(nurse(2))
    with d.cond:
        given = d.assign()
    assert len(given) == 2 and {a.id for _, a in given} == {1, 2}


def test_priority():
    """The most urgent colour always first, however impatient the others; within a colour, the learned weights."""
    d = Dispatcher(clock=lambda: 0)
    doctor = SimpleNamespace(id=1, name='Dr', role='doctor', tx=0, ty=0, fatigue=0.0, resting=False, task=None,
                             inbox=queue.Queue())
    d.staff = [doctor]
    patient = lambda rank, patience: SimpleNamespace(name=f'{rank}/{patience}', rank=rank, patience=patience, tx=0, ty=0)
    green_fed_up, yellow_calm, yellow_fed_up = (Task('consult', 'doctor', 0, patient=patient(r, p))
                                                for r, p in ((0, 1), (1, 100), (1, 5)))
    d.policy = [0.0, 30.0]
    d.submit(green_fed_up, yellow_calm, yellow_fed_up)
    with d.cond:
        (task, _), = d.assign()
    assert task is yellow_fed_up  # Yellow before Green; of the Yellows, the one about to give up
    assert d.priority(green_fed_up) > d.priority(yellow_calm)  # (the weights alone would have said the Green)


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
                tiles = [(a.tx, a.ty) for a in actors  # (whoever is inside the elevator is off the floor)
                         if (a.role != 'patient' or not a.gone) and not a.hidden]
                assert len(set(tiles)) == len(tiles), 'two actors on one tile'
                for a in actors:
                    if a.role == 'patient':
                        room = h.lay.room_at[a.ty][a.tx]
                        assert (a.tx, a.ty) not in h.lay.staff_only, f'{a.name} behind a counter'
                        assert room is None or room.kind not in PATIENT_ONLY_NOT, f'{a.name} in {room.name}'
            time.sleep(0.01)
        # the doctors are upstairs: the first ones take a while to get there
        assert until(lambda: h.stats['treated'] >= 5, 30), h.summary()
    finally:
        stop(h)
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
                if p.on_stretcher and not p.path and not p.hidden:  # between two steps (not inside the elevator)
                    pushing = [n for n in nurses if n.task is not None and n.task.kind == 'stretcher']
                    assert len(pushing) == 2, 'two nurses push the stretcher'
                    for n in pushing:
                        assert abs(n.tx - p.tx) + abs(n.ty - p.ty) == 1, 'the stretcher came apart'
                        assert abs(n.px - p.px) + abs(n.py - p.py) <= 1.01, 'the stretcher came apart (drawn)'
            time.sleep(0.003)
        parked = lambda: sum(1 for t, c in list(h.world.occupied.items())
                             if type(c).__name__ == 'Cart' and c.driver is None and t in h.lay.stretcher_bay)
        assert until(lambda: parked() == config.STRETCHERS, 30), 'the stretcher was not pushed back to its place'
    finally:
        stop(h)
    assert h.stats['treated'] == 1, h.summary()
    assert any('on a stretcher' in t for _, t in h.events) and 'corridor' in rode, rode


def test_ambulance():
    """A Red patient can't walk: an ambulance comes down the road, its paramedics carry the patient on their stretcher
    to a bed in Emergency (through no other room), then get back in and the ambulance leaves."""
    h = Hospital(seed=10, n_doctors=1, n_surgeries=1, n_patients=0)
    h.start()
    rooms, walked = set(), []
    try:
        ambulance = h.admit(info(2, surgery=True))

        def brought():
            p = next((a for a in h.actors if a.role == 'patient'), None)
            if p is not None and not p.delivered.is_set():
                rooms.add(getattr(h.lay.room_at[p.ty][p.tx], 'kind', 'door'))
                walked.append(not p.lying)
            return p is not None and p.delivered.is_set() and ambulance not in h.actors

        assert until(brought, 30), 'the ambulance never brought the patient in and left'
    finally:
        stop(h)
    assert not any(walked) and rooms <= {'street', 'emergency', 'door'}, rooms
    assert not any(a.role in ('paramedic', 'ambulance') for a in h.actors)


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


def test_positions():
    """Where everyone is goes to the UI through shared memory: the UI reads exactly what the simulation wrote,
    and someone who left has no row any more."""
    shared = new_positions()
    row = lambda i: (i, i + 0.5, 2.5, i, 2, 3, 1.25, True, False, 2, 0.0, 0.0, -1, False)
    people = [SimpleNamespace(numbers=lambda i=i: row(i)) for i in range(3)]
    write_positions(shared, people)
    version, rows = read_positions(shared)
    assert rows == {i: row(i) for i in range(3)}
    assert read_positions(shared, version) == (version, None)  # nothing new since: no copy at all
    write_positions(shared, people[:1])
    assert read_positions(shared, version)[1] == {0: row(0)}


def write_rounds(shared, n):
    """(test_seqlock's writer, in another process) n writes of 50 rows; in write k every row says k."""
    for k in range(n):
        write_positions(shared, [SimpleNamespace(numbers=lambda i=i, k=k: (i, float(k), float(k), 0, 0, 0, 0.0,
                                                                          False, False, 0, 0.0, 0.0, -1, False))
                                 for i in range(50)])


def test_seqlock():
    """A writer in another process at full speed and a reader here: every copy the reader keeps is whole (all 50
    rows from the same write), never half of one write and half of the next. The writer never waits."""
    shared = new_positions()
    writer = mp.Process(target=write_rounds, args=(shared, 20000))
    writer.start()
    seen, whole = None, 0
    try:
        while writer.is_alive():
            seen, rows = read_positions(shared, seen)
            if rows:
                assert len({r[1] for r in rows.values()}) == 1, 'a torn read: rows from two different writes'
                whole += 1
    finally:
        writer.join()
    assert whole > 10, whole


def test_arrivals_at_night():
    """The arrivals process sends nobody while the doors are closed, and starts again when they open."""
    out, stop, open_ = mp.Queue(), threading.Event(), threading.Event()  # (a thread here: the same function)
    t = threading.Thread(target=run_arrivals, args=(out, stop, 1, None, open_), daemon=True)
    t.start()
    try:
        time.sleep(1.0)
        assert out.empty(), 'someone arrived at night'
        open_.set()
        assert until(lambda: not out.empty(), 5), 'nobody arrived once open'
    finally:
        stop.set()
        t.join(2)


def test_elevator():
    """The elevator serves the trains in the order they called, one at a time, and takes each to its floor."""
    h = Hospital(seed=12, n_doctors=1, n_surgeries=0, n_patients=0)  # not started: only the elevator runs
    lift = h.lifts[0]
    threading.Thread(target=h.run_lift, args=(lift,), daemon=True).start()
    first, second = object(), object()
    try:
        lift.call(first, 2)
        lift.call(second, 0)
        assert until(lambda: lift.take(first), 10) and lift.floor == 2  # it went up for whoever called first
        assert not lift.take(second)
        lift.send(0)
        assert until(lambda: lift.there(0), 10)
        assert not lift.take(second)  # still the first one's until it's out
        lift.leave(first)
        assert until(lambda: lift.take(second), 5)
    finally:
        h.stop.set()


def test_money():
    """A patient pays by urgency, less the later it saw a doctor; the payment is shared out by who did what."""
    h = Hospital(seed=13, n_doctors=1, n_surgeries=0, n_patients=0)  # not started: we pay by hand
    staff = {step: SimpleNamespace(label=lambda step=step: step) for step in ('reception', 'triage', 'doctor')}
    on_time = SimpleNamespace(base_urgency=1, waited=config.ON_TIME_S[1], helpers={k: [a] for k, a in staff.items()})
    h.treated(on_time)
    assert h.stats['income'] == config.PRICE[1]
    # no stretcher for it: that share is the doctor's
    assert abs(h.credit['doctor'] - config.PRICE[1] * (config.SHARE['doctor'] + config.SHARE['stretcher'])) < 1e-6
    assert abs(h.credit['triage'] - config.PRICE[1] * config.SHARE['triage']) < 1e-6
    late = SimpleNamespace(base_urgency=1, waited=3 * config.ON_TIME_S[1], helpers={'doctor': [staff['doctor']]})
    h.treated(late, surgery=True)
    assert abs(h.stats['income'] - config.PRICE[1] - (config.PRICE[1] + config.SURGERY_PRICE) * config.LATE_PAYS) < 1e-6


def test_day():
    """At midnight the doors close; once the last patient is gone the day is over and the world waits, paused,
    until the next day is started (pause can't skip it). Wages are paid by the hour."""
    with settings(HOUR_S=1, DAY_HOURS=4):
        h = Hospital(seed=14, n_doctors=1, n_surgeries=0, n_patients=0)
        h.start()
        try:
            h.admit(new_patient(h.rng, urgency=0))
            assert until(lambda: h.closed, 10)
            assert h.admit(new_patient(h.rng)) is None  # closed: nobody else comes in
            assert until(lambda: h.day_over is not None, 60), 'the day never ended'
            report = h.day_over
            assert h.speed == 0 and report['wages'] > 0 and report['day'] == 0
            assert abs(report['profit'] - (report['income'] - report['wages'])) < 1e-6
            h.command(('pause',))
            assert h.speed == 0  # only starting the next day goes on
            h.command(('next_day',))
            assert h.day == 1 and not h.closed and h.day_over is None and h.speed > 0
            assert h.today()['wages'] < report['wages']  # a new day, counted from zero
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
        assert len(h.working(JOBS['Doctor'])) == doctors
        rooms = len(h.lay.rooms_of('doctor'))
        for _ in range(rooms + 1):  # the last one finds no free room: refused
            h.command(('hire', 'Doctor'))
        assert len(h.working(JOBS['Doctor'])) == rooms
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
    runner.set_path(runner.route((x0 + 21, y)))  # 20 tiles: ~6 s running past the walker, ~13 s stuck behind it
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
    with settings(CONSULT_S=[(900, 900)] * 3, PATIENCE_LOSS=(25, 25), ESCALATE_AFTER_S=8,
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
