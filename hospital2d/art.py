# art.py - Loads the PNG assets (Top Down Hospital pack + our own in assets/custom) and draws the map
import os

import pygame

import config
from layout import EMPTY, WALL, DOOR

T = config.TILE
ASSETS = os.path.join(os.path.dirname(__file__), 'assets')

# Furniture kind -> image. H = Top Down Hospital pack, M = Modern Town sample, C = made by tools/make_assets.py
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
    'books': H + 'lab_books',
}
STACKED = {'shelf'}  # one image repeated on every tile of the footprint
LIFT = {'pc': 9, 'bell': 9, 'cake': 9, 'donuts': 9, 'books': 9}  # pixels up: they stand on a counter/desk top

_cache = {}


def image(name):
    if name not in _cache:
        _cache[name] = pygame.image.load(os.path.join(ASSETS, name + '.png')).convert_alpha()
    return _cache[name]


def draw_layout(lay):
    """Draws the whole floor plan once into a Surface (the camera only crops and scales it)."""
    surf = pygame.Surface((lay.width * T, lay.height * T))
    for y in range(lay.height):
        for x in range(lay.width):
            tile = lay.tiles[y][x]
            room = lay.room_at[y][x]
            if tile == EMPTY:
                name = 'custom/floor_grass'
            elif tile == WALL:
                name = 'custom/wall'
            elif tile == DOOR:
                name = 'custom/door'
            else:
                name = f'custom/floor_{room.kind}'
            surf.blit(image(name), (x * T, y * T))

    counters = {(f.x, f.y) for f in lay.furniture if f.kind == 'counter'}
    # Back to front, so taller pieces (3/4 view) overlap what is behind them
    for f in sorted(lay.furniture, key=lambda f: f.y + f.h):
        if f.kind == 'counter':
            left, right = (f.x - 1, f.y) in counters, (f.x + 1, f.y) in counters
            piece = 'm' if left and right else 'r' if left else 'l'
            surf.blit(image(f'custom/counter_{piece}'), (f.x * T, f.y * T))
        elif f.kind == 'sofa':
            for i in range(f.w):
                piece = 'l' if i == 0 else 'r' if i == f.w - 1 else 'm'
                surf.blit(image(f'custom/sofa_{piece}'), ((f.x + i) * T, f.y * T))
        elif f.kind in STACKED:
            for x, y in f.tiles():
                surf.blit(image(FURNITURE[f.kind]), (x * T, y * T))
        else:  # centred on the footprint, standing on its bottom edge
            img = image(FURNITURE[f.kind])
            surf.blit(img, ((f.x + f.w / 2) * T - img.get_width() // 2, (f.y + f.h) * T - img.get_height() - LIFT.get(f.kind, 0)))
    return surf


# --- Characters (Jephed's Top Down Pixel Art Characters): 4 rows (down, left, right, up) x 3 frames of 20x32 ---
CHAR_W, CHAR_H = 20, 32
DIRECTIONS = ('down', 'left', 'right', 'up')


def character(index):
    """Returns {direction: [step_a, idle, step_b]} for sheet NNN.png."""
    sheet = image(f'third_party/characters/{index:03d}')
    return {d: [sheet.subsurface((c * CHAR_W, r * CHAR_H, CHAR_W, CHAR_H)) for c in range(3)]
            for r, d in enumerate(DIRECTIONS)}
