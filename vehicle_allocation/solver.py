"""CP-SAT allocator (Chunk 3): one trip per vehicle, weighted people served.

Implements PRD §2.2 — "maximize people served (weighted by priority), with a
small tie-breaker that minimizes travel time" — and PRD §5's optional
priority columns, as a pure seam between Chunk 2's travel layer and Chunk 4's
output layer.

Design decisions (lead confirmed 2026-11-27):

* **Two-phase lexicographic solve, never an epsilon blend.** Phase 1
  maximizes weighted people served; phase 2 re-solves with that optimum
  pinned and minimises total trip time. A blend could trade one weighted
  person for minutes; "tie-breaker" means a person always wins. Phase 1's
  remaining time budget carries over to phase 2 so ``time_limit_s`` bounds
  the *whole* solve (the lead's emergency-response constraint: default 12 s,
  middle of the proposed 10-15 s).
* **Vulnerable seats count double.** Value of one passenger = pickup's
  ``priority_weight``; each *vulnerable* passenger earns one extra
  ``priority_weight`` (``vulnerable_bonus``, default 1.0 -> counts 2x) and is
  counted first when a pickup is only partially served (capped at
  ``vulnerable_count`` and at passengers served). Weights/bonus are integers
  scaled x1000: a weight below 0.001 still gets 1 unit (a >0 weight can never
  round to "worth nothing" — the solver would starve that pickup).
* **D2: the drive from a vehicle's start to its pickup enters the time
  tie-break only** — not feasibility, not the objective's main term. The one
  exception is physical: a vehicle that cannot *reach* the pickup at all
  (disconnected component) forms a forbidden pair.
* **``no_route`` pickups are excluded before the model** (Ch2's None-triple;
  PRD §4: never assigned) and surface only as unserved rows.
* **Drive distances: one Dijkstra per vehicle**, not ``path_distance`` per
  (vehicle x pickup) pair — on the real graph that is V runs instead of
  V x M (KNOWN_ISSUES #14 forbids a pickup x vehicle *hospital*-time matrix;
  the drive leg is per-pair by nature, so it is computed from one
  single-source run per vehicle).
* **Preconditions:** ``graph`` is the scenario graph with closed roads
  *already stripped* — the same graph ``best_hospitals`` ran on — with edge
  ``length``s validated (call :func:`vehicle_allocation.travel.strip_closed_roads`).
* **Determinism:** inputs are indexed by id, CP-SAT runs seeded with a
  single worker, and outputs are sorted, so the same scenario solves to the
  same result. Equal optima that tie on *both* terms are still arbitrary.
* **``Assignment.data_label``** is the **demand** row's label (D4's proposed
  default: the carried passengers are the row's subject). D4 pending.
* **``est_travel_min``** = drive + pickup->hospital leg at the vehicle's
  speed — exactly the tie-break time. Which legs ``assignments.csv`` should
  report is a Ch4/output question (logged in KNOWN_ISSUES).
* ``run_allocation``'s wiring, route generation and the ``reason`` enum stay
  in Chunk 4 — this module returns raw assignments + unserved counts only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import networkx as nx
from ortools.sat.python import cp_model

from vehicle_allocation.loaders import SchemaError
from vehicle_allocation.models import DataLabel, Demand, Pickup, Vehicle
from vehicle_allocation.travel import PickupTravel, travel_min

__all__ = [
    "Assignment",
    "SolveResult",
    "SolverConfig",
    "UnservedCount",
    "solve_allocation",
]

#: float ``priority_weight`` / ``vulnerable_bonus`` -> integer model units.
_WEIGHT_SCALE = 1000
#: ``weighted_value`` is reported in these units (weight 1.0 x 1 passenger).
_VALUE_SCALE = _WEIGHT_SCALE * 1000
#: minutes -> centi-minutes for the integer time tie-break term.
_TIME_SCALE = 100


@dataclass(frozen=True)
class SolverConfig:
    """Allocator configuration (the ``config`` argument of ``run_allocation``).

    ``use_priority_weight=False`` gives PRD §5's plain version (every
    pickup counts 1.0); ``use_vulnerable_bonus=False`` / ``vulnerable_bonus``
    turn the vulnerable-seat bonus on its head or off entirely.
    """

    use_priority_weight: bool = True
    use_vulnerable_bonus: bool = True
    vulnerable_bonus: float = 1.0
    use_time_tiebreak: bool = True
    #: Total CP-SAT budget in seconds for both solve phases (lead, 2026-11-27).
    time_limit_s: float = 12.0


@dataclass(frozen=True)
class Assignment:
    """One vehicle's single trip (PRD §2.2) — row of ``assignments.csv``.

    ``capacity`` mirrors the input vehicle capacity (schema column);
    distances/``est_travel_min`` cover drive + pickup->hospital legs.
    """

    vehicle_id: str
    pickup_id: str
    hospital_id: str
    passengers: int
    capacity: int
    drive_distance_m: float
    hospital_distance_m: float
    est_travel_min: float
    data_label: DataLabel  # the demand row's label (D4 default)


@dataclass(frozen=True)
class UnservedCount:
    """Leftover demand per pickup. ``reason`` is Chunk 4's ``unserved.csv`` enum."""

    pickup_id: str
    unserved_count: int
    data_label: DataLabel


