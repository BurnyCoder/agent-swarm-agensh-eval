"""Seat selection rules: which seats are taken, and whether a chosen set is valid.

A seat is one row number plus one letter, such as ``12C``. Taken seats are read
from ``taken_seats``; any seat with no row is free. The rules here are used by
the flight page now and by checkout later, so they live in one place.
"""

from __future__ import annotations

import sqlite3

from . import aircraft


def taken_seats(conn: sqlite3.Connection, flight_id: int) -> set[str]:
    rows = conn.execute("SELECT seat FROM taken_seats WHERE flight_id = ?", (flight_id,))
    return {row["seat"] for row in rows}


def cabin_seats(aircraft_code: str, cabin: str) -> set[str]:
    """Every seat ID in one cabin of an aircraft."""
    return set(aircraft.get_aircraft(aircraft_code).cabin(cabin).seats())


def cabin_seat_map(aircraft_code: str, cabin: str, taken: set[str]) -> list[dict]:
    """Seat map rows for one cabin only."""
    return [row for row in aircraft.seat_map(aircraft_code, taken) if row["cabin"] == cabin]


def normalise_seat(text: str) -> str:
    return text.strip().upper()


def validate_selection(
    selected: list[str], passengers: int, valid: set[str], taken: set[str]
) -> list[str]:
    """Check a submitted seat choice. Return messages; an empty list means valid.

    The choice must hold exactly one seat per passenger, with no repeats, every
    seat must exist in the cabin, and none may be taken.
    """
    seats = [normalise_seat(seat) for seat in selected if seat.strip()]
    if len(seats) != passengers:
        noun = "seat" if passengers == 1 else "seats"
        return [f"Choose exactly {passengers} {noun}: one per passenger. You chose {len(seats)}."]

    messages: list[str] = []
    seen: set[str] = set()
    for seat in seats:
        if seat in seen:
            messages.append(f"Seat {seat} is listed more than once.")
        elif seat not in valid:
            messages.append(f"Seat {seat} does not exist in this cabin.")
        elif seat in taken:
            messages.append(f"Seat {seat} is already taken.")
        seen.add(seat)
    return messages
