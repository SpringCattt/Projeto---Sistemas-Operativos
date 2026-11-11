# Makes every floor symmetric about the building's middle line (the Main Hall's): edit the LEFT half in Tiled, then
# python tools/mirror_rooms.py and python tools/bake.py, from hospital2d.
# A room on the right that is where a room on the left is mirrored gets that room's furniture, mirrored; a room
# across the line (reception, the hall, the control room...) gets its left half mirrored onto its right half;
# Emergency and storage are each made symmetric about their own middle. The rest room is left as it is (it's laid
# out symmetric by hand, with different things on either side), and so are the elevators (both on one side).
import json
import os
import sys

sys.path.insert(0, os.getcwd())
from layout import base

T = 16
ts = json.load(open('maps/hospital.tsj', encoding='utf-8'))
props = {t['id'] + 1: {p['name']: p['value'] for p in t.get('properties', [])} for t in ts['tiles']}
image = {t['id'] + 1: t['image'] for t in ts['tiles']}
KEEP = {'elevator', 'energy_core'}  # left where they are
ACROSS = {'reception', 'corridor', 'control', 'disinfection', 'surgery'}  # mirrored onto themselves
OWN = {'emergency', 'storage'}  # symmetric about their own middle
NOT_FLOOR = ('wall', 'door', 'stairs', 'floor_grass', 'floor_void')


def footprint(o):
    p = {**props.get(o['gid'], {}), **{q['name']: q['value'] for q in o.get('properties', [])}}
    return p.get('w', 1), p.get('h', 1)


def tiles(o):
    w, h = footprint(o)
    x, y = round(o['x'] / T), round(o['y'] / T) - h
    return {(x + i, y + j) for i in range(w) for j in range(h)}


def kind(o):
    return props.get(o['gid'], {}).get('kind')


for floor in range(3):
    path = f'maps/floor{floor}.tmj'
    m = json.load(open(path, encoding='utf-8'))
    layers = {layer['name']: layer for layer in m['layers']}
    W, ground = m['width'], layers['ground']['data']
    rooms = [r for r in layers['rooms']['objects'] if r.get('type') not in ('staff_only', None)]
    objects = layers['furniture']['objects']
    hall = next(r for r in rooms if r['name'] == 'Main Hall')
    axis2 = round((2 * hall['x'] + hall['width']) / T)  # twice the middle line's x, in tiles
    box = lambda r: (round(r['x'] / T), round(r['y'] / T), round(r['width'] / T), round(r['height'] / T))

    def inside(o, r):
        x, y, w, h = box(r)
        return x <= round(o['x'] / T) < x + w and y <= round(o['y'] / T) - 1 < y + h
    next_id = max([m['nextobjectid']] + [o['id'] + 1 for o in objects])

    def mirrored(o, c2):  # a copy of o mirrored about x = c2 / 2 (tiles)
        global next_id
        c = dict(o, id=next_id, x=(c2 - round(o['x'] / T) - footprint(o)[0]) * T)
        next_id += 1
        return c

    def fits(o, taken):  # on floor tiles, not on another piece
        what = lambda x, y: base(image.get(ground[y * W + x], 'none.png')[:-4])
        return all(0 <= x < W and what(x, y) not in NOT_FLOOR and not what(x, y).startswith('grass_')
                   for x, y in tiles(o)) and not tiles(o) & taken
    out, done = list(objects), set()
    for r in rooms:
        x, y, w, h = box(r)
        if r['type'] in OWN:
            c2 = 2 * x + w
        elif r['type'] in ACROSS and 2 * x < axis2 < 2 * (x + w):
            c2 = axis2
        else:
            continue
        mine = [o for o in out if inside(o, r) and kind(o) not in KEEP]
        left = [o for o in mine if 2 * (round(o['x'] / T) + footprint(o)[0]) <= c2]
        right = [o for o in mine if 2 * round(o['x'] / T) >= c2]
        out = [o for o in out if not any(o is q for q in right)]
        taken = set().union(*(tiles(o) for o in out)) if out else set()
        for o in left:
            c = mirrored(o, c2)
            if fits(c, taken):
                out.append(c)
                taken |= tiles(c)
        done.add(r['id'])
    for r in rooms:  # the right half: rooms where the mirror image of one on the left is
        x, y, w, h = box(r)
        if r['id'] in done or 2 * x <= axis2:
            continue
        twin = next((q for q in rooms if q['type'] == r['type'] and box(q) == (axis2 - x - w, y, w, h)), None)
        if twin is not None:
            out = [o for o in out if not inside(o, r)]
            out += [mirrored(o, axis2) for o in out if inside(o, twin)]
    layers['furniture']['objects'] = out
    m['nextobjectid'] = next_id
    json.dump(m, open(path, 'w', encoding='utf-8'), indent=1)
    print('floor', floor, 'pieces', len(objects), '->', len(out))
