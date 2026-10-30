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

The hospital is drawn in the free [Tiled](https://www.mapeditor.org/) map editor, one map per floor
([`maps/floor0.tmj`](maps/floor0.tmj), `floor1.tmj`, `floor2.tmj`); nothing about the building is in the code. The
game shows one floor at a time: `PgUp` / `PgDn` or the floor buttons, and the view follows whoever you've clicked
when they go up or down.

* **Ground floor:** emergencies. Street, reception, triage, waiting rooms, bar, Emergency (with its ambulance
  bay), storage right next to it (the stretchers start there) and the Boss's control room.
* **First floor:** the doctors' rooms.
* **Second floor:** the surgeries (with their disinfection rooms) and rest.

Storage was on the second floor at first. With 4 doctors, a minute of the hospital, storage next to Emergency:
Red patients waited 72–79 s for a doctor instead of 101–107 s (two runs each); upstairs, every Red patient cost
the elevator two more trips.

Every floor has the **Main Hall** in the same place, with **stairs** and an **elevator** in it. People walk up
and down the stairs (from a stairs tile straight to the same place on the next floor; nobody idles on them);
stretchers can't, so they take the elevator: one stretcher at a time, first come first served, the car moving a
floor every `LIFT_FLOOR_S` (see the `Elevator` monitor). Inside it, nurses and stretcher are out of sight until they
come out on the other floor.

On the ground floor, the hall has the Boss's round **Control Room** in the middle
(sealed: no doors; the Boss sits in the exact centre and nobody else goes in). The main corridor runs through the
hall and out to both sides, and ends in an exit at each end: whoever has seen a doctor leaves that way, so they
don't walk back against the people coming in through reception and the waiting rooms. Under the hall is the base
block: street → reception → a triage room on each side → the two waiting rooms, plus the bar; Emergency and
storage are on top of the hall.

**Editing the building.** Open a floor (`maps/floorN.tmj`, 0 = ground floor) in Tiled (turn on *View → Snapping →
Snap to Grid*). Another file `floor3.tmj` is another floor. Each map has three layers, and the game reads them all
the next time it starts:
* **ground** — paint tiles from the `hospital` tileset. `wall` is a wall, `door` a door, `floor_grass` outside;
  any other floor tile is floor (each room type has its own look). `stairs` lead to the `stairs` tile at the same
  place on the floor above and below.
* **rooms** — a rectangle over each room, walls included (an ellipse for a round one). Its *name* is shown in the
  game; its *class* is its kind: `waiting`, `reception`, `triage`, `doctor`, `emergency`, `surgery`, `disinfection`,
  `storage`, `rest`, `bar`, `control`, `corridor`, `street`. Custom properties: `partner` (the room it works with:
  triage → its waiting room, surgery → its disinfection room) and `walls` = false for the street. Rectangles of
  class `staff_only` mark where patients never go (behind the counters). Streets named `Exit` are the ways out
  after a visit; the one named `Ambulance Bay` is where ambulances leave patients.
* **furniture** — drag pieces from the tileset. A piece takes up the tiles its tile says (properties `w`, `h`),
  unless the piece has its own `w`, `h`. `stretcher` pieces are where stretchers park. An `elevator` stops on
  every floor that has one at the same place; it opens downwards, onto three free tiles (where a stretcher gets in
  and out).

The staff follows the map: one doctor per doctor's room (desk with an office chair and a patient's chair, and an
exam bed), one receptionist per office chair at reception, one triage nurse per office chair in triage, one
operating room per surgery (with an operating table). A piece that touches a door or sits across two rooms is left
out, and so is decoration that would leave a way through one tile wide. `python test_layout.py` checks the maps
work: every room reachable, stairs between each floor and the next, an elevator to every floor, the patients'
routes, every desk and bed usable.

Everything else you can tune is in [`config.py`](config.py): nurses, security, cleaners, arrivals per minute,
surges, how long things take, patience, the dispatcher's weights, fatigue, `TIME_SCALE` to speed everything up.

