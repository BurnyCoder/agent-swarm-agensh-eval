"""A seat taken between the passenger page and payment re-renders the payment page.

The seat check runs before payment, so a plain double-booking is caught there and
sent back to the flight page. These tests force the race: the pre-check is
bypassed (as if the other customer committed just after it ran), and the write
step must then refuse the booking, show the payment page with the reason, and
leave no booking or seat rows behind.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from html import unescape
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from skybook import checkout_routes, seats
from skybook.app import create_app

SEAT_INPUT = re.compile(r'<input type="checkbox" name="seat" value="([^"]+)"([^>]*)>')
CARD = "4242 4242 4242 4242"
EXPIRY = f"12/{(date.today().year + 3) % 100:02d}"


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "skybook.db"


@pytest.fixture()
def client(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture()
def flight_id(db_path, client):
    """A future flight in the seed. The app creates the database on first use."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM flights WHERE substr(departs_local, 1, 10) >= ? ORDER BY id LIMIT 1",
        (date.today().isoformat(),),
    ).fetchone()
    conn.close()
    assert row is not None, "the seed holds no future flight"
    return row[0]


def free_seats(client, flight_id):
    page = client.get(f"/flights/{flight_id}", params={"passengers": 2, "cabin": "economy"})
    return [
        unescape(value)
        for value, attrs in SEAT_INPUT.findall(page.text)
        if "disabled" not in attrs
    ]


def pay_form(seat_ids):
    people = [("Ada", "Lovelace", "1990-05-14"), ("Grace", "Hopper", "1985-12-09")]
    return {
        "passengers": "2",
        "cabin": "economy",
        "seat": list(seat_ids),
        "first_name": [f for f, _, _ in people],
        "last_name": [l for _, l, _ in people],
        "date_of_birth": [d for _, _, d in people],
        "email": "ada@example.com",
        "card_number": CARD,
        "expiry": EXPIRY,
        "cvc": "123",
    }


def take_seat(db_path, flight_id, seat):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id)"
        " VALUES (?, ?, 'economy', NULL)",
        (flight_id, seat),
    )
    conn.commit()
    conn.close()


def count_rows(db_path):
    conn = sqlite3.connect(db_path)
    counts = {
        "bookings": conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
        "passengers": conn.execute("SELECT COUNT(*) FROM passengers").fetchone()[0],
        "taken_seats": conn.execute("SELECT COUNT(*) FROM taken_seats").fetchone()[0],
    }
    conn.close()
    return counts


def seat_is_taken(db_path, flight_id, seat):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM taken_seats WHERE flight_id = ? AND seat = ?", (flight_id, seat)
    ).fetchone()
    conn.close()
    return row is not None


def test_seat_taken_before_write_shows_payment_page_with_reason(
    client, db_path, flight_id, monkeypatch
):
    first, second = free_seats(client, flight_id)[:2]
    # The other customer commits after the pre-check but before this write.
    take_seat(db_path, flight_id, first)
    monkeypatch.setattr(checkout_routes, "_seat_problems", lambda *args: [])
    before = count_rows(db_path)

    response = client.post(f"/book/{flight_id}/pay", data=pay_form([first, second]))

    assert response.status_code == 409
    assert 'role="alert"' in response.text
    assert f"Seat {first} is already taken." in response.text
    assert 'id="payment-form"' in response.text
    assert "Location" not in response.headers
    assert count_rows(db_path) == before
    assert not seat_is_taken(db_path, flight_id, second)


def test_write_conflict_rolls_back_and_shows_payment_page(
    client, db_path, flight_id, monkeypatch
):
    first, second = free_seats(client, flight_id)[:2]
    # Both checks see the seats as free, but the seat is already in taken_seats,
    # so the insert hits the primary key and the write is rolled back.
    take_seat(db_path, flight_id, first)
    monkeypatch.setattr(checkout_routes, "_seat_problems", lambda *args: [])
    monkeypatch.setattr(seats, "taken_seats", lambda conn, flight: set())
    before = count_rows(db_path)

    response = client.post(f"/book/{flight_id}/pay", data=pay_form([first, second]))

    assert response.status_code == 409
    assert "Another customer took one of these seats. Choose again." in response.text
    assert count_rows(db_path) == before
    assert not seat_is_taken(db_path, flight_id, second)


def test_api_conflict_returns_409_and_writes_nothing(client, db_path, flight_id):
    first, second = free_seats(client, flight_id)[:2]
    take_seat(db_path, flight_id, first)
    before = count_rows(db_path)
    body = {
        "flight_id": flight_id,
        "cabin": "economy",
        "passengers": [
            {"first_name": "Ada", "last_name": "Lovelace",
             "date_of_birth": "1990-05-14", "seat": first},
            {"first_name": "Grace", "last_name": "Hopper",
             "date_of_birth": "1985-12-09", "seat": second},
        ],
        "email": "ada@example.com",
        "card": {"number": CARD, "expiry": EXPIRY, "cvc": "123"},
    }

    response = client.post("/api/bookings", json=body)

    assert response.status_code == 409
    assert count_rows(db_path) == before
    assert not seat_is_taken(db_path, flight_id, second)
