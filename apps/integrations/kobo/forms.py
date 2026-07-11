from django import forms
from django.core.exceptions import ValidationError

from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboProjectBinding,
    KoboSubmission,
)
from apps.integrations.kobo.services import validate_routing_source_field


class KoboAssetConfigurationForm(forms.Form):
    name = forms.CharField(max_length=255)
    form_definition = forms.ModelChoiceField(
        queryset=KoboFormDefinition.objects.none()
    )
    form_role = forms.ChoiceField(choices=KoboAsset.FormRole.choices)

    def __init__(self, *args, **kwargs):
        # PRE: active form definitions may exist in the local registry mirror.
        # POST: exposes only active persisted definitions as configuration choices.
        super().__init__(*args, **kwargs)
        self.fields["form_definition"].queryset = KoboFormDefinition.objects.filter(
            is_active=True
        ).order_by("title", "version")


class KoboProjectBindingForm(forms.Form):
    routing_type = forms.ChoiceField(choices=KoboProjectBinding.RoutingType.choices)
    project = forms.ModelChoiceField(queryset=None)
    source_field = forms.CharField(max_length=255, required=False)
    source_value = forms.CharField(max_length=255, required=False)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        # PRE: the operations Project model is installed.
        # POST: project choices are constrained by a ModelChoiceField queryset.
        from apps.operations.models import Project

        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.order_by("name", "pk")

    def clean(self):
        # PRE: submitted values are candidate binding configuration.
        # POST: enforces route shape and delegates source-field domain validation.
        cleaned_data = super().clean()
        routing_type = cleaned_data.get("routing_type")
        source_field = cleaned_data.get("source_field", "").strip()
        source_value = cleaned_data.get("source_value", "").strip()
        if routing_type == KoboProjectBinding.RoutingType.DIRECT:
            if source_field or source_value:
                raise ValidationError("El routing directo no admite campos de origen.")
        elif routing_type == KoboProjectBinding.RoutingType.FIELD_VALUE:
            if not source_field or not source_value:
                raise ValidationError("El routing por campo exige campo y valor.")
            try:
                validate_routing_source_field(source_field)
            except ValidationError as exc:
                self.add_error("source_field", exc)
        cleaned_data["source_field"] = source_field
        cleaned_data["source_value"] = source_value
        return cleaned_data


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
