import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
from queue import Queue
import re
from threading import Barrier, Event, Thread
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.storage import InMemoryStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone as django_timezone

from apps.integrations.kobo.contracts import (
    AttachmentPrivacy,
    KoboAttachmentPayload,
    KoboSubmissionPayload,
    ProcessingResult,
    ValidationIssue,
    ValidationSeverity,
)
from apps.integrations.kobo.attachments import (
    build_safe_filename,
    download_and_store_attachment,
    process_pending_attachments,
)
from apps.integrations.kobo.client import (
    DownloadedContent,
    KoboApiClient,
    KoboRemoteAsset,
)
from apps.integrations.kobo.errors import (
    KoboAttachmentError,
    KoboAuthenticationError,
    KoboConfigurationError,
    KoboIntegrationError,
    KoboPayloadError,
    KoboProcessingError,
)
from apps.integrations.kobo.form_registry import (
    get_registered_form,
    list_registered_forms,
)
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.forms import (
    KoboAssetProjectLinkForm,
    KoboProjectBindingForm,
    SUPPORTED_FORM_ROLES,
)
from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboProcessingEvent,
    KoboProjectBinding,
    KoboSubmission,
)
from apps.integrations.kobo.normalizers import adapt_kobo_payload, normalize_submission
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services import (
    activate_kobo_asset,
    associate_submission_with_project,
    configure_discovered_asset,
    create_project_binding,
    deactivate_kobo_asset,
    discover_assets,
    get_asset_readiness,
    link_asset_to_project,
    get_project_imported_submissions,
    get_project_pending_submissions,
    get_project_submission_history,
    import_kobo_submission,
    reject_kobo_submission,
    restore_kobo_submission_to_review,
    process_pending_submissions,
    receive_api_submission,
    receive_webhook_submission,
    resolve_project_binding,
    resolve_routing_field,
    review_submission,
    sync_ficha_01_submissions,
    sync_registered_forms,
    unlink_asset_from_project,
    validate_routing_source_field,
)
from apps.integrations.kobo.services.importers import (
    _lock_submission_for_operational_import,
)
from apps.operations.models import AuditLog, Project, ProjectUpdate


class StubHttpTransport:
    def __init__(
        self,
        *,
        status_code=200,
        body=b'{"count": 0, "next": null, "previous": null, "results": []}',
        content_type="",
        content_length=None,
        exception=None,
    ):
        self.status_code = status_code
        self.body = body
        self.content_type = content_type
        self.content_length = content_length
        self.exception = exception
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        # PRE: the client supplies a complete simulated GET request.
        # POST: records the request and returns or raises the configured outcome.
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        if self.exception is not None:
            raise self.exception
        return SimpleNamespace(
            status_code=self.status_code,
            body=self.body,
            content_type=self.content_type,
            content_length=self.content_length,
        )


class StubKoboClient:
    def __init__(self, submissions):
        self.submissions = submissions
        self.calls = []

    def get_submissions(self, asset_uid, *, limit=100):
        # PRE: synchronization supplies its configured asset and positive limit.
        # POST: records the query and returns the configured payloads unchanged.
        self.calls.append((asset_uid, limit))
        return self.submissions


class StubAttachmentClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.in_atomic_flags = []

    def download_attachment(self, url):
        # PRE: a pending attachment supplies its source URL.
        # POST: records the URL and returns or raises the next configured outcome.
        self.calls.append(url)
        self.in_atomic_flags.append(connection.in_atomic_block)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingAttachmentStorage(InMemoryStorage):
    def __init__(self, *, fail_delete=False):
        super().__init__()
        self.fail_delete = fail_delete
        self.saved = []
        self.deleted = []

    def save(self, name, content, max_length=None):
        self.saved.append((name, connection.in_atomic_block))
        return super().save(name, content, max_length)

    def delete(self, name):
        self.deleted.append((name, connection.in_atomic_block))
        if self.fail_delete:
            raise OSError("storage delete failed")
        return super().delete(name)


class PausingAttachmentStorage(RecordingAttachmentStorage):
    def __init__(self):
        super().__init__()
        self.saved_file = Event()
        self.resume = Event()

    def save(self, name, content, max_length=None):
        stored_name = super().save(name, content, max_length)
        self.saved_file.set()
        self.resume.wait(timeout=10)
        return stored_name


class SequenceHttpTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        # PRE: one configured response exists for the paginated request.
        # POST: records safe request structure and returns/raises the next response.
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            status_code=200,
            body=json.dumps(response).encode(),
            content_type="application/json",
            content_length=None,
        )


class StubAssetClient:
    def __init__(self, assets=(), exception=None, details=None):
        self.assets = tuple(assets)
        self.exception = exception
        self.details = details or {}
        self.calls = []

    def list_assets(self, *, limit=100):
        # PRE: discovery supplies a positive page size.
        # POST: records the call and returns or raises the configured result.
        self.calls.append(limit)
        if self.exception is not None:
            raise self.exception
        return self.assets

    def get_asset_detail(self, asset_uid):
        # PRE: discovery requests technical metadata for one listed asset UID.
        # POST: returns configured safe detail metadata or raises its configured error.
        detail = self.details.get(asset_uid, {})
        if isinstance(detail, Exception):
            raise detail
        return detail


class KoboContractsTests(SimpleTestCase):
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
        self.assertNotIn("_submitted_by", result.normalized_payload)
        self.assertNotIn("deviceid", result.normalized_payload)
        self.assertNotIn("download_url", result.normalized_payload)

    def test_does_not_modify_raw_payload(self):
        raw_payload = self.valid_payload()
        original_payload = deepcopy(raw_payload)

        self.normalize(raw_payload)

        self.assertEqual(raw_payload, original_payload)

    def test_trims_nucleo_code_and_allows_empty_access_notes(self):
        raw_payload = self.valid_payload(nucleo_code="  NV-010  ")

        result = self.normalize(raw_payload)

        self.assertEqual(result.normalized_payload["nucleo_code"], "NV-010")
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
                with self.assertRaises(KoboPayloadError):
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

    def test_rejects_invalid_scores_and_calculated_values(self):
        for field, value in ((field, value) for field in self.SCORE_FIELDS for value in (None, "", "0", "6", "1.5", True, "no")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(KoboPayloadError):
                    self.normalize(self.valid_payload(**{field: value}))
        for field, value in (
            ("priority_total", "11"),
            ("suggested_semaphore", "red"),
            ("final_semaphore", "blue"),
            ("final_priority", "urgent"),
            ("priority_summary", " "),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(KoboPayloadError):
                    self.normalize(self.valid_payload(**{field: value}))

    def test_accepts_matching_optional_calculations(self):
        result = self.normalize(
            self.valid_payload(priority_total="10", suggested_semaphore="gray")
        )

        self.assertEqual(result.normalized_payload["priority_total"], 10)
        self.assertEqual(result.normalized_payload["suggested_semaphore"], "gray")


class KoboApiClientTests(SimpleTestCase):
    def create_client(self, transport, **overrides):
        # PRE: transport implements get and overrides contains constructor values.
        # POST: returns a configured client that cannot perform a real HTTP request.
        values = {
            "base_url": "https://kf.example.test",
            "api_token": "top-secret-token",
            "timeout_seconds": 15,
            "transport": transport,
        }
        values.update(overrides)
        return KoboApiClient(**values)

    def test_successful_response_returns_submissions(self):
        transport = StubHttpTransport(
            body=(
                b'{"count": 1, "next": null, "previous": null, '
                b'"results": [{"_uuid": "submission-001"}]}'
            )
        )
        client = self.create_client(transport)

        submissions = client.get_submissions("asset-01", limit=25)

        self.assertEqual(submissions, [{"_uuid": "submission-001"}])
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"],
            "Token top-secret-token",
        )
        self.assertEqual(transport.calls[0]["params"], {"limit": 25})
        self.assertEqual(transport.calls[0]["timeout"], 15)

    def test_asset_detail_extracts_safe_technical_contract_metadata(self):
        client = self.create_client(
            StubHttpTransport(
                body=(
                    b'{"uid":"asset-1","content":{"settings":'
                    b'{"id_string":"ficha_1_identificacion_territorial_depurada",'
                    b'"version":"2026-07-12-depurada"}},"url":"private"}'
                )
            )
        )

        detail = client.get_asset_detail("asset-1")

        self.assertEqual(detail["id_string"], FICHA_01_FORM_ID)
        self.assertEqual(detail["version"], FICHA_01_VERSION)

    def test_asset_detail_allows_missing_version_without_guessing(self):
        client = self.create_client(
            StubHttpTransport(
                body=b'{"id_string":"ficha_10_microproyecto_priorizado_depurada"}'
            )
        )

        self.assertEqual(
            client.get_asset_detail("asset-10"),
            {"id_string": FICHA_10_FORM_ID, "version": None},
        )

    def test_missing_or_non_list_results_uses_payload_error(self):
        bodies = (
            b'{"count": 0, "next": null, "previous": null}',
            b'{"count": 1, "next": null, "previous": null, "results": {}}',
        )

        for body in bodies:
            with self.subTest(body=body):
                client = self.create_client(StubHttpTransport(body=body))
                with self.assertRaises(KoboPayloadError):
                    client.get_submissions("asset-01")

    def test_non_object_result_uses_payload_error(self):
        client = self.create_client(
            StubHttpTransport(
                body=(
                    b'{"count": 1, "next": null, "previous": null, '
                    b'"results": ["invalid"]}'
                )
            )
        )

        with self.assertRaises(KoboPayloadError):
            client.get_submissions("asset-01")

    def test_invalid_v2_envelope_metadata_uses_payload_error(self):
        bodies = (
            b'{"count": -1, "next": null, "previous": null, "results": []}',
            b'{"count": 0, "next": 7, "previous": null, "results": []}',
            b'{"count": 0, "next": null, "previous": [], "results": []}',
        )

        for body in bodies:
            with self.subTest(body=body):
                client = self.create_client(StubHttpTransport(body=body))
                with self.assertRaises(KoboPayloadError):
                    client.get_submissions("asset-01")

    def test_missing_configuration_fails_before_transport(self):
        transport = StubHttpTransport()

        for override in ({"base_url": ""}, {"api_token": ""}):
            with self.subTest(override=override):
                with self.assertRaises(KoboConfigurationError):
                    self.create_client(transport, **override)

        self.assertEqual(transport.calls, [])

    def test_invalid_request_arguments_fail_before_transport(self):
        transport = StubHttpTransport()
        client = self.create_client(transport)

        for asset_uid, limit in (("", 100), ("asset-01", 0)):
            with self.subTest(asset_uid=asset_uid, limit=limit):
                with self.assertRaises(KoboConfigurationError):
                    client.get_submissions(asset_uid, limit=limit)

        self.assertEqual(transport.calls, [])

    def test_authentication_failure_uses_specialized_error(self):
        client = self.create_client(StubHttpTransport(status_code=401))

        with self.assertRaises(KoboAuthenticationError):
            client.get_submissions("asset-01")

    def test_server_failure_uses_integration_error(self):
        client = self.create_client(StubHttpTransport(status_code=500))

        with self.assertRaises(KoboIntegrationError):
            client.get_submissions("asset-01")

    def test_network_failure_uses_integration_error(self):
        transport = StubHttpTransport(exception=OSError("connection failed"))
        client = self.create_client(transport)

        with self.assertRaises(KoboIntegrationError):
            client.get_submissions("asset-01")

    def test_invalid_json_uses_payload_error(self):
        client = self.create_client(StubHttpTransport(body=b"not-json"))

        with self.assertRaises(KoboPayloadError):
            client.get_submissions("asset-01")

    def test_token_does_not_appear_in_exceptions(self):
        token = "token-that-must-stay-secret"
        scenarios = (
            StubHttpTransport(status_code=403),
            StubHttpTransport(status_code=500),
            StubHttpTransport(exception=OSError(token)),
        )

        for transport in scenarios:
            with self.subTest(transport=transport):
                client = self.create_client(transport, api_token=token)
                with self.assertRaises(KoboIntegrationError) as context:
                    client.get_submissions("asset-01")
                self.assertNotIn(token, str(context.exception))

    def test_downloads_valid_jpeg_and_png_content(self):
        scenarios = (
            (b"\xff\xd8\xffjpeg", "image/jpeg"),
            (b"\x89PNG\r\n\x1a\npng", "image/png"),
        )

        for content, content_type in scenarios:
            with self.subTest(content_type=content_type):
                transport = StubHttpTransport(
                    body=content,
                    content_type=content_type,
                    content_length=len(content),
                )
                client = self.create_client(transport)
                downloaded = client.download_attachment(
                    "https://kf.example.test/api/attachment/1"
                )
                self.assertEqual(downloaded.content, content)
                self.assertEqual(downloaded.content_type, content_type)
                self.assertEqual(downloaded.content_length, len(content))

    def test_download_rejects_http_and_external_hosts_before_transport(self):
        transport = StubHttpTransport(body=b"content")
        client = self.create_client(transport)
        urls = (
            "http://kf.example.test/api/attachment/1",
            "https://external.example.test/api/attachment/1",
        )

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(KoboAttachmentError):
                    client.download_attachment(url)
        self.assertEqual(transport.calls, [])

    def test_download_errors_do_not_expose_token_or_full_url(self):
        token = "download-secret-token"
        full_url = "https://kf.example.test/private/sensitive/attachment.jpg"
        client = self.create_client(
            StubHttpTransport(exception=OSError(f"failed {full_url} {token}")),
            api_token=token,
        )

        with self.assertRaises(KoboIntegrationError) as context:
            client.download_attachment(full_url)

        self.assertNotIn(token, str(context.exception))
        self.assertNotIn(full_url, str(context.exception))

    def asset_result(self, uid="asset-1", **overrides):
        # PRE: uid identifies a simulated remote Kobo asset.
        # POST: returns API metadata containing safe and intentionally unsafe fields.
        asset = {
            "uid": uid,
            "name": f"Asset {uid}",
            "asset_type": "survey",
            "deployment_status": "deployed",
            "date_created": "2026-07-10T10:00:00Z",
            "date_modified": "2026-07-11T10:00:00+00:00",
            "owner": {"username": "owner-user", "permissions": ["secret"]},
            "permissions": ["secret"],
            "url": "https://signed.example.test/secret",
            "submissions": [{"sensitive": True}],
        }
        asset.update(overrides)
        return asset

    def asset_page(self, results, *, next_page=None, previous=None):
        # PRE: results and links represent one simulated API v2 page.
        # POST: returns a complete asset pagination envelope.
        return {
            "count": len(results),
            "next": next_page,
            "previous": previous,
            "results": results,
        }

    def test_list_assets_returns_safe_single_page(self):
        transport = SequenceHttpTransport(
            [self.asset_page([self.asset_result()])]
        )
        client = self.create_client(transport)

        assets = client.list_assets(limit=25)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].asset_uid, "asset-1")
        self.assertEqual(assets[0].owner_username, "owner-user")
        self.assertIsNotNone(assets[0].created_at.utcoffset())
        self.assertEqual(
            set(assets[0].safe_metadata),
            {
                "uid",
                "name",
                "asset_type",
                "deployment_status",
                "date_created",
                "date_modified",
            },
        )
        self.assertNotIn("permissions", assets[0].safe_metadata)
        self.assertEqual(transport.calls[0]["params"], {"limit": 25})

    def test_list_assets_follows_multiple_pages(self):
        next_page = "https://kf.example.test/api/v2/assets/?page=2"
        transport = SequenceHttpTransport(
            [
                self.asset_page([self.asset_result("asset-1")], next_page=next_page),
                self.asset_page(
                    [self.asset_result("asset-2")],
                    previous="https://kf.example.test/api/v2/assets/?page=1",
                ),
            ]
        )
        client = self.create_client(transport)

        assets = client.list_assets()

        self.assertEqual(tuple(asset.asset_uid for asset in assets), ("asset-1", "asset-2"))
        self.assertEqual(transport.calls[1]["url"], next_page)
        self.assertEqual(transport.calls[1]["params"], {})

    def test_list_assets_rejects_external_or_wrong_path_next(self):
        invalid_urls = (
            "https://external.example.test/api/v2/assets/?page=2",
            "https://kf.example.test/api/v2/users/?page=2",
        )

        for invalid_url in invalid_urls:
            with self.subTest(invalid_url=invalid_url):
                client = self.create_client(
                    SequenceHttpTransport(
                        [self.asset_page([], next_page=invalid_url)]
                    )
                )
                with self.assertRaises(KoboPayloadError):
                    client.list_assets()

    def test_list_assets_detects_cycle(self):
        repeated_url = "https://kf.example.test/api/v2/assets/?page=2"
        client = self.create_client(
            SequenceHttpTransport(
                [
                    self.asset_page([], next_page=repeated_url),
                    self.asset_page([], next_page=repeated_url),
                ]
            )
        )

        with self.assertRaisesMessage(KoboPayloadError, "cycle"):
            client.list_assets()

    def test_list_assets_respects_maximum_pages(self):
        client = self.create_client(
            SequenceHttpTransport(
                [
                    self.asset_page(
                        [],
                        next_page="https://kf.example.test/api/v2/assets/?page=2",
                    )
                ]
            ),
            max_asset_pages=1,
        )

        with self.assertRaisesMessage(KoboPayloadError, "maximum"):
            client.list_assets()

    def test_list_assets_rejects_missing_uid_and_invalid_envelope(self):
        pages = (
            self.asset_page([{"name": "No uid"}]),
            {"count": -1, "next": None, "previous": None, "results": []},
            {"count": 0, "next": None, "previous": None, "results": {}},
            {
                "count": 0,
                "next": None,
                "previous": "http://kf.example.test/api/v2/assets/",
                "results": [],
            },
        )

        for page in pages:
            with self.subTest(page=page):
                client = self.create_client(SequenceHttpTransport([page]))
                with self.assertRaises(KoboPayloadError):
                    client.list_assets()

    def test_list_asset_error_does_not_expose_token(self):
        token = "asset-discovery-secret-token"
        client = self.create_client(
            SequenceHttpTransport([OSError(token)]),
            api_token=token,
        )

        with self.assertRaises(KoboIntegrationError) as context:
            client.list_assets()

        self.assertNotIn(token, str(context.exception))


class KoboStagingModelsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha territorial",
            version="20260710",
        )

    def create_submission(self, external_id="submission-001", **overrides):
        # PRE: form_definition exists and overrides contains valid model fields.
        # POST: a persisted submission with a non-null raw payload is returned.
        values = {
            "form_definition": self.form_definition,
            "external_id": external_id,
            "raw_payload": {"_uuid": external_id},
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def test_form_id_and_version_cannot_be_duplicated(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboFormDefinition.objects.create(
                form_id=self.form_definition.form_id,
                title="Duplicate",
                version=self.form_definition.version,
            )

    def test_external_submission_cannot_be_duplicated_per_form(self):
        self.create_submission()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_submission()

    def test_raw_payload_is_required(self):
        submission = KoboSubmission(
            form_definition=self.form_definition,
            external_id="submission-without-payload",
        )

        with self.assertRaises(ValidationError) as context:
            submission.full_clean()

        self.assertIn("raw_payload", context.exception.message_dict)

    def test_attachment_uses_safe_defaults(self):
        attachment = KoboAttachment(
            submission=self.create_submission(),
            field_name="temple_photo",
        )

        self.assertEqual(
            attachment.privacy_level,
            KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
        )
        self.assertEqual(attachment.status, KoboAttachment.Status.PENDING)

    def test_signature_cannot_be_a_public_candidate(self):
        attachment = KoboAttachment(
            submission=self.create_submission(),
            field_name="beneficiary_signature",
            privacy_level=KoboAttachment.PrivacyLevel.PUBLIC_CANDIDATE,
        )

        with self.assertRaises(ValidationError) as context:
            attachment.full_clean()

        self.assertIn("privacy_level", context.exception.message_dict)

    def test_imported_submission_requires_processed_at(self):
        submission = KoboSubmission(
            form_definition=self.form_definition,
            external_id="imported-without-date",
            raw_payload={"_uuid": "imported-without-date"},
            normalized_payload={"parish": "parroquia_1"},
            status=KoboSubmission.Status.IMPORTED,
        )

        with self.assertRaises(ValidationError) as context:
            submission.full_clean()

        self.assertIn("processed_at", context.exception.message_dict)

        submission.processed_at = django_timezone.now()
        submission.full_clean()

    def test_string_representations_include_identifying_data(self):
        submission = self.create_submission()
        attachment = KoboAttachment(
            submission=submission,
            field_name="temple_photo",
            original_filename="temple.jpg",
        )

        self.assertIn(self.form_definition.form_id, str(self.form_definition))
        self.assertIn(submission.external_id, str(submission))
        self.assertIn("temple.jpg", str(attachment))


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
        with self.assertRaises(KoboPayloadError):
            get_registered_form("unknown_form", "20260710")

    def test_unknown_version_raises_payload_error(self):
        with self.assertRaises(KoboPayloadError):
            get_registered_form(FICHA_01_FORM_ID, "unknown_version")

    def test_normalizer_names_follow_numbered_pattern(self):
        pattern = re.compile(r"^normalize_ficha_\d{2}$")

        for registered_form in list_registered_forms():
            with self.subTest(form_id=registered_form.form_id):
                self.assertRegex(registered_form.normalizer_name, pattern)


class KoboFormSynchronizationTests(TestCase):
    def test_sync_creates_three_form_definitions(self):
        synchronized_count = sync_registered_forms()

        self.assertEqual(synchronized_count, 3)
        self.assertEqual(KoboFormDefinition.objects.count(), 3)

    def test_sync_twice_does_not_duplicate_definitions(self):
        sync_registered_forms()
        sync_registered_forms()

        self.assertEqual(KoboFormDefinition.objects.count(), 3)

    def test_sync_preserves_historical_definition(self):
        KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 histórica",
            version="20260710",
        )

        sync_registered_forms()

        historical = KoboFormDefinition.objects.get(
            form_id="ficha_01_territorio", version="20260710"
        )
        self.assertFalse(historical.is_active)
        self.assertTrue(
            KoboFormDefinition.objects.filter(
                form_id=FICHA_01_FORM_ID, version=FICHA_01_VERSION
            ).exists()
        )
        self.assertTrue(
            KoboFormDefinition.objects.filter(
                form_id=FICHA_10_FORM_ID, version=FICHA_10_VERSION
            ).exists()
        )
        self.assertTrue(
            KoboFormDefinition.objects.filter(
                form_id=FICHA_11_FORM_ID, version=FICHA_11_VERSION
            ).exists()
        )

    def test_register_command_is_idempotent(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command("register_kobo_forms", stdout=first_output)
        call_command("register_kobo_forms", stdout=second_output)

        self.assertEqual(KoboFormDefinition.objects.count(), 3)
        self.assertIn("3", first_output.getvalue())
        self.assertIn("3", second_output.getvalue())

    def test_sync_deactivates_unsupported_definition_without_deleting_it(self):
        historical = KoboFormDefinition.objects.create(
            form_id="ficha_02_capacidad_parroquial",
            title="Ficha 02 histórica",
            version="20260710",
        )

        sync_registered_forms()
        historical.refresh_from_db()

        self.assertFalse(historical.is_active)


@override_settings(KOBO_FICHA_01_ASSET_UID="ficha-01-asset")
class KoboSubmissionSynchronizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
    def valid_payload(self, external_id="submission-001", **overrides):
        # PRE: external_id identifies the simulated Kobo submission.
        # POST: returns a valid Ficha 1 API payload with requested overrides.
        payload = {
            "_uuid": external_id,
            "_id": 9876,
            "_xform_id_string": "ficha-01-asset",
            "version": FICHA_01_VERSION,
            "meta/instanceID": f"uuid:{external_id}",
            "contact_phone": "+58-sensitive-phone",
            "gps_coordinates": "sensitive-coordinates",
        }
        payload.update(overrides)
        return payload

    def test_receive_uses_uuid_and_preserves_raw_payload(self):
        raw_payload = self.valid_payload()

        submission, created = receive_api_submission(
            self.form_definition,
            raw_payload,
        )

        self.assertTrue(created)
        self.assertEqual(submission.external_id, raw_payload["_uuid"])
        self.assertNotEqual(submission.external_id, str(raw_payload["_id"]))
        self.assertEqual(submission.raw_payload, raw_payload)
        self.assertEqual(submission.status, KoboSubmission.Status.RECEIVED)

    def test_second_sync_does_not_duplicate_submission(self):
        client = StubKoboClient([self.valid_payload()])

        first_result = sync_ficha_01_submissions(client, "ficha-01-asset")
        second_result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(first_result.created_count, 1)
        self.assertEqual(second_result.existing_count, 1)
        self.assertEqual(KoboSubmission.objects.count(), 1)

    def test_missing_uuid_counts_as_failed_without_inventing_submission(self):
        payload = self.valid_payload()
        payload.pop("_uuid")
        client = StubKoboClient([payload])

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertFalse(KoboProcessingEvent.objects.exists())

    def test_mismatched_asset_counts_as_failed(self):
        client = StubKoboClient(
            [self.valid_payload(_xform_id_string="different-asset")]
        )

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_inconsistent_instance_id_counts_as_failed(self):
        client = StubKoboClient(
            [self.valid_payload(**{"meta/instanceID": "uuid:different"})]
        )

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_invalid_payload_does_not_block_valid_payload(self):
        invalid_payload = self.valid_payload("invalid")
        invalid_payload.pop("_uuid")
        client = StubKoboClient([invalid_payload, self.valid_payload("valid")])

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.fetched_count, 2)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertTrue(KoboSubmission.objects.filter(external_id="valid").exists())

    def test_dry_run_does_not_persist_submissions_or_events(self):
        invalid_payload = self.valid_payload("invalid", _xform_id_string="wrong")
        client = StubKoboClient([self.valid_payload("valid"), invalid_payload])

        result = sync_ficha_01_submissions(
            client,
            "ficha-01-asset",
            dry_run=True,
        )

        self.assertEqual(result.fetched_count, 2)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertFalse(KoboProcessingEvent.objects.exists())

    def test_invalid_payload_creates_event_only_for_existing_submission(self):
        receive_api_submission(self.form_definition, self.valid_payload())
        invalid_payload = self.valid_payload(_xform_id_string="wrong")

        result = sync_ficha_01_submissions(
            StubKoboClient([invalid_payload]),
            "ficha-01-asset",
        )

        self.assertEqual(result.failed_count, 1)
        event = KoboProcessingEvent.objects.get()
        self.assertEqual(event.code, "invalid_payload")
        self.assertNotIn("sensitive", event.message)

    def test_old_definition_is_rejected(self):
        old_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 histórica",
            version="20260710",
        )

        with self.assertRaises(KoboPayloadError):
            receive_api_submission(old_definition, self.valid_payload())

    @override_settings(KOBO_FICHA_01_ASSET_UID="")
    def test_missing_configured_asset_uid_uses_configuration_error(self):
        with self.assertRaises(KoboConfigurationError):
            sync_ficha_01_submissions(StubKoboClient([]), "")


@override_settings(
    KOBO_BASE_URL="https://kf.example.test",
    KOBO_API_TOKEN="command-secret-token",
    KOBO_REQUEST_TIMEOUT_SECONDS=15,
    KOBO_FICHA_01_ASSET_UID="ficha-01-asset",
)
class KoboFicha01SyncCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )

    def test_command_queries_only_ficha_01_and_prints_safe_counts(self):
        sensitive_values = (
            "command-secret-token",
            "+58-sensitive-phone",
            "responsible-person",
            "sensitive-coordinates",
            "https://files.example.test/private.jpg",
        )
        client = StubKoboClient(
            [
                {
                    "_uuid": "command-submission",
                    "_xform_id_string": "ficha-01-asset",
                    "contact_phone": sensitive_values[1],
                    "survey_responsible": sensitive_values[2],
                    "gps_coordinates": sensitive_values[3],
                    "attachment_url": sensitive_values[4],
                }
            ]
        )
        output = StringIO()

        with patch(
            "apps.integrations.kobo.management.commands.sync_kobo_ficha_01.KoboApiClient",
            return_value=client,
        ):
            call_command("sync_kobo_ficha_01", limit=25, stdout=output)

        self.assertEqual(client.calls, [("ficha-01-asset", 25)])
        self.assertIn("fetched=1 created=1 existing=0 failed=0", output.getvalue())
        for sensitive_value in sensitive_values:
            self.assertNotIn(sensitive_value, output.getvalue())

    def test_dry_run_prints_explicit_projection_without_persisting(self):
        client = StubKoboClient(
            [
                {
                    "_uuid": "dry-run-submission",
                    "_xform_id_string": "ficha-01-asset",
                }
            ]
        )
        output = StringIO()

        with patch(
            "apps.integrations.kobo.management.commands.sync_kobo_ficha_01.KoboApiClient",
            return_value=client,
        ):
            call_command("sync_kobo_ficha_01", dry_run=True, stdout=output)

        self.assertFalse(KoboSubmission.objects.exists())
        self.assertNotIn("created=", output.getvalue())
        self.assertIn("would_create=1", output.getvalue())
        self.assertIn("would_exist=0", output.getvalue())


class KoboSubmissionProcessorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
        cls.default_timezone = ZoneInfo("America/Caracas")

    def create_submission(self, external_id="processor-submission", **overrides):
        # PRE: external_id is unique within this test and overrides are model fields.
        # POST: returns persisted retryable staging for the active Ficha 1 contract.
        raw_payload = {
            "_uuid": external_id,
            "today": "2026-07-12",
            "nucleo_code": "NV-001",
            "pastoral_zone": "catia_la_mar",
            "parish": "caraballeda",
            "community_sector": "caraballeda_tanaguarena",
            "location": "10 -66",
            "estimated_households": 10000,
            "access_difficulties": "unknown",
            "initial_priority_perception": "medium",
            "_attachments": [],
        }
        values = {
            "form_definition": self.form_definition,
            "external_id": external_id,
            "raw_payload": raw_payload,
            "status": KoboSubmission.Status.RECEIVED,
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def test_received_becomes_ready_with_normalized_staging(self):
        submission = self.create_submission()

        outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )
        submission.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.normalized_payload["nucleo_code"], "NV-001")
        self.assertEqual(submission.normalized_payload["estimated_households"], 10000)
        self.assertEqual(submission.pastoral_zone, "catia_la_mar")
        self.assertEqual(submission.parish, "caraballeda")
        self.assertEqual(
            submission.primary_community,
            "caraballeda_tanaguarena",
        )
        self.assertEqual(submission.assessment_date, date(2026, 7, 12))
        self.assertIsNotNone(submission.normalized_at)

    def test_creates_no_attachments_without_attachment_descriptors(self):
        submission = self.create_submission()

        first_outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )
        submission.status = KoboSubmission.Status.PROCESSING_FAILED
        submission.save(update_fields=("status",))
        second_outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )

        self.assertEqual(first_outcome.attachment_count, 0)
        self.assertEqual(second_outcome.attachment_count, 0)
        self.assertEqual(submission.attachments.count(), 0)
        self.assertFalse(
            submission.attachments.exclude(status=KoboAttachment.Status.PENDING).exists()
        )

    def test_invalid_payload_becomes_validation_failed_with_error_event(self):
        submission = self.create_submission()
        submission.raw_payload.pop("parish")
        submission.save(update_fields=("raw_payload",))

        outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )
        submission.refresh_from_db()

        self.assertEqual(
            submission.status,
            KoboSubmission.Status.VALIDATION_FAILED,
        )
        self.assertEqual(outcome.error_code, "invalid_payload")
        event = submission.processing_events.get()
        self.assertEqual(event.level, KoboProcessingEvent.Level.ERROR)
        self.assertEqual(event.stage, "normalization")
        self.assertNotIn("caraballeda", event.message)

    def test_unexpected_exception_becomes_safe_processing_failure(self):
        submission = self.create_submission()
        sensitive_error = "unexpected +58-000 coordinates and URL"

        with patch(
            "apps.integrations.kobo.processors.normalize_submission",
            side_effect=RuntimeError(sensitive_error),
        ):
            outcome = process_submission(
                submission,
                default_timezone=self.default_timezone,
            )
        submission.refresh_from_db()

        self.assertEqual(
            submission.status,
            KoboSubmission.Status.PROCESSING_FAILED,
        )
        self.assertEqual(outcome.error_code, "processing_error")
        self.assertNotIn(sensitive_error, outcome.error_message)
        event = submission.processing_events.get()
        self.assertEqual(event.stage, "processing")
        self.assertNotIn(sensitive_error, event.message)

    def test_success_clears_previous_errors(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.PROCESSING_FAILED,
            error_code="old_error",
            error_message="Old sensitive failure",
        )

        process_submission(submission, default_timezone=self.default_timezone)
        submission.refresh_from_db()

        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

    def test_non_processable_status_is_skipped_without_event(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )

        outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )

        self.assertFalse(outcome.processed)
        self.assertEqual(outcome.final_status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertFalse(submission.processing_events.exists())

    def test_batch_isolates_invalid_payload_from_valid_submission(self):
        invalid = self.create_submission("invalid")
        invalid.raw_payload.pop("_uuid")
        invalid.save(update_fields=("raw_payload",))
        valid = self.create_submission("valid")

        result = process_pending_submissions(
            default_timezone=self.default_timezone,
        )
        invalid.refresh_from_db()
        valid.refresh_from_db()

        self.assertEqual(result.selected_count, 2)
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(result.validation_failed_count, 1)
        self.assertEqual(invalid.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertEqual(valid.status, KoboSubmission.Status.READY_FOR_REVIEW)

    def test_batch_respects_limit(self):
        first = self.create_submission("first")
        second = self.create_submission("second")

        result = process_pending_submissions(
            limit=1,
            default_timezone=self.default_timezone,
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(result.selected_count, 1)
        self.assertEqual(result.processed_count, 1)
        statuses = {first.status, second.status}
        self.assertEqual(
            statuses,
            {
                KoboSubmission.Status.READY_FOR_REVIEW,
                KoboSubmission.Status.RECEIVED,
            },
        )

    def test_command_submission_id_processes_only_one_and_prints_safe_output(self):
        selected = self.create_submission("selected")
        untouched = self.create_submission("untouched")
        output = StringIO()

        call_command(
            "process_kobo_submissions",
            submission_id=selected.pk,
            stdout=output,
        )
        selected.refresh_from_db()
        untouched.refresh_from_db()

        self.assertEqual(selected.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(untouched.status, KoboSubmission.Status.RECEIVED)
        self.assertIn("selected=1 processed=1 ready=1", output.getvalue())
        sensitive_values = (
            "000000000",
            "PERSONA_PRUEBA",
            "RESPONSABLE_PRUEBA",
            "10.0",
            "https://example.invalid/attachment/",
        )
        for sensitive_value in sensitive_values:
            self.assertNotIn(sensitive_value, output.getvalue())

    def test_command_without_flag_does_not_download_attachments(self):
        submission = self.create_submission("without-download-flag")

        with patch(
            "apps.integrations.kobo.processors.process_pending_attachments"
        ) as download_mock:
            call_command(
                "process_kobo_submissions",
                submission_id=submission.pk,
                stdout=StringIO(),
            )

        download_mock.assert_not_called()
        self.assertEqual(submission.attachments.count(), 0)
        self.assertFalse(
            submission.attachments.exclude(status=KoboAttachment.Status.PENDING).exists()
        )

    def test_command_with_flag_downloads_created_attachments(self):
        submission = self.create_submission("with-download-flag")
        submission.raw_payload["_attachments"] = [
            {
                "question_xpath": "evidence/rear",
                "download_url": "https://kf.example.test/private/rear.jpg",
                "media_file_basename": "rear.jpg",
                "mimetype": "image/jpeg",
            },
            {
                "question_xpath": "evidence/side",
                "download_url": "https://kf.example.test/private/side.png",
                "media_file_basename": "side.png",
                "mimetype": "image/png",
            },
            {
                "question_xpath": "evidence/front",
                "download_url": "https://kf.example.test/private/front.jpg",
                "media_file_basename": "front.jpg",
                "mimetype": "image/jpeg",
            },
        ]
        submission.save(update_fields=("raw_payload",))
        client = StubAttachmentClient(
            [
                DownloadedContent(b"\xff\xd8\xffrear", "image/jpeg", 7),
                DownloadedContent(b"\x89PNG\r\n\x1a\nside", "image/png", 12),
                DownloadedContent(b"\xff\xd8\xfffront", "image/jpeg", 8),
            ]
        )
        storage = InMemoryStorage()

        with (
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.KoboApiClient",
                return_value=client,
            ),
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.default_storage",
                storage,
            ),
        ):
            call_command(
                "process_kobo_submissions",
                submission_id=submission.pk,
                download_attachments=True,
                stdout=StringIO(),
            )

        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(
            submission.attachments.filter(
                status=KoboAttachment.Status.DOWNLOADED
            ).count(),
            3,
        )


class KoboAttachmentProcessorTests(TestCase):
    JPEG_CONTENT = b"\xff\xd8\xffsafe-jpeg"
    PNG_CONTENT = b"\x89PNG\r\n\x1a\nsafe-png"

    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 - Territorio",
            version="20260710",
        )

    def setUp(self):
        self.storage = RecordingAttachmentStorage()
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="attachment-submission",
            raw_payload={"_uuid": "attachment-submission"},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )

    def create_attachment(self, **overrides):
        # PRE: overrides contains valid KoboAttachment model fields.
        # POST: returns a persisted pending JPEG descriptor for this submission.
        values = {
            "submission": self.submission,
            "field_name": "territorial_evidence/temple_photo",
            "external_id": "attachment-uuid",
            "source_url": "https://kf.example.test/api/attachment/1",
            "original_filename": "../../remote/private/photo.jpg",
            "content_type": "image/jpeg",
            "privacy_level": KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            "status": KoboAttachment.Status.PENDING,
        }
        values.update(overrides)
        return KoboAttachment.objects.create(**values)

    def process(self, attachment, outcome, *, max_bytes=1024, storage=None):
        # PRE: attachment is persisted and outcome configures a fake download.
        # POST: runs storage processing without a real network request.
        return download_and_store_attachment(
            attachment,
            client=StubAttachmentClient([outcome]),
            storage=storage or self.storage,
            max_bytes=max_bytes,
        )

    def successful_download(self):
        return DownloadedContent(
            self.JPEG_CONTENT,
            "image/jpeg; charset=binary",
            len(self.JPEG_CONTENT),
        )

    def test_rejects_disallowed_mime_type(self):
        attachment = self.create_attachment(content_type="application/pdf")

        outcome = self.process(
            attachment,
            DownloadedContent(b"%PDF", "application/pdf", 4),
        )
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.INVALID)
        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)

    def test_rejects_false_binary_signature(self):
        attachment = self.create_attachment()

        self.process(
            attachment,
            DownloadedContent(b"not-a-jpeg", "image/jpeg", 10),
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)

    def test_rejects_content_over_size_limit(self):
        attachment = self.create_attachment()

        self.process(
            attachment,
            DownloadedContent(self.JPEG_CONTENT, "image/jpeg", len(self.JPEG_CONTENT)),
            max_bytes=3,
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)

    def test_builds_stable_safe_filename_without_remote_path(self):
        attachment = self.create_attachment(external_id="../../unsafe/id")

        filename = build_safe_filename(attachment, "jpg")

        self.assertNotIn("/", filename)
        self.assertNotIn("..", filename)
        self.assertNotIn("remote", filename)
        self.assertTrue(filename.endswith(".jpg"))

    def test_pending_is_claimed_and_processed(self):
        attachment = self.create_attachment()

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(outcome.previous_status, KoboAttachment.Status.PENDING)
        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(attachment.file.name)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)
        self.assertEqual(attachment.error_message, "")

    def test_failed_attachment_can_be_retried(self):
        attachment = self.create_attachment(
            status=KoboAttachment.Status.FAILED,
            error_message="Attachment download or storage failed.",
        )

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(outcome.previous_status, KoboAttachment.Status.FAILED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.error_message, "")

    def test_downloaded_and_invalid_are_skipped(self):
        downloaded = self.create_attachment(
            external_id="already-downloaded",
            status=KoboAttachment.Status.DOWNLOADED,
        )
        invalid = self.create_attachment(
            external_id="already-invalid",
            status=KoboAttachment.Status.INVALID,
            source_url="https://kf.example.test/api/attachment/invalid",
        )
        client = StubAttachmentClient([])

        downloaded_outcome = download_and_store_attachment(
            downloaded,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        invalid_outcome = download_and_store_attachment(
            invalid,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )

        self.assertFalse(downloaded_outcome.processed)
        self.assertFalse(invalid_outcome.processed)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.storage.saved, [])

    def test_active_processing_is_skipped_without_client_or_storage(self):
        attachment = self.create_attachment(
            status=KoboAttachment.Status.PROCESSING,
            processing_token=uuid.uuid4(),
            processing_started_at=django_timezone.now(),
        )
        client = StubAttachmentClient([self.successful_download()])

        outcome = download_and_store_attachment(
            attachment,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        attachment.refresh_from_db()

        self.assertFalse(outcome.processed)
        self.assertEqual(attachment.status, KoboAttachment.Status.PROCESSING)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.storage.saved, [])

    @override_settings(KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS=60)
    def test_expired_processing_can_be_recovered(self):
        attachment = self.create_attachment(
            status=KoboAttachment.Status.PROCESSING,
            processing_token=uuid.uuid4(),
            processing_started_at=django_timezone.now() - timedelta(seconds=120),
        )

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)

    def test_download_and_storage_complete_successfully(self):
        attachment = self.create_attachment()
        client = StubAttachmentClient([self.successful_download()])

        download_and_store_attachment(
            attachment,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(self.storage.saved), 1)

    def test_success_clears_processing_token_and_timestamp(self):
        attachment = self.create_attachment()

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(attachment.file.name)
        self.assertTrue(self.storage.exists(attachment.file.name))
        self.assertEqual(attachment.size_bytes, len(self.JPEG_CONTENT))
        self.assertEqual(attachment.content_type, "image/jpeg")
        self.assertEqual(attachment.error_message, "")
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)

    def test_success_stores_file_and_marks_downloaded(self):
        attachment = self.create_attachment()

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(attachment.file.name)
        self.assertTrue(self.storage.exists(attachment.file.name))
        self.assertEqual(attachment.size_bytes, len(self.JPEG_CONTENT))
        self.assertEqual(attachment.content_type, "image/jpeg")
        self.assertEqual(attachment.error_message, "")

    def test_network_failure_marks_attachment_failed_safely(self):
        attachment = self.create_attachment()
        sensitive_url = attachment.source_url

        outcome = self.process(
            attachment,
            KoboIntegrationError(f"network failed for {sensitive_url}"),
        )
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.FAILED)
        self.assertEqual(attachment.status, KoboAttachment.Status.FAILED)
        self.assertFalse(attachment.file)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)
        self.assertNotIn(sensitive_url, attachment.error_message)
        self.assertEqual(self.storage.saved, [])

    def test_invalid_content_marks_invalid_and_clears_processing(self):
        attachment = self.create_attachment()

        outcome = self.process(
            attachment,
            DownloadedContent(b"not-a-jpeg", "image/jpeg", 10),
        )
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.INVALID)
        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)
        self.assertEqual(self.storage.saved, [])

    def test_storage_success_then_db_failure_compensates_new_file(self):
        attachment = self.create_attachment()
        client = StubAttachmentClient([self.successful_download()])

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=RuntimeError("db confirmation failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "db confirmation failed"):
                download_and_store_attachment(
                    attachment,
                    client=client,
                    storage=self.storage,
                    max_bytes=1024,
                )

        attachment.refresh_from_db()
        self.assertEqual(len(self.storage.saved), 1)
        self.assertEqual(len(self.storage.deleted), 1)
        self.assertEqual(self.storage.deleted[0][0], self.storage.saved[0][0])
        self.assertEqual(attachment.status, KoboAttachment.Status.PROCESSING)
        self.assertFalse(attachment.file)

    def test_replaced_token_compensates_stale_worker_file(self):
        attachment = self.create_attachment()
        client = StubAttachmentClient([self.successful_download()])
        winner_token = uuid.uuid4()
        from apps.integrations.kobo import attachments as attachments_module

        real_confirm = attachments_module._confirm_download_success

        def steal_then_confirm(claim, **kwargs):
            KoboAttachment.objects.filter(pk=claim.attachment_id).update(
                processing_token=winner_token,
                processing_started_at=django_timezone.now(),
                status=KoboAttachment.Status.PROCESSING,
            )
            return real_confirm(claim, **kwargs)

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=steal_then_confirm,
        ):
            outcome = download_and_store_attachment(
                attachment,
                client=client,
                storage=self.storage,
                max_bytes=1024,
            )

        attachment.refresh_from_db()
        self.assertFalse(outcome.processed)
        self.assertEqual(attachment.status, KoboAttachment.Status.PROCESSING)
        self.assertEqual(attachment.processing_token, winner_token)
        self.assertEqual(len(self.storage.saved), 1)
        self.assertEqual(len(self.storage.deleted), 1)
        self.assertEqual(self.storage.deleted[0][0], self.storage.saved[0][0])

    def test_compensation_failure_does_not_replace_original_exception(self):
        attachment = self.create_attachment()
        storage = RecordingAttachmentStorage(fail_delete=True)
        client = StubAttachmentClient([self.successful_download()])

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=RuntimeError("original db failure"),
        ):
            with self.assertRaisesMessage(RuntimeError, "original db failure"):
                download_and_store_attachment(
                    attachment,
                    client=client,
                    storage=storage,
                    max_bytes=1024,
                )

        self.assertEqual(len(storage.deleted), 1)

    def test_one_failed_attachment_does_not_block_another(self):
        first = self.create_attachment(external_id="first")
        second = self.create_attachment(
            external_id="second",
            source_url="https://kf.example.test/api/attachment/2",
        )
        client = StubAttachmentClient(
            [
                OSError("network unavailable"),
                DownloadedContent(
                    self.JPEG_CONTENT,
                    "image/jpeg",
                    len(self.JPEG_CONTENT),
                ),
            ]
        )

        result = process_pending_attachments(
            self.submission,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.downloaded, 1)
        self.assertEqual(first.status, KoboAttachment.Status.FAILED)
        self.assertEqual(second.status, KoboAttachment.Status.DOWNLOADED)

    def test_batch_skips_active_processing_without_counting_failure(self):
        active = self.create_attachment(
            external_id="active",
            status=KoboAttachment.Status.PROCESSING,
            processing_token=uuid.uuid4(),
            processing_started_at=django_timezone.now(),
        )
        pending = self.create_attachment(
            external_id="pending",
            source_url="https://kf.example.test/api/attachment/2",
        )
        client = StubAttachmentClient([self.successful_download()])

        result = process_pending_attachments(
            self.submission,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        active.refresh_from_db()
        pending.refresh_from_db()

        self.assertEqual(result.selected, 2)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.downloaded, 1)
        self.assertEqual(active.status, KoboAttachment.Status.PROCESSING)
        self.assertEqual(pending.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(len(client.calls), 1)

    def test_reprocessing_downloaded_attachment_is_skipped(self):
        attachment = self.create_attachment(status=KoboAttachment.Status.DOWNLOADED)
        client = StubAttachmentClient([])

        outcome = download_and_store_attachment(
            attachment,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )

        self.assertFalse(outcome.processed)
        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(client.calls, [])

    def test_no_duplicate_confirmed_files_or_events_on_repeat(self):
        attachment = self.create_attachment()
        first = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()
        confirmed_name = attachment.file.name
        event_count = self.submission.processing_events.count()

        second = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertEqual(attachment.file.name, confirmed_name)
        self.assertEqual(len(self.storage.saved), 1)
        self.assertEqual(self.submission.processing_events.count(), event_count)

    def test_attachment_failure_does_not_change_submission_status(self):
        attachment = self.create_attachment()

        self.process(attachment, OSError("network unavailable"))
        self.submission.refresh_from_db()

        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )

    def test_ready_submission_without_flag_does_not_process_attachments(self):
        attachment = self.create_attachment()
        output = StringIO()

        call_command(
            "process_kobo_submissions",
            submission_id=self.submission.pk,
            stdout=output,
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.PENDING)
        self.assertIn("skipped=1", output.getvalue())
        self.assertIn("attachments_selected=0", output.getvalue())

    def test_ready_submission_with_flag_downloads_without_normalizing(self):
        attachment = self.create_attachment()
        original_normalized_at = self.submission.normalized_at
        client = StubAttachmentClient(
            [
                DownloadedContent(
                    self.JPEG_CONTENT,
                    "image/jpeg",
                    len(self.JPEG_CONTENT),
                )
            ]
        )
        output = StringIO()

        with (
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.KoboApiClient",
                return_value=client,
            ),
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.default_storage",
                self.storage,
            ),
            patch(
                "apps.integrations.kobo.processors.normalize_submission"
            ) as normalize_mock,
        ):
            call_command(
                "process_kobo_submissions",
                submission_id=self.submission.pk,
                download_attachments=True,
                stdout=output,
            )
        attachment.refresh_from_db()
        self.submission.refresh_from_db()

        normalize_mock.assert_not_called()
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.assertEqual(self.submission.normalized_at, original_normalized_at)
        self.assertFalse(self.submission.processing_events.exists())
        self.assertIn("skipped=1", output.getvalue())
        self.assertIn("attachments_selected=1", output.getvalue())
        self.assertIn("attachments_downloaded=1", output.getvalue())
        self.assertIn("attachments_skipped=0", output.getvalue())

    def test_downloaded_attachment_is_not_downloaded_again_by_command(self):
        attachment = self.create_attachment(status=KoboAttachment.Status.DOWNLOADED)
        client = StubAttachmentClient([])
        output = StringIO()

        with patch(
            "apps.integrations.kobo.management.commands.process_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "process_kobo_submissions",
                submission_id=self.submission.pk,
                download_attachments=True,
                stdout=output,
            )

        self.assertEqual(client.calls, [])
        self.assertIn("attachments_skipped=1", output.getvalue())
        self.assertIn("attachments_downloaded=0", output.getvalue())

    def test_batch_includes_ready_with_pending_only_when_flag_is_active(self):
        attachment = self.create_attachment()
        without_flag_output = StringIO()

        call_command("process_kobo_submissions", stdout=without_flag_output)
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.PENDING)
        self.assertIn("selected=0", without_flag_output.getvalue())

        client = StubAttachmentClient(
            [
                DownloadedContent(
                    self.JPEG_CONTENT,
                    "image/jpeg",
                    len(self.JPEG_CONTENT),
                )
            ]
        )
        with (
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.KoboApiClient",
                return_value=client,
            ),
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.default_storage",
                self.storage,
            ),
        ):
            with_flag_output = StringIO()
            call_command(
                "process_kobo_submissions",
                download_attachments=True,
                stdout=with_flag_output,
            )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertIn("selected=1", with_flag_output.getvalue())
        self.assertIn("skipped=1", with_flag_output.getvalue())
        self.assertIn("attachments_downloaded=1", with_flag_output.getvalue())


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking")
@override_settings(KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS=60)
class KoboAttachmentConcurrencyTests(TransactionTestCase):
    JPEG_CONTENT = b"\xff\xd8\xffsafe-jpeg"

    def setUp(self):
        self.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio_concurrent",
            title="Ficha concurrente",
            version="20260710",
        )
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="attachment-concurrent",
            raw_payload={"_uuid": "attachment-concurrent"},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.attachment = KoboAttachment.objects.create(
            submission=self.submission,
            field_name="territorial_evidence/temple_photo",
            external_id="concurrent-attachment",
            source_url="https://kf.example.test/api/attachment/concurrent",
            original_filename="photo.jpg",
            content_type="image/jpeg",
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.PENDING,
        )

    def successful_download(self):
        return DownloadedContent(
            self.JPEG_CONTENT,
            "image/jpeg",
            len(self.JPEG_CONTENT),
        )

    def create_pending_attachment(self, *, external_id="boundary-attachment"):
        return KoboAttachment.objects.create(
            submission=self.submission,
            field_name="territorial_evidence/temple_photo",
            external_id=external_id,
            source_url=f"https://kf.example.test/api/attachment/{external_id}",
            original_filename="photo.jpg",
            content_type="image/jpeg",
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.PENDING,
        )

    def run_in_thread(self, operation):
        results = Queue()

        def run():
            close_old_connections()
            try:
                results.put(("ok", operation()))
            except BaseException as exc:
                results.put(("error", exc))
            finally:
                connections.close_all()

        thread = Thread(target=run)
        thread.start()
        return thread, results

    def test_download_and_storage_happen_outside_atomic_block(self):
        attachment = self.create_pending_attachment()
        storage = RecordingAttachmentStorage()
        client = StubAttachmentClient([self.successful_download()])

        download_and_store_attachment(
            attachment,
            client=client,
            storage=storage,
            max_bytes=1024,
        )

        self.assertEqual(client.in_atomic_flags, [False])
        self.assertEqual(storage.saved[0][1], False)
        self.assertEqual(storage.deleted, [])

    def test_storage_success_then_db_failure_compensates_outside_atomic(self):
        attachment = self.create_pending_attachment(external_id="compensate-boundary")
        storage = RecordingAttachmentStorage()
        client = StubAttachmentClient([self.successful_download()])

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=RuntimeError("db confirmation failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "db confirmation failed"):
                download_and_store_attachment(
                    attachment,
                    client=client,
                    storage=storage,
                    max_bytes=1024,
                )

        self.assertEqual(storage.saved[0][1], False)
        self.assertEqual(storage.deleted[0][1], False)
        self.assertEqual(storage.deleted[0][0], storage.saved[0][0])

    def test_two_workers_only_one_claims_downloads_and_stores(self):
        barrier = Barrier(2)
        storage = RecordingAttachmentStorage()
        client = StubAttachmentClient([self.successful_download()])
        outcomes = Queue()

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                outcome = download_and_store_attachment(
                    KoboAttachment.objects.get(pk=self.attachment.pk),
                    client=client,
                    storage=storage,
                    max_bytes=1024,
                )
                outcomes.put(("ok", outcome))
            except BaseException as exc:
                outcomes.put(("error", exc))
            finally:
                connections.close_all()

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        results = [outcomes.get_nowait() for _ in threads]
        self.assertTrue(all(kind == "ok" for kind, _ in results))
        processed = [outcome for _, outcome in results if outcome.processed]
        skipped = [outcome for _, outcome in results if not outcome.processed]
        self.attachment.refresh_from_db()

        self.assertEqual(len(processed), 1)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(storage.saved), 1)
        self.assertEqual(self.attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(self.attachment.file.name)
        self.assertIsNone(self.attachment.processing_token)
        self.assertFalse(
            any(isinstance(payload, IntegrityError) for kind, payload in results)
        )

    def test_stale_worker_does_not_overwrite_recovered_claim(self):
        storage = PausingAttachmentStorage()
        client_a = StubAttachmentClient([self.successful_download()])
        client_b = StubAttachmentClient([self.successful_download()])

        thread, results = self.run_in_thread(
            lambda: download_and_store_attachment(
                KoboAttachment.objects.get(pk=self.attachment.pk),
                client=client_a,
                storage=storage,
                max_bytes=1024,
            )
        )
        self.assertTrue(storage.saved_file.wait(timeout=10))
        stale_name = storage.saved[0][0]

        KoboAttachment.objects.filter(pk=self.attachment.pk).update(
            processing_started_at=django_timezone.now() - timedelta(seconds=120),
        )
        winner_storage = RecordingAttachmentStorage()
        winner = download_and_store_attachment(
            KoboAttachment.objects.get(pk=self.attachment.pk),
            client=client_b,
            storage=winner_storage,
            max_bytes=1024,
        )
        storage.resume.set()
        thread.join(timeout=15)
        kind, payload = results.get_nowait()

        self.attachment.refresh_from_db()
        self.assertEqual(kind, "ok")
        self.assertFalse(payload.processed)
        self.assertTrue(winner.processed)
        self.assertEqual(self.attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(self.attachment.file.name, winner_storage.saved[0][0])
        self.assertNotEqual(self.attachment.file.name, stale_name)
        self.assertIn(stale_name, [name for name, _ in storage.deleted])
        self.assertEqual(len(client_a.calls), 1)
        self.assertEqual(len(client_b.calls), 1)


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
            form_id="ficha_01_territorio",
            title="Ficha 01 - Territorio",
            version="20260710",
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
            },
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            pastoral_zone="zone-one",
            parish="parish-one",
            primary_community="community-one",
            assessment_date=date(2026, 7, 11),
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

    def test_detail_separates_sensitive_data(self):
        self.client.force_login(self.viewer)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "Datos internos sensibles")
        self.assertContains(response, "Sensitive Delegate")
        self.assertContains(response, "Sensitive Informant Role")
        self.assertContains(response, "+58-secret-phone")
        self.assertContains(response, "internal-submitter")
        self.assertContains(response, "private-device-id")
        self.assertContains(response, "NV-001")

    def test_raw_payload_requires_existing_elevated_permission(self):
        self.client.force_login(self.viewer)
        viewer_response = self.client.get(self.detail_url)

        self.assertNotContains(viewer_response, "Raw payload")
        self.assertNotContains(viewer_response, "raw-secret-marker")

        self.client.force_login(self.reviewer)
        reviewer_response = self.client.get(self.detail_url)

        self.assertContains(reviewer_response, "Raw payload")
        self.assertContains(reviewer_response, "raw-secret-marker")

    def test_approval_changes_status_and_creates_event(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            self.review_url,
            {
                "decision": KoboSubmission.Status.APPROVED_FOR_IMPORT,
                "reason": "",
            },
        )
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        event = self.submission.processing_events.get()
        self.assertEqual(event.stage, "review")
        self.assertEqual(event.code, KoboSubmission.Status.APPROVED_FOR_IMPORT)

    def test_rejection_requires_reason(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            self.review_url,
            {"decision": KoboSubmission.Status.REJECTED, "reason": "   "},
        )
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "La razón es obligatoria", status_code=400)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.assertFalse(self.submission.processing_events.exists())

    def test_submission_cannot_be_reviewed_twice(self):
        self.client.force_login(self.reviewer)
        self.client.post(
            self.review_url,
            {
                "decision": KoboSubmission.Status.APPROVED_FOR_IMPORT,
                "reason": "",
            },
        )

        second_response = self.client.post(
            self.review_url,
            {"decision": KoboSubmission.Status.REJECTED, "reason": "Second"},
        )

        self.assertEqual(second_response.status_code, 400)
        self.assertEqual(self.submission.processing_events.count(), 1)

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


class KoboProjectAssociationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(
            username="association-reviewer",
            password="test-password",
        )
        cls.viewer = user_model.objects.create_user(
            username="association-viewer",
            password="test-password",
        )
        view_permission = Permission.objects.get(codename="view_kobosubmission")
        change_permission = Permission.objects.get(codename="change_kobosubmission")
        cls.reviewer.user_permissions.add(view_permission, change_permission)
        cls.viewer.user_permissions.add(view_permission)
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )

    def setUp(self):
        self.project = Project.objects.create(
            code="PRJ-ASSOCIATION",
            name="Configured exact project",
            status=Project.Status.ACTIVE,
        )
        self.other_project = Project.objects.create(
            code="PRJ-BROWSER-CHOICE",
            name="Browser supplied project",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="exact-kobo-asset",
            name="Territorial asset",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="catia_la_mar",
        )
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="association-submission",
            raw_payload={
                "_uuid": "association-submission",
                "_xform_id_string": self.asset.asset_uid,
                "contact_phone": "+58-secret-phone",
            },
            normalized_payload={"official_parish_name": "Normalized parish"},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            pastoral_zone="catia_la_mar",
            error_code="old_error",
            error_message="old message",
        )

    def associate(self):
        # PRE: submission and reviewer belong to the current association fixture.
        # POST: delegates to the exact binding association service.
        return associate_submission_with_project(
            self.submission,
            reviewed_by=self.reviewer,
        )

    def assert_safe_failure(self, expected_code):
        # PRE: one expected association configuration error was prepared.
        # POST: verifies safe warning persistence with no domain association.
        original_raw_payload = deepcopy(self.submission.raw_payload)
        result = self.associate()
        self.submission.refresh_from_db()

        self.assertFalse(result.associated)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.assertIsNone(self.submission.asset_id)
        self.assertIsNone(self.submission.project_id)
        self.assertIsNone(self.submission.imported_at)
        self.assertEqual(self.submission.error_code, expected_code)
        self.assertEqual(self.submission.raw_payload, original_raw_payload)
        event = self.submission.processing_events.get()
        self.assertEqual(event.level, KoboProcessingEvent.Level.WARNING)
        self.assertEqual(event.stage, "project_association")
        self.assertNotIn("+58-secret-phone", event.message)

    def test_associates_exact_asset_and_zone_to_configured_project(self):
        original_raw_payload = deepcopy(self.submission.raw_payload)
        original_normalized_payload = deepcopy(self.submission.normalized_payload)

        result = self.associate()
        self.submission.refresh_from_db()

        self.assertTrue(result.associated)
        self.assertEqual(self.submission.asset_id, self.asset.pk)
        self.assertEqual(self.submission.project_id, self.project.pk)
        self.assertEqual(self.submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(self.submission.imported_at)
        self.assertIsNotNone(self.submission.processed_at)
        self.assertEqual(self.submission.error_code, "")
        self.assertEqual(self.submission.error_message, "")
        self.assertEqual(self.submission.raw_payload, original_raw_payload)
        self.assertEqual(self.submission.normalized_payload, original_normalized_payload)
        event = self.submission.processing_events.get()
        self.assertEqual(event.level, KoboProcessingEvent.Level.INFO)
        self.assertEqual(event.stage, "project_association")
        self.assertEqual(event.code, "project_associated")
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_asset_is_taken_only_from_xform_id_string(self):
        decoy_asset = KoboAsset.objects.create(
            asset_uid="decoy-asset",
            name=self.asset.name,
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        KoboProjectBinding.objects.create(
            asset=decoy_asset,
            project=self.other_project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="catia_la_mar",
        )

        self.associate()
        self.submission.refresh_from_db()

        self.assertEqual(self.submission.asset_id, self.asset.pk)
        self.assertEqual(self.submission.project_id, self.project.pk)

    def test_missing_asset_keeps_submission_approved(self):
        self.submission.raw_payload["_xform_id_string"] = "missing-asset"
        self.submission.save(update_fields=("raw_payload",))

        self.assert_safe_failure("asset_not_found")

    def test_missing_asset_uid_keeps_submission_approved(self):
        self.submission.raw_payload.pop("_xform_id_string")
        self.submission.save(update_fields=("raw_payload",))

        self.assert_safe_failure("asset_uid_missing")

    def test_inactive_asset_blocks_association(self):
        self.asset.is_active = False
        self.asset.save(update_fields=("is_active",))

        self.assert_safe_failure("asset_inactive")

    def test_incompatible_asset_role_blocks_association(self):
        self.asset.form_role = KoboAsset.FormRole.PRIORITIZED_MICROPROJECT
        self.asset.save(update_fields=("form_role",))

        self.assert_safe_failure("asset_role_incompatible")

    def test_missing_binding_blocks_association(self):
        self.binding.delete()

        self.assert_safe_failure("routing_not_found")

    def test_inactive_binding_blocks_association(self):
        self.binding.is_active = False
        self.binding.save(update_fields=("is_active",))

        self.assert_safe_failure("routing_not_found")

    def test_empty_pastoral_zone_blocks_association(self):
        self.submission.pastoral_zone = ""
        self.submission.save(update_fields=("pastoral_zone",))

        self.assert_safe_failure("routing_value_invalid")

    def test_second_association_does_not_duplicate_events(self):
        first_result = self.associate()
        second_result = self.associate()

        self.assertTrue(first_result.associated)
        self.assertFalse(second_result.associated)
        self.assertEqual(self.submission.processing_events.count(), 1)

    @override_settings(KOBO_ENABLED=True)
    def test_post_requires_change_permission(self):
        self.client.force_login(self.viewer)
        url = reverse(
            "kobo:submission_associate_project",
            args=(self.submission.pk,),
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )

    @override_settings(KOBO_ENABLED=True)
    def test_get_cannot_associate(self):
        self.client.force_login(self.reviewer)
        url = reverse(
            "kobo:submission_associate_project",
            args=(self.submission.pk,),
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 405)
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )

    @override_settings(KOBO_ENABLED=True)
    def test_browser_project_id_is_ignored(self):
        self.client.force_login(self.reviewer)
        url = reverse(
            "kobo:submission_associate_project",
            args=(self.submission.pk,),
        )

        response = self.client.post(url, {"project_id": self.other_project.pk})
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.submission.project_id, self.project.pk)
        self.assertNotEqual(self.submission.project_id, self.other_project.pk)


