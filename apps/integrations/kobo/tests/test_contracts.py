from apps.integrations.kobo.contracts import AttachmentPrivacy
from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.contracts import TerritorialRoutingResult
from apps.integrations.kobo.contracts import TerritorialRoutingStatus
from apps.integrations.kobo.contracts import KoboAttachmentPayload
from apps.integrations.kobo.contracts import KoboSubmissionPayload
from apps.integrations.kobo.contracts import ProcessingResult
from apps.integrations.kobo.contracts import ValidationIssue
from apps.integrations.kobo.contracts import ValidationSeverity
from apps.integrations.kobo.errors import KoboAttachmentError
from apps.integrations.kobo.errors import KoboAuthenticationError
from apps.integrations.kobo.errors import KoboConfigurationError
from apps.integrations.kobo.errors import KoboIntegrationError
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.errors import KoboProcessingError
from apps.integrations.kobo.errors import KoboNormalizationError
from apps.integrations.kobo.errors import KoboUnsupportedFormError
from apps.integrations.kobo.form_registry import get_registered_form
from apps.integrations.kobo.form_registry import list_registered_forms
from apps.integrations.kobo.form_registry import KoboFormType
from apps.integrations.kobo.form_registry import resolve_form_type
from apps.integrations.kobo.forms import SUPPORTED_FORM_ROLES
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.normalizers import adapt_kobo_payload
from apps.integrations.kobo.normalizers import normalize_submission
from apps.integrations.kobo.territorial import normalize_nucleo_code
from apps.integrations.kobo.territorial import normalize_pastoral_zone
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date
from datetime import datetime
from datetime import timezone
from django.test import SimpleTestCase
from zoneinfo import ZoneInfo
import re


class KoboContractsTests(SimpleTestCase):
    def test_normalizes_nucleo_code_without_changing_internal_symbols(self):
        cases = {
            " cat-004 ": "CAT-004",
            "centro 01": "CENTRO 01",
            "A-B/C": "A-B/C",
            "Núcleo 1": "NÚCLEO 1",
        }

        for raw_value, expected_value in cases.items():
            with self.subTest(raw_value=raw_value):
                self.assertEqual(normalize_nucleo_code(raw_value), expected_value)

    def test_rejects_invalid_nucleo_code_values(self):
        for raw_value in ("   ", None, 7):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(KoboNormalizationError):
                    normalize_nucleo_code(raw_value)

    def test_normalizes_only_the_five_canonical_pastoral_zones(self):
        for raw_value, expected_value in (
            ("catia_la_mar", PastoralZone.CATIA_LA_MAR),
            (" CENTRO ", PastoralZone.CENTRO),
            ("este", PastoralZone.ESTE),
            ("montana", PastoralZone.MONTANA),
            ("insular", PastoralZone.INSULAR),
        ):
            with self.subTest(raw_value=raw_value):
                self.assertEqual(normalize_pastoral_zone(raw_value), expected_value)

    def test_rejects_pastoral_zone_labels_and_unknown_values(self):
        for raw_value in ("Centro Pastoral", "catia la mar", "montaña", "norte", "", None):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(KoboNormalizationError):
                    normalize_pastoral_zone(raw_value)

    def test_territorial_routing_contract_is_pure_and_identifier_based(self):
        result = TerritorialRoutingResult(
            status=TerritorialRoutingStatus.PENDING_IDENTITY,
            form_type=KoboFormType.FICHA_10,
            nucleo_code_original=" nv-010 ",
            nucleo_code_normalized="NV-010",
            reason_code=TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY,
        )

        self.assertIsNone(result.project_id)
        self.assertEqual(result.status, TerritorialRoutingStatus.PENDING_IDENTITY)
    def test_submission_contract_can_be_created(self):
        attachment = KoboAttachmentPayload(
            field_name="temple_front_photo",
            source_url="https://example.test/photo.jpg",
        )
        submitted_at = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)
        payload = KoboSubmissionPayload(
            external_id="submission-001",
            form_id="ficha_01_territorio",
            form_version="20260710",
            pastoral_zone="zona_1",
            parish="parroquia_1",
            assessment_date=date(2026, 7, 10),
            submitted_at=submitted_at,
            submitted_by="enumerator-01",
            device_id="device-01",
            normalized_payload={"families": 24},
            attachments=(attachment,),
        )

        self.assertEqual(payload.parish, "parroquia_1")
        self.assertEqual(payload.submitted_at, submitted_at)
        self.assertEqual(payload.attachments, (attachment,))
        self.assertFalse(hasattr(payload, "raw_payload"))

    def test_validation_and_processing_contracts_can_be_created(self):
        issue = ValidationIssue(
            code="required",
            message="Parish is required.",
            severity=ValidationSeverity.ERROR,
            field_name="parish",
        )
        result = ProcessingResult(
            success=False,
            status="rejected",
            issues=(issue,),
        )

        self.assertEqual(result.issues, (issue,))

    def test_contract_is_immutable(self):
        payload = KoboSubmissionPayload(
            external_id="submission-001",
            form_id="ficha_01_territorio",
            form_version="20260710",
            pastoral_zone="zona_1",
            parish="parroquia_1",
        )

        with self.assertRaises(FrozenInstanceError):
            payload.parish = "otra"

    def test_attachment_defaults_to_internal_review(self):
        attachment = KoboAttachmentPayload(
            field_name="temple_front_photo",
            source_url="https://example.test/photo.jpg",
        )

        self.assertEqual(
            attachment.privacy_level,
            AttachmentPrivacy.INTERNAL_REVIEW,
        )

    def test_kobo_errors_share_base_exception(self):
        error_types = (
            KoboConfigurationError,
            KoboAuthenticationError,
            KoboPayloadError,
            KoboProcessingError,
            KoboAttachmentError,
        )

        for error_type in error_types:
            with self.subTest(error_type=error_type):
                self.assertTrue(issubclass(error_type, KoboIntegrationError))


