from django.contrib import admin

from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboProjectBinding,
    KoboProcessingEvent,
    KoboSubmission,
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
    list_filter = ("status", "form_definition", "pastoral_zone", "asset", "project")
    readonly_fields = (
        "raw_payload",
        "normalized_payload",
        "asset",
        "project",
        "imported_at",
    )


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
