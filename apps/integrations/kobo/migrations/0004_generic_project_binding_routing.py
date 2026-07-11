from django.db import migrations, models


def migrate_pastoral_bindings(apps, schema_editor):
    # PRE: every existing KoboProjectBinding has a valid pastoral_zone.
    # POST: every binding preserves asset/project and routes by the same zone.
    KoboProjectBinding = apps.get_model("kobo", "KoboProjectBinding")
    for binding in KoboProjectBinding.objects.all().iterator():
        binding.routing_type = "field_value"
        binding.source_field = "submission.pastoral_zone"
        binding.source_value = binding.pastoral_zone
        binding.save(
            update_fields=("routing_type", "source_field", "source_value")
        )


class Migration(migrations.Migration):

    dependencies = [
        ("kobo", "0003_kobosubmission_asset_kobosubmission_imported_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="koboprojectbinding",
            name="routing_type",
            field=models.CharField(
                choices=[("direct", "Direct"), ("field_value", "Field value")],
                default="field_value",
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="koboprojectbinding",
            name="source_field",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="koboprojectbinding",
            name="source_value",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.RunPython(
            migrate_pastoral_bindings,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="koboprojectbinding",
            name="kobo_unique_asset_pastoral_zone",
        ),
        migrations.RemoveConstraint(
            model_name="koboprojectbinding",
            name="kobo_unique_asset_project",
        ),
        migrations.RemoveConstraint(
            model_name="koboprojectbinding",
            name="kobo_binding_valid_pastoral_zone",
        ),
        migrations.RemoveField(
            model_name="koboprojectbinding",
            name="pastoral_zone",
        ),
        migrations.AddConstraint(
            model_name="koboprojectbinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(("routing_type", "direct")),
                fields=("asset",),
                name="kobo_unique_direct_per_asset",
            ),
        ),
        migrations.AddConstraint(
            model_name="koboprojectbinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(("routing_type", "field_value")),
                fields=("asset", "source_field", "source_value"),
                name="kobo_unique_field_route",
            ),
        ),
        migrations.AddConstraint(
            model_name="koboprojectbinding",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("routing_type", "direct"),
                        ("source_field", ""),
                        ("source_value", ""),
                    )
                    | (
                        models.Q(("routing_type", "field_value"))
                        & ~models.Q(("source_field", ""))
                        & ~models.Q(("source_value", ""))
                    )
                ),
                name="kobo_binding_valid_route_fields",
            ),
        ),
        migrations.AddConstraint(
            model_name="koboprojectbinding",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("routing_type__in", ("direct", "field_value"))
                ),
                name="kobo_binding_valid_routing_type",
            ),
        ),
        migrations.AlterModelOptions(
            name="koboprojectbinding",
            options={
                "ordering": (
                    "asset",
                    "routing_type",
                    "source_field",
                    "source_value",
                )
            },
        ),
    ]
