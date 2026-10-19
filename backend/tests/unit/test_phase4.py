"""Saved-result scope and factual summaries survive current-role changes."""

from datetime import date

from app.history.service import permitted
from app.presentation.summary import factual, numbers_match


def test_saved_result_rechecks_current_role_and_factory() -> None:
    factory_a = {"domain": "production", "factory_id": 1}
    factory_b = {"domain": "production", "factory_id": 2}
    revenue = {"domain": "sales", "factory_id": None}
    assert permitted(factory_a, "production")
    assert not permitted(factory_b, "production")
    assert not permitted(factory_a, "sales")
    assert permitted(factory_b, "manager")
    assert permitted(revenue, "sales")
    assert not permitted(revenue, "it_admin")


def test_summary_numbers_must_come_from_result_rows() -> None:
    rows = [{"revenue": "123.45", "sample_count": 3}]
    assert numbers_match("Revenue 123.45 from 3 orders.", rows)
    assert not numbers_match("Revenue 999.00 from 3 orders.", rows)
    assert not numbers_match("Revenue 123.45 on 2025-05-01.", rows)
    assert factual("revenue", rows, date(2025, 5, 1), date(2025, 6, 1)) == (
        "Revenue: 123.45 source currency for 2025-05-01 to 2025-05-31."
    )


def test_clearing_saved_data_deletes_only_the_callers_rows_and_keeps_the_audit() -> (
    None
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.history.service import clear_owned

    seen: list[tuple[str, dict[str, object]]] = []

    class Connection:
        def execute(self, statement: object, params: dict[str, object]) -> object:
            seen.append((str(statement), params))
            return SimpleNamespace(rowcount=3)

    class Engine:
        @contextmanager
        def begin(self):  # type: ignore[no-untyped-def]
            yield Connection()

    assert clear_owned(Engine(), 7) == 3  # type: ignore[arg-type]
    statements = [s for s, _ in seen]
    assert statements[0].startswith("DELETE FROM saved_results")  # child rows first
    assert statements[1].startswith("DELETE FROM chat_context")
    assert all(p == {"user_id": 7} for _, p in seen)
    assert not any("access_audit" in s for s in statements)
