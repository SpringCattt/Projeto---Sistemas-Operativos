# config.py - Simulation parameters. Change them here and run the program again.
# Times are in simulated seconds; TIME_SCALE turns them into real ones.

TILE = 16  # tile size in pixels (before zoom)

# The building is drawn in Tiled (maps/): one doctor per doctor's room, one receptionist per chair behind the
# reception counter, one triage nurse per office chair in triage, one operating team per surgery

# Actors
N_NURSES = 6  # ward nurses (walking the medical wing)
N_SECURITY = 2
N_CLEANERS = 2
N_PATIENTS = 40  # already triaged and waiting when the simulation starts

# Performance: below MIN_FPS the counter turns red (the hospital's limit)
TARGET_FPS = 144
MIN_FPS = 30

ACTOR_SPEED = 1.5  # tiles per second
LIFT_FLOOR_S = 3.0  # the elevator, per floor (stretchers can't take the stairs)
RUN_SPEED = [1.4, 1.8, 2.4]  # x ACTOR_SPEED: staff run to a task in another room, faster the more urgent
STRETCHER_SPEED = 2.4  # the two nurses pushing a stretcher (and the patient on it)
TIME_SCALE = 1.0  # 2.0 = everything (walking, waiting, arrivals) happens twice as fast

# Concurrency: every actor is a thread in the simulation process, which sends the UI process snapshots
ACTOR_HZ = 30  # updates per second of an actor thread while it walks...
IDLE_HZ = 10  # ...and while it stands or sits still (it doesn't need more: saves CPU for big hospitals)
SNAPSHOT_HZ = 60  # times per second the simulation writes where everyone is (shared memory with the UI)...
WORDS_HZ = 10  # ...and sends the words (names, speech, cards, stats) through the snapshots queue

# Arrivals (the arrivals process)
ARRIVALS_PER_MINUTE = 6
SURGE_EVERY_S = 240  # on average a surge starts this often...
SURGE_SECONDS = 20  # ...lasts this long...
SURGE_MULTIPLIER = 6  # ...and multiplies arrivals by this

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
ESCALATE_AFTER_S = 180  # waiting this long, Green turns Yellow (never Red: Reds come by ambulance, they can't walk)
VOMIT_CHANCE_PER_S = [0.0, 0.003, 0.006]  # by urgency
EXPEL_CHANCE = 0.5  # security throws an aggressive patient out (otherwise it calms down)

# The dispatcher (who does what): lowest score wins
W_DISTANCE = 1.0  # per tile away from the task
W_FATIGUE = 0.5  # per fatigue point
W_BUSY = 25  # extra if it has to drop what it's doing
W_RESTING = 40  # extra if it's resting
PREEMPT_GAP = 2  # only interrupt for a task this many urgency levels higher (Red over Green)
# Who goes first when there are more tasks than staff: always the most urgent colour first, like real triage
# (Manchester); within a colour, the highest minutes waited * w + impatience * w (impatience: 0 = full patience,
# 1 = about to give up), ties by arrival. Starts as arrival order (both 0); learn.py tunes the two weights, and
# CALL_IN_AT and SEND_HOME_AFTER_S below (the Boss's policy: world.start_policy).
PRIORITY_START = [0.0, 0.0]

# Staff on call: the Boss calls one in when CALL_IN_AT more tasks are waiting than there are free staff of that role
# (at most one every CALL_EVERY_S per role), and sends them home after SEND_HOME_AFTER_S with nothing waiting.
# On-call doctors need a room: that many doctor's rooms start empty.
ON_CALL = {'Doctor': 3, 'WardNurse': 2, 'Security': 1, 'Cleaner': 1}
CALL_IN_AT = 3
CALL_EVERY_S = 30
SEND_HOME_AFTER_S = 90

# A day: DAY_HOURS hours of HOUR_S simulated seconds (24 x 30 s: 12 minutes at x1), from day 0. At midnight the
# doors close: whoever is inside is seen to (or gives up), and once the last patient is gone the day is over. In the
# game it then waits, paused, on the day's report until you start the next day.
HOUR_S = 30
DAY_HOURS = 24

# Money (game numbers). A treated patient pays PRICE for its urgency at triage (+ SURGERY_PRICE if operated on):
# all of it if it saw a doctor within ON_TIME_S, less the longer it waited (LATE_PAYS of it at 3 x ON_TIME_S or
# more). Nothing if it gave up or was thrown out. Staff cost WAGE per hour in the hospital, ON_CALL_PAY times
# that if the Boss called them in. The Boss is the owner: no wage.
PRICE = [60, 150, 400]  # Green, Yellow, Red
SURGERY_PRICE = 800
ON_TIME_S = [240, 120, 45]
LATE_PAYS = 0.4
WAGE = {'doctor': 10, 'nurse': 5, 'receptionist': 3, 'security': 3, 'cleaner': 3}
ON_CALL_PAY = 1.5
# The bonus: BONUS_SHARE of the day's profit (if there is any), shared among the staff by what each one brought
# in. Of each payment, SHARE goes to whoever did each step for that patient (a step nobody did, like the stretcher
# for someone who walked, goes to the doctor); security and cleaners count CREDIT for each fight stopped and mess
# cleaned. The Boss learns (learn.py) to make this pool as big as it can.
BONUS_SHARE = 0.3
SHARE = {'reception': 0.10, 'triage': 0.15, 'stretcher': 0.15, 'doctor': 0.60}
CREDIT = {'fight': 40, 'clean': 20}

STRETCHERS = 4  # parked at the back of storage: with all of them out, a Red patient waits for one to come back

# Fatigue (0-100)
FATIGUE_PER_TASK = {'consult': (6, 9, 13), 'surgery': 25, 'fight': 10, 'clean': 6,
                    'register': 2, 'stretcher': 8, 'complaint': 3, 'triage': 3}
REST_AT = 70  # idle and this tired: goes to the rest room...
RESTED_AT = 20  # ...until it's down to this
RECOVER_PER_S = 0.15  # idle
RECOVER_RESTING_PER_S = 2.5
