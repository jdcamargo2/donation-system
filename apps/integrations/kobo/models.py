from math import isfinite

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.errors import KoboNormalizationError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import (
    BENEFICIARY_GROUPS,
    COMPONENTS,
    ESTIMATED_COST_RANGES,
    FICHA_10_FORM_ID,
    IMPLEMENTATION_URGENCIES,
    TECHNICAL_VIABILITIES,
)
from apps.integrations.kobo.mappings.ficha_11 import (
    FINAL_PRIORITIES,
    FINAL_SEMAPHORES,
    FICHA_11_FORM_ID,
    SCORE_FIELDS,
    SCORE_MAX,
    SCORE_MIN,
    calculate_ficha_11_suggested_semaphore,
)
from apps.integrations.kobo.territorial import normalize_nucleo_code


SIGNATURE_FIELD_MARKERS = ("signature", "firma")
KOBO_ASSET_FORM_ROLES = (
    "territorial_profile",
    "prioritized_microproject",
    "prioritization_matrix",
)
KOBO_ROUTING_TYPES = ("direct", "field_value")
PASTORAL_ZONE_CODES = tuple(zone.value for zone in PastoralZone)
PASTORAL_ZONE_CHOICES = tuple(
    (zone.value, zone.value.replace("_", " ").title()) for zone in PastoralZone
)
TERRITORIAL_ROUTING_REASON_CODES = tuple(
    reason.value for reason in TerritorialRoutingReasonCode
)
TERRITORIAL_PROFILE_ACCESS_DIFFICULTIES = ("yes", "no", "unknown")
TERRITORIAL_PROFILE_PRIORITY_PERCEPTIONS = (
    "low",
    "medium",
    "high",
    "critical",
    "unknown",
)
TERRITORIAL_PROFILE_ACCESS_DIFFICULTY_CHOICES = tuple(
    (value, value.title()) for value in TERRITORIAL_PROFILE_ACCESS_DIFFICULTIES
)
TERRITORIAL_PROFILE_PRIORITY_CHOICES = tuple(
    (value, value.title()) for value in TERRITORIAL_PROFILE_PRIORITY_PERCEPTIONS
)
TERRITORIAL_PROFILE_LOCATION_KEYS = frozenset(
    {"latitude", "longitude", "altitude", "accuracy"}
)
PRIORITIZED_MICROPROJECT_COMPONENT_CHOICES = tuple(
    (value, value.replace("_", " ").title()) for value in sorted(COMPONENTS)
)
PRIORITIZED_MICROPROJECT_COST_RANGE_CHOICES = tuple(
    (value, value.replace("_", " ").title())
    for value in sorted(ESTIMATED_COST_RANGES)
)
PRIORITIZED_MICROPROJECT_URGENCY_CHOICES = tuple(
    (value, value.replace("_", " ").title())
    for value in sorted(IMPLEMENTATION_URGENCIES)
)
PRIORITIZED_MICROPROJECT_VIABILITY_CHOICES = tuple(
    (value, value.replace("_", " ").title())
    for value in sorted(TECHNICAL_VIABILITIES)
)
PRIORITIZATION_SEMAPHORE_CHOICES = tuple(
    (value, value.title()) for value in sorted(FINAL_SEMAPHORES)
)
PRIORITIZATION_PRIORITY_CHOICES = tuple(
    (value, value.title()) for value in sorted(FINAL_PRIORITIES)
)
PRIORITIZATION_WARNING_MESSAGES = {
    "PRIORITY_TOTAL_MISMATCH": (
        "Kobo priority_total differs from the SIGEDON calculation."
    ),
    "SUGGESTED_SEMAPHORE_MISMATCH": (
        "Kobo suggested_semaphore differs from the SIGEDON calculation."
    ),
}
PRIORITIZATION_WARNING_KEYS = frozenset(
    {"code", "message", "original_value", "calculated_value"}
)
PRIORITIZATION_SCORE_VALIDATORS = (
    MinValueValidator(SCORE_MIN),
    MaxValueValidator(SCORE_MAX),
)


def validate_prioritized_microproject_beneficiary_groups(value):
    """
    PRE: value is the persisted normalized Ficha 10 select-multiple value.
    POST: accepts only a non-empty, ordered, duplicate-free list of canonical codes.
    """
    if not isinstance(value, list) or not value:
        raise ValidationError("Beneficiary groups must be a non-empty list.")
    if any(not isinstance(item, str) or item not in BENEFICIARY_GROUPS for item in value):
        raise ValidationError("Beneficiary groups contain an unsupported value.")
    if len(value) != len(set(value)):
        raise ValidationError("Beneficiary groups cannot contain duplicates.")


