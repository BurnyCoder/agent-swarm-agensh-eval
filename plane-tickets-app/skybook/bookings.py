"""Booking lookup, the booking view model and cancellation.

A booking is found by its reference plus the lead passenger's last name. The
reference alone is not enough, so a guessed reference reveals nothing. The lead
passenger is the lowest ``passengers.id`` on the booking.

Cancelling sets the status to ``cancelled`` and deletes the booking's
``taken_seats`` rows, which releases the seats. The passenger rows keep their
seat so the record still shows what was booked. Cancelling is idempotent. Once
the flight's departure date has passed, a booking can no longer be cancelled.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

REFERENCE_PATTERN = re.compile(r"^[A-Z0-9]{6}$")


def normalise_reference(text: str | None) -> str:
    return (text or "").strip().upper()


def normalise_last_name(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def find_booking(conn: sqlite3.Connection, reference: str, last_name: str) -> sqlite3.Row | None:
    """Return the booking row when the reference and lead last name both match."""
    reference = normalise_reference(reference)
    wanted = normalise_last_name(last_name)
    if not REFERENCE_PATTERN.match(reference) or not wanted:
        return None
    row = conn.execute("SELECT * FROM bookings WHERE reference = ?", (reference,)).fetchone()
    if row is None:
        return None
    lead = conn.execute(
        "SELECT last_name FROM passengers WHERE booking_id = ? ORDER BY id LIMIT 1", (row["id"],)
    ).fetchone()
    if lead is None or normalise_last_name(lead["last_name"]) != wanted:
        return None
    return row


def booking_details(conn: sqlite3.Connection, booking: sqlite3.Row) -> dict:
    """Everything the confirmation, manage and API views show for one booking."""
    flight = conn.execute("SELECT * FROM flights WHERE id = ?", (booking["flight_id"],)).fetchone()
    passengers = conn.execute(
        "SELECT first_name, last_name, date_of_birth, seat, email FROM passengers"
        " WHERE booking_id = ? ORDER BY id",
        (booking["id"],),
    ).fetchall()
    return {
        "reference": booking["reference"],
        "status": booking["status"],
        "cabin": booking["cabin"],
        "passenger_count": booking["passengers"],
        "total_cents": booking["total_cents"],
        "lead_email": booking["lead_email"],
        "created_at": booking["created_at"],
        "can_cancel": booking["status"] == "confirmed" and flight_open(flight),
        "flight": {
            "id": flight["id"],
            "flight_number": flight["flight_number"],
            "airline_name": flight["airline_name"],
            "origin": flight["origin"],
            "destination": flight["destination"],
            "departs_local": flight["departs_local"],
            "arrives_local": flight["arrives_local"],
            "duration_minutes": flight["duration_minutes"],
            "stops": flight["stops"],
            "aircraft": flight["aircraft"],
        },
        "passengers": [dict(row) for row in passengers],
        "seats": [row["seat"] for row in passengers],
    }


def flight_open(flight: sqlite3.Row, today: date | None = None) -> bool:
    """A flight is open for cancellation until its departure date has passed."""
    departs = date.fromisoformat(flight["departs_local"][:10])
    return departs >= (today or date.today())


class CancelError(Exception):
    """The booking cannot be cancelled. The message says why."""


def cancel_booking(conn: sqlite3.Connection, booking: sqlite3.Row, today: date | None = None) -> None:
    """Cancel a confirmed booking and release its seats. Already cancelled is a no-op."""
    if booking["status"] == "cancelled":
        return
    flight = conn.execute("SELECT * FROM flights WHERE id = ?", (booking["flight_id"],)).fetchone()
    if not flight_open(flight, today):
        raise CancelError("This flight has already departed, so the booking can no longer be cancelled.")
    with conn:
        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking["id"],))
        conn.execute("DELETE FROM taken_seats WHERE booking_id = ?", (booking["id"],))


def refresh(conn: sqlite3.Connection, booking: sqlite3.Row) -> sqlite3.Row:
    """Reload a booking row after it has changed."""
    return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking["id"],)).fetchone()
