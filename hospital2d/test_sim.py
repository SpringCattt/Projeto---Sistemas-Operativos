# test_sim.py - The simulation with its real threads (no UI, no other processes), sped up 8x: python test_sim.py
# Takes about 1.5 minutes.
import contextlib
import queue
import time
from types import SimpleNamespace

import config

config.TIME_SCALE = 8

from arrivals import new_patient  # noqa: E402 (after the speed-up)
from sim import Hospital  # noqa: E402
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
        return Task('consult', 'doctor', rank, patient=SimpleNamespace(name='P', rank=rank, tx=0, ty=0))

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
