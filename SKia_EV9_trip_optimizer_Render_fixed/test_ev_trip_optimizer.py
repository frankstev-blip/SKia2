import unittest
from dataclasses import replace

from ev_trip_optimizer import (
    Charger, ChargerSearchResponse, DrivingProfile, GeoPoint, RouteResponse,
    RouteSegment, VehicleModel, charge_minutes, demo_route, optimize_shortest_time,
    _project_onto_route,
)


class ChargingCurveTests(unittest.TestCase):
    def test_10_to_80_reference_is_approximately_24_minutes(self):
        vehicle = VehicleModel()
        self.assertAlmostEqual(charge_minutes(10, 80, vehicle), 24.0, delta=1.0)

    def test_peak_is_capped_by_vehicle_and_charger(self):
        from ev_trip_optimizer import dc_power_kw
        vehicle = VehicleModel()
        self.assertAlmostEqual(dc_power_kw(20, vehicle, 350), 235.0)
        self.assertAlmostEqual(dc_power_kw(20, vehicle, 150), 150.0)

    def test_higher_soc_charges_slower(self):
        vehicle = VehicleModel()
        low_band_avg = charge_minutes(20, 30, vehicle)
        high_band_avg = charge_minutes(80, 90, vehicle)
        self.assertGreater(high_band_avg, low_band_avg)


class OptimizerTests(unittest.TestCase):
    def make_search(self, route, positions, power=300):
        chargers = tuple(Charger(
            id=f"c{km}", name=f"Charger {km}", lat=51, lon=5,
            along_route_km=float(km), max_power_kw=power,
            operational=True, provider="test"
        ) for km in positions)
        return ChargerSearchResponse("test", chargers, route.distance_km, len(chargers))

    def test_optimizer_finds_feasible_trip(self):
        route = demo_route(500)
        search = self.make_search(route, [120, 240, 360])
        result = optimize_shortest_time(
            route, search, profile=DrivingProfile(
                base_consumption_kwh_per_100km=18, average_speed_kmh=100
            ), start_soc_pct=100, reserve_soc_pct=10, soc_step_pct=2
        )
        self.assertGreaterEqual(result.arrival_soc_pct, 8.0)
        self.assertGreater(result.total_minutes, result.driving_minutes)
        self.assertGreaterEqual(len(result.stops), 1)
        for stop in result.stops:
            self.assertGreaterEqual(stop.departure_soc_pct, stop.arrival_soc_pct)
            self.assertGreaterEqual(stop.arrival_soc_pct, 10.0 - 2.0)

    def test_no_feasible_route_raises(self):
        route = demo_route(500)
        search = self.make_search(route, [])
        with self.assertRaises(RuntimeError):
            optimize_shortest_time(
                route, search, profile=DrivingProfile(
                    base_consumption_kwh_per_100km=30, average_speed_kmh=100
                ), start_soc_pct=60, reserve_soc_pct=10
            )

    def test_reserve_is_enforced(self):
        route = demo_route(500)
        search = self.make_search(route, [100, 200, 300, 400])
        result = optimize_shortest_time(
            route, search, profile=DrivingProfile(
                base_consumption_kwh_per_100km=20, average_speed_kmh=100
            ), start_soc_pct=100, reserve_soc_pct=15, soc_step_pct=1
        )
        for stop in result.stops:
            self.assertGreaterEqual(stop.arrival_soc_pct, 15.0 - 1.0)

    def test_closed_chargers_are_excluded(self):
        route = demo_route(500)
        closed = Charger(
            id="closed", name="Closed", lat=51, lon=5, along_route_km=100,
            max_power_kw=350, operational=False
        )
        search = ChargerSearchResponse("test", (closed,), 500, 1)
        with self.assertRaises(RuntimeError):
            optimize_shortest_time(
                route, search, profile=DrivingProfile(
                    base_consumption_kwh_per_100km=30, average_speed_kmh=100
                ), start_soc_pct=60, reserve_soc_pct=10
            )

    def test_route_projection_chainage(self):
        geometry = (GeoPoint(0, 0), GeoPoint(0, 1), GeoPoint(0, 2))
        km, lateral = _project_onto_route(GeoPoint(0.01, 1), geometry)
        self.assertAlmostEqual(km, 111.2, delta=3.0)
        self.assertLess(lateral, 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
