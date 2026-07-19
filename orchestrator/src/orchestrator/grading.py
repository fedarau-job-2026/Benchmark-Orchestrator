"""Answer grading: case-insensitive substring match (per the assignment brief)."""


def is_correct(expected: str, response: str) -> bool:
    return expected.strip().casefold() in response.casefold()
