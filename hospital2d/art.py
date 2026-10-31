# art.py - Loads the PNG assets (Top Down Hospital pack + our own in assets/custom) and draws the map
import os

import pygame

import config
from layout import DOOR, MARKERS, WALL, WALL_MOUNTED

T = config.TILE
ASSETS = os.path.join(os.path.dirname(__file__), 'assets')

# Furniture kind -> image. H = Top Down Hospital pack, M = Modern Town sample, C = our own PNGs (assets/custom)
H, M, C = 'third_party/hospital/Pngs/', 'third_party/moderntown/sprites/shadow/16px/', 'custom/'
FURNITURE = {
    'chair': H + 'maternity_chair', 'office_chair': C + 'office_chair', 'plant': H + 'room_flower',
    'tv': C + 'tv', 'vending': C + 'vending', 'coffee': C + 'coffee', 'pc': C + 'pc', 'bell': C + 'bell',
    'trash_bin': H + 'room_trash_bin', 'table': H + 'maternity_table', 'desk': H + 'lab_medium_table',
    'exam_bed': H + 'room_bed_v', 'bed': H + 'room_bed_v', 'operating_table': H + 'operation_table',
    'monitor': H + 'room_ecg_cart', 'sink': H + 'lab_sink', 'shelf': H + 'room_pharmacy_shelf',
    # decoration
    'bench': C + 'bench_up', 'clock': M + 'board/clock_round', 'eye_chart': M + 'exam/eye_chart',
    'bp_monitor': M + 'exam/bp_monitor', 'exit_sign': M + 'sign/sign_exit', 'map_stand': M + 'sign/map_stand',
    'soda': M + 'shelf/endcap_soda', 'snacks': M + 'shelf/shelf_canned_food', 'cake': M + 'bakery/cake_dome',
    'donuts': M + 'bakery/bakery_case_donut', 'iv_stand': H + 'room_iv_stand_w', 'sanitizer': H + 'room_hand_sanitizer',
    'biohazard': H + 'room_biohazard_bin', 'poster': H + 'room_anatomy_poster', 'cabinet': H + 'room_cabinet',
    'bedside': H + 'room_bedside_table', 'armchair': H + 'room_armchair', 'fridge': H + 'lab_refrigerator',
    'tray': H + 'operation_accessories_table_full', 'ultrasound': H + 'operation_ultrasound_machine',
    'books': H + 'lab_books', 'ambulance': C + 'ambulance', 'elevator': C + 'elevator', 'chair_up': C + 'chair_up',
    'big_tv': C + 'big_tv', 'call_screen': C + 'call_screen', 'speaker': C + 'speaker',
    # the AI's room
    'server_rack': C + 'server_rack', 'ai_screen': C + 'ai_screen', 'ai_core': C + 'ai_core',
    # more decoration
    'water_cooler': C + 'water_cooler', 'magazine_rack': C + 'magazine_rack', 'fire_extinguisher': C + 'fire_extinguisher',
    'painting': C + 'painting_0', 'painting_2': C + 'painting_1', 'notice_board': C + 'notice_board',
    'xray_viewer': C + 'xray_viewer', 'big_plant': C + 'big_plant_0', 'big_plant_2': C + 'big_plant_1',
    'coat_rack': C + 'coat_rack', 'first_aid': C + 'first_aid', 'boss_desk': C + 'boss_desk_0',
    'tv_back': C + 'tv_back',
}
# The furniture pack (assets/third_party/furniture, see README): kind -> its number there
PACK = {'sofa_beige': 173, 'sofa_corner': 208, 'sofa_grey': 422, 'chaise': 1000, 'bookcase': 475,
        'grandfather_clock': 591, 'wardrobe': 35, 'dresser': 40, 'sideboard': 438, 'pc_desk': 433, 'desk_white': 292,
        'workbench': 241, 'folding_screen': 746, 'console_curved': 88,
        'lab_desk': 106, 'holo_table': 523, 'monitor_wall': 639, 'sci_device': 640, 'scan_bed': 658, 'robot_desk': 892,
        'sleep_pod': 895, 'command_desk': 994, 'command_desk_2': 1034, 'energy_core': 1038, 'gaming_chair': 925}
FURNITURE.update({kind: f'third_party/furniture/Furniture ({n})' for kind, n in PACK.items()})
STACKED = {'shelf'}  # one image repeated on every tile of the footprint
# Animated pieces: kind -> (its frames custom/<name>_0.., frames per second). main.py picks the frame by time
ANIMATED = {'tv': ('tv', 8, 1.5), 'vending': ('vending', 3, 1.2), 'coffee': ('coffee', 3, 3.0),
            'pc': ('pc_back', 3, 1.0),  # (seen from behind: its screen faces whoever sits at it)
            'big_tv': ('big_tv', 12, 2.0), 'server_rack': ('server_rack', 4, 3.0), 'ai_screen': ('ai_screen', 8, 2.5),
            'ai_core': ('ai_core', 6, 4.0), 'xray_viewer': ('xray_viewer', 2, 0.7), 'boss_desk': ('boss_desk', 4, 5.0)}
