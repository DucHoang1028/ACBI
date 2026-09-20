"""Deterministic, half-open calendar periods anchored to warehouse data."""

import re
import unicodedata
from datetime import date, timedelta


def date_hints(question: str) -> dict[str, str | None]:
    """Resolve only unambiguous calendar wording; leave comparisons to the model."""
    value = "".join(
        c
        for c in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(c) != "Mn"
    )
    aliases = {
        "this_month": r"\b(?:thang nay|this month)\b",
        "last_month": r"\b(?:thang truoc|last month)\b",
        "this_quarter": r"\b(?:quy nay|this quarter)\b",
        "last_quarter": r"\b(?:quy truoc|last quarter)\b",
        "this_year": r"\b(?:nam nay|this year)\b",
        "last_year": r"\b(?:nam truoc|last year)\b",
    }
    found = [key for key, pattern in aliases.items() if re.search(pattern, value)]
    if len(found) == 1 and not re.search(r"\b\d{4}\b", value):
        return {"period": found[0], "start_date": None, "end_date": None}
    # Anchor complete, single month/quarter references without interpreting ranges.
    month = re.fullmatch(
        r"\s*(?:thang\s+)(\d{1,2})\s*(?:nam\s+|/)(\d{4})[ .?!]*", value
    )
    quarter = re.fullmatch(r"\s*quy\s+([1-4])\s*(?:nam\s+|/)(\d{4})[ .?!]*", value)
    if month or quarter:
        match = month or quarter
        assert match is not None
        number, year = map(int, match.groups())
        if month and not 1 <= number <= 12:
            return {}
        start = date(year, number if month else (number - 1) * 3 + 1, 1)
        return {
            "period": "explicit",
            "start_date": start.isoformat(),
            "end_date": month_start(start, 1 if month else 3).isoformat(),
        }
    dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if len(dates) == 2:
        start, end = map(date.fromisoformat, dates)
        # People state calendar end dates inclusively; queries use half-open ranges.
        return {
            "period": "explicit",
            "start_date": start.isoformat(),
            "end_date": (end + timedelta(days=1)).isoformat(),
        }
    year = re.search(r"\b(?:ca nam|tu dau den cuoi nam|nam)\s*(\d{4})\b", value)
    if year:
        start = date(int(year.group(1)), 1, 1)
        return {
            "period": "explicit",
            "start_date": start.isoformat(),
            "end_date": date(start.year + 1, 1, 1).isoformat(),
        }
    return {}


def intent_hints(question: str) -> dict[str, object]:
    """Recognize complete, common business requests before asking the model."""
    value = "".join(
        c
        for c in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(c) != "Mn"
    )
    hints: dict[str, object] = date_hints(question)
    if re.search(r"\b(?:doanh thu|revenue)\b", value) and not re.search(
        r"\b(?:loi nhuan|profit)\b", value
    ):
        hints["metric_id"] = "revenue"
    if re.search(r"\b(?:so sanh|compare|chart)\b", value):
        months = list(
            re.finditer(r"\bthang\s*(\d{1,2})(?:\s*(?:nam\s*|/)(\d{4}))?", value)
        )
        years = [int(match.group(2)) for match in months if match.group(2)]
        if len(months) == 2 and years:
            year = years[-1]
            numbers = [int(match.group(1)) for match in months]
            if all(1 <= number <= 12 for number in numbers) and abs(
                numbers[0] - numbers[1]
            ) == 1:
                start_month = min(numbers)
                start = date(year, start_month, 1)
                hints.update(
                    dimension="month",
                    period="explicit",
                    start_date=start.isoformat(),
                    end_date=month_start(start, 2).isoformat(),
                )
    territories = [
        name
        for name in (
            "Canada", "Northwest", "Northeast", "Central", "Southwest", "Southeast",
            "France", "Germany", "Australia", "United Kingdom",
        )
        if re.search(rf"\b{re.escape(name.lower())}\b", value)
    ]
    if len(territories) >= 2 and re.search(r"\b(?:so sanh|compare)\b", value):
        hints["dimension"] = "sales_territory"
        hints["territory"] = "|".join(territories)
    return hints


def is_confirmation(question: str) -> bool:
    value = "".join(
        c
        for c in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(c) != "Mn"
    ).strip(" .!?")
    return value in {
        "dung",
        "đung",
        "dung vay",
        "đung vay",
        "dung vay so sanh di",
        "đung vay so sanh đi",
        "nhu vi du ay",
        "ok",
        "okay",
        "yes",
        "go ahead",
    }


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
