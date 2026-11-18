"""Search tests: input validation, round trips, sorting, filters, seats left, the
JSON API and the HTML search pages.

The seed covers the next 30 days. Tests that need exact flights insert their own
rows on FAR_DAY, 45 days out, which the seed never uses, so counts stay exact.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skybook import aircraft, db, search
from skybook.app import create_app

TODAY = date.today()
FAR_DAY = TODAY + timedelta(days=45)
REF_TODAY = date(2026, 11, 18)
KNOWN = {"AAA", "BBB"}


# --- helpers and fixtures ----------------------------------------------------


def valid(**overrides):
    args = dict(
        origin="AAA",
        destination="BBB",
        day_text="2026-11-20",
        passengers_text="1",
        cabin="economy",
        known_airports=KNOWN,
        today=REF_TODAY,
    )
    args.update(overrides)
    return search.validate_query(**args)


def insert_flight(
    conn,
    origin,
    destination,
    day,
    number,
    *,
    airline=("SB", "Skybook Air"),
    departs="08:00",
    duration=180,
    stops=0,
    aircraft_code="A320",
    economy=20000,
    business=60000,
):
    start = datetime.fromisoformat(f"{day.isoformat()}T{departs}")
    end = start + timedelta(minutes=duration)
    cursor = conn.execute(
        "INSERT INTO flights (flight_number, airline_code, airline_name, origin, destination,"
        " departs_local, arrives_local, duration_minutes, stops, aircraft,"
        " economy_fare_cents, business_fare_cents)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            number,
            airline[0],
            airline[1],
            origin,
            destination,
            start.isoformat(timespec="minutes"),
            end.isoformat(timespec="minutes"),
            duration,
            stops,
            aircraft_code,
            economy,
            business,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def take_seats(conn, flight_id, aircraft_code, cabin, count):
    seats = list(aircraft.get_aircraft(aircraft_code).cabin(cabin).seats())[:count]
    conn.executemany(
        "INSERT INTO taken_seats (flight_id, seat, cabin) VALUES (?, ?, ?)",
        [(flight_id, seat, cabin) for seat in seats],
    )
    conn.commit()


def flight_numbers(body):
    return [f["flight_number"] for f in body]


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
def route(conn):
    codes = [row["code"] for row in conn.execute("SELECT code FROM airports ORDER BY code")]
    return codes[0], codes[1]


@pytest.fixture()
def day_flights(conn, route):
    """Three flights on one route and day: morning, early and evening."""
    origin, destination = route
    return {
        "morning": insert_flight(
            conn, origin, destination, FAR_DAY, "SB101", departs="08:00", duration=180,
            aircraft_code="A320", economy=20000, business=60000,
        ),
        "early": insert_flight(
            conn, origin, destination, FAR_DAY, "NX202", airline=("NX", "Northwind"),
            departs="06:30", duration=150, stops=1, aircraft_code="B737",
            economy=15000, business=50000,
        ),
        "evening": insert_flight(
            conn, origin, destination, FAR_DAY, "SB303", departs="18:00", duration=95,
            aircraft_code="A320", economy=12000, business=40000,
        ),
    }


def search_results(client, route, day, **extra):
    origin, destination = route
    params = {"origin": origin, "destination": destination, "date": day.isoformat(), **extra}
    response = client.get("/api/flights", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- input validation --------------------------------------------------------


def test_valid_input_is_normalised():
    query, errors = valid(
        origin=" aaa ", destination="bbb", passengers_text="2", cabin="Business",
        sort="Duration", max_stops_text="1", airline=" sb ",
    )
    assert errors == {}
    assert query == search.FlightQuery(
        origin="AAA", destination="BBB", day=date(2026, 11, 20), passengers=2,
        cabin="business", sort="duration", max_stops=1, airline="SB",
    )


def test_origin_and_destination_must_differ():
    query, errors = valid(origin="AAA", destination="aaa")
    assert query is None
    assert "different" in errors["destination"]


def test_unknown_airport_codes_are_rejected():
    _, errors = valid(origin="ZZZ", destination="")
    assert errors["origin"] == "Unknown airport code ZZZ."
    assert errors["destination"] == "Choose a destination airport."


def test_missing_fields_are_reported_and_defaults_apply():
    query, errors = search.validate_query(
        None, None, None, None, None, known_airports=KNOWN, today=REF_TODAY
    )
    assert query is None
    assert set(errors) == {"origin", "destination", "date"}


def test_departure_date_cannot_be_in_the_past():
    assert "past" in valid(day_text="2026-11-17")[1]["date"]
    assert valid(day_text="2026-11-18")[1] == {}


def test_malformed_date_is_rejected():
    assert "YYYY-MM-DD" in valid(day_text="18/11/2026")[1]["date"]


@pytest.mark.parametrize("bad", ["0", "10", "-2", "two"])
def test_passenger_count_rejects_out_of_range_values(bad):
    assert "passengers" in valid(passengers_text=bad)[1]


@pytest.mark.parametrize("good", ["1", "9"])
def test_passenger_count_accepts_the_bounds(good):
    assert valid(passengers_text=good)[1] == {}


def test_cabin_must_be_economy_or_business():
    assert "cabin" in valid(cabin="first")[1]


def test_sort_and_max_stops_are_checked():
    assert "sort" in valid(sort="stars")[1]
    assert "max_stops" in valid(max_stops_text="-1")[1]
    assert "max_stops" in valid(max_stops_text="many")[1]
    assert valid(max_stops_text="0")[1] == {}


def test_parse_party_defaults():
    assert search.parse_party(None, None) == (1, "economy", {})


# --- round trips ------------------------------------------------------------


def test_round_trip_swaps_airports_for_the_return(conn, route):
    origin, destination = route
    plan, errors = search.plan_search(
        conn,
        {
            "origin": origin, "destination": destination, "date": "2026-11-20",
            "return_date": "2026-11-25", "passengers": "2", "cabin": "business",
        },
        REF_TODAY,
    )
    assert errors == {}
    assert plan.outbound.origin == origin
    assert plan.outbound.day == date(2026, 11, 20)
    assert plan.inbound.origin == destination
    assert plan.inbound.destination == origin
    assert plan.inbound.day == date(2026, 11, 25)
    assert plan.inbound.passengers == 2
    assert plan.inbound.cabin == "business"


def test_one_way_search_has_no_inbound(conn, route):
    origin, destination = route
    plan, errors = search.plan_search(
        conn, {"origin": origin, "destination": destination, "date": "2026-11-20"}, REF_TODAY
    )
    assert errors == {}
    assert plan.inbound is None


def test_return_date_cannot_be_before_departure(conn, route):
    origin, destination = route
    plan, errors = search.plan_search(
        conn,
        {"origin": origin, "destination": destination, "date": "2026-11-20",
         "return_date": "2026-11-19"},
        REF_TODAY,
    )
    assert plan is None
    assert "before the departure" in errors["return_date"]


def test_return_date_cannot_be_in_the_past(conn, route):
    origin, destination = route
    plan, errors = search.plan_search(
        conn,
        {"origin": origin, "destination": destination, "date": "2026-11-20",
         "return_date": "2026-11-17"},
        REF_TODAY,
    )
    assert plan is None
    assert "past" in errors["return_date"]


def test_malformed_return_date_is_rejected(conn, route):
    origin, destination = route
    plan, errors = search.plan_search(
        conn,
        {"origin": origin, "destination": destination, "date": "2026-11-20",
         "return_date": "25-11-2026"},
        REF_TODAY,
    )
    assert plan is None
    assert "YYYY-MM-DD" in errors["return_date"]


def test_outbound_errors_block_the_whole_plan(conn, route):
    origin, _ = route
    plan, errors = search.plan_search(
        conn,
        {"origin": origin, "destination": origin, "date": "2026-11-20",
         "return_date": "2026-11-25"},
        REF_TODAY,
    )
    assert plan is None
    assert "destination" in errors


# --- format helpers ----------------------------------------------------------


def test_format_helpers():
    assert search.format_duration(125) == "2h 05m"
    assert search.format_duration(45) == "0h 45m"
    assert search.format_clock("2026-11-20T07:05") == "07:05"
    assert search.format_day("2026-11-20T07:05") == "Fri 20 Nov 2026"
    assert search.format_cents(123456) == "$1,234.56"
    assert search.format_cents(5) == "$0.05"


# --- matching, sorting and filters ------------------------------------------


def test_search_is_directional(client, route, day_flights):
    origin, destination = route
    reverse = search_results(client, (destination, origin), FAR_DAY)
    assert reverse == []


def test_seeded_flights_are_found_by_route_and_day(client, conn):
    row = conn.execute(
        "SELECT origin, destination, substr(departs_local, 1, 10) AS day"
        " FROM flights ORDER BY id LIMIT 1"
    ).fetchone()
    day = date.fromisoformat(row["day"])
    body = search_results(client, (row["origin"], row["destination"]), day)
    assert body, "the seeded route should have flights on its day"
    for flight in body:
        assert flight["origin"] == row["origin"]
        assert flight["destination"] == row["destination"]
        assert flight["departs_local"].startswith(row["day"])
        assert flight["seats_left"] >= 1
    prices = [f["total_cents"] for f in body]
    assert prices == sorted(prices)


@pytest.mark.parametrize(
    "sort, expected",
    [
        ("price", ["SB303", "NX202", "SB101"]),
        ("departure", ["NX202", "SB101", "SB303"]),
        ("duration", ["SB303", "NX202", "SB101"]),
    ],
)
def test_api_sorts_results(client, route, day_flights, sort, expected):
    body = search_results(client, route, FAR_DAY, sort=sort)
    assert flight_numbers(body) == expected


def test_max_stops_filter(client, route, day_flights):
    body = search_results(client, route, FAR_DAY, max_stops="0")
    assert flight_numbers(body) == ["SB303", "SB101"]


def test_airline_filter_is_case_insensitive(client, route, day_flights):
    body = search_results(client, route, FAR_DAY, airline="nx")
    assert flight_numbers(body) == ["NX202"]


def test_filters_combine_and_can_match_nothing(client, route, day_flights):
    body = search_results(client, route, FAR_DAY, max_stops="0", airline="NX")
    assert body == []


def test_leg_total_counts_flights_before_filters(conn, route, day_flights):
    origin, destination = route
    leg = search.search_leg(
        conn, search.FlightQuery(origin=origin, destination=destination, day=FAR_DAY, max_stops=0)
    )
    assert leg["total"] == 3
    assert [f["flight_number"] for f in leg["flights"]] == ["SB303", "SB101"]
    assert leg["airlines"] == [("NX", "Northwind"), ("SB", "Skybook Air")]


# --- seats left and totals --------------------------------------------------


def test_seats_left_counts_taken_seats(conn, client, day_flights):
    take_seats(conn, day_flights["morning"], "A320", "economy", 154)
    detail = client.get(f"/api/flights/{day_flights['morning']}").json()
    assert detail["seats_left"] == 2
    assert detail["seats_left_by_cabin"] == {"economy": 2, "business": 16}


def test_party_larger_than_seats_left_is_hidden(conn, client, route, day_flights):
    take_seats(conn, day_flights["morning"], "A320", "economy", 154)
    two = flight_numbers(search_results(client, route, FAR_DAY, passengers="2"))
    three = flight_numbers(search_results(client, route, FAR_DAY, passengers="3"))
    assert "SB101" in two
    assert "SB101" not in three
    assert "NX202" in three


def test_full_cabin_is_not_searchable_but_other_cabin_is(conn, client, route, day_flights):
    take_seats(conn, day_flights["early"], "B737", "economy", 150)
    economy = flight_numbers(search_results(client, route, FAR_DAY, cabin="economy"))
    business = flight_numbers(search_results(client, route, FAR_DAY, cabin="business"))
    assert "NX202" not in economy
    assert "NX202" in business


def test_totals_include_taxes_for_each_passenger(client, route, day_flights):
    body = search_results(client, route, FAR_DAY, passengers="3", cabin="business")
    sb101 = {f["flight_number"]: f for f in body}["SB101"]
    assert sb101["fare_per_passenger_cents"] == 60000
    # 60000 base + 12% tax per passenger (7200 each), times 3 passengers.
    assert sb101["total_cents"] == 201600
    assert sb101["passengers"] == 3
    assert sb101["cabin"] == "business"


def test_flight_detail_returns_fares_and_totals(client, day_flights):
    response = client.get(
        f"/api/flights/{day_flights['early']}", params={"passengers": "2", "cabin": "business"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fares_cents"] == {"economy": 15000, "business": 50000}
    assert body["fare_per_passenger_cents"] == 50000
    # 2 x 50000 base + 12% tax per passenger (6000 each) = 112000.
    assert body["total_cents"] == 112000


def test_unknown_flight_is_404(client):
    assert client.get("/api/flights/999999").status_code == 404


def test_flight_detail_rejects_bad_party(client, day_flights):
    flight = day_flights["early"]
    zero = client.get(f"/api/flights/{flight}", params={"passengers": "0"})
    assert zero.status_code == 422
    assert "passengers" in zero.json()["errors"]
    first = client.get(f"/api/flights/{flight}", params={"cabin": "first"})
    assert first.status_code == 422
    assert "cabin" in first.json()["errors"]


# --- JSON API validation -----------------------------------------------------


def test_api_rejects_bad_input_with_field_errors(client):
    response = client.get(
        "/api/flights",
        params={"origin": "ZZZ", "destination": "ZZZ",
                "date": (TODAY - timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert {"origin", "destination", "date"} <= set(errors)


def test_api_requires_a_date(client, route):
    origin, destination = route
    response = client.get("/api/flights", params={"origin": origin, "destination": destination})
    assert response.status_code == 422
    assert "date" in response.json()["errors"]


def test_api_airports_list_is_sorted_by_city(client):
    body = client.get("/api/airports").json()
    cities = [airport["city"] for airport in body]
    assert cities == sorted(cities)


# --- HTML pages -------------------------------------------------------------


def test_search_page_lists_matching_flights(client, route, day_flights):
    origin, destination = route
    response = client.get(
        "/search", params={"origin": origin, "destination": destination,
                           "date": FAR_DAY.isoformat()}
    )
    assert response.status_code == 200
    assert "Outbound" in response.text
    assert "SB101" in response.text
    assert "NX202" in response.text
    assert f"/flights/{day_flights['morning']}?passengers=1&amp;cabin=economy" in response.text
    assert "No flights" not in response.text


def test_round_trip_page_shows_return_leg(client, route, day_flights):
    origin, destination = route
    response = client.get(
        "/search",
        params={"origin": origin, "destination": destination, "date": FAR_DAY.isoformat(),
                "return_date": (FAR_DAY + timedelta(days=3)).isoformat()},
    )
    assert response.status_code == 200
    assert "Return: " in response.text
    assert f"No flights from {destination} to {origin}" in response.text


def test_search_page_rejects_same_airport_with_422(client, route):
    origin, _ = route
    response = client.get(
        "/search", params={"origin": origin, "destination": origin,
                           "date": FAR_DAY.isoformat()}
    )
    assert response.status_code == 422
    assert "Please fix the highlighted fields" in response.text
    assert "Destination must be different" in response.text


def test_search_page_rejects_past_date_with_422(client, route):
    origin, destination = route
    response = client.get(
        "/search", params={"origin": origin, "destination": destination,
                           "date": (TODAY - timedelta(days=1)).isoformat()}
    )
    assert response.status_code == 422
    assert "cannot be in the past" in response.text


def test_search_page_with_no_input_shows_field_messages(client):
    response = client.get("/search")
    assert response.status_code == 422
    assert "Choose a departure airport." in response.text
    assert "Choose a departure date." in response.text


def test_empty_day_shows_empty_state(client, route):
    origin, destination = route
    response = client.get(
        "/search", params={"origin": origin, "destination": destination,
                           "date": (FAR_DAY + timedelta(days=10)).isoformat()}
    )
    assert response.status_code == 200
    assert f"No flights from {origin} to {destination}" in response.text


def test_filters_that_hide_everything_show_filter_message(client, route, day_flights):
    origin, destination = route
    response = client.get(
        "/search",
        params={"origin": origin, "destination": destination, "date": FAR_DAY.isoformat(),
                "max_stops": "0", "airline": "NX"},
    )
    assert response.status_code == 200
    assert "No flights match your filters." in response.text


def test_results_change_link_keeps_the_search(client, route, day_flights):
    origin, destination = route
    response = client.get(
        "/search", params={"origin": origin, "destination": destination,
                           "date": FAR_DAY.isoformat(), "passengers": "2"},
    )
    assert f'href="/?origin={origin}' in response.text


def test_index_prefills_from_query_string(client, conn):
    codes = [row["code"] for row in conn.execute("SELECT code FROM airports ORDER BY code")]
    origin, destination = codes[0], codes[1]
    response = client.get(
        "/",
        params={"origin": origin.lower(), "destination": destination,
                "date": FAR_DAY.isoformat(), "passengers": "3", "cabin": "business"},
    )
    assert response.status_code == 200
    assert f'value="{origin}" selected' in response.text
    assert f'value="{destination}" selected' in response.text
    assert f'value="{FAR_DAY.isoformat()}"' in response.text
    assert 'value="3" selected' in response.text
    assert 'value="business" selected' in response.text
