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
| `hospital/` (with `Pngs/` inside) | *Top Down Hospital* (itch.io) |
| `characters/` (`000.png` … `039.png`) | *2D Top Down Pixel Art Characters* by Jephed |
| `moderntown/` (with `sprites/` inside) | *Modern Town Tileset — Free Sample* by Chucky Assets |

**Controls:** WASD / arrows or right-drag to move · mouse wheel to zoom · click a person for their card, or any tile
of a room to see who is inside (the list is clickable) · `L` event log on/off · `B` what the Boss is doing
(also shown when you click the Boss) · `ESC` closes the card.

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
task (consultation, fight, mess), most urgent first, it scores every actor of the right role and picks the lowest:

```
score = walking distance to the task × W_DISTANCE + fatigue × W_FATIGUE  (+ W_BUSY if busy) (+ W_RESTING if resting)
```

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
| `tools/make_assets.py` | generated some of our own PNGs (`assets/custom/`) in the hospital pack's palette |
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

* *Top Down Hospital* asset pack (itch.io).
* *2D Top Down Pixel Art Characters* — Jephed, Game Between The Lines, https://gamebetweenthelines.com/
* *Modern Town Tileset — Free Sample* — Chucky Assets.

## Ideas for later

Several floors and freely editable plans, parking, a working storage room (nurses and cleaners fetching
supplies), bathrooms and other needs (bar, vending machines, TV), patients wandering where they shouldn't.
