"""Typed records produced by the validating loaders (Chunk 1).

DataFrame in -> these dataclasses out. ``data_label`` is carried on every
record so input provenance survives through to the outputs (PRD §4).

Node references (``start_node``, ``node``, ``u``/``v``) are always resolved
plain string ids from the scenario GraphML. Where a file supplied ``lat``/``lon``
instead, the original coordinates are kept alongside the resolved node so the
resolution can be audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

import networkx as nx

DataLabel = Literal["source_derived", "manual", "simulated"]

#: Allowed ``data_label`` values (SCHEMAS.md conventions) — derived from
#: ``DataLabel`` so the two can never drift apart.
DATA_LABELS: frozenset[str] = frozenset(get_args(DataLabel))


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    type: str  # free text, reporting only (SCHEMAS.md)
    capacity: int
    start_node: str  # resolved from start_node_or_lat_lon
    speed_kmh: float
    data_label: DataLabel
    start_lat: float | None = None  # set only if resolved from lat/lon
    start_lon: float | None = None


@dataclass(frozen=True)
class Pickup:
    pickup_id: str
    node: str
    data_label: DataLabel
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class Demand:
    pickup_id: str
    demand_count: int
    data_label: DataLabel
    vulnerable_count: int = 0  # PRD §5, optional (default 0)
    priority_weight: float = 1.0  # PRD §5, optional (default 1.0)


@dataclass(frozen=True)
class Hospital:
    hospital_id: str
    node: str
    data_label: DataLabel
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class ClosedRoad:
    u: str
    v: str
    data_label: DataLabel
    reason: str | None = None  # kept if present, ignored by the allocator


@dataclass(frozen=True)
class Reachability:
    """One (pickup, hospital) row — Muhammad's file, format unconfirmed."""

    pickup_id: str
    hospital_id: str
    data_label: DataLabel


@dataclass
class Scenario:
    """Everything ``load_scenario`` reads for one flood scenario."""

    graph: nx.Graph
    pickups: list[Pickup]
    demand: list[Demand]
    vehicles: list[Vehicle]
    hospitals: list[Hospital]
    closed_roads: list[ClosedRoad]
    reachability: list[Reachability]
