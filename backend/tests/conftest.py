"""Install the dictionary vocabulary with sample members, as startup does."""

from pathlib import Path

import pytest
import yaml
from app.metadata import vocabulary

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(autouse=True, scope="session")
def installed_vocabulary() -> None:
    dictionary = yaml.safe_load(
        (DATA / "business_dictionary/dictionary.yaml").read_text("utf-8")
    )
    vocab = vocabulary.Vocabulary(
        dictionary,
        members={
            "sales_territory": [
                "Australia",
                "Canada",
                "Central",
                "France",
                "Germany",
                "Northeast",
                "Northwest",
                "Southeast",
                "Southwest",
                "United Kingdom",
            ],
            "factory": ["Factory A", "Factory B", "Factory C"],
            "product_category": ["Accessories", "Bikes", "Clothing", "Components"],
        },
    )
    vocab.member_ids = {"factory": {"Factory A": 1, "Factory B": 2, "Factory C": 3}}
    vocabulary.install(vocab)