class KoboFicha01NormalizerTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default_timezone = ZoneInfo("America/Caracas")

    def valid_payload(self, **overrides):
        # PRE: overrides contains only Ficha 1 XLSForm values or Kobo metadata.
        # POST: returns a complete new-contract payload without persistence.
        payload = {
            "_uuid": "ficha-01-normalized",
            "_xform_id_string": "ficha-01-asset",
            "_submission_time": "2026-07-12T12:30:00",
            "_submitted_by": "internal-submitter",
            "deviceid": "private-device-id",
            "today": "2026-07-12",
            "nucleo_code": " NV-001 ",
            "pastoral_zone": "catia_la_mar",
            "parish": "Caraballeda",
            "community_sector": "Tanaguarena",
            "location": "10.5 -66.5 12 3",
            "parish_delegate": "Delegada reservada",
            "contact_phone": "+58-sensitive-phone",
            "main_informant_role": "Vocero comunitario",
            "communities_covered": "Tanaguarena y zonas vecinas",
            "estimated_households": "0",
            "access_difficulties": "yes",
            "access_difficulties_notes": "",
            "initial_priority_perception": "high",
            "general_notes": "Nota general.",
            "_attachments": [
                {
                    "question_xpath": "evidence/photo",
                    "download_url": "https://kf.example.test/private/photo.jpg",
                    "media_file_basename": "photo.jpg",
                    "mimetype": "image/jpeg",
                }
            ],
        }
        payload.update(overrides)
        return payload

    def normalize(self, payload=None, **routing_overrides):
        # PRE: payload is Ficha 1-like data and overrides contains routing fields.
        # POST: returns a pure normalization result using an explicit timezone.
        routing = {
            "form_id": FICHA_01_FORM_ID,
            "form_version": FICHA_01_VERSION,
        }
        routing.update(routing_overrides)
        return normalize_submission(
            self.valid_payload() if payload is None else payload,
            default_timezone=self.default_timezone,
            **routing,
        )

    def slash_payload(self, **overrides):
        # PRE: overrides contains synthetic Ficha 1 slash-path values or metadata.
        # POST: returns a complete Kobo REST Services-shaped payload.
        payload = {
            "_uuid": "ficha-01-slash-normalized",
            "_xform_id_string": "ficha-01-asset",
            "__version__": FICHA_01_VERSION,
            "today": "2026-07-12",
            "identification/nucleo_code": "NV-SYNTHETIC",
            "identification/pastoral_zone": "catia_la_mar",
            "identification/parish": "Parroquia sintética",
            "identification/community_sector": "Sector sintético",
            "identification/location": "10.5 -66.5",
            "identification/parish_delegate": "",
            "identification/contact_phone": "",
            "identification/main_informant_role": "Vocería",
            "territorial_summary/communities_covered": "Comunidad sintética",
            "territorial_summary/estimated_households": "10",
            "territorial_summary/access_difficulties": "unknown",
            "territorial_summary/initial_priority_perception": "high",
            "territorial_summary/general_notes": "Nota sintética.",
        }
        payload.update(overrides)
        return payload

    def test_adapts_slash_paths_without_mutating_raw_payload(self):
        raw_payload = {
            "_uuid": "adapter-synthetic",
            "identification/parish": "Parroquia sintética",
            "territorial_summary/estimated_households": 10,
            "a/b/c": None,
        }
        original_payload = deepcopy(raw_payload)

        adapted = adapt_kobo_payload(raw_payload)

        self.assertEqual(adapted["_uuid"], "adapter-synthetic")
        self.assertEqual(adapted["identification"]["parish"], "Parroquia sintética")
        self.assertEqual(
            adapted["territorial_summary"]["estimated_households"], 10
        )
        self.assertIsNone(adapted["a"]["b"]["c"])
        self.assertEqual(raw_payload, original_payload)

    def test_rejects_structural_slash_path_collisions(self):
        with self.assertRaises(KoboPayloadError):
            adapt_kobo_payload(
                {
                    "identification": "texto",
                    "identification/parish": "Parroquia sintética",
                }
            )

    def test_normalizes_realistic_slash_payload(self):
        result = self.normalize(self.slash_payload())

        self.assertEqual(result.external_id, "ficha-01-slash-normalized")
        self.assertEqual(result.pastoral_zone, "catia_la_mar")
        self.assertEqual(result.parish, "Parroquia sintética")
        self.assertEqual(result.primary_community, "Sector sintético")
        self.assertEqual(result.normalized_payload["nucleo_code"], "NV-SYNTHETIC")
        self.assertEqual(result.normalized_payload["estimated_households"], 10)

    def test_normalizes_complete_depurated_payload(self):
        result = self.normalize()

        self.assertEqual(result.external_id, "ficha-01-normalized")
        self.assertEqual(result.form_id, FICHA_01_FORM_ID)
        self.assertEqual(result.form_version, FICHA_01_VERSION)
        self.assertEqual(result.pastoral_zone, "catia_la_mar")
        self.assertEqual(result.parish, "Caraballeda")
        self.assertEqual(result.primary_community, "Tanaguarena")
        self.assertEqual(result.assessment_date, date(2026, 7, 12))
        self.assertEqual(result.normalized_payload["nucleo_code"], "NV-001")
        self.assertEqual(result.normalized_payload["nucleo_code_original"], " NV-001 ")
        self.assertEqual(result.normalized_payload["nucleo_code_normalized"], "NV-001")
        self.assertEqual(result.normalized_payload["pastoral_zone_original"], "catia_la_mar")
        self.assertEqual(result.normalized_payload["pastoral_zone_normalized"], "catia_la_mar")
        self.assertNotIn("_submitted_by", result.normalized_payload)
        self.assertNotIn("deviceid", result.normalized_payload)
        self.assertNotIn("download_url", result.normalized_payload)

    def test_does_not_modify_raw_payload(self):
        raw_payload = self.valid_payload()
        original_payload = deepcopy(raw_payload)

        self.normalize(raw_payload)

        self.assertEqual(raw_payload, original_payload)

    def test_trims_nucleo_code_and_allows_empty_access_notes(self):
        raw_payload = self.valid_payload(
            nucleo_code="  NV-010  ",
            pastoral_zone=" CENTRO ",
        )

        result = self.normalize(raw_payload)

        self.assertEqual(result.normalized_payload["nucleo_code"], "NV-010")
        self.assertEqual(result.normalized_payload["nucleo_code_original"], "  NV-010  ")
        self.assertEqual(result.pastoral_zone, "centro")
        self.assertEqual(result.normalized_payload["pastoral_zone_original"], " CENTRO ")
        self.assertIsNone(result.normalized_payload["access_difficulties_notes"])

    def test_parses_assessment_date_and_aware_submission_time(self):
        result = self.normalize()

        self.assertEqual(result.assessment_date, date(2026, 7, 12))
        self.assertIsNotNone(result.submitted_at.utcoffset())
        self.assertEqual(result.submitted_at.tzinfo, self.default_timezone)

    def test_estimated_households_zero_and_none_are_valid(self):
        self.assertEqual(self.normalize(self.valid_payload(estimated_households=0)).normalized_payload["estimated_households"], 0)
        self.assertIsNone(self.normalize(self.valid_payload(estimated_households=None)).normalized_payload["estimated_households"])

    def test_normalizes_flat_location_and_prefers_geolocation(self):
        raw_payload = self.valid_payload(_geolocation=[10.6, -66.6, 12, 3])

        location = self.normalize(raw_payload).normalized_payload["location"]

        self.assertEqual(
            location,
            {
                "latitude": 10.6,
                "longitude": -66.6,
                "altitude": 12.0,
                "accuracy": 3.0,
            },
        )

    def test_uses_flat_location_when_geolocation_is_missing(self):
        raw_payload = self.valid_payload()
        raw_payload.pop("_geolocation", None)

        location = self.normalize(raw_payload).normalized_payload["location"]

        self.assertEqual(location["latitude"], 10.5)
        self.assertEqual(location["longitude"], -66.5)

    def test_rejects_coordinates_outside_valid_range(self):
        raw_payload = self.valid_payload(location="91 -66")

        with self.assertRaises(KoboPayloadError):
            self.normalize(raw_payload)

    def test_attachments_are_internal_and_absent_from_normalized_payload(self):
        result = self.normalize()

        self.assertEqual(len(result.attachments), 1)
        self.assertTrue(
            all(
                attachment.privacy_level == AttachmentPrivacy.INTERNAL_REVIEW
                for attachment in result.attachments
            )
        )
        self.assertNotIn("_attachments", result.normalized_payload)
        self.assertNotIn("download_url", result.normalized_payload)

    def test_ignores_deleted_attachments(self):
        raw_payload = self.valid_payload()
        deleted_attachment = deepcopy(raw_payload["_attachments"][0])
        deleted_attachment["is_deleted"] = True
        raw_payload["_attachments"].append(deleted_attachment)

        result = self.normalize(raw_payload)

        self.assertEqual(len(result.attachments), 1)

    def test_invalid_attachment_reports_its_index(self):
        raw_payload = self.valid_payload()
        raw_payload["_attachments"][0].pop("download_url")

        with self.assertRaisesMessage(KoboPayloadError, "Attachment 0"):
            self.normalize(raw_payload)

    def test_missing_uuid_fails_early(self):
        raw_payload = self.valid_payload()
        raw_payload.pop("_uuid")

        with self.assertRaisesMessage(KoboPayloadError, "_uuid"):
            self.normalize(raw_payload)

    def test_rejects_required_and_controlled_values(self):
        cases = (
            ("nucleo_code", "   "),
            ("pastoral_zone", "unknown"),
            ("estimated_households", -1),
            ("estimated_households", True),
            ("estimated_households", "not-an-integer"),
            ("access_difficulties", "sometimes"),
            ("initial_priority_perception", "urgent"),
            ("location", "bad-location"),
            ("_attachments", {}),
        )

        for field, value in cases:
            with self.subTest(field=field, value=value):
                with self.assertRaises(KoboPayloadError):
                    self.normalize(self.valid_payload(**{field: value}))

    def test_unknown_form_or_version_fails(self):
        routing_overrides = (
            {"form_id": "unknown"},
            {"form_version": "unknown"},
        )

        for overrides in routing_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(KoboUnsupportedFormError):
                    self.normalize(**overrides)