## Architecture

### Four processes

```
                 positions (shared memory, seqlock, 60/s)        patients, surges (Queue)
   UI process  <=========================  Simulation process  <-------------------------  Arrivals process
   (main.py)   <-------------------------  (sim.py)            -------------------------->
               ------------------------->        |               doors open (Event), speed (Value)
                 words (Queue, 10/s); commands   |  events, summary (Queue)
                                                 v
                                            Log process (eventlog.py) -> hospital.log
```

* **UI** only draws. It never touches the actors: every frame it reads where everyone is from shared memory
  (`sim.read_positions`: one fixed-size row of numbers per person, `actors.ROW`) and puts that together with the
  latest words from the queue (names, speech, cards, stats; `WORDS_HZ` times a second). The shared block is a
  **seqlock** (`sim.write_positions` / `read_positions`): the simulation, its only writer, makes a counter odd
  while it writes and even when done; the UI keeps a copy only if it saw the same even counter before and after
  (else it copies again). The writer never waits for a reader, not even one that froze or died mid-copy (with a
  lock, that would have blocked the simulation for good), and the counter is a version too: a frame that finds the
  one it already has skips the copy (83 % of them, measured). Measured against the lock it replaced (`mp.Array`,
  writer and reader as fast as they can): about 29 µs a write either way, since the lock was only held for
  microseconds; what changed is that the writer can't be blocked, and the skipped copies. If the simulation
  process dies, the UI says so on screen.
  `test_seqlock` runs a writer in another process at full speed while the test reads: never a copy with rows from
  two different writes.
* **Simulation** owns the hospital: the plan, the shared structures and one thread per actor.
* **Arrivals** decides when people walk in (Poisson arrivals, with random surges) and what they come with
  (`data/complaints.csv`). It follows the simulation's speed (a shared `Value`, paused too at the end of a day) and
  sleeps on a shared `Event`, "doors open", from midnight until the next day starts.
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
| `Elevator` | monitor (`Condition`) + the car's own thread | stretchers between floors: they call it and wait their turn (first come, first served); it goes to whoever's turn it is, waits until the stretcher is in, takes it to its floor and waits until it's out. A stretcher waits its turn a few steps away and holds the elevator until it's out, so two never block each other at its door |
| disinfection rooms | counting `Semaphore` (one per room) | as many doctors washing at once as the room has sinks |

