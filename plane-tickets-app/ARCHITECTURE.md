# SkyBook architecture

## Overview

SkyBook is one Python package, `skybook`, served by FastAPI and Uvicorn.
Pages are server-rendered with Jinja2. JavaScript and CSS are plain files
under `skybook/static/`. There is no Node build step.

```
skybook/
  __main__.py   entry point: python3 -m skybook (reads PORT, binds 0.0.0.0)
  app.py        create_app(): routes, templates, static files, DB dependency
  db.py         connect(), SCHEMA, init_db() (schema + first-start seed)
  seed.py       fixed-seed sample airports, airlines and flights
  aircraft.py   seat layouts per aircraft type and seat_map() for the UI
  search.py     search validation, seats left, filtering and sorting
  pricing.py    fare breakdown: base, taxes and total in integer cents
  seats.py      seat rules: taken seats, valid cabin seats, selection checks
  bookings.py   booking lookup (reference + lead last name), view model, cancellation
  payment.py    mock card check: only the test card is accepted
  templates/    base.html, index.html (search form), results.html, flight.html, booking.html
  static/       style.css, search.js (swap button), seatmap.js (seat picker)
tests/          pytest suite (FastAPI TestClient for HTTP tests)
```

## Decisions

- **Persistence: stdlib sqlite3, no ORM.** The schema is small and fixed. Raw
  SQL keeps the dependency list short. Foreign keys are enforced per connection
  with `PRAGMA foreign_keys = ON`.
- **One connection per request.** `app.get_db` opens a connection, yields it and
  closes it. SQLite handles this well at demo scale.
- **Database location.** `data/skybook.db` in the repository, overridable with
  `SKYBOOK_DB`. `data/` is git-ignored.
- **Seeding.** `init_db` creates the schema with `IF NOT EXISTS`. It seeds only
  when `airports` is empty, so restarts never duplicate data. The generator
  uses `random.Random(SEED)`, so the data is reproducible and tests can rely on it.
- **Times.** Departure and arrival times are stored as local ISO 8601 strings,
  with `duration_minutes` stored separately. Each airport has a fixed UTC offset
  (no daylight saving). The test suite checks that arrival minus departure equals
  the duration plus the offset difference.
- **Money.** Fares are stored as integer cents (`*_fare_cents`). Floats are never
  used for money.
- **Seats.** `taken_seats` stores only occupied seats. A seat with no row is free,
  so seats left = layout capacity minus taken rows. Seats sold during the seed
  have `booking_id` NULL. Seat IDs look like `12C`. Each aircraft's layout is
  defined once in `aircraft.py`.
- **Aircraft.** Three layouts (A320, B737, B789), each with business and economy
  cabins. Seat letters are `A`-`F` on the A320 and 737. The 787's economy block
  uses `HJK`, which skips `G` and `I`.
- **Fictional data.** Airports are real, with IATA codes, names and coordinates.
  Airlines and flight numbers are invented. No real schedules or prices are used.
- **Search is a GET form.** The home form submits to `GET /search` as a plain
  query string, so results pages are bookmarkable and work without JavaScript.
  `/api/flights` takes the same parameters and returns JSON. Both use
  `search.validate_query` and `search.plan_search`, so the rules live in one place.
- **Validation.** Errors are returned as a dict keyed by field (`origin`,
  `destination`, `date`, `return_date`, `passengers`, `cabin`, `sort`,
  `max_stops`). The HTML page re-renders the form with inline errors. The API
  returns 422 with `{"errors": ...}`. Dates must not be in the past, and origin
  must differ from destination. Passengers are 1-9.
- **Seats left and party size.** A flight is listed only when its cabin has at
  least as many seats left as the party size. `seats_left` is the layout
  capacity minus the `taken_seats` rows for that flight and cabin.
- **Round trips.** `plan_search` returns an outbound query and, when a return
  date is given, an inbound query with origin and destination swapped. The return
  date must be on or after the departure date. Each leg is searched and rendered
  separately, so one leg can have no flights while the other has some.
- **Filters and sorting.** Filters (`max_stops`, `airline`) run after the seats
  check. The leg's `total` counts flights before filters, which lets the page say
  "N flights run on this day" when filters remove everything. Sorting is by
  total price for the party, departure time or duration. Each sort uses a second
  key to break ties: price breaks on departure time, and departure and duration
  break on total price.

- **Flight page.** `GET /flights/{id}` takes `passengers`, `cabin`, and for the
  seat step `picked=1` plus repeated `seat` values. It is a plain GET form, so it
  works without JavaScript and the URL can be shared. Bad party input returns 422
  and an unknown flight returns 404, both rendering the page's error panel. The
  seat check runs only when `picked=1`, so the first visit never shows a
  "taken" error for a seat the user has not chosen.
- **Validation authority.** The server decides every seat question: the count
  must equal the passenger count, each seat must exist in the requested cabin,
  no seat may repeat (case-insensitive), and no seat may be taken. The rules live
  in `seats.validate_selection`. `seatmap.js` only makes the choice easier.
  Taken seats are rendered `disabled`, so a no-JS user cannot pick them either.
- **Progressive enhancement for seats.** `seatmap.js` caps the boxes at the
  passenger count, updates the "N of M chosen" counter, enables "Confirm seats"
  only when the count is exact, and marks chosen seats. Without it the user can
  still tick boxes and submit, and the server explains any problem. The script
  captures the taken boxes once, so it never re-enables them.
- **Fares.** `pricing.fare_breakdown` works in integer cents. Taxes are 12% of
  each passenger's base fare, rounded down to the cent per passenger, and then
  multiplied by the party size. Rounding per passenger keeps the per-passenger
  total times the party size equal to the party total, which the tests check. The
  same breakdown is returned by `GET /api/flights/{id}` and rendered in the fare
  table.
