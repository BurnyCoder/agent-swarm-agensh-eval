"""Checkout rules: passenger details, booking references and the booking write.

``create_booking`` is the only code that writes a booking. It runs in one
``BEGIN IMMEDIATE`` transaction, so two people cannot sell the same seat. Card
details never reach this module: the payment step checks them with
``payment.validate_card`` and drops them.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import string
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from . import pricing, seats

CABINS = ("economy", "business")
REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
REFERENCE_LENGTH = 6
MAX_REFERENCE_ATTEMPTS = 10
MAX_PASSENGERS = 9
MAX_AGE_YEARS = 120
MAX_NAME_LENGTH = 50
MAX_EMAIL_LENGTH = 254

# Letters with single spaces, hyphens, apostrophes or dots between them.
_NAME_RE = re.compile(r"[^\W\d_]+(?:[ '\-.][^\W\d_]+)*")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class BookingError(Exception):
    """A booking cannot be made. ``status`` is the HTTP status for the JSON API."""

    def __init__(self, message: str, status: int = 409, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details or [message]


def _text(value: Any) -> str:
    """Form and JSON values as a stripped string; anything else is empty."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def validate_name(value: str, label: str) -> str | None:
    """Return an error for a first or last name, or None if it is fine."""
    if not value:
        return f"Enter the {label}."
    if len(value) > MAX_NAME_LENGTH:
        return f"The {label} is longer than {MAX_NAME_LENGTH} characters."
    if not _NAME_RE.fullmatch(value):
        return f"The {label} may contain letters, spaces, hyphens, apostrophes and dots only."
    return None


def validate_birth_date(value: str, today: date) -> str | None:
    """Return an error for a date of birth given as YYYY-MM-DD, or None."""
    if not value:
        return "Enter the date of birth."
    try:
        born = date.fromisoformat(value)
    except ValueError:
        return "Date of birth must be a real date."
    if born > today:
        return "Date of birth cannot be in the future."
    if born.year < today.year - MAX_AGE_YEARS:
        return "Date of birth is too far in the past."
    return None


def validate_email(value: str) -> str | None:
    if not value:
        return "Enter an email address for the lead passenger."
    if len(value) > MAX_EMAIL_LENGTH or not _EMAIL_RE.fullmatch(value):
        return "Enter a valid email address, for example name@example.com."
    return None


def validate_passengers(
    rows: Sequence[Mapping[str, Any]], today: date
) -> tuple[list[dict], dict[str, str]]:
    """Check passenger details. Return cleaned rows and errors keyed by form field.

    Each row has ``first_name``, ``last_name``, ``date_of_birth`` (YYYY-MM-DD)
    and, for the lead passenger only, ``email``. Errors use field names with the
    passenger index appended, such as ``first_name_0``, so the form can show
    each message next to its input. ``email`` has no index: it belongs to the
    lead passenger.
    """
    cleaned: list[dict] = []
    errors: dict[str, str] = {}
    if not 1 <= len(rows) <= MAX_PASSENGERS:
        errors["passengers"] = f"Book between 1 and {MAX_PASSENGERS} passengers."
        return cleaned, errors

    for index, row in enumerate(rows):
        first = _text(row.get("first_name"))
        last = _text(row.get("last_name"))
        born = _text(row.get("date_of_birth"))
        checks = {
            f"first_name_{index}": validate_name(first, "first name"),
            f"last_name_{index}": validate_name(last, "last name"),
            f"date_of_birth_{index}": validate_birth_date(born, today),
        }
        for field, message in checks.items():
            if message:
                errors[field] = message
        cleaned.append({"first_name": first, "last_name": last, "date_of_birth": born})

    lead_email = _text(rows[0].get("email")) if rows else ""
    email_error = validate_email(lead_email)
    if email_error:
        errors["email"] = email_error
    if cleaned:
        cleaned[0]["email"] = lead_email
    return cleaned, errors


