from django.contrib import admin

from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboPastoralZoneProjectMapping,
    KoboProjectBinding,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)


@admin.register(KoboDiscoveredAsset)
class KoboDiscoveredAssetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "asset_uid",
        "asset_type",
        "deployment_status",
        "is_available",
        "last_seen_at",
    )
    list_filter = ("is_available", "asset_type", "deployment_status")
    search_fields = ("name", "asset_uid", "owner_username")
    readonly_fields = ("metadata_snapshot", "discovered_at", "last_seen_at")

    def has_add_permission(self, request):
        # PRE: request is an authenticated admin request.
        # POST: always prevents manual creation of discovered remote assets.
        return False

    def has_delete_permission(self, request, obj=None):
        # PRE: request targets discovered remote asset administration.
        # POST: always preserves discovery history from manual deletion.
        return False


@admin.register(KoboAsset)
class KoboAssetAdmin(admin.ModelAdmin):
    list_display = ("asset_uid", "name", "form_role", "is_active", "updated_at")
    list_filter = ("form_role", "is_active")
    search_fields = ("asset_uid", "name", "form_definition__form_id")


@admin.register(KoboProjectBinding)
class KoboProjectBindingAdmin(admin.ModelAdmin):
    list_display = (
        "asset",
        "routing_type",
        "source_field",
        "source_value",
        "project",
        "is_active",
    )
    list_filter = ("routing_type", "is_active", "asset")
    search_fields = (
        "asset__asset_uid",
        "asset__name",
        "project__code",
        "project__name",
    )


@admin.register(KoboFormDefinition)
class KoboFormDefinitionAdmin(admin.ModelAdmin):
    list_display = ("form_id", "title", "version", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("form_id", "title", "version")


@admin.register(KoboSubmission)
class KoboSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "form_definition",
        "status",
        "parish",
        "received_at",
    )
    search_fields = ("external_id", "parish", "primary_community")
    list_filter = ("status", "routing_status", "form_definition", "pastoral_zone", "asset", "project")
    readonly_fields = (
        "raw_payload",
        "normalized_payload",
        "asset",
        "project",
        "imported_at",
    )


@admin.register(KoboPastoralZoneProjectMapping)
class KoboPastoralZoneProjectMappingAdmin(admin.ModelAdmin):
    list_display = ("pastoral_zone", "project", "is_active", "updated_at")
    list_filter = ("pastoral_zone", "is_active", "project")
    search_fields = ("pastoral_zone", "project__code", "project__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(KoboTerritorialIdentity)
class KoboTerritorialIdentityAdmin(admin.ModelAdmin):
    list_display = ("nucleo_code_normalized", "pastoral_zone", "project", "status")
    list_filter = ("pastoral_zone", "status", "project")
    search_fields = ("nucleo_code_original", "nucleo_code_normalized", "project__code")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        # PRE: obj is either a new identity or its persisted admin instance.
        # POST: protects the identity code, source evidence, zone, and project after creation.
        if obj is None:
            return self.readonly_fields
        return self.readonly_fields + (
            "nucleo_code_original",
            "nucleo_code_normalized",
            "pastoral_zone",
            "project",
            "source_submission",
        )


@admin.register(KoboTerritorialIdentityConflict)
class KoboTerritorialIdentityConflictAdmin(admin.ModelAdmin):
    list_display = (
        "identity",
        "existing_pastoral_zone",
        "proposed_pastoral_zone",
        "status",
        "created_at",
    )
    list_filter = ("status", "existing_pastoral_zone", "proposed_pastoral_zone")
    search_fields = ("identity__nucleo_code_normalized", "incoming_submission__external_id")
    readonly_fields = (
        "identity",
        "incoming_submission",
        "existing_pastoral_zone",
        "proposed_pastoral_zone",
        "status",
        "resolution",
        "resolved_by",
        "resolved_at",
        "created_at",
    )

    def has_add_permission(self, request):
        # PRE: request is an authenticated admin request.
        # POST: prevents creating conflicts outside a future idempotent routing service.
        return False


@admin.register(KoboAttachment)
class KoboAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "submission",
        "field_name",
        "privacy_level",
        "status",
    )
    list_filter = ("privacy_level", "status", "content_type")
    search_fields = ("external_id", "original_filename", "field_name")


@admin.register(KoboProcessingEvent)
class KoboProcessingEventAdmin(admin.ModelAdmin):
    list_display = ("submission", "stage", "level", "code", "created_at")
    list_filter = ("level", "stage")
    search_fields = ("submission__external_id", "code", "message")
