"""Turn a question plus context into a complete Structured Intent.

The model interprets the question. The backend then fills only what the model left
empty, resolves dates deterministically, checks every choice against the approved
dictionary and the data's own members, and asks instead of guessing. Vocabulary
(metric names, breakdowns, territory names) comes from the dictionary and the
warehouse, never from this file.
"""

import re
from datetime import date, timedelta
from typing import Any

from app.ai.client import Intent
from app.core.dates import (
    COMPARE_WORDS,
    GROWTH,
    MONTH_NAMES,
    STACK,
    WHY,
    compares_two_periods,
    intent_hints,
    is_confirmation,
    named_periods,
    single_dimension,
    territory_names,
)
from app.core.text import fold
from app.metadata import vocabulary

DATA_TYPES = {"metric_query", "comparison", "trend", "ranking", "needs_clarification"}
PERIOD_KEYS = ("period", "start_date", "end_date")
FILL_KEYS = ("metric_id", "dimension", "territory", "series_dimension", "limit")


FOLLOW_UP = (
    r"\b(?:con|the con|vay|nua|trong do|o do|cua no|nhu tren|tuong tu|"
    r"what about|how about|same|those|them|these|instead|and)\b"
)


RELATIVE_PERIOD = (
    r"\b(?:(?:thang|quy|nam|tuan)\s+(?:nay|truoc|ngoai|vua roi|vua qua)|"
    r"hom nay|hom qua|"
    r"(?:this|last)\s+(?:month|quarter|year|week))\b"
)


def is_standalone(question: str, hints: dict[str, object]) -> bool:
    """True when the question names its own metric and period and leans on nothing.

    "Doanh thu năm 2024" asked after a France-versus-Germany answer is a new
    question: territory, factory and breakdown of the earlier turn must not leak in."""
    value = fold(question)
    named_period = bool(hints.get("period") or hints.get("start_date")) or bool(
        re.search(RELATIVE_PERIOD, value)  # two periods in one message leave no hint
    )
    return (
        bool(vocabulary.get().match_metrics(value))  # one metric, or several
        and named_period
        and not re.search(FOLLOW_UP, CLAUSE.split(value)[0])  # a later "còn ..." is ok
        and not value.startswith(("chi ", "only "))
    )


COMPARING = r"\b(?:so voi|so sanh|compare|versus|vs|hon)\b"
RANKING = (
    r"\b(?:cao nhat|thap nhat|nhieu nhat|it nhat|lon nhat|nho nhat|top|tang|giam|"
    r"tot nhat|kem nhat|highest|lowest|best|worst|trend)\b"
)


def says_something(
    question: str, hints: dict[str, object], prior: dict[str, Any] | None
) -> bool:
    """True when the message itself names a metric, period, member, breakdown or
    follow-up, or answers a pending question; a bare vague request says nothing."""
    value = fold(question)
    vocab = vocabulary.get()
    return bool(
        hints
        or (prior or {}).get("pending_question")
        or is_confirmation(question)
        or re.search(r"\d", value)
        or re.search(FOLLOW_UP, value)
        or re.search(RANKING, value)
        or re.search(GROWTH, value)
        or vocab.match_metrics(value)
        or vocab.match_members(value)
        or vocab.match_dimensions(value)
    )


def _close_end(current: dict[str, Any]) -> None:
    """A model that ends a month or quarter on its last day left that day out.

    Dates are half-open, so a range from a first of the month that ends on the last
    day of a month is read as ending the day after: 01-01 to 03-31 is all of Q1."""
    try:
        start = date.fromisoformat(str(current.get("start_date")))
        end = date.fromisoformat(str(current.get("end_date")))
    except ValueError:
        return
    if current.get("period") == "explicit" and start.day == 1 and end.day != 1:
        if (end + timedelta(days=1)).day == 1:
            current["end_date"] = (end + timedelta(days=1)).isoformat()


def drop_inherited(
    current: dict[str, Any], slots: dict[str, Any], question: str, hints: dict[str, Any]
) -> None:
    """Clear filters copied from earlier turns that this question never says."""
    vocab = vocabulary.get()
    value = fold(question)
    members = vocab.match_members(value)
    named = {
        "territory": bool(hints.get("territory") or members.get("sales_territory")),
        "factory_id": bool(members.get("factory")),
        "dimension": bool(hints.get("dimension") or vocab.match_dimensions(value)),
    }
    for key, was_named in named.items():
        if was_named or slots.get(key) in (None, "none"):
            continue
        if current.get(key) == slots[key]:
            current[key] = "none" if key == "dimension" else None
    same_limit = current.get("limit") == slots.get("limit") != 100
    if same_limit and not re.search(r"\d", value):
        current["limit"] = 100  # an earlier "top 3" is not asked again


