from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class NormalizedJob:
    title: str
    company: str
    description: str = ""
    location: str = ""
    source_url: str = ""
    apply_url: str = ""
    external_id: str = ""
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = ""
    salary_period: str = ""
    employment_type: str = ""
    is_remote: bool | None = None
    posted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    department: str = ""
    updated_at: datetime | None = None
