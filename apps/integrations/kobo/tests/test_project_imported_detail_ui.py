"""Focused UI and presentation tests for imported Kobo project detail (KD2)."""

from __future__ import annotations

import re
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
    build_openstreetmap_map_url,
    choice_value_label,
    format_linked_collection,
    format_location,
    format_presented_value,
    get_valid_coordinates,
    present_imported_submission_registration,
    present_imported_submission_sections,
    present_imported_submission_summary,
)
from apps.operations.models import Project


def _field_dd_markup(content: str, label: str) -> str:
    """
    PRE: content is rendered imported-detail HTML with matching <dt>/<dd> pairs.
    POST: returns the first <dd> inner HTML whose preceding <dt> equals label.
    """
    pattern = (
        rf'<dt[^>]*>\s*{re.escape(label)}\s*</dt>\s*'
        rf'<dd[^>]*>(.*?)</dd>'
    )
    match = re.search(pattern, content, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"Imported-detail field not found: {label!r}")
    return match.group(1)


def _assert_plain_value_field(test_case: TestCase, content: str, label: str) -> str:
    """
    PRE: label is an ordinary imported-detail field rendered in content.
    POST: asserts no list markup in the value column and returns the <dd> markup.
    """
    dd = _field_dd_markup(content, label)
    test_case.assertNotIn("<ul", dd)
    test_case.assertNotIn("<li", dd)
    test_case.assertIn("<span", dd)
    test_case.assertNotRegex(
        dd,
        rf"(?is)<li[^>]*>\s*{re.escape(label)}\s*</li>",
    )
    return dd


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
        self.assertEqual(choice_value_label("red"), "Rojo")
        self.assertEqual(choice_value_label("yellow"), "Amarillo")
        self.assertEqual(choice_value_label("green"), "Verde")
        self.assertEqual(choice_value_label("gray"), "Gris")
        self.assertEqual(choice_value_label("infrastructure"), "Infraestructura")
        self.assertEqual(choice_value_label("immediate"), "Inmediata")
        self.assertEqual(choice_value_label("future_choice_code"), "Future choice code")
        self.assertEqual(choice_value_label(""), "—")
        self.assertEqual(choice_value_label(None), "—")
        self.assertEqual(choice_value_label(True), "Sí")
        self.assertEqual(choice_value_label(False), "No")

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

    def test_get_valid_coordinates_accepts_valid_and_zero(self):
        self.assertEqual(
            get_valid_coordinates(
                {"latitude": 13.125832, "longitude": -68.515603}
            ),
            (13.125832, -68.515603),
        )
        self.assertEqual(get_valid_coordinates({"latitude": 0, "longitude": 0}), (0.0, 0.0))

    def test_get_valid_coordinates_rejects_incomplete_and_non_dict(self):
        self.assertIsNone(get_valid_coordinates({"longitude": -68.515603}))
        self.assertIsNone(get_valid_coordinates({"latitude": 13.125832}))
        self.assertIsNone(get_valid_coordinates(None))
        self.assertIsNone(get_valid_coordinates("13.1,-68.5"))
        self.assertIsNone(get_valid_coordinates(["13.1", "-68.5"]))

    def test_get_valid_coordinates_rejects_malformed_and_boolean(self):
        self.assertIsNone(
            get_valid_coordinates({"latitude": "13.125832", "longitude": -68.515603})
        )
        self.assertIsNone(
            get_valid_coordinates({"latitude": True, "longitude": -68.515603})
        )
        self.assertIsNone(
            get_valid_coordinates({"latitude": 13.125832, "longitude": False})
        )

    def test_get_valid_coordinates_rejects_out_of_range(self):
        self.assertIsNone(get_valid_coordinates({"latitude": -90.1, "longitude": 0}))
        self.assertIsNone(get_valid_coordinates({"latitude": 90.1, "longitude": 0}))
        self.assertIsNone(get_valid_coordinates({"latitude": 0, "longitude": -180.1}))
        self.assertIsNone(get_valid_coordinates({"latitude": 0, "longitude": 180.1}))

    def test_get_valid_coordinates_rejects_nan_and_infinity(self):
        self.assertIsNone(
            get_valid_coordinates({"latitude": float("nan"), "longitude": 0})
        )
        self.assertIsNone(
            get_valid_coordinates({"latitude": float("inf"), "longitude": 0})
        )
        self.assertIsNone(
            get_valid_coordinates({"latitude": 0, "longitude": float("-inf")})
        )

    def test_get_valid_coordinates_does_not_mutate_input(self):
        location = {
            "latitude": 13.125832,
            "longitude": -68.515603,
            "altitude": None,
            "accuracy": None,
        }
        original = deepcopy(location)
        get_valid_coordinates(location)
        self.assertEqual(location, original)

    def test_build_openstreetmap_map_url_valid_and_privacy(self):
        location = {
            "latitude": 13.125832,
            "longitude": -68.515603,
            "altitude": None,
            "accuracy": None,
            "parish": "must-not-appear",
            "community": "must-not-appear",
        }
        url = build_openstreetmap_map_url(location)
        self.assertEqual(
            url,
            "https://www.openstreetmap.org/?mlat=13.125832&mlon=-68.515603"
            "#map=15/13.125832/-68.515603",
        )
        self.assertIn("mlat=13.125832", url)
        self.assertIn("mlon=-68.515603", url)
        self.assertIn("#map=15/", url)
        for forbidden in (
            "PRJ-",
            "parish",
            "community",
            "NV-001",
            "external",
            "form_id",
            "must-not-appear",
        ):
            self.assertNotIn(forbidden, url)

        self.assertIsNone(build_openstreetmap_map_url({"latitude": 13.1}))
        self.assertIsNone(build_openstreetmap_map_url(None))

    def test_presentation_location_includes_map_url(self):
        presentation = build_imported_submission_detail_context(
            self.submission,
            can_view_sensitive=False,
        )
        self.assertEqual(
            presentation["location"]["map_url"],
            "https://www.openstreetmap.org/?mlat=13.125832&mlon=-68.515603"
            "#map=15/13.125832/-68.515603",
        )
        self.assertEqual(presentation["location"]["latitude"], "13.125832")
        self.assertEqual(presentation["location"]["longitude"], "-68.515603")

        incomplete_payload = deepcopy(self.payload)
        incomplete_payload["location"] = {
            "latitude": 13.125832,
            "longitude": None,
            "altitude": None,
            "accuracy": None,
        }
        self.submission.normalized_payload = incomplete_payload
        incomplete_presentation = build_imported_submission_detail_context(
            self.submission,
            can_view_sensitive=False,
        )
        self.assertIsNone(incomplete_presentation["location"]["map_url"])
        self.assertEqual(incomplete_presentation["location"]["latitude"], "13.125832")
        self.assertEqual(
            incomplete_presentation["location"]["longitude"],
            "No disponible",
        )
        self.submission.normalized_payload = deepcopy(self.payload)

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
        for section in sections:
            for field in section["fields"]:
                self.assertNotIn("values", field)
                self.assertNotIn("value_list", field)
                self.assertIn("label", field)
                self.assertIn("value", field)

    def test_ficha_10_builder_summary_and_sections(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.ficha_10,
            asset=KoboAsset.objects.create(
                asset_uid="unit-ficha10",
                name="Ficha 10",
                form_definition=self.ficha_10,
                form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            ),
            project=self.project,
            external_id="unit-ficha10",
            raw_payload={"_uuid": "unit-ficha10"},
            normalized_payload={
                "nucleo_code": "NV-010",
                "microproject_name": "Techo comunitario",
                "component": "infrastructure",
                "problem_summary": "Filtraciones persistentes en el salón.",
                "specific_objective": "Recuperar la cubierta.",
                "beneficiary_group": ["youth", "women"],
                "main_activities": "Reparar el techo.",
                "estimated_cost_range": "5000_15000",
                "implementation_urgency": "immediate",
                "technical_viability": "high",
                "expected_result": "Espacio protegido.",
            },
            status=KoboSubmission.Status.IMPORTED,
            nucleo_code_normalized="NV-010",
            pastoral_zone="catia_la_mar",
            parish="Parroquia piloto",
            assessment_date=date(2026, 7, 12),
            imported_at=django_timezone.now(),
        )
        original = deepcopy(submission.normalized_payload)
        presentation = build_imported_submission_detail_context(
            submission, can_view_sensitive=False
        )
        self.assertTrue(presentation["is_redesigned"])
        self.assertEqual(
            presentation["page_title"],
            "Ficha 10 · Microproyecto priorizado",
        )
        self.assertNotIn("(depurada)", presentation["page_title"])
        self.assertIn("PRJ-000001", presentation["page_subtitle"])
        self.assertIn("NV-010", presentation["page_subtitle"])
        self.assertIn("Techo comunitario", presentation["page_subtitle"])
        self.assertIsNone(presentation["location"])
        self.assertEqual(
            [item["label"] for item in presentation["summary_items"]],
            [
                "Nombre del microproyecto",
                "Componente",
                "Urgencia",
                "Viabilidad técnica",
                "Rango de costo",
            ],
        )
        self.assertEqual(
            [item["value"] for item in presentation["summary_items"]],
            [
                "Techo comunitario",
                "Infraestructura",
                "Inmediata",
                "Alta",
                "Entre 5.000 y 15.000 USD",
            ],
        )
        self.assertEqual(
            [section["title"] for section in presentation["sections"]],
            [
                "Diagnóstico y objetivo",
                "Población y actividades",
                "Contexto territorial",
            ],
        )
        poblacion = {
            field["label"]: field["value"]
            for field in presentation["sections"][1]["fields"]
        }
        self.assertEqual(poblacion["Grupo beneficiario"], "Jóvenes, Mujeres")
        for section in presentation["sections"]:
            for field in section["fields"]:
                self.assertNotIn("values", field)
                self.assertNotIn("value_list", field)
        self.assertEqual(submission.normalized_payload, original)

    def test_ficha_11_builder_summary_sections_and_linked(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.ficha_11,
            asset=KoboAsset.objects.create(
                asset_uid="unit-ficha11",
                name="Ficha 11",
                form_definition=self.ficha_11,
                form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
            ),
            project=self.project,
            external_id="unit-ficha11",
            raw_payload={"_uuid": "unit-ficha11"},
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
                "linked_microprojects": "MP-01, MP-02",
            },
            status=KoboSubmission.Status.IMPORTED,
            nucleo_code_normalized="NV-011",
            assessment_date=date(2026, 7, 12),
            imported_at=django_timezone.now(),
        )
        original = deepcopy(submission.normalized_payload)
        presentation = build_imported_submission_detail_context(
            submission, can_view_sensitive=False
        )
        self.assertTrue(presentation["is_redesigned"])
        self.assertEqual(
            presentation["page_title"],
            "Ficha 11 · Evaluación de priorización",
        )
        self.assertIsNone(presentation["location"])
        self.assertEqual(
            [item["label"] for item in presentation["summary_items"]],
            [
                "Puntaje total",
                "Semáforo final",
                "Prioridad final",
                "Código del Núcleo Vital",
                "Fecha de evaluación",
            ],
        )
        self.assertEqual(presentation["summary_items"][1]["value"], "Amarillo")
        self.assertEqual(presentation["summary_items"][2]["value"], "Alta")
        self.assertEqual(
            [section["title"] for section in presentation["sections"]],
            [
                "Puntajes de evaluación",
                "Decisión de priorización",
                "Microproyectos vinculados",
            ],
        )
        scores = presentation["sections"][0]["fields"]
        self.assertEqual(scores[0]["label"], "Nivel de daño físico")
        self.assertEqual(scores[0]["value"], "4")
        self.assertEqual(len(scores), 10)
        for field in scores:
            self.assertNotIn("values", field)
            self.assertNotIn("value_list", field)
        decision = {
            field["label"]: field["value"]
            for field in presentation["sections"][1]["fields"]
        }
        self.assertEqual(decision["Semáforo sugerido"], "Rojo")
        self.assertEqual(decision["Semáforo final"], "Amarillo")
        for field in presentation["sections"][1]["fields"]:
            self.assertNotIn("values", field)
            self.assertNotIn("value_list", field)
        linked = presentation["sections"][2]["fields"][0]
        self.assertEqual(linked["value_list"], ["MP-01", "MP-02"])
        self.assertNotIn("values", linked)
        self.assertEqual(linked["value"], "—")
        self.assertNotIn("[", linked.get("value", ""))
        self.assertEqual(submission.normalized_payload, original)

    def test_linked_collection_formatting(self):
        empty = format_linked_collection("")
        self.assertEqual(empty["value"], "—")
        self.assertNotIn("values", empty)
        self.assertNotIn("value_list", empty)

        single = format_linked_collection("MP-01")
        self.assertEqual(single["value"], "MP-01")
        self.assertNotIn("values", single)
        self.assertNotIn("value_list", single)

        multi = format_linked_collection(["MP-01", "MP-02"])
        self.assertEqual(multi["value_list"], ["MP-01", "MP-02"])
        self.assertNotIn("values", multi)
        self.assertEqual(multi["value"], "—")
        self.assertNotIn("[", multi["value"])

        self.assertEqual(
            format_presented_value(["youth", "women"], format_name="multi_choice"),
            "Jóvenes, Mujeres",
        )
        self.assertNotIn("[", format_presented_value(["a", "b"], format_name="text"))

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
            nucleo_code_normalized="NV-001",
            pastoral_zone="catia_la_mar",
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
                "linked_microprojects": "MP-01, MP-02",
            },
            status=KoboSubmission.Status.IMPORTED,
            nucleo_code_normalized="NV-011",
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

        project_value = "PRJ-000001 - Proyecto piloto"
        self.assertEqual(
            len(re.findall(r"<dt[^>]*>\s*Proyecto\s*</dt>", content)),
            1,
        )
        proyecto_dd = _assert_plain_value_field(self, content, "Proyecto")
        self.assertEqual(proyecto_dd.count(project_value), 1)
        for label in (
            "Proyecto",
            "Parroquia",
            "Comunidad",
            "Hogares estimados",
            "Dificultades de acceso",
        ):
            _assert_plain_value_field(self, content, label)
        # Ordinary Ficha 1 fields must not invent list markup from dict.values.
        self.assertNotIn("list-unstyled", content)

    def test_valid_location_renders_privacy_conscious_map_link(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        self.client.force_login(self.viewer)
        response = self.client.get(url)
        content = response.content.decode()
        expected_map_url = (
            "https://www.openstreetmap.org/?mlat=13.125832&mlon=-68.515603"
            "#map=15/13.125832/-68.515603"
        )
        expected_href = (
            "https://www.openstreetmap.org/?mlat=13.125832&amp;mlon=-68.515603"
            "#map=15/13.125832/-68.515603"
        )

        self.assertContains(response, "Ver en mapa")
        self.assertContains(response, "www.openstreetmap.org")
        self.assertIn(f'href="{expected_href}"', content)
        self.assertEqual(
            response.context["presentation"]["location"]["map_url"],
            expected_map_url,
        )
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)
        self.assertIn(
            'aria-label="Ver ubicación en OpenStreetMap; se abrirá en una pestaña nueva"',
            content,
        )
        self.assertIn('aria-hidden="true"', content)
        self.assertContains(response, "bi-box-arrow-up-right")
        self.assertContains(response, "13.125832")
        self.assertContains(response, "-68.515603")
        for forbidden in (
            "leaflet",
            "<iframe",
            "tile.openstreetmap",
            "google.com/maps",
            "maps.googleapis",
            "btn-outline-secondary disabled",
            'aria-disabled="true"',
        ):
            self.assertNotIn(forbidden, content)
        self.assertNotRegex(
            content,
            r"<script[^>]*(?:openstreetmap|leaflet|maps\.googleapis)",
        )
        self.assertNotRegex(
            content,
            r"<img[^>]*(?:openstreetmap|leaflet|maps\.googleapis)",
        )
        self.assertNotRegex(
            content,
            r'<link[^>]*rel="preconnect"[^>]*(?:openstreetmap|maps\.google)',
        )
        self.assertEqual(content.count("openstreetmap.org"), 1)

    def test_missing_or_invalid_location_omits_map_link(self):
        self.client.force_login(self.viewer)

        missing_payload = deepcopy(self.imported.normalized_payload)
        missing_payload["location"] = {
            "latitude": None,
            "longitude": None,
            "altitude": None,
            "accuracy": None,
        }
        self.imported.normalized_payload = missing_payload
        self.imported.save(update_fields=("normalized_payload",))
        missing_response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        )
        self.assertEqual(missing_response.status_code, 200)
        self.assertContains(missing_response, "Ubicación")
        self.assertContains(missing_response, "No disponible")
        self.assertNotContains(missing_response, "Ver en mapa")
        self.assertNotContains(missing_response, "www.openstreetmap.org")
        self.assertNotContains(missing_response, "disabled")

        incomplete_payload = deepcopy(missing_payload)
        incomplete_payload["location"] = {
            "latitude": 13.125832,
            "longitude": None,
            "altitude": None,
            "accuracy": None,
        }
        self.imported.normalized_payload = incomplete_payload
        self.imported.save(update_fields=("normalized_payload",))
        invalid_response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        )
        self.assertContains(invalid_response, "13.125832")
        self.assertContains(invalid_response, "No disponible")
        self.assertNotContains(invalid_response, "Ver en mapa")
        self.assertNotContains(invalid_response, "www.openstreetmap.org")
        self.assertNotContains(invalid_response, "disabled")

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

    def test_ficha_10_redesigned_detail(self):
        KoboAttachment.objects.create(
            submission=self.microproject_imported,
            field_name="evidence/front",
            source_url="https://kf.example.test/private/ficha10-source",
            content_type="image/jpeg",
            size_bytes=12,
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.DOWNLOADED,
            file="kobo-ficha10-evidence.jpg",
        )
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.microproject_imported.pk,),
            )
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha 10 · Microproyecto priorizado")
        self.assertNotContains(response, "(depurada)")
        self.assertContains(response, "Nombre del microproyecto")
        self.assertContains(response, "Componente")
        self.assertContains(response, "Urgencia")
        self.assertContains(response, "Viabilidad técnica")
        self.assertContains(response, "Rango de costo")
        self.assertContains(response, "Diagnóstico y objetivo")
        self.assertContains(response, "Población y actividades")
        self.assertContains(response, "Contexto territorial")
        self.assertContains(response, "Evidencias")
        self.assertContains(response, "Registro Kobo")
        self.assertContains(response, "Infraestructura")
        self.assertContains(response, "Inmediata")
        self.assertContains(response, "Alta")
        self.assertContains(response, "Entre 5.000 y 15.000 USD")
        self.assertContains(response, "Jóvenes")
        self.assertContains(response, "Mujeres")
        self.assertContains(response, "Filtraciones persistentes.")
        self.assertContains(response, "kobo-ficha10-evidence.jpg")
        self.assertNotIn(">infrastructure<", content)
        self.assertNotIn(">immediate<", content)
        self.assertNotIn(">high<", content)
        self.assertNotIn(">5000_15000<", content)
        self.assertNotContains(response, "Ver en mapa")
        self.assertNotContains(response, "www.openstreetmap.org")
        self.assertNotContains(response, "Ubicación")
        self.assertNotContains(response, "/media/")
        self.assertNotContains(response, "ficha10-source")
        self.assertEqual(content.count("<h1"), 1)
        self.assertTrue(response.context["presentation"]["is_redesigned"])
        for label in (
            "Resumen del problema",
            "Objetivo específico",
            "Grupo beneficiario",
            "Proyecto",
        ):
            _assert_plain_value_field(self, content, label)
        self.assertNotIn("list-unstyled", content)

    def test_ficha_11_redesigned_detail(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.prioritization_imported.pk,),
            )
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha 11 · Evaluación de priorización")
        self.assertNotContains(response, "(depurada)")
        self.assertContains(response, "Puntaje total")
        self.assertContains(response, "Semáforo final")
        self.assertContains(response, "Prioridad final")
        self.assertContains(response, "Código del Núcleo Vital")
        self.assertContains(response, "Fecha de evaluación")
        self.assertContains(response, "Puntajes de evaluación")
        self.assertContains(response, "Decisión de priorización")
        self.assertContains(response, "Microproyectos vinculados")
        self.assertContains(response, "Evidencias")
        self.assertContains(response, "Registro Kobo")
        self.assertContains(response, "Nivel de daño físico")
        self.assertContains(response, "Rojo")
        self.assertContains(response, "Amarillo")
        self.assertContains(response, "Alta")
        self.assertContains(response, "MP-01")
        self.assertContains(response, "MP-02")
        self.assertIn("<ul", content)
        self.assertNotIn(">red<", content)
        self.assertNotIn(">yellow<", content)
        self.assertNotIn(">high<", content)
        self.assertNotIn("['MP-01'", content)
        self.assertNotIn('["MP-01"', content)
        self.assertNotContains(response, "physical_damage_score")
        self.assertNotContains(response, "suggested_semaphore")
        self.assertNotContains(response, "Ver en mapa")
        self.assertNotContains(response, "www.openstreetmap.org")
        self.assertNotContains(response, "Ubicación")
        self.assertEqual(content.count("<h1"), 1)
        self.assertTrue(response.context["presentation"]["is_redesigned"])

        for label in (
            "Nivel de daño físico",
            "Resumen de priorización",
            "Semáforo sugerido",
            "Semáforo final",
            "Prioridad final",
        ):
            _assert_plain_value_field(self, content, label)
        score_dd = _assert_plain_value_field(self, content, "Nivel de daño físico")
        self.assertIn(">4<", score_dd)

        linked_dd = _field_dd_markup(content, "Referencias")
        self.assertIn('class="list-unstyled mb-0 d-grid gap-1"', linked_dd)
        self.assertEqual(linked_dd.count("<ul"), 1)
        self.assertEqual(linked_dd.count("<li"), 2)
        self.assertIn("MP-01", linked_dd)
        self.assertIn("MP-02", linked_dd)
        self.assertNotIn("Referencias", linked_dd)
        self.assertNotIn("Microproyectos vinculados", linked_dd)

    def test_linked_microprojects_empty_and_single_render_without_list(self):
        self.client.force_login(self.viewer)

        empty_payload = deepcopy(self.prioritization_imported.normalized_payload)
        empty_payload["linked_microprojects"] = ""
        self.prioritization_imported.normalized_payload = empty_payload
        self.prioritization_imported.save(update_fields=["normalized_payload"])
        empty_response = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.prioritization_imported.pk,),
            )
        )
        empty_content = empty_response.content.decode()
        empty_dd = _field_dd_markup(empty_content, "Referencias")
        self.assertIn("—", empty_dd)
        self.assertNotIn("<ul", empty_dd)
        self.assertNotIn("<li", empty_dd)

        single_payload = deepcopy(empty_payload)
        single_payload["linked_microprojects"] = "MP-01"
        self.prioritization_imported.normalized_payload = single_payload
        self.prioritization_imported.save(update_fields=["normalized_payload"])
        single_response = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.prioritization_imported.pk,),
            )
        )
        single_content = single_response.content.decode()
        single_dd = _field_dd_markup(single_content, "Referencias")
        self.assertIn("MP-01", single_dd)
        self.assertNotIn("<ul", single_dd)
        self.assertNotIn("<li", single_dd)
        self.assertIn("<span", single_dd)

    def test_common_shell_across_forms(self):
        self.client.force_login(self.viewer)
        for submission in (
            self.imported,
            self.microproject_imported,
            self.prioritization_imported,
        ):
            response = self.client.get(
                reverse("kobo:project_submission_detail", args=(submission.pk,))
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Levantamiento Kobo importado")
            self.assertContains(response, "Registro Kobo")
            self.assertContains(response, "Evidencias")
            self.assertContains(response, 'aria-label="Resumen del levantamiento"')
            self.assertTrue(response.context["presentation"]["is_redesigned"])
            self.assertEqual(response.content.decode().count("<h1"), 1)

    def test_ficha_10_and_11_project_links_and_no_legacy_fallback(self):
        self.client.force_login(self.viewer)
        ficha_10 = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.microproject_imported.pk,),
            )
        )
        self.assertEqual(ficha_10.status_code, 200)
        self.assertContains(ficha_10, "Ficha 10 · Microproyecto priorizado")
        self.assertContains(ficha_10, "Diagnóstico y objetivo")
        self.assertNotContains(ficha_10, "Ver en mapa")

        ficha_11 = self.client.get(
            reverse(
                "kobo:project_submission_detail",
                args=(self.prioritization_imported.pk,),
            )
        )
        self.assertEqual(ficha_11.status_code, 200)
        self.assertContains(ficha_11, "Puntajes de evaluación")
        self.assertContains(ficha_11, "Semáforo final")
        self.assertNotContains(ficha_11, "Semáforo final validado")
        self.assertNotContains(ficha_11, "Ver en mapa")

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
        self.assertNotContains(response, "Diagnóstico y objetivo")
        self.assertNotContains(response, "Puntajes de evaluación")

    def test_evidence_privacy_uses_spanish_presentation_label(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisión interna")
        self.assertNotContains(response, "Internal review")
        self.assertNotContains(response, "Public candidate")
        self.assertNotContains(response, "Private")

    def test_history_detail_attachment_labels_are_spanish(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse(
                "kobo:project_submission_history_detail",
                args=(self.project.pk, self.imported.pk),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisión interna")
        self.assertContains(response, "Disponible")
        self.assertNotContains(response, "Internal review")
        self.assertNotContains(response, "Downloaded")
        self.assertNotContains(response, "Pending")
        self.assertContains(response, "Registro territorial")
        self.assertNotContains(response, "Territorial profile")
