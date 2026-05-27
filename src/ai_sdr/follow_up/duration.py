"""ISO-8601 duration -> timedelta. Thin wrapper over `isodate`."""

from __future__ import annotations

from datetime import timedelta

import isodate


def parse_duration(s: str) -> timedelta:
    """Parse an ISO-8601 duration string into a timedelta.

    Examples:
      "PT24H" -> 24h
      "P1D"   -> 1 day
      "P1W"   -> 7 days
      "PT2H30M" -> 2h30m

    Raises ValueError on invalid input (empty string, non-ISO format).
    Month/year-relative durations (P1M, P1Y) are rejected because they
    are not well-defined timedeltas without an anchor date.
    """
    if not s:
        raise ValueError("empty duration string")
    try:
        result = isodate.parse_duration(s)
    except isodate.ISO8601Error as e:
        raise ValueError(f"invalid ISO-8601 duration {s!r}: {e}") from e
    except Exception as e:
        raise ValueError(f"invalid ISO-8601 duration {s!r}: {e}") from e
    if isinstance(result, timedelta):
        return result
    raise ValueError(
        f"month/year-relative durations not supported (got {s!r}); "
        f"use weeks (P*W) or days (P*D)"
    )
