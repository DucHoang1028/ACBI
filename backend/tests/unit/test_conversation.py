from datetime import date

import pytest
from app.ai.client import Intent
from app.api.chat import merged_intent, special_kind
from app.core.dates import date_hints, resolve_period
from app.presentation.summary import factual
from app.query.builder import build
from app.query.validation import validate


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


def test_complete_comparisons_and_confirmations_do_not_require_date_templates() -> None:
    query = "Cho tôi chart so sánh doanh thu tháng 5 và doanh thu tháng 6 năm 2025"
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=["request"]), None, query
    )
    assert (resolved.metric_id, resolved.dimension) == ("revenue", "month")
    assert (resolved.start_date, resolved.end_date) == ("2025-05-01", "2025-07-01")
    prior = {"turns": [{"question": query, "answer": "Please confirm."}]}
    confirmed = merged_intent(
        intent(metric_id=None, missing_fields=["request"]), prior, "Đúng vậy"
    )
    assert not confirmed.needs_clarification and confirmed.dimension == "month"


def test_year_and_two_territories_are_enough_information() -> None:
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=["request"]),
        None,
        "So sánh doanh thu Canada và Northwest cả năm 2024",
    )
    assert (resolved.start_date, resolved.end_date) == ("2024-01-01", "2025-01-01")
    assert resolved.dimension == "sales_territory"
    assert resolved.territory == "Canada|Northwest"
    plan = build(resolved, "manager", date(2025, 6, 29))
    validated = validate(plan, resolved, "manager", trusted_template=True)
    assert validated.params["territory_0"] == "Canada"
    assert validated.params["territory_1"] == "Northwest"
    assert ":territory_0" in validated.sql and ":territory_1" in validated.sql


def test_common_business_wording_and_data_overview_do_not_need_exact_metric_ids() -> (
    None
):
    assert (
        merged_intent(intent(metric_id=None), None, "Sản xuất tháng này").metric_id
        == "production_output"
    )
    assert (
        merged_intent(intent(metric_id=None), None, "Tỷ lệ lỗi quý trước").metric_id
        == "defect_rate"
    )
    assert (
        special_kind("Database có bao nhiêu nhà máy và những nhà máy nào?")
        == "factories"
    )
    assert special_kind("Có dữ liệu năm 2024 không?") == "coverage"
    assert special_kind("Doanh thu của từng nhà máy") == "factory_revenue"


def test_shortcut_answers_are_role_gated() -> None:
    from app.api.chat import SPECIAL_ROLES

    assert "production" not in SPECIAL_ROLES["coverage"]
    assert "sales" not in SPECIAL_ROLES["factories"]
    assert "it_admin" not in {r for roles in SPECIAL_ROLES.values() for r in roles}