class KoboFicha10NormalizerTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default_timezone = ZoneInfo("America/Caracas")

    def valid_payload(self, **overrides):
        # PRE: overrides contains only Ficha 10 form values or Kobo metadata.
        # POST: returns a complete Ficha 10 payload without persistence.
        payload = {
            "_uuid": "ficha-10-normalized",
            "_submission_time": "2026-07-12T12:30:00",
            "_submitted_by": "internal-submitter",
            "deviceid": "private-device-id",
            "today": "2026-07-12",
            "nucleo_code": " NV-010 ",
            "microproject": {
                "microproject_name": "Rehabilitación del centro comunitario",
                "component": "infrastructure",
                "problem_summary": "El centro requiere reparaciones urgentes.",
                "specific_objective": "Recuperar un espacio seguro de atención.",
                "beneficiary_group": "youth women parish_volunteers youth",
                "main_activities": "Reparar techo y adecuar instalaciones.",
                "estimated_cost_range": "5000_15000",
                "implementation_urgency": "immediate",
                "technical_viability": "high",
                "expected_result": "Centro comunitario operativo.",
            },
            "_attachments": [
                {
                    "question_xpath": "evidence/photo",
                    "download_url": "https://kf.example.test/private/photo.jpg",
                    "media_file_basename": "photo.jpg",
                    "mimetype": "image/jpeg",
                }
            ],
        }
        microproject_overrides = {
            key: value
            for key, value in overrides.items()
            if key in payload["microproject"]
        }
        payload["microproject"].update(microproject_overrides)
        payload.update(
            {
                key: value
                for key, value in overrides.items()
                if key not in microproject_overrides
            }
        )
        return payload

    def normalize(self, payload=None, **routing_overrides):
        # PRE: payload is Ficha 10-like data and overrides contains routing fields.
        # POST: returns the generic dispatcher result using an explicit timezone.
        routing = {"form_id": FICHA_10_FORM_ID, "form_version": FICHA_10_VERSION}
        routing.update(routing_overrides)
        return normalize_submission(
            self.valid_payload() if payload is None else payload,
            default_timezone=self.default_timezone,
            **routing,
        )

    def test_normalizes_complete_microproject_payload(self):
        result = self.normalize()

        self.assertEqual(result.external_id, "ficha-10-normalized")
        self.assertEqual(result.form_id, FICHA_10_FORM_ID)
        self.assertEqual(result.form_version, FICHA_10_VERSION)
        self.assertEqual(result.pastoral_zone, "")
        self.assertEqual(result.parish, "")
        self.assertEqual(result.primary_community, "")
        self.assertEqual(result.assessment_date, date(2026, 7, 12))
        self.assertEqual(result.normalized_payload["nucleo_code"], "NV-010")
        self.assertEqual(result.normalized_payload["nucleo_code_original"], " NV-010 ")
        self.assertEqual(result.normalized_payload["nucleo_code_normalized"], "NV-010")
        self.assertEqual(
            result.normalized_payload["beneficiary_group"],
            ["youth", "women", "parish_volunteers"],
        )
        self.assertNotIn("_submitted_by", result.normalized_payload)
        self.assertNotIn("download_url", result.normalized_payload)

    def test_preserves_raw_payload_and_private_attachment_sources(self):
        raw_payload = self.valid_payload()
        original_payload = deepcopy(raw_payload)

        result = self.normalize(raw_payload)

        self.assertEqual(raw_payload, original_payload)
        self.assertEqual(result.attachments[0].privacy_level, AttachmentPrivacy.INTERNAL_REVIEW)
        self.assertNotIn("_attachments", result.normalized_payload)

    def test_normalizes_slash_paths_into_the_microproject_section(self):
        raw_payload = self.valid_payload()
        microproject = raw_payload.pop("microproject")
        raw_payload.update(
            {f"microproject/{key}": value for key, value in microproject.items()}
        )

        result = self.normalize(raw_payload)

        self.assertEqual(
            result.normalized_payload["microproject_name"],
            "Rehabilitación del centro comunitario",
        )
        self.assertEqual(
            result.normalized_payload["beneficiary_group"],
            ["youth", "women", "parish_volunteers"],
        )

    def test_rejects_missing_empty_or_invalid_microproject_section(self):
        for value in (None, {}, "not-a-mapping"):
            with self.subTest(value=value):
                raw_payload = self.valid_payload()
                raw_payload["microproject"] = value
                with self.assertRaises(KoboPayloadError):
                    self.normalize(raw_payload)

    def test_rejects_required_and_unsupported_values(self):
        cases = (
            ("nucleo_code", " "),
            ("microproject_name", None),
            ("component", "other"),
            ("beneficiary_group", "unknown"),
            ("estimated_cost_range", "unbounded"),
            ("implementation_urgency", "eventually"),
            ("technical_viability", "unknown"),
            ("_attachments", {}),
        )

        for field, value in cases:
            with self.subTest(field=field, value=value):
                with self.assertRaises(KoboPayloadError):
                    self.normalize(self.valid_payload(**{field: value}))

    def test_ignores_deleted_attachments_and_identifies_invalid_indices(self):
        raw_payload = self.valid_payload()
        raw_payload["_attachments"].append(
            {"is_deleted": True, "question_xpath": "ignored"}
        )
        self.assertEqual(len(self.normalize(raw_payload).attachments), 1)

        raw_payload = self.valid_payload(_attachments=[{"question_xpath": "missing-url"}])
        with self.assertRaisesMessage(KoboPayloadError, "Attachment 0"):
            self.normalize(raw_payload)


