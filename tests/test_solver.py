"""Chunk 3 tests: the CP-SAT allocator (PRD §2.2 + §5).

Covers the solver seam end to end on the toy (PRD §4 invariants: capacity
and remaining-demand caps, one trip per vehicle, ``no_route`` never
assigned, insufficient fleet, baseline vs flood, label propagation) and the
fine-grained semantics on synthetic fixtures where every number is exact:

* the §5 objective — ``priority_weight``, and **vulnerable seats counting
  double** (lead decision 2026-11-27: value = weight x passengers + one
  extra x weight per vulnerable passenger, counted first), with the plain
  version available via config;
* D2 — the drive from a vehicle's start to its pickup counts in the **time
  tie-break only**: it can decide between equal-value solutions but is
  never a feasibility filter *and* never the main objective term (an
  unreachable drive is a separate, physical infeasibility);
* the tie-break is strictly lexicographic: extra weighted people always
  beat any time saving.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from vehicle_allocation.loaders import (
    SchemaError,
    load_closed_roads,
    load_demand,
    load_graph,
    load_hospitals,
    load_pickups,
    load_vehicles,
)
from vehicle_allocation.models import Demand, Pickup, Vehicle
from vehicle_allocation.solver import (
    Assignment,
    SolveResult,
    SolverConfig,
    UnservedCount,
    solve_allocation,
)
from vehicle_allocation.travel import PickupTravel, best_hospitals, strip_closed_roads

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "data" / "toy"
REAL_DIR = ROOT / "data" / "musi_corridor"


# --- helpers --------------------------------------------------------------


def _bidir(*edges: tuple[str, str, float]) -> nx.DiGraph:
    """Undirected-by-convention toy graph: every road exists both ways."""
    g = nx.DiGraph()
    for u, v, length in edges:
        g.add_edge(u, v, length=float(length))
        g.add_edge(v, u, length=float(length))
    return g


def _p(pid: str, node: str, label: str = "simulated") -> Pickup:
    return Pickup(pid, node, label)


def _t(pid: str, node: str, dist: float = 500.0, label: str = "simulated") -> PickupTravel:
    """A reachable pickup travel result (PRD §2.1 shape)."""
    return PickupTravel(pid, "h1", dist, (node,), {"h1": dist}, label)


def _no_route(pid: str, label: str = "simulated") -> PickupTravel:
    """Ch2's ``None``-triple — the ``no_route`` marker (KNOWN_ISSUES #15)."""
    return PickupTravel(pid, None, None, None, {}, label)


def _d(
    pid: str,
    count: int,
    label: str = "simulated",
    vulnerable: int = 0,
    weight: float = 1.0,
) -> Demand:
    return Demand(pid, count, label, vulnerable, weight)


def _v(vid: str, node: str, cap: int, speed: float = 30.0, label: str = "manual") -> Vehicle:
    return Vehicle(vid, "van", cap, node, speed, label)


def _solve(graph, pickups, travels, demand, vehicles, **cfg) -> SolveResult:
    return solve_allocation(graph, pickups, travels, demand, vehicles, SolverConfig(**cfg))


# --- toy scenario fixtures --------------------------------------------------


@pytest.fixture(scope="module")
def toy_scenario():
    graph = load_graph(TOY / "toy_town.graphml")
    return {
        "graph": graph,
        "baseline": strip_closed_roads(graph, []),
        "flood": strip_closed_roads(graph, load_closed_roads(TOY / "closed_roads.csv", graph)),
        "pickups": load_pickups(TOY / "pickup_points.csv", graph),
        "demand": load_demand(TOY / "demand_points.csv"),
        "vehicles": load_vehicles(TOY / "vehicles.csv", graph),
        "hospitals": load_hospitals(TOY / "hospitals.csv", graph),
    }


