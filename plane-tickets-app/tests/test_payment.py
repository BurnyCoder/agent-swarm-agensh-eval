"""Mock card checks: only the test card is accepted, and rejections explain why.

These tests call ``payment.validate_card`` directly with a fixed ``today`` so
the expiry rules do not depend on the day the suite runs.
"""

from __future__ import annotations

from datetime import date

import pytest

from skybook import payment

TODAY = date(2026, 11, 18)
GOOD_CARD = "4242 4242 4242 4242"


def check(number=GOOD_CARD, expiry="12/30", cvc="123", today=TODAY):
    return payment.validate_card(number, expiry, cvc, today=today)


# --- accepted ----------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    ["4242 4242 4242 4242", "4242-4242-4242-4242", "4242424242424242"],
)
def test_test_card_is_accepted_in_any_grouping(number):
    assert check(number=number) == {}


@pytest.mark.parametrize("expiry", ["12/30", "01/27", "5/2031", " 07 / 28 "])
def test_any_future_expiry_is_accepted(expiry):
    assert check(expiry=expiry) == {}


@pytest.mark.parametrize("cvc", ["000", "123", "999", " 042 "])
def test_any_three_digit_cvc_is_accepted(cvc):
    assert check(cvc=cvc) == {}


def test_expiry_in_the_current_month_is_still_valid():
    assert check(expiry="11/26", today=date(2026, 11, 18)) == {}


# --- rejected: card number ---------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "4000 0000 0000 0002",  # a valid-looking card that is not the test card
        "5555 5555 5555 4444",
        "4242 4242 4242 4241",  # one digit off
    ],
)
def test_other_card_numbers_are_declined_with_a_helpful_message(number):
    errors = check(number=number)
    assert set(errors) == {"card_number"}
    assert "4242 4242 4242 4242" in errors["card_number"]
    assert "No real card is charged" in errors["card_number"]


def test_short_card_number_explains_the_length():
    errors = check(number="4242 4242 4242")
    assert set(errors) == {"card_number"}
    assert "16 digits" in errors["card_number"]


def test_card_number_with_letters_is_rejected():
    errors = check(number="4242 4242 4242 42x2")
    assert set(errors) == {"card_number"}
    assert "digits only" in errors["card_number"]


def test_empty_card_number_asks_for_one():
    assert check(number="")["card_number"] == "Enter the card number."


def test_error_messages_never_echo_the_card_number():
    errors = check(number="5555 5555 5555 4444")
    assert "5555" not in " ".join(errors.values())


# --- rejected: expiry --------------------------------------------------------


@pytest.mark.parametrize("expiry", ["10/26", "01/20", "11/2025"])
def test_past_expiry_is_rejected(expiry):
    errors = check(expiry=expiry)
    assert set(errors) == {"expiry"}
    assert "expired" in errors["expiry"]


@pytest.mark.parametrize("expiry", ["13/30", "00/30", "1/2", "2030", "ab/cd", "12-30"])
def test_malformed_expiry_is_rejected(expiry):
    errors = check(expiry=expiry)
    assert set(errors) == {"expiry"}
    assert "MM/YY" in errors["expiry"]


def test_empty_expiry_asks_for_one():
    assert check(expiry="")["expiry"] == "Enter the expiry date as MM/YY."


# --- rejected: cvc -----------------------------------------------------------


@pytest.mark.parametrize("cvc", ["12", "1234", "12a", "--1", "１２３"])
def test_cvc_that_is_not_three_digits_is_rejected(cvc):
    errors = check(cvc=cvc)
    assert set(errors) == {"cvc"}
    assert "exactly 3 digits" in errors["cvc"]


def test_empty_cvc_asks_for_one():
    assert "3-digit" in check(cvc="")["cvc"]


# --- several problems at once ------------------------------------------------


def test_every_bad_field_is_reported_together():
    errors = payment.validate_card("1111", "99/99", "1", today=TODAY)
    assert set(errors) == {"card_number", "expiry", "cvc"}


# --- parse_expiry ------------------------------------------------------------


def test_parse_expiry_reads_two_and_four_digit_years():
    assert payment.parse_expiry("03/29") == (2029, 3)
    assert payment.parse_expiry("03/2029") == (2029, 3)


def test_parse_expiry_rejects_bad_month():
    assert payment.parse_expiry("13/29") is None
    assert payment.parse_expiry("0/29") is None


# --- nothing is stored -------------------------------------------------------


def test_payment_module_keeps_no_state():
    # validate_card returns only messages. It has no storage and no logger.
    import inspect

    source = inspect.getsource(payment)
    for word in ("open(", "sqlite", "logging", "print("):
        assert word not in source
