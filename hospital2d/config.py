# config.py - Simulation parameters. Change them here and run the program again.

TILE = 16  # tile size in pixels (before zoom)

# Layout: every doctor gets their own room, so the hospital grows with MAX_DOCTORS
MAX_DOCTORS = 7
N_SURGERIES = 5

# Actors
N_NURSES = 4
N_RECEPTIONISTS = 3
TRIAGE_DESKS_PER_ROOM = 1  # 1 or 2 (there is one triage room beside each waiting room), one nurse each
N_SECURITY = 2
N_CLEANERS = 2
N_PATIENTS = 20

# Performance: below MIN_FPS the counter turns red (the hospital's limit)
TARGET_FPS = 100
MIN_FPS = 30

ACTOR_SPEED = 1.5  # tiles per second

# Concurrency: every actor is a thread in the simulation process, which sends the UI process snapshots
ACTOR_HZ = 30  # updates per second of each actor thread
SNAPSHOT_HZ = 60  # snapshots per second sent to the UI
