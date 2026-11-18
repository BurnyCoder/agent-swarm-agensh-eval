"""Fare breakdown: base fare, taxes and total, in integer cents.

Taxes are a flat percentage of each passenger's base fare, rounded down to the
whole cent per passenger. Totals are then the sum over the party, so the
per-passenger figures always add up to the party figures.
"""

from __future__ import annotations

TAX_PERCENT = 12


def fare_breakdown(fare_per_passenger_cents: int, passengers: int) -> dict:
    taxes_each = fare_per_passenger_cents * TAX_PERCENT // 100
    base = fare_per_passenger_cents * passengers
    taxes = taxes_each * passengers
    return {
        "passengers": passengers,
        "tax_percent": TAX_PERCENT,
        "per_passenger": {
            "base_cents": fare_per_passenger_cents,
            "taxes_cents": taxes_each,
            "total_cents": fare_per_passenger_cents + taxes_each,
        },
        "base_cents": base,
        "taxes_cents": taxes,
        "total_cents": base + taxes,
    }
