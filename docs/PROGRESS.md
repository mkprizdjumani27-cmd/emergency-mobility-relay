# Progress — Vehicle Allocation Module

**Snapshot: 2026-11-27.** What the module must deliver (per the PRD), what is
built and verified today, what is missing, and how much is left.

**TL;DR:** Input handling and hospital selection are complete and tested.
The vehicle-assignment optimizer, route generation, output files, and the
baseline-vs-flood report are **not started**. Roughly **a third** of the
module is built; the optimizer is the hardest remaining piece.

---

## The task

Given a flood scenario for central Hyderabad, assign the available vehicles
to pickup points so the most people reach a hospital, and report who cannot
be served and why. Concretely, the module must:

1. For each pickup, compute travel time to every reachable hospital and pick
   the nearest; a pickup with no reachable hospital is marked `no_route`.
2. Assign vehicles to pickups with an optimizer: one trip per vehicle,
   never exceeding capacity or remaining demand, maximizing people served
   (weighted by priority and vulnerable-passenger counts), with travel time
   as a tie-break.
3. Generate the route for each assignment as a node path plus geometry for
   the map.
4. Report unserved demand per pickup with a reason: `no_route`,
   `insufficient_fleet`, or `capacity_exhausted`.
5. Expose everything as **one call** for the app integrator, plus
   CSV/GeoJSON outputs and a summary.
6. Prove it with acceptance tests, run it on the real Hyderabad data, and
   produce baseline-vs-flood comparison numbers for the report.

---

## Built and working (53 automated tests passing)

### Input pipeline — complete

- Reads every input file the PRD specifies: the scenario GraphML,
  `closed_roads.csv`, `pickup_points.csv`, `demand_points.csv`,
  `vehicles.csv`, `hospitals.csv`, `reachability_results.csv` — plus a
  single call that loads a whole scenario directory at once.
- Validates each file against the agreed schema and fails with a precise
  error (file, row, column) on: missing or unknown columns, invalid
  `data_label` values, out-of-range numbers (capacity ≥ 1, speed > 0,
  demand ≥ 0, priority weight > 0), duplicate IDs, node IDs that don't exist
  in the graph, and broken references between files (demand → pickup,
  reachability → pickup/hospital).
- Accepts locations either as graph node IDs or as `lat,lon` coordinates,
  which are resolved to the nearest graph node; the resolution is kept so it
  can be audited.
- Carries `data_label` (`source_derived` / `manual` / `simulated`) on every
  loaded record, so provenance survives through to the outputs.
- Handles the real-OSM quirk where every graph attribute arrives as a
  string (lengths, coordinates, flags) by converting them to native types on
  load — without this, travel time would work on the toy and crash on real
  data.

### Flood-scenario graph handling — complete

- Closed roads are stripped from a **working copy** of the graph, in both
  directions, including all parallel segments of the same road; the
  original graph is never modified.
- The same raw graph serves both modes: **baseline** (closures ignored) and
  **flood** (closures applied) — which is what the baseline-vs-flood
   comparison will need later.
- Graph data problems (missing, negative, or non-numeric road lengths) are
  reported against the offending road, and both modes report them
  identically, so neither mode can pass while the other fails.

### Travel time and best-hospital selection — complete (core-logic step 1)

- For every pickup: shortest-path distance to **every** reachable hospital
  on the scenario graph, with the nearest chosen. Travel time is
  distance ÷ vehicle speed; because each vehicle's speed is uniform across
  the network, "nearest by time" is the same as "nearest by distance" for
  every vehicle — so the choice is made once per pickup and reused.
- The result per pickup carries: chosen hospital, distance, full node path,
  the distances of *all* reachable hospitals, and the pickup's `data_label`.
- A pickup cut off from every hospital (an island, or a dead-end severed by
  a closure) is marked `no_route` and can never be assigned.
- Travel time in minutes is derivable on demand for any vehicle speed
  (this is what the eventual `est_travel_min` output column will use).
- Also provides generic origin→destination distance for
  vehicle-start→pickup legs — the optimizer needs it for its time
  tie-break.
- Proven on the toy town **and** end-to-end on the real
  18,282-node / 47,066-edge Musi-corridor drive graph with its 128
  hospitals and 38 disconnected pockets.

### Test and data foundation — complete

- **Toy dataset:** an 11-node town with deliberately placed edge cases —
  an island pickup (always `no_route`), a closure-sensitive dead-end, demand
  exactly equal to a vehicle's capacity, a demand of 1, a vehicle given as
  `lat,lon`, and a total fleet smaller than total demand (insufficient
  fleet). A README records which case each fixture demonstrates.
- **Real data, pulled ahead of need:** the Musi river corridor drive graph
  (real one-ways) and 128 hospitals, pulled from OpenStreetMap.
- **53 automated tests** covering: schema validation, label propagation,
  closed-road removal (both directions, multigraph-safe), exact travel
  distances, `no_route` marking, baseline-vs-flood difference, a cross-check
  of computed reachability against the known-good reachability file, and the
  real-graph end-to-end run.

