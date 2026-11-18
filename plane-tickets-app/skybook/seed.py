"""Deterministic sample data: airports, airlines and 30 days of flights.

Everything here is fictional. Airline names are invented and no real booking
or pricing data is used. The random generator uses a fixed seed, so every
fresh database has the same flights.
"""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import date, datetime, time, timedelta

from . import aircraft as fleet

SEED = 20261118
DAYS = 30  # flights are generated for the next 30 days, starting tomorrow
FLIGHTS_PER_DAY = 9

# code, name, city, country, latitude, longitude, UTC offset in hours.
# Offsets are fixed (no daylight saving). They match the winter (Nov-Dec)
# offsets that apply to the seeded window.
AIRPORTS: list[tuple[str, str, str, str, float, float, float]] = [
    ("JFK", "John F. Kennedy International", "New York", "United States", 40.6413, -73.7781, -5),
    ("LAX", "Los Angeles International", "Los Angeles", "United States", 33.9416, -118.4085, -8),
    ("ORD", "O'Hare International", "Chicago", "United States", 41.9742, -87.9073, -6),
    ("YYZ", "Toronto Pearson International", "Toronto", "Canada", 43.6777, -79.6248, -5),
    ("GRU", "Sao Paulo-Guarulhos International", "Sao Paulo", "Brazil", -23.4356, -46.4731, -3),
    ("LHR", "London Heathrow", "London", "United Kingdom", 51.4700, -0.4543, 0),
    ("CDG", "Paris Charles de Gaulle", "Paris", "France", 49.0097, 2.5479, 1),
    ("AMS", "Amsterdam Schiphol", "Amsterdam", "Netherlands", 52.3105, 4.7683, 1),
    ("FRA", "Frankfurt Airport", "Frankfurt", "Germany", 50.0379, 8.5622, 1),
    ("MAD", "Madrid-Barajas", "Madrid", "Spain", 40.4983, -3.5676, 1),
    ("FCO", "Rome Fiumicino", "Rome", "Italy", 41.8003, 12.2389, 1),
    ("DXB", "Dubai International", "Dubai", "United Arab Emirates", 25.2532, 55.3657, 4),
    ("SIN", "Singapore Changi", "Singapore", "Singapore", 1.3644, 103.9915, 8),
    ("HND", "Tokyo Haneda", "Tokyo", "Japan", 35.5494, 139.7798, 9),
    ("SYD", "Sydney Kingsford Smith", "Sydney", "Australia", -33.9461, 151.1772, 11),
]

AIRLINES: list[tuple[str, str]] = [
    ("AU", "Aurora Air"),
    ("MR", "Meridian Airways"),
    ("SB", "SkyBridge"),
    ("SL", "Silverline"),
    ("CR", "Cascade Regional"),
]

CRUISE_KMH = 820
TURNAROUND_MIN = 25
STOP_MIN = 70
MIN_ROUTE_KM = 400
MAX_ROUTE_KM = 14000


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def seed_database(conn: sqlite3.Connection, today: date, rng: random.Random | None = None) -> None:
    """Insert airports, flights and seeded seat occupancy. Caller commits."""
    rng = rng or random.Random(SEED)
    conn.executemany(
        "INSERT INTO airports (code, name, city, country, latitude, longitude, utc_offset_hours)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        AIRPORTS,
    )

    coords = {a[0]: (a[4], a[5], a[6]) for a in AIRPORTS}
    routes: list[tuple[str, str, float]] = []
    for origin in coords:
        for dest in coords:
            if origin == dest:
                continue
            km = haversine_km(coords[origin][0], coords[origin][1], coords[dest][0], coords[dest][1])
            if MIN_ROUTE_KM <= km <= MAX_ROUTE_KM:
                routes.append((origin, dest, km))

    for day_offset in range(1, DAYS + 1):
        day = today + timedelta(days=day_offset)
        for _ in range(FLIGHTS_PER_DAY):
            origin, dest, km = rng.choice(routes)
            _insert_flight(conn, rng, day, origin, dest, km, coords)


def _insert_flight(
    conn: sqlite3.Connection,
    rng: random.Random,
    day: date,
    origin: str,
    dest: str,
    km: float,
    coords: dict[str, tuple[float, float, float]],
) -> None:
    airline_code, airline_name = rng.choice(AIRLINES)
    aircraft = rng.choice(list(fleet.AIRCRAFT))
    stops = 0 if km < 5000 or rng.random() < 0.6 else 1

    flight_time = round(km / CRUISE_KMH * 60)
    duration = flight_time + TURNAROUND_MIN + stops * STOP_MIN

    depart_local = datetime.combine(
        day, time(rng.randint(6, 21), rng.choice(range(0, 60, 5)))
    )
    origin_offset = coords[origin][2]
    dest_offset = coords[dest][2]
    depart_utc = depart_local - timedelta(hours=origin_offset)
    arrive_local = depart_utc + timedelta(minutes=duration) + timedelta(hours=dest_offset)

    economy_dollars = round((km * 0.07 + 60) * rng.uniform(0.8, 1.3) / 5) * 5
    business_dollars = round(economy_dollars * rng.uniform(2.6, 3.4) / 5) * 5

    cursor = conn.execute(
        "INSERT INTO flights (flight_number, airline_code, airline_name, origin, destination,"
        " departs_local, arrives_local, duration_minutes, stops, aircraft,"
        " economy_fare_cents, business_fare_cents)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{airline_code}{rng.randint(100, 9999)}",
            airline_code,
            airline_name,
            origin,
            dest,
            depart_local.isoformat(timespec="minutes"),
            arrive_local.isoformat(timespec="minutes"),
            duration,
            stops,
            aircraft,
            int(economy_dollars) * 100,
            int(business_dollars) * 100,
        ),
    )
    flight_id = cursor.lastrowid

    # Some seats are already sold to other passengers (booking_id NULL), so the
    # seat map has taken seats from the start.
    layout = fleet.get_aircraft(aircraft)
    occupied = []
    for cabin in layout.cabins:
        seats = cabin.seats()
        k = int(len(seats) * rng.uniform(0.2, 0.85))
        occupied.extend((flight_id, seat, cabin.name, None) for seat in rng.sample(seats, k))
    conn.executemany(
        "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id) VALUES (?, ?, ?, ?)",
        occupied,
    )
