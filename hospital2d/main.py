# main.py - Runs the simulator: python main.py
# Controls: WASD/arrows or right-drag = move camera | wheel = zoom | click = actor card | ESC = close card
import random

import pygame

import config
from actors import create_actors
from art import CHAR_H, CHAR_W, draw_layout
from layout import build_layout

T = config.TILE
ZOOM_MIN, ZOOM_MAX = 1, 6  # integer zoom keeps the pixel art crisp
FEET = 6 / T  # how far below the tile centre the feet are drawn, in tiles


def draw_card(screen, font, lines):
    w, h = 260, 16 + 20 * len(lines)
    panel = pygame.Surface((w, h), pygame.SRCALPHA)
    panel.fill((20, 22, 28, 220))
    for i, (k, v) in enumerate(lines):
        panel.blit(font.render(f'{k}:', True, (150, 160, 175)), (12, 8 + 20 * i))
        panel.blit(font.render(str(v), True, (240, 240, 240)), (110, 8 + 20 * i))
    rect = panel.get_rect(topright=(screen.get_width() - 10, 10))
    screen.blit(panel, rect)
    return rect


def main():
    pygame.init()
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    pygame.display.set_caption('Hospital')
    clock = pygame.time.Clock()
    font = pygame.font.SysFont('consolas', 15)
    room_font = pygame.font.SysFont('consolas', 13, bold=True)
    rng = random.Random()

    lay = build_layout()
    floor_map = draw_layout(lay)
    actors = create_actors(lay, rng)
    room_labels = [(r, room_font.render(r.name, True, (40, 44, 52))) for r in lay.rooms if r.kind != 'street']

    zoom = 3
    rec = lay.rooms_of('reception')[0]
    cam_x = (rec.x + rec.w / 2) * T - screen.get_width() / zoom / 2
    cam_y = (rec.y + rec.h / 2) * T - screen.get_height() / zoom / 2
    selected, dragging, cache, card = None, False, {}, pygame.Rect(0, 0, 0, 0)

    while True:
        dt = min(clock.tick(config.TARGET_FPS) / 1000, 0.1)
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                selected = None
            elif ev.type == pygame.MOUSEWHEEL:  # zoom around the mouse
                mx, my = pygame.mouse.get_pos()
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
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and not card.collidepoint(ev.pos):
                wx, wy = (cam_x + ev.pos[0] / zoom) / T, (cam_y + ev.pos[1] / zoom) / T
                # Compare with the middle of the body, which is drawn above the actor's tile
                dist2 = lambda a: (a.px - wx) ** 2 + (a.py + FEET - CHAR_H / T / 2 - wy) ** 2
                nearest = min(actors, key=dist2)
                selected = nearest if dist2(nearest) < 0.8 ** 2 else None

        keys = pygame.key.get_pressed()
        speed = 700 * dt / zoom
        cam_x += ((keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])) * speed
        cam_y += ((keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])) * speed

        for a in actors:
            a.update(dt, lay, rng)

        # Map: crop the visible part and scale it without smoothing
        screen.fill((30, 34, 40))
        src = pygame.Rect(int(cam_x), int(cam_y), int(screen.get_width() / zoom) + 2,
                          int(screen.get_height() / zoom) + 2).clip(floor_map.get_rect())
        if src.w > 0 and src.h > 0:
            part = pygame.transform.scale(floor_map.subsurface(src), (src.w * zoom, src.h * zoom))
            screen.blit(part, ((src.x - cam_x) * zoom, (src.y - cam_y) * zoom))

        def to_screen(tx, ty):
            return (tx * T - cam_x) * zoom, (ty * T - cam_y) * zoom

        if zoom >= 2:
            for r, label in room_labels:
                screen.blit(label, to_screen(r.x + 1.2, r.y + 1.1))

        if selected:
            for tx, ty in selected.path:
                x, y = to_screen(tx + 0.5, ty + 0.5)
                pygame.draw.rect(screen, (250, 220, 60), (x - zoom, y - zoom, 2 * zoom, 2 * zoom))

        for a in sorted(actors, key=lambda a: a.py):
            # Feet a bit below the tile centre; the body sticks up over the tile behind (3/4 view)
            x, y = to_screen(a.px - CHAR_W / 2 / T, a.py + FEET - CHAR_H / T)
            w, h = CHAR_W * zoom, CHAR_H * zoom
            if -w < x < screen.get_width() and -h < y < screen.get_height():
                base = a.frame()
                key = (id(base), zoom)
                if key not in cache:
                    cache[key] = pygame.transform.scale(base, (w, h))
                screen.blit(cache[key], (x, y))
                if a is selected:
                    pygame.draw.rect(screen, (250, 220, 60), (x, y, w, h), 2)

        card = draw_card(screen, font, selected.info(lay)) if selected else pygame.Rect(0, 0, 0, 0)

        fps = clock.get_fps()
        hud = f'FPS {fps:4.0f}   actors {len(actors)}   doctors {config.MAX_DOCTORS}   zoom x{zoom}'
        screen.blit(font.render(hud, True, (240, 90, 90) if 0 < fps < config.MIN_FPS else (240, 240, 240),
                                (20, 22, 28)), (10, 10))
        screen.blit(font.render('WASD/right-drag: move   wheel: zoom   click: info card   ESC: close', True,
                                (200, 200, 200), (20, 22, 28)), (10, screen.get_height() - 26))
        pygame.display.flip()


if __name__ == '__main__':
    main()
