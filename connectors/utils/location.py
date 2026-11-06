_COUNTRIES = {
    "australia",
    "brazil",
    "canada",
    "china",
    "denmark",
    "finland",
    "france",
    "germany",
    "india",
    "ireland",
    "italy",
    "japan",
    "mexico",
    "netherlands",
    "new zealand",
    "norway",
    "singapore",
    "south korea",
    "spain",
    "sweden",
    "switzerland",
    "uk",
    "united kingdom",
    "us",
    "usa",
    "united states",
    "united states of america",
}


def split_location(value: object) -> tuple[str, str, str]:
    """Split comma-separated locations into city, region, and country.

    For two components, recognize common country names and codes; otherwise,
    interpret the second component as a region.
    """
    if not isinstance(value, str):
        return "", "", ""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        if parts[1].casefold() in _COUNTRIES:
            return parts[0], "", parts[1]
        return parts[0], parts[1], ""
    return parts[0], ", ".join(parts[1:-1]), parts[-1]