def validate_prioritization_calculation_warnings(value):
    """
    PRE: value is the persisted warning snapshot produced by Ficha 11 normalization.
    POST: accepts only the two known calculation discrepancies with safe scalar values.
    """
    if not isinstance(value, list):
        raise ValidationError("Calculation warnings must be a list.")
    seen_codes = set()
    for warning in value:
        if not isinstance(warning, dict) or set(warning) != PRIORITIZATION_WARNING_KEYS:
            raise ValidationError("Calculation warnings use an invalid structure.")
        code = warning.get("code")
        if code not in PRIORITIZATION_WARNING_MESSAGES or code in seen_codes:
            raise ValidationError("Calculation warnings contain an unsupported code.")
        if warning.get("message") != PRIORITIZATION_WARNING_MESSAGES[code]:
            raise ValidationError("Calculation warnings contain an unsafe message.")
        for key in ("original_value", "calculated_value"):
            compared_value = warning.get(key)
            if isinstance(compared_value, bool) or not isinstance(
                compared_value, (int, str)
            ):
                raise ValidationError(
                    "Calculation warning values must be safe scalar codes."
                )
            if isinstance(compared_value, str) and len(compared_value) > 32:
                raise ValidationError("Calculation warning values are too long.")
        original_value = warning["original_value"]
        calculated_value = warning["calculated_value"]
        if code == "PRIORITY_TOTAL_MISMATCH" and (
            not (
                isinstance(original_value, int)
                or (isinstance(original_value, str) and original_value.isdigit())
            )
            or not isinstance(calculated_value, int)
        ):
            raise ValidationError("Priority total warnings require numeric values.")
        if code == "SUGGESTED_SEMAPHORE_MISMATCH" and (
            original_value not in FINAL_SEMAPHORES
            or calculated_value not in FINAL_SEMAPHORES
        ):
            raise ValidationError("Semaphore warnings require canonical codes.")
        seen_codes.add(code)


def validate_territorial_profile_location(value):
    """
    PRE: value is the persisted normalized Ficha 1 location or None.
    POST: accepts only the canonical coordinate object with bounded latitude and
    longitude and numeric optional altitude/accuracy values.
    """
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != TERRITORIAL_PROFILE_LOCATION_KEYS:
        raise ValidationError("Location must use the canonical coordinate structure.")
    for field_name in TERRITORIAL_PROFILE_LOCATION_KEYS:
        component = value[field_name]
        if component is not None and (
            isinstance(component, bool) or not isinstance(component, (int, float))
        ):
            raise ValidationError(f"Location {field_name} must be numeric or null.")
        if component is not None and not isfinite(component):
            raise ValidationError(f"Location {field_name} must be finite.")
    latitude = value["latitude"]
    longitude = value["longitude"]
    if latitude is None or not -90 <= latitude <= 90:
        raise ValidationError("Location latitude must be between -90 and 90.")
    if longitude is None or not -180 <= longitude <= 180:
        raise ValidationError("Location longitude must be between -180 and 180.")
    if value["accuracy"] is not None and value["accuracy"] < 0:
        raise ValidationError("Location accuracy cannot be negative.")


class KoboFormDefinition(models.Model):
    form_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    version = models.CharField(max_length=100)
    schema_snapshot = models.JSONField(default=dict)
    field_mapping = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("form_id", "version"),
                name="kobo_unique_form_version",
            ),
        ]
        ordering = ("form_id", "version")

    def __str__(self):
        return f"{self.title} ({self.form_id}, v{self.version})"


class KoboAsset(models.Model):
    class FormRole(models.TextChoices):
        TERRITORIAL_PROFILE = "territorial_profile", "Territorial profile"
        PRIORITIZED_MICROPROJECT = (
            "prioritized_microproject",
            "Prioritized microproject",
        )
        PRIORITIZATION_MATRIX = "prioritization_matrix", "Prioritization matrix"

    asset_uid = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    form_definition = models.ForeignKey(
        KoboFormDefinition,
        on_delete=models.PROTECT,
        related_name="assets",
    )
    form_role = models.CharField(max_length=32, choices=FormRole.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_successful_sync_cursor = models.DateTimeField(null=True, blank=True)
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    last_remote_watermark = models.DateTimeField(null=True, blank=True)
    sync_lease_started_at = models.DateTimeField(null=True, blank=True)
    sync_lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(form_role__in=KOBO_ASSET_FORM_ROLES),
                name="kobo_asset_valid_form_role",
            ),
        ]
        ordering = ("name", "asset_uid")

    def __str__(self):
        return f"{self.name} ({self.asset_uid})"


class KoboSyncRun(models.Model):
    """Safe operational record for one synchronous remote Kobo operation."""

    class Kind(models.TextChoices):
        DISCOVERY = "discovery", "Discovery"
        SUBMISSIONS = "submissions", "Submissions"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        ABANDONED = "abandoned", "Abandoned"

    class Mode(models.TextChoices):
        FULL = "full", "Full"
        INCREMENTAL = "incremental", "Incremental"

    asset = models.ForeignKey(KoboAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="sync_runs")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.FULL)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="kobo_sync_runs")
    pages_fetched = models.PositiveIntegerField(default=0)
    items_seen = models.PositiveIntegerField(default=0)
    items_created = models.PositiveIntegerField(default=0)
    items_updated = models.PositiveIntegerField(default=0)
    items_unchanged = models.PositiveIntegerField(default=0)
    remote_updates_detected = models.PositiveIntegerField(default=0)
    items_failed = models.PositiveIntegerField(default=0)
    partial = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, blank=True)
    safe_error_message = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict)
    cursor_before = models.DateTimeField(null=True, blank=True)
    cursor_after = models.DateTimeField(null=True, blank=True)
    watermark_before = models.DateTimeField(null=True, blank=True)
    watermark_after = models.DateTimeField(null=True, blank=True)
    lease_recovered = models.BooleanField(default=False)

    class Meta:
        ordering = ("-started_at",)


