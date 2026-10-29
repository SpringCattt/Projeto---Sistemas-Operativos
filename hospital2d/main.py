# main.py - Runs the simulator: python main.py
# Four processes, talking only through queues:
#   UI (this one, pygame)  <- snapshots --  simulation (sim.py: one thread per actor)  <- patients, surges --  arrivals
#                          -- commands ->                                              -- events ->  log (hospital.log)
# Controls: WASD/arrows or right-drag = move camera | wheel = zoom | click an NPC or a room = info card
#           L = event log on/off | ESC = close the card
import heapq
import multiprocessing as mp
import queue
from types import SimpleNamespace

import pygame

import config
from actors import ROLE_LABEL
from arrivals import run_arrivals
from art import CHAR_H, CHAR_W, character, draw_layout, furniture_sprites, image
from eventlog import run_log
from layout import build_layout
from sim import run_simulation

T = config.TILE
ZOOM_MIN, ZOOM_MAX = 1, 6  # integer zoom keeps the pixel art crisp
FEET = 6 / T  # how far below the tile centre the feet are drawn, in tiles
WALK_CYCLE = [0, 1, 2, 1]  # sheet columns: step, idle, step, idle
CARD_W, ROW_H, LIST_ROWS = 320, 20, 12  # info card width, row height, people listed at once
TEXT, MUTED, HOVER, HIGHLIGHT = (240, 240, 240), (150, 160, 175), (60, 66, 80), (250, 220, 60)
URGENCY_COLORS = [(70, 200, 90), (240, 200, 50), (235, 70, 70)]
BUBBLE_COLORS = {'!': (235, 70, 70), '?': (240, 190, 40), '~': (120, 170, 50), 'z': (90, 140, 230)}


def card_panel(screen, n_rows):
    panel = pygame.Surface((CARD_W, 16 + ROW_H * n_rows), pygame.SRCALPHA)
    panel.fill((20, 22, 28, 220))
    return panel, panel.get_rect(topright=(screen.get_width() - 10, 10))


def info_lines(a):
    return [('Name', a.name), ('ID', a.id), ('Role', ROLE_LABEL[a.role]), ('State', a.state), ('Room', a.room),
            *a.card]


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


def draw_events(screen, font, events):
    if not events:
        return
    panel = pygame.Surface((560, 10 + 18 * len(events)), pygame.SRCALPHA)
    panel.fill((20, 22, 28, 190))
    for i, (t, text) in enumerate(events):
        color = (255, 120, 110) if text.startswith(('SURGE', 'ERROR')) else (225, 225, 225)
        panel.blit(font.render(f'{int(t // 60):02d}:{int(t % 60):02d}  {text}'[:78], True, color), (8, 5 + 18 * i))
    screen.blit(panel, (10, screen.get_height() - 36 - panel.get_height()))


