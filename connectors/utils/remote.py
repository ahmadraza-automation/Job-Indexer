import re

_REMOTE = re.compile(
    r"\b(?:remote|work\s+from\s+home|wfh|telecommut(?:e|ing)|distributed)\b",
    re.IGNORECASE,
)
_NOT_REMOTE = re.compile(r"\bnot\s+remote\b", re.IGNORECASE)
_ON_SITE = re.compile(r"\b(?:on[- ]?site|in[- ]office|office[- ]based)\b", re.IGNORECASE)


def detect_remote(value: object) -> bool | None:
    """Return whether text indicates remote work, or None when unspecified."""
    if not isinstance(value, str):
        return None
    if _NOT_REMOTE.search(value):
        return False
    if _REMOTE.search(value):
        return True
    if _ON_SITE.search(value):
        return False
    return None
