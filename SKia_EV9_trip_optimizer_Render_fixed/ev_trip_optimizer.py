"""
SKia EV9 Long Range AWD long-trip routing and charging optimizer.

Dependencies: Python 3.10+ standard library only.
Live providers:
  - GraphHopper Routing API (requires GRAPHHOPPER_API_KEY)
  - Open Charge Map API (requires OPENCHARGEMAP_API_KEY)

Important: battery capacity is provisional. The default SOC-dependent DC curve is
calibrated to the supplied vehicle reference: 96.0 kWh usable, up to 235 kW peak,
10-80% in approximately 24 minutes. Actual charge power depends on battery temperature, preconditioning,
charger sharing, vehicle software, and state of health.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import asin, cos, radians, sin, sqrt
from typing import Any, Iterable, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import os
import time


# ---------------------------- Normalized schemas ----------------------------

@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    elevation_m: float | None = None


@dataclass(frozen=True)
class RouteSegment:
    start_km: float
    end_km: float
    distance_km: float
    ascent_m: float = 0.0
    descent_m: float = 0.0
    duration_s: float | None = None


@dataclass(frozen=True)
class RouteResponse:
    provider: str
    origin: GeoPoint
    destination: GeoPoint
    distance_km: float
    duration_s: float | None
    geometry: tuple[GeoPoint, ...]
    segments: tuple[RouteSegment, ...]
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Charger:
    id: str
    name: str
    lat: float
    lon: float
    along_route_km: float
    max_power_kw: float
    connector_types: tuple[str, ...] = ()
    operational: bool | None = None
    provider: str = "unknown"
    access_notes: str | None = None


@dataclass(frozen=True)
class ChargerSearchResponse:
    provider: str
    chargers: tuple[Charger, ...]
    queried_route_distance_km: float
    raw_count: int = 0


class RoutingProvider(Protocol):
    def route(self, origin: GeoPoint, destination: GeoPoint) -> RouteResponse: ...


class ChargerProvider(Protocol):
    def chargers_along_route(
        self, route: RouteResponse, corridor_km: float = 15.0, max_results: int = 100
    ) -> ChargerSearchResponse: ...


@dataclass(frozen=True)
class VehicleModel:
    usable_capacity_kwh: float = 96.0  # user-specified approximate usable capacity
    dc_peak_kw: float = 235.0
    ac_max_kw: float = 11.0
    reserve_soc_pct: float = 10.0
    # SOC bands are [start, end, power]. Approximate curve calibrated to ~24 min from 10-80%.
    dc_curve_kw: tuple[tuple[float, float, float], ...] = (
        (0.0, 10.0, 180.0),
        (10.0, 40.0, 235.0),
        (40.0, 60.0, 180.0),
        (60.0, 70.0, 140.0),
        (70.0, 80.0, 95.0),
        (80.0, 90.0, 55.0),
        (90.0, 100.0, 30.0),
    )


@dataclass(frozen=True)
class DrivingProfile:
    base_consumption_kwh_per_100km: float = 18.0
    average_speed_kmh: float = 100.0
    vehicle_mass_kg: float = 2600.0
    drivetrain_efficiency_uphill: float = 0.90
    regen_efficiency_downhill: float = 0.65
    regen_power_limit_kw: float = 70.0
    # Coarse temperature adjustment around 20 C; replace with measured vehicle data.
    temperature_c: float = 20.0
    cold_penalty_per_c_below_20: float = 0.006
    # Optional load factor: e.g. 1.05 for a loaded family trip.
    load_factor: float = 1.0


@dataclass(frozen=True)
class ChargingStop:
    charger: Charger
    arrival_soc_pct: float
    departure_soc_pct: float
    charge_minutes: float


@dataclass(frozen=True)
class OptimizationResult:
    total_minutes: float
    driving_minutes: float
    charging_minutes: float
    distance_km: float
    arrival_soc_pct: float
    stops: tuple[ChargingStop, ...]
    route_provider: str
    charger_provider: str
    explored_states: int
    assumptions: tuple[str, ...] = ()


# ------------------------------ HTTP helpers --------------------------------

def _get_json(url: str, headers: dict[str, str] | None = None, timeout_s: int = 25) -> Any:
    req = Request(url, headers={"User-Agent": "SKia-EV9-trip-planner/0.2", **(headers or {})})
    with urlopen(req, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


# ----------------------------- Routing adapter -----------------------------

class GraphHopperAdapter:
    """GraphHopper adapter. API docs: https://docs.graphhopper.com/"""

    def __init__(self, api_key: str | None = None, base_url: str = "https://graphhopper.com/api/1/route"):
        self.api_key = api_key or os.getenv("GRAPHHOPPER_API_KEY")
        self.base_url = base_url

    def route(self, origin: GeoPoint, destination: GeoPoint) -> RouteResponse:
        if not self.api_key:
            raise ValueError("Set GRAPHHOPPER_API_KEY or pass api_key.")
        params = [
            ("point", f"{origin.lat},{origin.lon}"),
            ("point", f"{destination.lat},{destination.lon}"),
            ("profile", "car"),
            ("points_encoded", "false"),
            ("elevation", "true"),
            ("instructions", "false"),
            ("calc_points", "true"),
            ("key", self.api_key),
        ]
        data = _get_json(self.base_url + "?" + urlencode(params))
        paths = data.get("paths") or []
        if not paths:
            raise ValueError(f"GraphHopper returned no route: {data}")
        path = paths[0]
        coords = (path.get("points") or {}).get("coordinates") or []
        if len(coords) < 2:
            raise ValueError("GraphHopper route did not include at least two geometry points.")
        geometry = tuple(
            GeoPoint(float(c[1]), float(c[0]), float(c[2]) if len(c) > 2 and c[2] is not None else None)
            for c in coords
        )
        segments = _segments_from_geometry(geometry)
        distance_km = float(path.get("distance", 0.0)) / 1000.0
        # Scale point-derived segment chainage to provider's authoritative route distance.
        chainage = segments[-1].end_km if segments else 0.0
        if chainage > 0 and distance_km > 0 and abs(chainage - distance_km) / distance_km > 0.01:
            scale = distance_km / chainage
            segments = tuple(RouteSegment(
                s.start_km * scale, s.end_km * scale, s.distance_km * scale,
                s.ascent_m, s.descent_m, s.duration_s
            ) for s in segments)
        return RouteResponse(
            provider="GraphHopper",
            origin=geometry[0],
            destination=geometry[-1],
            distance_km=distance_km,
            duration_s=float(path["time"]) / 1000.0 if path.get("time") is not None else None,
            geometry=geometry,
            segments=segments,
            raw_metadata={"weight": path.get("weight"), "bbox": path.get("bbox")},
        )