EVALUATIVE = re.compile(
    r"\b(?:te nhat|kem nhat|tot nhat|lam an|hieu qua|hieu suat|performance|"
    r"best|worst|underperform\w*)\b"
)
WHICH = re.compile(r"\b((?:[a-z]+\s){1,2})nao\b|\bwhich\s+((?:[a-z]+\s?){1,2})")


BY = re.compile(r"\b(?:theo|by|per|moi|tung|each)\s+((?:[a-z]+\s?){1,3})")


def _by_dimension(current: dict[str, Any], question: str) -> None:
    """"Theo nhà máy" in the first task asks for that split, whatever the model kept."""
    if current["dimension"] != "none":
        return
    match = BY.search(fold(first_clause(question)))
    named = vocabulary.get().match_dimensions("theo " + match.group(1)) if match else []
    if len(named) == 1:
        current["dimension"] = named[0]


def _which_dimension(current: dict[str, Any], intent: Intent, question: str) -> None:
    """"Nhà máy nào ...?" asks about every factory: no factory filter, split by it."""
    value = fold(question)
    match = WHICH.search(value)
    if not match:
        return
    named = vocabulary.get().match_dimensions(match.group(1) or match.group(2) or "")
    if not named or named[0] in {"month", "week", "day"}:
        return
    dimension = named[0]
    if dimension == "factory" and intent.factory_id is None:
        current["factory_id"] = None
    if dimension == "sales_territory" and not intent.territory:
        current["territory"] = None
    if current["dimension"] == "none":
        current["dimension"] = dimension


EXTREME = re.compile(
    r"\b(?:cao nhat|thap nhat|nhieu nhat|it nhat|lon nhat|nho nhat|"
    r"highest|lowest|best|worst|most|least)\b"
)


def _verify_extreme(
    current: dict[str, Any], hints: dict[str, Any], question: str
) -> None:
    """ "Is Canada the highest?" is answered against every territory, not for Canada."""
    value = fold(question)
    vocab = vocabulary.get()
    if not EXTREME.search(value):
        return
    if set(vocab.match_dimensions(value)) - {"sales_territory", "factory"}:
        return  # another breakdown was named: the extreme is within it
    members = vocab.match_members(value)
    territory_dim = current["dimension"] in {"none", "sales_territory"}
    factory_dim = current["dimension"] in {"none", "factory"}
    if (
        territory_dim
        and len(members.get("sales_territory", [])) == 1
        and not members.get("factory")
    ):
        current.update(dimension="sales_territory", territory=None, limit=100)
        hints.pop("territory", None)
    elif (
        factory_dim
        and len(members.get("factory", [])) == 1
        and not members.get("sales_territory")
    ):
        current.update(dimension="factory", factory_id=None, limit=100)


def _stacked_by_month(current: dict[str, Any], question: str) -> None:
    """ "By month ... stacked" for two territories: months, stacked by territory."""
    value = fold(question)
    vocab = vocabulary.get()
    if (
        re.search(STACK, value)
        and "month" in vocab.match_dimensions(value)
        and len(vocab.match_members(value).get("sales_territory", [])) >= 2
    ):
        current.update(dimension="month", series_dimension="sales_territory", limit=250)


def _those_members(
    current: dict[str, Any], slots: dict[str, Any], intent: Intent, question: str
) -> None:
    """Growth for those regions: what the last answer showed on screen."""
    value = fold(question)
    if not re.search(
        r"\b(?:khu vuc|nuoc|nha may|region|territor\w*|factor\w*)\s+(?:do|nay|kia)\b|"
        r"\b(?:those|these|them)\b",
        value,
    ):
        return
    shown = slots.get("shown") or {}
    if shown.get("territory") and not intent.territory:
        current["territory"] = "|".join(dict.fromkeys(shown["territory"]))
        if current["dimension"] == "none":
            current["dimension"] = "sales_territory"
        current["limit"] = 100
    elif shown.get("factory") and intent.factory_id is None:
        current["dimension"] = "factory"
        current["limit"] = 100


