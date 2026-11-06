import re
from collections.abc import Iterable

_ALIASES = {
    "full_time": {"full time", "full-time", "fulltime", "permanent", "fte"},
    "part_time": {"part time", "part-time", "parttime"},
    "contract": {"contract", "contractor", "freelance", "temporary contract"},
    "internship": {"intern", "internship", "trainee"},
    "temporary": {"temporary", "temp", "seasonal"},
}


def map_employment_type(value: object) -> str:
    """Map common employment labels and schema.org values to a canonical value."""
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        labels = [map_employment_type(item) for item in value]
        return next((label for label in labels if label), "")
    if not isinstance(value, str):
        return ""

    normalized = re.sub(r"[_\s]+", " ", value).strip().lower()
    normalized = normalized.replace("http://schema.org/", "").replace(
        "https://schema.org/", ""
    )
    normalized = normalized.replace("_", " ")
    for canonical, aliases in _ALIASES.items():
        if normalized == canonical.replace("_", " ") or normalized in aliases:
            return canonical
    return ""
