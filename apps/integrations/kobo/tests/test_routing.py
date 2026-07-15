from apps.integrations.kobo.errors import KoboConfigurationError
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.services import resolve_project_binding
from apps.integrations.kobo.services import resolve_routing_field
from apps.operations.models import Project
from apps.operations.models import ProjectUpdate
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.test import override_settings


class KoboProjectBindingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 - Territorio",
            version="20260710",
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="asset-ficha-01",
            name="Ficha territorial",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        cls.project = Project.objects.create(
            code="PRJ-KOBO-001",
            name="Proyecto Kobo uno",
        )
        cls.other_project = Project.objects.create(
            code="PRJ-KOBO-002",
            name="Proyecto Kobo dos",
        )

    @override_settings(KOBO_ENABLED=False)
    def test_kobo_is_disabled_by_default(self):
        self.assertIs(settings.KOBO_ENABLED, False)

    def test_asset_uid_cannot_be_duplicated(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboAsset.objects.create(
                asset_uid=self.asset.asset_uid,
                name="Duplicado",
                form_definition=self.form_definition,
                form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            )

    def test_asset_accepts_only_declared_roles(self):
        asset = KoboAsset(
            asset_uid="invalid-role-asset",
            name="Rol inválido",
            form_definition=self.form_definition,
            form_role="approximate_role",
        )

        with self.assertRaises(ValidationError) as context:
            asset.full_clean()

        self.assertIn("form_role", context.exception.message_dict)

    def test_field_route_cannot_repeat_for_same_asset(self):
        KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="centro",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboProjectBinding.objects.create(
                asset=self.asset,
                project=self.other_project,
                routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
                source_field="submission.pastoral_zone",
                source_value="centro",
            )

    def test_project_accepts_multiple_source_values_for_same_asset(self):
        first = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="centro",
        )
        second = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="este",
        )

        self.assertNotEqual(first.pk, second.pk)

    def test_direct_requires_empty_source_fields(self):
        binding = KoboProjectBinding(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            source_field="submission.parish",
            source_value="parish",
        )

        with self.assertRaises(ValidationError):
            binding.full_clean()

    def test_field_value_requires_both_source_fields(self):
        binding = KoboProjectBinding(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.parish",
            source_value="   ",
        )

        with self.assertRaises(ValidationError):
            binding.full_clean()

    def test_only_one_direct_binding_is_allowed_per_asset(self):
        KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboProjectBinding.objects.create(
                asset=self.asset,
                project=self.other_project,
                routing_type=KoboProjectBinding.RoutingType.DIRECT,
            )

    def test_different_assets_can_bind_same_project(self):
        other_asset = KoboAsset.objects.create(
            asset_uid="asset-ficha-10",
            name="Microproyecto priorizado",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )

        first_binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="centro",
        )
        second_binding = KoboProjectBinding.objects.create(
            asset=other_asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="centro",
        )

        self.assertNotEqual(first_binding.asset_id, second_binding.asset_id)
        self.assertEqual(self.project.kobo_bindings.count(), 2)

    def test_inactive_binding_is_preserved_but_cannot_import(self):
        binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="insular",
            is_active=False,
        )

        with self.assertRaises(ValidationError):
            binding.validate_for_import()

        binding.refresh_from_db()
        self.assertFalse(binding.is_active)
        self.assertTrue(KoboProjectBinding.objects.filter(pk=binding.pk).exists())

    def test_binding_does_not_modify_project_or_create_updates(self):
        original_project_values = {
            "code": self.project.code,
            "name": self.project.name,
            "status": self.project.status,
        }

        KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="catia_la_mar",
        )
        self.project.refresh_from_db()

        self.assertEqual(
            {
                "code": self.project.code,
                "name": self.project.name,
                "status": self.project.status,
            },
            original_project_values,
        )
        self.assertFalse(ProjectUpdate.objects.exists())
        self.assertFalse(
            any(
                field.name.startswith("kobo_") and not field.auto_created
                for field in Project._meta.get_fields()
            )
        )


class KoboRoutingResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 - Territorio",
            version="20260710",
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="routing-asset",
            name="Routing asset",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        cls.project = Project.objects.create(
            code="PRJ-ROUTING-1",
            name="Exact routing project",
            status=Project.Status.ACTIVE,
        )
        cls.other_project = Project.objects.create(
            code="PRJ-ROUTING-2",
            name="Other routing project",
            status=Project.Status.ACTIVE,
        )

    def setUp(self):
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="routing-submission",
            raw_payload={
                "_uuid": "routing-submission",
                "project_code": "RAW-MUST-NOT-BE-USED",
            },
            normalized_payload={"project_code": "PROJECT-A"},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            pastoral_zone="catia_la_mar",
            parish="caraballeda",
            primary_community="community-a",
        )

    def create_field_binding(
        self,
        *,
        source_field="submission.pastoral_zone",
        source_value="catia_la_mar",
        project=None,
        is_active=True,
    ):
        # PRE: route data represents one field-value binding candidate.
        # POST: returns the persisted exact binding for this fixture asset.
        return KoboProjectBinding.objects.create(
            asset=self.asset,
            project=project or self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field=source_field,
            source_value=source_value,
            is_active=is_active,
        )

    def test_direct_route_resolves_without_reading_fields(self):
        binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
        )
        self.submission.normalized_payload = {}
        self.submission.pastoral_zone = ""

        resolution = resolve_project_binding(self.submission, self.asset)

        self.assertEqual(resolution.binding_id, binding.pk)
        self.assertEqual(resolution.routing_type, KoboProjectBinding.RoutingType.DIRECT)
        self.assertEqual(resolution.project_id, self.project.pk)

    def test_submission_pastoral_zone_resolves_exactly(self):
        binding = self.create_field_binding()

        resolution = resolve_project_binding(self.submission, self.asset)

        self.assertEqual(resolution.binding_id, binding.pk)
        self.assertEqual(resolution.source_value, "catia_la_mar")

    def test_nucleo_code_payload_field_resolves_exactly(self):
        self.submission.normalized_payload = {"nucleo_code": "NV-001"}
        binding = self.create_field_binding(
            source_field="payload.nucleo_code",
            source_value="NV-001",
        )

        resolution = resolve_project_binding(self.submission, self.asset)

        self.assertEqual(resolution.binding_id, binding.pk)
        self.assertEqual(resolution.project_id, self.project.pk)

    def test_routing_field_never_reads_raw_payload(self):
        self.submission.normalized_payload = {}

        with self.assertRaises(KoboPayloadError):
            resolve_routing_field(self.submission, "payload.nucleo_code")

    def test_invalid_routing_field_syntax_is_rejected(self):
        source_fields = (
            "unknown.project_code",
            "submission.status",
            "payload._private",
            "payload.items[0]",
            "payload.get(project_code)",
        )

        for source_field in source_fields:
            with self.subTest(source_field=source_field):
                with self.assertRaises(KoboPayloadError):
                    resolve_routing_field(self.submission, source_field)

    def test_missing_empty_or_non_text_value_is_rejected(self):
        scenarios = (
            ({}, "payload.missing"),
            ({"empty": "   "}, "payload.empty"),
            ({"number": 7}, "payload.number"),
        )

        for normalized_payload, source_field in scenarios:
            with self.subTest(source_field=source_field):
                self.submission.normalized_payload = normalized_payload
                with self.assertRaises(KoboPayloadError):
                    resolve_routing_field(self.submission, source_field)

    def test_inactive_binding_is_ignored_and_zero_matches_is_safe(self):
        self.create_field_binding(is_active=False)

        with self.assertRaisesMessage(KoboConfigurationError, "routing_not_found"):
            resolve_project_binding(self.submission, self.asset)

    def test_multiple_exact_matches_are_rejected(self):
        self.create_field_binding()
        self.create_field_binding(
            source_field="payload.project_code",
            source_value="PROJECT-A",
            project=self.other_project,
        )

        with self.assertRaisesMessage(KoboConfigurationError, "routing_ambiguous"):
            resolve_project_binding(self.submission, self.asset)