def merged_intent(
    intent: Intent, prior: dict[str, Any] | None, question: str = ""
) -> Intent:
    if intent.intent_type not in DATA_TYPES:
        return intent  # chat, metadata, unsupported and forecast are routed as they are
    slots = dict((prior or {}).get("slots") or {})
    current = intent.model_dump()
    hints = intent_hints(question)
    if len(vocabulary.get().match_metrics(fold(question))) > 1 or further_requests(
        question
    ):
        # Several tasks: the first one's own period wins over the later ones'.
        head = intent_hints(first_clause(question))
        if head.get("period") or head.get("start_date"):
            hints = {**hints, **{k: v for k, v in head.items() if k in PERIOD_KEYS}}
    standalone = is_standalone(question, hints)
    if standalone:
        drop_inherited(current, slots, question, hints)
    if question and not says_something(question, hints, prior):
        # "cho tôi xem số liệu": whatever the model copied from context is not asked.
        return intent.model_copy(
            update={
                "needs_clarification": True,
                "metric_id": None,
                "period": None,
                "clarification_question": None,
                "missing_fields": ["metric_id"],
            }
        )
    _by_dimension(current, question)
    _which_dimension(current, intent, question)
    _verify_extreme(current, hints, question)
    _stacked_by_month(current, question)
    _those_members(current, slots, intent, question)
    if EVALUATIVE.search(fold(question)) and not vocabulary.get().match_metrics(
        fold(question)
    ):
        # "Which factory is doing worst?" does not say by which measure.
        current.update(
            needs_clarification=True,
            metric_id=None,
            period=None,
            clarification_question=None,
            missing_fields=["metric_id"],
        )
        return Intent.model_validate(current)
    adds_information = bool(
        hints
        or intent.metric_id
        or intent.period
        or intent.dimension != "none"
        or intent.limit != 100  # "top 3"
        or current["dimension"] != "none"  # "theo khu vực" read from the wording
        or intent.territory
        or intent.factory_id is not None
        or intent.start_date
        or intent.zero_scrap_only
        or is_confirmation(question)
    )
    if not adds_information:
        # An unclear message must not replay the previous query.
        return intent.model_copy(update={"needs_clarification": True})
    if not hints and is_confirmation(question):
        for turn in reversed((prior or {}).get("turns") or []):
            hints = intent_hints(turn.get("question", ""))
            if hints:
                break
    for key in PERIOD_KEYS:  # calendar wording is resolved by the backend
        if key in hints:
            current[key] = hints[key]
    override = {"metric_id"} if hints.get("metric_override") else set()
    for key in FILL_KEYS:  # the model's reading stands; hints only fill gaps
        if key not in hints:
            continue
        if key == "limit":
            if hints[key] != 100:
                current[key] = hints[key]
        elif key in override or current.get(key) in (None, "none"):
            current[key] = hints[key]
    earlier = slots.get("territory")
    comparing = bool(re.search(COMPARING, fold(question)))
    if (
        not standalone
        and comparing
        and earlier
        and current.get("territory")
        and set(str(current["territory"]).split("|")).isdisjoint(earlier.split("|"))
    ):
        current["territory"] = f"{earlier}|{current['territory']}"
    if (
        not standalone
        and comparing
        and len(str(current.get("territory") or "").split("|")) > 1
        and current["dimension"] in {"none", "month", "week", "day"}
        and not re.search(r"\b(?:thang|tuan|ngay|month|week|day)\b", fold(question))
    ):
        current["dimension"] = "sales_territory"  # one row per territory compared
    for field in ("metric_id", "period", "factory_id", "territory"):
        if standalone and field in ("factory_id", "territory"):
            continue
        if current[field] is None:
            current[field] = slots.get(field)
    named_metric = bool(vocabulary.get().match_metrics(fold(question)))
    kept = vocabulary.get().metrics.get(str(current["metric_id"]))
    if (
        hints.get("territory")
        and not named_metric
        and kept is not None
        and "sales_territory" not in kept.dimensions
    ):
        current["metric_id"] = "revenue"  # only sales figures split by territory
    metric = vocabulary.get().metrics.get(str(current.get("metric_id")))
    if metric and not hints.get("territory") and not intent.territory:
        # A territory kept from a revenue answer means nothing for production.
        if "sales_territory" not in metric.dimensions:
            current["territory"] = None
        if "factory" not in metric.dimensions and not intent.factory_id:
            current["factory_id"] = None
    kept = str(slots.get("dimension"))
    splits = (
        metric is None
        or kept in metric.dimensions
        or (kept in {"day", "week", "month"} and "date" in metric.dimensions)
    )
    if (
        not standalone
        and current["dimension"] == "none"
        and slots.get("dimension") not in (None, "none")
        and splits
    ):
        current["dimension"] = slots["dimension"]
    _close_end(current)
    if current["period"] == "explicit" and not any(k in hints for k in PERIOD_KEYS):
        if intent.period is None or not (current["start_date"] or current["end_date"]):
            # No dates of its own (a half-given new date is asked about instead).
            for field in ("start_date", "end_date"):
                if current[field] is None:
                    current[field] = slots.get(field)
    missing = [
        field
        for field in current["missing_fields"]
        if not (
            metric
            and (
                (field == "factory_id" and "factory" not in metric.dimensions)
                or (field == "territory" and "sales_territory" not in metric.dimensions)
            )
        )
    ]  # a factory is not missing from a revenue request
    if missing != current["missing_fields"] and not missing:
        current["needs_clarification"] = False
        current["clarification_question"] = None
    current["missing_fields"] = missing
    if missing and "request" not in missing:
        unresolved = [
            field
            for field in missing
            if not current.get(field)
            or (field == "period" and current[field] == "recently")
        ]
        current["missing_fields"] = unresolved
        current["needs_clarification"] = bool(unresolved)
        if not unresolved:
            current["clarification_question"] = None
    if (
        "metric_id" in hints
        and current.get("metric_id") == hints["metric_id"]
        and current.get("period")
        and (
            current["period"] != "explicit"
            or (current.get("start_date") and current.get("end_date"))
        )
    ):
        current.update(
            needs_clarification=False,
            clarification_question=None,
            missing_fields=[],
        )
    unknown = _unknown_choice(current, question)
    if unknown:
        current.update(
            needs_clarification=True,
            missing_fields=[unknown],
            clarification_question=None,
        )
    return Intent.model_validate(current)


