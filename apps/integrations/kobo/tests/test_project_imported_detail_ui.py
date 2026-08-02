"""Focused UI and presentation tests for imported Kobo project detail (KD1)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset, KoboAttachment, KoboFormDefinition, KoboSubmission
from apps.integrations.kobo.submission_presentation import (
    build_imported_submission_detail_context,
    choice_value_label,
    format_location,
    present_imported_submission_registration,
    present_imported_submission_sections,
    present_imported_submission_summary,
)
from apps.operations.models import Project


@override_settings(KOBO_ENABLED=True)
class ImportedDetailPresentationUnitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
        cls.ficha_10 = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 - Microproyecto priorizado (depurada)",
            version=FICHA_10_VERSION,
        )
        cls.ficha_11 = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 - Matriz de priorización y semáforo (depurada)",
            version=FICHA_11_VERSION,
        )

    def setUp(self):
        self.project = Project.objects.create(
            code="PRJ-000001",
            name="Catia La Mar piloto",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="imported-detail-ui-asset",
            name="Ficha 1 activa",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.payload = {
            "nucleo_code": "NV-001",
            "communities_covered": "Comunidades visibles",
            "estimated_households": 300,
            "access_difficulties": "yes",
            "access_difficulties_notes": None,
            "initial_priority_perception": "medium",
            "general_notes": "Nota visible",
            "location": {
                "latitude": 13.125832,
                "longitude": -68.515603,
                "altitude": None,
                "accuracy": None,
            },
            "parish_delegate": "Sensitive Delegate",
            "contact_phone": "+58-sensitive-phone",
            "main_informant_role": "Sensitive Informant Role",
        }
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            asset=self.asset,
            project=self.project,
            external_id="imported-detail-ui",
            raw_payload={
                "_uuid": "imported-detail-ui",
                "_submitted_by": "Sensitive Submitter",
                "deviceid": "Sensitive Device",
            },
            normalized_payload=deepcopy(self.payload),
            status=KoboSubmission.Status.IMPORTED,
            pastoral_zone="catia_la_mar",
            parish="visible-parish",
            primary_community="visible-community",
            nucleo_code_normalized="NV-001",
            assessment_date=date(2026, 7, 11),
            imported_at=django_timezone.now(),
            normalized_at=django_timezone.now(),
            processed_at=django_timezone.now(),
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )

    def test_choice_translations(self):
        self.assertEqual(choice_value_label("yes"), "Sí")
        self.assertEqual(choice_value_label("no"), "No")
        self.assertEqual(choice_value_label("unknown"), "Sin determinar")
        self.assertEqual(choice_value_label("low"), "Baja")
        self.assertEqual(choice_value_label("medium"), "Media")
        self.assertEqual(choice_value_label("high"), "Alta")
        self.assertEqual(choice_value_label("critical"), "Crítica")
        self.assertEqual(choice_value_label(""), "—")
        self.assertEqual(choice_value_label(None), "—")

    def test_format_location_complete_and_partial(self):
        complete = format_location(
            {
                "latitude": 13.125832,
                "longitude": -68.515603,
                "altitude": 12.5,
                "accuracy": 3,
            }
        )
        self.assertEqual(complete["latitude"], "13.125832")
        self.assertEqual(complete["longitude"], "-68.515603")
        self.assertEqual(complete["altitude"], "12.5")
        self.assertEqual(complete["accuracy"], "3")

        partial = format_location(
            {"latitude": 13.125832, "longitude": -68.515603, "altitude": None}
        )
        self.assertEqual(partial["accuracy"], "No disponible")
        self.assertEqual(partial["altitude"], "No disponible")

    def test_format_location_null_malformed_and_zero(self):
        null_location = format_location(None)
        self.assertEqual(null_location["latitude"], "No disponible")
        self.assertEqual(null_location["longitude"], "No disponible")

        malformed = format_location("10 -66")
        self.assertEqual(malformed["latitude"], "No disponible")
        self.assertNotIn("{", malformed["latitude"])
        self.assertNotIn("None", malformed["latitude"])

        zero = format_location({"latitude": 0, "longitude": 0.0, "accuracy": 0})
        self.assertEqual(zero["latitude"], "0")
        self.assertEqual(zero["longitude"], "0")
        self.assertEqual(zero["accuracy"], "0")

    def test_ficha_1_grouping_and_summary_ordering(self):
        summary = present_imported_submission_summary(self.submission)
        self.assertEqual(
            [item["label"] for item in summary],
            [
                "Código del Núcleo Vital",
                "Zona pastoral",
                "Hogares estimados",
                "Prioridad inicial",
                "Fecha de evaluación",
            ],
        )
        self.assertEqual(summary[1]["value"], "Catia La Mar")
        self.assertEqual(summary[3]["value"], "Media")

        sections = present_imported_submission_sections(self.submission)
        self.assertEqual(
            [section["title"] for section in sections],
            ["Territorio y población", "Acceso y evaluación"],
        )
        acceso = {field["label"]: field["value"] for field in sections[1]["fields"]}
        self.assertEqual(acceso["Dificultades de acceso"], "Sí")
        self.assertEqual(acceso["Notas de acceso"], "—")

    def test_registration_and_no_payload_mutation(self):
        original = deepcopy(self.submission.normalized_payload)
        registration = present_imported_submission_registration(self.submission)
        labels = [item["label"] for item in registration]
        self.assertEqual(
            labels,
            [
                "Formulario técnico",
                "Versión del formulario",
                "Identificador externo",
                "Recibido",
                "Importado",
            ],
        )
        self.assertEqual(registration[0]["value"], FICHA_01_FORM_ID)
        build_imported_submission_detail_context(
            self.submission,
            can_view_sensitive=True,
        )
        self.assertEqual(self.submission.normalized_payload, original)

    def test_sensitive_block_omitted_without_permission(self):
        presentation = build_imported_submission_detail_context(
            self.submission,
            can_view_sensitive=False,
        )
        self.assertEqual(presentation["sensitive_fields"], [])
        self.assertEqual(presentation["technical_fields"], [])
        self.assertFalse(presentation["show_sensitive_block"])
        self.assertTrue(presentation["is_redesigned"])
        self.assertEqual(
            presentation["page_title"],
            "Ficha 1 · Identificación territorial",
        )
        self.assertNotIn("(depurada)", presentation["page_title"])


@override_settings(KOBO_ENABLED=True)
class ImportedProjectDetailUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="imported-detail-viewer",
            password="test-password",
        )
        cls.reviewer = user_model.objects.create_user(
            username="imported-detail-reviewer",
            password="test-password",
        )
        cls.unprivileged = user_model.objects.create_user(
            username="imported-detail-unprivileged",
            password="test-password",
        )
        permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(
                codename__in=(
                    "view_project",
                    "change_project",
                    "view_kobosubmission",
                    "change_kobosubmission",
                )
            )
        }
        cls.viewer.user_permissions.add(
            permissions["view_project"],
            permissions["view_kobosubmission"],
        )
        cls.reviewer.user_permissions.add(
            permissions["view_project"],
            permissions["change_project"],
            permissions["view_kobosubmission"],
            permissions["change_kobosubmission"],
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
        cls.microproject_form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 - Microproyecto priorizado (depurada)",
            version=FICHA_10_VERSION,
        )
        cls.prioritization_form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 - Matriz de priorización y semáforo (depurada)",
            version=FICHA_11_VERSION,
        )

    def setUp(self):
        self.project = Project.objects.create(
            code="PRJ-000001",
            name="Proyecto piloto",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="imported-ui-ficha1",
            name="Ficha 1",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.microproject_asset = KoboAsset.objects.create(
            asset_uid="imported-ui-ficha10",
            name="Ficha 10",
            form_definition=self.microproject_form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )
        self.prioritization_asset = KoboAsset.objects.create(
            asset_uid="imported-ui-ficha11",
            name="Ficha 11",
            form_definition=self.prioritization_form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )
        self.imported = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            asset=self.asset,
            project=self.project,
            external_id="ui-ficha1-imported",
            raw_payload={
                "_uuid": "ui-ficha1-imported",
                "_submitted_by": "Sensitive Submitter",
                "deviceid": "Sensitive Device",
            },
            normalized_payload={
                "nucleo_code": "NV-001",
                "communities_covered": "Comunidades visibles",
                "estimated_households": 300,
                "access_difficulties": "yes",
                "access_difficulties_notes": None,
                "initial_priority_perception": "medium",
                "general_notes": "Nota visible",
                "location": {
                    "latitude": 13.125832,
                    "longitude": -68.515603,
                    "altitude": None,
                    "accuracy": None,
                },
                "parish_delegate": "Sensitive Delegate",
                "contact_phone": "+58-sensitive-phone",
                "main_informant_role": "Sensitive Informant Role",
            },
            status=KoboSubmission.Status.IMPORTED,
            pastoral_zone="catia_la_mar",
            parish="visible-parish",
            primary_community="visible-community",
            assessment_date=date(2026, 7, 11),
            imported_at=django_timezone.now(),
            normalized_at=django_timezone.now(),
            processed_at=django_timezone.now(),
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )
        self.microproject_imported = KoboSubmission.objects.create(
            form_definition=self.microproject_form_definition,
            asset=self.microproject_asset,
            project=self.project,
            external_id="ui-ficha10-imported",
            raw_payload={"_uuid": "ui-ficha10-imported"},
            normalized_payload={
                "nucleo_code": "NV-001",
                "microproject_name": "Techo para el centro comunitario",
                "component": "infrastructure",
                "problem_summary": "Filtraciones persistentes.",
                "specific_objective": "Recuperar la cubierta.",
                "beneficiary_group": ["youth", "women"],
                "main_activities": "Reparar el techo.",
                "estimated_cost_range": "5000_15000",
                "implementation_urgency": "immediate",
                "technical_viability": "high",
                "expected_result": "Espacio protegido.",
            },
            status=KoboSubmission.Status.IMPORTED,
            assessment_date=date(2026, 7, 12),
            imported_at=django_timezone.now(),
            processed_at=django_timezone.now(),
        )
        self.prioritization_imported = KoboSubmission.objects.create(
            form_definition=self.prioritization_form_definition,
            asset=self.prioritization_asset,
            project=self.project,
            external_id="ui-ficha11-imported",
            raw_payload={"_uuid": "ui-ficha11-imported"},
            normalized_payload={
                "nucleo_code": "NV-011",
                "physical_damage_score": 4,
                "affected_families_score": 4,
                "social_vulnerability_score": 4,
                "services_interruption_score": 4,
                "livelihood_loss_score": 4,
                "parish_capacity_score": 4,
                "territorial_accessibility_score": 4,
                "allies_availability_score": 4,
                "rapid_impact_score": 4,
                "financial_viability_score": 4,
                "priority_total": 40,
                "suggested_semaphore": "red",
                "final_semaphore": "yellow",
                "final_priority": "high",
                "priority_summary": "Prioridad validada.",
                "linked_microprojects": "MP-01",
            },
            status=KoboSubmission.Status.IMPORTED,
            assessment_date=date(2026, 7, 12),
            imported_at=django_timezone.now(),
            processed_at=django_timezone.now(),
        )
        self.downloaded_attachment = KoboAttachment.objects.create(
            submission=self.imported,
            field_name="territorial_evidence/front",
            source_url="https://kf.example.test/private/source-never-visible",
            content_type="image/jpeg",
            size_bytes=20,
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.DOWNLOADED,
            file="kobo-visible-evidence.jpg",
        )

    def test_authorized_anonymous_and_forbidden(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.unprivileged)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.viewer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_ficha_1_title_summary_translations_and_sections(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        self.client.force_login(self.viewer)
        response = self.client.get(url)
        content = response.content.decode()

        self.assertContains(response, "Ficha 1 · Identificación territorial")
        self.assertNotContains(response, "(depurada)")
        self.assertContains(response, FICHA_01_FORM_ID)
        self.assertContains(response, "Registro Kobo")
        self.assertContains(response, "Código del Núcleo Vital")
        self.assertContains(response, "Zona pastoral")
        self.assertContains(response, "Hogares estimados")
        self.assertContains(response, "Prioridad inicial")
        self.assertContains(response, "Fecha de evaluación")
        self.assertContains(response, "Catia La Mar")
        self.assertContains(response, "Sí")
        self.assertContains(response, "Media")
        self.assertNotIn(">yes<", content)
        self.assertNotIn(">medium<", content)
        self.assertNotIn(">catia_la_mar<", content)
        self.assertNotContains(response, "{'latitude'")
        self.assertNotContains(response, "'latitude'")
        self.assertNotIn(">None<", content)
        self.assertNotIn("None,", content)
        self.assertContains(response, "13.125832")
        self.assertContains(response, "-68.515603")
        self.assertContains(response, "No disponible")
        self.assertContains(response, "Territorio y población")
        self.assertContains(response, "Acceso y evaluación")
        self.assertContains(response, "Ubicación")
        self.assertContains(response, "Evidencias")
        self.assertEqual(content.count("<h1"), 1)
        self.assertIn("<dl", content)
        self.assertIn("<dt", content)
        self.assertIn("<dd", content)

    def test_sensitive_collapsed_and_evidence_protected(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        self.client.force_login(self.viewer)
        viewer_response = self.client.get(url)
        self.assertNotContains(viewer_response, "Datos internos y técnicos")
        self.assertNotContains(viewer_response, "Delegado parroquial")
        self.assertNotContains(viewer_response, "parish_delegate")
        self.assertNotContains(viewer_response, "contact_phone")
        self.assertNotContains(viewer_response, "main_informant_role")
        self.assertNotContains(viewer_response, "submitted_by")
        self.assertNotContains(viewer_response, "device_id")
        self.assertContains(viewer_response, "kobo-visible-evidence.jpg")
        self.assertContains(viewer_response, "Ver")
        self.assertContains(viewer_response, "Descargar")
        self.assertNotContains(viewer_response, "/media/")
        self.assertNotContains(viewer_response, self.downloaded_attachment.source_url)
        self.assertNotContains(viewer_response, ".url")
        self.assertNotContains(viewer_response, "border-warning")

        self.client.force_login(self.reviewer)
        reviewer_response = self.client.get(url)
        self.assertContains(reviewer_response, "<details")
        self.assertContains(reviewer_response, "<summary")
        self.assertContains(reviewer_response, "Datos internos y técnicos")
        self.assertContains(reviewer_response, "Delegado parroquial")
        self.assertContains(reviewer_response, "Teléfono de contacto")
        self.assertContains(reviewer_response, "Rol del informante principal")
        self.assertContains(reviewer_response, "Enviado por")
        self.assertContains(reviewer_response, "ID del dispositivo")

    def test_empty_evidence_state(self):
        self.downloaded_attachment.delete()
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        )
        self.assertContains(response, "No hay evidencias descargadas disponibles.")
        self.assertNotContains(response, "list-group-item")

    def test_ficha_10_and_11_compatibility_smoke(self):
        self.client.force_login(self.viewer)
        ficha_10 = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.microproject_imported.pk,),
            )
        )
        self.assertEqual(ficha_10.status_code, 200)
        self.assertContains(ficha_10, "Microproyecto priorizado")
        self.assertContains(ficha_10, "Nombre del microproyecto")
        self.assertContains(ficha_10, "Infraestructura")
        self.assertContains(ficha_10, "Registro Kobo")

        ficha_11 = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.prioritization_imported.pk,),
            )
        )
        self.assertEqual(ficha_11.status_code, 200)
        self.assertContains(ficha_11, "Nivel de daño físico")
        self.assertContains(ficha_11, "Semáforo sugerido")
        self.assertContains(ficha_11, "Semáforo final validado")

        project_response = self.client.get(
            reverse("project_detail", args=(self.project.pk,))
        )
        self.assertEqual(project_response.status_code, 200)
        self.assertContains(
            project_response,
            reverse("kobo:project_submission_detail", args=(self.imported.pk,)),
        )
        self.assertContains(
            project_response,
            reverse(
                "kobo:project_submission_detail",
                args=(self.microproject_imported.pk,),
            ),
        )
        self.assertContains(
            project_response,
            reverse(
                "kobo:project_submission_detail",
                args=(self.prioritization_imported.pk,),
            ),
        )

    def test_hub_submission_detail_remains_available(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:submission_detail", args=(self.imported.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información principal del formulario")
        self.assertNotContains(response, "Territorio y población")
