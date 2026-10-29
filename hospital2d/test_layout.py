# test_layout.py - Checks the layout is valid for several sizes: python test_layout.py
from collections import deque

from layout import build_layout

for n_doctors, n_surgeries in ((1, 1), (5, 2), (100, 3)):
    lay = build_layout(n_doctors, n_surgeries)
    assert len(lay.rooms_of('doctor')) == n_doctors
    assert len(lay.rooms_of('surgery')) == n_surgeries

    # Every free tile must be reachable from the street (no closed rooms or corners)
    start = lay.free_tiles(lay.rooms_of('street')[0])[0]
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if lay.walkable(*nb) and nb not in seen:
                seen.add(nb)
                queue.append(nb)
    for r in lay.rooms:
        free = lay.free_tiles(r)
        assert free, f'{r.name} has no free floor'
        assert set(free) <= seen, f'{r.name} has unreachable areas'

    assert lay.path(start, lay.free_tiles(lay.rooms_of('doctor')[-1])[0])

    # Patients not yet called go between waiting rooms without touching the corridor
    w1, w2 = lay.rooms_of('waiting')
    route = lay.path(lay.free_tiles(w1)[0], lay.free_tiles(w2)[-1], avoid=('corridor',))
    assert route and all(lay.room_at[y][x] is None or lay.room_at[y][x].kind != 'corridor' for x, y in route)
    assert not lay.path(start, lay.free_tiles(lay.rooms_of('corridor')[0])[0], avoid=('corridor',))
print('ok')