ALL_WORDS = {"cac", "moi", "tat", "ca", "all", "every", "each", "the", "nhung"}


def _means_every_member(text: str) -> bool:
    """True for a plural such as "các nước" that names the dimension, not a member."""
    dimension = vocabulary.get().dimensions.get("sales_territory")
    if dimension is None:
        return False
    words = {w for phrase in dimension.synonyms for w in phrase.split()}
    tokens = fold(text).replace("|", " ").split()
    return bool(tokens) and all(t in words or t in ALL_WORDS for t in tokens)


OPTION = re.compile(
    r"\((\d)\)\s*(.+?)(?=(?:,?\s*(?:hay|or|and|và)\s*)?\(\d\)|\?|$)", re.S
)
FIRST = r"\b(?:dau tien|cai dau|cau dau|first|ca hai|ca 2|both|tat ca)\b"
SECOND = r"\b(?:thu hai|cai sau|cau sau|second)\b"


def chosen_option(reply: str, pending: str | None) -> str | None:
    """The offered choice a short reply picks: "1 đi", "cái đầu tiên", "cả 2".

    A clarification that offers "(1) ... hay (2) ..." is answered by naming one; the
    reply then stands for that option's text. "Both" takes the first and leaves
    the other for the next turn."""
    options = {int(n): t.strip(" ,.;:") for n, t in OPTION.findall(pending or "")}
    if len(options) < 2:
        return None
    folded = fold(reply).strip(" .!?")
    if len(folded.split()) > 10:
        return None
    digits = set(re.findall(r"\d+", folded))
    if re.search(SECOND, folded):
        number = 2
    elif re.search(FIRST, folded):  # "cả 2" means both: start with the first
        number = 1
    elif len(digits) == 1 and int(next(iter(digits))) in options:
        number = int(next(iter(digits)))
    else:
        return None  # another number or no number: a new request, not a choice
    return options.get(number)


LATER = (
    r"\b(?:tiep|con lai|cai kia|cai sau|thu hai|thu 2|ca hai|ca 2|both|tat ca|"
    r"yeu cau 2|cau 2|so 2|next|rest)\b"
)


def next_deferred(
    reply: str, prior: dict[str, Any] | None
) -> tuple[str | None, list[str]]:
    """The request left for later that a short reply asks for ("tiếp đi").

    Returns its text and the requests still waiting after it."""
    later = list(((prior or {}).get("slots") or {}).get("deferred_requests") or [])
    folded = fold(reply).strip(" .!?")
    if not later or len(folded.split()) > 10 or not re.search(LATER, folded):
        return None, []
    return later[0], later[1:]


def resume_pending(intent: Intent, prior: dict[str, Any] | None) -> Intent:
    """Keep the kind of an unresolved request while its clarification is answered.

    Without this, answering "which period?" for a forecast would run a plain query
    for that period instead."""
    pending = (prior or {}).get("pending_question")
    waiting = ((prior or {}).get("slots") or {}).get("intent_type")
    if pending and waiting == "forecast" and intent.intent_type in DATA_TYPES:
        return intent.model_copy(update={"intent_type": "forecast"})
    return intent


def unstick(question: str, prior: dict[str, Any] | None, language: str) -> str:
    """Ask differently when the same question would be put to the user twice."""
    pending = ((prior or {}).get("pending_question") or "").strip()
    if not pending or fold(pending) != fold(question.strip()):
        return question
    return (
        "Tôi vẫn chưa hiểu ý bạn. Hãy nêu lại yêu cầu trong một câu, gồm chỉ số, "
        "khoảng thời gian và cách chia nhóm. Ví dụ: “Doanh thu theo khu vực từ "
        "01/01/2022 đến 30/06/2025”."
        if language == "vi"
        else "I still do not understand. Please restate the request in one sentence "
        "with the metric, the period and the breakdown, for example: “Revenue by "
        "territory from 2022-01-01 to 2025-06-30”."
    )