@dataclass(frozen=True)
class SolveResult:
    """Solver output: assignments + unserved counts + totals.

    ``weighted_value`` is the achieved objective in weight units (the same
    scale the model maximised, divided back out) — reporting/diagnostic.
    ``optimal`` is False only when a time limit cut the proof short.
    """

    assignments: list[Assignment]
    unserved: list[UnservedCount]
    served: int
    total_demand: int
    vehicles_used: int
    weighted_value: float
    optimal: bool


@dataclass(frozen=True)
class _Pair:
    """A feasible (vehicle, pickup) pair — the model's binaries."""

    vi: int  # index into ``vehicles``
    pid: str  # pickup id: hospital reachable, demand > 0
    drive_m: float  # vehicle start -> pickup, metres
    minutes: float  # drive + pickup->hospital at this vehicle's speed (D2)


def _index(items, attr: str, what: str) -> dict:
    """Key records by ``attr``; duplicates are a contract breach, not data."""
    out = {}
    for item in items:
        key = getattr(item, attr)
        if key in out:
            raise SchemaError(f"{what}: duplicate '{attr}' value {key!r}")
        out[key] = item
    return out


def _check_config(config: SolverConfig) -> None:
    if not config.time_limit_s > 0:
        raise ValueError(f"time_limit_s must be > 0 (got {config.time_limit_s!r})")
    if config.vulnerable_bonus < 0:
        raise ValueError(f"vulnerable_bonus must be >= 0 (got {config.vulnerable_bonus!r})")


def _weight_units(weight: float) -> int:
    """Scaled integer weight; never 0 (a >0 weight would starve the pickup)."""
    return max(1, int(round(weight * _WEIGHT_SCALE)))


def _check_inputs(pickups, travels, demand, vehicles, graph) -> tuple[dict, dict, dict]:
    """Validate cross-file contracts; raise :class:`SchemaError` on any breach."""
    pickup_by_id = _index(pickups, "pickup_id", "pickup_points.csv")
    travel_by_id = _index(travels, "pickup_id", "travels")
    demand_by_id = _index(demand, "pickup_id", "demand_points.csv")
    _index(vehicles, "vehicle_id", "vehicles.csv")

    if set(pickup_by_id) != set(travel_by_id):
        raise SchemaError(
            "travels: pickup ids do not match pickups "
            f"(missing: {sorted(set(pickup_by_id) - set(travel_by_id))}, "
            f"unknown: {sorted(set(travel_by_id) - set(pickup_by_id))})"
        )
    for pid in demand_by_id:
        if pid not in pickup_by_id:
            raise SchemaError(f"demand_points.csv: unknown pickup_id {pid!r}")

    for p in pickups:
        if p.node not in graph:
            raise SchemaError(f"pickup {p.pickup_id!r}: node {p.node!r} is not in the graph")
    for v in vehicles:
        if v.start_node not in graph:
            raise SchemaError(f"vehicle {v.vehicle_id!r}: node {v.start_node!r} is not in the graph")
        if v.capacity < 1:
            raise SchemaError(f"vehicle {v.vehicle_id!r}: capacity must be >= 1 (got {v.capacity!r})")
        if not v.speed_kmh > 0:
            raise SchemaError(f"vehicle {v.vehicle_id!r}: speed_kmh must be > 0 (got {v.speed_kmh!r})")
    for d in demand:
        if d.demand_count < 0:
            raise SchemaError(
                f"demand_points.csv: {d.pickup_id!r}: demand_count must be >= 0 (got {d.demand_count!r})"
            )
        if d.vulnerable_count < 0:
            raise SchemaError(
                f"demand_points.csv: {d.pickup_id!r}: vulnerable_count must be >= 0 (got {d.vulnerable_count!r})"
            )
        if not d.priority_weight > 0:
            raise SchemaError(
                f"demand_points.csv: {d.pickup_id!r}: priority_weight must be > 0 (got {d.priority_weight!r})"
            )
    return pickup_by_id, travel_by_id, demand_by_id


