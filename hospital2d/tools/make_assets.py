# make_assets.py - Generates the custom PNGs the Top Down Hospital pack lacks (reception, bar, floors...)
# using the pack's own palette. Run once: python tools/make_assets.py  (output: assets/custom/)
# The PNGs are the real assets: edit them in Aseprite/LibreSprite freely after generating.
import os
import random

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
import pygame

OUT = os.path.join(os.path.dirname(__file__), '..', 'assets', 'custom')

# Colours sampled from the pack (metal, wood, blue, dark) + a red/yellow for the vending machine
PAL = {
    'O': (64, 82, 115), 'm': (108, 129, 161), 'M': (150, 169, 193), 'L': (187, 195, 208),
    'w': (62, 39, 49), 'd': (115, 62, 57), 'D': (184, 111, 80), 'l': (228, 166, 114),
    'b': (18, 78, 137), 'B': (35, 105, 174), 'c': (0, 153, 219), 'W': (241, 246, 240),
    'k': (20, 35, 58), 'g': (48, 56, 67), 'r': (140, 40, 52), 'R': (196, 64, 70), 'y': (236, 196, 84),
}

SPRITES = {
    # Reception/bar counter, 3/4 view: wooden top, front panel facing the patients
    'counter_m': [
        "wwwwwwwwwwwwwwww", "llllllllllllllll", "DDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDD",
        "DDDDDDDDDDDDDDDD", "dddddddddddddddd", "wwwwwwwwwwwwwwww", "DDDDDDDDDDDDDDDD", "dddddddddddddddd",
        "ddddddddwddddddd", "ddddddddwddddddd", "dddddddddddddddd", "wwwwwwwwwwwwwwww",
    ],
    'office_chair': [
        "................", ".....OOOOOO.....", "....OkkkkkkO....", "....OggggggO....", ".....OOOOOO.....",
        "....OOOOOOOO....", "...OkggggggkO...", "...OgkkkkkkgO...", "...OggggggggO...", "...OkggggggkO...",
        "....OOOOOOOO....", ".......OO.......", ".....OmMMmO.....", "....Om.OO.mO....", "....OO....OO....",
    ],
    'pc': [  # receptionist monitor, sits on the counter
        "................", "...OOOOOOOOOO...", "...OkkkkkkkkO...", "...OkBccBBBkO...", "...OkBBBBBBkO...",
        "...OkbBBBBBkO...", "...OkkkkkkkkO...", "...OOOOOOOOOO...", "......OmmO......", ".....OMMMMO.....",
        ".....OOOOOO.....",
    ],
    'bell': [
        "................", "................", "................", "................", "................",
        "................", "................", ".......OO.......", "......OLMO......", ".....OLMMmO.....",
        "....OOOOOOOO....",
    ],
    'tv': [
        "................", "OOOOOOOOOOOOOOOO", "OkkkkkkkkkkkkkkO", "OkBBBBBBBBBccBkO", "OkBBBBBBBBcccBkO",
        "OkbBBBBBBBBBBBkO", "OkbbBBBBBBBBBBkO", "OkkkkkkkkkkkkkkO", "OOOOOOOOOOOOOOOO", "......OmmO......",
    ],
    'vending': [
        "..OOOOOOOOOOOO..", ".OrrrrrrrrrrrrO.", ".ORRRRRRRRRRRRO.", ".ORkkkkkkkRmmRO.", ".ORkcyBrckRMLRO.",
        ".ORkkkkkkkRMmRO.", ".ORkyBrcykRmmRO.", ".ORkkkkkkkRLMRO.", ".ORkBcyrBkRmmRO.", ".ORkkkkkkkRRRRO.",
        ".ORRRRRRRRRRRRO.", ".ORkkkkkkkkkkRO.", ".OrrrrrrrrrrrrO.", ".OkkkkkkkkkkkkO.", "..OOOOOOOOOOOO..",
    ],
    'coffee': [
        "................", "...OOOOOOOOOO...", "...OLLLLLLLLO...", "...OMMMMMMMMO...", "...OkkkkkkkkO...",
        "...OkrkkkkckO...", "...OkkkkkkkkO...", "...OMMMMMMMMO...", "...OMOOOOOOMO...", "...OMOkkkkOMO...",
        "...OMOkWWkOMO...", "...OMOkWWkOMO...", "...OmOOOOOOmO...", "...OmmmmmmmmO...", "...OOOOOOOOOO...",
    ],
    'sofa_m': [
        "................", "................", "OOOOOOOOOOOOOOOO", "bbbbbbbbbbbbbbbb", "BBBBBBBBBBBBBBBB",
        "BBBBBBBBBBBBBBBB", "bbbbbbbbbbbbbbbb", "cccccccccccccccb", "BBBBBBBBBBBBBBBb", "BBBBBBBBBBBBBBBb",
        "BBBBBBBBBBBBBBBb", "BBBBBBBBBBBBBBBb", "bbbbbbbbbbbbbbbb", "OOOOOOOOOOOOOOOO",
    ],
}


