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
    # Exactly one month or quarter, stated with a year and not compared with another.
    month_mentions = re.findall(r"\bthang\s*\d{1,2}\b", value)
    quarter_mentions = re.findall(r"\bquy\s*[1-4]\b", value)
    comparing = re.search(r"\b(?:so sanh|so voi|compare|vs)\b", value)
    month = quarter = None
    if not comparing and len(month_mentions) + len(quarter_mentions) == 1:
        month = re.search(r"\bthang\s+(\d{1,2})\s*(?:nam\s+|/)(\d{4})\b", value)
        quarter = re.search(r"\bquy\s+([1-4])\s*(?:nam\s+|/)(\d{4})\b", value)
    if month or quarter:
        match = month or quarter
        assert match is not None
        number, match_year = map(int, match.groups())
        if month and not 1 <= number <= 12:
            return {}
        start = date(match_year, number if month else (number - 1) * 3 + 1, 1)
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
    named_year = re.search(r"\b(?:ca nam|tu dau den cuoi nam|nam)\s*(\d{4})\b", value)
    if named_year:
        start = date(int(named_year.group(1)), 1, 1)
        return {
            "period": "explicit",
            "start_date": start.isoformat(),
            "end_date": date(start.year + 1, 1, 1).isoformat(),
        }
    return {}


TERRITORIES = {
    "Canada": "canada",
    "Northwest": "northwest",
    "Northeast": "northeast",
    "Central": "central",
    "Southwest": "southwest",
    "Southeast": "southeast",
    "France": "france|phap",
    "Germany": "germany|duc",
    "Australia": "australia|uc",
    "United Kingdom": "united kingdom|vuong quoc anh|nuoc anh|anh",
}
SHARE = r"phan tram|chiem bao nhieu|ty trong|share|percent"


STACK = r"\b(?:xep chong|cot chong|stacked)\b"
DIMENSION_WORDS = {
    "month": r"(?:theo|moi|hang|tung) thang|by month|monthly",
    "sales_territory": r"khu vuc|territor(?:y|ies)|regions?",
    "product_category": r"danh muc|nhom san pham|loai san pham|categor(?:y|ies)",
    "production_line": r"day chuyen|production lines?",
    "factory": r"nha may|factory|factories",
}


def stacked_dimensions(value: str) -> tuple[str, str] | None:
    """Two breakdowns named in order, e.g. 'by month and territory'."""
    found = sorted(
        (match.start(), name)
        for name, pattern in DIMENSION_WORDS.items()
        if (match := re.search(rf"\b(?:{pattern})\b", value))
    )
    if len(found) < 2:
        return None
    first, second = found[0][1], found[1][1]
    return (second, first) if second == "month" else (first, second)


METRIC_WORDS = (
    r"(?<![\w.])(?:doanh thu|doanh so|revenue|sales|san luong|ty le|phe pham|"
    r"scrap|defect|loi nhuan|profit|tang truong|growth|bao nhieu tien)(?![\w.])"
)


def territory_names(value: str) -> list[str] | None:
    """Canonical territory names found in free text; None when some word is unknown."""
    folded = fold(value)
    names = [
        name
        for name, pattern in TERRITORIES.items()
        if re.search(rf"\b(?:{pattern})\b", folded)
    ]
    for pattern in TERRITORIES.values():
        folded = re.sub(rf"\b(?:{pattern})\b", " ", folded)
    leftover = re.sub(r"\b(?:va|and|voi|,|&)\b|[|,&]", " ", folded).split()
    return names if names and not leftover else None


def fold(question: str) -> str:
    """Lowercase, accent-free text for keyword matching."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(c) != "Mn"
    ).replace("đ", "d")


def single_dimension(value: str) -> str | None:
    """'none' when no breakdown is named, the name when one is, None if unclear."""
    words = {
        **DIMENSION_WORDS,
        "product": r"san pham|product",
        "scrap_reason": r"ly do|scrap reason",
    }
    found = {
        name
        for name, pattern in words.items()
        if re.search(rf"\b(?:{pattern})\b", value)
    }
    if "product_category" in found:
        found.discard("product")
    if len(found) > 1:
        return None
    return next(iter(found), "none")


def is_share_question(question: str) -> bool:
    value = "".join(
        c
        for c in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(c) != "Mn"
    ).replace("đ", "d")
    return bool(re.search(rf"\b(?:{SHARE})\b", value))


def intent_hints(question: str) -> dict[str, object]:
    """Recognize complete, common business requests before asking the model."""
    value = "".join(
        c
        for c in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(c) != "Mn"
    ).replace("đ", "d")
    hints: dict[str, object] = {**date_hints(question)}
    growth = (
        r"\b(?:tang truong|growth|(?:tang|giam) bao nhieu"
        r"|so voi (?:thang|quy|ky) (?:lien )?truoc)\b"
    )
    found: list[str] = []
    if re.search(r"(?<![\w.])(?:doanh thu|doanh so|revenue|sales)(?![\w.])", value):
        found.append("sales_growth" if re.search(growth, value) else "revenue")
    if re.search(r"\b(?:san luong|san xuat|production output)\b", value):
        found.append("production_output")
    if re.search(r"\b(?:ty le loi|ty le phe pham|phe pham|scrap|defect)\b", value):
        found.append("defect_rate")
    # Profit has no approved definition; several metrics need a clarification.
    if len(found) == 1 and not re.search(r"\b(?:loi nhuan|profit)\b", value):
        hints["metric_id"] = found[0]
    if re.search(r"\b(?:so sanh|compare|chart|so voi)\b", value):
        months = list(
            re.finditer(r"\bthang\s*(\d{1,2})(?:\s*(?:nam\s*|/)(\d{4}))?", value)
        )
        years = [int(match.group(2)) for match in months if match.group(2)]
        if len(months) == 2 and years:
            year = years[-1]
            numbers = [int(match.group(1)) for match in months]
            if (
                all(1 <= number <= 12 for number in numbers)
                and abs(numbers[0] - numbers[1]) == 1
            ):
                start_month = min(numbers)
                start = date(year, start_month, 1)
                hints.update(
                    dimension="month",
                    period="explicit",
                    start_date=start.isoformat(),
                    end_date=month_start(start, 2).isoformat(),
                )
    if re.search(STACK, value) and (pair := stacked_dimensions(value)):
        hints.update(dimension=pair[0], series_dimension=pair[1], limit=250)
    territories = [
        name
        for name, pattern in TERRITORIES.items()
        if re.search(rf"\b(?:{pattern})\b", value)
    ]
    if territories and re.search(rf"\b(?:so sanh|compare|{SHARE})\b", value):
        if len(territories) >= 2 or re.search(rf"\b(?:{SHARE})\b", value):
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