@override_settings(KOBO_ENABLED=True)
class KoboFicha10AssociationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(
            username="ficha-10-reviewer",
            password="test-password",
        )
        cls.project = Project.objects.create(
            code="PRJ-FICHA-10",
            name="Proyecto para microproyectos",
            status=Project.Status.ACTIVE,
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 - Microproyecto priorizado (depurada)",
            version=FICHA_10_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="ficha-10-asset",
            name="Ficha de microproyectos",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )
        KoboProjectBinding.objects.create(
            asset=cls.asset,
            project=cls.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="NV-010",
        )

    def valid_payload(self):
        # PRE: Ficha 10 asset and binding are configured for NV-010.
        # POST: returns a complete raw payload whose routing uses normalized data.
        return {
            "_uuid": "ficha-10-association",
            "_xform_id_string": self.asset.asset_uid,
            "today": "2026-07-12",
            "nucleo_code": "NV-010",
            "microproject": {
                "microproject_name": "Rehabilitación del centro comunitario",
                "component": "infrastructure",
                "problem_summary": "Filtraciones persistentes.",
                "specific_objective": "Recuperar la cubierta.",
                "beneficiary_group": "youth women",
                "main_activities": "Reparar el techo.",
                "estimated_cost_range": "5000_15000",
                "implementation_urgency": "immediate",
                "technical_viability": "high",
                "expected_result": "Espacio protegido.",
            },
        }

    def test_ficha_10_associates_without_creating_operational_records(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-10-association",
            raw_payload=self.valid_payload(),
        )

        process_submission(
            submission,
            default_timezone=ZoneInfo("America/Caracas"),
        )
        submission.refresh_from_db()
        review_submission(
            submission,
            decision=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            reason="",
            reviewed_by=self.reviewer,
        )
        result = associate_submission_with_project(
            submission,
            reviewed_by=self.reviewer,
        )
        submission.refresh_from_db()

        self.assertTrue(result.associated)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(submission.project_id, self.project.pk)
        self.assertEqual(submission.normalized_payload["nucleo_code"], "NV-010")
        self.assertFalse(ProjectUpdate.objects.exists())
        self.assertEqual(Project.objects.count(), 1)

    def test_asset_configuration_rejects_the_territorial_role_for_ficha_10(self):
        discovered_asset = KoboDiscoveredAsset.objects.create(
            asset_uid="ficha-10-incompatible-role",
            name="Ficha 10 - Microproyecto priorizado (depurada)",
            last_seen_at=django_timezone.now(),
        )

        with self.assertRaises(ValidationError):
            configure_discovered_asset(
                discovered_asset,
                name="Configuración incompatible",
                form_definition=self.form_definition,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
                configured_by=self.reviewer,
            )


@override_settings(KOBO_ENABLED=True)
class KoboFicha11AssociationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(
            username="ficha-11-reviewer",
            password="test-password",
        )
        cls.project = Project.objects.create(
            code="PRJ-FICHA-11",
            name="Proyecto para priorización",
            status=Project.Status.ACTIVE,
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 - Matriz de priorización y semáforo (depurada)",
            version=FICHA_11_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="ficha-11-asset",
            name="Ficha de priorización",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )
        KoboProjectBinding.objects.create(
            asset=cls.asset,
            project=cls.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="NV-011",
        )

    def valid_payload(self, **overrides):
        # PRE: the Ficha 11 asset has an explicit active routing binding.
        # POST: returns a complete raw priority matrix payload.
        payload = {
            "_uuid": "ficha-11-association",
            "_xform_id_string": self.asset.asset_uid,
            "nucleo_code": "NV-011",
            "scoring": {
                **{field: "4" for field in KoboFicha11NormalizerTests.SCORE_FIELDS},
                "priority_total": "40",
                "suggested_semaphore": "red",
                "final_semaphore": "yellow",
                "final_priority": "high",
                "priority_summary": "Validación técnica independiente.",
            },
        }
        scoring_overrides = {
            key: value for key, value in overrides.items() if key in payload["scoring"]
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

    def test_ficha_11_processes_reviews_and_associates_without_operations_effects(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-11-association",
            raw_payload=self.valid_payload(),
        )
        process_submission(submission, default_timezone=ZoneInfo("America/Caracas"))
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        submission.raw_payload["nucleo_code"] = "RAW-MUST-NOT-ROUTE"
        submission.save(update_fields=("raw_payload",))
        review_submission(
            submission,
            decision=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            reason="",
            reviewed_by=self.reviewer,
        )

        result = associate_submission_with_project(submission, reviewed_by=self.reviewer)
        submission.refresh_from_db()

        self.assertTrue(result.associated)
        self.assertEqual(submission.project_id, self.project.pk)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(submission.normalized_payload["priority_total"], 40)
        self.assertFalse(ProjectUpdate.objects.exists())
        self.assertEqual(Project.objects.count(), 1)

    def test_manipulated_total_fails_processing_before_review(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-11-invalid-total",
            raw_payload=self.valid_payload(priority_total="39"),
        )

        process_submission(submission, default_timezone=ZoneInfo("America/Caracas"))
        submission.refresh_from_db()

        self.assertEqual(submission.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_ficha_11_rejects_a_crossed_asset_role(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-11-crossed-role",
            raw_payload={
                "_uuid": "ficha-11-crossed-role",
                "_xform_id_string": self.asset.asset_uid,
            },
            normalized_payload={"nucleo_code": "NV-011"},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.asset.form_role = KoboAsset.FormRole.TERRITORIAL_PROFILE
        self.asset.save(update_fields=("form_role",))

        result = associate_submission_with_project(submission, reviewed_by=self.reviewer)
        submission.refresh_from_db()

        self.assertFalse(result.associated)
        self.assertEqual(submission.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertEqual(submission.error_code, "asset_role_incompatible")


@override_settings(KOBO_ENABLED=True)
class KoboProjectImportedSubmissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="project-kobo-viewer",
            password="test-password",
        )
        cls.reviewer = user_model.objects.create_user(
            username="project-kobo-reviewer",
            password="test-password",
        )
        cls.unprivileged = user_model.objects.create_user(
            username="project-only-viewer",
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
        cls.unprivileged.user_permissions.add(permissions["view_project"])
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
            code="PRJ-KOBO-DETAIL",
            name="Proyecto con levantamiento",
            status=Project.Status.ACTIVE,
        )
        self.other_project = Project.objects.create(
            code="PRJ-KOBO-OTHER",
            name="Otro proyecto",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="project-detail-asset",
            name="Ficha territorial activa",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.inactive_asset = KoboAsset.objects.create(
            asset_uid="inactive-project-detail-asset",
            name="Ficha territorial inactiva",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=False,
        )
        self.microproject_asset = KoboAsset.objects.create(
            asset_uid="project-detail-microproject-asset",
            name="Ficha de microproyectos activa",
            form_definition=self.microproject_form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )
        self.prioritization_asset = KoboAsset.objects.create(
            asset_uid="project-detail-prioritization-asset",
            name="Ficha de priorización activa",
            form_definition=self.prioritization_form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )
        self.binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="NV-001",
        )
        self.imported = self.create_submission(
            "visible-imported",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.IMPORTED,
        )
        self.other_imported = self.create_submission(
            "other-project-imported",
            project=self.other_project,
            asset=self.asset,
            status=KoboSubmission.Status.IMPORTED,
        )
        self.ready = self.create_submission(
            "ready-hidden",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.approved = self.create_submission(
            "approved-hidden",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.validation_failed = self.create_submission(
            "validation-failed-hidden",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.VALIDATION_FAILED,
        )
        self.inactive_asset_submission = self.create_submission(
            "inactive-asset-hidden",
            project=self.project,
            asset=self.inactive_asset,
            status=KoboSubmission.Status.IMPORTED,
        )
        self.microproject_imported = KoboSubmission.objects.create(
            form_definition=self.microproject_form_definition,
            asset=self.microproject_asset,
            project=self.project,
            external_id="visible-microproject-imported",
            raw_payload={"_uuid": "visible-microproject-imported"},
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
            external_id="visible-prioritization-imported",
            raw_payload={"_uuid": "visible-prioritization-imported"},
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
        self.pending_attachment = KoboAttachment.objects.create(
            submission=self.imported,
            field_name="territorial_evidence/pending",
            source_url="https://kf.example.test/private/pending-never-visible",
            content_type="image/jpeg",
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.PENDING,
        )

    def create_submission(self, external_id, *, project, asset, status):
        # PRE: project, asset and status define a staging visibility scenario.
        # POST: returns a persisted Ficha 1 submission with sensitive test data.
        return KoboSubmission.objects.create(
            form_definition=self.form_definition,
            asset=asset,
            project=project,
            external_id=external_id,
            raw_payload={
                "_uuid": external_id,
                "_submitted_by": "Sensitive Submitter",
                "deviceid": "Sensitive Device",
            },
            normalized_payload={
                "nucleo_code": "NV-001",
                "communities_covered": "Comunidades visibles",
                "estimated_households": 300,
                "access_difficulties": "no",
                "access_difficulties_notes": None,
                "initial_priority_perception": "medium",
                "general_notes": "Nota visible",
                "location": {"latitude": 10.0, "longitude": -66.0},
                "parish_delegate": "Sensitive Delegate",
                "contact_phone": "+58-sensitive-phone",
                "main_informant_role": "Sensitive Informant Role",
            },
            status=status,
            pastoral_zone="catia_la_mar",
            parish="visible-parish",
            primary_community="visible-community",
            assessment_date=date(2026, 7, 11),
            imported_at=django_timezone.now() if status == KoboSubmission.Status.IMPORTED else None,
            processed_at=django_timezone.now() if status == KoboSubmission.Status.IMPORTED else None,
        )

    def test_service_returns_only_imported_exact_project_active_asset(self):
        submissions = list(
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            )
        )

        self.assertEqual(submissions, [self.imported])
        self.assertEqual(submissions[0].attachment_count, 2)
        self.assertEqual(submissions[0].downloaded_attachment_count, 1)

    def test_service_separates_imported_microprojects_by_role(self):
        submissions = list(
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            )
        )

        self.assertEqual(submissions, [self.microproject_imported])

    def test_service_separates_imported_prioritization_matrices_by_role(self):
        submissions = list(
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
            )
        )

        self.assertEqual(submissions, [self.prioritization_imported])

    def test_pending_service_and_project_detail_show_only_reviewable_submissions(self):
        submissions = list(get_project_pending_submissions(self.project))

        self.assertEqual(submissions, [self.ready])
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("project_detail", args=(self.project.pk,)))
        queue_response = self.client.get(
            reverse("kobo:project_pending_submission_list", args=(self.project.pk,))
        )

        self.assertContains(response, "Fichas Kobo pendientes de revisión")
        self.assertContains(response, "ready-hidden")
        self.assertNotContains(response, "approved-hidden")
        self.assertNotContains(response, "validation-failed-hidden")
        self.assertNotContains(response, "other-project-imported")
        self.assertNotContains(response, "Revisar")
        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, "ready-hidden")
        self.assertNotContains(queue_response, "approved-hidden")
        self.assertNotContains(queue_response, "validation-failed-hidden")
        self.assertNotContains(queue_response, "other-project-imported")

    def test_project_detail_shows_only_visible_imported_submission(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("project_detail", args=(self.project.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "operations/project_detail.html")
        self.assertContains(response, "Levantamientos de campo")
        self.assertContains(response, "visible-parish")
        self.assertNotContains(response, "other-project-imported")
        self.assertContains(response, "ready-hidden")
        self.assertNotContains(response, "approved-hidden")
        self.assertNotContains(response, "inactive-asset-hidden")
        self.assertContains(response, "Microproyectos priorizados")
        self.assertContains(response, "Techo para el centro comunitario")
        self.assertContains(response, "Matriz de priorización y semáforo")
        self.assertContains(response, self.project.code)
        for sensitive_value in (
            "Sensitive Delegate",
            "Sensitive Informant Role",
            "+58-sensitive-phone",
            "Sensitive Submitter",
            "Sensitive Device",
        ):
            self.assertNotContains(response, sensitive_value)

    def test_pending_review_is_project_scoped_and_uses_normalized_payload(self):
        review_url = reverse(
            "kobo:project_pending_submission_review",
            args=(self.project.pk, self.ready.pk),
        )
        mismatched_url = reverse(
            "kobo:project_pending_submission_review",
            args=(self.other_project.pk, self.ready.pk),
        )
        self.client.force_login(self.reviewer)

        response = self.client.get(review_url)
        mismatched_response = self.client.get(mismatched_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comunidades visibles")
        self.assertContains(response, "Identificación territorial")
        self.assertContains(response, "Pendiente de revisión")
        self.assertContains(response, "Rechazar ficha")
        self.assertNotContains(response, "Sensitive Submitter")
        self.assertNotContains(response, "raw_payload")
        self.assertEqual(mismatched_response.status_code, 404)

        self.client.force_login(self.unprivileged)
        self.assertEqual(self.client.get(review_url).status_code, 403)

    def test_rejection_action_requires_other_comment_and_is_idempotent(self):
        url = reverse(
            "kobo:project_pending_submission_reject",
            args=(self.project.pk, self.ready.pk),
        )
        self.client.force_login(self.reviewer)

        invalid_response = self.client.post(url, {"reason": "other", "comment": ""})
        self.assertEqual(invalid_response.status_code, 400)
        self.ready.refresh_from_db()
        self.assertEqual(self.ready.status, KoboSubmission.Status.READY_FOR_REVIEW)

        first_response = self.client.post(
            url,
            {"reason": "test_submission", "comment": ""},
        )
        second_response = self.client.post(
            url,
            {"reason": "test_submission", "comment": ""},
        )
        self.ready.refresh_from_db()

        self.assertRedirects(
            first_response,
            reverse("kobo:project_pending_submission_list", args=(self.project.pk,)),
        )
        self.assertRedirects(
            second_response,
            reverse("kobo:project_pending_submission_list", args=(self.project.pk,)),
        )
        self.assertEqual(self.ready.status, KoboSubmission.Status.REJECTED)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.ready.pk),
                action=AuditLog.Action.REJECTED,
                summary="Ficha Kobo rechazada.",
            ).count(),
            1,
        )

    def test_imports_each_supported_form_idempotently_and_audits_actor(self):
        microproject_pending = KoboSubmission.objects.create(
            form_definition=self.microproject_form_definition,
            asset=self.microproject_asset,
            project=self.project,
            external_id="pending-microproject",
            raw_payload={"_uuid": "pending-microproject"},
            normalized_payload=self.microproject_imported.normalized_payload,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            processed_at=django_timezone.now(),
        )
        prioritization_pending = KoboSubmission.objects.create(
            form_definition=self.prioritization_form_definition,
            asset=self.prioritization_asset,
            project=self.project,
            external_id="pending-prioritization",
            raw_payload={"_uuid": "pending-prioritization"},
            normalized_payload=self.prioritization_imported.normalized_payload,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            processed_at=django_timezone.now(),
        )

        for submission in (self.ready, microproject_pending, prioritization_pending):
            with self.subTest(submission=submission.external_id):
                result = import_kobo_submission(submission, actor=self.reviewer)
                submission.refresh_from_db()
                self.assertTrue(result.imported)
                self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
                self.assertEqual(submission.project, self.project)
                self.assertIsNotNone(submission.imported_at)
                self.assertTrue(
                    submission.processing_events.filter(
                        stage="operational_import", code="imported"
                    ).exists()
                )

        repeated = import_kobo_submission(self.ready, actor=self.reviewer)
        self.assertFalse(repeated.imported)
        self.assertTrue(repeated.already_imported)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.ready.pk),
                action=AuditLog.Action.CREATED,
                user=self.reviewer,
                summary="Ficha Kobo importada al proyecto.",
            ).count(),
            1,
        )
        self.assertNotIn(self.ready, get_project_pending_submissions(self.project))
        self.assertIn(
            microproject_pending,
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            ),
        )
        self.assertIn(
            prioritization_pending,
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
            ),
        )

    def test_operational_import_lock_query_has_no_nullable_join(self):
        with transaction.atomic():
            with CaptureQueriesContext(connection) as queries:
                locked_submission = _lock_submission_for_operational_import(
                    self.ready.pk
                )

        lock_query = next(
            query["sql"]
            for query in queries.captured_queries
            if "kobo_kobosubmission" in query["sql"].lower()
        ).upper()
        self.assertEqual(locked_submission.pk, self.ready.pk)
        self.assertNotIn(" JOIN ", lock_query)
        if connection.vendor == "postgresql":
            self.assertIn("FOR UPDATE", lock_query)

    def test_import_action_preserves_ready_submission_when_configuration_is_invalid(self):
        self.asset.is_active = False
        self.asset.save(update_fields=("is_active",))

        result = import_kobo_submission(self.ready, actor=self.reviewer)
        self.ready.refresh_from_db()

        self.assertFalse(result.imported)
        self.assertFalse(result.already_imported)
        self.assertEqual(self.ready.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIsNone(self.ready.imported_at)
        self.assertTrue(
            self.ready.processing_events.filter(
                stage="operational_import", code="import_asset_invalid"
            ).exists()
        )

    def test_import_action_requires_project_change_permission_and_transitions_submission(self):
        url = reverse(
            "kobo:project_pending_submission_import",
            args=(self.project.pk, self.ready.pk),
        )
        self.client.force_login(self.unprivileged)
        self.assertEqual(self.client.post(url).status_code, 403)

        self.client.force_login(self.reviewer)
        response = self.client.post(url)
        self.ready.refresh_from_db()

        self.assertRedirects(response, reverse("project_detail", args=(self.project.pk,)))
        self.assertEqual(self.ready.status, KoboSubmission.Status.IMPORTED)

    def test_rejection_is_auditable_idempotent_and_excluded_from_pending(self):
        original_raw = deepcopy(self.ready.raw_payload)
        original_normalized = deepcopy(self.ready.normalized_payload)

        result = reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="duplicate",
            comment="<b>Repetida</b>",
        )
        self.ready.refresh_from_db()

        self.assertTrue(result.rejected)
        self.assertEqual(self.ready.status, KoboSubmission.Status.REJECTED)
        self.assertEqual(self.ready.raw_payload, original_raw)
        self.assertEqual(self.ready.normalized_payload, original_normalized)
        rejection_event = self.ready.processing_events.get(stage="review", code="duplicate")
        self.assertEqual(rejection_event.message, "Repetida")
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.ready.pk),
                action=AuditLog.Action.REJECTED,
                user=self.reviewer,
                summary="Ficha Kobo rechazada.",
            ).count(),
            1,
        )
        self.assertNotIn(self.ready, get_project_pending_submissions(self.project))
        self.assertNotIn(
            self.ready,
            get_project_imported_submissions(self.project),
        )

        repeated = reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="duplicate",
        )
        self.assertTrue(repeated.already_rejected)
        self.assertEqual(
            self.ready.processing_events.filter(stage="review", code="duplicate").count(),
            1,
        )

    def test_rejection_validates_reason_state_and_restoration(self):
        with self.assertRaises(KoboPayloadError):
            reject_kobo_submission(
                self.ready,
                actor=self.reviewer,
                reason="other",
            )
        with self.assertRaises(KoboPayloadError):
            reject_kobo_submission(
                self.ready,
                actor=self.reviewer,
                reason="invalid",
            )
        with self.assertRaises(KoboPayloadError):
            reject_kobo_submission(
                self.imported,
                actor=self.reviewer,
                reason="duplicate",
            )

        reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="other",
            comment="Descartada por revisión.",
        )
        restored = restore_kobo_submission_to_review(self.ready, actor=self.reviewer)
        self.ready.refresh_from_db()

        self.assertTrue(restored.restored)
        self.assertEqual(self.ready.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertTrue(
            self.ready.processing_events.filter(stage="review", code="other").exists()
        )
        self.assertTrue(
            self.ready.processing_events.filter(stage="review", code="restored").exists()
        )
        with self.assertRaises(KoboPayloadError):
            restore_kobo_submission_to_review(self.imported, actor=self.reviewer)

    def test_history_shows_only_imported_and_rejected_submissions(self):
        reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="test_submission",
        )
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:project_submission_history", args=(self.project.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importada")
        self.assertContains(response, "Rechazada")
        self.assertContains(response, "Submission de prueba")
        self.assertNotContains(response, "approved-hidden")
        self.assertNotContains(response, "validation-failed-hidden")
        self.assertIn(self.ready, get_project_submission_history(self.project))

    @override_settings(KOBO_ENABLED=False)
    def test_disabled_kobo_hides_section_and_uses_legacy_project_detail(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("project_detail", args=(self.project.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "web/project_detail.html")
        self.assertNotContains(response, "Levantamientos de campo")
        self.assertEqual(response.context["kobo_submissions"], ())

    def test_project_submission_detail_requires_login_and_permission(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))

        anonymous_response = self.client.get(url)
        self.client.force_login(self.unprivileged)
        forbidden_response = self.client.get(url)

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertEqual(forbidden_response.status_code, 403)

    def test_sensitive_detail_requires_elevated_permission(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        self.client.force_login(self.viewer)
        viewer_response = self.client.get(url)

        self.assertContains(viewer_response, "NV-001")
        self.assertNotContains(viewer_response, "Datos internos sensibles")
        self.assertNotContains(viewer_response, "+58-sensitive-phone")
        self.assertNotContains(viewer_response, "Sensitive Delegate")
        self.assertNotContains(viewer_response, "Sensitive Informant Role")
        self.assertNotContains(viewer_response, "Nombre del microproyecto")

        self.client.force_login(self.reviewer)
        reviewer_response = self.client.get(url)

        self.assertContains(reviewer_response, "Datos internos sensibles")
        self.assertContains(reviewer_response, "+58-sensitive-phone")
        self.assertContains(reviewer_response, "Sensitive Delegate")
        self.assertContains(reviewer_response, "Sensitive Informant Role")
        self.assertContains(reviewer_response, "Sensitive Device")

    def test_microproject_detail_uses_human_readable_labels(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.microproject_imported.pk,))
        )

        self.assertContains(response, "Microproyecto priorizado")
        self.assertContains(response, "Nombre del microproyecto")
        self.assertNotContains(response, "Salud y atención psicosocial")
        self.assertContains(response, "Infraestructura")
        self.assertContains(response, "Inmediata")
        self.assertContains(response, "Alta")
        self.assertNotContains(response, "Hogares estimados")
        self.assertNotContains(response, "_submitted_by")

    def test_prioritization_detail_distinguishes_suggested_and_final_semaphores(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.prioritization_imported.pk,))
        )

        self.assertContains(response, "Nivel de daño físico")
        self.assertContains(response, "Puntaje total")
        self.assertContains(response, "Semáforo sugerido")
        self.assertContains(response, "Semáforo final validado")
        self.assertNotContains(response, "Nombre del microproyecto")
        self.assertNotContains(response, "Hogares estimados")
        self.assertNotContains(response, "raw_payload")

    def test_detail_hides_sources_and_non_downloaded_attachments(self):
        self.client.force_login(self.viewer)
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))

        response = self.client.get(url)

        self.assertContains(response, "kobo-visible-evidence.jpg")
        self.assertNotContains(response, self.downloaded_attachment.source_url)
        self.assertNotContains(response, self.pending_attachment.source_url)
        self.assertNotContains(response, "territorial_evidence/pending")

    def test_project_model_dashboard_and_public_portal_remain_kobo_free(self):
        self.assertFalse(
            any(
                field.name.startswith("kobo_") and not field.auto_created
                for field in Project._meta.get_fields()
            )
        )
        self.client.force_login(self.viewer)
        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertNotContains(dashboard_response, "Levantamientos de campo")

        self.client.logout()
        public_response = self.client.get(
            reverse(
                "public_portal:public_project_detail",
                args=(self.project.pk,),
            )
        )
        self.assertEqual(public_response.status_code, 200)
        self.assertNotContains(public_response, "Levantamientos de campo")
        self.assertNotContains(public_response, "visible-parish")


