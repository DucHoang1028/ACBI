"""Business vocabulary read from the approved dictionary and the data itself.

Metric and dimension names, synonyms and the members of each dimension (territory
names, factories, ...) live in the Business Dictionary and the warehouse, not in
code, so a changed or different database needs a dictionary edit, not a code edit.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.text import fold

logger = logging.getLogger("acbi.vocabulary")
MEMBER_LIMIT = 200
NOT_A_BREAKDOWN = {
    "nam",
    "quy",
    "thang",
    "tuan",
    "ngay",
    "ky",
    "year",
    "quarter",
    "month",
    "week",
    "day",
    "hom",
    "doi",
    "toi",
    "nhu",
    "yeu",
    "do",
    "cach",
    "mot",
    "cai",
    "the",
    "nay",
    "dieu",
    "ket",
    "so",
    "chi",
}  # calendar units and ordinary words that follow 'theo'


@dataclass(frozen=True)
class Named:
    id: str
    vi: str
    en: str
    synonyms: tuple[str, ...]  # folded

    def label(self, language: str) -> str:
        return self.vi if language == "vi" else self.en


@dataclass(frozen=True)
class Metric(Named):
    formula: str = ""
    description: str = ""
    unit: str = ""
    domain: str = ""
    dimensions: tuple[str, ...] = ()
    date_column: str = ""


@dataclass(frozen=True)
class Dimension(Named):
    grains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source: dict[str, str] | None = None


def alternation(words: list[str] | tuple[str, ...]) -> str:
    """Regex over folded words; longest first, never inside a dotted identifier."""
    ordered = sorted({w for w in words if w}, key=len, reverse=True)
    return "(?<![\\w.])(?:" + "|".join(re.escape(w) for w in ordered) + ")(?![\\w.])"


class Vocabulary:
    def __init__(
        self,
        dictionary: dict[str, Any],
        members: dict[str, list[str]] | None = None,
        coverage: dict[str, tuple[str, str]] | None = None,
    ):
        self.metrics: dict[str, Metric] = {}
        for entry in dictionary["businessMetrics"]:
            approved = [
                d for d in entry["definitions"] if d["approvalStatus"] == "approved"
            ]
            if not approved:
                continue
            current = max(approved, key=lambda d: d["version"])
            names = entry.get("displayName", {})
            synonyms = entry.get("synonyms", {})
            self.metrics[entry["metricId"]] = Metric(
                id=entry["metricId"],
                vi=names.get("vi", entry["name"]),
                en=names.get("en", entry["name"]),
                synonyms=tuple(
                    fold(w) for w in synonyms.get("vi", []) + synonyms.get("en", [])
                ),
                formula=current["formula"],
                description=entry.get("description", ""),
                unit=current["unit"],
                domain=entry["domain"],
                dimensions=tuple(entry.get("supportedDimensions", [])),
                date_column=current.get("dateColumn", ""),
            )
        self.dimensions: dict[str, Dimension] = {}
        for entry in dictionary["dimensions"]:
            names = entry.get("displayName", {})
            synonyms = entry.get("synonyms", {})
            self.dimensions[entry["dimensionId"]] = Dimension(
                id=entry["dimensionId"],
                vi=names.get("vi", entry["name"]),
                en=names.get("en", entry["name"]),
                synonyms=tuple(
                    fold(w) for w in synonyms.get("vi", []) + synonyms.get("en", [])
                ),
                grains={
                    grain: tuple(
                        fold(w) for w in words.get("vi", []) + words.get("en", [])
                    )
                    for grain, words in (entry.get("grains") or {}).items()
                },
                source=entry.get("memberSource"),
            )
        self.sources = dictionary.get("dataSources", [])
        self.members = members or {}
        self.member_ids: dict[str, dict[str, int]] = {}
        self.coverage = coverage or {}

    # ---- names -------------------------------------------------------------
    def metric_label(self, metric_id: str, language: str) -> str:
        found = self.metrics.get(metric_id)
        return found.label(language) if found else metric_id

    def ratio_metrics(self) -> set[str]:
        return {m.id for m in self.metrics.values() if m.unit == "ratio"}

    def metric_words(self) -> str:
        words = [w for m in self.metrics.values() for w in m.synonyms]
        return alternation(words) if words else "(?!)"

    # ---- matching ----------------------------------------------------------
    def match_metrics(self, folded: str) -> list[str]:
        """Metric ids named in the text, in order of appearance."""
        hits = []
        for metric in self.metrics.values():
            match = re.search(alternation(metric.synonyms), folded)
            if match:
                hits.append((match.start(), metric.id))
        return [metric_id for _, metric_id in sorted(hits)]

    def match_dimensions(self, folded: str) -> list[str]:
        """Breakdowns named in the text, in order; date grains read month/day/week."""
        hits: list[tuple[int, str]] = []
        spans: list[tuple[int, int]] = []
        for dimension in self.dimensions.values():
            if dimension.synonyms:
                match = re.search(alternation(dimension.synonyms), folded)
                if match:
                    hits.append((match.start(), dimension.id))
                    spans.append(match.span())
        for dimension in self.dimensions.values():
            for grain, words in dimension.grains.items():
                # A grain counts only as "by month"; a bare month is a date.
                match = re.search(
                    r"(?<![\w.])(?:theo|moi|hang|tung|by|per|each|every)\s+"
                    + alternation(words),
                    folded,
                )
                # Folding makes "dây" read as "day": a longer name wins.
                if match and not any(a <= match.end() <= b for a, b in spans):
                    hits.append((match.start(), grain))
        ordered = [name for _, name in sorted(hits)]
        if "product_category" in ordered and "product" in ordered:
            ordered.remove("product")  # "nhóm sản phẩm" is not a per-product split
        return ordered

    def match_members(self, folded: str) -> dict[str, list[str]]:
        """Dimension members (territory names, factories, ...) present in the text."""
        found: dict[str, list[str]] = {}
        for dimension, names in self.members.items():
            for name in names:
                if re.search(alternation([fold(name)]), folded):
                    found.setdefault(dimension, []).append(name)
        return found

    def member_names(self, folded: str, dimension: str) -> list[str] | None:
        """Canonical members named in free text; None when a word is unknown."""
        known = self.members.get(dimension)
        if not known:
            return []  # the catalogue is empty: nothing to validate against
        names = [n for n in known if re.search(alternation([fold(n)]), folded)]
        rest = folded
        for name in names:
            rest = re.sub(alternation([fold(name)]), " ", rest)
        leftover = re.sub(r"\b(?:va|and|voi|cung)\b|[|,&]", " ", rest).split()
        return names if names and not leftover else None

    def unknown_member_reference(self, folded: str) -> str | None:
        """A dimension named with a short identifier that is not a member ("Factory D").

        Such a request must be asked about, not answered for every member."""
        for dimension in self.dimensions.values():
            known = self.members.get(dimension.id)
            if not known or not dimension.synonyms:
                continue
            tails = {fold(name).split()[-1] for name in known}
            for match in re.finditer(
                alternation(dimension.synonyms) + r"\s+([a-z]|\d{1,2})\b", folded
            ):
                if match.group(1) not in tails:
                    return dimension.id
        return None

    def unrecognised_breakdown(self, folded: str) -> str | None:
        """A "by X" phrase that names no known breakdown, metric or period unit."""
        for match in re.finditer(
            r"(?<![\w.])(?:theo|by|per|each)\s+(\w+(?:\s+\w+)?)", folded
        ):
            phrase = match.group(1)
            first = phrase.split()[0]
            if first in NOT_A_BREAKDOWN or first.isdigit():
                continue
            if (
                self.match_dimensions("theo " + phrase)
                or self.match_metrics(phrase)
                or any(
                    re.search(alternation([fold(n)]), phrase)
                    for names in self.members.values()
                    for n in names
                )
            ):
                continue
            return phrase
        return None

    def capitalised_after_dimension(self, question: str) -> list[tuple[str, str]]:
        """Proper names right after a dimension word, e.g. khu vực Atlantis."""
        tokens = re.findall(r"\S+", question)
        folded = [fold(re.sub(r"[^\w]", "", t)) for t in tokens]
        found: list[tuple[str, str]] = []
        for dimension in self.dimensions.values():
            if not self.members.get(dimension.id):
                continue
            for synonym in dimension.synonyms:
                words = synonym.split()
                for i in range(len(folded) - len(words)):
                    if folded[i : i + len(words)] == words:
                        nxt = tokens[i + len(words)]
                        if nxt[:1].isupper():
                            found.append((dimension.id, re.sub(r"[^\w-]", "", nxt)))
        return found

    def ids(self, dimension: str) -> dict[str, int]:
        return self.member_ids.get(dimension, {})

    # ---- prompts and references -------------------------------------------
    def describe(self, role_visible: set[str] | None = None) -> str:
        """The approved vocabulary as text for the model (no access information)."""
        lines = ["Metrics:"]
        for m in self.metrics.values():
            if role_visible is not None and m.id not in role_visible:
                continue
            lines.append(
                f"- {m.id} ({m.vi} / {m.en}): {m.description} Formula {m.formula}; "
                f"unit {m.unit}; split by {', '.join(m.dimensions)}"
            )
        lines.append("Dimensions:")
        for d in self.dimensions.values():
            members = self.members.get(d.id, [])
            ids = self.member_ids.get(d.id, {})
            names = [f"{n} (id {ids[n]})" if n in ids else n for n in members[:40]]
            shown = ", ".join(names) + (" ..." if len(members) > 40 else "")
            lines.append(
                f"- {d.id} ({d.vi} / {d.en})" + (f": {shown}" if members else "")
            )
        return "\n".join(lines)


_current: Vocabulary | None = None


def install(vocabulary: Vocabulary) -> None:
    global _current
    _current = vocabulary


def get() -> Vocabulary:
    """The installed vocabulary; loaded from the dictionary file when none is."""
    global _current
    if _current is None:
        root = Path(__file__).resolve().parents[3] / "data"
        text = (root / "business_dictionary/dictionary.yaml").read_text("utf-8")
        _current = Vocabulary(yaml.safe_load(text))
    return _current


def load_members(engine: Any, vocabulary: Vocabulary) -> None:
    """Read each dimension's members from the warehouse, as the dictionary declares."""
    from sqlalchemy import text

    members: dict[str, list[str]] = {}
    member_ids: dict[str, dict[str, int]] = {}
    coverage: dict[str, tuple[str, str]] = {}
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            for dimension in vocabulary.dimensions.values():
                source = dimension.source
                if not source:
                    continue
                id_column = source.get("idColumn")
                pick = (
                    f"{source['column']}, {id_column}"
                    if id_column
                    else source["column"]
                )
                rows = connection.execute(
                    text(
                        f"SELECT DISTINCT {pick} FROM {source['table']} "
                        f"WHERE {source['column']} IS NOT NULL ORDER BY 1 "
                        f"LIMIT {MEMBER_LIMIT}"
                    )
                ).all()
                members[dimension.id] = [str(r[0]) for r in rows]
                if id_column:
                    member_ids[dimension.id] = {str(r[0]): int(r[1]) for r in rows}
            for metric in vocabulary.metrics.values():
                if not metric.date_column:
                    continue
                table, _, column = metric.date_column.rpartition(".")
                low, high = connection.execute(
                    text(
                        f"SELECT MIN({column})::date, MAX({column})::date FROM {table}"
                    )
                ).one()
                coverage[metric.id] = (str(low), str(high))
    except Exception:
        logger.warning(
            "Could not read dimension members from the warehouse", exc_info=True
        )
    vocabulary.members = members
    vocabulary.member_ids = member_ids
    vocabulary.coverage = coverage