@pytest.fixture(scope="module")
def toy_baseline(toy_scenario):
    travels = best_hospitals(
        toy_scenario["baseline"], toy_scenario["pickups"], toy_scenario["hospitals"]
    )
    return solve_allocation(
        toy_scenario["baseline"],
        toy_scenario["pickups"],
        travels,
        toy_scenario["demand"],
        toy_scenario["vehicles"],
    )


@pytest.fixture(scope="module")
def toy_flood(toy_scenario):
    travels = best_hospitals(
        toy_scenario["flood"], toy_scenario["pickups"], toy_scenario["hospitals"]
    )
    return solve_allocation(
        toy_scenario["flood"],
        toy_scenario["pickups"],
        travels,
        toy_scenario["demand"],
        toy_scenario["vehicles"],
    )


def _demand_by_id(demand):
    return {d.pickup_id: d for d in demand}


def _served_by_pickup(result: SolveResult) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in result.assignments:
        out[a.pickup_id] = out.get(a.pickup_id, 0) + a.passengers
    return out


def _unserved_by_id(result: SolveResult) -> dict[str, UnservedCount]:
    return {u.pickup_id: u for u in result.unserved}


# --- toy: PRD §4 invariants --------------------------------------------------


def test_toy_baseline_never_breaks_the_caps(toy_scenario, toy_baseline):
    vehicles = {v.vehicle_id: v for v in toy_scenario["vehicles"]}
    demand = _demand_by_id(toy_scenario["demand"])

    # one trip per vehicle (PRD §2.2)
    vehicle_ids = [a.vehicle_id for a in toy_baseline.assignments]
    assert len(vehicle_ids) == len(set(vehicle_ids))

    for a in toy_baseline.assignments:
        assert a.passengers <= vehicles[a.vehicle_id].capacity  # never over capacity
        assert a.passengers >= 1  # an assigned vehicle always carries someone
    served = _served_by_pickup(toy_baseline)
    for pid, count in served.items():
        assert count <= demand[pid].demand_count  # never over remaining demand

    # capacity edge cases (PRD §4): p1 demand == v1 capacity (40) -> exact fill;
    # p5 demand of 1 with v4 larger than demand -> carries 1, not 2.
    assert served["p1"] == 40
    assert served.get("p5", 0) <= 1
    assert toy_baseline.served <= sum(v.capacity for v in toy_scenario["vehicles"]) == 77


def test_toy_no_route_pickup_is_never_assigned(toy_baseline, toy_scenario):
    served = _served_by_pickup(toy_baseline)
    unserved = _unserved_by_id(toy_baseline)
    demand = _demand_by_id(toy_scenario["demand"])

    # p3 sits on an island with no hospital: always no_route, never assigned.
    assert "p3" not in served
    assert unserved["p3"].unserved_count == demand["p3"].demand_count == 8
    assert toy_baseline.served == 99 - sum(u.unserved_count for u in toy_baseline.unserved)


def test_toy_insufficient_fleet_leaves_leftover(toy_baseline, toy_scenario):
    # Reachable baseline demand 91 > total fleet capacity 77 (toy README):
    # the leftover must show as unserved (PRD §4 "insufficient fleet").
    assert toy_baseline.served <= 77
    assert toy_baseline.served < 91
    assert sum(u.unserved_count for u in toy_baseline.unserved) == 99 - toy_baseline.served
    assert toy_baseline.served + sum(u.unserved_count for u in toy_baseline.unserved) == 99


def test_toy_flood_differs_from_baseline(toy_scenario, toy_baseline, toy_flood):
    # The closure n2-n9 severs the spur: p2 flips to no_route in flood mode,
    # so it can never be assigned and its whole demand turns unserved.
    base_served, flood_served = _served_by_pickup(toy_baseline), _served_by_pickup(toy_flood)
    assert base_served.get("p2", 0) > 0
    assert "p2" not in flood_served
    assert _unserved_by_id(toy_flood)["p2"].unserved_count == 30
    # the island is no_route in both modes
    assert "p3" not in base_served and "p3" not in flood_served
    # only p1 (demand == v1's capacity) is pinned: p2's freed capacity
    # legitimately redistributes to p4/p5 in flood mode
    assert base_served["p1"] == flood_served["p1"] == 40
    assert toy_flood.served != toy_baseline.served


