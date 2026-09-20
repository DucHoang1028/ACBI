"""Small BM25 corpus, filtered by access and approval before ranking."""

import math
import re
from collections import Counter
from typing import Any, Protocol

from app.auth.service import allows
from app.query.validation import allowed_tables


class Retriever(Protocol):
    def retrieve(
        self, question: str, role: str, metric_id: str
    ) -> list[dict[str, Any]]: ...


def terms(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold())


class BM25Retriever:
    def __init__(self, dictionary: dict[str, Any], examples: list[dict[str, Any]]):
        self.dictionary = dictionary
        self.examples = examples

    def retrieve(
        self, question: str, role: str, metric_id: str
    ) -> list[dict[str, Any]]:
        corpus: list[dict[str, Any]] = []
        for metric in self.dictionary["businessMetrics"]:
            if metric["metricId"] != metric_id or not allows(
                role, metric["domain"], 1 if role == "production" else None
            ):
                continue
            for definition in metric["definitions"]:
                if definition["approvalStatus"] != "approved":
                    continue
                corpus.append(
                    {
                        "id": f"metric:{metric_id}:v{definition['version']}",
                        "kind": "definition",
                        "text": str({**metric, "definitions": [definition]}),
                    }
                )
                for mapping in self.dictionary["dataMappings"]:
                    if (
                        mapping["mappingId"] in definition["dataMappingIds"]
                        and mapping["approvalStatus"] == "approved"
                    ):
                        corpus.append(
                            {
                                "id": "mapping:" + mapping["mappingId"],
                                "kind": "mapping",
                                "text": str(mapping),
                            }
                        )
        if not corpus:
            return []
        domain_role = (
            "sales" if metric_id in {"revenue", "sales_growth"} else "production"
        )
        schemas = {
            k: v
            for k, v in allowed_tables(role).items()
            if k in allowed_tables(domain_role)
        }
        corpus.append(
            {"id": "schema:" + domain_role, "kind": "schema", "text": str(schemas)}
        )
        for example in self.examples:
            if (
                example["metric_id"] == metric_id
                and example["approval_status"] == "approved"
            ):
                corpus.append(
                    {
                        "id": example["id"],
                        "kind": "example",
                        "text": example["description"] + "\n" + example["sql"],
                    }
                )
        tokens = [Counter(terms(doc["text"])) for doc in corpus]
        average = sum(sum(t.values()) for t in tokens) / len(tokens)
        query = set(terms(question + " " + metric_id))
        scores = []
        for index, document in enumerate(tokens):
            score = 0.0
            for term in query:
                frequency = document[term]
                containing = sum(term in t for t in tokens)
                inverse = math.log(
                    1 + (len(tokens) - containing + 0.5) / (containing + 0.5)
                )
                score += (
                    inverse
                    * frequency
                    * 2.5
                    / (
                        frequency
                        + 1.5 * (0.25 + 0.75 * sum(document.values()) / average)
                    )
                )
            scores.append((score, index))
        # Mandatory definitions/schema are never dropped by lexical ranking.
        mandatory = [d for d in corpus if d["kind"] != "example"]
        ranked = [
            corpus[i]
            for _, i in sorted(scores, reverse=True)
            if corpus[i]["kind"] == "example"
        ]
        return mandatory + ranked[:2]