class KoboGenericRoutingMigrationTests(TransactionTestCase):
    migrate_from = [
        ("operations", "0015_projectupdatereviewdecision"),
        ("kobo", "0003_kobosubmission_asset_kobosubmission_imported_at_and_more"),
    ]
    migrate_to = [
        ("operations", "0015_projectupdatereviewdecision"),
        ("kobo", "0004_generic_project_binding_routing"),
    ]

    def _restore_leaf_migrations(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_leaf_migrations)

    def test_pastoral_binding_migrates_without_losing_asset_or_project(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldFormDefinition = old_apps.get_model("kobo", "KoboFormDefinition")
        OldAsset = old_apps.get_model("kobo", "KoboAsset")
        OldBinding = old_apps.get_model("kobo", "KoboProjectBinding")
        OldProject = old_apps.get_model("operations", "Project")
        form_definition = OldFormDefinition.objects.create(
            form_id="migration-ficha-01",
            title="Migration form",
            version="20260710",
        )
        asset = OldAsset.objects.create(
            asset_uid="migration-asset",
            name="Migration asset",
            form_definition=form_definition,
            form_role="territorial_profile",
        )
        project = OldProject.objects.create(
            code="PRJ-MIGRATION",
            name="Migration project",
        )
        old_binding = OldBinding.objects.create(
            asset=asset,
            project=project,
            pastoral_zone="catia_la_mar",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewBinding = new_apps.get_model("kobo", "KoboProjectBinding")
        migrated = NewBinding.objects.get(pk=old_binding.pk)

        self.assertEqual(migrated.asset_id, asset.pk)
        self.assertEqual(migrated.project_id, project.pk)
        self.assertEqual(migrated.routing_type, "field_value")
        self.assertEqual(migrated.source_field, "submission.pastoral_zone")
        self.assertEqual(migrated.source_value, "catia_la_mar")


class KoboAssetDiscoveryTests(TestCase):
    def remote_asset(self, uid="discovered-1", **overrides):
        # PRE: uid identifies one validated remote discovery projection.
        # POST: returns immutable safe metadata with requested overrides.
        values = {
            "asset_uid": uid,
            "name": f"Discovered {uid}",
            "asset_type": "survey",
            "deployment_status": "deployed",
            "owner_username": "owner-user",
            "created_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
            "modified_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
            "safe_metadata": {
                "uid": uid,
                "name": f"Discovered {uid}",
                "asset_type": "survey",
            },
        }
        values.update(overrides)
        return KoboRemoteAsset(**values)

    def test_discovery_creates_assets_without_configuring_integrations(self):
        project = Project.objects.create(
            code="PRJ-DISCOVERY",
            name="Unchanged project",
        )

        result = discover_assets(StubAssetClient([self.remote_asset()]))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.created_count, 1)
        discovered = KoboDiscoveredAsset.objects.get(asset_uid="discovered-1")
        self.assertTrue(discovered.is_available)
        self.assertEqual(discovered.metadata_snapshot["uid"], "discovered-1")
        self.assertFalse(KoboAsset.objects.exists())
        self.assertFalse(KoboProjectBinding.objects.exists())
        self.assertFalse(KoboFormDefinition.objects.exists())
        project.refresh_from_db()
        self.assertEqual(project.name, "Unchanged project")

    def test_second_discovery_is_idempotent_and_updates_last_seen(self):
        client = StubAssetClient([self.remote_asset()])
        discover_assets(client)
        discovered = KoboDiscoveredAsset.objects.get(asset_uid="discovered-1")
        old_seen_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        discovered.last_seen_at = old_seen_at
        discovered.save(update_fields=("last_seen_at",))

        result = discover_assets(client)
        discovered.refresh_from_db()

        self.assertEqual(KoboDiscoveredAsset.objects.count(), 1)
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.unchanged_count, 1)
        self.assertGreater(discovered.last_seen_at, old_seen_at)

    def test_discovery_updates_name_and_safe_metadata(self):
        discover_assets(StubAssetClient([self.remote_asset()]))
        changed = self.remote_asset(
            name="Updated safe name",
            safe_metadata={"uid": "discovered-1", "name": "Updated safe name"},
        )

        result = discover_assets(StubAssetClient([changed]))
        discovered = KoboDiscoveredAsset.objects.get(asset_uid="discovered-1")

        self.assertEqual(result.updated_count, 1)
        self.assertEqual(discovered.name, "Updated safe name")
        self.assertEqual(discovered.metadata_snapshot["name"], "Updated safe name")

    def test_complete_discovery_marks_absent_assets_unavailable(self):
        discover_assets(
            StubAssetClient(
                [self.remote_asset("present"), self.remote_asset("absent")]
            )
        )

        result = discover_assets(StubAssetClient([self.remote_asset("present")]))

        self.assertEqual(result.unavailable_count, 1)
        self.assertTrue(
            KoboDiscoveredAsset.objects.get(asset_uid="present").is_available
        )
        self.assertFalse(
            KoboDiscoveredAsset.objects.get(asset_uid="absent").is_available
        )

    def test_failed_discovery_does_not_mark_absent_assets_unavailable(self):
        discover_assets(StubAssetClient([self.remote_asset("existing")]))

        with self.assertRaises(KoboIntegrationError):
            discover_assets(
                StubAssetClient(exception=KoboIntegrationError("safe failure"))
            )

        self.assertTrue(
            KoboDiscoveredAsset.objects.get(asset_uid="existing").is_available
        )

    @override_settings(KOBO_ENABLED=False)
    def test_command_blocks_when_kobo_is_disabled(self):
        with self.assertRaises(CommandError):
            call_command("discover_kobo_assets", stdout=StringIO())

    @override_settings(
        KOBO_ENABLED=True,
        KOBO_BASE_URL="https://kf.example.test",
        KOBO_API_TOKEN="command-discovery-secret",
        KOBO_REQUEST_TIMEOUT_SECONDS=15,
    )
    def test_command_dry_run_does_not_persist_or_print_sensitive_data(self):
        remote_asset = self.remote_asset(
            name="https://private.example.test/signed",
        )
        output = StringIO()

        with patch(
            "apps.integrations.kobo.management.commands.discover_kobo_assets.KoboApiClient",
            return_value=StubAssetClient([remote_asset]),
        ):
            call_command(
                "discover_kobo_assets",
                dry_run=True,
                limit=25,
                stdout=output,
            )

        self.assertFalse(KoboDiscoveredAsset.objects.exists())
        self.assertIn("fetched=1 would_create=1 would_update=0", output.getvalue())
        self.assertNotIn("command-discovery-secret", output.getvalue())
        self.assertNotIn("https://", output.getvalue())


