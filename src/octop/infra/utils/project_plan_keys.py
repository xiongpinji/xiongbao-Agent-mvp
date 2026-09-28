"""Full Unicode keys shared by plan migrations, writers and restore paths."""

from __future__ import annotations

import unicodedata


def normalize_project_plan_key(value: str) -> str:
    """Normalize the complete value without changing or truncating its display."""
    return unicodedata.normalize("NFKC", value).casefold()


def project_plan_display_sort_key(username: str, display_name: str | None) -> str:
    """Use the persisted nonempty display name, otherwise the username."""
    return normalize_project_plan_key(display_name if display_name else username)
