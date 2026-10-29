# arrivals.py - The arrivals process: decides when patients walk in (and when a surge starts) and sends them to
# the simulation process through a Queue. It shares nothing else with it.
import os
import random
import time

import config
from actors import FIRST_NAMES, LAST_NAMES, PATIENT_LOOKS


def read_complaints():
    rows = []
    with open(os.path.join(os.path.dirname(__file__), 'data', 'complaints.csv'), encoding='utf-8') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                reason, urgency, weight, surgery = line.strip().split(',')
                rows.append((reason, int(urgency), float(weight), surgery == '1'))
    return rows


COMPLAINTS = read_complaints()


def new_patient(rng):
    """Who walks in and what they'll tell reception."""
    reason, urgency, _, surgery = rng.choices(COMPLAINTS, weights=[c[2] for c in COMPLAINTS])[0]
    temper = rng.choices(list(config.TEMPERAMENTS), weights=list(config.TEMPERAMENTS.values()))[0]
    return {'name': f'{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}', 'look': rng.choice(PATIENT_LOOKS),
            'reason': reason, 'urgency': urgency, 'surgery': surgery, 'temperament': temper}


def run_arrivals(out, stop, seed=None):
    """Process body. out: Queue -> simulation, ('patient', info) or ('surge', seconds). stop: Event."""
    out.cancel_join_thread()  # on exit, don't wait for the simulation to read what it no longer needs
    rng = random.Random(seed)
    step = 0.1  # real seconds between checks
    surge_until = 0.0
    next_arrival = time.monotonic()
    while not stop.wait(step):
        now = time.monotonic()
        # Surges start at random (on average every SURGE_EVERY_S simulated seconds)
        if now >= surge_until and rng.random() < step * config.TIME_SCALE / config.SURGE_EVERY_S:
            surge_until = now + config.SURGE_SECONDS / config.TIME_SCALE
            next_arrival = now
            out.put(('surge', config.SURGE_SECONDS))
        rate = config.ARRIVALS_PER_MINUTE / 60 * config.TIME_SCALE  # per real second
        if now < surge_until:
            rate *= config.SURGE_MULTIPLIER
        while next_arrival <= now:  # Poisson arrivals: exponential gaps between them
            out.put(('patient', new_patient(rng)))
            next_arrival += rng.expovariate(rate)
