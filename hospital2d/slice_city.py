# slice_city.py - Cuts NYKNCK's City Pack (assets/third_party/citypack, see README) into one PNG per piece, in
# assets/third_party/citypack/pieces: the maps' tileset points at those. Run once after unzipping the pack.
# Pieces that light up at night get two more: <name>_off (lights off: drawn at night under the dark sky) and
# <name>_glow (only the light: lit windows, a lamp's glow; drawn over the dark sky).
import os

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import pygame

PACK = os.path.join(os.path.dirname(__file__), 'assets', 'third_party', 'citypack')
OUT = os.path.join(PACK, 'pieces')
SHEET, LIGHTS, STREET = 'CP_V1.0.4_nyknck/CP_V1.0.4.png', 'Animations/BL001.png', 'Animations/SL001.png'

# name: (x, y, w, h) on the main sheet
PIECES = {
    'bench_0': (400, 48, 32, 16), 'bench_1': (464, 48, 32, 16), 'info_board': (512, 48, 16, 16),
    'bollard': (413, 80, 3, 16), 'bike_rack_0': (432, 85, 16, 11), 'bike_rack_1': (464, 86, 16, 10),
    'bike_rack_2': (496, 85, 16, 11), 'barrier_0': (400, 131, 16, 13), 'barrier_1': (432, 133, 16, 11),
    'barrier_2': (464, 128, 16, 16), 'cone': (497, 131, 14, 13), 'trash_can': (401, 160, 14, 16),
    'newspaper_box': (432, 160, 16, 16), 'hydrant': (466, 162, 10, 14), 'street_vending': (496, 176, 25, 32),
    'box_0': (544, 185, 16, 23), 'box_1': (576, 185, 16, 23), 'box_2': (608, 188, 23, 20),
    'mailbox_0': (402, 192, 12, 16), 'mailbox_1': (434, 192, 12, 16), 'billboard_0': (368, 254, 48, 66),
    'billboard_1': (432, 254, 48, 66), 'billboard_2': (496, 254, 48, 66), 'road_sign_0': (385, 468, 14, 28),
    'road_sign_1': (418, 470, 12, 26), 'road_sign_2': (450, 470, 12, 26), 'tree_0': (384, 514, 16, 30),
    'tree_1': (415, 517, 18, 27),
    'car_side': (129, 389, 48, 27), 'car_front': (240, 483, 32, 45),  # (art.py paints them in other colours)
    'pavement': (432, 816, 16, 16), 'manhole': (16, 672, 16, 16),
}
# buildings: name: (rect on the main sheet, lights off, lights on (on BL001: same size))
BUILDINGS = {
    'building_0': ((32, 32, 48, 96), (48, 32), (128, 32)), 'building_1': ((112, 32, 48, 96), (48, 176), (128, 176)),
    'building_2': ((192, 32, 48, 96), (48, 304), (128, 304)), 'building_3': ((272, 32, 80, 96), (48, 432), (144, 432)),
    'building_4': ((16, 278, 64, 42), (48, 598), (144, 598)), 'building_5': ((16, 176, 64, 48), (48, 688), (161, 688)),
    'building_6': ((112, 176, 48, 48), (48, 783), (161, 784)), 'building_7': ((192, 170, 64, 54), (49, 874), (161, 874)),
    'building_8': ((288, 176, 48, 48), (49, 960), (161, 960)), 'building_9': ((112, 278, 48, 42), (49, 1061), (161, 1062)),
    'building_10': ((208, 272, 32, 48), (49, 1152), (161, 1152)),
    'building_11': ((288, 272, 48, 96), (49, 1232), (161, 1232)),
}
# street lamps: name: (off, on) on SL001; the lit one is wider (its glow), so it's lined up on the lamp itself
LAMPS = {'lamp_0': ((32, 368, 9, 32), (53, 368, 31, 32)), 'lamp_1': ((32, 416, 24, 32), (64, 416, 36, 32)),
         'lamp_2': ((32, 464, 9, 32), (53, 464, 31, 32)), 'lamp_3': ((32, 513, 9, 31), (52, 513, 33, 31))}
# traffic lights: name: their 4 frames (red, red and amber, green, amber) on SL001 -> <name>_0..3
TRAFFIC = {'traffic_light_0': [(31 + 32 * i, 34, 8, 30) for i in range(4)],
           'traffic_light_1': [(32 + 48 * i, 80, 29, 32) for i in range(4)],
           'traffic_light_2': [(35 + 48 * i, 144, 29, 32) for i in range(4)],
           'traffic_light_3': [(31 + 32 * i, 258, 7, 30) for i in range(4)],
           'traffic_light_4': [(32 + 32 * i, 306, 7, 30) for i in range(4)]}


def glow(off, on):
    """The pixels of `on` that differ from `off` (the light), the rest transparent."""
    g = pygame.Surface(on.get_size(), pygame.SRCALPHA)
    for y in range(on.get_height()):
        for x in range(on.get_width()):
            if on.get_at((x, y)) != off.get_at((x, y)):
                g.set_at((x, y), on.get_at((x, y)))
    return g


def line_up(off, on):
    """off drawn on a canvas the size of `on`, where it matches `on` best."""
    def misses(dx, dy):
        return sum(on.get_at((x + dx, y + dy)) != off.get_at((x, y)) for y in range(off.get_height())
                   for x in range(off.get_width()) if off.get_at((x, y)).a)
    dx, dy = min(((dx, dy) for dx in range(on.get_width() - off.get_width() + 1)
                  for dy in range(on.get_height() - off.get_height() + 1)), key=lambda d: misses(*d))
    canvas = pygame.Surface(on.get_size(), pygame.SRCALPHA)
    canvas.blit(off, (dx, dy))
    return canvas


def main():
    pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.HIDDEN)
    sheet, lights, street = (pygame.image.load(os.path.join(PACK, p)).convert_alpha() for p in (SHEET, LIGHTS, STREET))
    os.makedirs(OUT, exist_ok=True)
    save = lambda s, name: pygame.image.save(s, os.path.join(OUT, name + '.png'))
    for name, rect in PIECES.items():
        save(sheet.subsurface(rect), name)
    for name, (rect, off, on) in BUILDINGS.items():
        off, on = (lights.subsurface((x, y, rect[2], rect[3])) for x, y in (off, on))
        save(sheet.subsurface(rect), name)
        save(off, name + '_off')
        save(glow(off, on), name + '_glow')
    for name, (off, on) in LAMPS.items():
        on = street.subsurface(on)
        off = line_up(street.subsurface(off), on)
        save(off, name)
        save(off, name + '_off')
        save(glow(off, on), name + '_glow')
    for name, frames in TRAFFIC.items():
        for i, rect in enumerate(frames):
            save(street.subsurface(rect), f'{name}_{i}')
    print(f'{len(os.listdir(OUT))} pieces in {OUT}')


if __name__ == '__main__':
    main()
