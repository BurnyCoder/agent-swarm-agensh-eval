"""SQLite storage: connections, schema and first-start seeding.

Each request opens its own connection (see ``app.get_db``). The schema is
created with ``IF NOT EXISTS``. Seeding runs only when the airports table is
empty.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from . import seed

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "skybook.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    utc_offset_hours REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY,
    flight_number TEXT NOT NULL,
    airline_code TEXT NOT NULL,
    airline_name TEXT NOT NULL,
    origin TEXT NOT NULL REFERENCES airports(code),
    destination TEXT NOT NULL REFERENCES airports(code),
    departs_local TEXT NOT NULL,  -- ISO 8601, local time at origin
    arrives_local TEXT NOT NULL,  -- ISO 8601, local time at destination
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    stops INTEGER NOT NULL CHECK (stops >= 0),
    aircraft TEXT NOT NULL,
    economy_fare_cents INTEGER NOT NULL CHECK (economy_fare_cents > 0),
    business_fare_cents INTEGER NOT NULL CHECK (business_fare_cents > 0),
    CHECK (origin <> destination)
);

CREATE INDEX IF NOT EXISTS flights_route_departure
    ON flights (origin, destination, departs_local);

-- Only taken seats are stored. A seat with no row is free.
-- booking_id is NULL for seats sold before this demo started.
CREATE TABLE IF NOT EXISTS taken_seats (
    flight_id INTEGER NOT NULL REFERENCES flights(id),
    seat TEXT NOT NULL,
    cabin TEXT NOT NULL,
    booking_id INTEGER,
    PRIMARY KEY (flight_id, seat)
);

-- One row per booking. The lead passenger is the lowest passengers.id.
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,  -- 6 uppercase letters or digits
    flight_id INTEGER NOT NULL REFERENCES flights(id),
    cabin TEXT NOT NULL CHECK (cabin IN ('economy', 'business')),
    passengers INTEGER NOT NULL CHECK (passengers BETWEEN 1 AND 9),
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    lead_email TEXT NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    created_at TEXT NOT NULL  -- ISO 8601, UTC
);

CREATE TABLE IF NOT EXISTS passengers (
    id INTEGER PRIMARY KEY,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    date_of_birth TEXT NOT NULL,  -- YYYY-MM-DD
    seat TEXT NOT NULL,
    email TEXT                    -- set for the lead passenger only
);

CREATE INDEX IF NOT EXISTS passengers_booking ON passengers (booking_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with dict-like rows and foreign keys enforced."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | Path, today: date | None = None) -> None:
    """Create the schema and seed sample data if the database is empty."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        has_airports = conn.execute("SELECT 1 FROM airports LIMIT 1").fetchone()
        if has_airports is None:
            seed.seed_database(conn, today or date.today())
            conn.commit()
    finally:
        conn.close()


def list_airports(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT code, name, city, country FROM airports ORDER BY city, code"
    ).fetchall()