def generate_reference() -> str:
    """Six random uppercase letters or digits, as printed on the confirmation."""
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_booking(conn: sqlite3.Connection, values: tuple[Any, ...]) -> str:
    """Insert the booking with a fresh reference, retrying on a rare collision."""
    for _ in range(MAX_REFERENCE_ATTEMPTS):
        reference = generate_reference()
        try:
            conn.execute(
                "INSERT INTO bookings (reference, flight_id, cabin, passengers, status,"
                " lead_email, total_cents, created_at) VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?)",
                (reference, *values),
            )
            return reference
        except sqlite3.IntegrityError as exc:
            if "bookings.reference" not in str(exc):
                raise
    raise BookingError("Could not allocate a booking reference. Please try again.", status=503)


def create_booking(
    conn: sqlite3.Connection,
    flight_id: int,
    cabin: str,
    passengers: list[dict],
    seat_choice: list[str],
    today: date | None = None,
) -> dict:
    """Write a confirmed booking: booking, passengers and seats, all or nothing.

    ``passengers`` must already be cleaned by ``validate_passengers``; the lead
    passenger's email is read from ``passengers[0]["email"]``. Seat ``i`` goes
    to passenger ``i``. Raises ``BookingError`` with the HTTP status to use.
    """
    today = today or date.today()
    if cabin not in CABINS:
        raise BookingError("Choose economy or business.", status=422)
    if not 1 <= len(passengers) <= MAX_PASSENGERS:
        raise BookingError(f"Book between 1 and {MAX_PASSENGERS} passengers.", status=422)
    lead_email = passengers[0].get("email", "")
    if validate_email(lead_email):
        raise BookingError("Enter a valid email address for the lead passenger.", status=422)

    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        flight = conn.execute("SELECT * FROM flights WHERE id = ?", (flight_id,)).fetchone()
        if flight is None:
            raise BookingError("That flight does not exist.", status=404)
        if flight["departs_local"][:10] < today.isoformat():
            raise BookingError("This flight has already left.")

        chosen = [seats.normalise_seat(seat) for seat in seat_choice if seat.strip()]
        valid = seats.cabin_seats(flight["aircraft"], cabin)
        taken = seats.taken_seats(conn, flight_id)
        messages = seats.validate_selection(chosen, len(passengers), valid, taken)
        if messages:
            raise BookingError(messages[0], status=409, details=messages)

        fare = flight[f"{cabin}_fare_cents"]
        breakdown = pricing.fare_breakdown(fare, len(passengers))
        total = breakdown["total_cents"]
        created_at = _utc_now()
        reference = _insert_booking(
            conn, (flight_id, cabin, len(passengers), lead_email, total, created_at)
        )
        booking_id = conn.execute(
            "SELECT id FROM bookings WHERE reference = ?", (reference,)
        ).fetchone()["id"]

        for index, person in enumerate(passengers):
            conn.execute(
                "INSERT INTO passengers (booking_id, first_name, last_name, date_of_birth,"
                " seat, email) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    booking_id,
                    person["first_name"],
                    person["last_name"],
                    person["date_of_birth"],
                    chosen[index],
                    lead_email if index == 0 else None,
                ),
            )
            conn.execute(
                "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id)"
                " VALUES (?, ?, ?, ?)",
                (flight_id, chosen[index], cabin, booking_id),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "taken_seats" in str(exc):
            raise BookingError("Another customer took one of these seats. Choose again.") from exc
        raise
    except BaseException:
        conn.rollback()
        raise

    return {
        "reference": reference,
        "flight_id": flight_id,
        "cabin": cabin,
        "status": "confirmed",
        "lead_email": lead_email,
        "total_cents": total,
        "breakdown": breakdown,
        "created_at": created_at,
        "seats": chosen,
        "passengers": [
            {**person, "seat": chosen[index]} for index, person in enumerate(passengers)
        ],
    }