def validate_choices(intent: Intent, question: str) -> Intent:
    """Canonical territory names; ask when a factory or territory is not in the data."""
    current = intent.model_dump()
    unknown = _unknown_choice(current, question)
    if unknown:
        current.update(
            needs_clarification=True,
            missing_fields=[unknown],
            clarification_question=None,
        )
    return Intent.model_validate(current)


def _unknown_choice(current: dict[str, Any], question: str) -> str | None:
    """A factory or territory that is not in the data must be asked about."""
    vocab = vocabulary.get()
    if vocab.unknown_member_reference(fold(question)):
        return "factory_id"
    deferred = bool(current.get("deferred_requests"))
    named = vocab.match_members(fold(question))
    metric = vocab.metrics.get(str(current.get("metric_id")))
    for dimension, _ in vocab.capitalised_after_dimension(question):
        if deferred and named.get(dimension):
            continue
        if current.get("dimension") == dimension and len(named.get(dimension, [])) > 1:
            continue  # "Factory A with Factory B": a breakdown over the named members
        if metric and named.get(dimension) and dimension not in metric.dimensions:
            continue  # a known member this metric cannot be split by: not this request
        if dimension == "sales_territory" and not current.get("territory"):
            return "territory"  # named after a dimension word, yet no filter was set
        if dimension == "factory" and current.get("factory_id") is None:
            return "factory_id"
    if (
        metric
        and not current.get("territory")
        and current.get("factory_id") is None
        and unfamiliar_name(question)
    ):
        # A name we do not know ("Wakanda") must be asked about, not dropped.
        return "territory" if "sales_territory" in metric.dimensions else "factory_id"
    known = set(vocab.ids("factory").values())
    if (
        current.get("factory_id") is not None
        and known
        and (current["factory_id"] not in known)
    ):
        return "factory_id"
    if current.get("territory"):
        names = territory_names(str(current["territory"]))
        if names is None and _means_every_member(str(current["territory"])):
            current["territory"] = None  # "các nước": no filter, one row per member
            if current["dimension"] == "none":
                current["dimension"] = "sales_territory"
            return None
        if names is None:
            return "territory"
        if names:
            current["territory"] = "|".join(names)
    return None


PERIOD_PHRASES = (
    r"\b(?:hom nay|hom qua|tuan nay|tuan truoc|today|yesterday|this week|last week)\b"
)
LOCAL_BLOCK = (
    r"\b(?:top|cao nhat|thap nhat|lon nhat|nho nhat|chi|khong|trung binh|xep hang|"
    r"sap xep|moi nhat|tuan|ngay|week|weekly|daily|average|highest|lowest|only|"
    r"except)\b"
)


def local_intent(question: str) -> Intent | None:
    """Build a complete Structured Intent from unambiguous wording, no model call.

    An optional shortcut (off by default): it returns None whenever the wording needs
    interpretation, so the model decides.
    """
    vocab = vocabulary.get()
    value = fold(question)
    hints = intent_hints(question)
    if not hints.get("metric_id") or hints.get("period") in (None, "recently"):
        return None
    wording = re.sub(PERIOD_PHRASES, " ", value)
    if re.search(LOCAL_BLOCK, wording) or re.search(r"\bso voi\b", value):
        return None
    if vocab.unknown_member_reference(value):
        return None
    members = vocab.match_members(value)
    factory_id: int | None = None
    if "factory" in members:
        factory_id = vocab.ids("factory").get(members["factory"][0])
    dims = vocab.match_dimensions(wording)
    if (
        any(rest for rest in re.findall(r"\b(?:theo|by|per)\s+\w+", wording))
        and not dims
    ):
        return None  # a breakdown the dictionary does not know: let the model decide
    dimension = str(hints.get("dimension") or single_dimension(wording) or "")
    if not dimension:
        return None
    return Intent.model_validate(
        {
            "metric_id": hints["metric_id"],
            "dimension": dimension,
            "period": hints["period"],
            "start_date": hints.get("start_date"),
            "end_date": hints.get("end_date"),
            "factory_id": factory_id,
            "territory": hints.get("territory"),
            "limit": hints.get("limit", 100),
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
            "series_dimension": hints.get("series_dimension", "none"),
        }
    )


TITLE_WORDS = {
    "by", "for", "in", "of", "and", "the", "to", "vs", "or", "per", "with", "on", "at",
    "from", "between", "compared", "versus", "than", "against", "each", "all",
}  # fmt: skip


