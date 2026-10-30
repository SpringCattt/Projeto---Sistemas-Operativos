# test_layout.py - Checks the map drawn in Tiled (maps/) works for the simulation: python test_layout.py
from collections import deque

import config
from layout import DECOR, load_layout

for lay in [load_layout()]:
    assert lay.rooms_of('doctor') and lay.rooms_of('surgery')

    # Every free tile must be reachable from the street (no closed rooms or corners)
    start = lay.free_tiles(lay.rooms_of('street')[0])[0]
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1), *lay.links.get((x, y), ())):
            if lay.walkable(*nb) and nb not in seen:
                seen.add(nb)
                queue.append(nb)
    for r in lay.rooms:
        free = lay.free_tiles(r)
        assert free, f'{r.name} has no free floor'
        if r.kind == 'control':  # sealed on purpose: only the Boss is in there
            assert not set(free) & seen, 'the control room should have no way in'
        else:
            assert set(free) <= seen, f'{r.name} has unreachable areas'

    assert lay.path(start, lay.free_tiles(lay.rooms_of('doctor')[-1])[0])

    # Floors: stairs between each floor and the next; an elevator that stops on all of them, with the three tiles
    # in front of it free on each (where a stretcher train gets in and out)
    assert lay.floors >= 1 and all(lay.walkable(*t) for t in lay.links)
    for f in range(lay.floors - 1):
        assert any(lay.floor_of(t[0]) == f and any(lay.floor_of(u[0]) == f + 1 for u in up)
                   for t, up in lay.links.items()), f'no stairs from floor {f} to floor {f + 1}'
    if lay.floors > 1:
        assert any(len(stops) == lay.floors for stops in lay.lifts), 'no elevator to every floor'
    for stops in lay.lifts:
        for f, (column, _) in stops.items():
            assert all(lay.walkable(*t) for t in column) and set(column) <= seen, f'elevator door, floor {f}'

    # Patients not yet called go between waiting rooms without touching the corridor, triage or staff areas
    w1, w2 = lay.rooms_of('waiting')
    avoid, staff = ('corridor', 'triage'), lay.staff_only
    route = lay.path(lay.free_tiles(w1)[0], lay.free_tiles(w2)[-1], avoid, staff)
    assert route and not set(route) & staff
    assert all(lay.room_at[y][x] is None or lay.room_at[y][x].kind not in avoid for x, y in route)
    assert not lay.path(start, lay.free_tiles(lay.rooms_of('corridor')[0])[0], avoid, staff)

    # street -> reception -> triage -> waiting room, always on the patients' side of the counter
    assert staff and lay.seats
    for t, w in zip(lay.rooms_of('triage'), (w1, w2)):
        to_triage = lay.path(start, lay.free_tiles(t)[-1], ('corridor',), staff)
        to_waiting = lay.path(to_triage[-1], lay.free_tiles(w)[0], ('corridor',), staff)
        assert to_triage and to_waiting and not set(to_triage + to_waiting) & staff

    # Named spots: every one is walkable, and patients' ones are on the patients' side
    patient_spots = [front for _, front in lay.reception_desks] + lay.line_spots + lay.lobby
    patient_spots += [chair for _, _, chair in lay.triage_desks] + [c for _, c in lay.doctor_desks.values()]
    assert lay.reception_desks and len(lay.line_spots) >= 10 and lay.lobby and lay.street_line
    assert len(lay.doctor_desks) == len(lay.rooms_of('doctor')) and len(lay.triage_desks) >= 2
    assert all(lay.walkable(*t) and t not in staff for t in patient_spots)
    assert all(p and d for p, d in lay.surgery_spots.values()) and all(lay.sink_spots.values())
    assert all(s.partner and s.partner.kind == 'disinfection' for s in lay.rooms_of('surgery'))
    assert all(t.partner.kind == 'waiting' for t in lay.rooms_of('triage')) and lay.rest_spots
    assert all(len(b) >= 2 for b in lay.emergency_beds.values()) and lay.stretcher_spots
    assert len(lay.stretcher_bay) >= config.STRETCHERS and not set(lay.stretcher_bay) & set(lay.stretcher_spots)
    assert all(side for side, _ in lay.exam_spots.values())
    # The ambulance bay leads straight into Emergency, without crossing any other room
    em = lay.rooms_of('emergency')[0]
    route = lay.path(lay.ambulance_bay[0], lay.emergency_beds[em][0][0])
    assert lay.ambulance_bay and route and {lay.room_at[y][x].kind for x, y in route if lay.room_at[y][x]} <= {
        'street', 'emergency'}
    # Decorations never leave a way through only one tile wide
    assert not [f for f in lay.furniture if f.kind in DECOR and f.solid and lay.squeezes(f)]
# Old plans are freed (learn.py builds a new one every round)
import gc  # noqa: E402
from layout import Layout  # noqa: E402
for _ in range(3):
    load_layout().distances_from(lay.street_edge[0])
gc.collect()
assert sum(isinstance(o, Layout) for o in gc.get_objects()) <= 2, 'old plans are kept in memory'
print('ok')
