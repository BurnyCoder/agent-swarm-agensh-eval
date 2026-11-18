"""Flight search: input validation, matching, sorting and filtering.

Search works on the flights table and the taken_seats table. Seats left for a
cabin are the layout capacity minus the taken rows for that flight and cabin.
Flights without enough seats left for the party are not shown.

Money is always integer cents. Times are local ISO strings, so the calendar
day of a departure is the first ten characters of ``departs_local``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime

from . import aircraft, pricing

CABINS = ("economy", "business")
SORTS = ("price", "departure", "duration")
MAX_PASSENGERS = 9

SORT_KEYS: dict[str, Callable[[dict], tuple]] = {
    "price": lambda f: (f["total_cents"], f["departs_local"]),
    "departure": lambda f: (f["departs_local"], f["total_cents"]),
    "duration": lambda f: (f["duration_minutes"], f["total_cents"]),
}


@dataclass(frozen=True)
class FlightQuery:
    origin: str
    destination: str
    day: date
    passengers: int = 1
    cabin: str = "economy"
    sort: str = "price"
    max_stops: int | None = None
    airline: str | None = None


@dataclass(frozen=True)
class SearchPlan:
    outbound: FlightQuery
    inbound: FlightQuery | None = None


def parse_day(text: str | None) -> date | None:
    """Parse YYYY-MM-DD, or return None for empty or malformed input."""
    if not text or not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def parse_party(passengers_text: str | None, cabin_text: str | None) -> tuple[int, str, dict[str, str]]:
    """Check passenger count and cabin. Return (passengers, cabin, errors)."""
    errors: dict[str, str] = {}
    passengers = 1
    raw = (passengers_text or "").strip() or "1"
    try:
        passengers = int(raw)
    except ValueError:
        errors["passengers"] = "Passengers must be a whole number."
    else:
        if not 1 <= passengers <= MAX_PASSENGERS:
            errors["passengers"] = f"Passengers must be between 1 and {MAX_PASSENGERS}."

    cabin = (cabin_text or "").strip().lower() or "economy"
    if cabin not in CABINS:
        errors["cabin"] = "Cabin must be economy or business."
    return passengers, cabin, errors


def known_airport_codes(conn: sqlite3.Connection) -> set[str]:
    return {row["code"] for row in conn.execute("SELECT code FROM airports")}


def validate_query(
    origin: str | None,
    destination: str | None,
    day_text: str | None,
    passengers_text: str | None,
    cabin: str | None,
    *,
    known_airports: set[str],
    today: date,
    sort: str | None = None,
    max_stops_text: str | None = None,
    airline: str | None = None,
) -> tuple[FlightQuery | None, dict[str, str]]:
    """Check raw search input. Return (query, errors); query is None when errors exist."""
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()
    errors: dict[str, str] = {}

    if not origin:
        errors["origin"] = "Choose a departure airport."
    elif origin not in known_airports:
        errors["origin"] = f"Unknown airport code {origin}."
    if not destination:
        errors["destination"] = "Choose a destination airport."
    elif destination not in known_airports:
        errors["destination"] = f"Unknown airport code {destination}."
    if origin and destination and origin == destination and "origin" not in errors:
        errors["destination"] = "Destination must be different from the departure airport."

    day: date | None = None
    if not day_text:
        errors["date"] = "Choose a departure date."
    else:
        day = parse_day(day_text)
        if day is None:
            errors["date"] = "Use the date format YYYY-MM-DD."
        elif day < today:
            errors["date"] = "The departure date cannot be in the past."

    passengers, cabin_name, party_errors = parse_party(passengers_text, cabin)
    errors.update(party_errors)

    sort_name = (sort or "").strip().lower() or "price"
    if sort_name not in SORTS:
        errors["sort"] = "Sort by price, departure or duration."

    max_stops: int | None = None
    if max_stops_text not in (None, ""):
        try:
            max_stops = int(max_stops_text)
        except ValueError:
            errors["max_stops"] = "Max stops must be a whole number."
        else:
            if max_stops < 0:
                errors["max_stops"] = "Max stops cannot be negative."

    if errors or day is None:
        return None, errors
    return (
        FlightQuery(
            origin=origin,
            destination=destination,
            day=day,
            passengers=passengers,
            cabin=cabin_name,
            sort=sort_name,
            max_stops=max_stops,
            airline=(airline or "").strip().upper() or None,
        ),
        {},
    )


def plan_search(
    conn: sqlite3.Connection, params: Mapping[str, str | None], today: date
) -> tuple[SearchPlan | None, dict[str, str]]:
    """Validate the search form, including the optional return date (round trip)."""
    query, errors = validate_query(
        params.get("origin"),
        params.get("destination"),
        params.get("date"),
        params.get("passengers"),
        params.get("cabin"),
        known_airports=known_airport_codes(conn),
        today=today,
        sort=params.get("sort"),
        max_stops_text=params.get("max_stops"),
        airline=params.get("airline"),
    )

    inbound: FlightQuery | None = None
    return_text = (params.get("return_date") or "").strip()
    if return_text:
        return_day = parse_day(return_text)
        depart = parse_day(params.get("date"))
        if return_day is None:
            errors["return_date"] = "Use the date format YYYY-MM-DD."
        elif return_day < today:
            errors["return_date"] = "The return date cannot be in the past."
        elif depart is not None and return_day < depart:
            errors["return_date"] = "The return date cannot be before the departure date."
        elif query is not None:
            inbound = replace(
                query,
                origin=query.destination,
                destination=query.origin,
                day=return_day,
            )

    if query is None or errors:
        return None, errors
    return SearchPlan(outbound=query, inbound=inbound), {}


def _seats_taken(conn: sqlite3.Connection, flight_ids: list[int]) -> dict[tuple[int, str], int]:
    if not flight_ids:
        return {}
    marks = ",".join("?" for _ in flight_ids)
    rows = conn.execute(
        f"SELECT flight_id, cabin, COUNT(*) AS taken FROM taken_seats"
        f" WHERE flight_id IN ({marks}) GROUP BY flight_id, cabin",
        flight_ids,
    ).fetchall()
    return {(row["flight_id"], row["cabin"]): row["taken"] for row in rows}


def seats_left(row: sqlite3.Row, taken: dict[tuple[int, str], int], cabin: str) -> int:
    capacity = aircraft.get_aircraft(row["aircraft"]).capacity(cabin)
    return capacity - taken.get((row["id"], cabin), 0)


def _flight_dict(
    row: sqlite3.Row, taken: dict[tuple[int, str], int], cabin: str, passengers: int
) -> dict:
    fare = row[f"{cabin}_fare_cents"]
    return {
        "id": row["id"],
        "flight_number": row["flight_number"],
        "airline_code": row["airline_code"],
        "airline_name": row["airline_name"],
        "origin": row["origin"],
        "destination": row["destination"],
        "departs_local": row["departs_local"],
        "arrives_local": row["arrives_local"],
        "duration_minutes": row["duration_minutes"],
        "stops": row["stops"],
        "aircraft": row["aircraft"],
        "cabin": cabin,
        "passengers": passengers,
        "fare_per_passenger_cents": fare,
        # Tax-inclusive, the same total the flight page and checkout show.
        "total_cents": pricing.fare_breakdown(fare, passengers)["total_cents"],
        "seats_left": seats_left(row, taken, cabin),
    }


def filter_flights(
    flights: list[dict], max_stops: int | None = None, airline: str | None = None
) -> list[dict]:
    """Keep flights with at most max_stops stops and, if given, the airline code."""
    return [
        f
        for f in flights
        if (max_stops is None or f["stops"] <= max_stops)
        and (not airline or f["airline_code"] == airline)
    ]


def search_flights(conn: sqlite3.Connection, query: FlightQuery) -> list[dict]:
    """Return flights on the route and day that can seat the party, filtered and sorted."""
    rows = conn.execute(
        "SELECT * FROM flights WHERE origin = ? AND destination = ?"
        " AND substr(departs_local, 1, 10) = ?",
        (query.origin, query.destination, query.day.isoformat()),
    ).fetchall()
    taken = _seats_taken(conn, [row["id"] for row in rows])

    flights = [_flight_dict(row, taken, query.cabin, query.passengers) for row in rows]
    flights = [f for f in flights if f["seats_left"] >= query.passengers]
    flights = filter_flights(flights, query.max_stops, query.airline)
    flights.sort(key=SORT_KEYS[query.sort])
    return flights


def search_leg(conn: sqlite3.Connection, query: FlightQuery) -> dict:
    """One leg of a search for the results page.

    ``total`` counts flights before the stop and airline filters, so the page can
    tell "no flights on this day" apart from "filters hid every flight".
    ``airlines`` lists the airlines on the route and day, for the filter menu.
    """
    base = search_flights(conn, replace(query, max_stops=None, airline=None))
    return {
        "query": query,
        "flights": filter_flights(base, query.max_stops, query.airline),
        "airlines": airlines_for(base),
        "total": len(base),
    }


def flight_detail(
    conn: sqlite3.Connection, flight_id: int, passengers: int = 1, cabin: str = "economy"
) -> dict | None:
    """Return one flight with seats left in both cabins, or None if it does not exist."""
    row = conn.execute("SELECT * FROM flights WHERE id = ?", (flight_id,)).fetchone()
    if row is None:
        return None
    taken = _seats_taken(conn, [flight_id])
    detail = _flight_dict(row, taken, cabin, passengers)
    detail["seats_left_by_cabin"] = {name: seats_left(row, taken, name) for name in CABINS}
    detail["fares_cents"] = {name: row[f"{name}_fare_cents"] for name in CABINS}
    detail["breakdown"] = pricing.fare_breakdown(detail["fare_per_passenger_cents"], passengers)
    return detail


def airlines_for(flights: list[dict]) -> list[tuple[str, str]]:
    """Distinct (code, name) airline pairs in a result list, sorted by name."""
    return sorted({(f["airline_code"], f["airline_name"]) for f in flights}, key=lambda p: p[1])


def format_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def format_clock(iso_local: str) -> str:
    return datetime.fromisoformat(iso_local).strftime("%H:%M")


def format_day(iso_local: str) -> str:
    return datetime.fromisoformat(iso_local).strftime("%a %d %b %Y")


def format_cents(cents: int) -> str:
    """Format integer cents as dollars without using floats."""
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"
