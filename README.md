# Hospital simulator — Operating Systems project

A pixel-art hospital where **every person is a thread**. Patients arrive, register, get triaged, wait, see a
doctor (or go to surgery) and go home. Doctors, nurses, receptionists, security and cleaners do their jobs, and the
**Boss**, in the round control room at the centre, decides who does what.

![The control room, with the Boss in the middle, and people talking](docs/screenshot.png)

## The story

This started as the project for *Comunicação e Sincronização entre Processos* (Universidade de Aveiro), and that
version was handed in a long time ago (it's still here: [`SO Projeto Hospital/`](SO%20Projeto%20Hospital/), in
Tkinter). I came back to it to understand process synchronisation better, and to make it more interesting to
watch: pixel art, animations, a city with cars, day and night.

At the same time I've been learning AI algorithms in class, so I tried them on the Boss, to see if it could make
the hospital run better. That's where all the money comes from: patients pay, staff cost wages, and the Boss
learns to make the most profit.

Fair warning: I had never made a map before, so the building isn't exactly *aesthetic*. It works, though.

## Run it

1. Python 3.12+ and the game library: `pip install pygame-ce`
2. The art packs (their licences don't allow uploading them): unzip each into `hospital2d/assets/third_party/`:

   | Folder | Pack |
   |---|---|
   | `hospital/` (with `Pngs/`) | [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) — Soppycraft |
   | `characters/` (`000.png` … `039.png`) | [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) — Jephed |
   | `moderntown/` (with `sprites/`) | [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) — Chucky Assets |
   | `citypack/`, then `python slice_city.py` once in `hospital2d/` | [City Pack — Top Down](https://nyknck.itch.io/citypackpixelart) — NYKNCK |
   | `furniture/` (`Furniture (N).png`) | a furniture pixel-art pack (demo) |

   The rest of the art (floors, walls, roads, bubbles, cars in each colour...) is ours, in `hospital2d/assets/custom/`.
3. `cd hospital2d` and `python main.py`

**Controls:** WASD or right-drag to move · wheel to zoom · click a person or a room to see what's going on ·
`PgUp`/`PgDn` change floor · `B` what the Boss is doing · `L` event log · `Tab` admin tablet (pause, speed, send
patients, events, hire staff) · `Space` pause · `+`/`-` speed · `Enter` next day.

## What happens

Three floors, symmetric about the middle of the hall: emergencies on the ground floor (reception, triage, waiting
rooms, Emergency, storage, the Boss's control room), the doctors' rooms on the first, operating rooms and the
staff's rest room on the second. Around it, a city: roads, cars that park and keep right, taxis, ambulances.

A patient's day: **street → reception → triage (Green / Yellow) → waiting room → doctor → home**. Red patients
come by ambulance, go to a bed in Emergency, to surgery on a stretcher (elevator included) and home in a
wheelchair, to a car that comes for them.

Things go wrong on purpose: surges of patients, patients getting worse while they wait, people running out of
patience (they leave, complain or start a fight: security comes), vomit (cleaners come), tired staff going to rest.
Everyone talks in speech bubbles.

**A day** is 24 hours of 60 seconds (24 minutes at x1), from day 1. At 23:00 the doors close; from midnight,
whoever no doctor has called yet goes home (Reds excepted) and every extra minute is overtime (wages x1.5). When
the last patient leaves, the day's report: what patients paid, wages, profit, the staff bonus. All the numbers are
in [`hospital2d/config.py`](hospital2d/config.py).

## The Operating Systems part

**4 processes**, talking through queues, plus **shared memory** for where everyone is:

```
 Arrivals ──patients──> Simulation ══positions (shared memory, seqlock, 60/s)══> UI (what you see)
    ^                       ├──────words: names, speech, cards (queue, 10/s)──> UI
    └─ "doors open" (Event) ┘───────events──────> Log (hospital.log)
```

Positions go through shared memory with a **seqlock** (the writer never waits for the reader, even if the reader
dies); text goes through a queue. That took moving data to the UI from 5.3 % of a CPU core to 2.1 %.

**Inside the simulation, one thread per person**, sharing the building:

| Problem | Solution |
|---|---|
| Two people stepping onto the same tile | a **lock**: checking and taking a tile is one step |
| The reception line | a **monitor** (`Condition`) |
| Registered patients waiting for a triage nurse | a **queue** (producer–consumer) |
| A doctor calls a patient and waits for them | two **events**: "I'm here", "you can go" |
| Only so many operating rooms and sinks | **semaphores** (emergencies first) |
| Two nurses meet at storage to bring a stretcher | **condition + events** (rendezvous) |
| Two elevators for the stretchers | a **monitor** each, with its own thread: first there, first served |
| One ambulance bay | a **semaphore**: the next ambulance waits up the road |
| Two cars at a crossing | a crossing is taken whole or not at all (no deadlock); priority to the right |
| Two people blocking each other in a doorway | only one of them steps aside (no deadlock, no dancing) |
| Who does each task | the **Boss**, a thread scoring everyone by distance, fatigue and how busy they are |

**Limit:** about 900 threads on a Ryzen 5 3600 before the simulation can't keep up (Python's GIL).

## The AI part

The **Boss learns** to make the most profit in a day: who to see first among patients of the same colour (the most
urgent colour always goes first, like real triage), and when to **call in staff on call** and send them home (more
staff, more wages; fewer, longer waits and patients who give up). `python learn.py 8 4` plays 4 hospitals at once
without the window, a whole day each round, trying small changes and keeping what makes more (Ctrl+C stops it);
`python charts.py` draws how it's going (needs `pip install matplotlib`).

## More

Every file, every synchronisation, the tests and the performance numbers:
[`hospital2d/README.md`](hospital2d/README.md). The building is a [Tiled](https://www.mapeditor.org/) map per
floor ([`hospital2d/maps/`](hospital2d/maps/)), and everything you see is an image you can edit.

```
cd hospital2d
python test_layout.py   # the building
python test_sim.py      # the simulation, with real threads (~5 min)
```

## Credits

- [Hospital Top-Down Pixel Pack](https://soppycraft.itch.io/top-down-hospital) — Soppycraft
- [2D Top-Down Pixel Art Characters](https://gamebetweenthelines.itch.io/top-down-pixel-art-characters) — Jephed, [Game Between The Lines](https://gamebetweenthelines.com/)
- [Modern Town Tileset — Free Sample](https://chuckyassets.itch.io/modern-town-tileset-free-sample) — [Chucky Assets](https://chuckyassets.itch.io/)
- [City Pack — Top Down — Pixel Art](https://nyknck.itch.io/citypackpixelart) — NYKNCK (buildings, cars, street furniture)
- [m5x7](https://managore.itch.io/m5x7) font (speech bubbles) — Daniel Linssen, CC0
