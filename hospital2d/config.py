# config.py - Simulation parameters. Change them here and run the program again.
# Times are in simulated seconds; TIME_SCALE turns them into real ones.

TILE = 16  # tile size in pixels (before zoom)

# Layout: every doctor gets their own room, so the hospital grows with MAX_DOCTORS
MAX_DOCTORS = 10
N_SURGERIES = 5

# Actors
N_NURSES = 6  # ward nurses (walking the medical wing); triage nurses come from TRIAGE_DESKS_PER_ROOM
N_RECEPTIONISTS = 5 # max 10, then they stack in each other`s position
TRIAGE_DESKS_PER_ROOM = 4 # max 4
N_SECURITY = 2
N_CLEANERS = 2
N_PATIENTS = 40  # already triaged and waiting when the simulation starts

# Performance: below MIN_FPS the counter turns red (the hospital's limit)
TARGET_FPS = 144
MIN_FPS = 30

ACTOR_SPEED = 1.5  # tiles per second
RUN_SPEED = [1.4, 1.8, 2.4]  # x ACTOR_SPEED: staff run to a task in another room, faster the more urgent
STRETCHER_SPEED = 2.4  # the two nurses pushing a stretcher (and the patient on it)
TIME_SCALE = 1.0  # 2.0 = everything (walking, waiting, arrivals) happens twice as fast

# Concurrency: every actor is a thread in the simulation process, which sends the UI process snapshots
ACTOR_HZ = 30  # updates per second of an actor thread while it walks...
IDLE_HZ = 10  # ...and while it stands or sits still (it doesn't need more: saves CPU for big hospitals)
SNAPSHOT_HZ = 60  # snapshots per second sent to the UI

# Arrivals (the arrivals process)
ARRIVALS_PER_MINUTE = 6
SURGE_EVERY_S = 240  # on average a surge starts this often...
SURGE_SECONDS = 20  # ...lasts this long...
SURGE_MULTIPLIER = 6  # ...and multiplies arrivals by this
MAX_PATIENTS = 60  # with this many inside, new arrivals are turned away at the door

URGENCY_NAMES = ['Green', 'Yellow', 'Red']

# How long things take: (min, max) seconds
REGISTER_S = (3, 5)
COMPLAINT_S = (3, 5)
TRIAGE_S = (4, 6)
CONSULT_S = [(6, 10), (8, 14), (12, 18)]  # by urgency
DISINFECT_S = (3, 4)
SURGERY_S = (20, 30)
CALM_DOWN_S = (3, 5)  # security dealing with a fight
CLEAN_S = (4, 6)

# Patients waiting for a doctor
PATIENCE_LOSS = (0.3, 0.8)  # points per second (each patient gets a value in this range); patience starts at 100
TEMPERAMENTS = {'passive': 0.45, 'medium': 0.40, 'aggressive': 0.15}  # out of patience: leaves / complains / fights
ESCALATE_AFTER_S = [180, 240]  # waiting this long, Green turns Yellow, and then Yellow turns Red
VOMIT_CHANCE_PER_S = [0.0, 0.003, 0.006]  # by urgency
EXPEL_CHANCE = 0.5  # security throws an aggressive patient out (otherwise it calms down)

# The dispatcher (who does what): lowest score wins
W_DISTANCE = 1.0  # per tile away from the task
W_FATIGUE = 0.5  # per fatigue point
W_BUSY = 25  # extra if it has to drop what it's doing
W_RESTING = 40  # extra if it's resting
PREEMPT_GAP = 2  # only interrupt for a task this many urgency levels higher (Red over Green)

# Fatigue (0-100)
FATIGUE_PER_TASK = {'consult': (6, 9, 13), 'surgery': 25, 'fight': 10, 'clean': 6,
                    'register': 2, 'stretcher': 8, 'complaint': 3, 'triage': 3}
REST_AT = 70  # idle and this tired: goes to the rest room...
RESTED_AT = 20  # ...until it's down to this
RECOVER_PER_S = 0.15  # idle
RECOVER_RESTING_PER_S = 2.5
