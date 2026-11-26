# SCHEMAS — Vehicle Allocation Module

**Status: PROPOSED — circulate to the whole team Day 1 (PRD §7: ID/column mismatches are the top integration risk).**
Owner: Uzain. Every rule below applies to *all* files unless noted.

## Conventions (all files)

- **Encoding:** UTF-8, comma-separated, header row required.
- **IDs:** stable string IDs (`pickup_id`, `vehicle_id`, `hospital_id`, node IDs matching the GraphML). No whitespace, no renaming between files — the same ID appears verbatim in every file that references it.
- **`data_label` column: present in every file, one of `source_derived` | `manual` | `simulated`.** Values must survive through to outputs (PRD §4).
- **Missing values:** empty cell = missing. No sentinel values like `-1` or `N/A`.
- **Nodes:** every node reference is a GraphML node id from `hyderabad_drive.graphml` (or the toy graph). Where a file allows `node` **or** `lat,lon`, the loader resolves lat/lon to the nearest graph node and records the resolution.

## Input files

### `vehicles.csv` — defined by Uzain (PRD §1)

| column | type | notes |
|---|---|---|
| `vehicle_id` | str | unique |
| `type` | str | e.g. bus, van, boat, ambulance (free text, used only for reporting) |
| `capacity` | int ≥ 1 | max passengers per trip |
| `start_node_or_lat_lon` | str | graph node id, or `lat,lon` |
| `speed_kmh` | float > 0 | used for travel-time estimation |
| `data_label` | enum | see conventions |

### `pickup_points.csv` — reviewed by Zahraa

| column | type | notes |
|---|---|---|
| `pickup_id` | str | unique |
| `node` | str | graph node id (or `lat`/`lon` columns — final form TBD with Zahraa) |
| `data_label` | enum | |

*Pending:* Zahraa owns the space / access / flood-safety scoring columns; those are review notes, **not** allocator inputs (PRD §1 note 2).

### `demand_points.csv`

| column | type | notes |
|---|---|---|
| `pickup_id` | str | FK → `pickup_points.csv` |
| `demand_count` | int ≥ 0 | people to move from this pickup |
| `vulnerable_count` | int ≥ 0, **optional** | elderly/patients/limited-mobility (PRD §5, default 0) |
| `priority_weight` | float > 0, **optional** | isolation/urgency weight (PRD §5, default 1.0) |
| `data_label` | enum | the optional columns will be `manual` or `simulated` |

### `hospitals.csv`

| column | type | notes |
|---|---|---|
| `hospital_id` | str | unique |
| `node` | str | graph node id (or lat/lon) |
| `data_label` | enum | |

### `closed_roads.csv`

| column | type | notes |
|---|---|---|
| `u` | str | GraphML edge endpoint |
| `v` | str | GraphML edge endpoint |
| `data_label` | enum | |

*Open:* confirm with Muhammad whether edges are `(u,v)` directed or unordered, and whether a `reason` column exists (kept if present, ignored by the allocator).

### `reachability_results.csv` — from Muhammad

*Unconfirmed — Muhammad owns this file.* Working assumption: one row per (pickup, hospital) pair that is reachable, or per-pickup summary. The allocator's own NetworkX reachability check (PRD §2.1) is authoritative; this file is used to cross-check.

### Graph: `hyderabad_drive.graphml`

Closed roads are **removed before** travel-time computation, per PRD §2.1.

## Output files (PRD §3)

### `assignments.csv`

`vehicle_id, pickup_id, hospital_id, passengers, capacity, est_travel_min, data_label`

### `unserved.csv`

`pickup_id, unserved_count, reason` — `reason` ∈ `no_route` | `insufficient_fleet` | `capacity_exhausted`

### Routes

GeoJSON (or CSV with geometry column): node path + geometry per assignment.

### Summary

total demand, served, unserved, vehicles used.

## Wording rule (PRD §7)

Every output describing avoided roads says **"avoiding roads marked closed"** — never "safe routes".
