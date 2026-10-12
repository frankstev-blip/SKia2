# SKia EV9 Long Range AWD trip optimizer module

## What is included

- `ev_trip_optimizer.py`: normalized schemas, GraphHopper routing adapter, Open Charge Map charger adapter, route projection, elevation-aware energy approximation, SOC-dependent charging curve, and a shortest-time search.
- `test_ev_trip_optimizer.py`: offline unit tests using Python's `unittest`.
- `requirements.txt`: empty by design; runtime uses the Python standard library only.

## Run offline

```bash
python ev_trip_optimizer.py
python -m unittest -v
```

The demo is synthetic and does not represent a real navigable route.

## Enable live providers

```bash
export GRAPHHOPPER_API_KEY="..."
export OPENCHARGEMAP_API_KEY="..."
```

```python
from ev_trip_optimizer import (
    GraphHopperAdapter, OpenChargeMapAdapter, GeoPoint,
    VehicleModel, DrivingProfile, optimize_shortest_time,
)

route = GraphHopperAdapter().route(
    GeoPoint(51.65, 5.29),  # example: Vught area
    GeoPoint(45.46, 9.19),  # example: Milan
)
chargers = OpenChargeMapAdapter().chargers_along_route(
    route, corridor_km=15, max_results=100
)
result = optimize_shortest_time(
    route, chargers,
    vehicle=VehicleModel(usable_capacity_kwh=96.0, dc_peak_kw=235, ac_max_kw=11),
    profile=DrivingProfile(
        base_consumption_kwh_per_100km=21,
        average_speed_kmh=100,
        temperature_c=0,
        load_factor=1.05,
    ),
    start_soc_pct=100,
    reserve_soc_pct=10,
    soc_step_pct=2,
)
print(result)
```

## Normalized contracts

- `RouteResponse`: provider, origin/destination, route distance, optional provider ETA, geometry and ordered `RouteSegment`s. Each segment has chainage, distance, ascent/descent, and optional duration.
- `ChargerSearchResponse`: provider, candidate `Charger`s, queried route distance, raw result count.
- `Charger`: provider ID, coordinates, route chainage, max connector power, connector types, operational status and access notes.
- `OptimizationResult`: total, driving and charging minutes, arrival SOC, selected charging stops, provider IDs and model assumptions.

## Optimizer design

The optimizer runs Dijkstra over `(route node, SOC bin)` states. At a charger, it enumerates feasible departure SOC bins and considers every downstream node reachable without violating the reserve. SOC is rounded down after each drive leg; the unrounded estimate must still meet the reserve. The edge cost includes constant-speed driving time, SOC-integrated charging time, and configurable stop/detour overhead. This is a practical baseline, not yet a traffic-aware global routing engine.

## Model caveats and next production steps

1. **Provisional battery capacity:** 96.0 kWh is the approximate usable capacity supplied for this EV9 variant.
2. **Charging curve:** The supplied reference is used to calibrate a piecewise curve with a 235 kW cap and approximately 24-minute 10-80% charging. It is not an OEM-verified charge curve. Replace it with measured/validated data and add battery-temperature and preconditioning effects.
3. **AC:** 11 kW is represented in the vehicle schema, but this long-trip optimizer currently optimizes DC stops only. AC can be added for overnight/destination charging.
4. **Route energy:** Baseline consumption, temperature and elevation are approximations. For production, use calibrated speed/weather/road-class models and avoid double-counting elevation already present in empirical consumption.
5. **Route/charger matching:** OCM results are sampled along the route and projected to approximate chainage. The optimizer uses route chainage for energy, but does not yet re-route each charger detour or request live availability.
6. **Live data:** Open Charge Map is a discovery dataset, not guaranteed live occupancy/power. Validate operating status, connector compatibility and power at planning time.
7. **Production hardening:** Add request retries/backoff, caching, API quotas, provider attribution, schema-version checks, route alternatives, route-aware detour times, battery-temperature derating, charger-specific power curves, uncertainty margins, and property-based tests.

## Dependencies and provider docs

- GraphHopper Routing API: https://docs.graphhopper.com/
- Open Charge Map API: https://openchargemap.org/site/develop/api

## Local browser app (new)

A simple browser-based interface is included in `app.py`. It runs locally on your Mac and uses only Python's standard library. It does not yet use live routing or live charger data.

### Start it

1. Open the `bmw_i5_trip_optimizer` folder.
2. Double-click `START_APP.command`. If macOS blocks it, open Terminal in this folder and run `python3 app.py`.
3. Your browser should open at `http://127.0.0.1:8765`.
4. Keep the Terminal window open while using the app. Press `Control+C` in Terminal to stop it.

If port 8765 is already in use, close the other app using it or change `PORT` in `app.py`.

### Current limitations

This is an initial local UI prototype. Route distance is entered manually; there is no map, address lookup, live traffic, live charger availability, charger detour calculation, battery preconditioning model, or vehicle telemetry. The charging curve and energy adjustments are approximations and should be calibrated with real-trip data before relying on them for tight arrival reserves.


## Publish a shareable web link (Render)

This folder includes `render.yaml` and a server configuration that uses the `PORT` environment variable supplied by Render. The service is configured as a public web service; anyone with its URL can open the planner. No login is added by this app.

1. Create a new GitHub repository and upload the contents of this folder (the files should be at the repository root, including `app.py` and `render.yaml`).
2. In Render, choose **New + → Blueprint** and connect that GitHub repository. Render will detect `render.yaml`; approve creation of the `skia-ev9-trip-optimizer` web service.
3. Wait for the deploy to finish, then open the `onrender.com` URL Render assigns and share that URL.

The free service may spin down when idle, so the first visit after inactivity can take a little longer. This package prepares the app for deployment but does not itself publish it or create the public URL.

### Run locally on macOS

```bash
python3 app.py
```

Then open `http://127.0.0.1:8765`.