def grid_surface(rows):
    surf = pygame.Surface((16, len(rows)), pygame.SRCALPHA)
    for y, row in enumerate(rows):
        assert len(row) == 16, (y, row)
        for x, ch in enumerate(row):
            if ch != '.':
                surf.set_at((x, y), PAL[ch])
    return surf


def edge(rows, side, outline, inner):
    """Left ('l') or right ('r') end of a run: outline column + darker column on the solid rows."""
    out = []
    for row in rows:
        row = list(row)
        if any(ch != '.' for ch in row):
            o, i = (0, 1) if side == 'l' else (15, 14)
            row[o], row[i] = outline, inner if row[i] not in (outline,) else row[i]
        out.append(''.join(row))
    return out


def sofa_end(side):
    # armrest: the outer 3 columns become a raised dark-blue arm
    rows = [list(r) for r in SPRITES['sofa_m']]
    cols = (0, 1, 2) if side == 'l' else (15, 14, 13)
    for y in range(2, 14):
        rows[y][cols[0]] = 'O'
        rows[y][cols[1]] = 'b' if y in (2, 13) else 'B'
        rows[y][cols[2]] = 'b'
    rows[2][cols[1]] = rows[13][cols[1]] = 'O'
    return [''.join(r) for r in rows]


def tile(fill, line, seed, kind='tiles'):
    """16x16 floor tile in the pack's soft palette."""
    rng = random.Random(seed)
    s = pygame.Surface((16, 16))
    s.fill(fill)
    if kind == 'tiles':  # grout on the right/bottom, highlight top-left
        pygame.draw.line(s, line, (0, 15), (15, 15))
        pygame.draw.line(s, line, (15, 0), (15, 15))
        hl = tuple(min(255, v + 10) for v in fill)
        pygame.draw.line(s, hl, (0, 0), (14, 0))
        pygame.draw.line(s, hl, (0, 0), (0, 14))
    elif kind == 'planks':
        for y in (0, 4, 8, 12):
            pygame.draw.line(s, line, (0, y), (15, y))
            j = (y * 5 + 3) % 16
            pygame.draw.line(s, line, (j, y), (j, y + 3))
    elif kind == 'specks':
        for _ in range(10):
            s.set_at((rng.randrange(16), rng.randrange(16)), line)
    return s


def main():
    pygame.init()
    os.makedirs(OUT, exist_ok=True)
    out = {name: grid_surface(rows) for name, rows in SPRITES.items()}
    m = SPRITES['counter_m']
    out['counter_l'] = grid_surface(edge(m, 'l', 'w', 'l'))
    out['counter_r'] = grid_surface(edge(m, 'r', 'w', 'd'))
    out['sofa_l'], out['sofa_r'] = grid_surface(sofa_end('l')), grid_surface(sofa_end('r'))

    floors = {
        'waiting': ((205, 213, 224), (187, 195, 208), 'tiles'), 'reception': ((236, 220, 196), (214, 196, 170), 'tiles'),
        'corridor': ((214, 220, 228), (187, 195, 208), 'tiles'), 'doctor': ((206, 228, 216), (180, 206, 194), 'tiles'),
        'surgery': ((186, 216, 226), (150, 190, 206), 'tiles'), 'triage': ((224, 214, 236), (200, 188, 218), 'tiles'), 'disinfection': ((226, 238, 242), (196, 214, 222), 'tiles'),
        'storage': ((160, 164, 168), (138, 142, 148), 'specks'), 'rest': ((204, 140, 98), (184, 111, 80), 'planks'),
        'bar': ((184, 111, 80), (115, 62, 57), 'planks'), 'street': ((96, 100, 110), (84, 88, 98), 'specks'),
        'grass': ((96, 156, 84), (78, 134, 70), 'specks'),
    }
    for i, (name, (fill, line, kind)) in enumerate(floors.items()):
        out[f'floor_{name}'] = tile(fill, line, i, kind)

    wall = pygame.Surface((16, 16))
    wall.fill(PAL['O'])
    pygame.draw.line(wall, PAL['m'], (0, 0), (15, 0))
    pygame.draw.line(wall, (58, 68, 102), (0, 15), (15, 15))
    out['wall'] = wall
    door = tile((214, 220, 228), (187, 195, 208), 99)  # corridor floor with a door mat
    pygame.draw.rect(door, PAL['M'], (2, 2, 12, 12))
    pygame.draw.rect(door, PAL['m'], (2, 2, 12, 12), 1)
    out['door'] = door

    for name, surf in out.items():
        pygame.image.save(surf, os.path.join(OUT, f'{name}.png'))
    print(f'{len(out)} assets -> {os.path.abspath(OUT)}')


if __name__ == '__main__':
    main()
