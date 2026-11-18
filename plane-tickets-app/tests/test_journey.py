"""End-to-end journey over HTTP: search, results, flight page, seat choice.

Each step goes through the same routes a browser uses (HTML pages and the JSON
API), with FastAPI's test client. The route and date come from the seeded data,
so the test follows whatever the seed holds for the next 30 days.

Every flight-page request builds its full query string, so `passengers` and
`cabin` are never dropped (httpx replaces a URL's query when `params=` is given).

The booking steps (passengers, payment, confirmation) run the same way, through
the pages a browser posts to and the booking API that reads the result back.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from html import unescape
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from fastapi.testclient import TestClient

from skybook.app import create_app

PASSENGERS = 2
CABIN = "economy"
SEAT_INPUT = re.compile(r'<input type="checkbox" name="seat" value="([^"]+)"([^>]*)>')


# --- helpers and fixtures ----------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "skybook.db"


@pytest.fixture()
def client(db_path):
    return TestClient(create_app(db_path))


def search_params(route):
    return {
        "origin": route["origin"],
        "destination": route["destination"],
        "date": route["day"],
        "passengers": str(PASSENGERS),
        "cabin": CABIN,
    }


def flight_path(flight_id, seats=None):
    """The flight page URL. With `seats` given, it is a seat-form submission."""
    pairs = [("passengers", str(PASSENGERS)), ("cabin", CABIN)]
    if seats is not None:
        pairs.append(("picked", "1"))
        pairs.extend(("seat", s) for s in seats)
    return f"/flights/{flight_id}?" + urlencode(pairs)


def free_seats(html):
    """Seat IDs the page offers (checkboxes without the disabled attribute)."""
    return [unescape(v) for v, attrs in SEAT_INPUT.findall(html) if "disabled" not in attrs]


def taken_seat_ids(html):
    return [unescape(v) for v, attrs in SEAT_INPUT.findall(html) if "disabled" in attrs]


def insert_taken_seat(db_path, flight_id, seat, cabin=CABIN):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id) VALUES (?, ?, ?, NULL)",
        (flight_id, seat, cabin),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def route(client, db_path):
    """A seeded origin, destination and date with at least one bookable flight."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    candidates = conn.execute(
        "SELECT DISTINCT origin, destination, substr(departs_local, 1, 10) AS day"
        " FROM flights WHERE substr(departs_local, 1, 10) >= ?"
        " ORDER BY day, origin, destination",
        (date.today().isoformat(),),
    ).fetchall()
    conn.close()
    for row in candidates:
        found = client.get(
            "/api/flights",
            params={
                "origin": row["origin"],
                "destination": row["destination"],
                "date": row["day"],
                "passengers": str(PASSENGERS),
                "cabin": CABIN,
            },
        )
        if found.status_code == 200 and found.json():
            return {"origin": row["origin"], "destination": row["destination"], "day": row["day"]}
    pytest.fail("the seed holds no bookable flight for the next 30 days")


@pytest.fixture()
def flight(client, route):
    """The first flight the search returns for the route, as the API reports it."""
    return client.get("/api/flights", params=search_params(route)).json()[0]


# --- journey steps -----------------------------------------------------------


def test_home_form_leads_to_results_page(client, route):
    home = client.get("/")
    assert home.status_code == 200
    assert 'action="/search"' in home.text

    response = client.get("/search", params=search_params(route))

    assert response.status_code == 200
    assert "Select" in response.text
    assert route["origin"] in response.text
    assert route["destination"] in response.text


def test_result_link_opens_flight_page_with_fares_and_seats(client, route, flight):
    results = client.get("/search", params=search_params(route))
    assert f"/flights/{flight['id']}?passengers={PASSENGERS}&amp;cabin={CABIN}" in results.text

    page = client.get(flight_path(flight["id"]))

    assert page.status_code == 200
    assert "Confirm seats" in page.text
    assert "fare-total" in page.text
    assert free_seats(page.text), "the seat map must offer free seats"


def test_chosen_seats_confirm_once_each_passenger_has_one(client, flight):
    seats = free_seats(client.get(flight_path(flight["id"])).text)[:PASSENGERS]
    assert len(seats) == PASSENGERS

    response = client.get(flight_path(flight["id"], seats=seats))

    assert response.status_code == 200
    assert "Seats selected:" in response.text
    assert ", ".join(seats) in response.text


def test_wrong_seat_count_is_rejected(client, flight):
    seat = free_seats(client.get(flight_path(flight["id"])).text)[0]

    response = client.get(flight_path(flight["id"], seats=[seat]))

    assert response.status_code == 200
    assert "Seats selected:" not in response.text
    assert 'role="alert"' in response.text
    assert f"Choose exactly {PASSENGERS} seats" in response.text


def test_taken_seat_cannot_be_chosen(client, db_path, flight):
    taken, other = free_seats(client.get(flight_path(flight["id"])).text)[:2]
    insert_taken_seat(db_path, flight["id"], taken)

    response = client.get(flight_path(flight["id"], seats=[taken, other]))

    assert response.status_code == 200
    assert "Seats selected:" not in response.text
    assert taken in taken_seat_ids(response.text)
    assert f"Seat {taken} is already taken." in response.text