- **Seat ids.** Ids are normalised with `strip().upper()` before any check, so
  `6a` and `6A` mean the same seat. `cabin_seats` lists the ids valid for one
  cabin, so a business seat such as `1A` is rejected on an economy flight.
- **Seats feed checkout.** "Continue to passengers" is a GET form that sends
  `passengers`, `cabin` and one `seat` per chosen seat to `/book/{id}`. Nothing is
  stored until payment succeeds.

## Payment (mock card check)

`skybook/payment.py` holds `validate_card(number, expiry, cvc, today=None)`, which
returns field errors keyed `card_number`, `expiry` and `cvc`. An empty dict means
the card is accepted.

- **Only the test card works.** `4242 4242 4242 4242` is accepted with spaces or
  dashes between groups. Any other 16-digit number gets a "declined, no real card
  is charged" message. Shorter or non-digit input gets its own message.
- **Any future expiry and any 3-digit CVC.** Expiry is `MM/YY` or `MM/YYYY`. A
  card stays valid through the end of its expiry month, so `parsed < (today.year,
  today.month)` is the only expired case. The CVC must be exactly three ASCII
  digits.
- **Nothing is stored.** The module returns messages only. It has no database
  access, no logging and no return value that carries the card number, so error
  text never echoes what was typed.
- **Injectable date.** `today` is a parameter so the expiry rules can be tested
  without depending on the day the suite runs.

## Bookings and manage booking

Tables `bookings` and `passengers` live in `db.SCHEMA`. `taken_seats.booking_id`
points at a booking, and seats sold during the seed keep a NULL `booking_id`.

- **Reference.** Six characters, `A-Z` and `0-9`, stored uppercase and unique
  (`bookings.REFERENCE_PATTERN`). Input is stripped and uppercased before lookup.
- **Lead passenger.** The lead is the passenger with the lowest `passengers.id`.
  The lead's email is also stored on the booking as `lead_email`, and only the
  lead has `passengers.email` set.
- **Lookup rule.** A booking is found only by reference plus the lead's last
  name (`bookings.find_booking`). Last names match case-insensitively with runs of
  whitespace collapsed. Other passengers' names do not open the booking. A wrong
  name and an unknown reference both return the same 404 message, so a guessed
  reference reveals nothing.
- **Pages.** `GET /bookings` is the lookup form. A valid submission redirects to
  `GET /bookings/{REF}?last_name=...`, which shows the booking. `new=1` adds the
  confirmation banner after checkout, and `cancelled=1` adds the cancelled banner.
- **Cancellation.** `bookings.cancel_booking` sets `status = 'cancelled'` and
  deletes that booking's `taken_seats` rows, which releases its seats. Passenger
  rows keep their seats so the record still shows what was booked. Cancelling
  twice is a no-op. Once the flight's departure date (the date part of
  `departs_local`) is before today, the booking can no longer be cancelled. The
  page then hides the button, and the API returns 409 `{"errors": {"booking": ...}}`.
- **Forms.** The cancel form is a POST that redirects (303) to the booking page.
  Form posts need `python-multipart`, which is pinned in `requirements.txt`.
- **API.** `GET /api/bookings/{reference}?last_name=` returns the booking, with
  `can_cancel`, `seats` and `passengers`. `POST /api/bookings/{reference}/cancel?last_name=`
  returns the updated booking. A missing `last_name` is 422, and an unknown
  booking or wrong name is 404.

## Checkout

Pure logic lives in `skybook/checkout.py`; HTTP lives in `skybook/checkout_routes.py`,
registered from `app.py`. Templates are `checkout_passengers.html` and
`checkout_payment.html`.

- **Flow.** `GET /book/{id}?passengers=&cabin=&seat=...` shows one fieldset per seat.
  `POST /book/{id}/passengers` validates names, dates of birth and the lead email,
  then shows the payment page. `POST /book/{id}/pay` checks the card with
  `payment.validate_card` and then calls `checkout.create_booking`. Success is a
  303 to `/bookings/{REF}?last_name=...&new=1`.
- **Passenger data round-trips in hidden fields.** Nothing is kept server-side
  between steps, so every step re-validates everything. The pay step cannot be
  skipped: the card is checked again on the pay POST and on the API.
- **One write path.** `create_booking` runs in `BEGIN IMMEDIATE`. It checks the
  flight exists and has not left, checks the seats, then inserts the booking,
  passengers and `taken_seats` rows, and commits once. A duplicate seat (including
  a race that trips the `taken_seats` primary key) is a 409 with `details`. A
  reference collision is retried up to 10 times, then 503.
- **Errors.** Field errors re-render the page with 422. Seat problems on the pay
  step redirect to the seat map (303). Other `BookingError`s re-render the payment
  page with the message and a "choose seats again" link.
- **Card data.** Card fields are never stored, logged or echoed back. The payment
  page only shows the test-card hint.
- **API.** `POST /api/bookings` takes JSON `{flight_id, cabin, passengers:[{first_name,
  last_name, date_of_birth, seat}], email, card:{number, expiry, cvc}}`. It returns 201
  with the booking, 422 `{"errors": {...}}` for bad input, 409/404 from `BookingError`.
- **Decisions.** Endpoints are sync `def` because `get_db` yields a per-request sqlite
  connection that must stay on one thread. Names allow letters, spaces, hyphens,
  apostrophes and dots. Dates of birth can be at most 120 years back.

## Dependencies

Pinned in `requirements.txt`. `python-multipart` is required by FastAPI to read
HTML form posts. The cancel form is the only POST form. The test client currently emits a Starlette
deprecation warning that suggests `httpx2`. It is harmless, and the switch can be
made when the test client changes.
