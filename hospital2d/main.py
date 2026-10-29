# main.py - Runs the simulator: python main.py
# Two processes: this one is the UI (pygame), sim.py is the simulation (one thread per actor).
# The UI never touches the actors: it draws the latest snapshot the simulation sends through a Queue.
# Controls: WASD/arrows or right-drag = move camera | wheel = zoom | click an NPC or a room = info card | ESC = close
import heapq
import multiprocessing as mp
import queue
from types import SimpleNamespace

import pygame

import config
from actors import ROLE_LABEL
from art import CHAR_H, CHAR_W, character, draw_layout, furniture_sprites
from layout import build_layout
from sim import run_simulation

T = config.TILE
ZOOM_MIN, ZOOM_MAX = 1, 6  # integer zoom keeps the pixel art crisp
FEET = 6 / T  # how far below the tile centre the feet are drawn, in tiles
WALK_CYCLE = [0, 1, 2, 1]  # sheet columns: step, idle, step, idle
CARD_W, ROW_H, LIST_ROWS = 320, 20, 12  # info card width, row height, people listed at once
TEXT, MUTED, HOVER, HIGHLIGHT = (240, 240, 240), (150, 160, 175), (60, 66, 80), (250, 220, 60)


def card_panel(screen, n_rows):
    panel = pygame.Surface((CARD_W, 16 + ROW_H * n_rows), pygame.SRCALPHA)
    panel.fill((20, 22, 28, 220))
    return panel, panel.get_rect(topright=(screen.get_width() - 10, 10))


def info_lines(a):
    return [('Name', a.name), ('ID', a.id), ('Role', ROLE_LABEL[a.role]), ('State', a.state), ('Room', a.room),
            *a.extra]


def draw_card(screen, font, lines):
    panel, rect = card_panel(screen, len(lines))
    for i, (k, v) in enumerate(lines):
        panel.blit(font.render(f'{k}:', True, MUTED), (12, 8 + ROW_H * i))
        panel.blit(font.render(str(v), True, TEXT), (110, 8 + ROW_H * i))
    screen.blit(panel, rect)
    return [(rect, ('card',))]


def draw_room_card(screen, font, room, people, list_open, scroll):
    """Room name, how many people are in it and, when opened, who (click someone to select them).
    Returns the clickable areas as [(screen rect, action)], most specific first."""
    shown = people[scroll:scroll + LIST_ROWS] if list_open else []
    panel, rect = card_panel(screen, 3 + len(shown))
    mouse = pygame.mouse.get_pos()
    hits = []

    def row(i, text, color, action=None):
        r = pygame.Rect(rect.x, rect.y + 8 + ROW_H * i - 2, CARD_W, ROW_H)
        if action and r.collidepoint(mouse):
            panel.fill(HOVER, (0, r.y - rect.y, CARD_W, ROW_H))
        panel.blit(font.render(text, True, color), (12, r.y - rect.y + 2))
        if action:
            hits.append((r, action))

    row(0, f'Room:    {room.name}', TEXT)
    row(1, f'NPCs:    {len(people)}', TEXT)
    if list_open:
        more = f'   {scroll + 1}-{scroll + len(shown)} of {len(people)} (wheel)' if len(people) > LIST_ROWS else ''
        row(2, '[-] hide list' + more, HIGHLIGHT, ('toggle',))
    else:
        row(2, '[+] show list', HIGHLIGHT, ('toggle',))
    for i, a in enumerate(shown):
        row(3 + i, f'#{a.id:<4} {a.name} · {ROLE_LABEL[a.role]}', TEXT, ('pick', a.id))
    screen.blit(panel, rect)
    return hits + [(rect, ('card',))]