class KoboDiscoveredAsset(models.Model):
    asset_uid = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    asset_type = models.CharField(max_length=100, blank=True)
    deployment_status = models.CharField(max_length=100, blank=True)
    owner_username = models.CharField(max_length=255, blank=True)
    remote_created_at = models.DateTimeField(null=True, blank=True)
    remote_modified_at = models.DateTimeField(null=True, blank=True)
    metadata_snapshot = models.JSONField(default=dict)
    discovered_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()
    is_available = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "asset_uid")

    def __str__(self):
        return f"{self.name} ({self.asset_uid})"


class KoboProjectBinding(models.Model):
    """Historical asset-to-project configuration retained pending data audit.

    Supported Kobo submissions never read or write this model at runtime.
    """
    class RoutingType(models.TextChoices):
        DIRECT = "direct", "Direct"
        FIELD_VALUE = "field_value", "Field value"

    asset = models.ForeignKey(
        KoboAsset,
        on_delete=models.CASCADE,
        related_name="project_bindings",
    )
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.CASCADE,
        related_name="kobo_bindings",
    )
    routing_type = models.CharField(max_length=16, choices=RoutingType.choices)
    source_field = models.CharField(max_length=255, blank=True)
    source_value = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("asset",),
                condition=models.Q(routing_type="direct"),
                name="kobo_unique_direct_per_asset",
            ),
            models.UniqueConstraint(
                fields=("asset", "source_field", "source_value"),
                condition=models.Q(routing_type="field_value"),
                name="kobo_unique_field_route",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        routing_type="direct",
                        source_field="",
                        source_value="",
                    )
                    | (
                        models.Q(routing_type="field_value")
                        & ~models.Q(source_field="")
                        & ~models.Q(source_value="")
                    )
                ),
                name="kobo_binding_valid_route_fields",
            ),
            models.CheckConstraint(
                condition=models.Q(routing_type__in=KOBO_ROUTING_TYPES),
                name="kobo_binding_valid_routing_type",
            ),
        ]
        ordering = ("asset", "routing_type", "source_field", "source_value")

    def clean(self):
        # PRE: routing fields represent the proposed binding configuration.
        # POST: accepts only coherent direct or field-value routing definitions.
        super().clean()
        if self.routing_type == self.RoutingType.DIRECT:
            if self.source_field or self.source_value:
                raise ValidationError(
                    "Direct routing cannot define source_field or source_value."
                )
        elif self.routing_type == self.RoutingType.FIELD_VALUE:
            if not self.source_field.strip() or not self.source_value.strip():
                raise ValidationError(
                    "Field-value routing requires source_field and source_value."
                )

    def validate_for_import(self) -> None:
        """
        PRE: binding and its asset exist.
        POST: returns None only for an active binding on an active asset;
        otherwise raises ValidationError and performs no state change.
        """
        if not self.is_active:
            raise ValidationError("Inactive Kobo bindings cannot be used for import.")
        if not self.asset.is_active:
            raise ValidationError("Bindings for inactive Kobo assets cannot be imported.")

    def __str__(self):
        route = self.routing_type
        if self.routing_type == self.RoutingType.FIELD_VALUE:
            route = f"{self.source_field}={self.source_value}"
        return f"{self.asset.asset_uid} → {self.project} ({route})"


class KoboSubmission(models.Model):
    class RoutingStatus(models.TextChoices):
        UNRESOLVED = "unresolved", "Unresolved"
        PENDING_IDENTITY = "pending_identity", "Pending identity"
        RESOLVED = "resolved", "Resolved"
        CONFLICT = "conflict", "Conflict"
        ERROR = "error", "Error"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        NORMALIZED = "normalized", "Normalized"
        VALIDATION_FAILED = "validation_failed", "Validation failed"
        READY_FOR_REVIEW = "ready_for_review", "Ready for review"
        APPROVED_FOR_IMPORT = "approved_for_import", "Approved for import"
        IMPORTED = "imported", "Imported"
        PARTIALLY_IMPORTED = "partially_imported", "Partially imported"
        REJECTED = "rejected", "Rejected"
        DUPLICATE = "duplicate", "Duplicate"
        PROCESSING_FAILED = "processing_failed", "Processing failed"

    form_definition = models.ForeignKey(
        KoboFormDefinition,
        on_delete=models.PROTECT,
        related_name="submissions",
    )
    asset = models.ForeignKey(
        KoboAsset,
        on_delete=models.PROTECT,
        related_name="submissions",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_submissions",
        null=True,
        blank=True,
    )
    external_id = models.CharField(max_length=255)
    raw_payload = models.JSONField()
    remote_created_at = models.DateTimeField(null=True, blank=True)
    remote_updated_at = models.DateTimeField(null=True, blank=True)
    remote_version = models.CharField(max_length=255, blank=True)
    last_remote_payload_hash = models.CharField(max_length=64, blank=True)
    remote_update_pending = models.BooleanField(default=False)
    normalized_payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    pastoral_zone = models.CharField(max_length=255, blank=True)
    parish = models.CharField(max_length=255, blank=True)
    primary_community = models.CharField(max_length=255, blank=True)
    assessment_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    normalized_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    routing_status = models.CharField(
        max_length=32,
        choices=RoutingStatus.choices,
        default=RoutingStatus.UNRESOLVED,
    )
    routing_reason_code = models.CharField(
        max_length=100,
        choices=[(code, code) for code in TERRITORIAL_ROUTING_REASON_CODES],
        blank=True,
    )
    routing_resolved_at = models.DateTimeField(null=True, blank=True)
    nucleo_code_original = models.CharField(max_length=255, blank=True)
    nucleo_code_normalized = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("form_definition", "external_id"),
                name="kobo_unique_external_submission_per_form",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(routing_status="resolved")
                    | models.Q(project__isnull=False)
                ),
                name="kobo_resolved_routing_requires_project",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(routing_status="pending_identity")
                    | models.Q(project__isnull=True)
                ),
                name="kobo_pending_identity_has_no_project",
            ),
        ]
        ordering = ("-received_at",)

    def clean(self):
        # PRE: status and processed_at represent the intended persisted state.
        # POST: imported submissions are rejected unless processing is timestamped.
        super().clean()
        if self.status == self.Status.IMPORTED and self.processed_at is None:
            raise ValidationError(
                {"processed_at": "Imported submissions require processed_at."}
            )

    def __str__(self):
        return f"{self.form_definition.form_id}: {self.external_id} [{self.status}]"


