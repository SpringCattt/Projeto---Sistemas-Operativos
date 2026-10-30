# Hospital simulator — Operating Systems project

A pixel-art hospital where **every person is a thread**. Patients arrive, register, get triaged, wait, see a
doctor (or go to surgery) and leave. Doctors, nurses, receptionists, security and cleaners do their jobs, and the
**Boss** — sitting in the round control room at the centre — decides who does what.

It's the project for *Comunicação e Sincronização entre Processos* (Universidade de Aveiro): **processes** that
only talk through queues, and **threads** that share memory and synchronise with locks, conditions, events and
semaphores.

![The control room, with the Boss in the middle, and people talking](docs/screenshot.png)

## Run it

1. Install Python 3.12+ and the game library:
   ```
   pip install pygame-ce
   ```
2. Download the three free asset packs (their licences don't allow uploading them here) and unzip them into
   `hospital2d/assets/third_party/`:

   | Unzip into | Pack | Author |
   |---|---|---|
   | `hospital/` (with `Pngs/` inside) | [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) | Soppycraft |
   | `characters/` (`000.png` … `039.png`) | [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) | Jephed — Game Between The Lines |
   | `moderntown/` (with `sprites/` inside) | [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) | Chucky Assets |

   Everything else (floors, walls, reception desk, speech bubbles…) is our own art, already in
   `hospital2d/assets/custom/`.
3. Start it:
   ```
   cd hospital2d
   python main.py
   ```

**Controls:** WASD or right-drag to move · mouse wheel to zoom · click a person (or any room) to see what's going
on · `B` see what the Boss is doing · `L` event log · `ESC` close · `Tab` **admin tablet** (pause, speed, send
patients, trigger events, hire/send home staff) · `Space` pause · `+`/`-` speed · `Enter` start the next day.

**Days and money.** It starts on day 0; a day is 24 hours, one every 30 seconds (12 minutes at x1). At midnight the
doors close, the patients still inside are seen to, and when the last one is gone the day ends: the game pauses on
the day's report (what the patients paid, the wages, the profit and the staff bonus) until you start the next day.
Patients pay by urgency, less the longer they waited for a doctor, nothing if they gave up; staff are paid by the
hour; 30 % of the profit is the staff bonus, shared out by what each one brought in. All the numbers are in
[`config.py`](hospital2d/config.py) (`PRICE`, `WAGE`, `BONUS_SHARE`...).

To make it calmer or bigger, change [`hospital2d/config.py`](hospital2d/config.py): `TIME_SCALE` (0.5 = half
speed), `N_PATIENTS`, `ARRIVALS_PER_MINUTE`… The building itself is a map you draw in
[Tiled](https://www.mapeditor.org/), one map per floor: [`hospital2d/maps/`](hospital2d/maps/). Three floors:
emergencies (and the stretchers' storage) on the ground floor, doctors' rooms on the first, surgeries and rest on
the second; `PgUp` /
`PgDn` (or the floor buttons) show another one.

## How it works, in short

**4 processes.** They talk through queues, plus **shared memory** for where everyone is and two shared flags:

```
 Arrivals ──patients──> Simulation ══positions (shared memory, seqlock, 60/s)══> UI (what you see)
    ^                       ├──────words: names, speech, cards (queue, 10/s)──> UI
    └─ "doors open" (Event) ┘───────events──────> Log (hospital.log)
```

Each kind of data goes the way that suits it. Positions are a few numbers per person, the same size every time,
changing every frame: the simulation writes them straight into memory both processes see. Instead of a lock it's a
**seqlock**: the simulation makes a counter odd while it writes and even when it's done, and the UI keeps a copy
only if it saw the same even number before and after (else it copies again). The writer never waits for a reader,
not even for one that froze or died half-way (with a lock, that would block the simulation for good), and the
counter is also a version: 83 % of the UI's frames find nothing new and skip the copy. Words have any length and
change now and then: they go through a queue, which copies them and does its own synchronisation. Measured with
68 people, moving data to the UI went from 5.3 % of a CPU core (everything through the queue, 60 times a second)
to 2.1 %.

At midnight the simulation clears a shared **Event**, "doors open", and the arrivals process sleeps on it until the
next day (it used to keep sending patients nobody let in). If the simulation process dies, the window says so
instead of showing a world frozen for no reason.

**Inside the simulation, one thread per person.** Each one runs its own routine step by step. A patient's is:

> street → reception line → registration → triage (Green / Yellow) → waiting room → doctor → exit

Red patients can't walk: they come by ambulance, onto a bed in Emergency, and from there on a stretcher to surgery.

They share the building, so they synchronise:

| Problem | Solution |
|---|---|
| Two people stepping onto the same tile | a **lock**: checking and taking a tile is one single step |
| The reception line | a **monitor** (`Condition`): receptionists wait until someone is first in line |
| Registered patients waiting for a triage nurse | a **queue** (producer–consumer) |
| A doctor calls a patient and waits for them | two **events**: "I'm here" and "you can go" |
| Only so many operating rooms and sinks | **semaphores** |
| Two nurses meet at storage before bringing a stretcher | **condition + events** (rendezvous) |
| One elevator for every stretcher, between floors | a **monitor** with its own thread (the car): first come, first served, one stretcher at a time |
| Who handles each task | the **Boss**, a thread that scores everyone by distance, fatigue and how busy they are |
| Two people blocking each other in a doorway | whoever has waited too long steps aside (no deadlock) |

**Things happen:** surges of patients, patients getting worse while they wait, people running out of patience
(they leave, complain at reception or start a fight — security comes), vomit (cleaners come), doctors dropping a
Green patient for a Red one, tired staff going to rest. Everyone talks in speech bubbles.

**Limit:** about 900 people (threads) on a Ryzen 5 3600 before the simulation can't keep up (Python's GIL).

More details — every file, every synchronisation, tests and performance numbers — in
[`hospital2d/README.md`](hospital2d/README.md). The first version of the project (Tkinter) is in
[`SO Projeto Hospital/`](SO%20Projeto%20Hospital/).

## Tests

```
cd hospital2d
python test_layout.py   # the building
python test_sim.py      # the simulation with real threads (~1.5 min)
python bench.py         # how far it scales
```

**The Boss learns** to make the biggest staff bonus, that is, the most profit in a day. What it decides: who to see
first among patients of the same colour (the most urgent colour always goes first, like real triage), and when to
**call in staff on call** and send them home (more staff, more wages; fewer, longer waits and patients who give
up). `python learn.py 8 4` plays 4 hospitals at once without the window, a whole day each round, trying small
changes and keeping what makes more (Ctrl+C stops it), and `python charts.py` draws how it's going (needs
`pip install matplotlib`).

## Credits

- [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) — Soppycraft
- [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) — Jephed, [Game Between The Lines](https://gamebetweenthelines.com/)
- [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) — [Chucky Assets](https://chuckyassets.itch.io/)
- [m5x7](https://managore.itch.io/m5x7) font (speech bubbles) — Daniel Linssen, CC0; included in `hospital2d/assets/fonts/`
