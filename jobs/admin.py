from django.contrib import admin

from jobs.models import Company, FetchRun, Job, JobChange, JobSource, Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        "platform",
        "name",
        "kind",
        "is_active",
        "check_interval_minutes",
        "consecutive_failures",
    )
    list_filter = ("kind", "is_active")
    search_fields = ("platform", "name")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_name", "website")
    search_fields = ("name", "normalized_name")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "status", "is_remote", "posted_at", "first_seen_at")
    list_filter = ("status", "is_remote", "employment_type", "country")
    search_fields = ("title", "company__name", "description", "city", "region", "country")
    autocomplete_fields = ("company",)
    readonly_fields = ("first_seen_at",)


@admin.register(JobSource)
class JobSourceAdmin(admin.ModelAdmin):
    list_display = ("external_id", "job", "source", "status", "last_seen_at", "missed_runs")
    list_filter = ("status", "source")
    search_fields = ("external_id", "job__title", "source__name")
    autocomplete_fields = ("job", "source")
    readonly_fields = ("first_seen_at", "last_seen_at")


@admin.register(FetchRun)
class FetchRunAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "started_at",
        "finished_at",
        "status",
        "jobs_found",
        "jobs_closed",
        "http_status",
    )
    list_filter = ("status", "source")
    search_fields = ("source__name", "error_message")
    readonly_fields = ("started_at",)


@admin.register(JobChange)
class JobChangeAdmin(admin.ModelAdmin):
    list_display = ("job_source", "changed_at", "field", "old_value", "new_value")
    list_filter = ("field",)
    search_fields = ("job_source__external_id", "job_source__job__title", "field")
    readonly_fields = ("changed_at",)
