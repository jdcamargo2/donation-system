from django import forms
from django.core.exceptions import ValidationError

from apps.integrations.kobo.models import KoboSubmission


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