# -------------------------- Charger-provider adapter -----------------------

class OpenChargeMapAdapter:
    """Open Charge Map adapter; results are projected onto the route geometry."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openchargemap.io/v3/poi/",
        sample_spacing_km: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("OPENCHARGEMAP_API_KEY")
        self.base_url = base_url
        self.sample_spacing_km = max(5.0, sample_spacing_km)

    def chargers_along_route(
        self, route: RouteResponse, corridor_km: float = 15.0, max_results: int = 100
    ) -> ChargerSearchResponse:
        if not self.api_key:
            raise ValueError("Set OPENCHARGEMAP_API_KEY or pass api_key.")
        sample_points = _sample_route(route, self.sample_spacing_km)
        found: dict[str, dict[str, Any]] = {}
        for point, _chainage in sample_points:
            params = {
                "output": "json", "latitude": point.lat, "longitude": point.lon,
                "distance": max(1.0, corridor_km), "distanceunit": "KM",
                "maxresults": min(100, max_results), "compact": "false",
                "verbose": "false", "key": self.api_key,
            }
            rows = _get_json(self.base_url + "?" + urlencode(params))
            for row in rows if isinstance(rows, list) else []:
                address = row.get("AddressInfo") or {}
                ident = str(row.get("ID") or address.get("ID") or "")
                if ident:
                    found[ident] = row
            # Avoid hammering the API when route has many samples.
            time.sleep(0.05)

        chargers: list[Charger] = []
        for ident, row in found.items():
            address = row.get("AddressInfo") or {}
            lat, lon = address.get("Latitude"), address.get("Longitude")
            if lat is None or lon is None:
                continue
            location = GeoPoint(float(lat), float(lon))
            along_km, lateral_km = _project_onto_route(location, route.geometry)
            if lateral_km > corridor_km:
                continue
            connections = row.get("Connections") or []
            powers = [
                float(c.get("PowerKW")) for c in connections
                if c.get("PowerKW") is not None and float(c.get("PowerKW")) > 0
            ]
            if not powers:
                continue
            connector_types = tuple(sorted({
                str((c.get("ConnectionType") or {}).get("Title"))
                for c in connections if (c.get("ConnectionType") or {}).get("Title")
            }))
            status = row.get("StatusType") or {}
            chargers.append(Charger(
                id=f"ocm:{ident}",
                name=str(address.get("Title") or f"Charging site {ident}"),
                lat=float(lat), lon=float(lon), along_route_km=along_km,
                max_power_kw=max(powers), connector_types=connector_types,
                operational=status.get("IsOperational"),
                provider="OpenChargeMap",
                access_notes=address.get("AddressLine1"),
            ))
        chargers.sort(key=lambda c: c.along_route_km)
        return ChargerSearchResponse(
            provider="OpenChargeMap", chargers=tuple(chargers[:max_results]),
            queried_route_distance_km=route.distance_km, raw_count=len(found),
        )


# ------------------------------- Route math ---------------------------------

def _haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    r = 6371.0088
    p1, p2 = radians(a.lat), radians(b.lat)
    dlat, dlon = radians(b.lat - a.lat), radians(b.lon - a.lon)
    h = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    return 2 * r * asin(min(1.0, sqrt(h)))


def _segments_from_geometry(points: tuple[GeoPoint, ...]) -> tuple[RouteSegment, ...]:
    result = []
    chainage = 0.0
    for a, b in zip(points, points[1:]):
        distance = _haversine_km(a, b)
        if distance <= 0:
            continue
        dz = 0.0 if a.elevation_m is None or b.elevation_m is None else b.elevation_m - a.elevation_m
        result.append(RouteSegment(
            start_km=chainage, end_km=chainage + distance, distance_km=distance,
            ascent_m=max(0.0, dz), descent_m=max(0.0, -dz),
        ))
        chainage += distance
    return tuple(result)


def _sample_route(route: RouteResponse, spacing_km: float) -> list[tuple[GeoPoint, float]]:
    if not route.geometry:
        return []
    samples: list[tuple[GeoPoint, float]] = [(route.geometry[0], 0.0)]
    next_at = spacing_km
    chainage = 0.0
    for a, b in zip(route.geometry, route.geometry[1:]):
        d = _haversine_km(a, b)
        while d > 0 and chainage + d >= next_at:
            frac = (next_at - chainage) / d
            p = GeoPoint(
                a.lat + frac * (b.lat - a.lat),
                a.lon + frac * (b.lon - a.lon),
                None if a.elevation_m is None or b.elevation_m is None
                else a.elevation_m + frac * (b.elevation_m - a.elevation_m),
            )
            samples.append((p, next_at))
            next_at += spacing_km
        chainage += d
    samples.append((route.geometry[-1], route.distance_km))
    # Deduplicate near-identical samples.
    out = []
    for item in samples:
        if not out or _haversine_km(out[-1][0], item[0]) > 0.5:
            out.append(item)
    return out


def _project_onto_route(point: GeoPoint, geometry: tuple[GeoPoint, ...]) -> tuple[float, float]:
    """Approximate local equirectangular projection; returns chainage and lateral distance."""
    best = (0.0, float("inf"))
    chainage = 0.0
    for a, b in zip(geometry, geometry[1:]):
        seg_km = _haversine_km(a, b)
        if seg_km <= 0:
            continue
        lat0 = radians((a.lat + b.lat + point.lat) / 3)
        # local planar coordinates in km
        ax, ay = radians(a.lon) * cos(lat0) * 6371.0088, radians(a.lat) * 6371.0088
        bx, by = radians(b.lon) * cos(lat0) * 6371.0088, radians(b.lat) * 6371.0088
        px, py = radians(point.lon) * cos(lat0) * 6371.0088, radians(point.lat) * 6371.0088
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px-ax)*vx + (py-ay)*vy) / denom))
        dx, dy = px - (ax + t*vx), py - (ay + t*vy)
        lateral = sqrt(dx*dx + dy*dy)
        if lateral < best[1]:
            best = (chainage + t * seg_km, lateral)
        chainage += seg_km
    return best


def _energy_for_range(route: RouteResponse, start_km: float, end_km: float,
                      vehicle: VehicleModel, profile: DrivingProfile) -> float:
    """Estimated battery energy over a route interval, positive means energy consumed."""
    if end_km <= start_km:
        return 0.0
    base = profile.base_consumption_kwh_per_100km * profile.load_factor
    cold_delta = max(0.0, 20.0 - profile.temperature_c)
    base *= 1.0 + cold_delta * profile.cold_penalty_per_c_below_20
    total = 0.0
    for seg in route.segments:
        overlap_start = max(start_km, seg.start_km)
        overlap_end = min(end_km, seg.end_km)
        overlap = overlap_end - overlap_start
        if overlap <= 0:
            continue
        fraction = overlap / max(seg.distance_km, 1e-9)
        total += base * overlap / 100.0
        # Approximate elevation allocation proportional to distance in the segment.
        climb_kwh = profile.vehicle_mass_kg * 9.80665 * seg.ascent_m * fraction / 3_600_000
        climb_kwh /= max(0.1, profile.drivetrain_efficiency_uphill)
        downhill_potential = profile.vehicle_mass_kg * 9.80665 * seg.descent_m * fraction / 3_600_000
        regen = downhill_potential * profile.regen_efficiency_downhill
        # Regen cannot exceed energy used on the overlapped segment, nor power limit.
        segment_drive_kwh = base * overlap / 100.0
        regen = min(regen, segment_drive_kwh + climb_kwh,
                    profile.regen_power_limit_kw * (overlap / max(profile.average_speed_kmh, 1.0)))
        total += climb_kwh - regen
    return max(0.0, total)


# ------------------------- SOC charging-curve model ------------------------

def dc_power_kw(soc_pct: float, vehicle: VehicleModel, charger_power_kw: float = 350.0) -> float:
    soc = max(0.0, min(100.0, soc_pct))
    for low, high, power in vehicle.dc_curve_kw:
        if low <= soc < high or (soc == 100.0 and high == 100.0):
            return max(0.1, min(power, vehicle.dc_peak_kw, charger_power_kw))
    return min(vehicle.dc_curve_kw[-1][2], vehicle.dc_peak_kw, charger_power_kw)


def charge_minutes(start_soc_pct: float, end_soc_pct: float, vehicle: VehicleModel,
                   charger_power_kw: float = 350.0, step_pct: float = 0.25) -> float:
    """Numerically integrate SOC-dependent power; charging losses are not separately modelled."""
    start = max(0.0, min(100.0, start_soc_pct))
    end = max(start, min(100.0, end_soc_pct))
    if end <= start:
        return 0.0
    step = max(0.05, step_pct)
    minutes = 0.0
    soc = start
    while soc < end - 1e-9:
        nxt = min(end, soc + step)
        mid = (soc + nxt) / 2
        energy = vehicle.usable_capacity_kwh * (nxt - soc) / 100.0
        minutes += energy / dc_power_kw(mid, vehicle, charger_power_kw) * 60.0
        soc = nxt
    return minutes


# --------------------------- Shortest-time search --------------------------

def optimize_shortest_time(
    route: RouteResponse,
    charger_search: ChargerSearchResponse,
    vehicle: VehicleModel = VehicleModel(),
    profile: DrivingProfile = DrivingProfile(),
    start_soc_pct: float = 100.0,
    reserve_soc_pct: float | None = None,
    max_departure_soc_pct: float = 100.0,
    soc_step_pct: float = 2.0,
    include_unknown_status: bool = True,
    detour_minutes: float = 2.0,
    stop_overhead_minutes: float = 2.0,
) -> OptimizationResult:
    """
    Dynamic shortest-time search over (route node, discretized SOC) states.

    Charger nodes are ordered along the selected route. Each edge may skip any number
    of chargers, and each stop explores every reachable departure SOC up to the configured
    cap. SOC is rounded DOWN after driving, making reachability conservative.
    """
    if route.distance_km <= 0 or not route.segments:
        raise ValueError("Route must contain positive distance and segments.")
    if not 0 <= start_soc_pct <= 100:
        raise ValueError("start_soc_pct must be in [0, 100].")
    reserve = vehicle.reserve_soc_pct if reserve_soc_pct is None else reserve_soc_pct
    if not 0 <= reserve < 100:
        raise ValueError("reserve_soc_pct must be in [0, 100).")
    if not 0 < soc_step_pct <= 10:
        raise ValueError("soc_step_pct must be in (0, 10].")

    chargers = [
        c for c in charger_search.chargers
        if 0.0 < c.along_route_km < route.distance_km
        and c.max_power_kw > 0
        and (c.operational is True or (include_unknown_status and c.operational is None))
    ]
    # Merge near-duplicate sites along the same chainage; keep highest-power entry.
    chargers.sort(key=lambda c: (c.along_route_km, -c.max_power_kw))
    deduped: list[Charger] = []
    for c in chargers:
        if deduped and abs(c.along_route_km - deduped[-1].along_route_km) < 0.5:
            if c.max_power_kw > deduped[-1].max_power_kw:
                deduped[-1] = c
        else:
            deduped.append(c)
    nodes: list[tuple[float, Charger | None]] = [(0.0, None)]
    nodes.extend((c.along_route_km, c) for c in deduped)
    nodes.append((route.distance_km, None))
    nodes.sort(key=lambda x: x[0])
    destination_index = len(nodes) - 1

    # Use integer SOC bins. Floor SOC on arrival; reserve is checked on unrounded SOC.
    bins = int(round(100.0 / soc_step_pct))
    def floor_bin(soc: float) -> int:
        return max(0, min(bins, int((soc + 1e-9) / soc_step_pct)))
    def bin_soc(idx: int) -> float:
        return min(100.0, idx * soc_step_pct)

    start_bin = floor_bin(start_soc_pct)
    start_bin = min(start_bin, bins)
    start_soc_rounded = bin_soc(start_bin)
    if start_soc_rounded < reserve - 1e-9:
        raise ValueError("Starting SOC is below the reserve.")

    # Dijkstra states: (node_index, soc_bin). Parent records the previous state and action.
    start_state = (0, start_bin)
    dist: dict[tuple[int, int], float] = {start_state: 0.0}
    parent: dict[tuple[int, int], tuple[tuple[int, int], dict[str, Any]]] = {}
    heap = [(0.0, 0, start_bin)]
    explored = 0
    best_dest: tuple[int, int] | None = None

    while heap:
        cost, i, soc_bin = heappop(heap)
        state = (i, soc_bin)
        if cost > dist.get(state, float("inf")) + 1e-9:
            continue
        explored += 1
        if i == destination_index:
            best_dest = state
            break
        arrival_soc = bin_soc(soc_bin)
        node_km, charger = nodes[i]
        # At a charger, charge to any higher SOC. At origin, no charging is assumed.
        departure_bins = range(soc_bin, floor_bin(max_departure_soc_pct) + 1) if charger else (soc_bin,)
        for dep_bin in departure_bins:
            departure_soc = bin_soc(dep_bin)
            if departure_soc < arrival_soc - 1e-9:
                continue
            charge_mins = 0.0
            if charger and dep_bin > soc_bin:
                charge_mins = charge_minutes(
                    arrival_soc, departure_soc, vehicle,
                    charger_power_kw=charger.max_power_kw
                ) + stop_overhead_minutes
            for j in range(i + 1, len(nodes)):
                next_km, _next_charger = nodes[j]
                energy = _energy_for_range(route, node_km, next_km, vehicle, profile)
                energy_pct = energy / vehicle.usable_capacity_kwh * 100.0
                true_arrival_soc = departure_soc - energy_pct
                if true_arrival_soc < reserve - 1e-8:
                    # Later nodes require at least as much energy, so no need to test beyond.
                    break
                next_bin = floor_bin(true_arrival_soc)
                if bin_soc(next_bin) < reserve - soc_step_pct:
                    # The unrounded SOC already passed the reserve test; allow conservative
                    # flooring to one bin below reserve, but not below zero.
                    pass
                drive_mins = (next_km - node_km) / max(1.0, profile.average_speed_kmh) * 60.0
                added = drive_mins + charge_mins
                if charger and dep_bin > soc_bin:
                    added += detour_minutes
                next_state = (j, next_bin)
                new_cost = cost + added
                if new_cost + 1e-9 < dist.get(next_state, float("inf")):
                    dist[next_state] = new_cost
                    parent[next_state] = (state, {
                        "from_node": i, "to_node": j,
                        "arrival_soc": arrival_soc,
                        "next_arrival_soc": true_arrival_soc,
                        "departure_soc": departure_soc,
                        "charge_minutes": charge_mins - (stop_overhead_minutes if charge_mins else 0.0),
                        "charger": charger,
                        "drive_minutes": drive_mins,
                    })
                    heappush(heap, (new_cost, j, next_bin))

    if best_dest is None:
        raise RuntimeError(
            "No feasible route found with available chargers and reserve constraint. "
            "Try a lower reserve, finer SOC step, more charger candidates, or a different route."
        )

    actions = []
    cursor = best_dest
    while cursor != start_state:
        prev, action = parent[cursor]
        actions.append((prev, cursor, action))
        cursor = prev
    actions.reverse()

    stops: list[ChargingStop] = []
    driving_minutes = 0.0
    charging_minutes_total = 0.0
    for prev, cur, action in actions:
        driving_minutes += action["drive_minutes"]
        if action["charger"] is not None and action["departure_soc"] > action["arrival_soc"] + 0.1:
            actual_charge = charge_minutes(
                action["arrival_soc"], action["departure_soc"], vehicle,
                charger_power_kw=action["charger"].max_power_kw
            )
            charging_minutes_total += actual_charge
            stops.append(ChargingStop(
                charger=action["charger"],
                arrival_soc_pct=max(0.0, action["arrival_soc"]),
                departure_soc_pct=action["departure_soc"],
                charge_minutes=actual_charge,
            ))
    final_soc = bin_soc(best_dest[1])
    return OptimizationResult(
        total_minutes=dist[best_dest],
        driving_minutes=driving_minutes,
        charging_minutes=charging_minutes_total,
        distance_km=route.distance_km,
        arrival_soc_pct=final_soc,
        stops=tuple(stops),
        route_provider=route.provider,
        charger_provider=charger_search.provider,
        explored_states=explored,
        assumptions=(
            "Usable capacity 80.4 kWh is provisional.",
            "10-80% DC reference is calibrated to 30 minutes; actual vehicle curve may differ.",
            "Route energy uses baseline consumption, temperature factor and coarse elevation/regen estimates.",
            "Traffic, live charger availability, battery preconditioning, queueing and charger sharing are not modelled.",
            "Drive time uses a constant average speed; provider ETA is retained but not used by this optimizer.",
        ),
    )


# --------------------------------- Example -----------------------------------

def demo_route(distance_km: float = 700.0) -> RouteResponse:
    """Synthetic flat route for a reproducible offline example and tests."""
    # One segment per 10 km, flat elevation. Synthetic geometry is not navigable.
    segments = []
    count = max(1, int(distance_km // 10))
    step = distance_km / count
    for i in range(count):
        segments.append(RouteSegment(i * step, (i + 1) * step, step, 0.0, 0.0))
    return RouteResponse(
        provider="synthetic-demo", origin=GeoPoint(51.65, 5.29, 10),
        destination=GeoPoint(45.46, 9.19, 120), distance_km=distance_km,
        duration_s=None, geometry=(GeoPoint(51.65, 5.29, 10), GeoPoint(45.46, 9.19, 120)),
        segments=tuple(segments), raw_metadata={"synthetic": True},
    )


def _demo() -> None:
    route = demo_route(700)
    demo_chargers = ChargerSearchResponse("demo", tuple(
        Charger(
            id=f"demo:{km}", name=f"Demo HPC at km {km}", lat=0, lon=0,
            along_route_km=float(km), max_power_kw=300.0,
            connector_types=("CCS",), operational=True, provider="demo",
        ) for km in (150, 280, 410, 540)
    ), queried_route_distance_km=700, raw_count=4)
    result = optimize_shortest_time(route, demo_chargers, profile=DrivingProfile(
        base_consumption_kwh_per_100km=18, average_speed_kmh=100
    ))
    print(f"Distance: {result.distance_km:.0f} km")
    print(f"Total estimated time: {result.total_minutes/60:.2f} h")
    print(f"Driving: {result.driving_minutes/60:.2f} h; charging: {result.charging_minutes:.1f} min")
    print(f"Arrival SOC: {result.arrival_soc_pct:.1f}%")
    for stop in result.stops:
        print(f"- {stop.charger.name}: {stop.arrival_soc_pct:.0f}% -> "
              f"{stop.departure_soc_pct:.0f}% ({stop.charge_minutes:.1f} min)")


if __name__ == "__main__":
    _demo()
