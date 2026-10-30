# art.py - Loads the PNG assets (Top Down Hospital pack + our own in assets/custom) and draws the map
import os

import pygame

import config
from layout import DOOR, WALL

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
    'bench': M + 'waiting/bench_waiting_3', 'clock': M + 'board/clock_round', 'eye_chart': M + 'exam/eye_chart',
    'bp_monitor': M + 'exam/bp_monitor', 'exit_sign': M + 'sign/sign_exit', 'map_stand': M + 'sign/map_stand',
    'soda': M + 'shelf/endcap_soda', 'snacks': M + 'shelf/shelf_canned_food', 'cake': M + 'bakery/cake_dome',
    'donuts': M + 'bakery/bakery_case_donut', 'iv_stand': H + 'room_iv_stand_w', 'sanitizer': H + 'room_hand_sanitizer',
    'biohazard': H + 'room_biohazard_bin', 'poster': H + 'room_anatomy_poster', 'cabinet': H + 'room_cabinet',
    'bedside': H + 'room_bedside_table', 'armchair': H + 'room_armchair', 'fridge': H + 'lab_refrigerator',
    'tray': H + 'operation_accessories_table_full', 'ultrasound': H + 'operation_ultrasound_machine',
    'books': H + 'lab_books', 'ambulance': C + 'ambulance', 'elevator': C + 'elevator',
}
STACKED = {'shelf'}  # one image repeated on every tile of the footprint
LIFT = {'pc': 9, 'bell': 9, 'cake': 9, 'donuts': 9, 'books': 9}  # pixels up: they stand on a counter/desk top

_cache = {}


def image(name):
    if name not in _cache:
        _cache[name] = pygame.image.load(os.path.join(ASSETS, name + '.png')).convert_alpha()
    return _cache[name]


def draw_layout(lay):
    """Floor, walls and doors, drawn once into a Surface (the camera only crops and scales it).
    Furniture is drawn every frame together with the actors (furniture_sprites), so people can be behind it."""
    surf = pygame.Surface((lay.width * T, lay.height * T))
    walls = {(x, y) for y in range(lay.height) for x in range(lay.width) if lay.tiles[y][x] in (WALL, DOOR)}
    for y, row in enumerate(lay.ground):  # each tile as painted in the map
        for x, name in enumerate(row):
            # a wall that only goes up and down gets the upright piece, so it's one smooth wall like the others
            if name == 'custom/wall' and ({(x, y - 1), (x, y + 1)} & walls) and not ({(x - 1, y), (x + 1, y)} & walls):
                name = 'custom/wall_v'
            surf.blit(image(name), (x * T, y * T))
    return surf


def furniture_sprites(lay):
    """[(depth, image, x, y)] in world pixels, sorted back to front. depth is where the piece meets the floor:
    whatever has a bigger depth is drawn later and covers it. Seats use their top edge instead, so whoever
    sits on them is drawn over them."""
    counters = {(f.x, f.y) for f in lay.furniture if f.kind == 'counter'}
    out = []
    for f in lay.furniture:
        depth = (f.y + f.h) * T if f.solid else f.y * T
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
            out.append((depth, img, (f.x + f.w / 2) * T - img.get_width() // 2,
                        (f.y + f.h) * T - img.get_height() - LIFT.get(f.kind, 0)))
    out.sort(key=lambda s: s[0])  # stable: things standing on a counter/desk stay after it
    return out


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
    room = surf.get_width() - 2 * BUBBLE_PAD
    if words.get_width() > room:  # longer than the biggest bubble: cut (keep the lines in dialogue.txt short)
        words = words.subsurface((0, 0, room, words.get_height()))
    surf.blit(words, ((surf.get_width() - words.get_width()) // 2, (BUBBLE_TEXT_H - words.get_height()) // 2))
    return surf
