"""Manage booking tests: lookup by reference and lead last name, the confirmation
and manage pages, cancellation (seats released, idempotent, refused after
departure) and the booking JSON API.

Bookings are inserted with SQL so these tests do not depend on the booking
endpoint. Each test uses its own flight, dated 45 days out, which the seed never
uses. The departed-flight case uses a flight dated yesterday.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skybook import db, search, seats
from skybook.app import create_app

TODAY = date.today()
FAR_DAY = TODAY + timedelta(days=45)
PAST_DAY = TODAY - timedelta(days=1)


# --- helpers and fixtures ----------------------------------------------------


def insert_flight(conn, day, *, number="SB950", economy=20000, business=60000):
    start = datetime.fromisoformat(f"{day.isoformat()}T08:00")
    end = start + timedelta(minutes=180)
    cursor = conn.execute(
        "INSERT INTO flights (flight_number, airline_code, airline_name, origin, destination,"
        " departs_local, arrives_local, duration_minutes, stops, aircraft,"
        " economy_fare_cents, business_fare_cents)"
        " VALUES (?, 'SB', 'Skybook Air', 'LHR', 'JFK', ?, ?, 180, 0, 'A320', ?, ?)",
        (
            number,
            start.isoformat(timespec="minutes"),
            end.isoformat(timespec="minutes"),
            economy,
            business,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def insert_booking(
    conn,
    flight_id,
    *,
    reference="ABC123",
    last_names=("Lovelace",),
    seat_ids=("12A",),
    cabin="economy",
    status="confirmed",
    total_cents=45000,
):
    """Insert a booking with one passenger per last name, and its taken seats."""
    cursor = conn.execute(
        "INSERT INTO bookings (reference, flight_id, cabin, passengers, status, lead_email,"
        " total_cents, created_at) VALUES (?, ?, ?, ?, ?, 'ada@example.com', ?, '2026-01-01T00:00:00Z')",
        (reference, flight_id, cabin, len(last_names), status, total_cents),
    )
    booking_id = cursor.lastrowid
    for index, (last_name, seat) in enumerate(zip(last_names, seat_ids)):
        conn.execute(
            "INSERT INTO passengers (booking_id, first_name, last_name, date_of_birth, seat, email)"
            " VALUES (?, 'Ada', ?, '1990-12-10', ?, ?)",
            (booking_id, last_name, seat, "ada@example.com" if index == 0 else None),
        )
        if status == "confirmed":
            conn.execute(
                "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id) VALUES (?, ?, ?, ?)",
                (flight_id, seat, cabin, booking_id),
            )
    conn.commit()
    return booking_id


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
    return insert_flight(conn, FAR_DAY)


@pytest.fixture()
def booking(conn, flight_id):
    """A confirmed booking with two passengers on seats 12A and 12B."""
    insert_booking(
        conn,
        flight_id,
        last_names=("Lovelace", "Hopper"),
        seat_ids=("12A", "12B"),
        total_cents=90000,
    )
    return "ABC123"


def taken(conn, flight_id):
    return seats.taken_seats(conn, flight_id)


# --- lookup form -------------------------------------------------------------


def test_lookup_page_renders_form(client):
    response = client.get("/bookings")
    assert response.status_code == 200
    assert 'name="reference"' in response.text
    assert 'name="last_name"' in response.text


def test_lookup_redirects_to_booking_page(client, booking):
    response = client.get("/bookings?reference=abc123&last_name=%20LOVELACE%20", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/bookings/ABC123?last_name=")
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "ABC123" in page.text


def test_lookup_rejects_malformed_reference(client):
    response = client.get("/bookings?reference=AB1&last_name=Lovelace")
    assert response.status_code == 422
    assert "6-character booking reference" in response.text


def test_lookup_requires_last_name(client):
    response = client.get("/bookings?reference=ABC123&last_name=")
    assert response.status_code == 422
    assert "last name of the lead passenger" in response.text


# --- manage page -------------------------------------------------------------


def test_view_shows_confirmation_banner_and_details(client, booking):
    response = client.get("/bookings/ABC123?last_name=Lovelace&new=1")
    assert response.status_code == 200
    assert "Thank you. Your booking is confirmed" in response.text
    assert "12A" in response.text and "12B" in response.text
    assert "Ada Lovelace" in response.text and "Ada Hopper" in response.text
    assert search.format_cents(90000) in response.text
    assert "Cancel booking" in response.text


def test_view_without_new_flag_has_no_banner(client, booking):
    response = client.get("/bookings/ABC123?last_name=Lovelace")
    assert response.status_code == 200
    assert "Thank you. Your booking is confirmed" not in response.text


def test_last_name_match_ignores_case_and_spacing(client, booking):
    response = client.get("/bookings/abc123?last_name=%20%20lovelace%20%20")
    assert response.status_code == 200
    assert "ABC123" in response.text


def test_only_lead_passenger_last_name_opens_booking(client, booking):
    response = client.get("/bookings/ABC123?last_name=Hopper")
    assert response.status_code == 404


def test_wrong_last_name_and_unknown_reference_look_the_same(client, booking):
    wrong_name = client.get("/bookings/ABC123?last_name=Nobody")
    unknown_ref = client.get("/bookings/ZZZ999?last_name=Lovelace")
    assert wrong_name.status_code == unknown_ref.status_code == 404
    assert "No booking matches that reference and last name." in wrong_name.text
    assert "No booking matches that reference and last name." in unknown_ref.text


def test_missing_last_name_on_view_is_422(client, booking):
    response = client.get("/bookings/ABC123")
    assert response.status_code == 422


# --- cancel from the page ----------------------------------------------------


def test_cancel_form_releases_seats_and_redirects(client, conn, flight_id, booking):
    assert taken(conn, flight_id) == {"12A", "12B"}
    response = client.post(
        "/bookings/ABC123/cancel", data={"last_name": "Lovelace"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("&cancelled=1")

    assert taken(conn, flight_id) == set()
    status = conn.execute("SELECT status FROM bookings WHERE reference = 'ABC123'").fetchone()
    assert status["status"] == "cancelled"
    # Passenger rows keep their seats so the record still shows what was booked.
    passengers = conn.execute("SELECT seat FROM passengers ORDER BY id").fetchall()
    assert [row["seat"] for row in passengers] == ["12A", "12B"]


def test_cancelled_page_shows_banner_and_no_cancel_button(client, booking):
    client.post("/bookings/ABC123/cancel", data={"last_name": "Lovelace"})
    response = client.get("/bookings/ABC123?last_name=Lovelace&cancelled=1")
    assert response.status_code == 200
    assert "Your booking has been cancelled and its seats released." in response.text
    assert "Cancel booking" not in response.text


def test_cancel_only_releases_seats_of_that_booking(client, conn, flight_id, booking):
    insert_booking(conn, flight_id, reference="XYZ789", last_names=("Turing",), seat_ids=("14C",))
    # A pre-sold seat with no booking is not touched either.
    conn.execute(
        "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id) VALUES (?, '20F', 'economy', NULL)",
        (flight_id,),
    )
    conn.commit()
    client.post("/bookings/ABC123/cancel", data={"last_name": "Lovelace"})
    assert taken(conn, flight_id) == {"14C", "20F"}


def test_cancel_is_idempotent(client, conn, flight_id, booking):
    first = client.post("/api/bookings/ABC123/cancel?last_name=Lovelace")
    second = client.post("/api/bookings/ABC123/cancel?last_name=Lovelace")
    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert taken(conn, flight_id) == set()


def test_cancel_with_wrong_last_name_changes_nothing(client, conn, flight_id, booking):
    response = client.post("/bookings/ABC123/cancel", data={"last_name": "Nobody"})
    assert response.status_code == 404
    assert taken(conn, flight_id) == {"12A", "12B"}


def test_cancel_without_last_name_is_not_found(client, booking):
    response = client.post("/bookings/ABC123/cancel", data={})
    assert response.status_code == 404


# --- departed flights --------------------------------------------------------


@pytest.fixture()
def departed(conn):
    flight = insert_flight(conn, PAST_DAY, number="SB901")
    insert_booking(conn, flight, reference="OLD111", last_names=("Curie",), seat_ids=("3A",))
    return flight


def test_departed_booking_shows_no_cancel_button(client, departed):
    response = client.get("/bookings/OLD111?last_name=Curie")
    assert response.status_code == 200
    assert "Cancel booking" not in response.text
    assert "already departed" in response.text


def test_departed_flight_refuses_cancel_on_page(client, conn, departed):
    response = client.post("/bookings/OLD111/cancel", data={"last_name": "Curie"})
    assert response.status_code == 409
    assert "already departed" in response.text
    row = conn.execute("SELECT status FROM bookings WHERE reference = 'OLD111'").fetchone()
    assert row["status"] == "confirmed"
    assert taken(conn, departed) == {"3A"}


def test_departed_flight_refuses_cancel_in_api(client, conn, departed):
    response = client.post("/api/bookings/OLD111/cancel?last_name=Curie")
    assert response.status_code == 409
    assert "already departed" in response.json()["errors"]["booking"]
    assert taken(conn, departed) == {"3A"}


# --- JSON API ----------------------------------------------------------------


def test_api_returns_booking_details(client, booking):
    response = client.get("/api/bookings/abc123?last_name=lovelace")
    assert response.status_code == 200
    body = response.json()
    assert body["reference"] == "ABC123"
    assert body["status"] == "confirmed"
    assert body["cabin"] == "economy"
    assert body["passenger_count"] == 2
    assert body["total_cents"] == 90000
    assert body["seats"] == ["12A", "12B"]
    assert body["can_cancel"] is True
    assert body["flight"]["flight_number"] == "SB950"
    assert [p["last_name"] for p in body["passengers"]] == ["Lovelace", "Hopper"]


def test_api_missing_last_name_is_422(client, booking):
    response = client.get("/api/bookings/ABC123")
    assert response.status_code == 422
    assert "last_name" in response.json()["errors"]


def test_api_unknown_or_wrong_name_is_404(client, booking):
    assert client.get("/api/bookings/ABC123?last_name=Nobody").status_code == 404
    assert client.get("/api/bookings/ZZZ999?last_name=Lovelace").status_code == 404


def test_api_cancel_releases_seats_and_reports_status(client, conn, flight_id, booking):
    response = client.post("/api/bookings/ABC123/cancel?last_name=Lovelace")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["can_cancel"] is False
    assert taken(conn, flight_id) == set()


def test_api_cancel_missing_last_name_is_422(client, booking):
    response = client.post("/api/bookings/ABC123/cancel")
    assert response.status_code == 422
    assert "last_name" in response.json()["errors"]


def test_api_cancel_unknown_booking_is_404(client, booking):
    response = client.post("/api/bookings/ZZZ999/cancel?last_name=Lovelace")
    assert response.status_code == 404