UNSUPPORTED_ALWAYS = re.compile(
    r"\b(?:quy doi|chuyen doi tien|ty gia|exchange rate|convert\w*|vnd|usd|eur)\b|"
    r"\b(?:theo|by|per|moi|tung|each)\s+(?:khach hang|customers?|nhan vien|"
    r"employees?|nha cung cap|suppliers?)\b"
)
UNSUPPORTED_TOPICS = re.compile(
    r"\b(?:loi nhuan|profit|margin|khach hang|customers?|nhan vien|employees?|staff|"
    r"tien luong|bang luong|muc luong|salary|payroll|ton kho|inventory|stock|chi phi|"
    r"costs?|gia von|nha cung cap|suppliers?)\b"
)


def local_unsupported(question: str) -> Intent | None:
    """Things the warehouse metrics never cover, refused without asking a model.

    A currency conversion or a split by customer or employee is refused even beside a
    known metric (answering the plain figure would hide what was asked); a topic such
    as profit, stock or salary is refused when no known metric is named with it."""
    value = fold(question)
    if not (
        (re.search(WHY, value) and not vocabulary.get().match_metrics(value))
        or UNSUPPORTED_ALWAYS.search(value)
        or (
            UNSUPPORTED_TOPICS.search(value)
            and not vocabulary.get().match_metrics(value)
        )
    ):
        return None
    return Intent.model_validate(
        {
            "metric_id": None,
            "dimension": "none",
            "period": None,
            "start_date": None,
            "end_date": None,
            "factory_id": None,
            "territory": None,
            "limit": 100,
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
            "intent_type": "unsupported",
        }
    )


def follow_up_intent(
    question: str, slots: dict[str, Any] | None = None
) -> Intent | None:
    """A short follow-up ("Canada thì sao", "theo khu vực") read without a model.

    Only what the message itself says is filled (a member); the metric, period and
    breakdown come from the earlier turn when the intent is merged with it. None for
    a comparison, which needs more than the earlier turn can give."""
    value = fold(question)
    metrics = vocabulary.get().match_metrics(value)
    if (
        len(metrics) > 1
        or (metrics and not re.search(FOLLOW_UP, value))  # "còn tỷ lệ phế phẩm?" only
        or re.search(r"\b(?:du bao|forecast|predict)\b", value)
        or re.search(WHY, value)
    ):
        return None  # a new question or a different kind of request, not a follow-up
    if re.search(COMPARE_WORDS, value):
        # "So sánh với 2023": readable with one period named and one on screen.
        from app.query.comparison import with_earlier_period

        if not with_earlier_period(named_periods(question), slots or {}):
            return None
    top = re.search(r"\btop\s*(\d{1,3})\b", value)
    members = vocabulary.get().match_members(value)
    factories = members.get("factory", [])
    territories = members.get("sales_territory", [])
    return Intent.model_validate(
        {
            "metric_id": None,
            "dimension": "none",
            "period": None,
            "start_date": None,
            "end_date": None,
            "factory_id": (
                vocabulary.get().ids("factory").get(factories[0])
                if len(factories) == 1
                else None
            ),
            "territory": "|".join(territories) or None,
            "limit": min(int(top.group(1)), 250) if top else 100,
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
        }
    )


def unfamiliar_name(question: str) -> bool:
    """A capitalised word (not the first) that names no metric, breakdown or member.

    "Wakanda" in "Doanh thu Wakanda" is such a word: the data has no such place, so
    the question must be asked about, not answered for every place."""
    vocab = vocabulary.get()
    terms = [
        term
        for dimension, names in vocab.members.items()
        for name in names
        for term in vocab.member_terms(dimension, name)
    ]
    terms += [s for m in vocab.metrics.values() for s in m.synonyms]
    terms += [s for d in vocab.dimensions.values() for s in d.synonyms]
    familiar = {word for term in terms for word in fold(term).split()} | TITLE_WORDS
    familiar |= {*MONTH_NAMES, *(m[:3] for m in MONTH_NAMES), "sept", "adventureworks"}
    if question.isupper():
        return False  # shouting carries no information about names
    for index, word in enumerate(re.findall(r"[^\W\d_]{2,}", question)):
        folded = fold(word)
        forms = {folded, re.sub(r"ies$", "y", folded), folded.rstrip("s")}
        acronym = word.isupper() and len(word) <= 4  # KPI, USD, VIP
        if index and word[:1].isupper() and not acronym and not forms & familiar:
            return True
    return False


