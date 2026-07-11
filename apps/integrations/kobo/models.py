from django.core.exceptions import ValidationError
from django.db import models


SIGNATURE_FIELD_MARKERS = ("signature", "firma")
KOBO_ASSET_FORM_ROLES = (
    "territorial_profile",
    "prioritized_microproject",
    "prioritization_matrix",
)
KOBO_ROUTING_TYPES = ("direct", "field_value")


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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("form_definition", "external_id"),
                name="kobo_unique_external_submission_per_form",
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


class KoboAttachment(models.Model):
    class PrivacyLevel(models.TextChoices):
        PRIVATE = "private", "Private"
        INTERNAL_REVIEW = "internal_review", "Internal review"
        PUBLIC_CANDIDATE = "public_candidate", "Public candidate"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
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
