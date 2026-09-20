"""The evaluation sets stay well formed: enough questions, every kind covered."""

from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[3] / "data" / "eval"
STATUSES = {"ok", "needs_clarification", "denied", "no_data", "technical_failure"}


def golden() -> list[dict]:
    return yaml.safe_load((DATA / "golden_questions.yaml").read_text("utf-8"))[
        "questions"
    ]


def test_golden_set_is_large_and_covers_every_outcome() -> None:
    items = golden()
    assert len(items) >= 30
    assert len({i["id"] for i in items}) == len(items)
    seen = {i["expected_status"] for i in items}
    assert {"ok", "needs_clarification", "denied", "no_data"} <= seen
    for item in items:
        assert item["expected_status"] in STATUSES
        assert item["user"] in {"manager", "sales", "production_a", "it_admin"}
        if item["expected_status"] in {"ok", "no_data"}:
            assert item["reference_sql"].strip().upper().startswith(("SELECT", "WITH"))


def test_groq_edge_cases_are_well_formed() -> None:
    items = yaml.safe_load((DATA / "groq_edge_cases.yaml").read_text("utf-8"))
    assert len(items) >= 50
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        assert set(item["expect"]) <= STATUSES, item["id"]
        assert item["question"].strip()