def local_comparison_intent(question: str) -> Intent | None:
    """The intent of "compare X across periods" from its wording, with no model call.

    None whenever anything is unclear (no single metric, an unfamiliar name, two
    breakdowns, a forecast), so the model decides. The periods come from the text."""
    value = fold(question)
    if not compares_two_periods(question) or re.search(
        r"\b(?:du bao|forecast|predict)\b", value
    ):
        return None
    vocab = vocabulary.get()
    metric = intent_hints(question).get("metric_id")
    dimension = single_dimension(value)
    if not metric or dimension is None or unfamiliar_name(question):
        return None
    if vocab.unknown_member_reference(value):
        return None
    members = vocab.match_members(value)
    territories = members.get("sales_territory", [])
    factories = members.get("factory", [])
    if dimension == "none" and len(territories) >= 2:
        dimension = "sales_territory"
    if dimension == "none" and len(factories) >= 2:
        dimension = "factory"  # several named: one row each, not a filter
    factory_id = vocab.ids("factory").get(factories[0]) if len(factories) == 1 else None
    return Intent.model_validate(
        {
            "metric_id": metric,
            "dimension": dimension,
            "period": "explicit",
            "start_date": None,
            "end_date": None,
            "factory_id": factory_id,
            "territory": "|".join(territories) or None,
            "limit": 100,
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
            "intent_type": "comparison",
        }
    )


def clarification_text(intent: Intent, language: str) -> str:
    """Wording for an unresolved request; the model's own question comes first."""
    vi = language == "vi"
    vocab = vocabulary.get()
    if "factory_id" in intent.missing_fields:
        names = ", ".join(vocab.members.get("factory", []))
        return (
            f"Tôi không có nhà máy đó. Các nhà máy có dữ liệu: {names}."
            if vi
            else f"I have no such factory. Available factories: {names}."
        )
    if "territory" in intent.missing_fields:
        names = ", ".join(vocab.members.get("sales_territory", []))
        return (
            f"Tôi không nhận ra khu vực này. Các khu vực có dữ liệu: {names}."
            if vi
            else f"I do not recognise that territory. Available territories: {names}."
        )
    if intent.metric_id and intent.period == "recently":
        return (
            "“Gần đây” chưa đủ rõ để tôi chọn kỳ. Bạn muốn tháng này, tháng trước, "
            "quý trước, năm 2024 hay một khoảng ngày cụ thể?"
            if vi
            else "“Recently” is too loose for me to pick a period. Do you want this "
            "month, last month, last quarter, 2024, or an exact date range?"
        )
    if intent.clarification_question:
        return intent.clarification_question
    if not intent.metric_id:
        names = ", ".join(m.label(language).lower() for m in vocab.metrics.values())
        return (
            f"Bạn muốn xem chỉ số nào? Tôi hỗ trợ: {names}."
            if vi
            else f"Which metric do you want? I support: {names}."
        )
    if not intent.period or intent.period == "recently":
        return (
            "Bạn muốn xem kỳ nào? Ví dụ: tháng này, quý trước, năm 2024 "
            "hoặc một khoảng ngày."
            if vi
            else "Which period should I use? For example: this month, "
            "last quarter, 2024, or a date range."
        )
    return (
        "Cần thêm một chi tiết để trả lời câu hỏi này."
        if vi
        else "One more detail is needed to answer this question."
    )


SPLIT = re.compile(
    r",|;|\bvà\b|\brồi\b|\bluôn\b|\bsau đó\b|\bnữa\b|\band\b|\bthen\b"
    r"|\bđồng thời\b|\bngoài ra\b|\bbên cạnh đó\b|\balso\b|\bplus\b"
)
CLAUSE = re.compile(
    r",|;|\bva\b|\broi\b|\bluon\b|\bsau do\b|\bnua\b|\band\b|\bthen\b"
    r"|\bdong thoi\b|\bngoai ra\b|\bben canh do\b|\balso\b|\bplus\b"
)


