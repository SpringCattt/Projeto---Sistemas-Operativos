# test_actors.py - Checks the movement rules, first step by step and then with the real threads:
# python test_actors.py
import random
import threading
import time

from actors import create_actors
from layout import build_layout

lay = build_layout(5, 2)


def check(actors, when):
    tiles = [(a.tx, a.ty) for a in actors]
    assert len(set(tiles)) == len(tiles), f'two actors on one tile {when}'
    for a in actors:
        room = lay.room_at[a.ty][a.tx]
        if a.role == 'patient':
            assert (a.tx, a.ty) not in lay.staff_only, f'patient behind a counter {when}'
            assert room is None or room.kind not in ('corridor', 'triage'), f'patient in {room.name} {when}'


# 1) Logic, one actor after another (deterministic): ~200 s of simulation
actors = create_actors(lay, random.Random(7))
seated_staff = {a: (a.tx, a.ty) for a in actors if a.area == [(a.tx, a.ty)]}
assert len(seated_staff) >= 3
for step in range(4000):
    for a in actors:
        a.update(0.05, lay)
    check(actors, f'at step {step}')
    for a in actors:
        if not a.path and (a.tx, a.ty) in lay.seats:
            assert a.facing == 'down' and a.state(lay) == 'seated', (a.role, a.facing, step)
for a, seat in seated_staff.items():
    assert (a.tx, a.ty) == seat and a.state(lay) == 'seated', f'{a.role} left their desk'

# 2) Concurrency: every actor in its own thread for a few seconds. Sampling under the world lock gives a
#    consistent picture, because tiles only change while that lock is held.
actors = create_actors(lay, random.Random(8))
world, stop = actors[0].world, threading.Event()
threads = [threading.Thread(target=a.run, args=(lay, stop), daemon=True) for a in actors]
for t in threads:
    t.start()
end = time.time() + 5
while time.time() < end:
    with world.lock:
        check(actors, 'with threads')
        assert all(world.occupied.get((a.tx, a.ty)) is a for a in actors), 'occupied map out of sync'
    time.sleep(0.01)
stop.set()
for t in threads:
    t.join(timeout=2)
assert not any(t.is_alive() for t in threads), 'a thread did not stop'
print('ok')
