from apps.integrations.kobo.attachments import download_and_store_attachment
from apps.integrations.kobo.client import DownloadedContent
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboDiscoveredAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.models import KoboPastoralZoneProjectMapping
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.tests.helpers import RecordingAttachmentStorage
from apps.integrations.kobo.tests.helpers import StubAttachmentClient
from apps.integrations.kobo.tests.test_contracts import KoboFicha01NormalizerTests
from apps.operations.models import Project
from datetime import timedelta
from django.db import IntegrityError
from django.db import close_old_connections
from django.db import connection
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.test import TransactionTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone
from queue import Queue
from threading import Barrier
from threading import Event
from threading import Thread
from unittest import skipUnless
from unittest.mock import patch
import json


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
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="catia_la_mar",
            project=self.project,
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