@override_settings(KOBO_ENABLED=True)
class KoboAssetManualConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # PRE: auth, Kobo and operations models are migrated.
        # POST: creates reusable actors and one supported active definition.
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user("kobo-config-viewer")
        cls.editor = user_model.objects.create_user("kobo-config-editor")
        permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(
                codename__in=("view_koboasset", "change_koboasset")
            )
        }
        cls.viewer.user_permissions.add(permissions["view_koboasset"])
        cls.editor.user_permissions.add(
            permissions["view_koboasset"], permissions["change_koboasset"]
        )
        cls.definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )

    def setUp(self):
        self.discovered = KoboDiscoveredAsset.objects.create(
            asset_uid="manual-config-uid-sensitive-tail",
            name="Activo descubierto",
            asset_type="survey",
            deployment_status="deployed",
            metadata_snapshot={
                "uid": "manual-config-uid-sensitive-tail",
                "name": "Activo descubierto",
            },
            last_seen_at=django_timezone.now(),
        )
        self.project = Project.objects.create(
            code="PRJ-K13C",
            name="Proyecto K13C",
            status=Project.Status.ACTIVE,
        )

    def configure(self):
        # PRE: the default discovery is available and not configured.
        # POST: returns its newly configured inactive local asset.
        return configure_discovered_asset(
            self.discovered,
            name="Integración manual",
            form_definition=self.definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            configured_by=self.editor,
        )

    def test_configuration_uses_remote_uid_and_stays_isolated_inactive(self):
        project_count = Project.objects.count()
        asset = self.configure()

        self.assertEqual(asset.asset_uid, self.discovered.asset_uid)
        self.assertFalse(asset.is_active)
        self.assertFalse(KoboProjectBinding.objects.exists())
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertEqual(Project.objects.count(), project_count)
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_configuration_rejects_unavailable_duplicate_and_unsupported_definition(self):
        self.discovered.is_available = False
        self.discovered.save(update_fields=("is_available",))
        with self.assertRaises(ValidationError):
            self.configure()
        self.discovered.is_available = True
        self.discovered.save(update_fields=("is_available",))
        self.configure()
        with self.assertRaises(ValidationError):
            self.configure()

        other = KoboDiscoveredAsset.objects.create(
            asset_uid="unsupported-definition-asset",
            name="Unsupported",
            last_seen_at=django_timezone.now(),
        )
        unsupported = KoboFormDefinition.objects.create(
            form_id="not_registered", title="No registrada", version="1"
        )
        with self.assertRaises(ValidationError):
            configure_discovered_asset(
                other,
                name="No permitida",
                form_definition=unsupported,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
                configured_by=self.editor,
            )

    def test_binding_validation_and_strategy_exclusion(self):
        asset = self.configure()
        direct = create_project_binding(
            asset,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            project=self.project,
            source_field="",
            source_value="",
            is_active=True,
            configured_by=self.editor,
        )
        self.assertTrue(direct.is_active)
        self.assertFalse(asset.is_active)
        with self.assertRaises(ValidationError):
            create_project_binding(
                asset,
                routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
                project=self.project,
                source_field="submission.parish",
                source_value="parish-1",
                is_active=True,
                configured_by=self.editor,
            )

    def test_field_value_routes_and_unsafe_sources(self):
        asset = self.configure()
        for value in ("parish-1", "parish-2"):
            create_project_binding(
                asset,
                routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
                project=self.project,
                source_field="payload.parish_key",
                source_value=value,
                is_active=True,
                configured_by=self.editor,
            )
        self.assertTrue(get_asset_readiness(asset).ready)
        for source_field in (
            "payload._private",
            "raw_payload.secret",
            "payload.path/value",
            "payload.items[0]",
            "payload.two..parts",
            "payload.with space",
        ):
            with self.subTest(source_field=source_field):
                with self.assertRaises(ValidationError):
                    validate_routing_source_field(source_field)

    def test_readiness_activation_and_deactivation_preserve_bindings(self):
        asset = self.configure()
        self.assertEqual(get_asset_readiness(asset).code, "no_active_bindings")
        create_project_binding(
            asset,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            project=self.project,
            source_field="",
            source_value="",
            is_active=True,
            configured_by=self.editor,
        )
        self.assertEqual(get_asset_readiness(asset).code, "ready_to_activate")
        activate_kobo_asset(asset, activated_by=self.editor)
        self.assertTrue(asset.is_active)
        deactivate_kobo_asset(asset, deactivated_by=self.editor)
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.project_bindings.count(), 1)

    def test_readiness_rejects_mixed_routing_and_inactive_definition(self):
        asset = self.configure()
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            is_active=True,
        )
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.parish",
            source_value="parish-1",
            is_active=True,
        )
        self.assertEqual(get_asset_readiness(asset).code, "mixed_routing")
        with self.assertRaises(ValidationError):
            activate_kobo_asset(asset, activated_by=self.editor)

        self.definition.is_active = False
        self.definition.save(update_fields=("is_active",))
        self.assertEqual(get_asset_readiness(asset).code, "missing_form_definition")

    def test_browser_surfaces_require_login_permissions_and_do_not_mutate_on_get(self):
        list_url = reverse("kobo:discovered_asset_list")
        detail_url = reverse("kobo:discovered_asset_detail", args=(self.discovered.pk,))
        self.assertEqual(self.client.get(list_url).status_code, 302)
        self.client.force_login(self.editor)
        before = (KoboAsset.objects.count(), KoboProjectBinding.objects.count())
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activo descubierto, aún no configurado")
        self.assertEqual(before, (KoboAsset.objects.count(), KoboProjectBinding.objects.count()))

        self.client.force_login(get_user_model().objects.create_user("no-kobo-permission"))
        self.assertEqual(self.client.get(list_url).status_code, 403)

    def test_incompatible_discovery_hides_form_and_blocks_configuration_post(self):
        self.client.force_login(self.editor)
        detail_url = reverse(
            "kobo:discovered_asset_detail", args=(self.discovered.pk,)
        )
        response = self.client.get(detail_url)

        self.assertContains(
            response,
            "Este activo fue descubierto, pero todavía no tiene una definición "
            "soportada en SIGEDON.",
        )
        self.assertNotContains(response, "Configurar activo")

        response = self.client.post(
            reverse("kobo:configure_discovered_asset", args=(self.discovered.pk,)),
            {
                "name": "Intento incompatible",
                "form_definition": self.definition.pk,
                "form_role": KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(KoboAsset.objects.exists())

    def test_compatible_discovery_exposes_only_fixed_definition_and_role(self):
        self.discovered.metadata_snapshot["id_string"] = FICHA_01_FORM_ID
        self.discovered.metadata_snapshot["version"] = FICHA_01_VERSION
        self.discovered.save(update_fields=("metadata_snapshot",))
        other_definition = KoboFormDefinition.objects.create(
            form_id="ficha_02_capacidad_parroquial",
            title="Ficha 02 - Capacidad parroquial",
            version="20260710",
        )
        self.client.force_login(self.editor)
        detail_url = reverse(
            "kobo:discovered_asset_detail", args=(self.discovered.pk,)
        )
        response = self.client.get(detail_url)

        self.assertContains(response, "Configurar activo")
        form = response.context["configuration_form"]
        self.assertEqual(list(form.fields["form_definition"].queryset), [self.definition])
        self.assertEqual(
            tuple(value for value, _label in form.fields["form_role"].choices),
            (KoboAsset.FormRole.TERRITORIAL_PROFILE,),
        )

        configure_url = reverse(
            "kobo:configure_discovered_asset", args=(self.discovered.pk,)
        )
        tampered = self.client.post(
            configure_url,
            {
                "name": "Manipulado",
                "form_definition": other_definition.pk,
                "form_role": KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            },
        )
        self.assertEqual(tampered.status_code, 400)
        self.assertFalse(KoboAsset.objects.exists())

        valid = self.client.post(
            configure_url,
            {
                "name": "Compatible",
                "form_definition": self.definition.pk,
                "form_role": KoboAsset.FormRole.TERRITORIAL_PROFILE,
            },
        )
        self.assertEqual(valid.status_code, 302)
        asset = KoboAsset.objects.get()
        self.assertEqual(asset.form_definition, self.definition)
        self.assertEqual(asset.form_role, KoboAsset.FormRole.TERRITORIAL_PROFILE)

    @override_settings(KOBO_ENABLED=False)
    def test_disabled_feature_hides_configuration_surfaces(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("kobo:discovered_asset_list"))
        self.assertEqual(response.status_code, 404)

    def test_binding_form_rejects_tampered_project_and_invalid_shapes(self):
        form = KoboProjectBindingForm(
            {
                "routing_type": KoboProjectBinding.RoutingType.DIRECT,
                "project": self.project.pk + 9999,
                "source_field": "submission.parish",
                "source_value": "x",
                "is_active": "on",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("project", form.errors)

    @override_settings(KOBO_ENABLED=True)
    def test_operational_configuration_hides_technical_routing_fields(self):
        asset = self.configure()
        self.client.force_login(self.editor)

        response = self.client.get(
            reverse("kobo:asset_configuration", args=(asset.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proyecto actualmente enlazado")
        self.assertContains(response, "Enlazar proyecto")
        self.assertNotContains(response, "routing_type")
        self.assertNotContains(response, "source_field")
        self.assertNotContains(response, "source_value")
        self.assertNotContains(response, "no_active_bindings")
        self.assertIsInstance(response.context["binding_form"], KoboAssetProjectLinkForm)

    @override_settings(KOBO_ENABLED=True)
    def test_operational_link_ignores_technical_post_fields_and_preserves_history(self):
        asset = self.configure()
        historical = KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="PRJ-000001",
            is_active=True,
        )
        self.client.force_login(self.editor)

        response = self.client.post(
            reverse("kobo:create_project_binding", args=(asset.pk,)),
            {
                "project": self.project.pk,
                "routing_type": KoboProjectBinding.RoutingType.FIELD_VALUE,
                "source_field": "payload.nucleo_code",
                "source_value": "PRJ-000001",
                "is_active": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha enlazada correctamente")
        historical.refresh_from_db()
        direct = KoboProjectBinding.objects.get(
            asset=asset,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
        )
        asset.refresh_from_db()
        self.assertTrue(direct.is_active)
        self.assertEqual(direct.project, self.project)
        self.assertEqual(direct.source_field, "")
        self.assertEqual(direct.source_value, "")
        self.assertFalse(historical.is_active)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.project_bindings.filter(is_active=True).count(), 1)

    def test_link_change_and_unlink_preserve_submission_history(self):
        asset = self.configure()
        other_project = Project.objects.create(
            code="PRJ-K13C-OTHER",
            name="Proyecto K13C alterno",
            status=Project.Status.ACTIVE,
        )
        historical_submission = KoboSubmission.objects.create(
            form_definition=self.definition,
            external_id="historical-linked-submission",
            raw_payload={"_xform_id_string": asset.asset_uid},
            normalized_payload={},
            status=KoboSubmission.Status.IMPORTED,
            project=self.project,
            asset=asset,
            processed_at=django_timezone.now(),
        )
        link_asset_to_project(asset, project=self.project, linked_by=self.editor)
        link_asset_to_project(asset, project=other_project, linked_by=self.editor)

        asset.refresh_from_db()
        active_binding = asset.project_bindings.get(is_active=True)
        historical_submission.refresh_from_db()
        self.assertEqual(active_binding.project, other_project)
        self.assertEqual(asset.project_bindings.filter(is_active=True).count(), 1)
        self.assertEqual(historical_submission.project, self.project)

        new_submission = KoboSubmission.objects.create(
            form_definition=self.definition,
            external_id="new-linked-submission",
            raw_payload={"_xform_id_string": asset.asset_uid},
            normalized_payload={},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        result = associate_submission_with_project(
            new_submission,
            reviewed_by=self.editor,
        )
        new_submission.refresh_from_db()
        self.assertTrue(result.associated)
        self.assertEqual(new_submission.project, other_project)

        unlink_asset_from_project(asset, unlinked_by=self.editor)
        asset.refresh_from_db()
        self.assertFalse(asset.is_active)
        self.assertFalse(asset.project_bindings.filter(is_active=True).exists())
        historical_submission.refresh_from_db()
        self.assertEqual(historical_submission.project, self.project)

        unlinked_submission = KoboSubmission.objects.create(
            form_definition=self.definition,
            external_id="unlinked-submission",
            raw_payload={"_xform_id_string": asset.asset_uid},
            normalized_payload={},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        result = associate_submission_with_project(
            unlinked_submission,
            reviewed_by=self.editor,
        )
        self.assertFalse(result.associated)
        unlinked_submission.refresh_from_db()
        self.assertEqual(unlinked_submission.error_code, "asset_inactive")

    def test_operational_link_rejects_inactive_definition_and_unsupported_asset(self):
        asset = self.configure()
        inactive_project = Project.objects.create(
            code="PRJ-K13C-INACTIVE",
            name="Proyecto K13C inactivo",
            status=Project.Status.SUSPENDED,
        )
        with self.assertRaises(ValidationError):
            link_asset_to_project(
                asset,
                project=inactive_project,
                linked_by=self.editor,
            )

        self.definition.is_active = False
        self.definition.save(update_fields=("is_active",))
        with self.assertRaises(ValidationError):
            link_asset_to_project(asset, project=self.project, linked_by=self.editor)

        self.definition.is_active = True
        self.definition.save(update_fields=("is_active",))
        asset.form_role = KoboAsset.FormRole.PRIORITIZED_MICROPROJECT
        asset.save(update_fields=("form_role",))
        with self.assertRaises(ValidationError):
            link_asset_to_project(asset, project=self.project, linked_by=self.editor)


@override_settings(
    KOBO_ENABLED=True,
    KOBO_WEBHOOK_USERNAME="sigedon-kobo",
    KOBO_WEBHOOK_SECRET="test-webhook-secret",
)
class KoboWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        definitions = (
            (FICHA_01_FORM_ID, FICHA_01_VERSION, KoboAsset.FormRole.TERRITORIAL_PROFILE),
            (FICHA_10_FORM_ID, FICHA_10_VERSION, KoboAsset.FormRole.PRIORITIZED_MICROPROJECT),
            (FICHA_11_FORM_ID, FICHA_11_VERSION, KoboAsset.FormRole.PRIORITIZATION_MATRIX),
        )
        cls.project = Project.objects.create(
            code="PRJ-WEBHOOK-DIRECT",
            name="Proyecto sintético de webhook",
            status=Project.Status.ACTIVE,
        )
        cls.assets = {}
        for index, (form_id, version, role) in enumerate(definitions, start=1):
            definition = KoboFormDefinition.objects.create(
                form_id=form_id, version=version, title=f"Webhook {index}"
            )
            cls.assets[form_id] = KoboAsset.objects.create(
                asset_uid=f"webhook-asset-{index}",
                name=f"Webhook asset {index}",
                form_definition=definition,
                form_role=role,
            )
            KoboDiscoveredAsset.objects.create(
                asset_uid=cls.assets[form_id].asset_uid,
                name=f"Webhook discovery {index}",
                metadata_snapshot={"id_string": form_id, "version": version},
                last_seen_at=django_timezone.now(),
            )
            KoboProjectBinding.objects.create(
                asset=cls.assets[form_id],
                project=cls.project,
                routing_type=KoboProjectBinding.RoutingType.DIRECT,
            )

    def payload(self, form_id, **overrides):
        # PRE: form_id identifies an active webhook asset.
        # POST: returns a valid payload for that exact supported contract.
        asset = self.assets[form_id]
        payload = {"_uuid": f"webhook-{form_id}", "_xform_id_string": asset.asset_uid}
        if form_id == FICHA_01_FORM_ID:
            payload.update(KoboFicha01NormalizerTests().valid_payload())
        elif form_id == FICHA_10_FORM_ID:
            payload.update(KoboFicha10NormalizerTests().valid_payload())
        else:
            payload.update(KoboFicha11NormalizerTests().valid_payload())
        payload["_uuid"] = f"webhook-{form_id}"
        payload["_xform_id_string"] = asset.asset_uid
        payload.update(overrides)
        return payload

    def ficha_01_slash_payload(self, **overrides):
        # PRE: overrides contains only synthetic Kobo REST Services Ficha 1 data.
        # POST: returns a valid asset-UID payload with slash-separated field paths.
        payload = KoboFicha01NormalizerTests().slash_payload(
            _uuid="webhook-ficha-01-slash",
            _xform_id_string=self.assets[FICHA_01_FORM_ID].asset_uid,
        )
        payload.update(overrides)
        return payload

    def ficha_10_slash_payload(self, **overrides):
        # PRE: overrides contains only synthetic Kobo REST Services Ficha 10 data.
        # POST: returns a valid asset-UID payload with slash-separated microproject fields.
        payload = self.payload(FICHA_10_FORM_ID)
        microproject = payload.pop("microproject")
        payload.update(
            {f"microproject/{key}": value for key, value in microproject.items()}
        )
        payload["_uuid"] = "webhook-ficha-10-slash"
        payload.update(overrides)
        return payload

    def ficha_11_slash_payload(self, **overrides):
        # PRE: overrides contains only synthetic Kobo REST Services Ficha 11 data.
        # POST: returns a valid asset-UID payload with slash-separated scoring fields.
        payload = self.payload(FICHA_11_FORM_ID)
        scoring = payload.pop("scoring")
        payload.update({f"scoring/{key}": value for key, value in scoring.items()})
        payload["_uuid"] = "webhook-ficha-11-slash"
        payload.update(overrides)
        return payload

    def post(self, payload, *, secret="test-webhook-secret"):
        return self.client.post(
            reverse("kobo:webhook_submission"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_KOBO_WEBHOOK_SECRET=secret,
        )

    def post_basic(
        self,
        payload,
        *,
        username="sigedon-kobo",
        password="test-webhook-secret",
    ):
        # PRE: username and password are test-only Basic credential values.
        # POST: sends one webhook POST using an encoded Authorization header.
        credentials = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        return self.client.post(
            reverse("kobo:webhook_submission"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )

    def test_rejects_method_disabled_feature_and_invalid_authentication(self):
        url = reverse("kobo:webhook_submission")
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response["WWW-Authenticate"],
            'Basic realm="SIGEDON Kobo Webhook"',
        )
        self.assertEqual(self.post({}, secret="wrong").status_code, 401)
        with self.settings(KOBO_ENABLED=False):
            self.assertEqual(self.post({}).status_code, 404)

    def test_basic_authentication_accepts_valid_credentials_and_processes_submission(self):
        response = self.post_basic(self.payload(FICHA_01_FORM_ID))

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=f"webhook-{FICHA_01_FORM_ID}")
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)

    def test_webhook_normalizes_slash_payload_with_asset_uid_and_opaque_version(self):
        payload = self.ficha_01_slash_payload(__version__="deployment-opaque-version")

        response = self.post_basic(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.asset, self.assets[FICHA_01_FORM_ID])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.raw_payload, payload)
        self.assertEqual(submission.raw_payload["__version__"], "deployment-opaque-version")
        self.assertEqual(submission.project, self.project)
        self.assertIsNotNone(submission.processed_at)
        self.assertTrue(
            submission.processing_events.filter(
                stage="project_routing", code="project_assigned"
            ).exists()
        )
        self.assertEqual(submission.parish, "Parroquia sintética")
        self.assertEqual(submission.normalized_payload["nucleo_code"], "NV-SYNTHETIC")

    def test_webhook_normalizes_ficha_10_slash_payload_and_assigns_direct_project(self):
        payload = self.ficha_10_slash_payload()

        response = self.post(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(
            submission.normalized_payload["microproject_name"],
            "Rehabilitación del centro comunitario",
        )

    def test_webhook_normalizes_ficha_11_slash_payload_and_assigns_direct_project(self):
        payload = self.ficha_11_slash_payload()

        response = self.post(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertIsNotNone(submission.normalized_at)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(submission.normalized_payload["priority_total"], 10)

    def test_webhook_rejects_incompatible_discovered_asset_metadata_without_staging(self):
        discovered = KoboDiscoveredAsset.objects.get(
            asset_uid=self.assets[FICHA_01_FORM_ID].asset_uid
        )
        discovered.metadata_snapshot = {
            "id_string": "wrong-form-id",
            "version": FICHA_01_VERSION,
        }
        discovered.save(update_fields=("metadata_snapshot",))

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 400)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_webhook_rejects_mismatched_discovered_asset_version_without_staging(self):
        discovered = KoboDiscoveredAsset.objects.get(
            asset_uid=self.assets[FICHA_01_FORM_ID].asset_uid
        )
        discovered.metadata_snapshot["version"] = "wrong-contract-version"
        discovered.save(update_fields=("metadata_snapshot",))

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 400)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_slash_payload_uses_the_direct_binding_not_nucleo_code(self):
        project = self.project
        reviewer = get_user_model().objects.create_user("slash-reviewer")
        payload = self.ficha_01_slash_payload(
            **{"identification/nucleo_code": "NOT-A-PROJECT-CODE"}
        )

        response = self.post(payload)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        submission.status = KoboSubmission.Status.APPROVED_FOR_IMPORT
        submission.save(update_fields=("status",))
        result = associate_submission_with_project(submission, reviewed_by=reviewer)
        submission.refresh_from_db()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(result.associated)
        self.assertEqual(submission.project, project)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

    def test_basic_authentication_rejects_invalid_or_malformed_credentials(self):
        url = reverse("kobo:webhook_submission")
        cases = (
            self.post_basic({}, username="other"),
            self.post_basic({}, password="other"),
            self.client.post(
                url,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Basic not-base64!",
            ),
            self.client.post(
                url,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer token",
            ),
            self.client.post(
                url,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Basic Og==",
            ),
        )

        for response in cases:
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response["WWW-Authenticate"],
                    'Basic realm="SIGEDON Kobo Webhook"',
                )
        self.assertFalse(KoboSubmission.objects.exists())

    def test_basic_authentication_preserves_idempotency(self):
        payload = self.payload(FICHA_10_FORM_ID)
        first = self.post_basic(payload)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        event_count = submission.processing_events.count()

        second = self.post_basic(payload)
        submission.refresh_from_db()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        self.assertEqual(submission.processing_events.count(), event_count)

    def test_rejects_invalid_json_and_unavailable_assets_safely(self):
        url = reverse("kobo:webhook_submission")
        self.assertEqual(self.client.post(url, data="[", content_type="application/json", HTTP_X_KOBO_WEBHOOK_SECRET="test-webhook-secret").status_code, 400)
        self.assertEqual(self.post([]).status_code, 400)
        self.assertEqual(self.post({"_xform_id_string": "missing"}).status_code, 400)

    def test_stages_and_processes_each_supported_form_without_operations_effects(self):
        for form_id in self.assets:
            with self.subTest(form_id=form_id):
                response = self.post(self.payload(form_id))
                submission = KoboSubmission.objects.get(external_id=f"webhook-{form_id}")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
                self.assertEqual(submission.asset, self.assets[form_id])
                self.assertEqual(submission.project, self.project)
                self.assertIsNotNone(submission.processed_at)
                self.assertTrue(
                    submission.processing_events.filter(
                        stage="project_routing", code="project_assigned"
                    ).exists()
                )
                self.assertNotIn("raw_payload", response.json())
        self.assertEqual(Project.objects.count(), 1)
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_webhook_preserves_staging_when_direct_binding_is_unavailable(self):
        asset = self.assets[FICHA_01_FORM_ID]
        asset.project_bindings.update(is_active=False)
        payload = self.ficha_01_slash_payload()

        response = self.post(payload)

        self.assertEqual(response.status_code, 422)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIsNone(submission.project_id)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(submission.error_code, "routing_configuration_error")
        self.assertTrue(
            submission.processing_events.filter(
                stage="project_routing", code="routing_configuration_error"
            ).exists()
        )

    def test_webhook_rejects_multiple_active_bindings_without_losing_staging(self):
        asset = self.assets[FICHA_01_FORM_ID]
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="unused",
        )

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 422)
        submission = KoboSubmission.objects.get(external_id="webhook-ficha-01-slash")
        self.assertEqual(submission.error_code, "routing_configuration_error")

    def test_webhook_rejects_inactive_direct_project_without_losing_staging(self):
        self.project.status = Project.Status.SUSPENDED
        self.project.save(update_fields=("status",))

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 422)
        submission = KoboSubmission.objects.get(external_id="webhook-ficha-01-slash")
        self.assertEqual(submission.error_code, "routing_configuration_error")

    def test_duplicate_preserves_payload_and_events(self):
        payload = self.payload(FICHA_10_FORM_ID)
        self.post(payload)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        event_count = submission.processing_events.count()
        changed = {
            **payload,
            "microproject": {
                **payload["microproject"],
                "microproject_name": "No debe sobrescribir",
            },
        }

        response = self.post(changed)
        submission.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertNotEqual(
            submission.raw_payload["microproject"]["microproject_name"],
            changed["microproject"]["microproject_name"],
        )
        self.assertEqual(submission.processing_events.count(), event_count)

    def test_retry_processes_an_existing_received_submission(self):
        payload = self.ficha_01_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=self.assets[FICHA_01_FORM_ID].form_definition,
            asset=self.assets[FICHA_01_FORM_ID],
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.RECEIVED,
        )

        response = self.post(payload)

        submission.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)

    def test_retry_recovers_a_ready_submission_after_binding_is_restored(self):
        asset = self.assets[FICHA_01_FORM_ID]
        asset.project_bindings.update(is_active=False)
        payload = self.ficha_01_slash_payload()

        first = self.post(payload)
        asset.project_bindings.update(is_active=True)
        retry = self.post(payload)

        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(first.status_code, 422)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertEqual(
            submission.processing_events.filter(code="normalized").count(), 1
        )

    def test_rejects_oversized_body_without_staging(self):
        with self.settings(KOBO_WEBHOOK_MAX_BYTES=8):
            response = self.post(self.payload(FICHA_01_FORM_ID))

        self.assertEqual(response.status_code, 413)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_absent_or_invalid_content_length_is_safe(self):
        payload = self.payload(FICHA_10_FORM_ID)
        url = reverse("kobo:webhook_submission")
        for content_length in (None, "invalid"):
            with self.subTest(content_length=content_length):
                headers = {"HTTP_X_KOBO_WEBHOOK_SECRET": "test-webhook-secret"}
                if content_length is not None:
                    headers["CONTENT_LENGTH"] = content_length
                response = self.client.post(
                    url,
                    data=json.dumps({**payload, "_uuid": f"{payload['_uuid']}-{content_length}"}),
                    content_type="application/json",
                    **headers,
                )
                self.assertEqual(
                    response.status_code,
                    201 if content_length is None else 400,
                )

    def test_internal_errors_do_not_expose_request_data(self):
        payload = self.payload(FICHA_01_FORM_ID)
        with patch(
            "apps.integrations.kobo.views.converge_webhook_submission",
            side_effect=RuntimeError("secret payload diagnostic"),
        ):
            response = self.post(payload)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"ok": False, "error": "internal_error"})
        self.assertNotIn("secret", response.content.decode())


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking")
@override_settings(
    KOBO_ENABLED=True,
    KOBO_WEBHOOK_USERNAME="sigedon-kobo",
    KOBO_WEBHOOK_SECRET="test-webhook-secret",
)
class KoboWebhookConcurrencyTests(TransactionTestCase):
    def setUp(self):
        definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            version=FICHA_01_VERSION,
            title="Webhook concurrente",
        )
        self.project = Project.objects.create(
            code="PRJ-WEBHOOK-CONCURRENT",
            name="Proyecto concurrente",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="webhook-concurrent-asset",
            name="Webhook concurrente",
            form_definition=definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        KoboDiscoveredAsset.objects.create(
            asset_uid=self.asset.asset_uid,
            name=self.asset.name,
            metadata_snapshot={"id_string": FICHA_01_FORM_ID, "version": FICHA_01_VERSION},
            last_seen_at=django_timezone.now(),
        )
        KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
        )

    def test_simultaneous_webhooks_stage_and_converge_once(self):
        payload = KoboFicha01NormalizerTests().valid_payload()
        payload.update(
            _uuid="webhook-concurrent-uuid",
            _xform_id_string=self.asset.asset_uid,
        )
        barrier = Barrier(2)
        results = Queue()

        def post_webhook():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = Client().post(
                    reverse("kobo:webhook_submission"),
                    data=json.dumps(payload),
                    content_type="application/json",
                    HTTP_X_KOBO_WEBHOOK_SECRET="test-webhook-secret",
                )
                results.put(response.status_code)
            finally:
                connections.close_all()

        threads = [Thread(target=post_webhook) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse([thread for thread in threads if thread.is_alive()])
        self.assertEqual(sorted(results.get_nowait() for _ in threads), [200, 201])
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertEqual(
            submission.processing_events.filter(code="webhook_received").count(), 1
        )
        self.assertEqual(submission.processing_events.filter(code="normalized").count(), 1)


class KoboWebhookStagingTests(KoboWebhookTests):
    def test_service_stages_only_once_and_validates_asset_uid(self):
        asset = self.assets[FICHA_11_FORM_ID]
        payload = self.payload(FICHA_11_FORM_ID)
        submission, created = receive_webhook_submission(asset=asset, raw_payload=payload)
        duplicate, duplicate_created = receive_webhook_submission(asset=asset, raw_payload=payload)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(submission.pk, duplicate.pk)
        self.assertEqual(submission.status, KoboSubmission.Status.RECEIVED)
        self.assertEqual(submission.asset, asset)
        with self.assertRaises(KoboPayloadError):
            receive_webhook_submission(asset=asset, raw_payload={**payload, "_xform_id_string": "other"})


@override_settings(
    KOBO_ENABLED=True,
    KOBO_BASE_URL="https://kf.example.test",
    KOBO_API_TOKEN="test-token",
    KOBO_WEBHOOK_USERNAME="sigedon-kobo",
    KOBO_WEBHOOK_SECRET="test-webhook-secret",
)
class KoboReconciliationCommandTests(KoboWebhookTests):
    def test_command_dry_run_and_asset_filter(self):
        asset = self.assets[FICHA_01_FORM_ID]
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [self.payload(FICHA_01_FORM_ID)])
        with patch("apps.integrations.kobo.management.commands.reconcile_kobo_submissions.KoboApiClient", return_value=client):
            output = StringIO()
            call_command("reconcile_kobo_submissions", "--asset-uid", asset.asset_uid, "--dry-run", stdout=output)
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertIn("created=1", output.getvalue())

    def test_command_rejects_disabled_feature_and_invalid_limit(self):
        with self.settings(KOBO_ENABLED=False):
            with self.assertRaises(CommandError):
                call_command("reconcile_kobo_submissions", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("reconcile_kobo_submissions", "--limit", "0", stdout=StringIO())

    def test_command_reprocesses_local_failure_despite_remote_failure(self):
        asset = self.assets[FICHA_01_FORM_ID]
        project = self.project
        payload = self.ficha_01_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(
            get_submissions=lambda asset_uid, limit: (_ for _ in ()).throw(
                KoboIntegrationError("remote unavailable")
            )
        )

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=output,
            )

        submission.refresh_from_db()
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, project)
        self.assertTrue(submission.normalized_payload)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")
        self.assertTrue(
            submission.processing_events.filter(
                stage="reconciliation", code="local_reprocessed"
            ).exists()
        )
        self.assertIn("local_reprocessed=1", output.getvalue())
        self.assertIn("failed_assets=1", output.getvalue())

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=output,
            )
        self.assertIn("local_reprocessed=0", output.getvalue())
        self.assertEqual(KoboSubmission.objects.count(), 1)

    def test_command_dry_run_does_not_modify_local_recoverable_failure(self):
        asset = self.assets[FICHA_01_FORM_ID]
        payload = self.ficha_01_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                "--dry-run",
                stdout=output,
            )

        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertEqual(submission.processing_events.count(), 1)
        self.assertIn("local_would_reprocess=1", output.getvalue())

    def test_command_recovers_ficha_10_slash_validation_failure_idempotently(self):
        asset = self.assets[FICHA_10_FORM_ID]
        payload = self.ficha_10_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=StringIO(),
            )

        submission.refresh_from_db()
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertTrue(submission.normalized_payload)
        self.assertIsNotNone(submission.normalized_at)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

    def test_command_recovers_ficha_11_scoring_validation_failure_idempotently(self):
        asset = self.assets[FICHA_11_FORM_ID]
        payload = self.ficha_11_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=StringIO(),
            )

        submission.refresh_from_db()
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertTrue(submission.normalized_payload)
        self.assertIsNotNone(submission.normalized_at)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=StringIO(),
            )
        self.assertEqual(KoboSubmission.objects.count(), 1)
