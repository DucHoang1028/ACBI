"""Text normalisation shared by every keyword match."""

import unicodedata


def fold(value: str) -> str:
    """Lowercase, accent-free text, so "Đức" and "duc" compare equal."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", value.lower())
        if unicodedata.category(c) != "Mn"
    ).replace("đ", "d")
