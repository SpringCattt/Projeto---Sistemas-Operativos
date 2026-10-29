# learn.py - The Boss learns, with no window: python learn.py [speed] [hospitals]        (Ctrl+C stops it)
#   speed: default 8. Faster than that people can't move as fast as they should, and the Boss would learn wrong times.
#   hospitals: how many run at once, each in its own process (one CPU core each); default 1.
# Each hospital learns on its own copy of the Boss's brain. Every simulated minute they all send it here; this
# process averages them (federated averaging), saves the result in boss_brain.json (the game starts from it too),
# adds a row to learning.csv and sends the average back to every hospital. Run it again and it goes on from there.
# Graphs, whenever you want (even while this runs): python charts.py
import copy
import csv
import multiprocessing as mp
import os
import queue
import sys
import time

import config
from arrivals import new_patient
from sim import BRAIN, Hospital
from world import FEATURES, Dispatcher

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, 'learning.csv')
KINDS = ('consult', 'stretcher', 'clean', 'fight')
FIELDS = (['minute', 'tasks', 'error_s'] + [f'{k}_{f}' for k in KINDS for f in FEATURES]
          + ['arrived', 'treated', 'gave_up', 'patients', 'wait_green', 'wait_yellow', 'wait_red'])


def hospital(i, speed, reports, brains):
    """Process body: one hospital, learning. reports: Queue -> main process (what happened each simulated minute,
    and its brain). brains: Queue <- main process (the averaged brain, to go on from)."""
    h = Hospital(brain=BRAIN)
    h.set_speed(rate=speed)
    d = h.dispatcher
    h.start()
    try:
        mark = next_arrival = h.clock()
        learned, error_sum, stats, waits = 0, 0.0, {}, {}
        while True:
            time.sleep(0.05)
            try:
                brain = brains.get_nowait()
                with d.cond:
                    d.brain = brain
            except queue.Empty:
                pass
            while h.clock() >= next_arrival:  # Poisson arrivals, in simulated time
                h.admit(new_patient(h.rng))
                next_arrival += h.rng.expovariate(config.ARRIVALS_PER_MINUTE / 60)
            if h.clock() - mark < 60:
                continue
            mark += 60
            with d.cond:
                report = {'brain': copy.deepcopy(d.brain), 'tasks': d.learned - learned,
                          'errors': d.error_sum - error_sum}
                learned, error_sum = d.learned, d.error_sum
            with h.stats_lock:
                now = dict(h.stats)
                report['waits'] = {r: w[waits.get(r, 0):] for r, w in h.waits.items()}
                waits = {r: len(w) for r, w in h.waits.items()}
            for k in ('arrived', 'treated', 'gave_up'):
                report[k] = now.get(k, 0) - stats.get(k, 0)
            stats = now
            report['patients'] = len(h.patients())
            reports.put((i, report))
    except KeyboardInterrupt:
        pass
    finally:
        h.shutdown()


def main(speed, n):
    boss = Dispatcher(clock=lambda: 0.0)  # only to hold, average and save the brain
    boss.load(BRAIN)
    minute, new = 0, not os.path.exists(CSV) or os.path.getsize(CSV) == 0
    if not new:
        with open(CSV, encoding='utf-8') as f:
            minute = max(0, sum(1 for _ in f) - 1)  # rows so far: keeps counting from there
    reports, brains = mp.Queue(), [mp.Queue() for _ in range(n)]
    procs = [mp.Process(target=hospital, args=(i, speed, reports, brains[i]), daemon=True) for i in range(n)]
    for p in procs:
        p.start()
    print(f'Learning with {n} hospital(s) at x{speed:g} (Ctrl+C to stop). Graphs: python charts.py')
    pending = [[] for _ in range(n)]  # each hospital's reports not used yet
    try:
        with open(CSV, 'a', newline='', encoding='utf-8') as f:
            out = csv.writer(f)
            if new:
                out.writerow(FIELDS)
            while True:
                try:
                    i, report = reports.get(timeout=0.5)  # (a timeout, so Ctrl+C gets through)
                except queue.Empty:
                    continue
                pending[i].append(report)
                if not all(pending):  # a row once every hospital has done this minute
                    continue
                batch = [p.pop(0) for p in pending]
                minute += 1
                kinds = {k for r in batch for k in r['brain']}
                boss.brain = {k: [sum(ws) / len(ws) for ws in zip(*(r['brain'][k] for r in batch if k in r['brain']))]
                              for k in kinds}
                tasks, errors = sum(r['tasks'] for r in batch), sum(r['errors'] for r in batch)
                boss.learned += tasks
                boss.error_sum += errors
                for b in brains:
                    b.put(boss.brain)
                boss.save(BRAIN)
                per = [round(sum(r[k] for r in batch) / n, 1) for k in ('arrived', 'treated', 'gave_up', 'patients')]
                waits = [[w for r in batch for w in r['waits'].get(u, [])] for u in range(3)]
                out.writerow([minute, tasks, round(errors / tasks, 1) if tasks else '',
                              *[round(w, 3) for k in KINDS for w in boss.weights(k)], *per,
                              *[round(sum(w) / len(w), 1) if w else '' for w in waits]])
                f.flush()  # so charts.py sees it straight away
                print(f'minute {minute}: learned from {tasks} tasks, guesses off by '
                      f'{errors / tasks if tasks else 0:.0f} s on average')
    except KeyboardInterrupt:
        pass
    finally:
        boss.save(BRAIN)
        for p in procs:
            p.join(timeout=10)
            if p.is_alive():
                p.terminate()
        print(f'Stopped. The Boss has learned from {boss.learned} tasks in total (saved in boss_brain.json).')


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 8.0, int(sys.argv[2]) if len(sys.argv) > 2 else 1)
