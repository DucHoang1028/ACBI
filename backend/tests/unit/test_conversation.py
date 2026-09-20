from datetime import date

import pytest
from app.ai.client import Intent
from app.api.chat import merged_intent
from app.core.dates import date_hints, resolve_period
from app.presentation.summary import factual


def intent(**changes: object) -> Intent:
    values = dict(
        metric_id="revenue",
        dimension="none",
        period=None,
        start_date=None,
        end_date=None,
        factory_id=None,
        territory=None,
        limit=100,
        needs_clarification=True,
        clarification_question="Which period?",
        zero_scrap_only=False,
        missing_fields=["period"],
    )
    values.update(changes)
    return Intent.model_validate(values)


@pytest.mark.parametrize(
    "question,period,start,end",
    [
        ("Doanh thu tháng này", "this_month", date(2025, 6, 1), date(2025, 6, 30)),
        ("Còn quý trước?", "last_quarter", date(2025, 1, 1), date(2025, 4, 1)),
        ("Revenue last month", "last_month", date(2025, 5, 1), date(2025, 6, 1)),
    ],
)
def test_relative_dates_override_wrong_model_dates(
    question: str, period: str, start: date, end: date
) -> None:
    resolved = merged_intent(intent(period="last_year"), None, question)
    assert resolved.period == period
    assert not resolved.needs_clarification
    assert resolve_period(period, date(2025, 6, 29)) == (start, end)


def test_date_only_reply_keeps_metric_and_clears_redundant_clarification() -> None:
    prior = {"slots": intent().model_dump()}
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=["metric_id"]), prior, "Tháng 6 năm 2025"
    )
    assert resolved.metric_id == "revenue"
    assert (resolved.start_date, resolved.end_date) == ("2025-06-01", "2025-07-01")
    assert not resolved.needs_clarification


def test_unresolved_business_ambiguity_and_vague_dates_stay_clarifications() -> None:
    resolved = merged_intent(
        intent(missing_fields=["request"]), None, "Doanh thu và lợi nhuận tháng này"
    )
    assert resolved.needs_clarification
    assert merged_intent(intent(period="recently"), None).needs_clarification
    assert date_hints("So sánh tháng này với tháng trước") == {}


def test_incomplete_new_date_does_not_reuse_old_literal_dates() -> None:
    prior = {
        "slots": intent(
            period="explicit", start_date="2025-01-01", end_date="2025-02-01"
        ).model_dump()
    }
    resolved = merged_intent(
        intent(period="explicit", start_date="2024-01-01", missing_fields=["end_date"]),
        prior,
    )
    assert resolved.end_date is None
    assert resolved.needs_clarification


def test_vietnamese_summary_formats_actual_value_without_inventing_currency() -> None:
    summary = factual(
        "revenue", [{"revenue": "1234567.89"}], date(2025, 1, 1), date(2025, 4, 1), "vi"
    )
    assert "1.234.567,89" in summary and "31/03/2025" in summary
    assert "đơn vị tiền tệ nguồn" in summary and "VND" not in summary
