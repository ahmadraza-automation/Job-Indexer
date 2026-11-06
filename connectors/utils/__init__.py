from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.jsonld import extract_job_postings
from connectors.utils.location import split_location
from connectors.utils.remote import detect_remote
from connectors.utils.salary import Salary, parse_salary

__all__ = [
    "Salary",
    "detect_remote",
    "extract_job_postings",
    "html_to_text",
    "map_employment_type",
    "parse_salary",
    "split_location",
]
