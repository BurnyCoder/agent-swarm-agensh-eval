"""Checkout tests: passenger validation, booking writes, the mock payment step,
seat conflicts and the booking JSON API.

Flights are inserted 45 days out so the seed data does not change these tests.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from skybook import checkout, db, seats
from skybook.app import create_app

TODAY = date.today()
FAR_DAY = TODAY + timedelta(days=45)
GOOD_CARD = "4242 4242 4242 4242"
FUTURE_EXPIRY = f"12/{(TODAY.year + 3) % 100:02d}"
REFERENCE_RE = re.compile(r"^[A-Z0-9]{6}$")


# --- helpers and fixtures ----------------------------------------------------


def insert_flight(conn, origin, destination, *, number="SB950", economy=20000, business=60000):
    start = datetime.fromisoformat(f"{FAR_DAY.isoformat()}T09:00")
    end = start + timedelta(minutes=150)
    cursor = conn.execute(
        "INSERT INTO flights (flight_number, airline_code, airline_name, origin, destination,"
        " departs_local, arrives_local, duration_minutes, stops, aircraft,"
        " economy_fare_cents, business_fare_cents)"
        " VALUES (?, 'SB', 'Skybook Air', ?, ?, ?, ?, 150, 0, 'A320', ?, ?)",
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


def person(first="Ada", last="Lovelace", born="1990-05-01", seat=None):
    row = {"first_name": first, "last_name": last, "date_of_birth": born}
    if seat is not None:
        row["seat"] = seat
    return row


def api_body(flight_id, seats_list, *, card=None, passengers=None, email="ada@example.com"):
    if passengers is None:
        passengers = [person(seat=seat) for seat in seats_list]
    return {
        "flight_id": flight_id,
        "cabin": "economy",
        "passengers": passengers,
        "email": email,
        "card": card or {"number": GOOD_CARD, "expiry": FUTURE_EXPIRY, "cvc": "123"},
    }


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


@pytest.fixture()
def economy_seats():
    return sorted(seats.cabin_seats("A320", "economy"))


# --- passenger validation (pure) ---------------------------------------------


def test_valid_passengers_are_cleaned_and_only_lead_keeps_email():
    rows = [
        {"first_name": "  Ada ", "last_name": "Lovelace", "date_of_birth": "1990-05-01",
         "email": " ada@example.com "},
        {"first_name": "Charles", "last_name": "Babbage", "date_of_birth": "1960-12-26"},
    ]
    cleaned, errors = checkout.validate_passengers(rows, TODAY)
    assert errors == {}
    assert cleaned[0]["first_name"] == "Ada"
    assert cleaned[0]["email"] == "ada@example.com"
    assert "email" not in cleaned[1]


def test_missing_names_and_birth_date_are_reported_per_passenger():
    rows = [
        {"first_name": "", "last_name": "Lovelace", "date_of_birth": "1990-05-01",
         "email": "ada@example.com"},
        {"first_name": "Charles", "last_name": "", "date_of_birth": ""},
    ]
    _, errors = checkout.validate_passengers(rows, TODAY)
    assert set(errors) == {"first_name_0", "last_name_1", "date_of_birth_1"}


@pytest.mark.parametrize("name", ["R2D2", "<script>", "Ada99"])
def test_names_with_digits_or_markup_are_rejected(name):
    assert checkout.validate_name(name, "first name") is not None


@pytest.mark.parametrize("name", ["Mary-Jane", "O'Brien", "Jean Luc", "J.R"])
def test_names_with_punctuation_are_accepted(name):
    assert checkout.validate_name(name, "first name") is None


def test_name_longer_than_limit_is_rejected():
    assert checkout.validate_name("A" * (checkout.MAX_NAME_LENGTH + 1), "first name")


def test_birth_date_in_future_is_rejected():
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert checkout.validate_birth_date(tomorrow, TODAY) == "Date of birth cannot be in the future."


def test_birth_date_must_be_a_real_date():
    assert checkout.validate_birth_date("1990-02-30", TODAY) == "Date of birth must be a real date."


@pytest.mark.parametrize("email", ["", "ada", "ada@", "ada@example", "a b@example.com"])
def test_bad_lead_email_is_rejected(email):
    assert checkout.validate_email(email) is not None


def test_too_many_passengers_is_rejected():
    rows = [person() for _ in range(checkout.MAX_PASSENGERS + 1)]
    _, errors = checkout.validate_passengers(rows, TODAY)
    assert "passengers" in errors


# --- booking write -----------------------------------------------------------


def test_create_booking_writes_confirmed_booking_and_reference(conn, flight_id, economy_seats):
    passengers = [
        {"first_name": "Ada", "last_name": "Lovelace", "date_of_birth": "1990-05-01",
         "email": "ada@example.com"},
        {"first_name": "Charles", "last_name": "Babbage", "date_of_birth": "1960-12-26"},
    ]
    booking = checkout.create_booking(
        conn, flight_id, "economy", passengers, economy_seats[:2], TODAY
    )
    assert REFERENCE_RE.match(booking["reference"])
    assert booking["status"] == "confirmed"
    assert booking["total_cents"] == booking["breakdown"]["total_cents"]
    assert [p["seat"] for p in booking["passengers"]] == economy_seats[:2]

    row = conn.execute(
        "SELECT status, passengers, lead_email FROM bookings WHERE reference = ?",
        (booking["reference"],),
    ).fetchone()
    assert (row["status"], row["passengers"], row["lead_email"]) == (
        "confirmed", 2, "ada@example.com",
    )
    taken = seats.taken_seats(conn, flight_id)
    assert set(economy_seats[:2]) <= taken


def test_create_booking_rejects_seat_already_taken(conn, flight_id, economy_seats):
    passengers = [{"first_name": "Ada", "last_name": "Lovelace", "date_of_birth": "1990-05-01",
                   "email": "ada@example.com"}]
    checkout.create_booking(conn, flight_id, "economy", passengers, economy_seats[:1], TODAY)
    with pytest.raises(checkout.BookingError) as info:
        checkout.create_booking(conn, flight_id, "economy", passengers, economy_seats[:1], TODAY)
    assert info.value.status == 409
    assert info.value.details


def test_create_booking_rejects_departed_flight(conn, economy_seats):
    codes = [row["code"] for row in conn.execute("SELECT code FROM airports ORDER BY code")]
    flight_id = insert_flight(conn, codes[0], codes[1])
    passengers = [{"first_name": "Ada", "last_name": "Lovelace", "date_of_birth": "1990-05-01",
                   "email": "ada@example.com"}]
    with pytest.raises(checkout.BookingError):
        checkout.create_booking(
            conn, flight_id, "economy", passengers, economy_seats[:1], FAR_DAY + timedelta(days=1)
        )


def test_generated_references_are_six_uppercase_letters_or_digits():
    for _ in range(200):
        assert REFERENCE_RE.match(checkout.generate_reference())


# --- payment rejection through the API ---------------------------------------


@pytest.mark.parametrize(
    "card",
    [
        {"number": "4000 0000 0000 0002", "expiry": FUTURE_EXPIRY, "cvc": "123"},
        {"number": GOOD_CARD, "expiry": "01/20", "cvc": "123"},
        {"number": GOOD_CARD, "expiry": FUTURE_EXPIRY, "cvc": "12"},
    ],
)
def test_api_rejects_bad_card_with_422_and_stores_nothing(client, conn, flight_id, card,
                                                          economy_seats):
    response = client.post("/api/bookings", json=api_body(flight_id, economy_seats[:1],
                                                          card=card))
    assert response.status_code == 422
    assert response.json()["errors"]
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0


def test_api_rejection_never_echoes_card_number(client, flight_id, economy_seats):
    card = {"number": "4111 1111 1111 1111", "expiry": FUTURE_EXPIRY, "cvc": "123"}
    response = client.post("/api/bookings", json=api_body(flight_id, economy_seats[:1],
                                                          card=card))
    assert "4111" not in response.text


# --- JSON API ----------------------------------------------------------------


def test_api_creates_booking_with_201(client, flight_id, economy_seats):
    response = client.post("/api/bookings", json=api_body(flight_id, economy_seats[:2]))
    assert response.status_code == 201
    body = response.json()
    assert REFERENCE_RE.match(body["reference"])
    assert body["status"] == "confirmed"
    assert body["seats"] == economy_seats[:2]


def test_api_returns_422_for_missing_seat(client, flight_id):
    body = api_body(flight_id, [], passengers=[person()])
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 422
    assert "seat_0" in response.json()["errors"]


def test_api_returns_422_for_bad_cabin(client, flight_id, economy_seats):
    body = api_body(flight_id, economy_seats[:1])
    body["cabin"] = "first"
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 422
    assert "cabin" in response.json()["errors"]


def test_api_returns_422_for_bad_passenger_details(client, flight_id, economy_seats):
    body = api_body(flight_id, economy_seats[:1], passengers=[person(first="", seat=economy_seats[0])])
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 422
    assert "first_name_0" in response.json()["errors"]


def test_api_returns_409_for_taken_seat(client, flight_id, economy_seats):
    first = client.post("/api/bookings", json=api_body(flight_id, economy_seats[:1]))
    assert first.status_code == 201
    second = client.post("/api/bookings", json=api_body(flight_id, economy_seats[:1]))
    assert second.status_code == 409
    assert second.json()["details"]


def test_api_returns_404_for_unknown_flight(client, economy_seats):
    response = client.post("/api/bookings", json=api_body(999999, economy_seats[:1]))
    assert response.status_code == 404


def test_api_booking_is_visible_through_lookup(client, flight_id, economy_seats):
    reference = client.post(
        "/api/bookings", json=api_body(flight_id, economy_seats[:1])
    ).json()["reference"]
    response = client.get(f"/api/bookings/{reference}", params={"last_name": "Lovelace"})
    assert response.status_code == 200


# --- checkout pages ----------------------------------------------------------


def pass_form(flight_id, seats_list, **overrides):
    form = {
        "passengers": str(len(seats_list)),
        "cabin": "economy",
        "seat": seats_list,
        "first_name": ["Ada"] * len(seats_list),
        "last_name": ["Lovelace"] * len(seats_list),
        "date_of_birth": ["1990-05-01"] * len(seats_list),
        "email": "ada@example.com",
    }
    form.update(overrides)
    return form


def test_passenger_page_renders_one_fieldset_per_seat(client, flight_id, economy_seats):
    response = client.get(
        f"/book/{flight_id}",
        params={"passengers": "2", "cabin": "economy", "seat": economy_seats[:2]},
    )
    assert response.status_code == 200
    assert response.text.count('<fieldset class="passenger">') == 2


def test_passenger_page_redirects_back_when_seat_is_taken(client, conn, flight_id, economy_seats):
    taken = economy_seats[0]
    client.post("/api/bookings", json=api_body(flight_id, [taken]))
    response = client.get(
        f"/book/{flight_id}",
        params={"passengers": "1", "cabin": "economy", "seat": taken},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/flights/{flight_id}?")


def test_payment_step_shows_card_form_for_valid_passengers(client, flight_id, economy_seats):
    response = client.post(
        f"/book/{flight_id}/passengers", data=pass_form(flight_id, economy_seats[:1])
    )
    assert response.status_code == 200
    assert 'name="card_number"' in response.text


def test_payment_step_rerenders_passengers_with_errors(client, flight_id, economy_seats):
    form = pass_form(flight_id, economy_seats[:1], first_name=[""])
    response = client.post(f"/book/{flight_id}/passengers", data=form)
    assert response.status_code == 422
    assert "Enter the first name." in response.text


def test_pay_with_test_card_redirects_to_confirmation(client, conn, flight_id, economy_seats):
    form = pass_form(flight_id, economy_seats[:1])
    form.update(card_number=GOOD_CARD, expiry=FUTURE_EXPIRY, cvc="123")
    response = client.post(f"/book/{flight_id}/pay", data=form, follow_redirects=False)
    assert response.status_code == 303
    target = urlparse(response.headers["location"])
    assert re.fullmatch(r"/bookings/[A-Z0-9]{6}", target.path)
    query = parse_qs(target.query)
    assert query["last_name"] == ["Lovelace"]
    assert query["new"] == ["1"]
    reference = target.path.rsplit("/", 1)[1]
    assert conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE reference = ?", (reference,)
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "card_fields",
    [
        {"card_number": "4000 0000 0000 0002", "expiry": FUTURE_EXPIRY, "cvc": "123"},
        {"card_number": GOOD_CARD, "expiry": "01/20", "cvc": "123"},
        {"card_number": GOOD_CARD, "expiry": FUTURE_EXPIRY, "cvc": "1234"},
    ],
)
def test_pay_with_rejected_card_shows_error_and_writes_nothing(
    client, conn, flight_id, economy_seats, card_fields
):
    form = pass_form(flight_id, economy_seats[:1])
    form.update(card_fields)
    response = client.post(f"/book/{flight_id}/pay", data=form, follow_redirects=False)
    assert response.status_code == 422
    assert "field-error" in response.text
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0


def test_pay_on_taken_seat_redirects_to_flight_page(client, flight_id, economy_seats):
    client.post("/api/bookings", json=api_body(flight_id, economy_seats[:1]))
    form = pass_form(flight_id, economy_seats[:1])
    form.update(card_number=GOOD_CARD, expiry=FUTURE_EXPIRY, cvc="123")
    response = client.post(f"/book/{flight_id}/pay", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/flights/{flight_id}?")

