from django.contrib import admin

from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboPastoralZoneProjectMapping,
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboProjectBinding,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
    KoboTerritorialProfile,
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


@admin.register(KoboTerritorialProfile)
class KoboTerritorialProfileAdmin(admin.ModelAdmin):
    list_display = (
        "territorial_identity",
        "parish",
        "community_sector",
        "project",
        "created_at",
    )
    list_filter = (
        "territorial_identity__pastoral_zone",
        "project",
        "created_at",
    )
    search_fields = (
        "territorial_identity__nucleo_code_normalized",
        "parish",
        "community_sector",
    )
    readonly_fields = (
        "territorial_identity",
        "project",
        "source_submission",
        "parish",
        "community_sector",
        "location",
        "parish_delegate",
        "contact_phone",
        "main_informant_role",
        "communities_covered",
        "estimated_households",
        "access_difficulties",
        "access_difficulties_notes",
        "initial_priority_perception",
        "general_notes",
        "created_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        # PRE: request targets the territorial profile administration.
        # POST: prevents creating profiles outside the transactional import service.
        return False

    def has_change_permission(self, request, obj=None):
        # PRE: request targets an immutable imported territorial profile.
        # POST: permits safe viewing while rejecting every admin mutation request.
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return super().has_change_permission(request, obj)
        return False

    def has_delete_permission(self, request, obj=None):
        # PRE: request targets imported territorial evidence.
        # POST: always preserves the immutable profile and its traceability.
        return False


@admin.register(KoboPrioritizedMicroproject)
class KoboPrioritizedMicroprojectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "territorial_identity",
        "project",
        "component",
        "implementation_urgency",
        "technical_viability",
        "created_at",
    )
    list_filter = (
        "component",
        "implementation_urgency",
        "technical_viability",
        "project",
        "created_at",
    )
    search_fields = (
        "name",
        "territorial_identity__nucleo_code_normalized",
        "source_submission__primary_community",
        "project__code",
        "project__name",
    )
    readonly_fields = (
        "territorial_identity",
        "project",
        "source_submission",
        "name",
        "component",
        "problem_summary",
        "specific_objective",
        "beneficiary_group",
        "main_activities",
        "estimated_cost_range",
        "implementation_urgency",
        "technical_viability",
        "expected_result",
        "created_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        # PRE: request targets prioritized microproject administration.
        # POST: prevents creation outside the transactional Ficha 10 importer.
        return False

    def has_change_permission(self, request, obj=None):
        # PRE: request targets immutable imported proposal evidence.
        # POST: permits safe viewing while rejecting every admin mutation request.
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return super().has_change_permission(request, obj)
        return False

    def has_delete_permission(self, request, obj=None):
        # PRE: request targets an imported prioritized microproject.
        # POST: always preserves the immutable proposal and its traceability.
        return False


@admin.register(KoboPrioritizationAssessment)
class KoboPrioritizationAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "territorial_identity",
        "project",
        "priority_total_calculated",
        "final_semaphore",
        "final_priority",
        "created_at",
    )
    list_filter = (
        "territorial_identity__pastoral_zone",
        "final_semaphore",
        "final_priority",
        "project",
        "created_at",
    )
    search_fields = (
        "territorial_identity__nucleo_code_normalized",
        "project__code",
        "project__name",
        "priority_summary",
    )
    readonly_fields = (
        "territorial_identity",
        "project",
        "source_submission",
        "physical_damage_score",
        "affected_families_score",
        "social_vulnerability_score",
        "services_interruption_score",
        "livelihood_loss_score",
        "parish_capacity_score",
        "territorial_accessibility_score",
        "allies_availability_score",
        "rapid_impact_score",
        "financial_viability_score",
        "priority_total_original",
        "priority_total_calculated",
        "suggested_semaphore_original",
        "suggested_semaphore_calculated",
        "final_semaphore",
        "final_priority",
        "priority_summary",
        "calculation_warnings",
        "linked_microprojects_snapshot",
        "created_by",
        "created_at",
        "updated_at",
    )
    actions = ()

    def has_add_permission(self, request):
        # PRE: request targets prioritization assessment administration.
        # POST: prevents creation outside the transactional Ficha 11 importer.
        return False

    def has_change_permission(self, request, obj=None):
        # PRE: request targets immutable imported prioritization evidence.
        # POST: permits safe viewing while rejecting every admin mutation request.
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return super().has_change_permission(request, obj)
        return False

    def has_delete_permission(self, request, obj=None):
        # PRE: request targets an imported prioritization assessment.
        # POST: always preserves the assessment and its traceability.
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
