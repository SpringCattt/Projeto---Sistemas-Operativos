# sim.py - The simulation process: one thread per actor, sharing the World behind a lock.
# It talks to the UI process only through queues: snapshots out, commands in.
import queue
import random
import threading

import config
from actors import create_actors
from layout import build_layout


def run_simulation(snapshots, commands, seed=None):
    """Process body. snapshots: Queue -> UI (list of actor dicts). commands: Queue <- UI ('quit')."""
    lay = build_layout()  # same config, so the same plan the UI builds on its side
    actors = create_actors(lay, random.Random(seed))
    stop = threading.Event()
    threads = [threading.Thread(target=a.run, args=(lay, stop), name=f'{a.role}-{a.id}', daemon=True)
               for a in actors]
    for t in threads:
        t.start()
    snapshots.cancel_join_thread()  # on exit, don't wait for the UI to read snapshots nobody needs any more
    try:
        while True:
            try:  # the timeout paces the snapshots
                if commands.get(timeout=1 / config.SNAPSHOT_HZ) == 'quit':
                    break
            except queue.Empty:
                pass
            try:  # if the UI is behind, skip this one: it only ever wants the latest
                snapshots.put_nowait([a.snapshot(lay) for a in actors])
            except queue.Full:
                pass
    finally:
        stop.set()
        for t in threads:
            t.join()