class KoboFicha11NormalizerTests(SimpleTestCase):
    SCORE_FIELDS = (
        "physical_damage_score",
        "affected_families_score",
        "social_vulnerability_score",
        "services_interruption_score",
        "livelihood_loss_score",
        "parish_capacity_score",
        "territorial_accessibility_score",
        "allies_availability_score",
        "rapid_impact_score",
        "financial_viability_score",
    )
    SCORING_FIELDS = SCORE_FIELDS + (
        "priority_total",
        "suggested_semaphore",
        "final_semaphore",
        "final_priority",
        "priority_summary",
        "linked_microprojects",
    )

    def valid_payload(self, **overrides):
        # PRE: overrides contains only Ficha 11 fields or Kobo metadata.
        # POST: returns a complete prioritization payload without persistence.
        payload = {
            "_uuid": "ficha-11-normalized",
            "today": "2026-07-12",
            "nucleo_code": " NV-011 ",
            "scoring": {
                **{field: "1" for field in self.SCORE_FIELDS},
                "final_semaphore": "red",
                "final_priority": "critical",
                "priority_summary": "Intervención técnica prioritaria.",
            },
        }
        scoring_overrides = {
            key: value for key, value in overrides.items() if key in self.SCORING_FIELDS
        }
        payload["scoring"].update(scoring_overrides)
        payload.update(
            {
                key: value
                for key, value in overrides.items()
                if key not in scoring_overrides
            }
        )
        return payload

    def normalize(self, payload=None, **routing_overrides):
        # PRE: payload is Ficha 11 data and routing values identify its contract.
        # POST: returns the canonical dispatcher result with no persistence.
        routing = {"form_id": FICHA_11_FORM_ID, "form_version": FICHA_11_VERSION}
        routing.update(routing_overrides)
        return normalize_submission(
            self.valid_payload() if payload is None else payload,
            default_timezone=ZoneInfo("America/Caracas"),
            **routing,
        )

    def test_normalizes_scores_and_recalculates_total_and_semaphore(self):
        result = self.normalize()

        self.assertEqual(result.normalized_payload["nucleo_code"], "NV-011")
        self.assertEqual(result.normalized_payload["nucleo_code_original"], " NV-011 ")
        self.assertEqual(result.normalized_payload["nucleo_code_normalized"], "NV-011")
        self.assertTrue(
            all(isinstance(result.normalized_payload[field], int) for field in self.SCORE_FIELDS)
        )
        self.assertEqual(result.normalized_payload["priority_total"], 10)
        self.assertEqual(result.normalized_payload["suggested_semaphore"], "gray")
        self.assertEqual(result.normalized_payload["linked_microprojects"], "")

    def test_normalizes_slash_paths_into_the_scoring_section_without_mutation(self):
        raw_payload = self.valid_payload()
        scoring = raw_payload.pop("scoring")
        raw_payload.update({f"scoring/{key}": value for key, value in scoring.items()})
        original_payload = deepcopy(raw_payload)

        result = self.normalize(raw_payload)

        self.assertEqual(raw_payload, original_payload)
        self.assertEqual(result.normalized_payload["physical_damage_score"], 1)
        self.assertEqual(result.normalized_payload["final_semaphore"], "red")

    def test_rejects_missing_empty_non_mapping_or_root_only_scoring_section(self):
        for value in (None, {}, "not-a-mapping"):
            with self.subTest(value=value):
                raw_payload = self.valid_payload(scoring=value)
                with self.assertRaises(KoboPayloadError):
                    self.normalize(raw_payload)

        raw_payload = self.valid_payload()
        scoring = raw_payload.pop("scoring")
        raw_payload.update(scoring)
        with self.assertRaises(KoboPayloadError):
            self.normalize(raw_payload)

    def test_calculates_each_semaphore_threshold(self):
        for score, expected in ((1, "gray"), (2, "green"), (3, "yellow"), (4, "red"), (5, "red")):
            with self.subTest(score=score):
                payload = self.valid_payload(**{field: str(score) for field in self.SCORE_FIELDS})
                self.assertEqual(
                    self.normalize(payload).normalized_payload["suggested_semaphore"],
                    expected,
                )

    def test_final_values_can_differ_from_suggested_and_linked_text_is_preserved(self):
        result = self.normalize(
            self.valid_payload(
                final_semaphore="yellow",
                final_priority="low",
                linked_microprojects="MP-01, MP-02",
            )
        )

        self.assertEqual(result.normalized_payload["final_semaphore"], "yellow")
        self.assertEqual(result.normalized_payload["final_priority"], "low")
        self.assertEqual(result.normalized_payload["linked_microprojects"], "MP-01, MP-02")

    def test_rejects_invalid_scores_and_final_values(self):
        for field, value in ((field, value) for field in self.SCORE_FIELDS for value in (None, "", "0", "6", "1.5", True, "no")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(KoboPayloadError):
                    self.normalize(self.valid_payload(**{field: value}))
        for field, value in (
            ("final_semaphore", "blue"),
            ("final_priority", "urgent"),
            ("priority_summary", " "),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(KoboPayloadError):
                    self.normalize(self.valid_payload(**{field: value}))

    def test_keeps_matching_kobo_calculations_without_warnings(self):
        result = self.normalize(
            self.valid_payload(priority_total="10", suggested_semaphore="gray")
        )

        self.assertEqual(result.normalized_payload["priority_total_original"], "10")
        self.assertEqual(result.normalized_payload["priority_total_calculated"], 10)
        self.assertEqual(result.normalized_payload["suggested_semaphore_original"], "gray")
        self.assertEqual(result.normalized_payload["suggested_semaphore_calculated"], "gray")
        self.assertEqual(result.normalized_payload["calculation_warnings"], [])

    def test_preserves_mismatching_kobo_calculations_as_warnings(self):
        result = self.normalize(
            self.valid_payload(priority_total="11", suggested_semaphore="red")
        )

        self.assertEqual(result.normalized_payload["priority_total_original"], "11")
        self.assertEqual(result.normalized_payload["priority_total_calculated"], 10)
        self.assertEqual(result.normalized_payload["suggested_semaphore_original"], "red")
        self.assertEqual(result.normalized_payload["suggested_semaphore_calculated"], "gray")
        self.assertEqual(
            [warning["code"] for warning in result.normalized_payload["calculation_warnings"]],
            ["PRIORITY_TOTAL_MISMATCH", "SUGGESTED_SEMAPHORE_MISMATCH"],
        )

    def test_accepts_matching_optional_calculations(self):
        result = self.normalize(
            self.valid_payload(priority_total="10", suggested_semaphore="gray")
        )

        self.assertEqual(result.normalized_payload["priority_total"], 10)
        self.assertEqual(result.normalized_payload["suggested_semaphore"], "gray")


class KoboFormRegistryTests(SimpleTestCase):
    def test_registry_contains_exactly_three_supported_forms(self):
        self.assertEqual(len(list_registered_forms()), 3)

    def test_first_form_uses_expected_identifier(self):
        first_form = list_registered_forms()[0]

        self.assertEqual(first_form.form_id, FICHA_01_FORM_ID)
        self.assertEqual(first_form.version, FICHA_01_VERSION)
        self.assertEqual(
            first_form.title,
            "Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
        )
        self.assertEqual(first_form.form_type, KoboFormType.FICHA_1)

    def test_resolves_each_supported_form_by_stable_identifier(self):
        self.assertEqual(
            resolve_form_type(FICHA_01_FORM_ID, FICHA_01_VERSION),
            KoboFormType.FICHA_1,
        )
        self.assertEqual(
            resolve_form_type(FICHA_10_FORM_ID, FICHA_10_VERSION),
            KoboFormType.FICHA_10,
        )
        self.assertEqual(
            resolve_form_type(FICHA_11_FORM_ID, FICHA_11_VERSION),
            KoboFormType.FICHA_11,
        )

    def test_registry_contains_only_active_contract_versions(self):
        versions = {form.version for form in list_registered_forms()}

        self.assertEqual(
            versions,
            {FICHA_01_VERSION, FICHA_10_VERSION, FICHA_11_VERSION},
        )

    def test_ficha_10_uses_its_exact_contract(self):
        registered_form = get_registered_form(FICHA_10_FORM_ID, FICHA_10_VERSION)

        self.assertEqual(
            registered_form.title,
            "Ficha 10 - Microproyecto priorizado (depurada)",
        )
        self.assertEqual(registered_form.normalizer_name, "normalize_ficha_10")

    def test_ficha_11_uses_its_exact_contract(self):
        registered_form = get_registered_form(FICHA_11_FORM_ID, FICHA_11_VERSION)

        self.assertEqual(
            registered_form.title,
            "Ficha 11 - Matriz de priorización y semáforo (depurada)",
        )
        self.assertEqual(registered_form.normalizer_name, "normalize_ficha_11")

    def test_supported_roles_keep_ficha_10_separate_from_ficha_1(self):
        self.assertEqual(
            SUPPORTED_FORM_ROLES[(FICHA_01_FORM_ID, FICHA_01_VERSION)],
            KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.assertEqual(
            SUPPORTED_FORM_ROLES[(FICHA_10_FORM_ID, FICHA_10_VERSION)],
            KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )
        self.assertEqual(
            SUPPORTED_FORM_ROLES[(FICHA_11_FORM_ID, FICHA_11_VERSION)],
            KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )

    def test_fichas_2_to_9_are_not_registered(self):
        with self.assertRaises(KoboPayloadError):
            get_registered_form("ficha_04_servicios_infraestructura_abasto", "20260710")

    def test_unknown_form_raises_payload_error(self):
        with self.assertRaises(KoboUnsupportedFormError):
            get_registered_form("unknown_form", "20260710")

    def test_unknown_version_raises_payload_error(self):
        with self.assertRaises(KoboPayloadError):
            get_registered_form(FICHA_01_FORM_ID, "unknown_version")

    def test_normalizer_names_follow_numbered_pattern(self):
        pattern = re.compile(r"^normalize_ficha_\d{2}$")

        for registered_form in list_registered_forms():
            with self.subTest(form_id=registered_form.form_id):
                self.assertRegex(registered_form.normalizer_name, pattern)
