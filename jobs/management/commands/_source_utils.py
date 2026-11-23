import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from connectors.registry import get_connector
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


def build_source_config(platform: str, url: str, slug: str | None) -> tuple[str, dict]:
    validate_source_url(url)
    connector = get_connector(platform)

    if platform == "workday":
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").split(".")
        tenant = hostname[0] if hostname else ""
        wd_host = hostname[1] if len(hostname) > 1 else ""
        site = slug or next(
            (segment for segment in parsed.path.split("/") if segment),
            "",
        )
        if not tenant or not re.fullmatch(r"wd\d+", wd_host, re.IGNORECASE):
            raise ValidationError(
                "Workday --url must use a <tenant>.wdN.myworkdayjobs.com host"
            )
        validate_slug(site)
        return connector.kind, {
            "base_url": url,
            "company_slug": f"{tenant}-{site}",
            "tenant": tenant,
            "wd_host": wd_host,
            "site": site,
        }

    slug = slug or _slug_from_url(url)
    validate_slug(slug)
    config = {"base_url": url, "company_slug": slug}
    slug_fields = {
        "greenhouse": "board_token",
        "ashby": "board_name",
        "workable": "subdomain",
        "smartrecruiters": "company",
    }
    config[slug_fields.get(platform, "company_slug")] = slug
    return connector.kind, config


def validate_source_url(url: str) -> None:
    try:
        URLValidator(schemes=["http", "https"])(url)
    except ValidationError as exc:
        raise ValidationError("--url must be a valid HTTP or HTTPS URL") from exc


def validate_slug(slug: str | None) -> None:
    if not slug or not _SLUG_PATTERN.fullmatch(slug.strip()):
        raise ValidationError(
            "--slug (or a slug in --url) must contain only letters, numbers, "
            "underscores, or hyphens"
        )


def _slug_from_url(url: str) -> str:
    parsed = urlsplit(url)
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    return path_segments[-1] if path_segments else (parsed.hostname or "").split(".")[0]


def source_kind(connector_kind: str) -> str:
    return Source.Kind.ATS if connector_kind == "ats" else Source.Kind.JOB_BOARD
