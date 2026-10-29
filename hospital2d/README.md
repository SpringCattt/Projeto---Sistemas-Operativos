# Hospital — a concurrent hospital simulator

A top-down pixel-art hospital where every person is a thread. Patients walk in, register, get triaged, wait and
see a doctor (or go to surgery); doctors, nurses, receptionists, security and cleaners do their jobs, and a
dispatcher — the Boss, sitting in the round control room at the centre of the hospital — decides who does
what. It is the Operating Systems project
(*Comunicação e Sincronização entre Processos*): processes that only talk through queues, and threads that share
memory through locks, conditions, events and semaphores.

## Running it

```
pip install -r requirements.txt
python main.py
```

The third-party art is not in the repository (the licences don't allow redistributing it). Download it and unzip
it into `assets/third_party/`:

| Folder | Pack |
|---|---|
| `hospital/` (with `Pngs/` inside) | [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) by Soppycraft |
| `characters/` (`000.png` … `039.png`) | [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) by Jephed |
| `moderntown/` (with `sprites/` inside) | [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) by Chucky Assets |

**Controls:** WASD / arrows or right-drag to move · mouse wheel to zoom · click a person for their card, or any tile
of a room to see who is inside (the list is clickable) · `L` event log on/off · `B` what the Boss is doing
(also shown when you click the Boss) · `ESC` closes the card · `Tab` admin tablet · `Space` pause · `+`/`-` speed.

**Admin tablet** (`Tab`): pause and speed (x0.25 to x8), send patients of any urgency (with or without surgery, at
the door or already triaged, 1/5/20 at a time), trigger a surge, a fight or someone throwing up, and hire or send
home staff while it runs (doctors, receptionists and triage nurses up to their rooms/desks; new staff walk in from
the street, and whoever is sent home finishes what it's doing and walks out). Its orders go to the simulation through the
commands queue and are carried out by a thread of their own that is always waiting on it, like an interrupt handler:
they take effect at once (a few ms), whatever the actors are doing.

## The building

The hospital is built around its centre: the **Main Hall**, with the Boss's round **Control Room** in the middle
(sealed: no doors; the Boss sits in the exact centre and nobody else goes in). The main corridor runs through the
hall and out to both sides, and ends in an exit at each end: whoever has seen a doctor leaves that way, so they
don't walk back against the people coming in through reception and the waiting rooms. Under the hall is the base
block, always the same: street → reception → a triage room on each side (two nurses each) → the two waiting rooms,
plus the bar. Every other room — doctors' rooms first, then the surgery groups (surgery | disinfection |
surgery), storage and rest — goes to whichever side is shortest so far (on top of the hall, or along the corridor,
above or below, left or right), so the hospital grows evenly around the control room as `MAX_DOCTORS` grows.

Everything you can tune is in [`config.py`](config.py): number of doctors (the building grows with them), surgeries,
staff, arrivals per minute, surges, how long things take, patience, the dispatcher's weights, fatigue,
`TIME_SCALE` to speed everything up.

## Architecture

### Four processes

```
                 snapshots (Queue, 60/s)                         patients, surges (Queue)
   UI process  <-------------------------  Simulation process  <-------------------------  Arrivals process
   (main.py)   ------------------------->  (sim.py)
                 commands: 'quit' (Queue)        |  events, summary (Queue)
                                                 v
                                            Log process (eventlog.py) -> hospital.log
```

* **UI** only draws. It never touches the actors: it draws the latest snapshot (plain data) the simulation sends.
* **Simulation** owns the hospital: the plan, the shared structures and one thread per actor.
* **Arrivals** decides when people walk in (Poisson arrivals, with random surges) and what they come with
  (`data/complaints.csv`).
* **Log** writes every event to `hospital.log` and, at the end, a summary (people treated, average waits by
  urgency, fights, how well the threads kept up…).

Closing the window stops everything: the UI tells the simulation to quit, the simulation stops every thread
(`threading.Event`), sends the summary and closes the log; anything still alive after a few seconds is terminated.

### Threads inside the simulation

Each actor runs its whole visit or shift as a plain script (`life()`) in its own thread — "walk there, wait for
this, do that" — advancing one *tick* at a time (30 per second while walking, 10 while still). They share these
structures ([`world.py`](world.py)):

| What | Primitive | Used for |
|---|---|---|
| `World.lock` | mutex (`Lock`) | who stands on each tile; reserving the next tile is check-and-set in one step, so two people can never take the same tile |
| `Line` | monitor (`Condition`) | the reception line: patients see their place and walk to it, receptionists `take()` the first one (`wait_for` / `notify`) |
| triage queue | `queue.Queue` | producer–consumer: receptionists put registered patients, triage nurses take them |
| `Summons` | two `Event`s | rendezvous between a member of staff and a patient: *arrived* (patient → staff), *done* (staff → patient) |
| `Dispatcher` | monitor (`Condition`) | open tasks and who is doing what; woken up by every new task and every finished one |
| `Theatres` | counting `Semaphore` + `Lock` | how many operating rooms are free, and which |
| `Transfer` | `Condition` + `Event`s | two nurses meet at storage with a stretcher, then pick up and deliver a Red patient |
| disinfection rooms | counting `Semaphore` (one per room) | as many doctors washing at once as the room has sinks |

**Deadlocks.** No thread ever holds two of those locks at the same time, so there is no lock-ordering deadlock.
People blocking each other in a narrow spot (each waiting for the other's tile) is the other kind: whoever has
been blocked too long steps aside and finds a new route.

### A patient's visit ([`patients.py`](patients.py))

street → reception line → registration (says why it came) → waits for triage → triage nurse assigns the urgency
(Green / Yellow / Red, from the complaint) → its waiting room → a doctor calls it → consultation
(→ surgery) → street.

While it waits, its **patience** runs down and its **condition can get worse** (Green → Yellow → Red), Yellow and
Red patients may **throw up** (a cleaner is sent). When the patience is gone it reacts by temperament:
**passive** leaves, **medium** goes to complain at reception, **aggressive** starts a fight (security is sent and
either calms it down or throws it out). Patients never walk behind a counter, into the corridor unless a doctor
called them, or into staff rooms.

### The dispatcher ([`world.py`](world.py), [`staff.py`](staff.py))

The dispatcher is a person: the Boss in the control room, whose thread runs the assignment loop. For every open
task (consultation, stretcher, fight, mess), most urgent first, it guesses how long each actor of the right role
would take to get it done and picks the quickest:

```
seconds = base + distance to the task × w_distance + fatigue × w_fatigue  (+ w_busy if busy) (+ w_resting if resting)
```

When the actor finishes, it compares the real time with its guess and nudges each weight in the direction that
would have made the guess better (normalised least mean squares; one set of weights per kind of task).

**Who goes first** when there are more patients than doctors is a priority with three weights: urgency, minutes
waited and impatience (how close the patient is to giving up). It starts as plain "most urgent first" and
`learn.py` tunes it with an **evolution strategy**, in rounds of 10 simulated minutes: every hospital plays the same
day (same seed) with the weights nudged a bit (half one way, half the opposite), each round is scored
(treated − 3 × gave up − minutes Red patients waited) and the weights move towards the nudges that scored better.
Two of the hospitals play each day with the learned weights and with the original ones: that difference
(`vs_start`, its own chart) is the fair test of whether what it learned is actually better.

```
python learn.py 8 4       # 4 hospitals at once at x8 (one process each); Ctrl+C stops it, run it again to go on
python charts.py          # graphs of how it's going so far (charts.png), also while learn.py runs
```

Don't go above x8: people couldn't move as fast as they should. After every round it saves `boss_brain.json` (the
game starts from it too) and a row in `learning.csv`. In PowerShell, at low priority:
`cmd /c start "learn" /low python learn.py 8 4`.

**Staff on call** (`config.ON_CALL`): when tasks pile up for a role, the Boss calls someone in (they walk in from
the street; on-call doctors get the rooms left empty for them) and sends them home once it has been quiet for a
while.

The distance is the real one, walking around walls and furniture (a breadth-first search from the task, cached).
Click the Boss (or press `B`) to watch it: a line from every busy member of staff to its task, a pulsing ring on
every task still waiting, and its last decisions with the score of the winner and of the runner-up.

Routes (A*) are always the shortest; among equally short ones people take the one with fewest turns, so they walk
straight instead of zig-zagging.

* A busy actor is only **interrupted** when the new task is at least `PREEMPT_GAP` (2) urgency levels above what
  it's doing — a Red patient takes a doctor away from a Green one, never from a Yellow one, so doctors don't keep
  jumping between similar tasks. The interrupted patient goes back to the waiting room, keeping its place.
* **Surgery** is never interrupted. The doctor takes a free operating room (semaphore), washes in its disinfection
  room (semaphore) and only then goes in, from the disinfection room — never straight from the corridor.
* **Ambulances:** patients who need surgery arrive by ambulance, already triaged as Red, through Emergency's own
  door at the back (the ambulance bay), so they never walk through the hospital.
* **Red patients** wait lying in a bed in **Emergency**. When a doctor takes one, two ward nurses run to storage,
  wait for each other there, bring the stretcher right next to the patient's bed (it slides across onto it) and push
  it to the doctor's exam bed or straight to surgery, where it slides across onto the bed or table. Nurse, stretcher and nurse are one block of three tiles that moves together, like a train:
  the lead nurse's thread moves all three at once, so they can never come apart (it can also turn round).
* **Running:** staff run to tasks in other rooms, faster the more urgent (`RUN_SPEED`). Someone running never queues
  behind someone slower or standing still: it looks a few tiles ahead and goes round them.
* **Fatigue** goes up with every task and down with rest; tired people work slower and get fewer tasks, and when
  they're exhausted they go to the rest room.

## Files

| File | What it does |
|---|---|
| `main.py` | starts the processes; the UI (pygame) |
| `sim.py` | the simulation process: `Hospital` (actors, threads, stats, snapshots) |
| `world.py` | everything the threads share, and how they synchronise |
| `actors.py` | what every actor has in common: ticks, walking, pathfinding, giving way |
| `staff.py` | dispatcher, receptionists, triage nurses, doctors, security, cleaners, ward nurses |
| `patients.py` | a patient's visit |
| `arrivals.py` | the arrivals process |
| `eventlog.py` | the log process |
| `layout.py` | builds the plan from `config.py`: rooms, doors, furniture, named spots |
| `art.py` | loads the PNGs and draws the plan |
| `dialogue.py` | picks what people say from `data/dialogue.txt` |
| `data/` | names, complaints and dialogue lines, one per line |

**Speech bubbles.** People talk when they interact (registration, triage, the doctor's call, complaints, fights,
security, someone throwing up, a surge, the Boss shouting orders…). The lines are in `data/dialogue.txt`
(`situation | text`) — add or change them freely, keep them short. The bubbles are PNGs in `assets/custom/`:
`bubble_s/m/l/xl/xxl` for words (the smallest one that fits is used) and `bubble_alert`, `bubble_surprise`,
`bubble_heart`, `bubble_sick`, `bubble_sleep` for icons.

## Tests

```
python test_layout.py   # the plan, for 1, 5 and 100 doctors: every tile reachable, patients' routes
python test_sim.py      # with real threads, 8x faster (about 1.5 minutes)
```

`test_sim.py` checks the dispatcher's choices and interruptions, the line and the theatres semaphore, the whole
flow with arrivals (nobody ever shares a tile, no patient behind a counter or in a staff room), pre-emption, the
surgery route through disinfection, and every patience reaction.

## Performance

`python bench.py` runs the simulation without the UI with more and more people and measures how well the actor
threads keep up with the rate they ask for. Below 80 % the hospital is too big for the machine. Results on a
Ryzen 5 3600, Python 3.14 (one process, so all actor threads share the GIL):

| doctors | patients | threads | keeping up, everyone at 30/s | keeping up, still people at 10/s | snapshot to the UI |
|--------:|---------:|--------:|-----------------------------:|---------------------------------:|-------------------:|
| 7 | 20 | 41 | 96 % | 100 % | 0.3 ms, 10 KB |
| 25 | 200 | 239 | 99 % | 100 % | 1.0 ms, 50 KB |
| 60 | 450 | 524 | 100 % | 100 % | 2.7 ms, 133 KB |
| 80 | 600 | 694 | 90 % | 96 % | 4.7 ms, 166 KB |
| 120 | 900 | 1034 | 64 % | 73 % | 7.2 ms, 239 KB |
| 160 | 1200 | 1374 | 35 % | 32 % | — |

* **The limit is around 900 actor threads.** Past that, Python's GIL (only one thread runs Python code at a
  time) can't serve every thread on time. Those numbers also include absurd crowding (900 patients in two waiting
  rooms), so people keep blocking each other and re-planning their routes.
* **Optimisation:** people standing or sitting still update 10 times a second instead of 30 (`IDLE_HZ`); walking
  ones still update 30 times, so movement is just as smooth. With 694 threads that took them from 90 % to 96 %
  on time, with 1034 from 64 % to 73 %: the 80 % limit moved from roughly 800 to roughly 950 threads (estimated
  between the measured sizes).
* The snapshot the UI gets 60 times a second stays small (a few ms to build and serialise even with a thousand
  people), so the communication between processes is not the bottleneck.
* On the UI side, the FPS counter (top left) turns red below `MIN_FPS`; `TARGET_FPS` only caps it — above 60
  nothing looks smoother, since the simulation sends 60 snapshots a second.

## Credits

* [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) — Soppycraft
* [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) — Jephed, [Game Between The Lines](https://gamebetweenthelines.com/)
* [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) — [Chucky Assets](https://chuckyassets.itch.io/)
* [m5x7](https://managore.itch.io/m5x7) font, used in the speech bubbles — Daniel Linssen, CC0 (in `assets/fonts/`)

## Ideas for later

Several floors and freely editable plans, parking, a working storage room (nurses and cleaners fetching
supplies), bathrooms and other needs (bar, vending machines, TV), patients wandering where they shouldn't.
