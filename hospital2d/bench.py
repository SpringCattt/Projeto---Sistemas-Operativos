# bench.py - How far can the hospital grow? Runs the simulation without the UI (threads only) with more and more
# actors and measures how well the actor threads keep up with the update rate they ask for (ACTOR_HZ walking,
# IDLE_HZ still). Below 80 % the simulation can't keep up: that's the limit.
#   python bench.py            -> prints a table (markdown) of the results
import pickle
import time

import config
from sim import Hospital

CASES = [  # (doctors, patients already waiting)
    (7, 20), (7, 60), (15, 120), (25, 200), (40, 300), (60, 450), (80, 600), (120, 900), (160, 1200),
    (200, 1500), (250, 1900),
]
WARMUP_S, MEASURE_S = 2.0, 6.0


def measure(n_doctors, n_patients):
    h = Hospital(seed=1, n_doctors=n_doctors, n_patients=n_patients)
    h.start()
    time.sleep(WARMUP_S)
    h.measure()  # starts the window
    cpu0 = time.process_time()
    time.sleep(MEASURE_S)
    h.measure()
    cpu = (time.process_time() - cpu0) / MEASURE_S
    # What the UI process gets SNAPSHOT_HZ times a second: build it and serialise it (what a Queue does)
    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        data = pickle.dumps(h.snapshot())
        timings.append((time.perf_counter() - t0) * 1000)
    snap_ms = sorted(timings)[2]  # median
    threads = len(h.threads)
    h.shutdown()
    assert not h.errors, h.errors[0][1]
    return threads, h.keep_up, h.sim_hz, cpu, snap_ms, len(data) / 1024


if __name__ == '__main__':
    print(f'Threads ask for {config.ACTOR_HZ} updates/s walking and {config.IDLE_HZ} standing still; '
          f'limit at 80 % keeping up.\n')
    print('| doctors | patients | threads | keeping up | updates/s per actor | CPU (cores) | snapshot | size |')
    print('|--------:|---------:|--------:|-----------:|--------------------:|------------:|---------:|-----:|')
    for n_doctors, n_patients in CASES:
        threads, keep_up, hz, cpu, snap_ms, kb = measure(n_doctors, n_patients)
        print(f'| {n_doctors} | {n_patients} | {threads} | {100 * keep_up:.0f} % | {hz:.1f} | {cpu:.2f} '
              f'| {snap_ms:.1f} ms | {kb:.0f} KB |', flush=True)
        if keep_up < 0.5:
            break  # far past the limit: no point going on