def split_tasks(question: str) -> list[str]:
    """The requests in a message, in order and in the user's own words.

    A clause starts a new request when it names another metric, asks to compare,
    forecast or rank, asks to show something with a period of its own, or repeats
    the metric with a period of its own after a first clause that had one. Any other
    clause (a bare period, a breakdown, a chart) stays with the request before it."""
    vocab = vocabulary.get()
    parts = [p.strip(" .?!") for p in CLAUSE.split(fold(question)) if p.strip()]
    originals = SPLIT.split(question)
    originals = [o.strip(" .?!") for o in originals if o.strip()]
    if len(parts) != len(originals) or len(parts) < 2:
        return [question]
    first: set[str] = set()
    for part in parts:
        if found := set(vocab.match_metrics(part)):
            first = found
            break
    first_owns_period = bool(re.search(OWN_PERIOD, parts[0]))
    first_compares = bool(re.search(COMPARE_WORDS, parts[0]))
    tasks: list[str] = []
    for index, (part, original) in enumerate(zip(parts, originals)):
        found = set(vocab.match_metrics(part))
        own_period = bool(re.search(OWN_PERIOD, part))
        # "Úc và Canada ...": a clause opening with a member name goes on with the
        # list before it.
        continues = index and any(
            re.match(vocab.member_pattern(dimension, name), part)
            for dimension, names in vocab.members.items()
            for name in names
        )
        starts = (
            index
            and not continues
            and (
                re.search(TASK_WORDS, part)
                or (re.search(RANK_WORDS, part) and own_period)
                or (re.match(IMPERATIVE, part) and (found or own_period))
                or (found and found != first)
                or (found and own_period and first_owns_period and not first_compares)
            )
        )
        if starts or not tasks:
            tasks.append(original)
        else:
            bare_period = not re.sub(PERIOD_TOKEN, "", part).strip()
            joiner = " và " if bare_period or continues else ", "
            tasks[-1] += f"{joiner}{original}"
    if len(tasks) < 2:
        return [question]
    found = list(re.finditer(OWN_PERIOD, fold(tasks[-1])))
    if found:
        # "Doanh thu Canada và sản lượng Factory A quý trước": one period for both.
        phrase = tasks[-1][found[-1].start() : found[-1].end()]
        vocab = vocabulary.get()

        def shares(t: str) -> bool:
            value = fold(t)
            return (
                bool(vocab.match_metrics(value))
                and not re.search(OWN_PERIOD, value)
                and not re.search(TASK_WORDS, value)  # a forecast keeps its own horizon
            )

        tasks = [f"{t} {phrase}" if shares(t) else t for t in tasks[:-1]] + tasks[-1:]
    return tasks[:9]


def further_requests(question: str) -> list[str]:
    """Later requests of a message that holds several, in the user's own words.

    The model is asked to list them; when it does not, these are kept so none is
    dropped without a word."""
    return split_tasks(question)[1:]


TASK_WORDS = r"\b(?:du bao|forecast|so sanh|compare)\b"
IMPERATIVE = (
    r"^(?:show|list|give|display|plot|draw|cho toi|cho minh|hien thi|xem|ve|"
    r"liet ke|tinh|tim)\b"
)
PERIOD_TOKEN = r"\b(?:nam|thang|quy|year|quarter|month|q[1-4]|20\d{2}|\d{1,2})\b"
RANK_WORDS = r"\b(?:top\s*\d+|ban chay)\b"  # a task only with a period of its own
OWN_PERIOD = (
    r"\b(?:nam\s+\d{4}|20\d{2}|thang\s+\d{1,2}|quy\s+\d|(?:this|last)\s+\w+)\b|"
    + RELATIVE_PERIOD
)


CORRECTION = re.compile(
    r"\b(?:a ma thoi|ma thoi|y minh la|y toi la|y em la|nham|doi lai|no wait|"
    r"i mean|actually|scratch that)\b"
)
PERIOD_WORDS = re.compile(
    r"\b(?:(?:thang|quy|nam)\s*\d{1,4}(?:\s*(?:nam\s*|/)\s*\d{4})?|20\d{2}"
    r"|(?:thang|quy|nam|tuan)\s+(?:nay|truoc)|hom (?:nay|qua))\b"
)


def resolve_corrections(question: str) -> str:
    """The part after a correction wins ("tháng 3, ý mình là tháng 4 năm 2025").

    A correction that names a metric replaces the whole earlier part; one that names
    only a period keeps the earlier metric and drops the earlier period."""
    value = fold(question)
    matches = list(CORRECTION.finditer(value))
    if not matches or len(value) != len(question):
        return question
    last = matches[-1]
    before = question[: last.start()].strip(" ,;.-–")
    after = question[last.end() :].strip(" ,;.-–")
    if not after:
        return question
    if vocabulary.get().match_metrics(fold(after)):
        return after
    spans = [m.span() for m in PERIOD_WORDS.finditer(fold(before))]
    kept = "".join(
        ch for i, ch in enumerate(before) if not any(a <= i < b for a, b in spans)
    )
    return f"{' '.join(kept.split())} {after}".strip()


def first_task_text(question: str, later: list[str]) -> str:
    """The question without the requests left for later, when they appear as written."""
    text = question
    for task in later:
        text = re.sub(re.escape(task), " ", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,;.")
    text = re.sub(r"(?:,|;|\b(?:và|and|rồi|then)\b)\s*$", "", text).strip(" ,;.")
    return text or question


def first_clause(question: str) -> str:
    """The first task of a message, whose own wording sets its period."""
    parts = [p for p in SPLIT.split(question) if p.strip()]
    return parts[0] if parts else question


def first_metric(question: str) -> str | None:
    """The metric of the first clause that names one: the task to run first."""
    vocab = vocabulary.get()
    for part in CLAUSE.split(fold(question)):
        found = vocab.match_metrics(part)
        if found:
            return str(found[0])
    return None