---

## Not built yet

- **Vehicle assignment — the optimizer.** The heart of the module: an
  OR-Tools CP-SAT model where each vehicle makes at most one trip to one
  pickup, passengers never exceed min(vehicle capacity, remaining demand),
  the objective maximizes people served weighted by
  `priority_weight`/`vulnerable_count`, travel time breaks ties, and the
  drive from a vehicle's start to its pickup counts in that tie-break only.
  Nothing of this exists yet.
- **Route generation.** The full route per assignment (vehicle start →
  pickup → hospital) as a node path plus geometry for the map
  (GeoJSON, or CSV with a geometry column).
- **Output files.** `assignments.csv`
  (`vehicle_id, pickup_id, hospital_id, passengers, capacity, est_travel_min,
  data_label`), `unserved.csv`
  (`pickup_id, unserved_count, reason` with the three allowed reasons), and
  the summary (total demand, served, unserved, vehicles used).
- **One-call entrypoint.** `run_allocation(graph, pickups, vehicles,
  hospitals, config)` exists with the agreed signature but still raises
  `NotImplementedError` — the app integrator cannot call it yet.
- **Acceptance test suite** — every PRD §4 criterion as a test. The current
  suite already enforces part of four of the seven criteria at the layers
  that exist (no closed road ever on a path; `no_route` marking; label
  propagation into results; baseline and flood differ). The capacity,
  insufficient-fleet, capacity-edge-case, and output-level label criteria
  all need the optimizer and outputs first.
- **Full real-data run.** The travel stage runs on the real graph, but a
  complete scenario run needs the real pickup/demand/vehicle files (see
  "Waiting on others").
- **Baseline-vs-flood comparison and report numbers** — same inputs with
  and without closures, the diff, and the report statistics. Scheduled last.

---

## How much remains

| Area | State |
|---|---|
| Input contracts, validation, loading | **Done** |
| Flood / baseline scenario graph handling | **Done** |
| Travel time + best-hospital selection | **Done** |
| Vehicle assignment optimizer | **Not started** — largest and hardest remaining piece |
| Route generation + map geometry | **Not started** |
| Output files (assignments / unserved / summary) | **Not started** |
| One-call entrypoint for the app | Signature reserved; body **not started** |
| Acceptance tests (PRD §4) | ~4 of 7 criteria partially covered; the rest blocked on the above |
| Full real-data run | Travel stage proven; full run waiting on teammate files |
| Baseline-vs-flood report numbers | **Not started** (scheduled last) |

**Estimate:** by feature area, inputs and scenario handling are complete
(100%), core logic is 1 of 4 steps done, and outputs are 0% — about **a
third of the module built**. The remaining two-thirds is the optimizer (the
one genuinely complex piece) plus route/output plumbing, the integration
call, and the tests that go with them.

In PRD-schedule terms: the spec, contracts, and toy-data phase (days 1–4) is
finished, and the first of the four core-logic steps is done. What remains is
the days 5–10 work — solver, routes, tests, the integration call, and the
comparison/report.

---

## Waiting on others (not blocking development — the toy scenario covers everything until these arrive)

- **`pickup_points.csv`** for the real scenario — Zahraa's pickup-point
  review.
- **Real `demand_points.csv`** — people to move per pickup for the Musi
  corridor.
- **`reachability_results.csv`** — Muhammad's file; format still
  unconfirmed. The module's own reachability check is authoritative and his
  file is only a cross-check, so this never blocks.
- **Scenario graph** — already in hand (Musi corridor pulled from OSM).

## Open decisions (proposed defaults are active — none of these block work)

| # | Question | Default in use |
|---|---|---|
| D1 | One trip per vehicle, or multiple? | One trip (single-leg, no hubs) |
| D2 | Does the drive from a vehicle's start to its pickup count? | Time tie-break only |
| D3 | Hospital capacity? | Not modeled — stated as a limitation in the report |
| D4 | Which input's `data_label` goes on `assignments.csv` (each row mixes vehicle + pickup + hospital + demand)? | The demand row's label |
| — | Short vs. long team-distribution PDF (whether `demand_points.csv` + `vehicles.csv` both exist) | Long version |

---

## Code map

| Path | Role |
|---|---|
| `vehicle_allocation/models.py` | Typed records for every input row (each carrying `data_label`) |
| `vehicle_allocation/loaders.py` | Validating loaders + the one-call scenario loader |
| `vehicle_allocation/travel.py` | Closed-road stripping, travel time, best-hospital selection |
| `vehicle_allocation/allocation.py` | One-call entrypoint — signature reserved, body not started |
| `data/toy/` | Toy-town scenario with the edge cases listed above |
| `data/musi_corridor/` | Real Hyderabad Musi-corridor graph + 128 hospitals |
| `tests/` | 53 tests: smoke, loaders, travel, real-graph end-to-end |
