from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.errors import KoboNormalizationError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
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


class KoboPastoralZoneProjectMapping(models.Model):
    pastoral_zone = models.CharField(max_length=32, choices=PASTORAL_ZONE_CHOICES)
    project = models.ForeignKey(
        "operations.Project",
        on_delete=models.PROTECT,
        related_name="kobo_pastoral_zone_mappings",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
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
        # PRE: conflict captures an incoming zone that differs from identity's zone.
        # POST: rejects same-zone records without modifying identity or project.
        super().clean()
        if self.identity_id and self.existing_pastoral_zone != self.identity.pastoral_zone:
            raise ValidationError(
                {"existing_pastoral_zone": "Must preserve the identity's current zone."}
            )
        if self.existing_pastoral_zone == self.proposed_pastoral_zone:
            raise ValidationError(
                {"proposed_pastoral_zone": "Conflict requires a different zone."}
            )

    def __str__(self):
        return f"{self.identity}: {self.existing_pastoral_zone} → {self.proposed_pastoral_zone}"


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