class KoboSubmissionRemoteRevision(models.Model):
    """Immutable, private remote snapshot retained when Kobo changes a submission."""
    submission = models.ForeignKey(KoboSubmission, on_delete=models.PROTECT, related_name="remote_revisions")
    remote_version = models.CharField(max_length=255, blank=True)
    remote_updated_at = models.DateTimeField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    applied = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("submission", "payload_hash"), name="kobo_unique_remote_revision_hash")]
        ordering = ("-received_at",)


class KoboImportRecord(models.Model):
    submission = models.OneToOneField(
        KoboSubmission,
        on_delete=models.PROTECT,
        related_name="import_record",
    )
    handler_type = models.CharField(max_length=32)
    target_app_label = models.CharField(max_length=100)
    target_model = models.CharField(max_length=100)
    target_object_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="kobo_import_records",
    )
    result_metadata = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_object_id__gt=0),
                name="kobo_import_record_positive_target_id",
            ),
        ]
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return (
            f"{self.handler_type}: "
            f"{self.target_app_label}.{self.target_model}#{self.target_object_id}"
        )


class KoboPastoralZoneProjectMapping(models.Model):
    pastoral_zone = models.CharField(max_length=32, choices=PASTORAL_ZONE_CHOICES)
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_pastoral_zone_mappings",
    )
    is_active = models.BooleanField(default=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deactivated_kobo_pastoral_zone_mappings",
        null=True,
        blank=True,
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("manage_pastoral_zone_mappings", "Can manage pastoral-zone project mappings"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("pastoral_zone",),
                condition=models.Q(is_active=True),
                name="kobo_unique_active_zone_project_mapping",
            ),
            models.CheckConstraint(
                condition=models.Q(pastoral_zone__in=PASTORAL_ZONE_CODES),
                name="kobo_mapping_valid_pastoral_zone",
            ),
        ]
        ordering = ("pastoral_zone", "-is_active")

    def __str__(self):
        return f"{self.pastoral_zone} → {self.project}"


class KoboTerritorialIdentity(models.Model):
    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending review"
        ACTIVE = "active", "Active"
        OBSERVED = "observed", "Observed"
        INACTIVE = "inactive", "Inactive"

    nucleo_code_original = models.CharField(max_length=255)
    nucleo_code_normalized = models.CharField(max_length=255, unique=True)
    pastoral_zone = models.CharField(max_length=32, choices=PASTORAL_ZONE_CHOICES)
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_territorial_identities",
    )
    source_submission = models.ForeignKey(
        KoboSubmission,
        on_delete=models.PROTECT,
        related_name="confirmed_territorial_identities",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("view_territorial_administration", "Can view territorial administration"),
            ("change_territorial_identity_status", "Can change territorial identity status"),
            ("run_territorial_reconciliation", "Can run territorial reconciliation"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(nucleo_code_original=""),
                name="kobo_identity_original_code_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(nucleo_code_normalized=""),
                name="kobo_identity_normalized_code_not_empty",
            ),
            models.CheckConstraint(
                condition=models.Q(pastoral_zone__in=PASTORAL_ZONE_CODES),
                name="kobo_identity_valid_pastoral_zone",
            ),
        ]
        indexes = [
            models.Index(
                fields=("pastoral_zone", "status"),
                name="kobo_identity_zone_status_idx",
            ),
        ]
        ordering = ("nucleo_code_normalized",)

    def clean(self):
        """
        PRE: the source submission is a persisted Kobo Ficha 1 submission.
        POST: accepts only an exact canonical code derived by the shared pure
        normalizer and a source submission from the supported Ficha 1 form.
        """
        super().clean()
        errors = {}
        try:
            expected_normalized = normalize_nucleo_code(self.nucleo_code_original)
        except KoboNormalizationError as exc:
            errors["nucleo_code_original"] = str(exc)
        else:
            if self.nucleo_code_normalized != expected_normalized:
                errors["nucleo_code_normalized"] = (
                    "Normalized code must use the shared territorial contract."
                )
        if (
            self.source_submission_id
            and self.source_submission.form_definition.form_id != FICHA_01_FORM_ID
        ):
            errors["source_submission"] = "Identity sources must be Ficha 1 submissions."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.nucleo_code_normalized

    def latest_profile(self):
        """
        PRE: this identity is persisted.
        POST: returns its newest immutable profile by creation order or None.
        """
        if self.pk is None:
            return None
        return self.territorial_profiles.order_by("-created_at", "-pk").first()

    def latest_prioritization_assessment(self):
        """
        PRE: this identity is persisted.
        POST: returns its newest immutable prioritization assessment or None.
        """
        if self.pk is None:
            return None
        return self.prioritization_assessments.order_by(
            "-created_at", "-pk"
        ).first()