FACING_AWAY = {'chair_up', 'bench'}  # their backs to us: drawn over whoever sits on them (the backrest hides the legs)
VARIANTS = {'custom/floor_grass': ('grass', 4), 'custom/floor_street': ('street', 3)}  # (so they don't look tiled)
LIFT = {'pc': 9, 'bell': 9, 'cake': 9, 'donuts': 9, 'books': 9}  # pixels up: they stand on a counter/desk top
LIFT.update({k: 12 for k in WALL_MOUNTED})  # (and these hang on the wall above their tile)

_cache = {}


def image(name):
    if name not in _cache:
        _cache[name] = pygame.image.load(os.path.join(ASSETS, name + '.png')).convert_alpha()
    return _cache[name]


def tile_image(lay, x, y):
    """The image of the ground at (x, y), as painted in the map (grass and street: one of a few looks)."""
    name = lay.ground[y][x]
    if name in VARIANTS:  # (scattered, so they don't look tiled; the same every time)
        base, n = VARIANTS[name]
        name = f'custom/{base}_{((x * 73856093) ^ (y * 19349663)) % n}'
    return image(name)


def door_floor(lay, x, y):
    """A door tile's floor: half of each room it joins (the floor on either side of it)."""
    across = lay.tiles[y - 1][x] in (WALL, DOOR) or lay.tiles[y + 1][x] in (WALL, DOOR)  # down a vertical wall
    a, b = ((x - 1, y), (x + 1, y)) if across else ((x, y - 1), (x, y + 1))
    surf = pygame.Surface((T, T))
    surf.blit(tile_image(lay, *a), (0, 0))
    half = (T // 2, 0, T - T // 2, T) if across else (0, T // 2, T, T - T // 2)
    surf.blit(tile_image(lay, *b).subsurface(half), half[:2])
    return surf


def draw_layout(lay):
    """Floor, walls and doors, drawn once into a Surface (the camera only crops and scales it).
    Furniture is drawn every frame together with the actors (furniture_sprites), so people can be behind it.
    Walls are one block: the light and shadow lines only on their outer edges; a door's floor is half each room's."""
    surf = pygame.Surface((lay.width * T, lay.height * T))
    walls = {(x, y) for y in range(lay.height) for x in range(lay.width) if lay.tiles[y][x] in (WALL, DOOR)}
    wall = image('custom/wall')
    fill, light, shade = wall.get_at((8, 8)), wall.get_at((8, 0)), wall.get_at((8, 15))
    door_band = {'h': image('custom/door_h').subsurface((0, 4, T, 8)), 'v': image('custom/door_v').subsurface((4, 0, 8, T))}
    for y, row in enumerate(lay.ground):
        for x, name in enumerate(row):
            if name == 'custom/wall':
                surf.fill(fill, (x * T, y * T, T, T))
                for (dx, dy), color, edge in (((0, -1), light, (0, 0, T, 1)), ((-1, 0), light, (0, 0, 1, T)),
                                             ((0, 1), shade, (0, T - 1, T, 1)), ((1, 0), shade, (T - 1, 0, 1, T))):
                    if (x + dx, y + dy) not in walls:
                        surf.fill(color, (x * T + edge[0], y * T + edge[1], edge[2], edge[3]))
            elif name == 'custom/door':  # the door across its wall, over half of each room's floor
                surf.blit(door_floor(lay, x, y), (x * T, y * T))
                if {(x, y - 1), (x, y + 1)} & walls:
                    surf.blit(door_band['v'], (x * T + 4, y * T))
                else:
                    surf.blit(door_band['h'], (x * T, y * T + 4))
            else:
                surf.blit(tile_image(lay, x, y), (x * T, y * T))
    return surf


def furniture_sprites(lay, night=False):
    """[(depth, image, x, y)] in world pixels, sorted back to front. depth is where the piece meets the floor:
    whatever has a bigger depth is drawn later and covers it. Seats use their top edge instead, so whoever
    sits on them is drawn over them. An animated piece's image is (frames, frames per second, a time offset so
    they don't all move in step). The city's pieces (scenery_sprites) come along."""
    counters = {(f.x, f.y) for f in lay.furniture if f.kind == 'counter'}
    out = []
    for f in lay.furniture:
        if f.kind in ('ambulance', 'elevator') or f.kind in MARKERS:  # animated: main.py draws them (the
            continue  # ambulance comes and goes); the bays of the parking lot are only marks for Tiled
        depth = (f.y + f.h) * T if f.solid or f.kind in FACING_AWAY else f.y * T
        if f.kind == 'counter':
            left, right = (f.x - 1, f.y) in counters, (f.x + 1, f.y) in counters
            piece = 'm' if left and right else 'r' if left else 'l'
            out.append((depth, image(f'custom/counter_{piece}'), f.x * T, f.y * T))
        elif f.kind == 'sofa':
            for i in range(f.w):
                piece = 'l' if i == 0 else 'r' if i == f.w - 1 else 'm'
                out.append((depth, image(f'custom/sofa_{piece}'), (f.x + i) * T, f.y * T))
        elif f.kind in STACKED:
            out += [(depth, image(FURNITURE[f.kind]), x * T, y * T) for x, y in f.tiles()]
        else:  # centred on the footprint, standing on its bottom edge
            img = image(FURNITURE[f.kind])
            x, y = (f.x + f.w / 2) * T - img.get_width() // 2, (f.y + f.h) * T - img.get_height() - LIFT.get(f.kind, 0)
            offset = ((f.x * 7 + f.y * 13) % 17) / 7
            if f.kind in ANIMATED:
                name, n, fps = ANIMATED[f.kind]
                if f.kind == 'pc' and lay.seat_facing.get((f.x, f.y + 1)) == 'up':  # whoever uses it sits below:
                    name, n = 'pc', 4  # its screen faces us
                frames = tuple(image(f'custom/{name}_{i}') for i in range(n))
                out.append((depth, (frames, fps, offset), x + (img.get_width() - frames[0].get_width()) // 2,
                            y + img.get_height() - frames[0].get_height()))
            else:
                out.append((depth, img, x, y))
            if f.kind == 'monitor':  # the cart gets a screen with the heartbeat on it, sitting on its top
                screen = tuple(image(f'custom/ecg_{i}') for i in range(10))
                out.append((depth, (screen, 5.0, offset), x + 2, y + 4))
    out += scenery_sprites(lay, night)
    out.sort(key=lambda s: s[0])  # stable: things standing on a counter/desk stay after it
    return out


def has(name):
    return os.path.exists(os.path.join(ASSETS, name + '.png'))


def lit(name, x, y):
    """Whether this piece of scenery has its lights on at night (lamps always; two buildings in three)."""
    return has(name + '_glow') and ('building' not in name or (x * 7 + y * 3) // T % 3 != 0)


def scenery_sprites(lay, night=False):
    """The city's pieces (Layout.scenery: an image and its top-left corner) like furniture: traffic lights change,
    and by night whatever has lights shows them off (<name>_off; main.py lays the light itself over the dark sky:
    glows)."""
    out = []
    for name, x, y in lay.scenery:
        img = image(name)
        depth = y + img.get_height()
        if 'traffic_light' in name and name.endswith('_0'):
            out.append((depth, (tuple(image(name[:-1] + str(i)) for i in range(4)), 0.3, (x * 7 + y) % 13 / 3), x, y))
        else:
            out.append((depth, image(name + '_off') if night and has(name + '_off') else img, x, y))
    return out


def glows(lay):
    """[(light, x, y)]: the night lights of the city (lit windows, the lamps' glow)."""
    return [(image(name + '_glow'), x, y) for name, x, y in lay.scenery if lit(name, x, y)]


# Cars (NYKNCK's City Pack): seen from the side going left or right, from the front coming down (and the front
# upside down going up). The pack's car is orange; its paint (shadow .. shine) is swapped for each colour
CITY = 'third_party/citypack/pieces/'
CAR_RAMP = [(93, 39, 93), (177, 62, 83), (239, 125, 87), (249, 155, 78), (255, 205, 117)]
CAR_PAINTS = [[(90, 30, 50), (150, 40, 55), (200, 60, 60), (235, 100, 90), (250, 160, 140)],  # red
              [(40, 40, 100), (50, 80, 160), (70, 120, 210), (110, 160, 235), (170, 210, 250)],  # blue
              [(110, 110, 130), (170, 175, 190), (215, 220, 228), (238, 240, 244), (255, 255, 255)],  # white
              [(25, 25, 35), (40, 44, 56), (60, 66, 82), (85, 92, 110), (120, 130, 150)],  # black
              [(30, 70, 50), (40, 110, 70), (60, 150, 90), (100, 190, 120), (160, 225, 170)],  # green
              [(80, 85, 100), (130, 136, 150), (165, 172, 185), (195, 200, 210), (225, 228, 235)],  # silver
              CAR_RAMP]  # orange
TAXI = [(120, 80, 30), (200, 150, 30), (240, 200, 50), (250, 220, 90), (255, 240, 160)]


def car_images(look):
    """{facing: image} of a car: look 0 is a taxi (yellow, a sign on its roof), 1.. a colour of CAR_PAINTS."""
    paint = TAXI if look == 0 else CAR_PAINTS[(look - 1) % len(CAR_PAINTS)]

    def repaint(img, levels):  # (the side is darker than the roof seen from above: one shade up)
        img, swap = img.copy(), {CAR_RAMP[i]: paint[j] for i, j in enumerate(levels)}
        for x in range(img.get_width()):
            for y in range(img.get_height()):
                c = img.get_at((x, y))
                if c.a and tuple(c)[:3] in swap:
                    img.set_at((x, y), (*swap[tuple(c)[:3]], c.a))
        return img
    side, front = repaint(image(CITY + 'car_side'), (0, 2, 3, 3, 4)), repaint(image(CITY + 'car_front'), range(5))
    if look == 0:
        pygame.draw.rect(side, (36, 40, 52), (19, 0, 10, 3))
        pygame.draw.rect(side, (255, 250, 220), (20, 1, 8, 1))
        pygame.draw.rect(front, (36, 40, 52), (11, 9, 10, 4))
        pygame.draw.rect(front, (255, 250, 220), (12, 10, 8, 2))
    return {'left': side, 'right': pygame.transform.flip(side, True, False), 'down': front,
            'up': pygame.transform.flip(front, False, True)}


# --- Characters (Jephed's Top Down Pixel Art Characters): 4 rows (down, left, right, up) x 3 frames of 20x32 ---
CHAR_W, CHAR_H = 20, 32
DIRECTIONS = ('down', 'left', 'right', 'up')


def character(index):
    """Returns {direction: [step_a, idle, step_b]} for sheet NNN.png."""
    sheet = image(f'third_party/characters/{index:03d}')
    return {d: [sheet.subsurface((c * CHAR_W, r * CHAR_H, CHAR_W, CHAR_H)) for c in range(3)]
            for r, d in enumerate(DIRECTIONS)}


def lying(frames, across):
    """Someone in bed / on a stretcher: head and shoulders out of a blanket (lengthwise, or across if `across`)."""
    body = pygame.Surface((CHAR_W, 26), pygame.SRCALPHA)
    body.blit(frames['down'][1], (0, 0), (0, 0, CHAR_W, 20))
    body.blit(image('custom/blanket'), (3, 14))
    return pygame.transform.rotate(body, 90) if across else body


# --- Speech bubbles: PNGs in assets/custom. Words go in the smallest bubble_s/m/l/xl/xxl they fit in; an icon
# bubble (bubble_alert, ...) is used as it is.
TEXT_BUBBLES = ['bubble_s', 'bubble_m', 'bubble_l', 'bubble_xl', 'bubble_xxl']
ICONS = {'!': 'bubble_alert', '?': 'bubble_surprise', '~': 'bubble_sick', 'z': 'bubble_sleep', '<3': 'bubble_heart'}
BUBBLE_PAD = 4  # pixels between the side of a bubble and its words
BUBBLE_TEXT_H = 18  # height of the white part of the text bubbles (the tail is below it)
BUBBLE_TAIL_X = 3  # x of the tail's tip in every bubble PNG


def icon_bubble(kind):
    return image(f'custom/{ICONS[kind]}')


def text_bubble(font, text):
    words = font.render(text, False, (24, 24, 28))  # no antialiasing: pixel text, scaled like the rest
    for name in TEXT_BUBBLES:
        if words.get_width() + 2 * BUBBLE_PAD <= image(f'custom/{name}').get_width():
            break
    surf = image(f'custom/{name}').copy()
    w = words.get_width() + 2 * BUBBLE_PAD
    if w > surf.get_width():  # longer than the biggest bubble: stretch its middle column (long names)
        base, mid = surf, surf.get_width() // 2
        surf = pygame.Surface((w, base.get_height()), pygame.SRCALPHA)
        surf.blit(base, (0, 0), (0, 0, mid, base.get_height()))
        right = base.get_width() - mid
        surf.blit(base, (w - right, 0), (mid, 0, right, base.get_height()))
        column = base.subsurface((mid, 0, 1, base.get_height()))
        surf.blit(pygame.transform.scale(column, (w - base.get_width(), base.get_height())), (mid, 0))
    surf.blit(words, ((surf.get_width() - words.get_width()) // 2, (BUBBLE_TEXT_H - words.get_height()) // 2))
    return surf
