"""Responsive layout checks (issue #11).

The look at narrow widths is checked in a real browser during QA. These tests
guard the parts that can be checked without one: every page declares a mobile
viewport, the search, results and flight pages render their layout hooks, and
the stylesheet keeps the rules that stop the seat map from breaking the page.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from skybook import db
from skybook.app import create_app

STYLE = Path(__file__).resolve().parents[1] / "skybook" / "static" / "style.css"


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "skybook.db"
    db.init_db(path, today=date.today())
    return path


@pytest.fixture()
def client(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture()
def seeded_flight(db_path):
    """A real seeded flight, so the results and flight pages have data to show."""
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, origin, destination, departs_local FROM flights ORDER BY departs_local LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row


def seed_booking(db_path, flight_id, *, reference="RSP001", status="confirmed"):
    """Insert a one-passenger booking on a free seat of the flight (issue #21 QA)."""
    conn = db.connect(db_path)
    try:
        taken = {row[0] for row in conn.execute(
            "SELECT seat FROM taken_seats WHERE flight_id = ?", (flight_id,)
        )}
        seat = next(
            f"{row}{column}" for row in range(1, 40) for column in "ABCDEF"
            if f"{row}{column}" not in taken
        )
        cursor = conn.execute(
            "INSERT INTO bookings (reference, flight_id, cabin, passengers, status, lead_email,"
            " total_cents, created_at) VALUES (?, ?, 'economy', 1, ?, 'ada@example.com', 45600,"
            " '2026-01-01T00:00:00Z')",
            (reference, flight_id, status),
        )
        conn.execute(
            "INSERT INTO passengers (booking_id, first_name, last_name, date_of_birth, seat, email)"
            " VALUES (?, 'Ada', 'Lovelace', '1990-12-10', ?, 'ada@example.com')",
            (cursor.lastrowid, seat),
        )
        if status == "confirmed":
            conn.execute(
                "INSERT INTO taken_seats (flight_id, seat, cabin, booking_id) VALUES (?, ?, 'economy', ?)",
                (flight_id, seat, cursor.lastrowid),
            )
        conn.commit()
    finally:
        conn.close()
    return reference


def css_block(css: str, selector: str) -> str:
    """Return the body of the first rule whose selector line starts with `selector`."""
    match = re.search(rf"^{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.MULTILINE)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_search_page_declares_a_mobile_viewport(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text
    assert "width=device-width" in response.text


def test_results_page_declares_a_mobile_viewport_and_renders_cards(client, seeded_flight):
    query = urlencode(
        {
            "origin": seeded_flight["origin"],
            "destination": seeded_flight["destination"],
            "date": seeded_flight["departs_local"][:10],
            "passengers": 1,
            "cabin": "economy",
        }
    )
    response = client.get(f"/search?{query}")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text
    assert "flight-card" in response.text
    assert "filter-form" in response.text


def test_flight_page_declares_a_mobile_viewport_and_renders_seat_map(client, seeded_flight):
    response = client.get(
        f"/flights/{seeded_flight['id']}", params={"passengers": 2, "cabin": "economy"}
    )
    assert response.status_code == 200
    body = response.text
    assert 'name="viewport"' in body
    assert "flight-layout" in body
    assert 'class="seat-map"' in body
    assert 'role="group"' in body


def test_seat_map_scrolls_inside_its_own_box():
    css = STYLE.read_text(encoding="utf-8")
    seat_map = css_block(css, ".seat-map")
    assert "overflow-x: auto" in seat_map
    assert "max-width: 100%" in seat_map
    assert "align-items: center" not in seat_map  # centring would push the left edge out of reach


def test_seat_rows_keep_their_width_so_the_box_can_scroll():
    css = STYLE.read_text(encoding="utf-8")
    seat_row = css_block(css, ".seat-row")
    assert "width: max-content" in seat_row


def test_flight_layout_lets_the_seat_panel_shrink():
    css = STYLE.read_text(encoding="utf-8")
    assert "minmax(0, 1fr)" in css_block(css, ".flight-layout")
    assert "min-width: 0" in css_block(css, ".flight-layout > *")


def test_seat_targets_are_at_least_44_pixels():
    css = STYLE.read_text(encoding="utf-8")
    seat = css_block(css, ".seat")
    assert "width: 44px" in seat
    assert "height: 44px" in seat


def test_nav_links_and_small_buttons_are_at_least_44_pixels():
    css = STYLE.read_text(encoding="utf-8")
    assert "min-height: 44px" in css_block(css, ".site-nav a")
    assert "min-height: 44px" in css_block(css, ".button.small")


def test_manage_lookup_page_declares_a_mobile_viewport(client):
    response = client.get("/bookings")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text
    assert 'name="reference"' in response.text
    assert 'name="last_name"' in response.text


def test_booking_page_declares_a_mobile_viewport_and_labels_passenger_cells(
    client, db_path, seeded_flight
):
    reference = seed_booking(db_path, seeded_flight["id"])
    response = client.get(f"/bookings/{reference}", params={"last_name": "Lovelace"})
    assert response.status_code == 200
    body = response.text
    assert 'name="viewport"' in body
    assert 'class="booking-table"' in body
    # The stacked narrow-screen layout shows these labels in front of each cell.
    assert 'data-label="Date of birth"' in body
    assert 'data-label="Seat"' in body
    assert 'class="button danger"' in body  # confirmed bookings can be cancelled


def test_cancelled_booking_page_renders_without_a_cancel_button(client, db_path, seeded_flight):
    reference = seed_booking(db_path, seeded_flight["id"], reference="RSP002", status="cancelled")
    response = client.get(f"/bookings/{reference}", params={"last_name": "Lovelace"})
    assert response.status_code == 200
    assert 'name="viewport"' in response.text
    assert 'class="button danger"' not in response.text
    assert 'data-label="Seat"' in response.text


def test_booking_table_stacks_into_labelled_blocks_on_narrow_screens():
    css = STYLE.read_text(encoding="utf-8")
    assert "@media (max-width: 559px)" in css
    assert "content: attr(data-label)" in css
    # The header row is hidden visually but kept for screen readers.
    assert "clip: rect(0 0 0 0)" in css


def test_header_keeps_side_padding_on_every_width():
    css = STYLE.read_text(encoding="utf-8")
    assert "padding: 12px 16px" in css_block(css, ".header-inner")


def test_form_actions_wrap_and_buttons_stack_full_width_on_phones():
    css = STYLE.read_text(encoding="utf-8")
    actions = css_block(css, ".form-actions")
    assert "flex-wrap: wrap" in actions
    assert "gap: 8px" in actions
    # Under 560px every action button takes a full row, so the labels never squeeze.
    phone_rule = re.search(
        r"@media \(max-width: 559px\)\s*\{\s*\.form-actions \.button\s*\{([^}]*)\}", css
    )
    assert phone_rule, "no full-width form-action rule under 560px"
    assert "flex: 1 1 100%" in phone_rule.group(1)


def test_checkout_layout_is_one_column_then_two_columns_on_desktop():
    css = STYLE.read_text(encoding="utf-8")
    assert "display: grid" in css_block(css, ".checkout-layout")
    desktop = css[css.index("@media (min-width: 960px)"):]
    assert "grid-template-columns: minmax(0, 1fr) 320px" in desktop
    assert "position: sticky" in desktop  # the fare summary stays in view while scrolling


def test_checkout_columns_can_shrink_below_their_content():
    css = STYLE.read_text(encoding="utf-8")
    assert "min-width: 0" in css_block(css, ".checkout-form")
    assert "min-width: 0" in css_block(css, ".checkout-summary")


def test_checkout_buttons_are_at_least_44_pixels():
    css = STYLE.read_text(encoding="utf-8")
    assert "min-height: 44px" in css_block(css, ".button")


def free_economy_seats(db_path, flight_id, count):
    """Pick `count` untaken economy seats, skipping the business rows at the front."""
    conn = db.connect(db_path)
    try:
        taken = {row[0] for row in conn.execute(
            "SELECT seat FROM taken_seats WHERE flight_id = ?", (flight_id,)
        )}
    finally:
        conn.close()
    seats = [f"{row}{column}" for row in range(10, 40) for column in "ABCDEF"]
    return [seat for seat in seats if seat not in taken][:count]


def test_passengers_and_payment_pages_use_the_checkout_layout(client, db_path, seeded_flight):
    chosen = free_economy_seats(db_path, seeded_flight["id"], 2)
    form = {
        "passengers": "2",
        "cabin": "economy",
        "seat": chosen,
        "first_name": ["Ada", "Grace"],
        "last_name": ["Lovelace", "Hopper"],
        "date_of_birth": ["1990-12-10", "1985-06-02"],
        "email": "ada@example.com",
    }
    response = client.post(f"/book/{seeded_flight['id']}/passengers", data=form, follow_redirects=False)
    assert response.status_code == 200, response.headers.get("location")
    body = response.text
    assert 'name="viewport"' in body
    assert "checkout-layout" in body
    assert "checkout-summary" in body
    assert "form-actions" in body
    assert 'id="pay-submit"' in body