def run_ui(snapshots):
    pygame.init()
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    pygame.display.set_caption('Hospital')
    clock = pygame.time.Clock()
    font = pygame.font.SysFont('consolas', 15)
    small = pygame.font.SysFont('consolas', 13)
    bold = pygame.font.SysFont('consolas', 14, bold=True)
    banner_font = pygame.font.SysFont('consolas', 28, bold=True)
    room_font = pygame.font.SysFont('consolas', 13, bold=True)

    lay = build_layout()  # same config as the simulation process, so the same plan
    floor_map = draw_layout(lay)
    props = furniture_sprites(lay)
    vomit = image('custom/vomit')
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
    snap = {'actors': [], 'messes': [], 'events': [], 'stats': {}, 'time': 0.0, 'surge': False, 'sim_hz': 0.0,
            'line': 0, 'triage': 0, 'patients': 0}
    actors, selected_id, room_sel, list_open, scroll, show_log = [], None, None, False, 0, True
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
            snap = latest
            actors = [SimpleNamespace(**d) for d in snap['actors']]
        selected = next((a for a in actors if a.id == selected_id), None)

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                selected = selected_id = room_sel = None
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_l:
                show_log = not show_log
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

        for tx, ty in snap['messes']:  # on the floor, under everything else
            blit_scaled(vomit, *to_screen(tx, ty))

        if selected:
            for tx, ty in selected.path:
                x, y = to_screen(tx + 0.5, ty + 0.5)
                pygame.draw.rect(screen, HIGHLIGHT, (x - zoom, y - zoom, 2 * zoom, 2 * zoom))

        # Furniture and people together, back to front: whoever is behind something is covered by it
        bodies = sorted((((a.py + FEET) * T, None, a) for a in actors), key=lambda s: s[0])
        heads = []
        for item in heapq.merge(props, bodies, key=lambda s: s[0]):
            if item[1] is not None:  # furniture: (depth, image, x, y) in world pixels
                blit_scaled(item[1], (item[2] - cam_x) * zoom, (item[3] - cam_y) * zoom)
                continue
            a = item[2]
            # Feet a bit below the tile centre; the body sticks up over the tile behind (3/4 view)
            x, y = to_screen(a.px - CHAR_W / 2 / T, a.py + FEET - CHAR_H / T)
            blit_scaled(frame(a), x, y)
            heads.append((a, x + CHAR_W * zoom / 2, y))
            if a is selected:
                pygame.draw.rect(screen, HIGHLIGHT, (x, y, CHAR_W * zoom, CHAR_H * zoom), 2)

        if zoom >= 2:  # over the heads: urgency (triaged patients) and what's going on
            for a, hx, hy in heads:
                if a.mark is not None:
                    r = zoom + 1
                    pygame.draw.polygon(screen, URGENCY_COLORS[a.mark],
                                        [(hx, hy - 3 * r), (hx + r, hy - 2 * r), (hx, hy - r), (hx - r, hy - 2 * r)])
                    pygame.draw.polygon(screen, (30, 30, 30),
                                        [(hx, hy - 3 * r), (hx + r, hy - 2 * r), (hx, hy - r), (hx - r, hy - 2 * r)], 1)
                if a.bubble:
                    bx, by = int(hx + 4 * zoom), int(hy - 2 * zoom)
                    pygame.draw.circle(screen, (250, 250, 250), (bx, by), 9)
                    pygame.draw.circle(screen, BUBBLE_COLORS[a.bubble], (bx, by), 9, 2)
                    glyph = bold.render(a.bubble, True, BUBBLE_COLORS[a.bubble])
                    screen.blit(glyph, glyph.get_rect(center=(bx, by)))
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

        if snap['surge']:
            text = banner_font.render('SURGE! Lots of patients arriving', True, (255, 255, 255))
            box = text.get_rect(midtop=(sw // 2, 44)).inflate(24, 10)
            pygame.draw.rect(screen, (200, 40, 40), box, border_radius=6)
            screen.blit(text, text.get_rect(center=box.center))
        if show_log:
            draw_events(screen, small, snap['events'])

        fps, st, t = clock.get_fps(), snap['stats'], snap['time']
        hud = (f'FPS {fps:4.0f}   sim {snap["sim_hz"]:4.1f}/{config.ACTOR_HZ} Hz   time {int(t // 60):02d}:{int(t % 60):02d}'
               f'   patients {snap["patients"]}   treated {st.get("treated", 0)}   gave up {st.get("gave_up", 0)}'
               f'   line {snap["line"]}   triage {snap["triage"]}   zoom x{zoom}')
        slow = 0 < fps < config.MIN_FPS or 0 < snap['sim_hz'] < config.ACTOR_HZ * 0.8
        screen.blit(font.render(hud, True, (240, 90, 90) if slow else TEXT, (20, 22, 28)), (10, 10))
        screen.blit(font.render('WASD/right-drag: move   wheel: zoom   click: NPC or room info   L: log   ESC: close',
                                True, (200, 200, 200), (20, 22, 28)), (10, sh - 26))
        pygame.display.flip()


def main():
    snapshots, commands = mp.Queue(maxsize=2), mp.Queue()
    arrivals, log_queue, arrivals_stop = mp.Queue(), mp.Queue(), mp.Event()
    processes = [
        mp.Process(target=run_log, args=(log_queue,), name='log'),
        mp.Process(target=run_simulation, args=(snapshots, commands, arrivals, log_queue), name='simulation'),
        mp.Process(target=run_arrivals, args=(arrivals, arrivals_stop), name='arrivals'),
    ]
    for p in processes:
        p.daemon = True
        p.start()
    try:
        run_ui(snapshots)
    finally:  # closing the window (or a crash in the UI) always stops everything else too
        arrivals_stop.set()
        commands.put('quit')  # the simulation then sends the summary and closes the log
        for p in processes[::-1]:
            p.join(timeout=8)
            if p.is_alive():
                p.terminate()


if __name__ == '__main__':
    main()
