"""Foundation tests: seeding, schema, aircraft layouts, the server entry point and
the first routes. Feature tests (search, seats, booking) come with their own issues."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skybook import __main__ as entry
from skybook import aircraft, db
from skybook.app import create_app

TODAY = date(2026, 11, 18)


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "skybook.db"
    db.init_db(path, today=TODAY)
    return path


@pytest.fixture()
def conn(db_path):
    connection = db.connect(db_path)
    yield connection
    connection.close()


def test_seed_has_enough_airports_and_flights(conn):
    airports = conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
    flights = conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    assert airports >= 12
    assert flights >= 200


def test_flights_fall_in_the_next_30_days(conn):
    first = TODAY + timedelta(days=1)
    last = TODAY + timedelta(days=30)
    rows = conn.execute("SELECT departs_local FROM flights").fetchall()
    for (departs,) in rows:
        day = datetime.fromisoformat(departs).date()
        assert first <= day <= last


def test_flight_times_agree_with_duration_and_time_zones(conn):
    offsets = dict(conn.execute("SELECT code, utc_offset_hours FROM airports").fetchall())
    rows = conn.execute(
        "SELECT origin, destination, departs_local, arrives_local, duration_minutes FROM flights"
    ).fetchall()
    for origin, dest, departs, arrives, duration in rows:
        assert origin != dest
        elapsed = datetime.fromisoformat(arrives) - datetime.fromisoformat(departs)
        expected = timedelta(minutes=duration) + timedelta(
            hours=offsets[dest] - offsets[origin]
        )
        assert elapsed == expected


def test_fares_are_positive_and_business_costs_more(conn):
    rows = conn.execute("SELECT economy_fare_cents, business_fare_cents FROM flights").fetchall()
    for economy, business in rows:
        assert economy > 0
        assert business > economy


def test_taken_seats_exist_in_the_aircraft_layout(conn):
    rows = conn.execute(
        "SELECT f.aircraft, t.seat, t.cabin FROM taken_seats t JOIN flights f ON f.id = t.flight_id"
    ).fetchall()
    assert rows, "seeding should mark some seats as taken"
    for code, seat, cabin in rows:
        layout = aircraft.get_aircraft(code)
        assert seat in layout.cabin(cabin).seats()


def test_seeding_is_idempotent(db_path):
    db.init_db(db_path, today=TODAY)
    db.init_db(db_path, today=TODAY + timedelta(days=5))
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0] >= 200
    finally:
        conn.close()


def test_seed_is_deterministic(tmp_path):
    first = tmp_path / "a.db"
    second = tmp_path / "b.db"
    db.init_db(first, today=TODAY)
    db.init_db(second, today=TODAY)

    def dump(path):
        c = sqlite3.connect(path)
        try:
            return c.execute(
                "SELECT flight_number, origin, destination, departs_local FROM flights ORDER BY id"
            ).fetchall()
        finally:
            c.close()

    assert dump(first) == dump(second)


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO taken_seats (flight_id, seat, cabin) VALUES (999999, '1A', 'economy')"
        )


def test_seat_map_capacity_matches_layout():
    assert aircraft.get_aircraft("A320").capacity("business") == 16
    assert aircraft.get_aircraft("A320").capacity("economy") == 156
    rows = aircraft.seat_map("A320", taken={"1A"})
    seats = [seat for row in rows for block in row["blocks"] for seat in block]
    assert len(seats) == 172
    assert [s["id"] for s in seats if s["taken"]] == ["1A"]


def test_api_airports_lists_seeded_airports(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/api/airports")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 12
    assert {"code", "name", "city", "country"} <= set(body[0])


def test_index_page_renders(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/")
    assert response.status_code == 200
    assert "SkyBook" in response.text
    assert "viewport" in response.text


def test_static_stylesheet_is_served(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "@media" in response.text


def test_main_binds_all_interfaces_and_honours_port(monkeypatch, db_path):
    calls = {}

    def fake_run(app, host, port):
        calls.update(host=host, port=port)

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)
    monkeypatch.setenv("SKYBOOK_DB", str(db_path))
    monkeypatch.setenv("PORT", "9123")
    entry.main()
    assert calls == {"host": "0.0.0.0", "port": 9123}


def test_main_defaults_to_port_8000(monkeypatch, db_path):
    calls = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, host, port: calls.update(port=port))
    monkeypatch.setenv("SKYBOOK_DB", str(db_path))
    monkeypatch.delenv("PORT", raising=False)
    entry.main()
    assert calls["port"] == 8000
