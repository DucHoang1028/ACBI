"""Deterministic, half-open calendar periods anchored to warehouse data."""

import re
from datetime import date, timedelta

from app.core.text import fold
from app.metadata import vocabulary


def date_hints(question: str) -> dict[str, str | None]:
    """Resolve only unambiguous calendar wording; leave comparisons to the model."""
    value = re.sub(r"\bq([1-4])\b", r"quy \1", fold(question))  # Q2 reads as quý 2
    # "from 2022 until now" is a range the backend cannot close without the
    # anchor: the model resolves it against context.data_as_of.
    if re.search(r"\b(?:den nay|toi nay|den gio|to now|until now|to date)\b", value):
        return {}
    aliases = {
        "today": r"\b(?:hom nay|today)\b",
        "yesterday": r"\b(?:hom qua|yesterday)\b",
        "this_week": r"\b(?:tuan nay|this week)\b",
        "last_week": r"\b(?:tuan (?:truoc|ngoai|vua roi|vua qua)|last week)\b",
        "this_month": r"\b(?:thang nay|this month)\b",
        "last_month": r"\b(?:thang (?:truoc|ngoai|vua roi|vua qua)|last month)\b",
        "this_quarter": r"\b(?:quy nay|this quarter)\b",
        "last_quarter": r"\b(?:quy (?:truoc|ngoai|vua roi|vua qua)|last quarter)\b",
        "this_year": r"\b(?:nam nay|this year)\b",
        "last_year": r"\b(?:nam (?:truoc|ngoai|vua roi|vua qua)|last year)\b",
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
        month = re.search(r"\bthang\s+(\d{1,2})\s*(?:nam\s+|/|\s)(\d{4})\b", value)
        quarter = re.search(r"\bquy\s+([1-4])\s*(?:nam\s+|/|\s)(\d{4})\b", value)
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


SHARE = r"phan tram|chiem bao nhieu|ty trong|share|percent|trong tong|ty le tren tong"
FORMAT_REQUEST = (
    r"\b(?:(?:tinh bang|bang|don vi|in)\s+(?:trieu|nghin|ty|million|billion|thousand)s?"
    r"|lam tron|round(?:ed|ing)?)\b"
)

STACK = r"\b(?:xep chong|cot chong|stacked)\b"
WHY = r"\b(?:tai sao|vi sao|nguyen nhan|why)\b"
GROWTH = (
    r"\b(?:tang truong|growth|grew|(?:tang|giam) bao nhieu"
    r"|so voi (?:thang|quy|ky) (?:lien )?truoc"
    r"|compared (?:to|with) the previous (?:month|quarter|period))\b"
)


def metric_words() -> str:
    """Regex for any approved metric name or synonym, from the dictionary."""
    return vocabulary.get().metric_words()


def stacked_dimensions(value: str) -> tuple[str, str] | None:
    """Two breakdowns named in order, e.g. 'by month and territory'."""
    named = vocabulary.get().match_dimensions(value)
    if len(named) < 2:
        return None
    first, second = named[0], named[1]
    return (second, first) if second == "month" else (first, second)


def territory_names(value: str) -> list[str] | None:
    """Canonical territories in free text; None when some word is unknown."""
    return vocabulary.get().member_names(fold(value), "sales_territory")


def single_dimension(value: str) -> str | None:
    """'none' when no breakdown is named, the name when one is, None if unclear."""
    named = vocabulary.get().match_dimensions(value)
    return "none" if not named else named[0] if len(named) == 1 else None


def is_share_question(question: str) -> bool:
    return "%" in question or bool(re.search(rf"\b(?:{SHARE})\b", fold(question)))


def intent_hints(question: str) -> dict[str, object]:
    """Deterministic readings of a question: dates always, other fields as fallback."""
    value = fold(question)
    vocab = vocabulary.get()
    hints: dict[str, object] = {**date_hints(question)}
    found = vocab.match_metrics(value)
    growth_wording = re.search(GROWTH, value)
    if growth_wording and "revenue" in found and "sales_growth" not in found:
        found = ["sales_growth" if m == "revenue" else m for m in found]
        hints["metric_override"] = True
    if "sales_growth" in found and "revenue" in found:
        found.remove("revenue")
    if len(found) == 1:
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
    named = vocab.match_members(value).get("sales_territory", [])
    if named:
        hints["territory"] = "|".join(named)
        asks_comparison = re.search(rf"\b(?:so sanh|compare|{SHARE})\b", value)
        if "dimension" not in hints and (len(named) >= 2 or asks_comparison):
            hints["dimension"] = "sales_territory"  # one row per named territory
    return hints


# Calendar units are language, not schema: they do not change with the database.
# The two languages are matched separately: folding "đây" gives "day", which would
# otherwise collide with the English unit.
UNITS_VI = r"\b(?:ngay|tuan|thang|quy|nam|ky)\b"
UNITS_EN = r"\b(?:day|week|month|quarter|year|period)s?\b"


def mentions_time(question: str) -> bool:
    """True when the question itself names a date, a period or a number of them.

    A follow-up that names none is asking about the answer already given, not for
    a new period, even when the model copies one from the conversation."""
    return bool(
        re.search(r"\d", question)
        or re.search(UNITS_VI, fold(question))
        or re.search(UNITS_EN, question.lower())
        or date_hints(question)
    )


def is_confirmation(question: str) -> bool:
    value = fold(question).strip(" .!?")
    return value in {
        "dung",
        "dung vay",
        "dung vay so sanh di",
        "nhu vi du ay",
        "ok",
        "okay",
        "yes",
        "go ahead",
    }


COMPARE_WORDS = (
    r"\b(?:so voi|so sanh|compare[ds]?|comparison|vs|versus|tang truong|growth|"
    r"chenh lech|tang bao nhieu|giam bao nhieu|thay doi|change[sd]?|difference|"
    r"between|giua|khac nhau|khac gi)\b"
)


MONTH_NAMES = (
    "january february march april may june july august september october november "
    "december"
).split()
EN_MONTHS = "|".join(MONTH_NAMES + [m[:3] for m in MONTH_NAMES] + ["sept"])
PERIOD = re.compile(
    r"\b(?:(?:quy|quarter)\s*(?P<q>[1-4])(?:\s*(?:nam|/|of|,))?\s*(?P<qy>20\d{2})?"
    r"|thang\s*(?P<m>\d{1,2})(?:\s*(?:nam|/))?\s*(?P<my>20\d{2})?"
    rf"|(?P<em>{EN_MONTHS})\.?\s*,?\s*(?P<ey>20\d{{2}})"
    r"|(?P<y>20\d{2}))\b"
)
Period = tuple[str, date, date]  # kind, first day, day after the last


def named_periods(question: str) -> list[Period]:
    """Every distinct year, quarter or month named in the question, oldest first.

    A quarter or month with no year of its own takes the last year mentioned
    ("Q1 and Q2 2025"); with no year anywhere it is not a period we can place."""
    value = re.sub(r"\bq([1-4])\b", r"quy \1", fold(question))
    years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", value)
    default = int(years[-1]) if years else None
    found: dict[tuple[str, date], date] = {}
    for match in PERIOD.finditer(value):
        if match["q"]:
            year = int(match["qy"]) if match["qy"] else default
            if year is None:
                continue
            kind, start = "quarter", date(year, int(match["q"]) * 3 - 2, 1)
            end = month_start(start, 3)
        elif match["m"] or match["em"]:
            number = (
                int(match["m"])
                if match["m"]
                else next(
                    i
                    for i, name in enumerate(MONTH_NAMES, 1)
                    if name.startswith(match["em"][:3])
                )
            )
            year = int(match["my"] or match["ey"] or 0) or default
            if year is None or not 1 <= number <= 12:
                continue
            kind, start = "month", date(year, number, 1)
            end = month_start(start, 1)
        else:
            kind, start = "year", date(int(match["y"]), 1, 1)
            end = date(start.year + 1, 1, 1)
        found[(kind, start)] = end
    return sorted(((k, s, e) for (k, s), e in found.items()), key=lambda p: p[1])


def impossible_date(question: str) -> str | None:
    """A month or day that the calendar does not have ("tháng 13", "31/02/2024").

    Returned as the wording to quote back; the question must be asked about, since a
    model may quietly turn it into a real date."""
    value = fold(question)
    if match := re.search(r"\b(?:thang|month)\s*(\d{1,2})\b", value):
        if not 1 <= int(match.group(1)) <= 12:
            # folding keeps the length, so the same span is the user's own wording
            same = len(value) == len(question)
            return question[match.start() : match.end()] if same else match.group(0)
    for day, month, year in re.findall(
        r"(?<![\d/-])(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(20\d{2}))?(?![\d/-])",
        value,
    ):
        y = int(year) if year else 2024
        try:
            date(y, int(month), int(day))
        except ValueError:
            return f"{day}/{month}" + (f"/{year}" if year else "")
    for year, month, day in re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})\b", value):
        try:
            date(int(year), int(month), int(day))
        except ValueError:
            return f"{year}-{month}-{day}"
    return None


