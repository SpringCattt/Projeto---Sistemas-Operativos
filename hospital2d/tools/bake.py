# Makes the image files the game draws, from the maps (run it after editing a map in Tiled, and after
# mirror_rooms.py): python tools/bake.py, from hospital2d. Everything the game used to draw with code is a file:
#   - ground: walls (their light / shadow edges), doors (closed, and open: <name>_open), the city's crossings and
#     kerbs, the ambulance bay's marks, the dark round the building upstairs, which grass / street look each tile has
#     -> assets/custom/baked/<what>@<n>.png, in the tileset and the maps (@: a variant; the game reads the name
#     before it for what the tile is)
#   - cars (each colour, each way), the ambulance coming down, the wheelchair going right, people lying under a
#     blanket (each character sheet, along and across), the urgency marks over heads
# It can run again and again: tiles it made before are read back as what they are (a baked wall is a wall).
# art_procedural.py is the drawing code that makes them.
import json
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))
import pygame

pygame.init()
pygame.display.set_mode((1, 1))
import art_procedural as A
A.ASSETS = os.path.abspath("assets")  # (the copy lives elsewhere: its images are still the game's)
import layout

T = 16
OUT = 'assets/custom/baked'
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))
save = lambda img, name: pygame.image.save(img, f'assets/custom/{name}.png')

# --- ground -------------------------------------------------------------------------------------------------------
void = pygame.Surface((T, T))
void.fill(A.UPSTAIRS)
save(void, 'floor_void')
lay = layout.load_layout()


def plain(name):  # what a tile was before baking (the drawing code knows only those)
    what = layout.base(name)
    if what.startswith('grass_') or what == 'floor_void':
        return 'custom/floor_grass'
    if what.startswith('street_'):
        return 'custom/floor_street'
    return 'custom/' + what if name.startswith('custom/baked/') else name


lay.ground = [[plain(n) for n in row] for row in lay.ground]
surf = A.draw_layout(lay)
walls = {(x, y) for y in range(lay.height) for x in range(lay.width) if lay.tiles[y][x] in (layout.WALL, layout.DOOR)}
made, names = {}, {}  # pixels -> name; (x, y) -> name


def variant(base, img, label=None):
    key = pygame.image.tobytes(img, 'RGB')
    if key not in made:
        n = label or str(sum(1 for v in made.values() if v.split('@')[0].endswith('/' + base)))
        made[key] = f'custom/baked/{base}@{n}'
        pygame.image.save(img, f'assets/{made[key]}.png')
    return made[key]


for y in range(lay.height):
    for x in range(lay.width):
        name, base = lay.ground[y][x], lay.ground[y][x].rsplit('/', 1)[-1]
        img = surf.subsurface((x * T, y * T, T, T)).copy()
        if base == 'floor_grass' and x >= lay.stride - layout.FLOOR_GAP:
            names[x, y] = 'custom/floor_void'  # (upstairs, and between the floors)
        elif base == 'wall':
            edges = ''.join(d for d, (dx, dy) in zip('nwse', ((0, -1), (-1, 0), (0, 1), (1, 0))) if (x + dx, y + dy) not in walls)
            names[x, y] = variant('wall', img, edges or 'none')
        elif base == 'door':
            names[x, y] = variant('door', img)
            pygame.image.save(A.door_floor(lay, x, y), f'assets/{names[x, y]}_open.png')
        elif pygame.image.tobytes(img, 'RGB') != pygame.image.tobytes(A.tile_image(lay, x, y), 'RGB'):
            names[x, y] = variant(base, img)  # (a crossing, a kerb, the ambulance bay...)
        elif name in A.VARIANTS:  # which look of grass / street (it was picked by its position)
            b, n = A.VARIANTS[name]
            names[x, y] = f'custom/{b}_{((x * 73856093) ^ (y * 19349663)) % n}'
        else:
            names[x, y] = name

# into the tileset and the maps
ts_path = 'maps/hospital.tsj'
ts = json.load(open(ts_path, encoding='utf-8'))
ts['tiles'] = [t for t in ts['tiles'] if '/baked/' not in t['image']]  # (made again below)
gid = {t['image'].split('assets/', 1)[1][:-4]: t['id'] + 1 for t in ts['tiles']}
for name in sorted(set(names.values())):
    if name not in gid:
        tile = {'id': max(t['id'] for t in ts['tiles']) + 1, 'image': f'../assets/{name}.png', 'imagewidth': T,
                'imageheight': T}
        ts['tiles'].append(tile)
        gid[name] = tile['id'] + 1
ts['tilecount'] = len(ts['tiles'])
json.dump(ts, open(ts_path, 'w', encoding='utf-8'), indent=1)
for floor in range(3):
    path = f'maps/floor{floor}.tmj'
    m = json.load(open(path, encoding='utf-8'))
    ground = next(layer for layer in m['layers'] if layer['name'] == 'ground')
    W, dx = m['width'], floor * lay.stride
    ground['data'] = [gid[names[dx + i % W, i // W]] for i in range(len(ground['data']))]
    json.dump(m, open(path, 'w', encoding='utf-8'), indent=1)
print('ground files', len(made), 'tiles named', len(names))

# --- vehicles, people, marks --------------------------------------------------------------------------------------
os.makedirs('assets/custom/cars', exist_ok=True)
for look in range(1 + len(A.CAR_PAINTS)):
    for facing, img in A.car_images(look).items():
        save(img, f'cars/car_{look}_{facing}')
for name in ['ambulance'] + [f'ambulance_{k}_{i}' for k in ('siren', 'open') for i in (0, 1)]:
    save(pygame.transform.rotate(A.image(f'custom/{name}'), 180), name + '_down')
save(pygame.transform.flip(A.image('custom/wheelchair_h'), True, False), 'wheelchair_h_right')
os.makedirs('assets/custom/lying', exist_ok=True)
sheets = sorted(int(f[:-4]) for f in os.listdir('assets/third_party/characters') if f[:-4].isdigit())
for index in sheets:
    frames = A.character(index)
    save(A.lying(frames, False), f'lying/{index:03d}_along')
    save(A.lying(frames, True), f'lying/{index:03d}_across')
for urgency, color in enumerate([(70, 200, 90), (240, 200, 50), (235, 70, 70)]):  # (main.URGENCY_COLORS)
    mark = pygame.Surface((5, 5), pygame.SRCALPHA)  # a diamond, outlined
    points = [(2, 0), (4, 2), (2, 4), (0, 2)]
    pygame.draw.polygon(mark, color, points)
    pygame.draw.polygon(mark, (30, 30, 30), points, 1)
    save(mark, f'mark_{urgency}')
print('cars, ambulance, wheelchair, lying', len(sheets), 'sheets, marks')
