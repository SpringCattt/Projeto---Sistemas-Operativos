# art.py - Loads the PNG assets (Top Down Hospital pack + our own in assets/custom) and draws the map
import os

import pygame

import config
from layout import MARKERS, WALL_MOUNTED

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
LIFT = {'pc': 9, 'bell': 9, 'cake': 9, 'donuts': 9, 'books': 9}  # pixels up: they stand on a counter/desk top
LIFT.update({k: 12 for k in WALL_MOUNTED})  # (and these hang on the wall above their tile)

_cache = {}


def image(name):
    if name not in _cache:
        _cache[name] = pygame.image.load(os.path.join(ASSETS, name + '.png')).convert_alpha()
    return _cache[name]


def tile_image(lay, x, y):
    """The image of the ground at (x, y), as the map has it (a file for every look: see assets/custom/baked)."""
    return image(lay.ground[y][x])


def draw_layout(lay):
    """The ground (floors, walls, doors, roads), drawn once into a Surface from the map's tiles (the camera only
    crops and scales it). Furniture is drawn every frame together with the actors (furniture_sprites), so people
    can be behind it."""
    surf = pygame.Surface((lay.width * T, lay.height * T))
    for y, row in enumerate(lay.ground):
        for x, name in enumerate(row):
            surf.blit(image(name), (x * T, y * T))
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


# Cars (NYKNCK's City Pack, one file per colour and way: assets/custom/cars/car_<look>_<facing>.png)
CAR_LOOKS = 8  # car_0: the taxi; car_1 .. car_7: colours


def car_images(look):
    """{facing: image} of a car: look 0 is a taxi, the others a colour."""
    look = look if look == 0 else 1 + (look - 1) % (CAR_LOOKS - 1)
    return {facing: image(f'custom/cars/car_{look}_{facing}') for facing in ('left', 'right', 'up', 'down')}


# --- Characters (Jephed's Top Down Pixel Art Characters): 4 rows (down, left, right, up) x 3 frames of 20x32 ---
CHAR_W, CHAR_H = 20, 32
DIRECTIONS = ('down', 'left', 'right', 'up')


def character(index):
    """Returns {direction: [step_a, idle, step_b]} for sheet NNN.png."""
    sheet = image(f'third_party/characters/{index:03d}')
    return {d: [sheet.subsurface((c * CHAR_W, r * CHAR_H, CHAR_W, CHAR_H)) for c in range(3)]
            for r, d in enumerate(DIRECTIONS)}


def lying(index, across):
    """Someone in bed / on a stretcher, under a blanket (lengthwise, or across): assets/custom/lying/NNN_*.png."""
    return image(f'custom/lying/{index:03d}_{"across" if across else "along"}')


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
