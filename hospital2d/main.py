# main.py - Runs the simulator: python main.py
# Four processes, talking through queues, plus shared memory for where everyone is (sim.write_positions):
#   UI (this one, pygame)  <- positions, words --  simulation (sim.py: one thread per actor)  <- patients, surges --  arrivals
#                          -- commands ->                                              -- events ->  log (hospital.log)
# Controls: WASD/arrows or right-drag = move camera | wheel = zoom | click an NPC or a room = info card
#           L = event log on/off | B = what the dispatcher (Boss) is doing | ESC = close the card
#           Tab = admin tablet (events, patients, staff) | Space = pause | +/- = speed
import heapq
import multiprocessing as mp
import os
import queue
import time
from types import SimpleNamespace

import pygame

import config
from actors import ACTIONS, FACINGS, ROLE_LABEL
from arrivals import run_arrivals
from art import ASSETS, BUBBLE_TAIL_X, CHAR_H, CHAR_W, character, draw_layout, furniture_sprites, icon_bubble, image, \
    lying, text_bubble
from eventlog import run_log
from layout import load_layout
from sim import new_positions, read_positions, run_simulation

T = config.TILE
ZOOM_MIN, ZOOM_MAX = 1, 6  # integer zoom keeps the pixel art crisp
FEET = 6 / T  # how far below the tile centre the feet are drawn, in tiles
WALK_CYCLE = [0, 1, 2, 1]  # sheet columns: step, idle, step, idle
CARD_W, ROW_H, LIST_ROWS = 320, 20, 12  # info card width, row height, people listed at once
TEXT, MUTED, HOVER, HIGHLIGHT = (240, 240, 240), (150, 160, 175), (60, 66, 80), (250, 220, 60)
URGENCY_COLORS = [(70, 200, 90), (240, 200, 50), (235, 70, 70)]


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


