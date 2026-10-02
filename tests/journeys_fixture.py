"""A small feed for the planner: a direct bus, a tram feeding a bus across a transfer walk,
a second bus on the same stops, a rare fast bus, a route outside the window, a weekend-only route and
a holiday on the next weekday."""

import io
import math
import zipfile

METRES_PER_DEGREE_LAT = 111_194.93
ORIGIN = (56.90, 24.10)
FLAT = (0, 100)
TARGET_ONE = (4000, 100)
TARGET_TWO = (4000, 4100)
STOPS = {
    "h1": ((0, 0), "Home stop"),
    "h8": ((0, -100), "Rare stop"),
    "m1": ((2000, 0), "Middle"),
    "t1": ((4000, 0), "Target stop"),
    "x1": ((0, 2000), "Tram middle"),
    "hub_a": ((0, 4000), "Hub tram"),
    "hub_b": ((150, 4000), "Hub bus"),
    "t2": ((4000, 4000), "Second target stop"),
}
HOLIDAY = "20260928"


def point(east_m: float, north_m: float) -> tuple[float, float]:
    lat = ORIGIN[0] + north_m / METRES_PER_DEGREE_LAT
    lon = ORIGIN[1] + east_m / (METRES_PER_DEGREE_LAT * math.cos(math.radians(ORIGIN[0])))
    return lat, lon


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _every(first: int, last: int, step: int) -> list[int]:
    return list(range(first, last + 1, step))


def _csv(header: str, rows: list[str]) -> str:
    return "﻿" + "\n".join([header, *rows]) + "\n"


def build_feed() -> bytes:
    routes = [
        ("bus_1", "1", 3), ("bus_5", "5", 3), ("bus_8", "8", 3), ("tram_2", "2", 0),
        ("bus_3", "3", 3), ("bus_9", "9", 3), ("bus_7", "7", 3),
    ]
    services = {
        "bus_1": [("0", ["h1", "m1", "t1"], [0, 5, 10], _every(360, 1310, 10), "wd"),
                  ("1", ["t1", "m1", "h1"], [0, 5, 10], _every(360, 1310, 10), "wd")],
        "bus_5": [("0", ["h1", "t1"], [0, 10], _every(365, 1295, 30), "wd")],
        "bus_8": [("0", ["h8", "t1"], [0, 5], _every(450, 570, 60), "wd")],
        "tram_2": [("0", ["h1", "x1", "hub_a"], [0, 4, 8], _every(360, 1314, 6), "wd")],
        "bus_3": [("0", ["hub_b", "t2"], [0, 12], _every(360, 1305, 15), "wd")],
        "bus_9": [("0", ["h1", "t1"], [0, 3], [630], "wd")],
        "bus_7": [("0", ["h1", "t2"], [0, 20], _every(360, 1310, 10), "we")],
    }
    shapes = {
        "bus_1_shape": [point(east, 0) for east in range(0, 4001, 500)],
        "tram_2_shape": [point(0, 0), point(0, 2000), point(0, 4000)],
        "bus_3_shape": [point(150, 4000), point(4000, 4000)],
    }
    trips, stop_times = [], []
    for route_id, patterns in services.items():
        for direction, stops, offsets, starts, service in patterns:
            shape = f"{route_id}_shape" if f"{route_id}_shape" in shapes else ""
            for start in starts:
                trip_id = f"{route_id}_{direction}_{start}"
                trips.append(f"{route_id},{service},{trip_id},{direction},{shape}")
                for position, (stop_id, offset) in enumerate(zip(stops, offsets), start=1):
                    clock = _clock(start + offset)
                    stop_times.append(f"{trip_id},{clock},{clock},{stop_id},{position},0,0")
    files = {
        "stops.txt": _csv(
            "stop_id,stop_name,stop_lat,stop_lon",
            [f"{stop_id},{name},{point(*east_north)[0]},{point(*east_north)[1]}"
             for stop_id, (east_north, name) in STOPS.items()],
        ),
        "routes.txt": _csv(
            "route_id,route_short_name,route_long_name,route_type",
            [f"{route_id},\"{name}\",,{kind}" for route_id, name, kind in routes],
        ),
        "trips.txt": _csv("route_id,service_id,trip_id,direction_id,shape_id", trips),
        "calendar.txt": _csv(
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date",
            ["wd,1,1,1,1,1,0,0,20260901,20270901", "we,0,0,0,0,0,1,1,20260901,20270901"],
        ),
        "calendar_dates.txt": _csv(
            "service_id,date,exception_type", [f"wd,{HOLIDAY},2", f"we,{HOLIDAY},1"]
        ),
        "stop_times.txt": _csv(
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type",
            stop_times,
        ),
        "shapes.txt": _csv(
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence",
            [f"{shape_id},{lat},{lon},{index}"
             for shape_id, points in shapes.items()
             for index, (lat, lon) in enumerate(points, start=1)],
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text.encode("utf-8"))
    return buffer.getvalue()
