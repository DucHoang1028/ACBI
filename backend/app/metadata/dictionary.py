from pathlib import Path
from typing import Any

import yaml


def load_dictionary(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (directory / "business_dictionary/dictionary.yaml").read_text(encoding="utf-8")
    )
    return data


def approved_metrics(dictionary: dict[str, Any]) -> list[str]:
    return [
        metric["metricId"]
        for metric in dictionary["businessMetrics"]
        if any(d["approvalStatus"] == "approved" for d in metric["definitions"])
    ]
