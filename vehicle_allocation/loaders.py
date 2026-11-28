"""Validating loaders: CSV/GraphML -> typed records (Chunk 1).

Each loader reads one file, validates it against ``SCHEMAS.md``, and returns
dataclasses with ``data_label`` preserved. Anything that violates the contract
raises :class:`SchemaError`:

* missing or unknown columns,
* values outside the allowed ``data_label`` enum,
* wrong types or out-of-range numbers (``capacity >= 1``, ``speed_kmh > 0``,
  ``demand_count >= 0``, ``priority_weight > 0``, ...),
* duplicate IDs,
* node references that are not in the scenario graph,
* broken foreign keys (``demand_points.pickup_id``, ``reachability_*``).

Per SCHEMAS.md, an empty cell means *missing*: for **required** fields that is
an error, for **optional** fields (``vulnerable_count``, ``priority_weight``)
the documented default is applied instead.

Lat/lon cells are resolved to the nearest graph node (SCHEMAS.md "Nodes"
convention); the resolved node and the original coordinates are both kept.

Closed roads are treated as **unordered**: a ``(u, v)`` row blocks that road in
both directions. ``load_closed_roads`` validates the endpoints against the graph
but keeps them verbatim — the caller (Chunk 2+) must remove *both* arcs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import networkx as nx
import pandas as pd

from vehicle_allocation.models import (
    DATA_LABELS,
    ClosedRoad,
    Demand,
    Hospital,
    Pickup,
    Reachability,
    Scenario,
    Vehicle,
)

__all__ = [
    "SchemaError",
    "load_closed_roads",
    "load_demand",
    "load_graph",
    "load_hospitals",
    "load_pickups",
    "load_reachability",
    "load_scenario",
    "load_vehicles",
]


class SchemaError(ValueError):
    """A file does not match the SCHEMAS.md contract."""


# Sentinel: "no default given" (0 / 1.0 are valid defaults, so None won't do).
_MISSING = object()


# --- low-level helpers ----------------------------------------------------


def _check_columns(
    df: pd.DataFrame,
    *,
    required: Iterable[str],
    optional: Iterable[str],
    source: str,
) -> None:
    required, optional = set(required), set(optional)
    cols = set(df.columns)
    missing = required - cols
    if missing:
        raise SchemaError(f"{source}: missing required column(s): {sorted(missing)}")
    unknown = cols - required - optional
    if unknown:
        raise SchemaError(f"{source}: unknown column(s): {sorted(unknown)}")


def _check_enum(series: pd.Series, *, allowed: frozenset[str], col: str, source: str) -> None:
    bad = sorted({str(v) for v in series if str(v) not in allowed})
    if bad:
        raise SchemaError(f"{source}: invalid '{col}' value(s): {bad}; allowed: {sorted(allowed)}")


def _read_labels(df: pd.DataFrame, source: str) -> list[str]:
    """Validate the ``data_label`` column and return it as a list of strings."""
    _check_enum(df["data_label"], allowed=DATA_LABELS, col="data_label", source=source)
    return [str(v) for v in df["data_label"]]


def _str_col(df: pd.DataFrame, col: str, source: str, *, no_whitespace: bool = False) -> list[str]:
    out: list[str] = []
    for idx, val in df[col].items():
        if pd.isna(val):
            raise SchemaError(f"{source}: row {idx}: '{col}' is missing (empty cell)")
        s = str(val)
        if not s.strip():
            raise SchemaError(f"{source}: row {idx}: '{col}' must be a non-empty string")
        if no_whitespace and any(ch.isspace() for ch in s):
            raise SchemaError(f"{source}: row {idx}: '{col}' must not contain whitespace (got {s!r})")
        out.append(s)
    return out


def _int_col(
    df: pd.DataFrame,
    col: str,
    source: str,
    *,
    min_value: int | None = None,
    default: int | object = _MISSING,
) -> list[int]:
    out: list[int] = []
    for idx, val in df[col].items():
        if pd.isna(val):
            if default is not _MISSING:
                out.append(default)  # type: ignore[arg-type]
                continue
            raise SchemaError(f"{source}: row {idx}: '{col}' is missing (empty cell)")
        try:
            ival = int(val)
            if float(val) != ival:
                raise ValueError
        except (TypeError, ValueError):
            raise SchemaError(f"{source}: row {idx}: '{col}' must be an integer (got {val!r})") from None
        if min_value is not None and ival < min_value:
            raise SchemaError(f"{source}: row {idx}: '{col}' must be >= {min_value} (got {ival})")
        out.append(ival)
    return out


def _float_col(
    df: pd.DataFrame,
    col: str,
    source: str,
    *,
    exclusive_min: float | None = None,
    default: float | object = _MISSING,
) -> list[float]:
    out: list[float] = []
    for idx, val in df[col].items():
        if pd.isna(val):
            if default is not _MISSING:
                out.append(default)  # type: ignore[arg-type]
                continue
            raise SchemaError(f"{source}: row {idx}: '{col}' is missing (empty cell)")
        try:
            fval = float(val)
        except (TypeError, ValueError):
            raise SchemaError(f"{source}: row {idx}: '{col}' must be a number (got {val!r})") from None
        if exclusive_min is not None and not fval > exclusive_min:
            raise SchemaError(f"{source}: row {idx}: '{col}' must be > {exclusive_min} (got {fval})")
        out.append(fval)
    return out


def _check_unique(values: list[str], *, col: str, source: str) -> None:
    seen: set[str] = set()
    dupes: set[str] = set()
    for v in values:
        if v in seen:
            dupes.add(v)
        seen.add(v)
    if dupes:
        raise SchemaError(f"{source}: duplicate '{col}' value(s): {sorted(dupes)}")


# --- node / lat-lon resolution --------------------------------------------


def _nearest_node(graph: nx.Graph, lat: float, lon: float, *, source: str) -> str:
    """Nearest graph node by Euclidean distance on (x=lon, y=lat).

    Good enough for nearest-node lookup on a dense road network; not a
    geodesic. Revisit if the real graph's coordinate space needs it (Ch6).
    """
    best: str | None = None
    best_d = float("inf")
    for node, attrs in graph.nodes(data=True):
        x, y = attrs.get("x"), attrs.get("y")
        if x is None or y is None:
            continue
        d = (float(x) - lon) ** 2 + (float(y) - lat) ** 2
        if d < best_d:
            best, best_d = str(node), d
    if best is None:
        raise SchemaError(f"{source}: graph has no nodes with x/y coordinates; cannot resolve lat/lon")
    return best


def _resolve_node_cell(value: str, graph: nx.Graph, *, source: str, where: str) -> tuple[str, float | None, float | None]:
    """Resolve a 'graph node id OR lat,lon' cell -> (node, lat, lon).

    Tolerates surrounding whitespace and spaces around the comma so a
    human-written ``"17.301, 78.419"`` still resolves (``float`` strips it).
    """
    value = value.strip()
    if value in graph.nodes:
        return value, None, None
    parts = value.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except ValueError:
            raise SchemaError(
                f"{source}: {where}: start_node_or_lat_lon is neither a graph node "
                f"nor 'lat,lon' (got {value!r})"
            ) from None
        return _nearest_node(graph, lat, lon, source=source), lat, lon
    raise SchemaError(f"{source}: {where}: unknown node {value!r} (not in graph)")


def _validate_node(node: str, graph: nx.Graph, *, source: str, where: str) -> None:
    if node not in graph.nodes:
        raise SchemaError(f"{source}: {where}: unknown node {node!r} (not in graph)")


# --- loaders ----------------------------------------------------------------


# --- graph attribute normalization ----------------------------------------
# The toy graphml (written by networkx) carries native float/int attributes;
# OSM graphml (written by osmnx) round-trips *every* attribute as a string.
# Left alone, edge `length` would be a str on real data and Ch2's travel-time
# arithmetic would fail there while passing on the toy — the exact toy->real
# trap. Coerce the attributes we compute on so every graph source yields
# identical runtime types. Unparseable values are left untouched (never crash
# the load) so Ch2 can surface them against the right row.

def _coerce_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _coerce_int(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else v
    except (TypeError, ValueError):
        return v


def _coerce_bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return v


_FLOAT_NODE_ATTRS = ("x", "y")
_INT_NODE_ATTRS = ("street_count",)
_FLOAT_EDGE_ATTRS = ("length",)
_BOOL_EDGE_ATTRS = ("oneway", "reversed")


def _normalize_graph(graph: nx.Graph) -> nx.Graph:
    """Coerce known numeric/bool graph attributes to native types, in place."""
    for _, attrs in graph.nodes(data=True):
        for key in _FLOAT_NODE_ATTRS:
            if key in attrs:
                attrs[key] = _coerce_float(attrs[key])
        for key in _INT_NODE_ATTRS:
            if key in attrs:
                attrs[key] = _coerce_int(attrs[key])

    if graph.is_multigraph():
        edge_data = (d for _, _, _, d in graph.edges(keys=True, data=True))
    else:
        edge_data = (d for _, _, d in graph.edges(data=True))
    for attrs in edge_data:
        for key in _FLOAT_EDGE_ATTRS:
            if key in attrs:
                attrs[key] = _coerce_float(attrs[key])
        for key in _BOOL_EDGE_ATTRS:
            if key in attrs:
                attrs[key] = _coerce_bool(attrs[key])
    return graph


def load_graph(path: str | Path) -> nx.Graph:
    """Read the scenario road graph (GraphML), normalizing attribute types."""
    return _normalize_graph(nx.read_graphml(path))


def _load_node_records(path: str | Path, graph: nx.Graph, *, id_field: str, cls):
    """Load pickup_points.csv / hospitals.csv (node OR lat/lon form)."""
    df = pd.read_csv(path)
    source = Path(path).name
    has_node = "node" in df.columns
    has_lat, has_lon = "lat" in df.columns, "lon" in df.columns

    if has_node and (has_lat or has_lon):
        raise SchemaError(f"{source}: provide either 'node' or 'lat'/'lon', not both")
    if not has_node and not (has_lat and has_lon):
        raise SchemaError(f"{source}: must provide either 'node' or both 'lat' and 'lon'")

    node_cols = ["node"] if has_node else ["lat", "lon"]
    _check_columns(df, required=[id_field, *node_cols, "data_label"], optional=[], source=source)
    labels = _read_labels(df, source)

    ids = _str_col(df, id_field, source, no_whitespace=True)
    _check_unique(ids, col=id_field, source=source)

    if has_node:
        nodes = _str_col(df, "node", source, no_whitespace=True)
        for i, n in enumerate(nodes):
            _validate_node(n, graph, source=source, where=f"row {i}: {id_field}={ids[i]}")
        lats: list[float | None] = [None] * len(nodes)
        lons: list[float | None] = [None] * len(nodes)
    else:
        raw_lats = _float_col(df, "lat", source)
        raw_lons = _float_col(df, "lon", source)
        lats, lons = list(raw_lats), list(raw_lons)
        nodes = [
            _nearest_node(graph, la, lo, source=source) for la, lo in zip(raw_lats, raw_lons)
        ]

    return [
        cls(**{id_field: i, "node": n, "data_label": l, "lat": la, "lon": lo})
        for i, n, l, la, lo in zip(ids, nodes, labels, lats, lons)
    ]


def load_pickups(path: str | Path, graph: nx.Graph) -> list[Pickup]:
    return _load_node_records(path, graph, id_field="pickup_id", cls=Pickup)


def load_hospitals(path: str | Path, graph: nx.Graph) -> list[Hospital]:
    return _load_node_records(path, graph, id_field="hospital_id", cls=Hospital)


def load_demand(path: str | Path) -> list[Demand]:
    df = pd.read_csv(path)
    source = Path(path).name
    _check_columns(
        df,
        required=["pickup_id", "demand_count", "data_label"],
        optional=["vulnerable_count", "priority_weight"],
        source=source,
    )
    labels = _read_labels(df, source)

    ids = _str_col(df, "pickup_id", source, no_whitespace=True)
    _check_unique(ids, col="pickup_id", source=source)
    demand = _int_col(df, "demand_count", source, min_value=0)

    # Optional columns: absent -> default; empty cell -> default (SCHEMAS:
    # "empty cell = missing").
    if "vulnerable_count" in df.columns:
        vuln = _int_col(df, "vulnerable_count", source, min_value=0, default=0)
    else:
        vuln = [0] * len(df)
    if "priority_weight" in df.columns:
        pw = _float_col(df, "priority_weight", source, exclusive_min=0, default=1.0)
    else:
        pw = [1.0] * len(df)

    return [
        Demand(
            pickup_id=i,
            demand_count=d,
            data_label=l,
            vulnerable_count=v,
            priority_weight=p,
        )
        for i, d, l, v, p in zip(ids, demand, labels, vuln, pw)
    ]


def load_vehicles(path: str | Path, graph: nx.Graph) -> list[Vehicle]:
    df = pd.read_csv(path)
    source = Path(path).name
    _check_columns(
        df,
        required=[
            "vehicle_id",
            "type",
            "capacity",
            "start_node_or_lat_lon",
            "speed_kmh",
            "data_label",
        ],
        optional=[],
        source=source,
    )
    labels = _read_labels(df, source)

    ids = _str_col(df, "vehicle_id", source, no_whitespace=True)
    _check_unique(ids, col="vehicle_id", source=source)
    vehicle_types = _str_col(df, "type", source)  # free text, internal spaces allowed
    capacities = _int_col(df, "capacity", source, min_value=1)
    speeds = _float_col(df, "speed_kmh", source, exclusive_min=0)
    # Dual-purpose column (node id OR 'lat,lon'): allow spaces, resolve below.
    start_cells = _str_col(df, "start_node_or_lat_lon", source)

    out: list[Vehicle] = []
    for i, cell in enumerate(start_cells):
        node, lat, lon = _resolve_node_cell(
            cell, graph, source=source, where=f"row {i}: vehicle_id={ids[i]}"
        )
        out.append(
            Vehicle(
                vehicle_id=ids[i],
                type=vehicle_types[i],
                capacity=capacities[i],
                start_node=node,
                speed_kmh=speeds[i],
                data_label=labels[i],
                start_lat=lat,
                start_lon=lon,
            )
        )
    return out


def load_closed_roads(path: str | Path, graph: nx.Graph) -> list[ClosedRoad]:
    """Load closed_roads.csv, validating both endpoints against ``graph``.

    Rows are **unordered** — ``(u, v)`` blocks the road in both directions.
    Endpoints are kept verbatim; removing *both* arcs is the caller's job.
    """
    df = pd.read_csv(path)
    source = Path(path).name
    _check_columns(df, required=["u", "v", "data_label"], optional=["reason"], source=source)
    labels = _read_labels(df, source)

    us = _str_col(df, "u", source, no_whitespace=True)
    vs = _str_col(df, "v", source, no_whitespace=True)
    if "reason" in df.columns:
        reasons = [None if pd.isna(r) else str(r) for r in df["reason"]]
    else:
        reasons = [None] * len(df)

    for i, (u, v) in enumerate(zip(us, vs)):
        _validate_node(u, graph, source=source, where=f"row {i}")
        _validate_node(v, graph, source=source, where=f"row {i}")
        # Unordered: accept the road if it exists in either direction.
        if not graph.has_edge(u, v) and not graph.has_edge(v, u):
            raise SchemaError(f"{source}: row {i}: no edge between {u!r} and {v!r} in graph")

    return [
        ClosedRoad(u=u, v=v, data_label=l, reason=r)
        for u, v, l, r in zip(us, vs, labels, reasons)
    ]


def load_reachability(path: str | Path) -> list[Reachability]:
    """Load reachability_results.csv (Muhammad's file — format unconfirmed).

    Working assumption (KNOWN_ISSUES #3): one row per (pickup, hospital) pair.
    Revisit in Ch6 when the real file arrives; the allocator's own NetworkX
    check is authoritative either way.
    """
    df = pd.read_csv(path)
    source = Path(path).name
    _check_columns(
        df, required=["pickup_id", "hospital_id", "data_label"], optional=[], source=source
    )
    labels = _read_labels(df, source)

    pickups = _str_col(df, "pickup_id", source, no_whitespace=True)
    hospitals = _str_col(df, "hospital_id", source, no_whitespace=True)
    return [
        Reachability(pickup_id=p, hospital_id=h, data_label=l)
        for p, h, l in zip(pickups, hospitals, labels)
    ]


def load_scenario(data_dir: str | Path) -> Scenario:
    """Load every file in a scenario directory into one :class:`Scenario`.

    The graph is read first (node-bearing loaders need it), then the CSVs, then
    cross-file foreign keys are checked.
    """
    data_dir = Path(data_dir)
    graph_paths = sorted(data_dir.glob("*.graphml"))
    if not graph_paths:
        raise SchemaError(f"{data_dir}: no .graphml file found")

    graph = load_graph(graph_paths[0])
    pickups = load_pickups(data_dir / "pickup_points.csv", graph)
    demand = load_demand(data_dir / "demand_points.csv")
    vehicles = load_vehicles(data_dir / "vehicles.csv", graph)
    hospitals = load_hospitals(data_dir / "hospitals.csv", graph)
    closed_roads = load_closed_roads(data_dir / "closed_roads.csv", graph)
    reachability = load_reachability(data_dir / "reachability_results.csv")

    pickup_ids = {p.pickup_id for p in pickups}
    hospital_ids = {h.hospital_id for h in hospitals}
    for d in demand:
        if d.pickup_id not in pickup_ids:
            raise SchemaError(f"demand_points.csv: unknown pickup_id {d.pickup_id!r}")
    for r in reachability:
        if r.pickup_id not in pickup_ids:
            raise SchemaError(f"reachability_results.csv: unknown pickup_id {r.pickup_id!r}")
        if r.hospital_id not in hospital_ids:
            raise SchemaError(f"reachability_results.csv: unknown hospital_id {r.hospital_id!r}")

    return Scenario(
        graph=graph,
        pickups=pickups,
        demand=demand,
        vehicles=vehicles,
        hospitals=hospitals,
        closed_roads=closed_roads,
        reachability=reachability,
    )
