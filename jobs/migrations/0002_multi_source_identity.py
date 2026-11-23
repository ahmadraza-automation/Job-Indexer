import hashlib
from urllib.parse import urlsplit

from django.db import migrations, models


def _identity_key(platform, base_url, config):
    identity = ""
    if platform == "workday" and isinstance(config, dict):
        tenant = config.get("tenant")
        site = config.get("site")
        if isinstance(tenant, str) and tenant.strip() and isinstance(site, str) and site.strip():
            identity = f"workday:{tenant.strip()}:{site.strip()}".casefold()

    identity_keys = {
        "greenhouse": ("board_token",),
        "ashby": ("board_name",),
        "workable": ("subdomain",),
        "smartrecruiters": ("company",),
    }.get(
        platform,
        ("company_slug", "board_token", "board_name", "company", "subdomain"),
    )
    if isinstance(config, dict):
        for key in identity_keys:
            if identity:
                break
            value = config.get(key)
            if isinstance(value, str) and value.strip():
                identity = f"slug:{value.strip().casefold()}"
                break
    if not identity:
        parsed = urlsplit(base_url.strip())
        host = (parsed.hostname or "").casefold()
        if parsed.port and (parsed.scheme.lower(), parsed.port) not in {
            ("http", 80),
            ("https", 443),
        }:
            host = f"{host}:{parsed.port}"
        identity = "|".join(
            (parsed.scheme.lower(), host, parsed.path.rstrip("/"), parsed.query)
        ).casefold()
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def populate_source_identity(apps, schema_editor):
    Source = apps.get_model("jobs", "Source")
    FetchRun = apps.get_model("jobs", "FetchRun")
    for source in Source.objects.all().iterator():
        source.identity_key = _identity_key(
            source.platform,
            source.base_url,
            source.config,
        )
        source.last_failure_at = (
            FetchRun.objects.filter(
                source_id=source.pk,
                status="failed",
                finished_at__isnull=False,
            )
            .order_by("-finished_at")
            .values_list("finished_at", flat=True)
            .first()
        )
        source.save(update_fields=["identity_key", "last_failure_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="source",
            name="platform",
            field=models.CharField(max_length=100),
        ),
        migrations.AddField(
            model_name="source",
            name="identity_key",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="source",
            name="last_failure_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(populate_source_identity, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="source",
            name="identity_key",
            field=models.CharField(editable=False, max_length=64),
        ),
        migrations.AlterField(
            model_name="source",
            name="kind",
            field=models.CharField(
                choices=[("ats", "ATS"), ("job_board", "Job board")],
                max_length=10,
            ),
        ),
        migrations.RunSQL(
            sql="UPDATE jobs_source SET kind = 'job_board' WHERE kind = 'board'",
            reverse_sql="UPDATE jobs_source SET kind = 'board' WHERE kind = 'job_board'",
        ),
        migrations.AddConstraint(
            model_name="source",
            constraint=models.UniqueConstraint(
                fields=("platform", "identity_key"),
                name="source_platform_identity_uniq",
            ),
        ),
    ]