def test_toy_labels_follow_the_demand_rows(toy_scenario, toy_baseline):
    # D4 default (pending): assignments.csv carries the *demand* row's label,
    # not the vehicle's (v1 is 'manual', p1 is 'simulated').
    demand = _demand_by_id(toy_scenario["demand"])
    assert {v.data_label for v in toy_scenario["vehicles"]} >= {"manual", "simulated"}
    for a in toy_baseline.assignments:
        assert a.data_label == demand[a.pickup_id].data_label
    for u in toy_baseline.unserved:
        assert u.data_label == demand[u.pickup_id].data_label


def test_toy_deterministic_across_runs(toy_scenario):
    travels = best_hospitals(
        toy_scenario["baseline"], toy_scenario["pickups"], toy_scenario["hospitals"]
    )
    runs = [
        solve_allocation(
            toy_scenario["baseline"],
            toy_scenario["pickups"],
            travels,
            toy_scenario["demand"],
            toy_scenario["vehicles"],
        )
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


# --- synthetic: exclusion rules ---------------------------------------------


def test_vehicle_outside_the_pickups_component_is_never_assigned():
    # D2's "not in feasibility" read: a vehicle that physically cannot reach
    # a pickup (real graph = 38 components, KNOWN_ISSUES #11) is a forbidden
    # pair — distinct from "no time budget".
    g = _bidir(("pa", "ha", 500.0))  # pickup/hospital component
    g.add_edge("vlonely", "vlonely", length=10.0)  # isolated component
    result = _solve(
        g,
        [_p("A", "pa")],
        [_t("A", "pa")],
        [_d("A", 5)],
        [_v("v1", "vlonely", 5)],
    )
    assert result.assignments == []
    assert result.vehicles_used == 0
    assert _unserved_by_id(result)["A"].unserved_count == 5


def test_zero_and_missing_demand_rows_attract_no_vehicle():
    # demand 0 and "no demand row at all" both mean nobody to move (SCHEMAS:
    # a pickup need not appear in demand_points.csv).
    g = _bidir(("start", "pa", 100.0), ("start", "pb", 100.0), ("start", "pc", 100.0))
    result = _solve(
        g,
        [_p("A", "pa"), _p("B", "pb"), _p("C", "pc")],
        [_t("A", "pa"), _t("B", "pb"), _t("C", "pc")],
        [_d("A", 0), _d("C", 3)],  # B has no demand row
        [_v("v1", "start", 40)],
    )
    assert [a.pickup_id for a in result.assignments] == ["C"]
    assert result.assignments[0].passengers == 3
    # C is fully served, A/B have nothing to serve -> no unserved rows at all
    assert result.unserved == []
    assert result.total_demand == 3
    assert result.served == 3


def test_no_route_pickup_is_excluded_and_counted_unserved():
    g = _bidir(("start", "pa", 100.0), ("start", "pb", 100.0))
    result = _solve(
        g,
        [_p("A", "pa"), _p("B", "pb")],
        [_t("A", "pa"), _no_route("B")],  # Ch2 None-triple
        [_d("A", 5), _d("B", 7)],
        [_v("v1", "start", 40)],
    )
    assert [a.pickup_id for a in result.assignments] == ["A"]
    unserved = _unserved_by_id(result)
    assert unserved["B"].unserved_count == 7
    assert "A" not in unserved  # fully served -> no unserved row
    assert result.total_demand == 12


def test_no_vehicles_means_everything_unserved():
    g = _bidir(("pa", "ha", 500.0))
    result = _solve(g, [_p("A", "pa")], [_t("A", "pa")], [_d("A", 5)], [])
    assert result.assignments == []
    assert result.served == 0
    assert result.vehicles_used == 0
    assert _unserved_by_id(result)["A"].unserved_count == 5


def test_empty_scenario_returns_an_empty_result():
    result = _solve(nx.DiGraph(), [], [], [], [])
    assert result == SolveResult(
        assignments=[], unserved=[], served=0, total_demand=0,
        vehicles_used=0, weighted_value=0.0, optimal=True,
    )


# --- synthetic: capacity / remaining-demand caps ------------------------------


def test_caps_hold_when_demand_exceeds_the_fleet():
    g = _bidir(("s1", "pa", 100.0), ("s1", "pb", 100.0), ("s2", "pa", 100.0), ("s2", "pb", 100.0))
    result = _solve(
        g,
        [_p("A", "pa"), _p("B", "pb")],
        [_t("A", "pa"), _t("B", "pb")],
        [_d("A", 10), _d("B", 10)],
        [_v("v1", "s1", 8), _v("v2", "s2", 6)],
    )
    assert result.served == 14  # 8 + 6 capacity is all there is
    assert len(result.assignments) == 2  # one trip per vehicle
    for a in result.assignments:
        assert a.passengers <= {"v1": 8, "v2": 6}[a.vehicle_id]
    assert sum(u.unserved_count for u in result.unserved) == 20 - 14


# --- synthetic: the §5 objective ------------------------------------------------


def test_priority_weight_trades_people_for_urgency():
    # v1 (cap 4) is far from A, near B; v2 (cap 3) the other way round.
    # A: demand 5, weight 5.0 -> B: demand 5, weight 1.0.
    g = _bidir(
        ("s1", "pb", 100.0), ("s2", "pa", 100.0),
        ("s1", "pa", 1000.0), ("s2", "pb", 1000.0),
    )
    pickups = [_p("A", "pa"), _p("B", "pb")]
    travels = [_t("A", "pa"), _t("B", "pb")]
    demand = [_d("A", 5, weight=5.0), _d("B", 5)]
    vehicles = [_v("v1", "s1", 4), _v("v2", "s2", 3)]

    weighted = _solve(g, pickups, travels, demand, vehicles)
    # 5 urgent people (25) beat 7 plain people: A full, B left behind — the
    # tradeoff PRD §5 says the results must show.
    assert _served_by_pickup(weighted) == {"A": 5}
    assert weighted.weighted_value == pytest.approx(25.0)
    assert _unserved_by_id(weighted)["B"].unserved_count == 5

    plain = _solve(g, pickups, travels, demand, vehicles, use_priority_weight=False)
    # Plain version (weight 1): maximise raw people -> 7 served, and the
    # time tie-break picks the composition with the short drives (v2->A,
    # v1->B) rather than the long ones.
    assert plain.served == 7
    assert _served_by_pickup(plain) == {"A": 3, "B": 4}
    assert plain.weighted_value == pytest.approx(7.0)


def test_vulnerable_seats_count_double():
    # Lead decision 2026-11-27: value = weight x passengers + one extra x
    # weight per *vulnerable* passenger (counted first). A=5/5 vulnerable is
    # worth 10 to the solver; B (6 plain, closer to the vehicle) is worth 5.
    g = _bidir(("v", "pb", 100.0), ("pb", "pa", 1000.0))
    pickups = [_p("A", "pa"), _p("B", "pb")]
    travels = [_t("A", "pa"), _t("B", "pb")]
    demand = [_d("A", 5, vulnerable=5), _d("B", 6)]
    vehicles = [_v("v1", "v", 5)]

    with_bonus = _solve(g, pickups, travels, demand, vehicles)
    assert _served_by_pickup(with_bonus) == {"A": 5}
    assert with_bonus.weighted_value == pytest.approx(10.0)

    # Bonus off: both are worth 5 people -> the drive-time tie-break sends
    # the (single, faster to B) vehicle to B instead.
    without = _solve(g, pickups, travels, demand, vehicles, use_vulnerable_bonus=False)
    assert _served_by_pickup(without) == {"B": 5}
    assert without.weighted_value == pytest.approx(5.0)


def test_tiny_priority_weight_is_not_starved():
    # priority_weight 0.0001 rounds to 0 at integer scale; the solver must
    # still serve it (a >0 weight can never be worth 0 — KNOWN_ISSUES log).
    g = _bidir(("start", "pa", 100.0))
    result = _solve(g, [_p("A", "pa")], [_t("A", "pa")], [_d("A", 5, weight=0.0001)],
                    [_v("v1", "start", 5)])
    assert result.served == 5


# --- synthetic: the time tie-break (D2) -----------------------------------------


def test_drive_to_pickup_counts_in_the_tie_break():
    # Equal value (5 people either way), equal hospital legs: only the
    # drive differs -> the nearer pickup wins. This is D2: the drive is
    # never in the objective's main term, only in the tie-break.
    g = _bidir(("v", "pa", 1000.0), ("v", "pb", 100.0))
    result = _solve(
        g,
        [_p("A", "pa"), _p("B", "pb")],
        [_t("A", "pa"), _t("B", "pb")],
        [_d("A", 5), _d("B", 5)],
        [_v("v1", "v", 5)],
    )
    assert [a.pickup_id for a in result.assignments] == ["B"]


def test_hospital_leg_counts_in_the_tie_break():
    # Mirror image: drives equal, pickup->hospital leg differs (B's hospital
    # is far) -> A wins. Both legs of the trip make up the tie-break time.
    g = _bidir(("v", "pa", 100.0), ("v", "pb", 100.0))
    result = _solve(
        g,
        [_p("A", "pa"), _p("B", "pb")],
        [_t("A", "pa", dist=500.0), _t("B", "pb", dist=1500.0)],
        [_d("A", 5), _d("B", 5)],
        [_v("v1", "v", 5)],
    )
    assert [a.pickup_id for a in result.assignments] == ["A"]


def test_est_travel_min_covers_drive_plus_hospital_leg():
    # 1000 m drive + 500 m to hospital at 30 km/h = 1.5 km / 30 km/h = 3 min.
    # (Ch4 writes this to assignments.csv; which legs count there is D-open.)
    g = _bidir(("v", "pa", 1000.0))
    result = _solve(g, [_p("A", "pa")], [_t("A", "pa", dist=500.0)], [_d("A", 5)],
                    [_v("v1", "v", 40)])
    [a] = result.assignments
    assert a.drive_distance_m == pytest.approx(1000.0)
    assert a.hospital_distance_m == pytest.approx(500.0)
    assert a.est_travel_min == pytest.approx(3.0)
    assert a.capacity == 40 and a.hospital_id == "h1"


# --- synthetic: contract guards ----------------------------------------------------


def test_rejects_mismatched_pickup_ids():
    g = _bidir(("v", "pa", 100.0))
    with pytest.raises(SchemaError, match="travels"):
        _solve(g, [_p("A", "pa")], [_t("B", "pa")], [_d("A", 1)], [_v("v1", "v", 1)])


def test_rejects_duplicate_ids():
    g = _bidir(("v", "pa", 100.0))
    with pytest.raises(SchemaError, match="vehicle_id"):
        _solve(g, [_p("A", "pa")], [_t("A", "pa")], [_d("A", 1)],
               [_v("v1", "v", 1), _v("v1", "v", 1)])
    with pytest.raises(SchemaError, match="pickup_id"):
        _solve(g, [_p("A", "pa"), _p("A", "pa")], [_t("A", "pa")], [_d("A", 1)],
               [_v("v1", "v", 1)])


def test_rejects_nodes_missing_from_the_graph():
    g = _bidir(("v", "pa", 100.0))
    with pytest.raises(SchemaError, match="v1"):
        _solve(g, [_p("A", "pa")], [_t("A", "pa")], [_d("A", 1)], [_v("v1", "ghost", 1)])
    with pytest.raises(SchemaError, match="A"):
        _solve(g, [_p("A", "ghost")], [_t("A", "ghost")], [_d("A", 1)], [_v("v1", "v", 1)])


def test_rejects_bad_config_values():
    g = _bidir(("v", "pa", 100.0))
    args = (g, [_p("A", "pa")], [_t("A", "pa")], [_d("A", 1)], [_v("v1", "v", 1)])
    with pytest.raises(ValueError, match="time_limit_s"):
        solve_allocation(*args, SolverConfig(time_limit_s=0))
    with pytest.raises(ValueError, match="vulnerable_bonus"):
        solve_allocation(*args, SolverConfig(vulnerable_bonus=-0.5))
    assert SolverConfig().time_limit_s == 12.0  # lead 2026-11-27: middle of 10-15 s


def test_result_types_and_ordering():
    g = _bidir(("s1", "pa", 100.0), ("s2", "pb", 100.0))
    result = _solve(
        g,
        [_p("B", "pb"), _p("A", "pa")],  # deliberately shuffled input
        [_t("B", "pb"), _t("A", "pa")],
        [_d("B", 4), _d("A", 4)],
        [_v("v2", "s2", 4), _v("v1", "s1", 4)],
    )
    assert isinstance(result, SolveResult)
    assert all(isinstance(a, Assignment) for a in result.assignments)
    assert all(isinstance(u, UnservedCount) for u in result.unserved)
    assert [a.vehicle_id for a in result.assignments] == sorted(
        a.vehicle_id for a in result.assignments
    )
    assert isinstance(result.optimal, bool) and result.optimal


# --- package exports --------------------------------------------------------------


def test_package_root_exports_solver_api():
    import vehicle_allocation as va

    for name in ("Assignment", "SolveResult", "SolverConfig", "UnservedCount", "solve_allocation"):
        assert hasattr(va, name), f"vehicle_allocation.{name} not exported"


# --- real Musi-corridor graph (KNOWN_ISSUES #10/#11) -------------------------------


@pytest.mark.skipif(
    not (REAL_DIR / "musi_corridor.graphml").exists(),
    reason="Musi-corridor graph is gitignored; pull with scripts/pull_osm.py (KNOWN_ISSUES #10)",
)
def test_real_musi_corridor_solves_end_to_end():
    # Scale + disconnected components: 38 weakly-connected components mean
    # real forbidden pairs (vehicle can't reach the pickup), and OSM string
    # attributes must survive the whole drive-time computation.
    graph = load_graph(REAL_DIR / "musi_corridor.graphml")
    stripped = strip_closed_roads(graph, [])
    hospitals = load_hospitals(REAL_DIR / "hospitals.csv", graph)

    step = max(1, len(stripped) // 5)
    sample = sorted(stripped.nodes)[::step][:5]
    pickups = [_p(f"rp{i}", node) for i, node in enumerate(sample)]
    travels = best_hospitals(stripped, pickups, hospitals)
    demand = [_d(f"rp{i}", 10, weight=1.5) for i in range(len(sample))]
    vehicles = [_v(f"rv{i}", sample[(i + 1) % len(sample)], 20, speed=40.0)
                for i in range(3)]

    result = solve_allocation(stripped, pickups, travels, demand, vehicles)

    assert result.optimal
    assert result.served <= 60  # 3 x capacity 20 is all the fleet there is
    assert len(result.assignments) <= 3  # one trip per vehicle
    assert result.total_demand == 50
    assert result.served + sum(u.unserved_count for u in result.unserved) == 50
    demand_by = {d.pickup_id: d.demand_count for d in demand}
    for a in result.assignments:
        assert a.passengers <= 20
        assert a.drive_distance_m >= 0 and a.est_travel_min > 0
    served = _served_by_pickup(result)
    for pid, count in served.items():
        assert count <= demand_by[pid]
        assert not any(t.pickup_id == pid and t.hospital_id is None for t in travels)