def run_ui(snapshots):
    pygame.init()
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    pygame.display.set_caption('Hospital')
    clock = pygame.time.Clock()
    font = pygame.font.SysFont('consolas', 15)
    room_font = pygame.font.SysFont('consolas', 13, bold=True)

    lay = build_layout()  # same config as the simulation process, so the same plan
    floor_map = draw_layout(lay)
    props = furniture_sprites(lay)
    room_labels = [(r, room_font.render(r.name, True, (40, 44, 52))) for r in lay.rooms if r.kind != 'street']
    looks = {}  # character sheet index -> frames

    def frame(a):
        if a.look not in looks:
            looks[a.look] = character(a.look)
        return looks[a.look][a.facing][WALK_CYCLE[int(a.anim_t * 6) % 4] if a.path else 1]

    zoom = 3
    rec = lay.rooms_of('reception')[0]
    cam_x = (rec.x + rec.w / 2) * T - screen.get_width() / zoom / 2
    cam_y = (rec.y + rec.h / 2) * T - screen.get_height() / zoom / 2
    actors, selected_id, room_sel, list_open, scroll = [], None, None, False, 0
    dragging, cache, hits, people = False, {}, [], []

    while True:
        dt = min(clock.tick(config.TARGET_FPS) / 1000, 0.1)
        latest = None
        try:  # only the newest snapshot matters
            while True:
                latest = snapshots.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            actors = [SimpleNamespace(**d) for d in latest]
        selected = next((a for a in actors if a.id == selected_id), None)

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                selected = selected_id = room_sel = None
            elif ev.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if room_sel and list_open and any(r.collidepoint(mx, my) for r, _ in hits):  # scroll the list
                    scroll = max(0, min(len(people) - LIST_ROWS, scroll - ev.y))
                else:  # zoom around the mouse
                    wx, wy = cam_x + mx / zoom, cam_y + my / zoom
                    zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom + ev.y))
                    cam_x, cam_y = wx - mx / zoom, wy - my / zoom
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button in (2, 3):
                dragging = True
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button in (2, 3):
                dragging = False
            elif ev.type == pygame.MOUSEMOTION and dragging:
                cam_x -= ev.rel[0] / zoom
                cam_y -= ev.rel[1] / zoom
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                action = next((act for r, act in hits if r.collidepoint(ev.pos)), None)
                if action and action[0] == 'toggle':
                    list_open, scroll = not list_open, 0
                elif action and action[0] == 'pick':
                    selected_id, room_sel = action[1], None
                elif not action:  # a click on the map: an NPC first, else the room under the mouse
                    wx, wy = (cam_x + ev.pos[0] / zoom) / T, (cam_y + ev.pos[1] / zoom) / T
                    # Compare with the middle of the body, which is drawn above the actor's tile
                    dist2 = lambda a: (a.px - wx) ** 2 + (a.py + FEET - CHAR_H / T / 2 - wy) ** 2
                    nearest = min(actors, key=dist2, default=None)
                    selected_id = nearest.id if nearest and dist2(nearest) < 0.8 ** 2 else None
                    tx, ty = int(wx), int(wy)
                    inside = 0 <= tx < lay.width and 0 <= ty < lay.height
                    room_sel = lay.room_at[ty][tx] if selected_id is None and inside else None
                    list_open, scroll = False, 0
                selected = next((a for a in actors if a.id == selected_id), None)

        keys = pygame.key.get_pressed()
        speed = 700 * dt / zoom
        cam_x += ((keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])) * speed
        cam_y += ((keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])) * speed

        # Map: crop the visible part and scale it without smoothing
        sw, sh = screen.get_size()
        screen.fill((30, 34, 40))
        src = pygame.Rect(int(cam_x), int(cam_y), int(sw / zoom) + 2, int(sh / zoom) + 2).clip(floor_map.get_rect())
        if src.w > 0 and src.h > 0:
            part = pygame.transform.scale(floor_map.subsurface(src), (src.w * zoom, src.h * zoom))
            screen.blit(part, ((src.x - cam_x) * zoom, (src.y - cam_y) * zoom))

        def to_screen(tx, ty):
            return (tx * T - cam_x) * zoom, (ty * T - cam_y) * zoom

        def blit_scaled(img, x, y):
            w, h = img.get_width() * zoom, img.get_height() * zoom
            if -w < x < sw and -h < y < sh:
                key = (id(img), zoom)
                if key not in cache:
                    cache[key] = pygame.transform.scale(img, (w, h))
                screen.blit(cache[key], (x, y))

        if selected:
            for tx, ty in selected.path:
                x, y = to_screen(tx + 0.5, ty + 0.5)
                pygame.draw.rect(screen, HIGHLIGHT, (x - zoom, y - zoom, 2 * zoom, 2 * zoom))

        # Furniture and people together, back to front: whoever is behind something is covered by it
        bodies = sorted((((a.py + FEET) * T, None, a) for a in actors), key=lambda s: s[0])
        for item in heapq.merge(props, bodies, key=lambda s: s[0]):
            if item[1] is not None:  # furniture: (depth, image, x, y) in world pixels
                blit_scaled(item[1], (item[2] - cam_x) * zoom, (item[3] - cam_y) * zoom)
                continue
            a = item[2]
            # Feet a bit below the tile centre; the body sticks up over the tile behind (3/4 view)
            x, y = to_screen(a.px - CHAR_W / 2 / T, a.py + FEET - CHAR_H / T)
            blit_scaled(frame(a), x, y)
            if a is selected:
                pygame.draw.rect(screen, HIGHLIGHT, (x, y, CHAR_W * zoom, CHAR_H * zoom), 2)

        if zoom >= 2:
            for r, label in room_labels:
                screen.blit(label, to_screen(r.x + 1.2, r.y + 1.1))

        hits = []
        if selected:
            hits = draw_card(screen, font, info_lines(selected))
        elif room_sel:
            x, y = to_screen(room_sel.x, room_sel.y)
            pygame.draw.rect(screen, HIGHLIGHT, (x, y, room_sel.w * T * zoom, room_sel.h * T * zoom), 2)
            people = sorted((a for a in actors if lay.room_at[a.ty][a.tx] is room_sel),
                            key=lambda a: (ROLE_LABEL[a.role], a.name))
            scroll = max(0, min(scroll, len(people) - LIST_ROWS))
            hits = draw_room_card(screen, font, room_sel, people, list_open, scroll)

        fps = clock.get_fps()
        hud = f'FPS {fps:4.0f}   actors {len(actors)} (threads)   doctors {config.MAX_DOCTORS}   zoom x{zoom}'
        screen.blit(font.render(hud, True, (240, 90, 90) if 0 < fps < config.MIN_FPS else TEXT,
                                (20, 22, 28)), (10, 10))
        screen.blit(font.render('WASD/right-drag: move   wheel: zoom   click: NPC or room info   ESC: close', True,
                                (200, 200, 200), (20, 22, 28)), (10, sh - 26))
        pygame.display.flip()


def main():
    snapshots, commands = mp.Queue(maxsize=2), mp.Queue()
    sim = mp.Process(target=run_simulation, args=(snapshots, commands), name='simulation', daemon=True)
    sim.start()
    try:
        run_ui(snapshots)
    finally:  # closing the window (or a crash in the UI) always stops the simulation too
        commands.put('quit')
        sim.join(timeout=3)
        if sim.is_alive():
            sim.terminate()


if __name__ == '__main__':
    main()
