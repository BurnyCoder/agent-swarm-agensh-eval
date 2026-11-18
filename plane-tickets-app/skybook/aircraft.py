"""Aircraft seat layouts. Seat maps are generated from these definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cabin:
    """A run of rows sharing one fare class. ``blocks`` lists the seat letters
    between aisles, e.g. ``("ABC", "DEF")`` is a 3-3 economy layout."""

    name: str  # "economy" or "business"
    first_row: int
    last_row: int
    blocks: tuple[str, ...]

    def seats(self) -> list[str]:
        return [
            f"{row}{letter}"
            for row in range(self.first_row, self.last_row + 1)
            for block in self.blocks
            for letter in block
        ]


@dataclass(frozen=True)
class Aircraft:
    code: str
    name: str
    cabins: tuple[Cabin, ...]

    def cabin(self, name: str) -> Cabin:
        for cabin in self.cabins:
            if cabin.name == name:
                return cabin
        raise KeyError(f"{self.code} has no {name} cabin")

    def capacity(self, name: str) -> int:
        return len(self.cabin(name).seats())


AIRCRAFT: dict[str, Aircraft] = {
    "A320": Aircraft(
        code="A320",
        name="Airbus A320",
        cabins=(
            Cabin("business", 1, 4, ("AB", "CD")),
            Cabin("economy", 5, 30, ("ABC", "DEF")),
        ),
    ),
    "B737": Aircraft(
        code="B737",
        name="Boeing 737-800",
        cabins=(
            Cabin("business", 1, 3, ("AB", "CD")),
            Cabin("economy", 4, 28, ("ABC", "DEF")),
        ),
    ),
    "B789": Aircraft(
        code="B789",
        name="Boeing 787-9",
        cabins=(
            Cabin("business", 1, 6, ("AB", "CD", "EF")),
            Cabin("economy", 7, 40, ("ABC", "DEF", "HJK")),
        ),
    ),
}


def get_aircraft(code: str) -> Aircraft:
    return AIRCRAFT[code]


def seat_map(code: str, taken: set[str]) -> list[dict]:
    """Rows for the seat map UI. Each row lists its blocks of seats, and each
    seat says whether it is taken."""
    aircraft = get_aircraft(code)
    rows: list[dict] = []
    for cabin in aircraft.cabins:
        for row in range(cabin.first_row, cabin.last_row + 1):
            blocks = [
                [
                    {"id": f"{row}{letter}", "taken": f"{row}{letter}" in taken}
                    for letter in block
                ]
                for block in cabin.blocks
            ]
            rows.append({"row": row, "cabin": cabin.name, "blocks": blocks})
    return rows