def draw_panel(screen, font, title, lines, topright, width=CARD_W):
    """A titled list of (text, colour) lines under the info card. Returns the bottom of the panel."""
    panel = pygame.Surface((width, 30 + 18 * len(lines)), pygame.SRCALPHA)
    panel.fill((20, 22, 28, 215))
    panel.blit(font.render(title, True, HIGHLIGHT), (10, 6))
    for i, (text, color) in enumerate(lines):
        panel.blit(font.render(text[:width // 7], True, color), (10, 26 + 18 * i))
    rect = panel.get_rect(topright=topright)
    screen.blit(panel, rect)
    return rect.bottom


def draw_day_report(screen, font, small, r):
    """The end of the day: what came in, what the staff cost, the profit and who got the biggest bonus. The world
    waits, paused, until the button (or Enter) starts the next day. Returns the clickable areas."""
    green, red = (110, 220, 120), (240, 110, 100)
    lines = [(f'Treated {r["treated"]:.0f}   gave up {r["gave_up"]:.0f}   thrown out {r["expelled"]:.0f}'
              f'   surgeries {r["surgeries"]:.0f}', TEXT),
             (f'Staff called in by the Boss: {r["called_in"]:.0f}', TEXT),
             ('', TEXT),
             (f'Patients paid      {r["income"]:10,.0f}', green),
             (f'Wages              {-r["wages"]:10,.0f}', red),
             (f'Profit             {r["profit"]:10,.0f}', green if r['profit'] >= 0 else red),
             (f'Staff bonus ({100 * config.BONUS_SHARE:.0f}%)  {r["bonus"]:10,.0f}', HIGHLIGHT),
             ('', TEXT)]
    lines += [(f'  {amount:7,.0f}  {who}', TEXT) for amount, who in r['staff_bonus'][:5]]
    sw, sh = screen.get_size()
    w, h = 480, 90 + 22 * len(lines)
    box = pygame.Rect(0, 0, w, h)
    box.center = (sw // 2, sh // 2)
    panel = pygame.Surface(box.size, pygame.SRCALPHA)
    panel.fill((20, 22, 28, 235))
    screen.blit(panel, box)
    pygame.draw.rect(screen, HIGHLIGHT, box, 2, border_radius=6)
    title = font.render(f'End of day {r["day"]}', True, HIGHLIGHT)
    screen.blit(title, title.get_rect(midtop=(box.centerx, box.y + 12)))
    for i, (text, color) in enumerate(lines):
        screen.blit(font.render(text, True, color), (box.x + 24, box.y + 44 + 22 * i))
    go = pygame.Rect(0, 0, 220, 30)
    go.midbottom = (box.centerx, box.bottom - 12)
    return [(button(screen, font, go, f'Start day {r["day"] + 1} (Enter)', on=True), ('cmd', ('next_day',)))]


def draw_dispatcher(screen, font, boss, to_screen, by_id, shown):
    """On the map: a line from each busy member of staff to its task (urgency colour), and a pulsing ring on
    every task still waiting for someone. When a task is about a person, it follows that person smoothly.
    Only what's on the floor on screen (shown(x): is x on it)."""
    pulse = 6 + 3 * abs((pygame.time.get_ticks() // 60) % 10 - 5) / 5

    def where(about_id, tile):
        who = by_id.get(about_id)
        x, y = (who.px, who.py) if who is not None else (tile[0] + 0.5, tile[1] + 0.5)
        return to_screen(x, y) if shown(x) else None

    for actor_id, about_id, tile, urgency, _ in boss['working']:
        a, end = by_id.get(actor_id), where(about_id, tile)
        if a is not None and end is not None and shown(a.px):
            pygame.draw.line(screen, URGENCY_COLORS[urgency], to_screen(a.px, a.py), end, 3)
            pygame.draw.circle(screen, URGENCY_COLORS[urgency], end, 6)
    for about_id, tile, urgency, _ in boss['open']:
        spot = where(about_id, tile)
        if spot is not None:
            pygame.draw.circle(screen, URGENCY_COLORS[urgency], spot, pulse * 2, 3)


def draw_events(screen, font, events):
    if not events:
        return
    panel = pygame.Surface((560, 10 + 18 * len(events)), pygame.SRCALPHA)
    panel.fill((20, 22, 28, 190))
    for i, (t, text) in enumerate(events):
        color = (255, 120, 110) if text.startswith(('SURGE', 'ERROR')) else (225, 225, 225)
        panel.blit(font.render(f'{int(t // 60):02d}:{int(t % 60):02d}  {text}'[:78], True, color), (8, 5 + 18 * i))
    screen.blit(panel, (10, screen.get_height() - 36 - panel.get_height()))


JOB_LABELS = [('Doctor', 'Doctors'), ('WardNurse', 'Ward nurses'), ('TriageNurse', 'Triage nurses'),
              ('Receptionist', 'Receptionists'), ('Security', 'Security'), ('Cleaner', 'Cleaners')]
TABLET_W = 340


def button(screen, font, rect, text, color=(58, 64, 80), on=False):
    pygame.draw.rect(screen, HIGHLIGHT if on else color, rect, border_radius=4)
    label = font.render(text, True, (20, 22, 28) if on else TEXT)
    screen.blit(label, label.get_rect(center=rect.center))
    return rect


def draw_tablet(screen, font, small, snap, opts, top):
    """The admin tablet: changes the world while it runs. Returns the clickable areas as [(rect, action)]."""
    x0, w = 10, TABLET_W
    frame = pygame.Rect(x0, top, w, 36 + 4 * 26 + 5 * 28 + 22 + len(JOB_LABELS) * 28 + 6)  # headings, rows, lines
    pygame.draw.rect(screen, (12, 13, 17), frame.inflate(10, 10), border_radius=14)  # the tablet's bezel
    pygame.draw.rect(screen, (28, 31, 39), frame, border_radius=8)
    hits = [(frame.inflate(10, 10), ('card',))]
    screen.blit(font.render('Admin tablet', True, HIGHLIGHT), (x0 + 12, top + 8))
    y = top + 36

    def heading(text):
        nonlocal y
        screen.blit(small.render(text, True, MUTED), (x0 + 12, y + 6))
        y += 26

    def row(*buttons):  # (text, action, colour, on): side by side, sharing the width
        nonlocal y
        bw = (w - 24 - 6 * (len(buttons) - 1)) // len(buttons)
        for i, (text, action, color, on) in enumerate(buttons):
            r = button(screen, small, pygame.Rect(x0 + 12 + i * (bw + 6), y, bw, 22), text, color, on)
            hits.append((r, action))
        y += 28

    grey = (58, 64, 80)
    heading('World')
    paused = snap['paused']
    row(('Resume' if paused else 'Pause', ('cmd', ('pause',)), (190, 60, 60) if paused else grey, False),
        ('Slower', ('cmd', ('speed', -1)), grey, False), ('Faster', ('cmd', ('speed', 1)), grey, False))
    screen.blit(small.render(f'speed x{snap["speed"]:g}' + ('  (paused)' if paused else ''), True, TEXT),
                (x0 + 14, y))
    y += 22
    heading('Patients (click to send)')
    row(*((f'x{n}', ('opt', 'count', n), grey, opts['count'] == n) for n in (1, 5, 20)))
    row(('At the door', ('opt', 'triaged', False), grey, not opts['triaged']),
        ('Already triaged', ('opt', 'triaged', True), grey, opts['triaged']))
    send = lambda u, s: ('cmd', ('patient', u, s, opts['count'], opts['triaged']))
    dark = [tuple(c // 2 for c in col) for col in URGENCY_COLORS]
    row(('Green', send(0, False), dark[0], False), ('Yellow', send(1, False), dark[1], False),
        ('Red', send(2, False), dark[2], False), ('Red+surg.', send(2, True), dark[2], False))
    heading('Events')
    row(('Surge', ('cmd', ('surge',)), grey, False), ('Fight', ('cmd', ('fight',)), grey, False),
        ('Vomit', ('cmd', ('vomit',)), grey, False))
    heading('Staff (on shift / places)')
    for job, label in JOB_LABELS:
        n, most = snap['staff'].get(job, (0, None))
        screen.blit(small.render(label, True, TEXT), (x0 + 14, y + 4))
        screen.blit(small.render(f'{n}' + (f' / {most}' if most is not None else ''), True, TEXT), (x0 + 200, y + 4))
        hits.append((button(screen, small, pygame.Rect(x0 + 150, y, 34, 22), '-'), ('cmd', ('fire', job))))
        hits.append((button(screen, small, pygame.Rect(x0 + 260, y, 34, 22), '+'), ('cmd', ('hire', job))))
        y += 28
    return hits


def run_ui(snapshots, commands, positions, sim=None):
    """commands: Queue -> simulation, for the admin tablet (None: the tablet does nothing). positions: the shared
    block the simulation writes (sim.write_positions). sim: the simulation process, to notice if it stops."""
    send = commands.put if commands is not None else (lambda cmd: None)
    pygame.init()
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    pygame.display.set_caption('Hospital')
    clock = pygame.time.Clock()
    font = pygame.font.SysFont('consolas', 15)
    small = pygame.font.SysFont('consolas', 13)
    banner_font = pygame.font.SysFont('consolas', 28, bold=True)
    # m5x7 (Daniel Linssen, CC0) at its native size, without antialiasing, scaled up with the rest: pixel text
    bubble_font = pygame.font.Font(os.path.join(ASSETS, 'fonts', 'm5x7.ttf'), 16)
    bubbles = {}  # what's said (or an icon) -> its speech bubble

    lay = load_layout()  # the same map the simulation process loads
    floor_map = draw_layout(lay)
    props = furniture_sprites(lay)
    props = [[p for p in props if lay.floor_of(p[2] / T) == f] for f in range(lay.floors)]  # each floor's
    vomit = image('custom/vomit')
    looks = {}  # character sheet index -> frames

    def frame(a):
        if a.look not in looks:
            looks[a.look] = character(a.look)
        return looks[a.look][a.facing][WALK_CYCLE[int(a.anim_t * 6) % 4] if a.path else 1]

    lying_imgs = {}

    def lying_img(a):
        across = a.stretcher and a.facing in ('left', 'right')
        if (a.look, across) not in lying_imgs:
            frame(a)
            lying_imgs[(a.look, across)] = lying(looks[a.look], across)
        return lying_imgs[(a.look, across)]

    stretcher = {False: image('custom/stretcher_v'), True: image('custom/stretcher_h')}
    keyboard = [image('custom/keyboard_0'), image('custom/keyboard_1')]
    mop, bucket = [image(f'custom/mop_{i}') for i in (0, 1, 2, 1)], image('custom/bucket')

    def centred(img, x, y, depth):  # (depth, image, world x, world y) like furniture, centred on (x, y) in tiles
        return depth, img, x * T - img.get_width() // 2, y * T - img.get_height() // 2

    zoom = 3
    centre = lay.rooms_of('control')[0]  # the hospital is built around it
    cam_x = (centre.x + centre.w / 2) * T - screen.get_width() / zoom / 2
    cam_y = (centre.y + centre.h / 2) * T - screen.get_height() / zoom / 2
    snap = {'actors': [], 'messes': [], 'events': [], 'stats': {}, 'time': 0.0, 'surge': False, 'sim_hz': 0.0,
            'line': 0, 'triage': 0, 'patients': 0, 'keep_up': 1.0, 'speed': config.TIME_SCALE, 'paused': False,
            'staff': {}, 'day': 0, 'hour': 0.0, 'closed': False, 'day_over': None, 'today': {}}
    show_tablet, opts, beat = False, {'count': 1, 'triaged': False}, 0.0
    actors, selected_id, room_sel, list_open, scroll, show_log, show_boss = [], None, None, False, 0, True, False
    view, sel_floor = 0, None  # the floor on screen; the one the selected person was on
    version, rows = None, {}  # the positions block's version we have, and its rows
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
        # Where everyone is, fresh from shared memory every frame (the words above come less often). Whoever has
        # no row has just left, or has just come in and isn't written yet: not drawn this frame
        version, fresh = read_positions(positions, version)  # (None: nothing new since the last frame)
        if fresh is not None or latest is not None:
            rows = fresh if fresh is not None else rows
            actors = [a for a in actors if a.id in rows]
            for a in actors:
                _, a.px, a.py, a.tx, a.ty, facing, a.anim_t, a.lying, a.stretcher, action, cx, cy, cf = rows[a.id]
                a.facing, a.action = FACINGS[facing], ACTIONS[action]
                a.cart = (cx, cy, FACINGS[cf]) if cf >= 0 else None
        selected = next((a for a in actors if a.id == selected_id), None)

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                selected = selected_id = room_sel = None
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_l:
                show_log = not show_log
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_b:
                show_boss = not show_boss
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_TAB:
                show_tablet = not show_tablet
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                new = max(0, min(lay.floors - 1, view + (1 if ev.key == pygame.K_PAGEUP else -1)))
                cam_x, view = cam_x + (new - view) * lay.stride * T, new  # same place, another floor
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and snap['day_over']:
                send(('next_day',))
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_SPACE:
                send(('pause',))
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                send(('speed', 1))
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                send(('speed', -1))
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
                elif action and action[0] == 'cmd':
                    send(action[1])
                elif action and action[0] == 'opt':
                    opts[action[1]] = action[2]
                elif action and action[0] == 'floor':
                    cam_x, view = cam_x + (action[1] - view) * lay.stride * T, action[1]
                elif action and action[0] == 'tablet':
                    show_tablet = not show_tablet
                elif action and action[0] == 'pick':
                    selected_id, room_sel = action[1], None
                elif not action:  # a click on the map: an NPC first, else the room under the mouse
                    wx, wy = (cam_x + ev.pos[0] / zoom) / T, (cam_y + ev.pos[1] / zoom) / T
                    # Compare with the middle of the body, which is drawn above the actor's tile
                    dist2 = lambda a: (a.px - wx) ** 2 + (a.py + FEET - CHAR_H / T / 2 - wy) ** 2
                    nearest = min((a for a in actors if lay.floor_of(a.px) == view), key=dist2, default=None)
                    selected_id = nearest.id if nearest and dist2(nearest) < 0.8 ** 2 else None
                    tx, ty = int(wx), int(wy)
                    inside = 0 <= tx < lay.width and 0 <= ty < lay.height
                    room_sel = lay.room_at[ty][tx] if selected_id is None and inside else None
                    list_open, scroll = False, 0
                selected = next((a for a in actors if a.id == selected_id), None)

        if selected is not None:  # it went up or down: the view goes with it
            f = lay.floor_of(selected.px)
            if sel_floor is not None and f != sel_floor and f != view:
                cam_x, view = cam_x + (f - view) * lay.stride * T, f
            sel_floor = f
        else:
            sel_floor = None
        here = [a for a in actors if lay.floor_of(a.px) == view]  # who's on the floor on screen
        shown = lambda x: lay.floor_of(x) == view

        keys = pygame.key.get_pressed()
        speed = 700 * dt / zoom
        cam_x += ((keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])) * speed
        cam_y += ((keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])) * speed

        # Map: crop the visible part and scale it without smoothing
        sw, sh = screen.get_size()
        screen.fill((30, 34, 40))
        src = pygame.Rect(int(cam_x), int(cam_y), int(sw / zoom) + 2, int(sh / zoom) + 2).clip(
            pygame.Rect(view * lay.stride * T, 0, lay.stride * T, lay.height * T))  # only this floor
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
            if shown(tx):
                blit_scaled(vomit, *to_screen(tx, ty))

        if selected:
            for tx, ty in (t for t in selected.path if shown(t[0])):
                x, y = to_screen(tx + 0.5, ty + 0.5)
                pygame.draw.rect(screen, HIGHLIGHT, (x - zoom, y - zoom, 2 * zoom, 2 * zoom))

        # Furniture and people together, back to front: whoever is behind something is covered by it
        if not snap['paused']:  # the little work animations freeze too
            beat = time.perf_counter()
        bodies = sorted((((a.py + (1.7 if a.lying else FEET)) * T, None, a) for a in here), key=lambda s: s[0])
        extras = [centred(stretcher[facing in ('left', 'right')], x, y, (y + 0.5) * T)  # parked in storage
                  for x, y, facing in snap.get('parked', []) if shown(x)]  # stretchers, keyboards, buckets too
        for a in here:
            if a.stretcher:
                extras.append(centred(stretcher[a.facing in ("left", "right")], a.px, a.py, (a.py + 1.7) * T - 1))
            if a.cart:
                x, y, facing = a.cart  # the empty stretcher it's moving
                extras.append(centred(stretcher[facing in ('left', 'right')], x, y, (y + 0.5) * T))
            if a.action == 'typing':  # on the counter, beside its screen (the patient stands in front of the screen)
                extras.append(centred(keyboard[int(beat * 8) % 2], a.px + 0.9, a.ty + 1.35, (a.ty + 2) * T + 1))
            if a.action == 'mopping':
                extras.append(centred(bucket, a.px + 0.8, a.py + 0.2, (a.py + FEET) * T))
                extras.append(centred(mop[int(beat * 5) % 4], a.px - 0.35, a.py, (a.py + FEET) * T + 1))
        extras.sort(key=lambda s: s[0])
        heads = []
        for item in heapq.merge(props[view], extras, bodies, key=lambda s: s[0]):
            if item[1] is not None:  # furniture: (depth, image, x, y) in world pixels
                blit_scaled(item[1], (item[2] - cam_x) * zoom, (item[3] - cam_y) * zoom)
                continue
            a = item[2]
            # Feet a bit below the tile centre; the body sticks up over the tile behind (3/4 view)
            if a.lying:
                img = lying_img(a)
                x, y = to_screen(a.px - img.get_width() / 2 / T, a.py - img.get_height() / 2 / T - 0.15)
                blit_scaled(img, x, y)
                y -= (CHAR_H - img.get_height()) * zoom  # bubbles and marks above the head as usual
            else:
                x, y = to_screen(a.px - CHAR_W / 2 / T, a.py + FEET - CHAR_H / T)
                if a.action == 'typing':  # bobs with the keystrokes
                    y -= zoom * (int(beat * 8) % 2)
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
                key = ('say', a.speech) if a.speech else ('icon', a.bubble) if a.bubble else None
                if key is not None:  # words win over an icon
                    if key not in bubbles:
                        bubbles[key] = text_bubble(bubble_font, a.speech) if a.speech else icon_bubble(a.bubble)
                    img = bubbles[key]
                    # the tail's tip just above the head (and its urgency mark), a little to the right
                    blit_scaled(img, hx + (2 - BUBBLE_TAIL_X) * zoom, hy - (img.get_height() + 3) * zoom)

        boss = next((a for a in actors if a.role == 'dispatcher'), None)
        watching_boss = boss is not None and (show_boss or selected is boss)
        if watching_boss:
            draw_dispatcher(screen, font, boss.boss, to_screen, {a.id: a for a in actors}, shown)

        hits = []
        if selected:
            hits = draw_card(screen, font, info_lines(selected))
        if room_sel and not selected:
            x, y = to_screen(room_sel.x, room_sel.y)
            pygame.draw.rect(screen, HIGHLIGHT, (x, y, room_sel.w * T * zoom, room_sel.h * T * zoom), 2)
            people = sorted((a for a in actors if lay.room_at[a.ty][a.tx] is room_sel),
                            key=lambda a: (ROLE_LABEL[a.role], a.name))
            scroll = max(0, min(scroll, len(people) - LIST_ROWS))
            hits = draw_room_card(screen, font, room_sel, people, list_open, scroll)
        if watching_boss:  # what it's juggling and why it decided what it did
            top = hits[0][0].bottom + 6 if hits else 10
            working = [(f'{label}  <- {next((a.name for a in actors if a.id == i), "?")}',
                        URGENCY_COLORS[u]) for i, _, _, u, label in boss.boss['working']]
            waiting = [(label, URGENCY_COLORS[u]) for _, _, u, label in boss.boss['open']] or [('nothing', MUTED)]
            decisions = [(text, TEXT)
                         for t, text in boss.boss['decisions'][::-1]] or [('nothing yet', MUTED)]
            def first(lines, n):  # keeps the panels on screen
                return lines[:n] + ([(f'... and {len(lines) - n} more', MUTED)] if len(lines) > n else [])

            top = draw_panel(screen, small, 'Boss: being done now', first(working or [('nothing', MUTED)], 6),
                             (sw - 10, top), 560) + 6
            top = draw_panel(screen, small, 'Boss: waiting for someone', first(waiting, 6), (sw - 10, top), 560) + 6
            draw_panel(screen, small, 'Boss: last decisions (newest first)', decisions[:5], (sw - 10, top), 560)

        if sim is not None and not sim.is_alive():  # it crashed: say so, instead of a world frozen for no reason
            text = banner_font.render('The simulation stopped (see the terminal and hospital.log)', True, TEXT)
            box = text.get_rect(midtop=(sw // 2, 90)).inflate(24, 10)
            pygame.draw.rect(screen, (120, 30, 30), box, border_radius=6)
            screen.blit(text, text.get_rect(center=box.center))
        if snap['surge']:
            text = banner_font.render('SURGE! Lots of patients arriving', True, (255, 255, 255))
            box = text.get_rect(midtop=(sw // 2, 44)).inflate(24, 10)
            pygame.draw.rect(screen, (200, 40, 40), box, border_radius=6)
            screen.blit(text, text.get_rect(center=box.center))
        if show_log and not show_tablet:
            draw_events(screen, small, snap['events'])
        if snap['day_over']:  # over everything: nothing goes on until the next day starts
            hits = draw_day_report(screen, font, small, snap['day_over'])
        elif show_tablet:
            hits += draw_tablet(screen, font, small, snap, opts, 46)
        else:
            hits.append((button(screen, small, pygame.Rect(10, 38, 150, 22), 'Admin tablet (Tab)'), ('tablet',)))
            for f in range(lay.floors):  # top floor on the left
                rect = pygame.Rect(170 + (lay.floors - 1 - f) * 74, 38, 68, 22)
                hits.append((button(screen, small, rect, f'Floor {f}', on=f == view), ('floor', f)))

        fps, st, hour, today = clock.get_fps(), snap['stats'], snap['hour'], snap['today']
        hud = (f'FPS {fps:4.0f}   sim {100 * snap["keep_up"]:3.0f}%   day {snap["day"]} '
               f'{int(hour):02d}:{int(hour % 1 * 60):02d}{" closed" if snap["closed"] else ""}'
               f'   today {today.get("income", 0) - today.get("wages", 0):+,.0f}   floor {view}'
               f'   patients {snap["patients"]}   treated {st.get("treated", 0)}   gave up {st.get("gave_up", 0)}'
               f'   line {snap["line"]}   triage {snap["triage"]}   zoom x{zoom}'
               f'   {"PAUSED" if snap["paused"] else "speed x" + format(snap["speed"], "g")}')
        slow = 0 < fps < config.MIN_FPS or snap['keep_up'] < 0.8
        screen.blit(font.render(hud, True, (240, 90, 90) if slow else TEXT, (20, 22, 28)), (10, 10))
        screen.blit(font.render('WASD/right-drag: move   wheel: zoom   PgUp/PgDn: floor   click: NPC or room info   '
                                'L: log   B: boss   Tab: admin   Space: pause   +/-: speed   ESC: close',
                                True, (200, 200, 200), (20, 22, 28)), (10, sh - 26))
        pygame.display.flip()


def main():
    snapshots, commands = mp.Queue(maxsize=2), mp.Queue()
    speed = mp.Value('d', config.TIME_SCALE)  # simulated seconds per real second: the simulation sets it (pause, speed)
    positions = new_positions()  # where everyone is: shared memory, written by the simulation, read by the UI
    arrivals, log_queue, arrivals_stop = mp.Queue(), mp.Queue(), mp.Event()
    open_ = mp.Event()  # the doors are open (the simulation sets it; the arrivals process sleeps on it at night)
    open_.set()
    processes = [
        mp.Process(target=run_log, args=(log_queue,), name='log'),
        mp.Process(target=run_simulation, args=(snapshots, commands, arrivals, log_queue, None, speed, positions,
                                                open_), name='simulation'),
        mp.Process(target=run_arrivals, args=(arrivals, arrivals_stop, None, speed, open_), name='arrivals'),
    ]
    for p in processes:
        p.daemon = True
        p.start()
    try:
        run_ui(snapshots, commands, positions, processes[1])
    finally:  # closing the window (or a crash in the UI) always stops everything else too
        arrivals_stop.set()
        commands.put('quit')  # the simulation then sends the summary and closes the log
        for p in processes[::-1]:
            p.join(timeout=8)
            if p.is_alive():
                p.terminate()


if __name__ == '__main__':
    main()
