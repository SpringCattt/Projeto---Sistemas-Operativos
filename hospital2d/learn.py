# learn.py - The Boss learns, with no window: python learn.py [speed] [hospitals]        (Ctrl+C stops it)
#   speed: default 8. Faster than that people can't move as fast as they should, and it would learn wrong things.
#   hospitals: how many run at once, each in its own process (one CPU core each); default 4. At least 2 to learn.
#
# What it's after: the biggest staff bonus, which is a share of the day's profit (what the patients paid minus the
# wages; config.PRICE, config.WAGE). Each round is one whole day (24 hours, then the ones still inside are seen
# to). What it decides, and so learns (world.start_policy):
#   - who goes first among patients of the same urgency (the most urgent always go first, like real triage):
#     weights for minutes waited and impatience
#   - when to call in staff on call (how many tasks more than free staff) and how long it waits, with nothing to
#     do, before sending them home: more staff, more wages; fewer, longer waits (patients pay less, or give up)
# It's an evolution strategy:
#   1. every hospital plays the same day (same seed: same patients arriving at the same times) with the current
#      policy nudged a bit, half of them one way and half the opposite way (+noise / -noise)
#   2. each one's score is that day's profit
#   3. the policy moves towards the nudges that made more than the average, and the next day starts
# With 4 or more hospitals, some play other days twice, with the current policy and with the one it started from
# (one day; five with 20+ hospitals): the fair test of whether what it learned is any better (vs_start in
# learning.csv: the average difference in profit over those days, above 0 = better).
# On top of that, as before, the Boss learns how long each task takes (who does it; averaged over the hospitals).
# Everything is saved after every round in boss_brain.json (the game starts from it too) and learning.csv.
# Run it again and it goes on from there. Graphs, whenever you want (even while this runs): python charts.py
import csv
import multiprocessing as mp
import os
import queue
import random
import sys
import time

import config
from arrivals import new_patient
from sim import BRAIN, Hospital
from world import Dispatcher, start_policy

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, 'learning.csv')
FIELDS = ['round', 'profit', 'best', 'worst', 'w_waited', 'w_impatience', 'call_in_at', 'send_home_after_s',
          'treated', 'gave_up', 'red_wait_min', 'called_in', 'tasks', 'error_s', 'vs_start']
NOISE = 2.0  # how big the nudges are...
SCALES = (1.0, 1.0, 0.5, 20.0)  # ...for each part of the policy: weights, weights, tasks, seconds
LOWEST = (0.0, 0.0, 1.0, 10.0)  # (no negative weights; call in at 1 task more at least; wait 10 s at least)
STEP = 1.0  # how far the policy moves each round (at most about one nudge)


def hospital(i, speed, jobs, reports):
    """Process body: plays one round after another. jobs: Queue <- main (seed, weights, brain), reports: -> main."""
    try:
        while True:
            seed, policy, brain = jobs.get()
            h = Hospital(seed=seed, on_call=True)
            d = h.dispatcher
            d.brain, d.policy = brain, policy
            h.set_speed(rate=speed)
            rng = random.Random(seed)  # the same patients at the same times in every hospital
            next_arrival = 0.0
            h.start()
            h.measure()  # from here: did the actor threads keep up with their rate? (too many hospitals: no)
            try:
                while h.day_over is None and h.clock() < 3 * config.DAY_HOURS * config.HOUR_S:  # (a day, and a cap)
                    while h.clock() >= next_arrival:
                        h.admit(new_patient(rng))
                        next_arrival += rng.expovariate(config.ARRIVALS_PER_MINUTE / 60)
                    h.stop.wait(0.05)
                h.measure()
            finally:
                h.shutdown()
            if h.day_over is None:  # (didn't end: the day so far)
                h.end_day()
            day = h.day_over
            red = sum(h.waits.get(2, [])) / 60
            reports.put((i, {'profit': day['profit'], 'treated': day['treated'], 'gave_up': day['gave_up'], 'red': red,
                             'called_in': day['called_in'], 'brain': d.brain, 'tasks': d.learned,
                             'errors': d.error_sum, 'keep_up': h.keep_up}))
    except KeyboardInterrupt:
        pass


def stay_awake_quietly():
    """Windows: no sleeping while it learns (until it stops), and below-normal priority for it and the hospital
    processes it starts, so the PC stays responsive if someone uses it meanwhile."""
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS


def main(speed, n):
    stay_awake_quietly()
    boss = Dispatcher(clock=lambda: 0.0)  # only to hold, update and save what it learned
    boss.load(BRAIN)
    rnd = 0
    if os.path.exists(CSV):
        with open(CSV, encoding='utf-8') as f:
            header = f.readline().strip().split(',')
            rnd = sum(1 for _ in f)
        if header == FIELDS[:-1]:  # from before vs_start: the same, with that column empty
            with open(CSV, encoding='utf-8') as f:
                old = list(csv.reader(f))
            with open(CSV, 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerows([FIELDS] + [row + [''] for row in old[1:]])
        elif header != FIELDS:  # from an older learn.py: kept aside
            os.replace(CSV, os.path.join(HERE, f'learning_old_{time.strftime("%Y%m%d_%H%M%S")}.csv'))
            rnd = 0
    jobs, reports = [mp.Queue() for _ in range(n)], mp.Queue()
    procs = [mp.Process(target=hospital, args=(i, speed, jobs[i], reports), daemon=True) for i in range(n)]
    for p in procs:
        p.start()
    if n < 2:
        print('With 1 hospital it only learns how long tasks take: use 2 or more to learn who goes first.')
    print(f'Learning with {n} hospitals at x{speed:g}, a day per round (Ctrl+C to stop). Graphs: python charts.py')
    rng = random.Random()
    try:
        with open(CSV, 'a', newline='', encoding='utf-8') as f:
            out = csv.writer(f)
            if rnd == 0:
                out.writerow(FIELDS)
            while True:
                rnd += 1
                seed = rng.randrange(10 ** 9)
                # +noise / -noise in pairs (an odd one out plays the current weights as they are), and the fair test
                days = [rng.randrange(10 ** 9) for _ in range(5 if n >= 20 else 1 if n >= 4 else 0)]
                compare = 2 * len(days)
                noises = []
                for _ in range((n - compare) // 2):
                    e = [rng.gauss(0, 1) for _ in boss.policy]
                    noises += [e, [-x for x in e]]
                noises += [[0.0] * len(boss.policy)] * ((n - compare) % 2)
                plays = [[max(low, w + NOISE * k * x) for w, x, k, low in zip(boss.policy, e, SCALES, LOWEST)]
                         for e in noises]
                seeds = [seed] * len(plays)
                for day in days:
                    plays += [list(boss.policy), start_policy()]
                    seeds += [day, day]
                for job, weights, s in zip(jobs, plays, seeds):
                    job.put((s, weights, boss.brain))
                results = [None] * n
                while None in results:
                    try:
                        i, r = reports.get(timeout=0.5)  # (a timeout, so Ctrl+C gets through)
                    except queue.Empty:
                        continue
                    results[i] = r
                rewards = [r['profit'] for r in results[:len(noises)]]
                m = len(rewards)
                mean = sum(rewards) / m
                spread = (sum((x - mean) ** 2 for x in rewards) / m) ** 0.5
                tests = results[len(noises):]
                vs_start = round(sum(a['profit'] - b['profit'] for a, b in zip(tests[::2], tests[1::2])) / len(days)) \
                    if days else ''
                if m >= 2 and spread > 0:  # towards the nudges that did better than the average
                    for k in range(len(boss.policy)):
                        move = sum((x - mean) / spread * e[k] for x, e in zip(rewards, noises)) / m
                        boss.policy[k] = max(LOWEST[k], boss.policy[k] + STEP * NOISE * SCALES[k] * move)
                kinds = {k for r in results for k in r['brain']}  # how long tasks take: averaged
                boss.brain = {k: [sum(ws) / len(ws) for ws in zip(*(r['brain'][k] for r in results if k in r['brain']))]
                              for k in kinds}
                tasks, errors = sum(r['tasks'] for r in results), sum(r['errors'] for r in results)
                boss.learned += tasks
                boss.error_sum += errors
                boss.save(BRAIN)
                avg = [sum(r[k] for r in results) / n for k in ('treated', 'gave_up', 'red', 'called_in')]
                out.writerow([rnd, round(mean), round(max(rewards)), round(min(rewards)),
                              *[round(w, 2) for w in boss.policy], *[round(x, 1) for x in avg], tasks,
                              round(errors / tasks, 1) if tasks else '', vs_start])
                f.flush()  # so charts.py sees it straight away
                keep_up = min(r['keep_up'] for r in results)
                print(f'day {rnd}: profit {mean:,.0f} (best {max(rewards):,.0f}, worst {min(rewards):,.0f}); '
                      f'weights waited {boss.policy[0]:.1f}, impatience {boss.policy[1]:.1f}; call in at '
                      f'{boss.policy[2]:.1f} more tasks, home after {boss.policy[3]:.0f} s; '
                      f'vs start {vs_start}; keeping up {100 * keep_up:.0f}%'
                      + ('  << too many hospitals for this PC: use fewer' if keep_up < 0.9 else ''))
    except KeyboardInterrupt:
        pass
    finally:
        boss.save(BRAIN)
        for p in procs:
            p.terminate()
        print('Stopped. What the Boss learned is saved in boss_brain.json.')


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 8.0, int(sys.argv[2]) if len(sys.argv) > 2 else 4)
