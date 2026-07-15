from django.core.exceptions import ValidationError

from apps.integrations.kobo.services.common import RoutingResolution
from apps.integrations.kobo.errors import KoboConfigurationError, KoboPayloadError
from apps.integrations.kobo.models import KoboAsset, KoboProjectBinding, KoboSubmission


def resolve_routing_field(
    submission: KoboSubmission,
    source_field: str,
) -> str:
    """
    PRE: source_field starts with submission. or payload.
    POST: returns a non-empty textual whitelisted/model or normalized payload
    value without raw payload access, arbitrary getattr, paths, indices, or calls.
    """
    try:
        prefix, field_name = validate_routing_source_field(source_field)
    except ValidationError as exc:
        raise KoboPayloadError(str(exc.message)) from exc

    if prefix == "submission":
        submission_values = {
            "pastoral_zone": submission.pastoral_zone,
            "parish": submission.parish,
            "primary_community": submission.primary_community,
            "external_id": submission.external_id,
        }
        if field_name not in submission_values:
            raise KoboPayloadError("Routing submission field is not allowed.")
        value = submission_values[field_name]
    elif prefix == "payload":
        if not isinstance(submission.normalized_payload, dict):
            raise KoboPayloadError("Normalized routing payload is unavailable.")
        if field_name not in submission.normalized_payload:
            raise KoboPayloadError("Normalized routing field is missing.")
        value = submission.normalized_payload[field_name]
    else:
        raise KoboPayloadError("Routing source field prefix is invalid.")

    if not isinstance(value, str) or not value.strip():
        raise KoboPayloadError("Routing source value must be non-empty text.")
    return value


def validate_routing_source_field(source_field: str) -> tuple[str, str]:
    """
    PRE: source_field is candidate direct model or normalized-payload routing data.
    POST: returns its safe prefix/key or raises ValidationError without data access.
    """
    if not isinstance(source_field, str) or "." not in source_field:
        raise ValidationError("Routing source field prefix is invalid.")
    if any(marker in source_field for marker in ("/", "[", "]", "(", ")", " ")):
        raise ValidationError("Routing source field is not allowed.")
    prefix, field_name = source_field.split(".", 1)
    if not field_name or field_name.startswith("_") or "." in field_name:
        raise ValidationError("Routing source field is not allowed.")
    allowed_submission_fields = {
        "pastoral_zone",
        "parish",
        "primary_community",
        "external_id",
    }
    if prefix == "submission" and field_name not in allowed_submission_fields:
        raise ValidationError("Routing submission field is not allowed.")
    if prefix not in {"submission", "payload"}:
        raise ValidationError("Routing source field prefix is invalid.")
    return prefix, field_name


def _routing_resolution(binding: KoboProjectBinding) -> RoutingResolution:
    # PRE: binding is the single exact active routing match.
    # POST: returns immutable identifiers and route metadata without modification.
    return RoutingResolution(
        binding_id=binding.pk,
        asset_id=binding.asset_id,
        project_id=binding.project_id,
        routing_type=binding.routing_type,
        source_field=binding.source_field,
        source_value=binding.source_value,
    )


def resolve_project_binding(
    submission: KoboSubmission,
    asset: KoboAsset,
) -> RoutingResolution:
    """
    PRE: submission is normalized, asset is active, and any assigned asset agrees.
    POST: returns the sole exact active direct/field-value binding, ignores inactive
    bindings, performs no project-name lookup, and never modifies submission.
    """
    if not asset.is_active:
        raise KoboConfigurationError("routing_asset_inactive")
    if submission.asset_id is not None and submission.asset_id != asset.pk:
        raise KoboConfigurationError("routing_asset_mismatch")
    if not isinstance(submission.normalized_payload, dict):
        raise KoboPayloadError("Normalized routing payload is unavailable.")

    active_bindings = KoboProjectBinding.objects.filter(
        asset=asset,
        is_active=True,
    ).select_related("project")
    direct_bindings = list(
        active_bindings.filter(routing_type=KoboProjectBinding.RoutingType.DIRECT)
    )
    if len(direct_bindings) > 1:
        raise KoboConfigurationError("routing_ambiguous")
    if direct_bindings:
        from apps.operations.models import Project

        if direct_bindings[0].project.status != Project.Status.ACTIVE:
            raise KoboConfigurationError("routing_project_inactive")
        return _routing_resolution(direct_bindings[0])

    matches = []
    for binding in active_bindings.filter(
        routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE
    ):
        resolved_value = resolve_routing_field(submission, binding.source_field)
        if resolved_value == binding.source_value:
            matches.append(binding)
    if not matches:
        raise KoboConfigurationError("routing_not_found")
    if len(matches) > 1:
        raise KoboConfigurationError("routing_ambiguous")
    from apps.operations.models import Project

    if matches[0].project.status != Project.Status.ACTIVE:
        raise KoboConfigurationError("routing_project_inactive")
    return _routing_resolution(matches[0])
