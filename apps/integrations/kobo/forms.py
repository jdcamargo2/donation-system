from django import forms
from django.core.exceptions import ValidationError

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboSubmission,
    KoboTerritorialIdentityConflict,
)
from apps.integrations.kobo.form_registry import list_registered_forms
from apps.integrations.kobo.services import REJECTION_REASON_LABELS


SUPPORTED_FORM_ROLES = {
    (FICHA_01_FORM_ID, FICHA_01_VERSION): KoboAsset.FormRole.TERRITORIAL_PROFILE,
    (FICHA_10_FORM_ID, FICHA_10_VERSION): KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
    (FICHA_11_FORM_ID, FICHA_11_VERSION): KoboAsset.FormRole.PRIORITIZATION_MATRIX,
}


def get_compatible_asset_configuration(
    discovered_asset: KoboDiscoveredAsset,
) -> tuple[KoboFormDefinition, str] | None:
    """
    PRE: discovered_asset is a persisted remote discovery projection.
    POST: returns one exact active registered definition and its fixed supported
    role, or None; never guesses from arbitrary UIDs, definitions, or roles.
    """
    registered_versions = {
        (registered.form_id, registered.version)
        for registered in list_registered_forms()
        if (registered.form_id, registered.version) in SUPPORTED_FORM_ROLES
    }
    metadata = discovered_asset.metadata_snapshot or {}
    remote_form_id = metadata.get("id_string")
    if not isinstance(remote_form_id, str) or not remote_form_id.strip():
        return None
    remote_version = metadata.get("version")
    if remote_version is not None and not isinstance(remote_version, str):
        return None
    candidates = list(
        KoboFormDefinition.objects.filter(is_active=True).order_by("pk")
    )
    matches = [
        definition
        for definition in candidates
        if (definition.form_id, definition.version) in registered_versions
        and definition.form_id == remote_form_id.strip()
        and (remote_version is None or definition.version == remote_version.strip())
    ]
    if len(matches) != 1:
        return None
    definition = matches[0]
    return definition, SUPPORTED_FORM_ROLES[(definition.form_id, definition.version)]


class KoboAssetConfigurationForm(forms.Form):
    name = forms.CharField(max_length=255)
    form_definition = forms.ModelChoiceField(
        queryset=KoboFormDefinition.objects.none()
    )
    form_role = forms.ChoiceField(choices=KoboAsset.FormRole.choices)

    def __init__(self, *args, discovered_asset: KoboDiscoveredAsset, **kwargs):
        # PRE: discovered_asset identifies the exact candidate being configured.
        # POST: exposes at most one compatible definition and its fixed local role.
        super().__init__(*args, **kwargs)
        compatible = get_compatible_asset_configuration(discovered_asset)
        if compatible is None:
            self.fields["form_definition"].queryset = KoboFormDefinition.objects.none()
            self.fields["form_role"].choices = ()
            return
        definition, form_role = compatible
        self.fields["form_definition"].queryset = KoboFormDefinition.objects.filter(
            pk=definition.pk
        )
        self.fields["form_definition"].initial = definition
        self.fields["form_role"].choices = ((form_role, dict(KoboAsset.FormRole.choices)[form_role]),)
        self.fields["form_role"].initial = form_role


class KoboReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(
            (
                KoboSubmission.Status.APPROVED_FOR_IMPORT,
                "Aprobar para importación",
            ),
            (KoboSubmission.Status.REJECTED, "Rechazar"),
        )
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Razón",
    )

    def __init__(self, *args, submission: KoboSubmission, **kwargs):
        super().__init__(*args, **kwargs)
        self.submission = submission

    def clean(self):
        # PRE: submission is the staging record being reviewed.
        # POST: allows only one ready-state decision and requires rejection reason.
        cleaned_data = super().clean()
        if self.submission.status != KoboSubmission.Status.READY_FOR_REVIEW:
            raise ValidationError("La submission ya no está lista para revisión.")
        if (
            cleaned_data.get("decision") == KoboSubmission.Status.REJECTED
            and not cleaned_data.get("reason", "").strip()
        ):
            self.add_error("reason", "La razón es obligatoria al rechazar.")
        return cleaned_data


class KoboRejectionForm(forms.Form):
    reason = forms.ChoiceField(
        choices=tuple(REJECTION_REASON_LABELS.items()),
        label="Motivo",
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Comentario",
    )

    def clean(self):
        # PRE: rejection values come from the project review confirmation form.
        # POST: requires a comment only for the other reason without mutating data.
        cleaned_data = super().clean()
        if (
            cleaned_data.get("reason") == "other"
            and not cleaned_data.get("comment", "").strip()
        ):
            self.add_error("comment", "El comentario es obligatorio para el motivo otro.")
        return cleaned_data


class PastoralZoneProjectMappingForm(forms.Form):
    pastoral_zone = forms.ChoiceField(choices=(), label="Zona pastoral")
    project = forms.ModelChoiceField(queryset=None, label="Proyecto asociado")

    def __init__(self, *args, **kwargs):
        # PRE: the operations Project model is available to the Kobo integration.
        # POST: accepts only canonical zones and projects the administration service may use.
        from apps.integrations.kobo.contracts import PastoralZone
        from apps.integrations.kobo.presentation import pastoral_zone_label
        from apps.operations.models import Project

        super().__init__(*args, **kwargs)
        self.fields["pastoral_zone"].choices = tuple(
            (zone.value, pastoral_zone_label(zone)) for zone in PastoralZone
        )
        self.fields["pastoral_zone"].widget.attrs.update({"class": "form-select"})
        self.fields["project"].queryset = Project.objects.filter(
            status__in=(Project.Status.PLANNED, Project.Status.ACTIVE, Project.Status.SUSPENDED)
        ).order_by("code", "pk")
        self.fields["project"].widget.attrs.update({"class": "form-select"})


class TerritorialReasonForm(forms.Form):
    reason = forms.CharField(
        label="Motivo",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class TerritorialConflictResolutionForm(TerritorialReasonForm):
    decision = forms.ChoiceField(
        label="Decisión",
        choices=(
            (KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING, "Conservar identidad actual"),
            (KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED, "Aceptar propuesta"),
            (KoboTerritorialIdentityConflict.Resolution.DISMISSED, "Descartar conflicto"),
        ),
    )
