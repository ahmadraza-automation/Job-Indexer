from decimal import Decimal
from pathlib import Path

import pytest

from connectors.types import NormalizedJob
from connectors.utils import (
    detect_remote,
    extract_job_postings,
    html_to_text,
    map_employment_type,
    parse_salary,
    split_location,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("text", "minimum", "maximum", "currency", "period"),
    [
        ("$80,000 - $100,000 per year", Decimal("80000"), Decimal("100000"), "USD", "year"),
        ("EUR 45k to 60k monthly", Decimal("45000"), Decimal("60000"), "EUR", "month"),
        ("£25 per hour", Decimal("25"), Decimal("25"), "GBP", "hour"),
        ("$80k-$100k/year", Decimal("80000"), Decimal("100000"), "USD", "year"),
        ("100k/month", Decimal("100000"), Decimal("100000"), "", "month"),
        ("AUD 90,000 annually", Decimal("90000"), Decimal("90000"), "AUD", "year"),
        ("Salary negotiable", None, None, "", ""),
        ("", None, None, "", ""),
        (None, None, None, "", ""),
    ],
)
def test_parse_salary(
    text: object,
    minimum: Decimal | None,
    maximum: Decimal | None,
    currency: str,
    period: str,
) -> None:
    parsed = parse_salary(text)

    assert (parsed.minimum, parsed.maximum, parsed.currency, parsed.period) == (
        minimum,
        maximum,
        currency,
        period,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fully remote role", True),
        ("Work from home", True),
        ("Telecommuting position", True),
        ("Hybrid; remote two days/week", True),
        ("On-site in Seattle", False),
        ("Not remote", False),
        ("", None),
        (None, None),
    ],
)
def test_detect_remote(text: object, expected: bool) -> None:
    assert detect_remote(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FULL_TIME", "full_time"),
        ("Full-time", "full_time"),
        ("PART_TIME", "part_time"),
        ("CONTRACTOR", "contract"),
        ("Internship", "internship"),
        ("TEMPORARY", "temporary"),
        (["OTHER", "PART_TIME"], "part_time"),
        ("OTHER", ""),
        (None, ""),
    ],
)
def test_map_employment_type(text: object, expected: str) -> None:
    assert map_employment_type(text) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<p>Hello <b>world</b></p><script>secret()</script>", "Hello world"),
        ("<style>.hidden{}</style><div>A&nbsp; B</div>", "A B"),
        ("", ""),
        (None, ""),
    ],
)
def test_html_to_text(markup: object, expected: str) -> None:
    assert html_to_text(markup) == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Seattle, WA, USA", ("Seattle", "WA", "USA")),
        ("Austin, TX", ("Austin", "TX", "")),
        ("London, UK", ("London", "", "UK")),
        ("Remote", ("Remote", "", "")),
        ("", ("", "", "")),
        (None, ("", "", "")),
    ],
)
def test_split_location(location: object, expected: tuple[str, str, str]) -> None:
    assert split_location(location) == expected


def test_extract_job_posting_fixture() -> None:
    html = (FIXTURES / "schema_jobposting.html").read_text(encoding="utf-8")

    jobs = list(extract_job_postings(html, page_url="https://jobs.example.com/listing"))

    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, NormalizedJob)
    assert (job.title, job.company, job.external_id) == (
        "Senior Engineer",
        "Example Corp",
        "eng-42",
    )
    assert job.location == "Seattle, WA, US"
    assert job.source_url == "https://jobs.example.com/eng-42"
    assert job.apply_url == "https://jobs.example.com/eng-42/apply"
    assert job.description == "Build reliable systems."
    assert (job.salary_min, job.salary_max) == (
        Decimal("120000"),
        Decimal("150000"),
    )
    assert job.salary_currency == "USD"
    assert job.salary_period == "year"
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at is not None
    assert job.raw["@type"] == "JobPosting"


@pytest.mark.parametrize(
    "html",
    [
        "",
        "not html",
        '<script type="application/ld+json">{bad json}</script>',
        '<script type="application/ld+json">{"@type":"JobPosting"}</script>',
        '<script type="application/ld+json">[null, {"@type":"Thing"}]</script>',
    ],
)
def test_extract_job_postings_ignores_malformed_or_incomplete_input(html: str) -> None:
    assert list(extract_job_postings(html)) == []


def test_extract_job_postings_handles_graph_and_arrays() -> None:
    html = """
    <script type="application/ld+json">
    {"@graph": [
      {"@type": "Organization", "name": "Not a job"},
      {"@type": "JobPosting", "title": "Developer",
       "hiringOrganization": {"name": "Acme"},
       "employmentType": ["OTHER", "PART_TIME"],
       "datePosted": "invalid"}
    ]}
    </script>
    <script type="application/ld+json">
    [{"@type":"JobPosting","title":"Designer","hiringOrganization":{"name":"Studio"}}]
    </script>
    """

    jobs = list(extract_job_postings(html))

    assert [job.title for job in jobs] == ["Developer", "Designer"]
    assert jobs[0].employment_type == "part_time"
    assert jobs[0].posted_at is None
    assert jobs[1].apply_url == ""