def _build_pairs(graph, vehicles, pickups_by_pid, travel_by_id, eligible: dict[str, Demand]) -> list[_Pair]:
    """Feasible pairs: drive path exists (D2's physical feasibility)."""
    pairs: list[_Pair] = []
    if not eligible:
        return pairs
    for vi, veh in enumerate(vehicles):
        # One Dijkstra per vehicle, not one per (vehicle x pickup) pair.
        drive = nx.single_source_dijkstra_path_length(graph, veh.start_node, weight="length")
        for pid in eligible:
            node = pickups_by_pid[pid].node
            drive_m = drive.get(node)
            if drive_m is None:
                continue  # disconnected: forbidden pair, never assignable
            travel = travel_by_id[pid]
            minutes = travel_min(float(drive_m) + travel.distance_m, veh.speed_kmh)
            pairs.append(_Pair(vi=vi, pid=pid, drive_m=float(drive_m), minutes=minutes))
    return pairs


def _read_carry(solver: cp_model.CpSolver, carry: dict[int, cp_model.IntVar]) -> dict[int, int]:
    """Vehicle-pickup pairs actually used (``carry >= assign`` keeps idle vehicles idle)."""
    return {i: solver.value(var) for i, var in carry.items() if solver.value(var) > 0}


def solve_allocation(
    graph: nx.Graph,
    pickups: list[Pickup],
    travels: list[PickupTravel],
    demand: list[Demand],
    vehicles: list[Vehicle],
    config: SolverConfig | None = None,
) -> SolveResult:
    """Assign vehicles to pickups so the most (weighted) people reach a hospital.

    Args:
        graph: scenario graph with **closed roads already stripped** — the
            graph :func:`vehicle_allocation.travel.best_hospitals` ran on.
        pickups: pickup points (nodes validated against ``graph``).
        travels: one :class:`PickupTravel` per pickup (PRD §2.1); its
            ``None``-triple marks ``no_route`` and is excluded from the model.
        demand: demand rows (a pickup without a row = nobody to move).
        vehicles: available vehicles, each making at most one trip.
        config: see :class:`SolverConfig` (defaults solve the plain+weighted
            objective with vulnerable seats double-counted, 12 s budget).

    Returns:
        :class:`SolveResult` — assignments (sorted by ``vehicle_id``),
        unserved counts for every pickup left short (sorted by ``pickup_id``,
        no ``reason`` yet: that is Chunk 4's enum), and totals.

    Raises:
        SchemaError: duplicate ids, id-set mismatch, unknown node, or an
            input value outside the SCHEMAS.md contract.
        ValueError: invalid :class:`SolverConfig`.
        RuntimeError: CP-SAT found no solution within ``time_limit_s``.
    """
    config = config or SolverConfig()
    _check_config(config)
    pickups = list(pickups)
    travels = list(travels)
    demand = list(demand)
    vehicles = list(vehicles)

    pickup_by_id, travel_by_id, demand_by_id = _check_inputs(
        pickups, travels, demand, vehicles, graph
    )

    # Eligible = reachable hospital (Ch2's marker otherwise) AND someone to move.
    eligible: dict[str, Demand] = {
        pid: d
        for pid, d in demand_by_id.items()
        if travel_by_id[pid].hospital_id is not None and d.demand_count > 0
    }

    pairs = _build_pairs(graph, vehicles, pickup_by_id, travel_by_id, eligible)
    chosen: dict[int, int] = {}
    optimal = True

    if pairs:
        bonus_units = (
            int(round(config.vulnerable_bonus * _WEIGHT_SCALE))
            if config.use_vulnerable_bonus
            else 0
        )

        model = cp_model.CpModel()
        assign: dict[int, cp_model.IntVar] = {}
        carry: dict[int, cp_model.IntVar] = {}
        for i, pr in enumerate(pairs):
            veh = vehicles[pr.vi]
            limit = min(veh.capacity, eligible[pr.pid].demand_count)
            assign[i] = model.new_bool_var(f"assign{i}")
            carry[i] = model.new_int_var(0, limit, f"carry{i}")
            model.add(carry[i] >= assign[i])  # an assigned vehicle always carries someone
            model.add(carry[i] <= limit * assign[i])

        # One trip per vehicle (PRD §2.2).
        for vi in {pr.vi for pr in pairs}:
            model.add(
                sum(assign[i] for i, pr in enumerate(pairs) if pr.vi == vi) <= 1
            )

        served: dict[str, cp_model.IntVar] = {}
        vulnerable: dict[str, cp_model.IntVar] = {}
        for pid in sorted({pr.pid for pr in pairs}):
            d = eligible[pid]
            served[pid] = model.new_int_var(0, d.demand_count, f"served_{pid}")
            # capacity + remaining-demand caps (PRD §2.2)
            model.add(
                served[pid]
                == sum(carry[i] for i, pr in enumerate(pairs) if pr.pid == pid)
            )
            vulnerable[pid] = model.new_int_var(
                0, min(d.vulnerable_count, d.demand_count), f"vuln_{pid}"
            )
            model.add(vulnerable[pid] <= served[pid])  # counted first, capped

        # Stage 1 objective: weighted people served (value = w x passengers
        # + one extra w per vulnerable passenger), in _VALUE_SCALE units.
        terms = []
        for pid in sorted(served):
            d = eligible[pid]
            w = _weight_units(d.priority_weight) if config.use_priority_weight else _WEIGHT_SCALE
            terms.append(w * (_WEIGHT_SCALE * served[pid] + bonus_units * vulnerable[pid]))
        objective = sum(terms)
        model.maximize(objective)

        started = time.monotonic()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = config.time_limit_s
        solver.parameters.random_seed = 0
        solver.parameters.num_search_workers = 1  # reproducible across runs
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(
                f"CP-SAT found no solution within {config.time_limit_s:g}s "
                f"(status: {status}); raise SolverConfig.time_limit_s"
            )
        optimal = status == cp_model.OPTIMAL
        best_units = int(round(solver.ObjectiveValue()))
        chosen = _read_carry(solver, carry)

        # Stage 2: pin the stage-1 optimum, minimise total trip time (D2).
        if config.use_time_tiebreak:
            model.add(objective == best_units)
            model.clear_objective()
            model.minimize(
                sum(
                    int(round(pr.minutes * _TIME_SCALE)) * assign[i]
                    for i, pr in enumerate(pairs)
                )
            )
            remaining = max(0.1, config.time_limit_s - (time.monotonic() - started))
            solver2 = cp_model.CpSolver()
            solver2.parameters.max_time_in_seconds = remaining
            solver2.parameters.random_seed = 0
            solver2.parameters.num_search_workers = 1
            status2 = solver2.solve(model)
            if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                chosen = _read_carry(solver2, carry)
                optimal = optimal and status2 == cp_model.OPTIMAL
            else:
                optimal = False  # kept the phase-1 solution; time ran out

    # --- extract -----------------------------------------------------------
    served_by_pid: dict[str, int] = {pid: 0 for pid in eligible}
    assignments: list[Assignment] = []
    for i, passengers in chosen.items():
        pr = pairs[i]
        veh = vehicles[pr.vi]
        travel = travel_by_id[pr.pid]
        served_by_pid[pr.pid] += passengers
        assignments.append(
            Assignment(
                vehicle_id=veh.vehicle_id,
                pickup_id=pr.pid,
                hospital_id=travel.hospital_id,
                passengers=passengers,
                capacity=veh.capacity,
                drive_distance_m=pr.drive_m,
                hospital_distance_m=travel.distance_m,
                est_travel_min=pr.minutes,
                data_label=eligible[pr.pid].data_label,  # D4: the demand row's label
            )
        )
    assignments.sort(key=lambda a: a.vehicle_id)

    unserved = [
        UnservedCount(pid, d.demand_count - served_by_pid.get(pid, 0), d.data_label)
        for pid, d in sorted(demand_by_id.items())
        if d.demand_count - served_by_pid.get(pid, 0) > 0
    ]

    bonus_units = (
        int(round(config.vulnerable_bonus * _WEIGHT_SCALE))
        if config.use_vulnerable_bonus
        else 0
    )
    weighted = 0
    for pid, s in served_by_pid.items():
        if not s:
            continue
        d = eligible[pid]
        w = _weight_units(d.priority_weight) if config.use_priority_weight else _WEIGHT_SCALE
        weighted += w * (_WEIGHT_SCALE * s + bonus_units * min(d.vulnerable_count, s))

    return SolveResult(
        assignments=assignments,
        unserved=unserved,
        served=sum(a.passengers for a in assignments),
        total_demand=sum(d.demand_count for d in demand_by_id.values()),
        vehicles_used=len(assignments),
        weighted_value=weighted / _VALUE_SCALE,
        optimal=optimal,
    )