**Deadlocks.** No thread ever holds two of those locks at the same time, so there is no lock-ordering deadlock.
People blocking each other in a narrow spot (each waiting for the other's tile) is the other kind: whoever has
been blocked too long steps aside and finds a new route.

### A patient's visit ([`patients.py`](patients.py))

street → reception line → registration (says why it came) → waits for triage → triage nurse assigns the urgency
(Green / Yellow, from the complaint) → its waiting room → a doctor calls it → consultation → street. Red patients
can't walk: they come by ambulance, onto a bed in Emergency, and always go to surgery on a stretcher.

While it waits, its **patience** runs down and its **condition can get worse** (Green → Yellow; never Red: it walked
in), Yellow and
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

**Who goes first** when there are more patients than doctors: always the most urgent colour first, like real triage
(the Manchester system used in Portuguese and Spanish emergency rooms). Within a colour it's a priority with two
weights: minutes waited and impatience (how close the patient is to giving up). It starts as arrival order.

**Days, money and the bonus.** The simulation runs in days of `DAY_HOURS` hours of `HOUR_S` simulated seconds
(24 × 30 s), from day 0, kept by its own thread (`Hospital.run_day`): it pays the wages by the hour, closes the
doors at midnight, and when the last patient is gone it writes the day's report and pauses the world until the
next day is started (`next_day`; in the game, the report's button or Enter; pause can't skip it). A treated
patient pays `PRICE` by urgency (+ `SURGERY_PRICE`), all of it if it saw a doctor within `ON_TIME_S`, less the later
(down to `LATE_PAYS` of it); nothing if it gave up. Each payment is credited to whoever did each step for that
patient (`SHARE`: reception, triage, stretcher, doctor); security and cleaners get `CREDIT` per fight stopped and
mess cleaned. Staff cost `WAGE` per hour (`ON_CALL_PAY` times that if called in). The staff bonus is `BONUS_SHARE`
of the day's profit, shared out by those credits. The staff don't decide anything: they do what the Boss gives
them, so making the bonus big is the Boss's job.

**What the Boss learns.** `learn.py` plays whole days and keeps what makes more profit (the bonus is a share of it).
Its policy has four numbers: the two weights above, and when to call in staff on call (`CALL_IN_AT`: how many
tasks more than free staff) and when to send them home (`SEND_HOME_AFTER_S` with nothing waiting). It's an
**evolution strategy**: every hospital plays the same day (same seed) with the policy nudged a bit (half one way,
half the opposite), each one's score is that day's profit, and the policy moves towards the nudges that made more.
Some hospitals play other days with the learned policy and with the one it started from: that difference
(`vs_start`, its own chart) is the fair test of whether what it learned is actually better.

```
python learn.py 8 4       # 4 hospitals at once at x8 (one process each); Ctrl+C stops it, run it again to go on
python charts.py          # graphs of how it's going so far (charts.png), also while learn.py runs
```

Don't go above x8: people couldn't move as fast as they should. A day takes about 2.5 minutes at x8. After every
round it saves `boss_brain.json` (the game starts from it too) and a row in `learning.csv`. In PowerShell, at low priority:
`cmd /c start "learn" /low python learn.py 8 4`.

**Staff on call** (`config.ON_CALL`): when tasks pile up for a role (by the learned `call in at`), the Boss calls
someone in (they walk in from the street; on-call doctors get the rooms left empty for them) and sends them home
once it has been quiet for a while (the learned `send home after`).

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
* **Ambulances:** every Red patient arrives by ambulance (and needs surgery), already triaged, onto a bed in
  Emergency, through its own door at the back (the ambulance bay): they never walk.
* **Stretchers** (`config.STRETCHERS`) are parked upright in storage, where the map's `stretcher` pieces are.
  Whoever parks one ends up on a side it can walk away from (never a corner the stretcher would shut), idle staff
  never stand next to the parked ones, and `test_layout.py` checks every parking spot can be used that way. The dispatcher hands out the two nurses of
  a stretcher together or not at all: giving one now and the other later would leave nurses waiting in storage for a
  partner who's busy with another half-started stretcher (hold and wait). Trains returning a stretcher claim their
  parking spot first, so two never head for the same one.
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
| `maps/` | the building, one Tiled map per floor (`floor0.tmj`...), and the tileset with every floor, wall, stairs and furniture PNG (`hospital.tsj`) |
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
Ryzen 5 3600, Python 3.14 (one process, so all actor threads share the GIL). These were measured when the building
still grew with the number of doctors; `bench.py` now keeps the map's staff and adds patients:

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
* Moving data to the UI costs 2.1 % of a CPU core with 68 people (5.3 % when everything went through the queue
  60 times a second): positions by shared memory, words by queue. Since the seqlock's version, the UI also skips
  the copy in the 83 % of frames that have nothing new.
* On the UI side, the FPS counter (top left) turns red below `MIN_FPS`; `TARGET_FPS` only caps it — above 60
  nothing looks smoother, since the simulation writes positions 60 times a second.

## Credits

* [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) — Soppycraft
* [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) — Jephed, [Game Between The Lines](https://gamebetweenthelines.com/)
* [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) — [Chucky Assets](https://chuckyassets.itch.io/)
* [m5x7](https://managore.itch.io/m5x7) font, used in the speech bubbles — Daniel Linssen, CC0 (in `assets/fonts/`)

## Ideas for later

Parking, a working storage room (nurses and cleaners fetching
supplies), bathrooms and other needs (bar, vending machines, TV), patients wandering where they shouldn't.
