# learn.py - The Boss learns, with no window: python learn.py [speed]      (default speed 8; Ctrl+C stops it)
# The hospital runs sped up with patients walking in as usual (no surges). Every simulated minute it saves what
# the Boss has learned (boss_brain.json: the game starts from it too) and adds a row to learning.csv. Run it again
# and it goes on from where it was. Graphs, whenever you want (even while this runs): python charts.py
import csv
import os
import sys
import time

import config
from arrivals import new_patient
from sim import BRAIN, Hospital
from world import FEATURES

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, 'learning.csv')
KINDS = ('consult', 'stretcher', 'clean', 'fight')
FIELDS = (['minute', 'tasks', 'error_s'] + [f'{k}_{f}' for k in KINDS for f in FEATURES]
          + ['arrived', 'treated', 'gave_up', 'patients', 'wait_green', 'wait_yellow', 'wait_red'])


def main(speed):
    h = Hospital(brain=BRAIN)
    h.set_speed(rate=speed)
    d = h.dispatcher
    minute = 0
    if os.path.exists(CSV):
        with open(CSV, encoding='utf-8') as f:
            minute = sum(1 for _ in f) - 1  # rows so far: keeps counting from there
    new = minute <= 0
    minute = max(0, minute)
    h.start()
    print(f'Learning at x{speed:g} (Ctrl+C to stop). Graphs: python charts.py')
    try:
        with open(CSV, 'a', newline='', encoding='utf-8') as f:
            out = csv.writer(f)
            if new:
                out.writerow(FIELDS)
            mark = h.clock()
            before = (d.learned, d.error_sum, dict(h.stats), {r: len(w) for r, w in h.waits.items()})
            next_arrival = h.clock()
            while True:
                time.sleep(0.05)
                while h.clock() >= next_arrival:  # Poisson arrivals, in simulated time
                    h.admit(new_patient(h.rng))
                    next_arrival += h.rng.expovariate(config.ARRIVALS_PER_MINUTE / 60)
                if h.clock() - mark < 60:
                    continue
                mark += 60
                minute += 1
                learned, error_sum, stats, waits = before
                with d.cond:
                    tasks, errors = d.learned - learned, d.error_sum - error_sum
                    weights = [round(w, 3) for k in KINDS for w in d.weights(k)]
                with h.stats_lock:
                    now = dict(h.stats)
                    new_waits = {r: w[waits.get(r, 0):] for r, w in h.waits.items()}
                    before = (d.learned, d.error_sum, now, {r: len(w) for r, w in h.waits.items()})
                diff = [now.get(k, 0) - stats.get(k, 0) for k in ('arrived', 'treated', 'gave_up')]
                avg = [round(sum(w) / len(w), 1) if (w := new_waits.get(r)) else '' for r in range(3)]
                out.writerow([minute, tasks, round(errors / tasks, 1) if tasks else '', *weights, *diff,
                              len(h.patients()), *avg])
                f.flush()  # so charts.py sees it straight away
                d.save(BRAIN)
                print(f'minute {minute}: learned from {tasks} tasks, guesses off by '
                      f'{errors / tasks if tasks else 0:.0f} s on average, {diff[1]} treated, {diff[2]} gave up')
    except KeyboardInterrupt:
        pass
    finally:
        d.save(BRAIN)
        h.shutdown()
        print(f'Stopped. The Boss has learned from {d.learned} tasks in total (saved in boss_brain.json).')


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 8.0)
