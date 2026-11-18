# SkyBook

SkyBook is a plane ticket booking web app. People search for flights, pick a
fare and seats, and book tickets. It is a demo: the airlines and flight numbers
are invented, and no real payments happen.

## Features

| Feature | Status |
| --- | --- |
| Search: origin, destination, date, optional return, 1-9 passengers, economy or business | Done |
| Results: times, duration, stops, airline and price; sort by price, departure or duration; filter by stops and airline; clear "no flights" message | Done |
| Flight details: fare breakdown (base, taxes, total) and a seat map where taken seats cannot be picked | Done |
| Passengers and checkout: per-passenger form with server-side validation, mock payment (test card only) | Done |
| Confirmation: booking reference, itinerary, seats, passengers, total paid | Done |
| Manage booking: look up by reference and last name, view, cancel | Done |
| JSON API for airports, flights and bookings | Done |

The seat picker works without JavaScript. The script only caps the number of
picks and shows progress. The server checks every selection.

## Getting started

Requires Python 3.12.

```sh
pip install -r requirements.txt   # install pinned dependencies
python3 -m skybook                # serve on http://0.0.0.0:8000
PORT=9000 python3 -m skybook      # choose another port
python3 -m pytest -q              # run the test suite
```

Open <http://localhost:8000> in a browser.

On first start SkyBook creates `data/skybook.db` and fills it with sample data:
15 airports and 270 flights over the next 30 days. The data comes from a fixed
seed, so every fresh database is the same. Delete `data/skybook.db` to reset it.
Set `SKYBOOK_DB` to use another database file.

## Walkthrough

1. **Search.** On the home page, choose an origin and a destination (they must
   differ), a date in the future, an optional return date, the passenger count
   and the cabin. Errors appear inline next to the field.
2. **Results.** Each flight shows its times, duration, stops, airline and the
   total price for the whole party. Use the sort and filter controls to narrow
   the list.
3. **Flight and seats.** Open a flight to see the fare breakdown. Pick one seat
   per passenger on the seat map. Taken seats are greyed out and cannot be
   chosen.
4. **Passengers and payment.** Click **Continue to passengers** on the flight
   page. Enter each passenger's first name, last name and date of birth. The
   lead passenger also gives an email address. Errors appear next to the field.
   Then pay with the test card `4242 4242 4242 4242`, any future expiry and any
   3-digit CVC. Other card numbers are declined with a message that says which
   number is accepted. No card details are stored.
5. **Confirmation and manage booking.** A successful payment opens the
   confirmation page with a 6-character booking reference, the itinerary, the
   seats, the passengers and the total paid. The seats are taken straight away,
   so the seat map and the seats left update for everyone. Look the booking up
   again later with the reference and the lead passenger's last name, and
   cancel it there if needed.

## JSON API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/airports` | All airports (IATA code, city, country) |
| GET | `/api/flights?origin=&destination=&date=&passengers=&cabin=` | Matching flights with the party price. Optional `max_stops`, `airline`, `sort` (`price`, `departure`, `duration`) |
| GET | `/api/flights/{id}?passengers=&cabin=` | One flight with the fare breakdown for the party |
| POST | `/api/bookings` | Done. Body has `flight_id`, `cabin`, `passengers` (each with `first_name`, `last_name`, `date_of_birth`, `seat`), `email` for the lead passenger, and `card` (`number`, `expiry`, `cvc`). 201 with the booking, 422 with `errors`, 409 if a seat is taken or the flight has left, 404 for an unknown flight |
| GET | `/api/bookings/{reference}?last_name=` | Done. 404 if no match |
| POST | `/api/bookings/{reference}/cancel?last_name=` | Done. 409 if the flight has departed; cancelling twice is a no-op |

Validation errors return HTTP 422 with `{"errors": {...}}`. Unknown flights
return 404.

## Tests

`python3 -m pytest -q` runs the suite from the repository root. Tests cover
search, seat selection, fares, the mock card check, checkout, manage booking
and the HTTP routes, including an end-to-end journey. They use
FastAPI's test client, so no server needs to be running.

## Project layout

- `skybook/app.py` - FastAPI application factory and routes
- `skybook/db.py` - SQLite connection, schema and first-start initialisation
- `skybook/seed.py` - deterministic sample airports, airlines and flights
- `skybook/aircraft.py` - aircraft seat layouts and seat map helper
- `skybook/search.py` - search validation, seats left, filters and sorting
- `skybook/pricing.py` - fare breakdown (base, taxes, total) in integer cents
- `skybook/payment.py` - mock card check (only the test card is accepted)
- `skybook/checkout.py` - passenger validation and the booking write
- `skybook/checkout_routes.py` - checkout pages and `POST /api/bookings`
- `skybook/bookings.py` - booking lookup, manage view model and cancellation
- `skybook/seats.py` - seat rules: taken seats and selection validation
- `skybook/templates/`, `skybook/static/` - Jinja2 pages, CSS and small scripts
- `tests/` - pytest suite

## Manage a booking

1. Open `http://localhost:8000/bookings`.
2. Enter the 6-character booking reference and the last name of the lead
   passenger (the first passenger on the booking). Case and extra spaces do not matter.
3. The booking page shows the itinerary, passengers with seats, and the total paid.
4. Click **Cancel booking** to cancel. The seats are released and the status
   becomes cancelled. A cancelled booking cannot be cancelled again, and
   a booking on a flight that has already departed has no cancel button.

The same lookup and cancel work through the JSON API:

```sh
curl 'http://localhost:8000/api/bookings/ABC123?last_name=Lovelace'
curl -X POST 'http://localhost:8000/api/bookings/ABC123/cancel?last_name=Lovelace'
```

- `ARCHITECTURE.md` - design decisions and how the pieces fit together
