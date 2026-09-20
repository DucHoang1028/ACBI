"""Deterministic, half-open calendar periods anchored to warehouse data."""

from datetime import date, timedelta


def month_start(day: date, delta: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def resolve_period(name: str, anchor: date) -> tuple[date, date]:
    tomorrow = anchor + timedelta(days=1)
    if name == "this_month":
        return month_start(anchor), tomorrow
    if name == "last_month":
        return month_start(anchor, -1), month_start(anchor)
    if name == "this_year":
        return date(anchor.year, 1, 1), tomorrow
    if name == "last_year":
        return date(anchor.year - 1, 1, 1), date(anchor.year, 1, 1)
    quarter = date(anchor.year, 3 * ((anchor.month - 1) // 3) + 1, 1)
    if name == "this_quarter":
        return quarter, tomorrow
    if name == "last_quarter":
        return month_start(quarter, -3), quarter
    if name == "last_30_days":
        return tomorrow - timedelta(days=30), tomorrow
    raise ValueError("Unresolved period; 'recently' requires clarification")
