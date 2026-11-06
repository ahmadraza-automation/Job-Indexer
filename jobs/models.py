from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models


class Source(models.Model):
    class Kind(models.TextChoices):
        ATS = "ats", "ATS"
        BOARD = "board", "Job board"

    platform = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=200)
    base_url = models.URLField()
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    check_interval_minutes = models.PositiveIntegerField(default=60)
    last_success_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["kind", "is_active"], name="source_kind_active_idx")
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Company(models.Model):
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, unique=True)
    website = models.URLField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Job(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        STALE = "stale", "Stale"

    title = models.CharField(max_length=255)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="jobs")
    description = models.TextField(blank=True)
    location_raw = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=150, blank=True)
    region = models.CharField(max_length=150, blank=True)
    country = models.CharField(max_length=150, blank=True)
    is_remote = models.BooleanField(null=True, blank=True)
    employment_type = models.CharField(max_length=50, blank=True)
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField(max_length=3, blank=True)
    salary_period = models.CharField(max_length=20, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "status"], name="job_company_status_idx"),
            models.Index(fields=["status", "posted_at"], name="job_status_posted_idx"),
            GinIndex(fields=["search_vector"], name="job_search_vector_gin"),
        ]
        ordering = ["-posted_at", "-first_seen_at"]

    def __str__(self) -> str:
        return f"{self.title} at {self.company.name}"


class JobSource(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        STALE = "stale", "Stale"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="sources")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="job_links")
    external_id = models.CharField(max_length=255)
    source_url = models.URLField(blank=True)
    apply_url = models.URLField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    last_changed_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    missed_runs = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                name="jobsource_source_external_id_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["source", "status"], name="jobsource_source_status_idx"),
            models.Index(fields=["job", "status"], name="jobsource_job_status_idx"),
        ]
        ordering = ["source", "external_id"]

    def __str__(self) -> str:
        return f"{self.source.platform}:{self.external_id}"


class FetchRun(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"
        SUSPICIOUS = "suspicious", "Suspicious"

    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="fetch_runs")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices)
    jobs_found = models.PositiveIntegerField(default=0)
    jobs_new = models.PositiveIntegerField(default=0)
    jobs_updated = models.PositiveIntegerField(default=0)
    jobs_closed = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source", "-started_at"], name="fetchrun_source_started_idx"),
            models.Index(fields=["status", "-started_at"], name="fetchrun_status_started_idx"),
        ]
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.source.platform} {self.started_at:%Y-%m-%d %H:%M:%S} ({self.status})"


class JobChange(models.Model):
    job_source = models.ForeignKey(JobSource, on_delete=models.CASCADE, related_name="changes")
    changed_at = models.DateTimeField(auto_now_add=True)
    field = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["job_source", "-changed_at"],
                name="jobchange_source_changed_idx",
            )
        ]
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return f"{self.job_source} {self.field} changed at {self.changed_at:%Y-%m-%d %H:%M:%S}"
