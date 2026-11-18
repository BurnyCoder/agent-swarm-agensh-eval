"""Mock card check for the checkout step. No real payments happen here.

Only the test card ``4242 4242 4242 4242`` is accepted. The expiry must be a
future month (``MM/YY`` or ``MM/YYYY``) and the CVC must be three digits. Card
details are checked and then dropped: this module returns error messages and
never returns, logs or stores the card number, expiry or CVC.
"""

from __future__ import annotations

import re
from datetime import date

TEST_CARD_NUMBER = "4242424242424242"
CVC_LENGTH = 3

_EXPIRY_RE = re.compile(r"^([0-9]{1,2})\s*/\s*([0-9]{2}|[0-9]{4})$")
_ASCII_DIGITS = re.compile(r"[0-9]+")


def _digits(text: str) -> str:
    """Remove the spaces and dashes people type between card number groups."""
    return re.sub(r"[\s-]", "", text or "")


def parse_expiry(text: str) -> tuple[int, int] | None:
    """Return ``(year, month)`` for ``MM/YY`` or ``MM/YYYY``, or ``None`` if malformed."""
    match = _EXPIRY_RE.match((text or "").strip())
    if not match:
        return None
    month = int(match.group(1))
    year = int(match.group(2))
    if year < 100:
        year += 2000
    if not 1 <= month <= 12:
        return None
    return year, month


def validate_card(
    number: str, expiry: str, cvc: str, today: date | None = None
) -> dict[str, str]:
    """Check the mock card. Return field errors keyed by field; empty means accepted.

    A card stays valid through the end of its expiry month, so a card that
    expires this month is still accepted.
    """
    today = today or date.today()
    errors: dict[str, str] = {}

    digits = _digits(number)
    if not digits:
        errors["card_number"] = "Enter the card number."
    elif not _ASCII_DIGITS.fullmatch(digits):
        errors["card_number"] = "Card numbers contain digits only."
    elif len(digits) != len(TEST_CARD_NUMBER):
        errors["card_number"] = (
            "Card numbers have 16 digits. This demo accepts only the test card "
            "4242 4242 4242 4242."
        )
    elif digits != TEST_CARD_NUMBER:
        errors["card_number"] = (
            "This card was declined. In this demo only the test card "
            "4242 4242 4242 4242 works. No real card is charged."
        )

    parsed = parse_expiry(expiry)
    if not (expiry or "").strip():
        errors["expiry"] = "Enter the expiry date as MM/YY."
    elif parsed is None:
        errors["expiry"] = "Expiry must look like MM/YY, for example 12/30."
    elif parsed < (today.year, today.month):
        errors["expiry"] = (
            "This card has expired. Use a card that expires this month or later."
        )

    cvc_digits = (cvc or "").strip()
    if not cvc_digits:
        errors["cvc"] = "Enter the 3-digit security code on the back of the card."
    elif not _ASCII_DIGITS.fullmatch(cvc_digits) or len(cvc_digits) != CVC_LENGTH:
        errors["cvc"] = "The security code (CVC) is exactly 3 digits."

    return errors
