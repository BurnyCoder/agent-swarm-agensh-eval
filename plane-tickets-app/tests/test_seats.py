"""Seat picking and fare breakdown tests: fare math, seat validation, the flight
page and the flight JSON API.

Flights are inserted on FAR_DAY, 45 days out, which the seed never uses, so
the seed does not change these tests.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skybook import db, pricing, seats
from skybook.app import create_app

TODAY = date.today()
FAR_DAY = TODAY + timedelta(days=45)


# --- helpers and fixtures ----------------------------------------------------


def insert_flight(conn, origin, destination, *, number="SB900", economy=20000, business=60000):
    start = datetime.fromisoformat(f"{FAR_DAY.isoformat()}T08:00")
    end = start + timedelta(minutes=180)
    cursor = conn.execute(
        "INSERT INTO flights (flight_number, airline_code, airline_name, origin, destination,"
        " departs_local, arrives_local, duration_minutes, stops, aircraft,"
        " economy_fare_cents, business_fare_cents)"
        " VALUES (?, 'SB', 'Skybook Air', ?, ?, ?, ?, 180, 0, 'A320', ?, ?)",
        (
            number,
            origin,
            destination,
            start.isoformat(timespec="minutes"),
            end.isoformat(timespec="minutes"),
            economy,
            business,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def take(conn, flight_id, seat_ids, cabin="economy"):
    conn.executemany(
        "INSERT INTO taken_seats (flight_id, seat, cabin) VALUES (?, ?, ?)",
        [(flight_id, seat, cabin) for seat in seat_ids],
    )
    conn.commit()


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


@pytest.fixture()
def client(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture()
def flight_id(conn):
    codes = [row["code"] for row in conn.execute("SELECT code FROM airports ORDER BY code")]
    return insert_flight(conn, codes[0], codes[1])


# --- fare breakdown ----------------------------------------------------------


def test_breakdown_single_passenger_adds_twelve_percent_tax():
    result = pricing.fare_breakdown(20000, 1)
    assert result["per_passenger"] == {"base_cents": 20000, "taxes_cents": 2400, "total_cents": 22400}
    assert result["base_cents"] == 20000
    assert result["taxes_cents"] == 2400
    assert result["total_cents"] == 22400
    assert result["tax_percent"] == 12


def test_taxes_round_down_to_the_cent_per_passenger():
    # 1234 * 12% = 148.08, so each passenger pays 148 cents of tax.
    result = pricing.fare_breakdown(1234, 1)
    assert result["taxes_cents"] == 148
    assert result["total_cents"] == 1382


def test_party_totals_are_the_sum_over_passengers():
    result = pricing.fare_breakdown(1234, 3)
    assert result["base_cents"] == 3702
    assert result["taxes_cents"] == 444
    assert result["total_cents"] == 4146
    per = result["per_passenger"]["total_cents"]
    assert per * 3 == result["total_cents"]


def test_breakdown_is_integer_cents():
    result = pricing.fare_breakdown(19999, 9)
    for value in (result["base_cents"], result["taxes_cents"], result["total_cents"]):
        assert isinstance(value, int)


# --- seat rules --------------------------------------------------------------


def test_cabin_seats_lists_only_that_cabin():
    economy = seats.cabin_seats("A320", "economy")
    business = seats.cabin_seats("A320", "business")
    assert "5A" in economy
    assert "1A" in business
    assert economy.isdisjoint(business)


def test_valid_selection_has_no_messages():
    valid = seats.cabin_seats("A320", "economy")
    assert seats.validate_selection(["6A", "6B"], 2, valid, set()) == []


def test_selection_count_must_match_passengers():
    valid = seats.cabin_seats("A320", "economy")
    messages = seats.validate_selection(["6A"], 2, valid, set())
    assert messages == ["Choose exactly 2 seats: one per passenger. You chose 1."]


def test_selection_singular_wording_for_one_passenger():
    valid = seats.cabin_seats("A320", "economy")
    messages = seats.validate_selection([], 1, valid, set())
    assert messages == ["Choose exactly 1 seat: one per passenger. You chose 0."]


def test_duplicate_seat_is_rejected():
    valid = seats.cabin_seats("A320", "economy")
    messages = seats.validate_selection(["6A", "6a"], 2, valid, set())
    assert messages == ["Seat 6A is listed more than once."]


def test_unknown_seat_is_rejected():
    valid = seats.cabin_seats("A320", "economy")
    messages = seats.validate_selection(["99Z"], 1, valid, set())
    assert messages == ["Seat 99Z does not exist in this cabin."]


def test_business_seat_is_unknown_in_economy():
    valid = seats.cabin_seats("A320", "economy")
    messages = seats.validate_selection(["1A"], 1, valid, set())
    assert messages == ["Seat 1A does not exist in this cabin."]


def test_taken_seat_is_rejected():
    valid = seats.cabin_seats("A320", "economy")
    messages = seats.validate_selection(["6A"], 1, valid, {"6A"})
    assert messages == ["Seat 6A is already taken."]


def test_taken_seats_reads_the_flight_only(conn, flight_id):
    row = conn.execute("SELECT origin, destination FROM flights WHERE id = ?", (flight_id,)).fetchone()
    other = insert_flight(conn, row["origin"], row["destination"], number="SB901")
    take(conn, flight_id, ["6A", "6B"])
    take(conn, other, ["7C"])
    assert seats.taken_seats(conn, flight_id) == {"6A", "6B"}
    assert seats.taken_seats(conn, other) == {"7C"}


def test_cabin_seat_map_keeps_only_the_cabin_rows():
    taken = {"6A"}
    rows = seats.cabin_seat_map("A320", "economy", taken)
    assert rows and all(row["cabin"] == "economy" for row in rows)
    seat_6a = [s for row in rows if row["row"] == 6 for block in row["blocks"] for s in block if s["id"] == "6A"]
    assert seat_6a and seat_6a[0]["taken"] is True


# --- flight page -------------------------------------------------------------


def test_flight_page_renders_fare_breakdown_and_seat_map(client, flight_id):
    response = client.get(f"/flights/{flight_id}", params={"passengers": 2, "cabin": "economy"})
    assert response.status_code == 200
    body = response.text
    assert "Fare breakdown" in body
    assert "$400.00" in body  # 2 x $200.00 base
    assert "$48.00" in body  # 12% tax on the party
    assert "$448.00" in body  # party total
    assert 'name="seat"' in body
    assert 'value="6A"' in body


def test_flight_page_unknown_flight_is_404(client):
    response = client.get("/flights/99999", params={"passengers": 1, "cabin": "economy"})
    assert response.status_code == 404
    assert "does not exist" in response.text


@pytest.mark.parametrize(
    "params",
    [
        {"passengers": 0, "cabin": "economy"},
        {"passengers": 10, "cabin": "economy"},
        {"passengers": "two", "cabin": "economy"},
        {"passengers": 1, "cabin": "first"},
    ],
)
def test_flight_page_bad_party_is_422(client, flight_id, params):
    response = client.get(f"/flights/{flight_id}", params=params)
    assert response.status_code == 422


def test_taken_seat_submitted_gives_error_and_no_confirmation(client, conn, flight_id):
    take(conn, flight_id, ["5A"])
    response = client.get(
        f"/flights/{flight_id}",
        params={"passengers": 1, "cabin": "economy", "picked": 1, "seat": "5A"},
    )
    assert response.status_code == 200
    assert "Seat 5A is already taken." in response.text
    assert "Seats selected" not in response.text


def test_count_mismatch_gives_error_and_no_confirmation(client, flight_id):
    response = client.get(
        f"/flights/{flight_id}",
        params={"passengers": 2, "cabin": "economy", "picked": 1, "seat": "6A"},
    )
    assert "Choose exactly 2 seats" in response.text
    assert "Seats selected" not in response.text


def test_valid_selection_confirms_seats(client, flight_id):
    response = client.get(
        f"/flights/{flight_id}",
        params={"passengers": 2, "cabin": "economy", "picked": 1, "seat": ["6A", "6b"]},
    )
    assert response.status_code == 200
    assert "Seats selected:</strong> 6A, 6B" in response.text
    assert "Continue to passengers" in response.text


def test_unpicked_page_does_not_validate_seats(client, conn, flight_id):
    take(conn, flight_id, ["5A"])
    response = client.get(f"/flights/{flight_id}", params={"passengers": 1, "cabin": "economy"})
    assert response.status_code == 200
    assert "already taken" not in response.text
    assert "Seats selected" not in response.text


def test_taken_seat_is_disabled_in_markup(client, conn, flight_id):
    take(conn, flight_id, ["5A"])
    response = client.get(f"/flights/{flight_id}", params={"passengers": 1, "cabin": "economy"})
    box = re.search(r'<input[^>]*value="5A"[^>]*>', response.text, re.S)
    assert box, "seat 5A checkbox is missing"
    assert " disabled" in box.group(0)
    free = re.search(r'<input[^>]*value="5B"[^>]*>', response.text, re.S)
    assert free and " disabled" not in free.group(0)
    assert 'aria-label="Seat 5A, taken"' in response.text


# --- JSON API ----------------------------------------------------------------


def test_api_flight_includes_breakdown(client, flight_id):
    body = client.get(f"/api/flights/{flight_id}", params={"passengers": 3, "cabin": "economy"}).json()
    assert body["breakdown"] == pricing.fare_breakdown(20000, 3)
    assert body["breakdown"]["total_cents"] == 67200