class KoboTerritorialProfile(models.Model):
    territorial_identity = models.ForeignKey(
        KoboTerritorialIdentity,
        on_delete=models.PROTECT,
        related_name="territorial_profiles",
    )
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_territorial_profiles",
    )
    source_submission = models.OneToOneField(
        KoboSubmission,
        on_delete=models.PROTECT,
        related_name="territorial_profile",
    )
    parish = models.CharField(max_length=255)
    community_sector = models.CharField(max_length=255)
    location = models.JSONField(
        null=True,
        blank=True,
        validators=(validate_territorial_profile_location,),
    )
    parish_delegate = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=100, blank=True)
    main_informant_role = models.CharField(max_length=255, blank=True)
    communities_covered = models.TextField(blank=True)
    estimated_households = models.PositiveIntegerField(null=True, blank=True)
    access_difficulties = models.CharField(
        max_length=16,
        choices=TERRITORIAL_PROFILE_ACCESS_DIFFICULTY_CHOICES,
    )
    access_difficulties_notes = models.TextField(blank=True)
    initial_priority_perception = models.CharField(
        max_length=16,
        choices=TERRITORIAL_PROFILE_PRIORITY_CHOICES,
    )
    general_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_kobo_territorial_profiles",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(estimated_households__isnull=True)
                    | models.Q(estimated_households__gte=0)
                ),
                name="kobo_profile_households_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    access_difficulties__in=TERRITORIAL_PROFILE_ACCESS_DIFFICULTIES
                ),
                name="kobo_profile_valid_access_difficulty",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    initial_priority_perception__in=(
                        TERRITORIAL_PROFILE_PRIORITY_PERCEPTIONS
                    )
                ),
                name="kobo_profile_valid_priority",
            ),
        ]
        indexes = [
            models.Index(
                fields=("territorial_identity", "-created_at"),
                name="kobo_prof_identity_date_idx",
            ),
            models.Index(
                fields=("project", "-created_at"),
                name="kobo_prof_project_date_idx",
            ),
        ]
        ordering = ("-created_at", "-pk")

    def clean(self):
        """
        PRE: profile relations identify an existing Ficha 1 submission and identity.
        POST: accepts only canonical, non-empty reviewed fields whose project,
        source form, nucleus code, and pastoral zone agree exactly.
        """
        super().clean()
        errors = {}
        if not isinstance(self.parish, str) or not self.parish.strip():
            errors["parish"] = "Parish is required."
        if (
            not isinstance(self.community_sector, str)
            or not self.community_sector.strip()
        ):
            errors["community_sector"] = "Community sector is required."
        if self.source_submission_id and self.territorial_identity_id:
            submission = self.source_submission
            identity = self.territorial_identity
            if submission.form_definition.form_id != FICHA_01_FORM_ID:
                errors["source_submission"] = (
                    "Territorial profiles require a Ficha 1 source."
                )
            if (
                submission.project_id != self.project_id
                or identity.project_id != self.project_id
            ):
                errors["project"] = (
                    "Submission, identity, and profile must share one project."
                )
            if submission.nucleo_code_normalized != identity.nucleo_code_normalized:
                errors["territorial_identity"] = (
                    "Submission and identity nucleus codes must match."
                )
            if submission.pastoral_zone != identity.pastoral_zone:
                errors["territorial_identity"] = (
                    "Submission and identity pastoral zones must match."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """
        PRE: new profiles already passed the materialization service invariants.
        POST: inserts a profile once and rejects later mutation through model save.
        """
        if self.pk is not None:
            raise ValidationError("Imported territorial profiles are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.territorial_identity.nucleo_code_normalized} - {self.parish}"


class KoboPrioritizedMicroproject(models.Model):
    territorial_identity = models.ForeignKey(
        KoboTerritorialIdentity,
        on_delete=models.PROTECT,
        related_name="prioritized_microprojects",
    )
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_prioritized_microprojects",
    )
    source_submission = models.OneToOneField(
        KoboSubmission,
        on_delete=models.PROTECT,
        related_name="prioritized_microproject",
    )
    name = models.CharField(max_length=255)
    component = models.CharField(
        max_length=32,
        choices=PRIORITIZED_MICROPROJECT_COMPONENT_CHOICES,
    )
    problem_summary = models.TextField()
    specific_objective = models.TextField()
    beneficiary_group = models.JSONField(
        validators=(validate_prioritized_microproject_beneficiary_groups,),
    )
    main_activities = models.TextField()
    estimated_cost_range = models.CharField(
        max_length=32,
        choices=PRIORITIZED_MICROPROJECT_COST_RANGE_CHOICES,
    )
    implementation_urgency = models.CharField(
        max_length=32,
        choices=PRIORITIZED_MICROPROJECT_URGENCY_CHOICES,
    )
    technical_viability = models.CharField(
        max_length=32,
        choices=PRIORITIZED_MICROPROJECT_VIABILITY_CHOICES,
    )
    expected_result = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_kobo_prioritized_microprojects",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="kobo_microproject_name_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(problem_summary=""),
                name="kobo_microproject_problem_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(specific_objective=""),
                name="kobo_microproject_objective_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(main_activities=""),
                name="kobo_microproject_activities_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(expected_result=""),
                name="kobo_microproject_result_not_empty",
            ),
            models.CheckConstraint(
                condition=models.Q(component__in=tuple(sorted(COMPONENTS))),
                name="kobo_microproject_valid_component",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    estimated_cost_range__in=tuple(sorted(ESTIMATED_COST_RANGES))
                ),
                name="kobo_microproject_valid_cost_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    implementation_urgency__in=tuple(
                        sorted(IMPLEMENTATION_URGENCIES)
                    )
                ),
                name="kobo_microproject_valid_urgency",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    technical_viability__in=tuple(sorted(TECHNICAL_VIABILITIES))
                ),
                name="kobo_microproject_valid_viability",
            ),
        ]
        indexes = [
            models.Index(
                fields=("territorial_identity", "-created_at"),
                name="kobo_micro_identity_date_idx",
            ),
            models.Index(
                fields=("project", "-created_at"),
                name="kobo_micro_project_date_idx",
            ),
        ]

    def clean(self):
        """
        PRE: relations and imported fields represent one reviewed Ficha 10 proposal.
        POST: accepts only required canonical data coherent with one identity and project.
        """
        super().clean()
        errors = {}
        required_text_fields = (
            "name",
            "problem_summary",
            "specific_objective",
            "main_activities",
            "expected_result",
        )
        for field_name in required_text_fields:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                errors[field_name] = "This imported field is required."
        if self.source_submission_id and self.territorial_identity_id:
            submission = self.source_submission
            identity = self.territorial_identity
            if submission.form_definition.form_id != FICHA_10_FORM_ID:
                errors["source_submission"] = (
                    "Prioritized microprojects require a Ficha 10 source."
                )
            if (
                submission.project_id != self.project_id
                or identity.project_id != self.project_id
            ):
                errors["project"] = (
                    "Submission, identity, and microproject must share one project."
                )
            if submission.nucleo_code_normalized != identity.nucleo_code_normalized:
                errors["territorial_identity"] = (
                    "Submission and identity nucleus codes must match."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """
        PRE: a new row already passed the Ficha 10 materialization invariants.
        POST: inserts it once and rejects every later mutation through model save.
        """
        if self.pk is not None:
            raise ValidationError("Imported prioritized microprojects are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.territorial_identity.nucleo_code_normalized} - {self.name}"


class KoboPrioritizationAssessment(models.Model):
    territorial_identity = models.ForeignKey(
        KoboTerritorialIdentity,
        on_delete=models.PROTECT,
        related_name="prioritization_assessments",
    )
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_prioritization_assessments",
    )
    source_submission = models.OneToOneField(
        KoboSubmission,
        on_delete=models.PROTECT,
        related_name="prioritization_assessment",
    )
    physical_damage_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    affected_families_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    social_vulnerability_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    services_interruption_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    livelihood_loss_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    parish_capacity_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    territorial_accessibility_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    allies_availability_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    rapid_impact_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    financial_viability_score = models.PositiveSmallIntegerField(
        validators=PRIORITIZATION_SCORE_VALIDATORS
    )
    priority_total_original = models.PositiveSmallIntegerField(null=True, blank=True)
    priority_total_calculated = models.PositiveSmallIntegerField()
    suggested_semaphore_original = models.CharField(
        max_length=16,
        choices=PRIORITIZATION_SEMAPHORE_CHOICES,
        blank=True,
    )
    suggested_semaphore_calculated = models.CharField(
        max_length=16,
        choices=PRIORITIZATION_SEMAPHORE_CHOICES,
    )
    final_semaphore = models.CharField(
        max_length=16,
        choices=PRIORITIZATION_SEMAPHORE_CHOICES,
    )
    final_priority = models.CharField(
        max_length=16,
        choices=PRIORITIZATION_PRIORITY_CHOICES,
    )
    priority_summary = models.TextField()
    calculation_warnings = models.JSONField(
        blank=True,
        default=list,
        validators=(validate_prioritization_calculation_warnings,),
    )
    linked_microprojects_snapshot = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_kobo_prioritization_assessments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    physical_damage_score__range=(SCORE_MIN, SCORE_MAX),
                    affected_families_score__range=(SCORE_MIN, SCORE_MAX),
                    social_vulnerability_score__range=(SCORE_MIN, SCORE_MAX),
                    services_interruption_score__range=(SCORE_MIN, SCORE_MAX),
                    livelihood_loss_score__range=(SCORE_MIN, SCORE_MAX),
                    parish_capacity_score__range=(SCORE_MIN, SCORE_MAX),
                    territorial_accessibility_score__range=(SCORE_MIN, SCORE_MAX),
                    allies_availability_score__range=(SCORE_MIN, SCORE_MAX),
                    rapid_impact_score__range=(SCORE_MIN, SCORE_MAX),
                    financial_viability_score__range=(SCORE_MIN, SCORE_MAX),
                ),
                name="kobo_assessment_scores_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    priority_total_calculated=(
                        models.F("physical_damage_score")
                        + models.F("affected_families_score")
                        + models.F("social_vulnerability_score")
                        + models.F("services_interruption_score")
                        + models.F("livelihood_loss_score")
                        + models.F("parish_capacity_score")
                        + models.F("territorial_accessibility_score")
                        + models.F("allies_availability_score")
                        + models.F("rapid_impact_score")
                        + models.F("financial_viability_score")
                    )
                ),
                name="kobo_assessment_total_matches_scores",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(suggested_semaphore_original="")
                    | models.Q(
                        suggested_semaphore_original__in=tuple(
                            sorted(FINAL_SEMAPHORES)
                        )
                    )
                ),
                name="kobo_assessment_valid_original_semaphore",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    suggested_semaphore_calculated__in=tuple(
                        sorted(FINAL_SEMAPHORES)
                    )
                ),
                name="kobo_assessment_valid_calculated_semaphore",
            ),
            models.CheckConstraint(
                condition=models.Q(final_semaphore__in=tuple(sorted(FINAL_SEMAPHORES))),
                name="kobo_assessment_valid_final_semaphore",
            ),
            models.CheckConstraint(
                condition=models.Q(final_priority__in=tuple(sorted(FINAL_PRIORITIES))),
                name="kobo_assessment_valid_final_priority",
            ),
            models.CheckConstraint(
                condition=~models.Q(priority_summary=""),
                name="kobo_assessment_summary_not_empty",
            ),
        ]
        indexes = [
            models.Index(
                fields=("territorial_identity", "-created_at"),
                name="kobo_assess_identity_date_idx",
            ),
            models.Index(
                fields=("project", "-created_at"),
                name="kobo_assess_project_date_idx",
            ),
        ]

    def clean(self):
        """
        PRE: fields represent one reviewed Ficha 11 submission and canonical identity.
        POST: accepts only coherent relations, scores, calculations, and human decisions.
        """
        super().clean()
        errors = {}
        scores = [getattr(self, field_name) for field_name in SCORE_FIELDS]
        if all(
            isinstance(score, int)
            and not isinstance(score, bool)
            and SCORE_MIN <= score <= SCORE_MAX
            for score in scores
        ):
            expected_total = sum(scores)
            if self.priority_total_calculated != expected_total:
                errors["priority_total_calculated"] = (
                    "Calculated total must equal the ten persisted scores."
                )
            expected_semaphore = calculate_ficha_11_suggested_semaphore(
                expected_total
            )
            if self.suggested_semaphore_calculated != expected_semaphore:
                errors["suggested_semaphore_calculated"] = (
                    "Calculated semaphore must use the SIGEDON score thresholds."
                )
        if not isinstance(self.priority_summary, str) or not self.priority_summary.strip():
            errors["priority_summary"] = "Priority summary is required."
        if not isinstance(self.linked_microprojects_snapshot, str):
            errors["linked_microprojects_snapshot"] = (
                "Linked microprojects snapshot must remain text."
            )
        if self.source_submission_id and self.territorial_identity_id:
            submission = self.source_submission
            identity = self.territorial_identity
            if submission.form_definition.form_id != FICHA_11_FORM_ID:
                errors["source_submission"] = (
                    "Prioritization assessments require a Ficha 11 source."
                )
            if (
                submission.project_id != self.project_id
                or identity.project_id != self.project_id
            ):
                errors["project"] = (
                    "Submission, identity, and assessment must share one project."
                )
            if submission.nucleo_code_normalized != identity.nucleo_code_normalized:
                errors["territorial_identity"] = (
                    "Submission and identity nucleus codes must match."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """
        PRE: a new row passed the Ficha 11 materialization invariants.
        POST: inserts it once and rejects every later mutation through model save.
        """
        if self.pk is not None:
            raise ValidationError("Imported prioritization assessments are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.territorial_identity.nucleo_code_normalized} - "
            f"{self.priority_total_calculated} ({self.final_semaphore})"
        )


class KoboTerritorialIdentityConflict(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED_KEEP_EXISTING = "resolved_keep_existing", "Resolved: keep existing"
        RESOLVED_ACCEPT_PROPOSED = "resolved_accept_proposed", "Resolved: accept proposed"
        DISMISSED = "dismissed", "Dismissed"

    class Resolution(models.TextChoices):
        KEEP_EXISTING = "keep_existing", "Keep existing"
        ACCEPT_PROPOSED = "accept_proposed", "Accept proposed"
        DISMISSED = "dismissed", "Dismissed"

    identity = models.ForeignKey(
        KoboTerritorialIdentity,
        on_delete=models.PROTECT,
        related_name="conflicts",
    )
    incoming_submission = models.ForeignKey(
        KoboSubmission,
        on_delete=models.PROTECT,
        related_name="territorial_identity_conflicts",
    )
    existing_pastoral_zone = models.CharField(
        max_length=32,
        choices=PASTORAL_ZONE_CHOICES,
    )
    proposed_pastoral_zone = models.CharField(
        max_length=32,
        choices=PASTORAL_ZONE_CHOICES,
    )
    existing_project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="existing_kobo_territorial_identity_conflicts",
        null=True,
        blank=True,
    )
    proposed_project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="proposed_kobo_territorial_identity_conflicts",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)
    resolution = models.CharField(
        max_length=32,
        choices=Resolution.choices,
        blank=True,
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_kobo_territorial_conflicts",
        null=True,
        blank=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("resolve_territorial_conflicts", "Can resolve territorial conflicts"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("identity", "incoming_submission", "proposed_pastoral_zone"),
                condition=models.Q(status="open"),
                name="kobo_unique_open_territorial_conflict",
            ),
            models.CheckConstraint(
                condition=models.Q(existing_pastoral_zone__in=PASTORAL_ZONE_CODES),
                name="kobo_conflict_valid_existing_zone",
            ),
            models.CheckConstraint(
                condition=models.Q(proposed_pastoral_zone__in=PASTORAL_ZONE_CODES),
                name="kobo_conflict_valid_proposed_zone",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "proposed_pastoral_zone"),
                name="kobo_conflict_status_zone_idx",
            ),
        ]
        ordering = ("-created_at",)

    def clean(self):
        # PRE: conflict captures an incoming territorial proposal for an identity.
        # POST: rejects only an identical zone/project proposal without modifying identity.
        super().clean()
        if self.identity_id and self.existing_pastoral_zone != self.identity.pastoral_zone:
            raise ValidationError(
                {"existing_pastoral_zone": "Must preserve the identity's current zone."}
            )
        same_zone = self.existing_pastoral_zone == self.proposed_pastoral_zone
        same_project = (
            self.existing_project_id is not None
            and self.existing_project_id == self.proposed_project_id
        )
        if same_zone and (self.proposed_project_id is None or same_project):
            raise ValidationError(
                {"proposed_pastoral_zone": "Conflict requires a different zone or project."}
            )

    def __str__(self):
        return f"{self.identity}: {self.existing_pastoral_zone} → {self.proposed_pastoral_zone}"


class KoboTerritorialAdministrationEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="kobo_territorial_administration_events",
    )
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.PositiveBigIntegerField()
    previous_state = models.JSONField(default=dict, blank=True)
    new_state = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return f"{self.action}: {self.entity_type}#{self.entity_id}"


class KoboAttachment(models.Model):
    class PrivacyLevel(models.TextChoices):
        PRIVATE = "private", "Private"
        INTERNAL_REVIEW = "internal_review", "Internal review"
        PUBLIC_CANDIDATE = "public_candidate", "Public candidate"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DOWNLOADED = "downloaded", "Downloaded"
        INVALID = "invalid", "Invalid"
        FAILED = "failed", "Failed"

    submission = models.ForeignKey(
        KoboSubmission,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    field_name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    file = models.FileField(
        upload_to="kobo/attachments/",
        null=True,
        blank=True,
    )
    privacy_level = models.CharField(
        max_length=32,
        choices=PrivacyLevel.choices,
        default=PrivacyLevel.INTERNAL_REVIEW,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    processing_token = models.UUIDField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        # PRE: field_name identifies the Kobo field and privacy_level is proposed.
        # POST: signature fields cannot pass validation as public candidates.
        super().clean()
        normalized_field_name = self.field_name.casefold()
        is_signature = any(
            marker in normalized_field_name for marker in SIGNATURE_FIELD_MARKERS
        )
        if is_signature and self.privacy_level == self.PrivacyLevel.PUBLIC_CANDIDATE:
            raise ValidationError(
                {"privacy_level": "Signature attachments cannot be public candidates."}
            )

    def __str__(self):
        filename = self.original_filename or self.external_id or self.field_name
        return f"{filename} ({self.submission.external_id})"


class KoboProcessingEvent(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    submission = models.ForeignKey(
        KoboSubmission,
        on_delete=models.CASCADE,
        related_name="processing_events",
    )
    stage = models.CharField(max_length=100)
    level = models.CharField(max_length=16, choices=Level.choices)
    code = models.CharField(max_length=100, blank=True)
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.submission.external_id}: {self.stage} [{self.level}]"

class Ficha01Territorio(models.Model):
    # Metadata block (Common across all 10 forms)
    pastoral_zone = models.CharField(max_length=100)
    parish_sector = models.CharField(max_length=150)
    survey_date = models.DateField()
    survey_responsible = models.CharField(max_length=150)
    parish_priest = models.CharField(max_length=150)
    contact_phone = models.CharField(max_length=50)

    # Core identification data
    official_parish_name = models.CharField(max_length=200)
    church_advocation = models.CharField(max_length=200, blank=True, null=True)
    civil_municipality = models.CharField(max_length=200)
    influence_radius = models.CharField(max_length=100)
    estimated_population = models.IntegerField()
    estimated_households = models.IntegerField()
    gps_coordinates = models.CharField(max_length=100)
    main_accessibility = models.TextField()

    # Stored as a JSON array in SQLite (e.g., ["dense_urban", "linear_coastal"])
    territory_type = models.JSONField(default=list)

    # Webhook synchronization control
    kobo_uuid = models.UUIDField(unique=True, editable=False)

    class Meta:
        verbose_name = "Ficha 1 - Identificación Territorial"
        verbose_name_plural = "Ficha 1 - Identificaciones Territoriales"

    def __str__(self):
        return f"{self.official_parish_name} - {self.survey_date}"


class Ficha01CoveredCommunity(models.Model):
    """
    Represents rows from the 'covered_communities_repeat' block in Kobo.
    """
    territory_form = models.ForeignKey(
        Ficha01Territorio,
        on_delete=models.CASCADE,
        related_name='covered_communities'
    )
    community_sector = models.CharField(max_length=200)
    estimated_community_population = models.IntegerField(blank=True, null=True)
    distance_time_to_church = models.CharField(max_length=150)
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Comunidad Cubierta"
        verbose_name_plural = "Comunidades Cubiertas"

    def __str__(self):
        return f"{self.community_sector} -> {self.territory_form.official_parish_name}"
