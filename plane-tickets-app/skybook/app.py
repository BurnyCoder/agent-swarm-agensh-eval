"""FastAPI application factory.

Each request gets its own SQLite connection through ``get_db``. The database
path comes from the ``SKYBOOK_DB`` environment variable, or from
``data/skybook.db`` in the repository by default.

Search lives in ``search.py``; this module only wires it to HTML pages and JSON.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bookings, checkout_routes, db, search, seats

PACKAGE_DIR = Path(__file__).resolve().parent

# Search form fields, in the order they appear in URLs.
FORM_FIELDS = (
    "origin",
    "destination",
    "date",
    "return_date",
    "passengers",
    "cabin",
    "sort",
    "max_stops",
    "airline",
)
FORM_DEFAULTS = {"passengers": "1", "cabin": "economy", "sort": "price"}
# Errors shown next to a form field. Any other error is listed in the alert.
INLINE_ERROR_FIELDS = ("origin", "destination", "date", "return_date", "passengers", "cabin")
NOT_FOUND_MESSAGE = "No booking matches that reference and last name."
LAST_NAME_REQUIRED = "Enter the last name of the lead passenger."
SORT_LABELS = {"price": "Price", "departure": "Departure time", "duration": "Duration"}


def form_values(params: Mapping[str, str | None]) -> dict[str, str]:
    """Raw search values with defaults, for re-filling the form."""
    values = {}
    for name in FORM_FIELDS:
        values[name] = (params.get(name) or "").strip() or FORM_DEFAULTS.get(name, "")
    values["origin"] = values["origin"].upper()
    values["destination"] = values["destination"].upper()
    values["airline"] = values["airline"].upper()
    return values


def create_app(database_path: str | Path | None = None) -> FastAPI:
    path = Path(database_path or os.environ.get("SKYBOOK_DB") or db.DEFAULT_DB_PATH)
    db.init_db(path)

    app = FastAPI(title="SkyBook", version="0.1.0")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.filters.update(
        clock=search.format_clock,
        day_label=search.format_day,
        duration=search.format_duration,
        money=search.format_cents,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    def get_db() -> Iterator[sqlite3.Connection]:
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    checkout_routes.register(app, templates, get_db)

    def render_index(
        request: Request,
        conn: sqlite3.Connection,
        values: dict[str, str],
        errors: dict[str, str],
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "airports": db.list_airports(conn),
                "values": values,
                "errors": errors,
                "inline_error_fields": INLINE_ERROR_FIELDS,
                "today": date.today().isoformat(),
                "cabins": search.CABINS,
                "max_passengers": search.MAX_PASSENGERS,
            },
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> HTMLResponse:
        return render_index(request, conn, form_values(request.query_params), {})

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> HTMLResponse:
        values = form_values(request.query_params)
        plan, errors = search.plan_search(conn, request.query_params, date.today())
        if plan is None:
            return render_index(request, conn, values, errors, status_code=422)

        outbound = search.search_leg(conn, plan.outbound)
        inbound = search.search_leg(conn, plan.inbound) if plan.inbound else None
        legs = [outbound] + ([inbound] if inbound else [])
        airlines = sorted({pair for leg in legs for pair in leg["airlines"]}, key=lambda p: p[1])
        change_url = "/?" + urlencode({k: values[k] for k in FORM_FIELDS if values[k]})
        return templates.TemplateResponse(
            request,
            "results.html",
            {
                "values": values,
                "outbound": outbound,
                "inbound": inbound,
                "airlines": airlines,
                "sort_labels": SORT_LABELS,
                "change_url": change_url,
            },
        )

    @app.get("/api/airports")
    def api_airports(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
        return [dict(row) for row in db.list_airports(conn)]

    @app.get("/api/flights")
    def api_flights(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:
        params = request.query_params
        query, errors = search.validate_query(
            params.get("origin"),
            params.get("destination"),
            params.get("date"),
            params.get("passengers"),
            params.get("cabin"),
            known_airports=search.known_airport_codes(conn),
            today=date.today(),
            sort=params.get("sort"),
            max_stops_text=params.get("max_stops"),
            airline=params.get("airline"),
        )
        if query is None:
            return JSONResponse({"errors": errors}, status_code=422)
        return JSONResponse(search.search_flights(conn, query))

    @app.get("/api/flights/{flight_id}")
    def api_flight(
        flight_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)
    ) -> JSONResponse:
        params = request.query_params
        passengers, cabin, errors = search.parse_party(params.get("passengers"), params.get("cabin"))
        if errors:
            return JSONResponse({"errors": errors}, status_code=422)
        detail = search.flight_detail(conn, flight_id, passengers, cabin)
        if detail is None:
            raise HTTPException(status_code=404, detail="Flight not found")
        return JSONResponse(detail)

    @app.get("/flights/{flight_id}", response_class=HTMLResponse)
    def flight_page(
        flight_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)
    ) -> HTMLResponse:
        params = request.query_params
        passengers, cabin, errors = search.parse_party(params.get("passengers"), params.get("cabin"))
        if errors:
            return flight_error(request, list(errors.values()), 422)
        detail = search.flight_detail(conn, flight_id, passengers, cabin)
        if detail is None:
            return flight_error(request, ["That flight does not exist."], 404)

        taken = seats.taken_seats(conn, flight_id)
        chosen = [seats.normalise_seat(s) for s in params.getlist("seat") if s.strip()]
        picked = params.get("picked") == "1"
        seat_errors: list[str] = []
        if picked:
            valid = seats.cabin_seats(detail["aircraft"], cabin)
            seat_errors = seats.validate_selection(chosen, passengers, valid, taken)
        confirmed = picked and not seat_errors

        return templates.TemplateResponse(
            request,
            "flight.html",
            {
                "flight": detail,
                "passengers": passengers,
                "cabin": cabin,
                "cabins": search.CABINS,
                "rows": seats.cabin_seat_map(detail["aircraft"], cabin, taken),
                "chosen": chosen if picked else [],
                "picked": picked,
                "confirmed": confirmed,
                "seat_errors": seat_errors,
            },
        )

    def booking_lookup_page(
        request: Request, values: dict[str, str], errors: dict[str, str], status_code: int = 200
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "booking.html",
            {"booking": None, "values": values, "errors": errors, "notice": None, "error": None},
            status_code=status_code,
        )

    def booking_view_page(
        request: Request,
        conn: sqlite3.Connection,
        booking_row,
        last_name: str,
        *,
        notice: str | None = None,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "booking.html",
            {
                "booking": bookings.booking_details(conn, booking_row),
                "values": {"reference": booking_row["reference"], "last_name": last_name},
                "errors": {},
                "notice": notice,
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/bookings", response_class=HTMLResponse)
    def booking_lookup(request: Request) -> HTMLResponse:
        params = request.query_params
        reference = bookings.normalise_reference(params.get("reference"))
        last_name = (params.get("last_name") or "").strip()
        values = {"reference": reference, "last_name": last_name}
        if not reference and not last_name:
            return booking_lookup_page(request, values, {})
        errors = {}
        if not bookings.REFERENCE_PATTERN.match(reference):
            errors["reference"] = "Enter the 6-character booking reference from your confirmation."
        if not last_name:
            errors["last_name"] = LAST_NAME_REQUIRED
        if errors:
            return booking_lookup_page(request, values, errors, status_code=422)
        return RedirectResponse(
            f"/bookings/{reference}?" + urlencode({"last_name": last_name}), status_code=303
        )

    @app.get("/bookings/{reference}", response_class=HTMLResponse)
    def booking_page(
        reference: str, request: Request, conn: sqlite3.Connection = Depends(get_db)
    ) -> HTMLResponse:
        params = request.query_params
        last_name = (params.get("last_name") or "").strip()
        values = {"reference": bookings.normalise_reference(reference), "last_name": last_name}
        if not last_name:
            return booking_lookup_page(request, values, {"last_name": LAST_NAME_REQUIRED}, 422)
        booking = bookings.find_booking(conn, reference, last_name)
        if booking is None:
            return booking_lookup_page(request, values, {"form": NOT_FOUND_MESSAGE}, 404)
        notice = None
        if params.get("cancelled") == "1":
            notice = "Your booking has been cancelled and its seats released."
        elif params.get("new") == "1":
            notice = "Thank you. Your booking is confirmed. Keep your reference for later."
        return booking_view_page(request, conn, booking, last_name, notice=notice)

    @app.post("/bookings/{reference}/cancel", response_class=HTMLResponse)
    def booking_cancel(
        reference: str,
        request: Request,
        last_name: str = Form(""),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> HTMLResponse:
        last_name = last_name.strip()
        booking = bookings.find_booking(conn, reference, last_name) if last_name else None
        if booking is None:
            values = {"reference": bookings.normalise_reference(reference), "last_name": last_name}
            return booking_lookup_page(request, values, {"form": NOT_FOUND_MESSAGE}, 404)
        try:
            bookings.cancel_booking(conn, booking)
        except bookings.CancelError as exc:
            return booking_view_page(
                request, conn, booking, last_name, error=str(exc), status_code=409
            )
        target = f"/bookings/{booking['reference']}?" + urlencode(
            {"last_name": last_name, "cancelled": "1"}
        )
        return RedirectResponse(target, status_code=303)

    @app.get("/api/bookings/{reference}")
    def api_booking(
        reference: str, request: Request, conn: sqlite3.Connection = Depends(get_db)
    ) -> JSONResponse:
        last_name = (request.query_params.get("last_name") or "").strip()
        if not last_name:
            return JSONResponse({"errors": {"last_name": LAST_NAME_REQUIRED}}, status_code=422)
        booking = bookings.find_booking(conn, reference, last_name)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        return JSONResponse(bookings.booking_details(conn, booking))

    @app.post("/api/bookings/{reference}/cancel")
    def api_cancel_booking(
        reference: str, request: Request, conn: sqlite3.Connection = Depends(get_db)
    ) -> JSONResponse:
        last_name = (request.query_params.get("last_name") or "").strip()
        if not last_name:
            return JSONResponse({"errors": {"last_name": LAST_NAME_REQUIRED}}, status_code=422)
        booking = bookings.find_booking(conn, reference, last_name)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        try:
            bookings.cancel_booking(conn, booking)
        except bookings.CancelError as exc:
            return JSONResponse({"errors": {"booking": str(exc)}}, status_code=409)
        return JSONResponse(bookings.booking_details(conn, bookings.refresh(conn, booking)))

    def flight_error(request: Request, messages: list[str], status_code: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "flight.html",
            {"flight": None, "error": messages},
            status_code=status_code,
        )

    return app
