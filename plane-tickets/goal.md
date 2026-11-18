# SkyBook: a plane ticket booking web app

Build a small but complete web application where people search for flights and
book plane tickets. This is a demo: no real airlines, no real payments.

## Stack

- Python 3.12 backend with FastAPI (or Flask), SQLite through the standard
  library or SQLAlchemy, server-rendered HTML (Jinja2) plus a little vanilla
  JavaScript and CSS. No Node build step.
- Pin dependencies in `requirements.txt` at the repository root.
- Seed the database on first start with realistic sample data: at least 12
  airports (IATA code, city, country) and at least 200 flights over the next
  30 days (flight number, airline, origin, destination, departure and arrival
  times, duration, number of stops, aircraft seat map, economy and business
  fares, seats left).
- One start command, documented in the README: `python3 -m skybook` serves the
  app on `http://0.0.0.0:8000` (port overridable with the `PORT` environment
  variable).

## Features

1. **Search**: home page form with origin, destination (autocomplete or select
   from airports), departure date, optional return date (round trip),
   passenger count (1-9) and cabin (economy/business). Validate input
   (origin != destination, date not in the past).
2. **Results**: matching flights with times, duration, stops, airline and
   price for the chosen cabin and passenger count. Sort by price, departure
   time or duration; filter by max stops and airline. A clear message when
   nothing matches.
3. **Flight details and seats**: fare breakdown (base fare, taxes, total) and
   a seat map where the user picks one seat per passenger; taken seats cannot
   be chosen.
4. **Passengers and checkout**: a form per passenger (first name, last name,
   date of birth, email for the lead passenger) with server-side validation,
   then a mock payment step that accepts only the test card number
   `4242 4242 4242 4242` with any future expiry and any 3-digit CVC, and
   rejects everything else with a helpful error. Never store card numbers.
5. **Confirmation**: a booking reference (6 uppercase letters/digits), the
   itinerary, seats, passengers and total paid. Seats left and the seat map
   update after booking.
6. **Manage booking**: look up a booking by reference + lead passenger last
   name; view it; cancel it (seats are released, status becomes cancelled).
7. **JSON API** used by the pages and by tests:
   `GET /api/airports`, `GET /api/flights?origin=&destination=&date=&passengers=&cabin=`,
   `GET /api/flights/{id}`, `POST /api/bookings`, `GET /api/bookings/{reference}?last_name=`,
   `POST /api/bookings/{reference}/cancel`.

## Quality

- pytest tests for search, seat selection, booking validation, payment
  rejection, confirmation and cancellation (API tests through the framework's
  test client are fine). `python3 -m pytest -q` must pass from the repository root.
- A README with features, screenshots or a short walkthrough, how to install
  (`pip install -r requirements.txt`), start and test.
- A clean, responsive look that works on desktop and mobile widths.

Done means every feature above works end to end in a browser after
`pip install -r requirements.txt && python3 -m skybook`, and the tests pass.
