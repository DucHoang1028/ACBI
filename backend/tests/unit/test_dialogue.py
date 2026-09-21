"""Metadata answers are grounded in scoped references and never invent figures."""

from app.ai.budget import RequestBudget
from app.ai.client import FakeLLM
from app.conversation.dialogue import (
    fallback,
    grounded,
    references,
    reply_from_metadata,
)


def text_of(refs: list[dict]) -> str:
    return "\n".join(doc["text"] for doc in refs)


def test_references_only_describe_what_the_role_may_see() -> None:
    sales = text_of(references("sales", "2025-06-29"))
    assert "revenue" in sales and "Germany" in sales
    assert "Factory A" not in sales and "production.workorder" not in sales
    factory_a = text_of(references("production", "2025-06-29"))
    assert "Factory A" in factory_a and "Factory B" not in factory_a
    assert "sales.salesorderheader" not in factory_a and "Germany" not in factory_a
    manager = text_of(references("manager", "2025-06-29"))
    assert "Factory C" in manager and "sales.salesorderheader" in manager
    assert "role" not in manager.lower()  # access terms never reach the model


def test_a_reply_quoting_an_unknown_number_is_not_trusted() -> None:
    refs = references("manager", "2025-06-29")
    count = str(
        len([d for d in refs if d["id"] == "tables"][0]["text"].splitlines()) - 1
    )
    assert grounded(f"You can read {count} tables.", refs, "how many tables")
    assert not grounded("The warehouse has 4321 tables.", refs, "how many tables")
    assert grounded("Factory 3 is not listed.", refs, "what about factory 3")


def test_reply_uses_the_model_only_through_references_and_falls_back_safely() -> None:
    llm = FakeLLM({})
    llm.replies["Trong dữ liệu có những bảng nào"] = "Có các bảng doanh thu."
    budget = RequestBudget(10, 3)
    ok = reply_from_metadata(
        llm, "Trong dữ liệu có những bảng nào", "sales", "answer", "2025-06-29", budget
    )
    assert ok == "Có các bảng doanh thu."
    llm.replies["x"] = "Có 9999 bảng."
    assert (
        reply_from_metadata(llm, "x", "sales", "answer", "2025-06-29", budget) is None
    )
    note = fallback("limitation", "vi", "sales")
    assert "doanh thu" in note.lower()
    assert "I can report" in fallback("answer", "en", "sales")


def test_a_dropped_breakdown_or_filter_is_detected_without_a_noun_list() -> None:
    from app.core.text import fold
    from app.metadata import vocabulary

    vocab = vocabulary.get()
    assert vocab.unrecognised_breakdown(fold("Doanh thu theo nhân viên bán hàng")) == (
        "nhan vien"
    )
    for known in (
        "doanh thu theo tháng",
        "doanh thu theo khu vực",
        "sản lượng theo dây chuyền",
        "top 5 sản phẩm theo doanh thu",
        "theo dõi doanh thu",
        "doanh thu theo năm 2024",
    ):
        assert vocab.unrecognised_breakdown(fold(known)) is None, known
    assert vocab.capitalised_after_dimension("Doanh thu khu vực Atlantis") == [
        ("sales_territory", "Atlantis")
    ]
    assert vocab.capitalised_after_dimension("doanh thu theo khu vực") == []


def test_member_names_in_the_users_language_come_from_the_dictionary() -> None:
    from app.core.dates import intent_hints, territory_names
    from app.core.text import fold
    from app.metadata import vocabulary

    vocab = vocabulary.get()
    assert vocab.match_members(fold("doanh thu cua Uc va Canada")) == {
        "sales_territory": ["Australia", "Canada"]
    }
    assert territory_names("Đức và Pháp") == ["France", "Germany"]
    assert territory_names("nước Anh") == ["United Kingdom"]
    assert territory_names("Atlantis") is None
    hints = intent_hints("doanh số của 2 nước là Úc và Canada năm 2024")
    assert hints["territory"] == "Australia|Canada"
    assert hints["dimension"] == "sales_territory"
    # The aliases reach the model, so it can translate what the user typed.
    assert "Đức" in vocab.describe() or "duc" in vocab.describe()


def test_an_open_ended_range_is_left_to_the_model() -> None:
    from app.core.dates import date_hints

    for text in (
        "doanh thu từ năm 2022 đến nay",
        "revenue from 2022 to now",
        "sản lượng từ 2023 tới nay",
    ):
        assert date_hints(text) == {}, text
    # A plain named year is still resolved here.
    assert date_hints("doanh thu năm 2022")["start_date"] == "2022-01-01"


def test_a_question_about_the_previous_answer_gets_it_as_a_reference() -> None:
    from app.conversation.dialogue import last_answer

    assert last_answer(None) is None
    plain = last_answer(
        {
            "question": "Doanh thu năm 2024",
            "metric_id": "revenue",
            "payload": {"table": [{"revenue": "1"}], "answer_text": "x", "sources": {}},
        }
    )
    assert plain and "not a forecast" in plain["text"]
    predicted = last_answer(
        {
            "question": "Dự báo doanh thu",
            "metric_id": "revenue",
            "payload": {
                "table": [],
                "answer_text": "Dự báo...",
                "sources": {
                    "forecast": {"method": "recent_average", "history_months": 36}
                },
            },
        }
    )
    assert predicted and "recent_average" in predicted["text"]


def test_dimension_ids_are_replaced_by_their_names_in_replies() -> None:
    llm = FakeLLM({})
    llm.replies["x"] = "Chiều Khu vực bán hàng (sales_territory) có 10 khu vực."
    budget = RequestBudget(30, 3)
    text = reply_from_metadata(llm, "x", "manager", "answer", "2025-06-29", budget)
    assert text is not None and "sales_territory" not in text
