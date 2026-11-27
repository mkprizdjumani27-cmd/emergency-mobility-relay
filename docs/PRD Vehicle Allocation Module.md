# PRD: Vehicle Allocation Module

Emergency Mobility Relay System | WFEO Engineering Hackathon 2027

**Owner:** Uzain (Technical Lead / Vehicle Allocation) **Goal:** Given a flood scenario, assign available vehicles to pickup points so the most people reach a hospital, and report who can't be served and why.

## Notes before you build

**The two team-distribution PDFs don't fully agree.** The "Uzain" blocks match, but the longer version adds `demand_points.csv` and `vehicles.csv` (Uzain defines the vehicles.csv format), while the shorter one folds demand into pickup points. This PRD assumes the longer version. Confirm with the lead.

**The teammate's hub suggestion conflicts with scope.** The proposal and both PDFs say no transfer hubs and only direct single-leg trips. Hub selection would break that. The useful half is kept: prioritizing isolated, urgent zones and vulnerable passengers (elderly, patients, limited mobility) as a priority weight in the allocator (section 5). The space, access and flood-safety scoring of locations belongs to the pickup-point review (Zahraa).

## 1. Inputs

- Scenario graph from Muhammad (`hyderabad_drive.graphml` with closed roads removed, via `closed_roads.csv`)
- `reachability_results.csv` and `hospitals.csv`
- `pickup_points.csv` (reviewed by Zahraa) and `demand_points.csv` (people to move per pickup)
- `vehicles.csv`, which Uzain defines

Proposed `vehicles.csv` columns: `vehicle_id, type, capacity, start_node_or_lat_lon, speed_kmh, data_label`

Every file carries a `data_label` of source-derived, manual or simulated. The same IDs and column names are used in every file.

## 2. Core logic (MVP)

1. For each pickup, compute the travel time to every reachable hospital on the scenario graph (NetworkX). Pick the best hospital, which is the nearest by time. If none is reachable, mark the pickup unserved with reason `no_route`.
2. Solve the assignment with OR-Tools (CP-SAT is the easiest fit). Each vehicle makes at most one trip to one pickup, and passengers carried can't exceed capacity or remaining demand. Maximize people served (weighted by priority, see section 5), with a small tie-breaker that minimizes travel time.
3. Generate the route for each assignment as the node path plus geometry for the map.
4. Compute unserved demand per pickup with a reason: `no_route`, `insufficient_fleet` or `capacity_exhausted`.

**Decisions to confirm with the lead:**

- One trip per vehicle (simplest, matches "single-leg"). Multiple trips per vehicle would be a stretch goal.
- Whether the vehicle's drive from its start point to the pickup counts. Suggested: count it in the time tie-breaker only.
- Hospital capacity isn't modeled (no data), so state that as a limitation.

## 3. Outputs

- `allocation.py` exposes one function, roughly `run_allocation(graph, pickups, vehicles, hospitals, config)`, which returns assignments, routes, unserved and a summary. This gives the app integrator one clean call.
- `assignments.csv`: `vehicle_id, pickup_id, hospital_id, passengers, capacity, est_travel_min, data_label`
- `unserved.csv`: `pickup_id, unserved_count, reason`
- Route results as GeoJSON or a CSV with a geometry column
- Summary: total demand, served, unserved, vehicles used

## 4. Tests and acceptance criteria

- No vehicle carries more passengers than its capacity.
- No assigned route uses a closed road. Check edge by edge against `closed_roads.csv`.
- No-route case: a pickup cut off from all hospitals shows as unserved with `no_route` and is never assigned.
- Insufficient fleet: demand exceeds total capacity, and the leftover shows as unserved.
- Capacity edge cases: demand exactly equal to capacity, demand of 1, and a vehicle larger than demand.
- Baseline vs flood: the same inputs run with and without closures give different results (needed for Days 9-10).
- Simulated inputs keep their label through to the outputs.

## 5. Absorbing the teammate's idea (optional, low cost)

Add two optional columns to `demand_points.csv`: `vulnerable_count` and `priority_weight` (for isolation and urgency). The solver's objective becomes weighted people served, and the default weight of 1 gives the plain version. For the report this lets the team say the allocator prioritizes isolated communities and vulnerable passengers, with almost no added complexity. These values would be manual or simulated, so label them that way.

Two gaps to watch: prioritizing some groups means others get left behind, so show that tradeoff in the results. And "last-mile accessibility" depends on the pickup point itself, so that part goes in the pickup-point review notes.

## 6. Timeline (from the PDFs)

- **Days 1-4:** Draft the `vehicles.csv` spec and a toy dataset (fake graph, pickups, vehicles) so work isn't blocked waiting for the real graph.
- **Days 5-7:** Build the solver, route generation and tests.
- **Day 8:** Integration with the app.
- **Days 9-10:** Baseline vs flood comparison and report numbers.

## 7. Risks

- The road graph or reachability file arrives late. The toy dataset covers this.
- ID or column mismatches. Send the schemas to the whole team on Day 1.
- Routes look "safe". Use "avoiding roads marked closed" wording in every output.