def conflicting_years(question: str) -> list[str]:
    """Two different years named with nothing joining them ("doanh thu 2025 năm 2024").

    A person may mean either; with "và", "đến", "so với" or a comma the message says
    what it does with them, so only the bare clash is reported."""
    value = fold(question)
    years = sorted(set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", value)))
    joined = re.search(
        r"\b(?:va|and|den|toi|to|vs|so|voi|tu|from|giua|between|ca)\b|[,;/-]", value
    )
    return years if len(years) >= 2 and not joined else []


def compares_two_periods(question: str) -> bool:
    """Two or more named years, quarters or months set against each other.

    "Q1 2025 so với Q1 2024" or "so sánh doanh thu 2023 và 2024". Growth exists only
    for the latest month or quarter, so these are answered side by side instead."""
    value = fold(question)
    is_range = (
        re.search(r"\b(?:tu|from)\b.*\b(?:den|to)\b", value)
        or re.search(r"\b(?:den|until|through)\b", value)
        or re.search(r"\d{4}-\d{2}-\d{2}", value)
    )
    return (
        bool(re.search(COMPARE_WORDS, value))
        and not is_range
        and len(named_periods(question)) >= 2
    )


def month_start(day: date, delta: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


YEAR_AGO = (
    r"\bcung ky (?:nam (?:truoc|ngoai)|truoc)\b|\bsame period (?:last|a) year\b|"
    r"\byear (?:ago|earlier)\b"
)
BEFORE_THAT = (
    r"\b(?P<unit>ky|quy|thang|nam|tuan) truoc do(?: nua)?\b|\btruoc do nua\b|"
    r"\bthe (?:period|one) before(?: that)?\b"
)


EN_BEFORE = (
    r"\b(?:the (?P<a>year|quarter|month|week|period) before|"
    r"previous (?P<b>year|quarter|month|week|period))\b"
)
EN_UNITS = {"year": "nam", "quarter": "quy", "month": "thang", "week": "tuan"}


def _minus_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def shifted_period(
    question: str, slots: dict[str, object], anchor: date
) -> tuple[date, date] | None:
    """"Cùng kỳ năm trước" or "quý trước đó nữa": the earlier answer's period, moved.

    The year-ago wording moves the whole window back a year; the "before that"
    wording takes the unit (or the same span) just before the window's start."""
    value = fold(question)
    year_ago = re.search(YEAR_AGO, value)
    before = re.search(BEFORE_THAT, value)
    english = re.search(EN_BEFORE, value)
    if not (year_ago or before or english):
        return None
    try:
        if slots.get("period") == "explicit":
            start = date.fromisoformat(str(slots["start_date"]))
            end = date.fromisoformat(str(slots["end_date"]))
        else:
            start, end = resolve_period(str(slots["period"]), anchor)
    except (KeyError, ValueError):
        return None
    if year_ago:
        return _minus_year(start), _minus_year(end)
    if english:
        word = english.group("a") or english.group("b")
        unit = EN_UNITS.get(word)
    else:
        assert before is not None
        unit = before.groupdict().get("unit")
    if unit == "quy":
        return month_start(start, -3), start
    if unit == "thang":
        return month_start(start, -1), start
    if unit == "nam":
        return date(start.year - 1, start.month, start.day), start
    if unit == "tuan":
        return start - timedelta(days=7), start
    if start.day == 1:  # the same calendar unit again, when the window is one
        if end == month_start(start, 1):
            return month_start(start, -1), start
        if end == month_start(start, 3) and start.month in (1, 4, 7, 10):
            return month_start(start, -3), start
        if end == date(start.year + 1, 1, 1) and start.month == 1:
            return date(start.year - 1, 1, 1), start
    return start - (end - start), start


def moves_period(question: str) -> bool:
    """True for "cùng kỳ năm trước" or "kỳ trước đó": it says something on its own."""
    value = fold(question)
    return bool(
        re.search(YEAR_AGO, value)
        or re.search(BEFORE_THAT, value)
        or re.search(EN_BEFORE, value)
    )


def resolve_period(name: str, anchor: date) -> tuple[date, date]:
    tomorrow = anchor + timedelta(days=1)
    monday = anchor - timedelta(days=anchor.weekday())
    if name == "today":
        return anchor, tomorrow
    if name == "yesterday":
        return anchor - timedelta(days=1), anchor
    if name == "this_week":
        return monday, tomorrow
    if name == "last_week":
        return monday - timedelta(days=7), monday
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