def test_search_and_flight_api_agree_on_seats_left_and_base_fare(client, route, flight):
    detail = client.get(
        f"/api/flights/{flight['id']}",
        params={"passengers": str(PASSENGERS), "cabin": CABIN},
    ).json()

    assert flight["seats_left"] == detail["seats_left_by_cabin"][CABIN]
    assert flight["fare_per_passenger_cents"] == detail["fares_cents"][CABIN]
    # Results show the tax-inclusive party total, the same as the flight page (#24).
    assert flight["total_cents"] == detail["breakdown"]["total_cents"]


def test_taken_seat_reduces_seats_left_everywhere(client, db_path, route, flight):
    before_left = flight["seats_left"]
    seat = free_seats(client.get(flight_path(flight["id"])).text)[0]
    insert_taken_seat(db_path, flight["id"], seat)

    after = client.get("/api/flights", params=search_params(route)).json()
    after_left = next(f["seats_left"] for f in after if f["id"] == flight["id"])
    detail = client.get(
        f"/api/flights/{flight['id']}",
        params={"passengers": str(PASSENGERS), "cabin": CABIN},
    ).json()
    page = client.get(flight_path(flight["id"]))

    assert after_left == before_left - 1
    assert detail["seats_left_by_cabin"][CABIN] == after_left
    assert seat in taken_seat_ids(page.text)


# --- booking steps -----------------------------------------------------------

TEST_CARD = "4242 4242 4242 4242"
EXPIRY = f"12/{(date.today().year + 3) % 100:02d}"
PEOPLE = [("Ada", "Lovelace", "1990-05-14"), ("Grace", "Hopper", "1985-12-09")]
LEAD_LAST_NAME = PEOPLE[0][1]


def book_path(flight_id, seats):
    """The passenger page URL the flight page's "Continue to passengers" builds."""
    pairs = [("passengers", str(PASSENGERS)), ("cabin", CABIN)]
    pairs.extend(("seat", s) for s in seats)
    return f"/book/{flight_id}?" + urlencode(pairs)


def passenger_data(seats):
    """Form fields the passenger page posts for the party (lists are repeated keys)."""
    return {
        "passengers": str(PASSENGERS),
        "cabin": CABIN,
        "seat": seats,
        "first_name": [first for first, _, _ in PEOPLE],
        "last_name": [last for _, last, _ in PEOPLE],
        "date_of_birth": [born for _, _, born in PEOPLE],
        "email": "ada@example.com",
    }


def payment_data(seats, card_number=TEST_CARD):
    return {
        **passenger_data(seats),
        "card_number": card_number,
        "expiry": EXPIRY,
        "cvc": "123",
    }


def test_passenger_page_then_payment_step_keep_the_chosen_seats(client, flight):
    seats = free_seats(client.get(flight_path(flight["id"])).text)[:PASSENGERS]

    passengers = client.get(book_path(flight["id"], seats))
    assert passengers.status_code == 200
    assert 'id="passenger-form"' in passengers.text
    assert all(seat in passengers.text for seat in seats)

    step = client.post(f"/book/{flight['id']}/passengers", data=passenger_data(seats))

    assert step.status_code == 200
    assert 'id="payment-form"' in step.text
    assert all(seat in step.text for seat in seats)


def test_test_card_books_the_seats_and_opens_confirmation(client, route, flight):
    seats = free_seats(client.get(flight_path(flight["id"])).text)[:PASSENGERS]
    seats_left_before = flight["seats_left"]

    paid = client.post(
        f"/book/{flight['id']}/pay", data=payment_data(seats), follow_redirects=False
    )

    assert paid.status_code == 303
    location = urlparse(paid.headers["location"])
    reference = location.path.rsplit("/", 1)[-1]
    assert re.fullmatch(r"[A-Z0-9]{6}", reference)
    assert parse_qs(location.query)["last_name"] == [LEAD_LAST_NAME]

    confirmation = client.get(paid.headers["location"])
    assert confirmation.status_code == 200
    assert reference in confirmation.text
    assert LEAD_LAST_NAME in confirmation.text

    booking = client.get(
        f"/api/bookings/{reference}", params={"last_name": LEAD_LAST_NAME}
    )
    assert booking.status_code == 200
    assert booking.json()["reference"] == reference

    after = client.get("/api/flights", params=search_params(route)).json()
    after_left = next(f["seats_left"] for f in after if f["id"] == flight["id"])
    page = client.get(flight_path(flight["id"]))
    assert after_left == seats_left_before - PASSENGERS
    assert set(seats) <= set(taken_seat_ids(page.text))


def test_declined_card_books_nothing_and_keeps_the_seats_free(client, flight):
    seats = free_seats(client.get(flight_path(flight["id"])).text)[:PASSENGERS]

    declined = client.post(
        f"/book/{flight['id']}/pay",
        data=payment_data(seats, card_number="4000 0000 0000 0002"),
        follow_redirects=False,
    )

    assert declined.status_code == 422
    page = client.get(flight_path(flight["id"]))
    assert not set(seats) & set(taken_seat_ids(page.text))
    assert set(seats) <= set(free_seats(page.text))
