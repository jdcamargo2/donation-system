from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboImportRecord
from apps.integrations.kobo.models import KoboProcessingEvent
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.submission_presentation import (
    choice_value_label,
    present_processing_events,
    present_submission_fields,
    submission_status_label,
)
from apps.operations.models import Project
from django.utils import timezone


class KoboReviewPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="kobo-viewer",
            password="test-password",
        )
        cls.reviewer = user_model.objects.create_user(
            username="kobo-reviewer",
            password="test-password",
        )
        cls.unprivileged = user_model.objects.create_user(
            username="no-kobo-permission",
            password="test-password",
        )
        view_permission = Permission.objects.get(codename="view_kobosubmission")
        change_permission = Permission.objects.get(codename="change_kobosubmission")
        cls.viewer.user_permissions.add(view_permission)
        cls.reviewer.user_permissions.add(view_permission, change_permission)
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 01 - Territorio",
            version=FICHA_01_VERSION,
        )
        cls.ficha_10 = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10",
            version=FICHA_10_VERSION,
        )
        cls.ficha_11 = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11",
            version=FICHA_11_VERSION,
        )
        cls.project = Project.objects.create(
            code="PRJ-REVIEW-01",
            name="Proyecto revisión",
            status=Project.Status.ACTIVE,
        )

    def setUp(self):
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="long-external-identifier-123456789",
            raw_payload={
                "_uuid": "raw-secret-marker",
                "_submitted_by": "internal-submitter",
                "deviceid": "private-device-id",
            },
            normalized_payload={
                "parish_delegate": "Sensitive Delegate",
                "contact_phone": "+58-secret-phone",
                "main_informant_role": "Sensitive Informant Role",
                "nucleo_code": "NV-001",
                "communities_covered": "Sector Norte",
                "estimated_households": 12,
                "access_difficulties": "yes",
                "initial_priority_perception": "high",
                "general_notes": "Notas del territorio",
            },
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            pastoral_zone="centro",
            parish="parish-one",
            primary_community="community-one",
            assessment_date=date(2026, 7, 11),
            nucleo_code_original="NV-001",
            nucleo_code_normalized="NV-001",
            routing_reason_code=TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY,
        )
        self.attachment = KoboAttachment.objects.create(
            submission=self.submission,
            field_name="territorial_evidence/temple_photo",
            source_url="https://kf.example.test/private/source-secret",
            original_filename="remote-personal-name.jpg",
            content_type="image/jpeg",
            size_bytes=123,
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.DOWNLOADED,
            file="kobo-safe-attachment.jpg",
        )
        KoboProcessingEvent.objects.create(
            submission=self.submission,
            stage="webhook",
            level=KoboProcessingEvent.Level.INFO,
            code="webhook_received",
            message="Kobo webhook submission received.",
        )
        KoboProcessingEvent.objects.create(
            submission=self.submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.INFO,
            code="normalized",
            message="Submission normalized and ready for review.",
        )
        self.list_url = reverse("kobo:submission_list")
        self.detail_url = reverse(
            "kobo:submission_detail",
            args=(self.submission.pk,),
        )
        self.review_url = reverse(
            "kobo:submission_review",
            args=(self.submission.pk,),
        )

    def test_login_is_required(self):
        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.url)

    def test_view_permission_is_required(self):
        self.client.force_login(self.unprivileged)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 403)

    def test_list_hides_sensitive_data(self):
        self.client.force_login(self.viewer)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "+58-secret-phone")
        self.assertNotContains(response, "private-device-id")
        self.assertContains(response, "parish-one")

    def test_detail_uses_operator_language_and_status_labels(self):
        self.client.force_login(self.viewer)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "Incidencia")
        self.assertContains(response, "Ficha 1 · Registro territorial")
        self.assertNotContains(response, "Revisión de formulario")
        self.assertNotContains(response, "Pendiente de revisión")
        self.assertNotContains(response, "Pendientes de revisión")
        self.assertNotContains(response, "Submission #")
        self.assertNotContains(response, "READY_FOR_REVIEW")
        self.assertNotContains(response, "Ready for review")
        self.assertEqual(
            submission_status_label(KoboSubmission.Status.READY_FOR_REVIEW),
            "Incidencia",
        )
        self.assertEqual(
            submission_status_label(KoboSubmission.Status.APPROVED_FOR_IMPORT),
            "Aprobado para importar",
        )
        self.assertEqual(
            submission_status_label(KoboSubmission.Status.PROCESSING_FAILED),
            "Error de procesamiento",
        )

    def test_detail_shows_incident_explanation_without_human_review_actions(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.detail_url)

        self.assertContains(response, "Incidencia")
        self.assertContains(response, "Núcleo no encontrado")
        self.assertContains(response, "Espere o importe primero la Ficha 1")
        self.assertContains(response, "Reintentar importación")
        self.assertNotContains(response, "Aprobar e importar")
        self.assertNotContains(response, "Solicitar corrección")
        self.assertNotContains(response, "Rechazar formulario")
        self.assertNotContains(response, "Pendientes de revisión")
        self.assertNotContains(response, "Registrar decisión")

    def test_detail_shows_import_result_for_imported_submission(self):
        self.submission.status = KoboSubmission.Status.IMPORTED
        self.submission.routing_status = KoboSubmission.RoutingStatus.RESOLVED
        self.submission.project = self.project
        self.submission.imported_at = timezone.now()
        self.submission.processed_at = self.submission.imported_at
        self.submission.save(
            update_fields=(
                "status",
                "routing_status",
                "project",
                "imported_at",
                "processed_at",
            )
        )
        KoboImportRecord.objects.create(
            submission=self.submission,
            handler_type="ficha_1",
            target_app_label="kobo",
            target_model="KoboTerritorialProfile",
            target_object_id=99,
            created_by=self.reviewer,
        )
        self.client.force_login(self.viewer)
        response = self.client.get(self.detail_url)

        self.assertContains(response, "Resultado de la importación")
        self.assertContains(response, "Importado automáticamente")
        self.assertContains(response, "Detalle de formulario")
        self.assertNotContains(response, "Aprobar e importar")
        self.assertNotContains(response, "Reintentar importación")

    def test_detail_shows_contact_and_hides_technical_from_viewer(self):
        self.client.force_login(self.viewer)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "Datos de contacto")
        self.assertContains(response, "Sensitive Delegate")
        self.assertContains(response, "Sensitive Informant Role")
        self.assertContains(response, "+58-secret-phone")
        self.assertNotContains(response, "Información técnica")
        self.assertNotContains(response, "raw-secret-marker")
        self.assertNotContains(response, "private-device-id")
        self.assertNotContains(response, "internal-submitter")
        self.assertContains(response, "NV-001")
        self.assertContains(response, "Comunidades cubiertas")
        self.assertNotContains(response, "parish_delegate")
        self.assertNotContains(response, "nucleo_code_normalized")

    def test_technical_information_is_collapsible_for_reviewer(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.detail_url)

        self.assertContains(response, "<details")
        self.assertContains(response, "Información técnica")
        self.assertContains(response, "Identificador externo")
        self.assertContains(response, "Payload crudo")
        self.assertContains(response, "raw-secret-marker")
        self.assertContains(response, "private-device-id")
        self.assertNotContains(response, "Raw payload (solo lectura)")

    def test_history_uses_operator_facing_events(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.detail_url)

        self.assertContains(response, "Formulario recibido desde KoboToolbox")
        self.assertContains(response, "Información procesada correctamente")
        self.assertNotContains(response, "webhook / webhook_received")
        self.assertNotContains(response, "normalization / normalized")
        self.assertNotContains(response, "Aprobar e importar")
        self.assertNotContains(response, "Solicitar corrección")
        self.assertNotContains(response, "Rechazar formulario")

    def test_technical_tools_appear_only_when_pertinent(self):
        self.client.force_login(self.reviewer)
        ready = self.client.get(self.detail_url)
        self.assertNotContains(ready, "Herramientas técnicas")

        self.submission.status = KoboSubmission.Status.PROCESSING_FAILED
        self.submission.save(update_fields=("status",))
        failed = self.client.get(self.detail_url)
        self.assertContains(failed, "Herramientas técnicas")
        self.assertContains(failed, "Reintentar procesamiento")

        self.attachment.status = KoboAttachment.Status.FAILED
        self.attachment.save(update_fields=("status",))
        with_attachments = self.client.get(self.detail_url)
        self.assertContains(with_attachments, "Reintentar adjuntos")

    def test_empty_states_avoid_huge_empty_cards(self):
        self.attachment.delete()
        self.submission.normalized_payload = {
            "nucleo_code": "NV-001",
            "communities_covered": "Sector Norte",
            "estimated_households": 12,
            "access_difficulties": "no",
            "initial_priority_perception": "low",
        }
        self.submission.save(update_fields=("normalized_payload",))
        self.client.force_login(self.viewer)
        response = self.client.get(self.detail_url)

        self.assertContains(response, "No hay datos de contacto registrados.")
        self.assertNotContains(response, "Datos de contacto</h2>")
        self.assertNotContains(response, "<th scope=\"col\">Archivo</th>")
        self.assertContains(response, "Sin proyecto asociado todavía.")

    def test_ficha_field_labels_and_value_translations(self):
        micro = KoboSubmission.objects.create(
            form_definition=self.ficha_10,
            external_id="micro-1",
            raw_payload={"_uuid": "micro-1"},
            normalized_payload={
                "microproject_name": "Huertos",
                "component": "livelihoods",
                "problem_summary": "Falta de ingresos",
                "specific_objective": "Crear empleo",
                "beneficiary_group": ["women"],
                "main_activities": "Capacitación",
                "estimated_cost_range": "5000_15000",
                "technical_viability": "low",
                "implementation_urgency": "short_term",
                "expected_result": "Ingresos estables",
            },
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            nucleo_code_original="NV-MICRO",
            nucleo_code_normalized="NV-MICRO",
        )
        fields = {field.key: field for field in present_submission_fields(micro)}
        self.assertEqual(fields["microproject_name"].label, "Nombre del microproyecto")
        self.assertEqual(fields["component"].label, "Componente")
        self.assertEqual(fields["component"].value, "Medios de vida")
        self.assertEqual(fields["beneficiary_group"].value, "Mujeres")
        self.assertEqual(fields["estimated_cost_range"].value, "Entre 5.000 y 15.000 USD")
        self.assertEqual(fields["technical_viability"].value, "Baja")
        self.assertEqual(fields["implementation_urgency"].value, "Corto plazo")
        self.assertEqual(choice_value_label("women"), "Mujeres")

        profile_fields = {
            field.key: field for field in present_submission_fields(self.submission)
        }
        self.assertEqual(profile_fields["access_difficulties"].label, "Dificultades de acceso")
        self.assertEqual(profile_fields["access_difficulties"].value, "Sí")

        assessment = KoboSubmission.objects.create(
            form_definition=self.ficha_11,
            external_id="prio-1",
            raw_payload={"_uuid": "prio-1"},
            normalized_payload={
                "nucleo_code": "NV-002",
                "physical_damage_score": 4,
                "final_priority": "high",
                "final_semaphore": "red",
                "priority_summary": "Priorizar intervención",
            },
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            nucleo_code_original="NV-002",
            nucleo_code_normalized="NV-002",
        )
        assessment_fields = {
            field.key: field for field in present_submission_fields(assessment)
        }
        self.assertEqual(
            assessment_fields["physical_damage_score"].label,
            "Nivel de daño físico",
        )
        self.assertEqual(assessment_fields["final_priority"].value, "Alta")
        self.assertEqual(assessment_fields["final_semaphore"].value, "Rojo")

        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:submission_detail", args=(micro.pk,))
        )
        self.assertContains(response, "Ficha 10 · Microproyecto priorizado")
        self.assertContains(response, "Medios de vida")
        self.assertContains(response, "Entre 5.000 y 15.000 USD")
        self.assertNotContains(response, "microproject_name")
        self.assertNotContains(response, ">livelihoods<")

    def test_presented_events_hide_stage_code_headers(self):
        events = present_processing_events(self.submission.processing_events.all())
        titles = [event.title for event in events]
        self.assertIn("Formulario recibido desde KoboToolbox", titles)
        self.assertIn("Información procesada correctamente", titles)
        for event in events:
            self.assertNotIn(" / ", event.title)

    def test_legacy_review_endpoint_still_accepts_post_but_ui_hides_actions(self):
        self.client.force_login(self.reviewer)
        detail = self.client.get(self.detail_url)
        self.assertNotContains(detail, "Aprobar e importar")

        response = self.client.post(
            self.review_url,
            {
                "review_intent": "approve",
                "reason": "",
            },
        )
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )

    def test_get_cannot_execute_review(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(self.review_url)
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )

    def test_detail_does_not_expose_attachment_source_or_private_link(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "kobo-safe-attachment.jpg")
        self.assertNotContains(response, self.attachment.source_url)
        self.assertNotContains(response, "remote-personal-name.jpg")
        self.assertNotContains(response, "href=\"/media/")
        self.assertContains(response, "csrfmiddlewaretoken")
