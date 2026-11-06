from selectolax.parser import HTMLParser


def html_to_text(value: object) -> str:
    """Convert HTML to readable text while excluding non-content elements."""
    if not isinstance(value, str) or not value:
        return ""
    parser = HTMLParser(value)
    for selector in ("script", "style", "noscript", "svg", "template"):
        for node in parser.css(selector):
            node.decompose()
    return " ".join(parser.root.text(separator=" ").split())
