"""Checkout pages and the booking JSON API.

Pages:

* ``GET /book/{flight_id}`` shows the passenger form for the seats picked on the
  flight page.
* ``POST /book/{flight_id}/passengers`` checks that form and shows the payment step.
* ``POST /book/{flight_id}/pay`` checks the mock card, writes the booking and
  redirects to the confirmation page.

JSON: ``POST /api/bookings`` does the same in one request. It takes the same
passenger details plus a ``card`` object, so payment is never skipped.

Card numbers, expiry and CVC go through ``payment.validate_card`` and are never
echoed back into a page or stored. Endpoints are plain ``def`` because the
request connection from ``get_db`` is used on the worker thread that created it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Body, Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import checkout, payment, search, seats

PASSENGER_FIELDS = ("first_name", "last_name", "date_of_birth")


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _chosen(values: list[str] | None) -> list[str]:
    return [seats.normalise_seat(value) for value in values or [] if value.strip()]


def _people(
    first: list[str] | None,
    last: list[str] | None,
    born: list[str] | None,
    email: str,
    count: int,
) -> list[dict[str, str]]:
    """Passenger rows from the parallel form lists, padded to ``count``.

    Only the lead passenger (index 0) carries an ``email``.
    """
    columns = (first or [], last or [], born or [])
    rows = []
    for index in range(count):
        row = {
            name: column[index] if index < len(column) else ""
            for name, column in zip(PASSENGER_FIELDS, columns, strict=True)
        }
        if index == 0:
            row["email"] = email
        rows.append(row)
    return rows


def _flight_page_url(flight_id: int, passengers: int, cabin: str, chosen: list[str]) -> str:
    query = [("passengers", str(passengers)), ("cabin", cabin), ("picked", "1")]
    query += [("seat", seat) for seat in chosen]
    return f"/flights/{flight_id}?" + urlencode(query)


def _seat_problems(
    conn: sqlite3.Connection, flight: dict, cabin: str, chosen: list[str], passengers: int
) -> list[str]:
    valid = seats.cabin_seats(flight["aircraft"], cabin)
    taken = seats.taken_seats(conn, flight["id"])
    return seats.validate_selection(chosen, passengers, valid, taken)


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_db: Callable[..., Iterator[sqlite3.Connection]],
) -> None:
    """Attach the checkout pages and the booking API to ``app``."""

    def passenger_page(
        request: Request,
        flight: dict,
        passengers: int,
        cabin: str,
        chosen: list[str],
        people: list[dict],
        errors: dict[str, str],
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "checkout_passengers.html",
            {
                "flight": flight,
                "passengers": passengers,
                "cabin": cabin,
                "chosen": chosen,
                "people": people,
                "errors": errors,
                "today": date.today().isoformat(),
            },
            status_code=status_code,
        )

    def payment_page(
        request: Request,
        flight: dict,
        passengers: int,
        cabin: str,
        chosen: list[str],
        people: list[dict],
        errors: dict[str, str],
        status_code: int = 200,
        alert: list[str] | None = None,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "checkout_payment.html",
            {
                "flight": flight,
                "passengers": passengers,
                "cabin": cabin,
                "chosen": chosen,
                "people": people,
                "errors": errors,
                "alert": alert or [],
            },
            status_code=status_code,
        )

    @app.get("/book/{flight_id}", response_class=HTMLResponse, response_model=None)
    def book_passengers(
        flight_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)
    ) -> HTMLResponse | RedirectResponse:
        params = request.query_params
        passengers, cabin, errors = search.parse_party(params.get("passengers"), params.get("cabin"))
        if errors:
            return RedirectResponse(f"/flights/{flight_id}", status_code=303)
        flight = search.flight_detail(conn, flight_id, passengers, cabin)
        if flight is None:
            raise HTTPException(status_code=404, detail="Flight not found")

        chosen = _chosen(params.getlist("seat"))
        if _seat_problems(conn, flight, cabin, chosen, passengers):
            return RedirectResponse(
                _flight_page_url(flight_id, passengers, cabin, chosen), status_code=303
            )
        people = _people([], [], [], "", passengers)
        return passenger_page(request, flight, passengers, cabin, chosen, people, {})

    @app.post("/book/{flight_id}/passengers", response_class=HTMLResponse, response_model=None)
    def book_payment_step(
        flight_id: int,
        request: Request,
        passengers: Annotated[str, Form()] = "1",
        cabin: Annotated[str, Form()] = "economy",
        seat: Annotated[list[str] | None, Form()] = None,
        first_name: Annotated[list[str] | None, Form()] = None,
        last_name: Annotated[list[str] | None, Form()] = None,
        date_of_birth: Annotated[list[str] | None, Form()] = None,
        email: Annotated[str, Form()] = "",
        conn: sqlite3.Connection = Depends(get_db),
    ) -> HTMLResponse | RedirectResponse:
        count, cabin, errors = search.parse_party(passengers, cabin)
        if errors:
            return RedirectResponse(f"/flights/{flight_id}", status_code=303)
        flight = search.flight_detail(conn, flight_id, count, cabin)
        if flight is None:
            raise HTTPException(status_code=404, detail="Flight not found")

        chosen = _chosen(seat)
        if _seat_problems(conn, flight, cabin, chosen, count):
            return RedirectResponse(
                _flight_page_url(flight_id, count, cabin, chosen), status_code=303
            )
        rows = _people(first_name, last_name, date_of_birth, email, count)
        cleaned, errors = checkout.validate_passengers(rows, date.today())
        if errors:
            return passenger_page(request, flight, count, cabin, chosen, rows, errors, 422)
        return payment_page(request, flight, count, cabin, chosen, cleaned, {})

    @app.post("/book/{flight_id}/pay", response_class=HTMLResponse, response_model=None)
    def book_pay(
        flight_id: int,
        request: Request,
        passengers: Annotated[str, Form()] = "1",
        cabin: Annotated[str, Form()] = "economy",
        seat: Annotated[list[str] | None, Form()] = None,
        first_name: Annotated[list[str] | None, Form()] = None,
        last_name: Annotated[list[str] | None, Form()] = None,
        date_of_birth: Annotated[list[str] | None, Form()] = None,
        email: Annotated[str, Form()] = "",
        card_number: Annotated[str, Form()] = "",
        expiry: Annotated[str, Form()] = "",
        cvc: Annotated[str, Form()] = "",
        conn: sqlite3.Connection = Depends(get_db),
    ) -> HTMLResponse | RedirectResponse:
        count, cabin, errors = search.parse_party(passengers, cabin)
        if errors:
            return RedirectResponse(f"/flights/{flight_id}", status_code=303)
        flight = search.flight_detail(conn, flight_id, count, cabin)
        if flight is None:
            raise HTTPException(status_code=404, detail="Flight not found")

        chosen = _chosen(seat)
        if _seat_problems(conn, flight, cabin, chosen, count):
            return RedirectResponse(
                _flight_page_url(flight_id, count, cabin, chosen), status_code=303
            )
        rows = _people(first_name, last_name, date_of_birth, email, count)
        cleaned, errors = checkout.validate_passengers(rows, date.today())
        if errors:
            return passenger_page(request, flight, count, cabin, chosen, rows, errors, 422)

        # The card is checked and dropped here. It is never rendered or stored.
        card_errors = payment.validate_card(card_number, expiry, cvc)
        if card_errors:
            return payment_page(request, flight, count, cabin, chosen, cleaned, card_errors, 422)

        try:
            booking = checkout.create_booking(conn, flight_id, cabin, cleaned, chosen)
        except checkout.BookingError as exc:
            return payment_page(
                request, flight, count, cabin, chosen, cleaned, {}, exc.status, exc.details
            )
        query = urlencode({"last_name": cleaned[0]["last_name"], "new": "1"})
        return RedirectResponse(f"/bookings/{booking['reference']}?{query}", status_code=303)

    @app.post("/api/bookings", status_code=201)
    def api_create_booking(
        body: Annotated[dict[str, Any], Body()],
        conn: sqlite3.Connection = Depends(get_db),
    ) -> JSONResponse:
        """Book in one request.

        Body: ``{"flight_id", "cabin", "passengers": [{"first_name", "last_name",
        "date_of_birth", "seat"}, ...], "email": "<lead passenger>",
        "card": {"number", "expiry", "cvc"}}``. Each passenger's ``seat`` is
        their seat. Returns 201 with the booking, 422 with ``errors`` keyed by
        field, or 404/409 with ``error`` and ``details``.
        """
        today = date.today()
        errors: dict[str, str] = {}

        flight_id = body.get("flight_id")
        if not isinstance(flight_id, int) or isinstance(flight_id, bool):
            errors["flight_id"] = "flight_id must be a whole number."
        cabin = _str(body.get("cabin"))
        if cabin not in checkout.CABINS:
            errors["cabin"] = "Choose economy or business."

        raw = body.get("passengers")
        if not isinstance(raw, list) or not 1 <= len(raw) <= checkout.MAX_PASSENGERS:
            errors["passengers"] = (
                f"Book between 1 and {checkout.MAX_PASSENGERS} passengers."
            )
            raw = []
        rows: list[dict[str, Any]] = []
        seat_values: list[str] = []
        for index, item in enumerate(raw):
            item = item if isinstance(item, dict) else {}
            row = {name: item.get(name) for name in PASSENGER_FIELDS}
            if index == 0:
                row["email"] = item.get("email", body.get("email"))
            rows.append(row)
            seat = _str(item.get("seat"))
            if not seat.strip():
                errors[f"seat_{index}"] = "Choose a seat for this passenger."
            seat_values.append(seat)

        cleaned: list[dict] = []
        if rows:
            cleaned, passenger_errors = checkout.validate_passengers(rows, today)
            errors.update(passenger_errors)

        card = body.get("card") if isinstance(body.get("card"), dict) else {}
        errors.update(
            payment.validate_card(
                _str(card.get("number")), _str(card.get("expiry")), _str(card.get("cvc")), today
            )
        )
        if errors:
            return JSONResponse({"errors": errors}, status_code=422)

        chosen = _chosen(seat_values)
        try:
            booking = checkout.create_booking(conn, flight_id, cabin, cleaned, chosen, today)
        except checkout.BookingError as exc:
            return JSONResponse(
                {"error": exc.message, "details": exc.details}, status_code=exc.status
            )
        return JSONResponse(booking, status_code=201)
