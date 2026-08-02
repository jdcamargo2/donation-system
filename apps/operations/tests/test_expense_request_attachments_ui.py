"""Expense Request protected attachment UI / permissions / lifecycle (ER6)."""

from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.expense_request_services import (
    add_expense_request_attachments,
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    fulfill_expense_request,
    withdraw_expense_request,
)
from apps.operations.financials import get_allocation_reserved_amount
from apps.operations.models import (
    AuditLog,
    ExpenseRequest,
    ExpenseRequestAttachment,
    ExpenseRequestEvent,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation


PDF_BYTES = b'%PDF-1.4 er6-attachment\n'
PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)


class ExpenseRequestAttachmentsUITests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('500.00'))
        self.admin = self._user('er6-ui-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er6-ui-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._user('er6-ui-operator-b', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er6-ui-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er6-ui-auditor', ROLE_EXTERNAL_AUDITOR)
        self.request_obj = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('40.00'),
            purpose='Solicitud ER6 UI',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        self.admin_request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('25.00'),
            purpose='Solicitud ER6 Admin propia',
            requested_date=TEST_DATE,
            actor=self.admin,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _create_url(self, request_obj=None):
        request_obj = request_obj or self.request_obj
        return reverse('expense_request_attachment_create', args=[request_obj.pk])

    def _delete_url(self, attachment, request_obj=None):
        request_obj = request_obj or self.request_obj
        return reverse(
            'expense_request_attachment_delete',
            args=[request_obj.pk, attachment.pk],
        )

    def _upload(self, *, actor, request_obj=None, files=None, title='Evidencia', notes='Nota'):
        request_obj = request_obj or self.request_obj
        self.client.force_login(actor)
        payload = {
            'title': title,
            'notes': notes,
            'files': files
            or [SimpleUploadedFile('evidencia.pdf', PDF_BYTES, content_type='application/pdf')],
        }
        return self.client.post(self._create_url(request_obj), payload)

    def test_owner_pending_get_and_post_success(self):
        self.client.force_login(self.operator)
        get_response = self.client.get(self._create_url())
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, self.request_obj.code)
        self.assertContains(get_response, self.request_obj.get_status_display())

        event_count = ExpenseRequestEvent.objects.filter(
            expense_request=self.request_obj
        ).count()
        reserved_before = get_allocation_reserved_amount(self.allocation)
        audit_before = AuditLog.objects.count()

        response = self._upload(actor=self.operator)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('expense_request_detail', args=[self.request_obj.pk]),
        )
        attachment = ExpenseRequestAttachment.objects.get(expense_request=self.request_obj)
        self.assertEqual(attachment.title, 'Evidencia')
        self.assertEqual(attachment.notes, 'Nota')
        self.assertEqual(attachment.uploaded_by_id, self.operator.pk)
        self.assertTrue(attachment.file.name.startswith('expense_request_attachments/'))
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(expense_request=self.request_obj).count(),
            event_count,
        )
        self.assertEqual(get_allocation_reserved_amount(self.allocation), reserved_before)
        self.assertEqual(AuditLog.objects.count(), audit_before + 1)
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(attachment.pk),
                summary='Adjunto de solicitud agregado.',
            ).exists()
        )

        detail = self.client.get(reverse('expense_request_detail', args=[self.request_obj.pk]))
        self.assertContains(detail, 'Adjunto agregado a la solicitud.')
        self.assertContains(detail, 'Agregar adjunto')
        self.assertContains(detail, 'Eliminar adjunto')
        self.assertNotContains(detail, '/media/')
        self.assertNotContains(detail, attachment.file.url)

    def test_admin_owner_pending_can_mutate_own_request_only(self):
        response = self._upload(actor=self.admin, request_obj=self.admin_request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ExpenseRequestAttachment.objects.filter(expense_request=self.admin_request).count(),
            1,
        )

        forbidden = self._upload(actor=self.admin, request_obj=self.request_obj)
        self.assertEqual(forbidden.status_code, 404)
        self.assertFalse(
            ExpenseRequestAttachment.objects.filter(expense_request=self.request_obj).exists()
        )

    def test_committee_auditor_anonymous_and_foreign_operator_cannot_upload(self):
        for actor in (self.committee, self.auditor, self.other_operator):
            with self.subTest(actor=actor.username):
                before = ExpenseRequestAttachment.objects.count()
                response = self._upload(actor=actor)
                self.assertIn(response.status_code, {403, 404})
                self.assertEqual(ExpenseRequestAttachment.objects.count(), before)

        self.client.logout()
        anonymous = self.client.post(
            self._create_url(),
            {
                'title': 'Anon',
                'files': SimpleUploadedFile('a.pdf', PDF_BYTES, content_type='application/pdf'),
            },
        )
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn('/accounts/login/', anonymous['Location'])

    def test_forged_parent_uploader_status_ignored(self):
        self.client.force_login(self.operator)
        response = self.client.post(
            self._create_url(),
            {
                'title': 'Forged',
                'notes': '',
                'files': SimpleUploadedFile('f.pdf', PDF_BYTES, content_type='application/pdf'),
                'expense_request': self.admin_request.pk,
                'uploaded_by': self.admin.pk,
                'status': ExpenseRequest.Status.APPROVED_RESERVED,
            },
        )
        self.assertEqual(response.status_code, 302)
        attachment = ExpenseRequestAttachment.objects.get(expense_request=self.request_obj)
        self.assertEqual(attachment.uploaded_by_id, self.operator.pk)
        self.assertEqual(attachment.expense_request_id, self.request_obj.pk)
        self.assertFalse(
            ExpenseRequestAttachment.objects.filter(expense_request=self.admin_request).exists()
        )

    def test_multiple_files_atomic_and_validation_failure_creates_nothing(self):
        response = self._upload(
            actor=self.operator,
            files=[
                SimpleUploadedFile('one.pdf', PDF_BYTES, content_type='application/pdf'),
                SimpleUploadedFile('two.png', PNG_BYTES, content_type='image/png'),
            ],
            title='Paquete',
        )
        self.assertEqual(response.status_code, 302)
        attachments = list(
            ExpenseRequestAttachment.objects.filter(expense_request=self.request_obj).order_by('pk')
        )
        self.assertEqual(len(attachments), 2)
        self.assertEqual(AuditLog.objects.filter(summary='Adjunto de solicitud agregado.').count(), 2)

        detail = self.client.get(reverse('expense_request_detail', args=[self.request_obj.pk]))
        self.assertContains(detail, 'Adjuntos agregados a la solicitud.')

        self.client.force_login(self.operator)
        invalid = self.client.post(
            self._create_url(),
            {'title': '', 'files': SimpleUploadedFile('x.pdf', PDF_BYTES)},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(
            ExpenseRequestAttachment.objects.filter(expense_request=self.request_obj).count(),
            2,
        )

    def test_owner_delete_post_only_and_success_message(self):
        created = add_expense_request_attachments(
            expense_request_id=self.request_obj.pk,
            files=[SimpleUploadedFile('del.pdf', PDF_BYTES)],
            title='Borrar',
            actor=self.operator,
        )[0]
        stored_name = created.file.name
        storage = created.file.storage
        self.assertTrue(storage.exists(stored_name))

        self.client.force_login(self.operator)
        get_denied = self.client.get(self._delete_url(created))
        self.assertEqual(get_denied.status_code, 405)
        self.assertTrue(ExpenseRequestAttachment.objects.filter(pk=created.pk).exists())

        response = self.client.post(self._delete_url(created))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExpenseRequestAttachment.objects.filter(pk=created.pk).exists())
        self.assertFalse(storage.exists(stored_name))
        self.assertTrue(
            AuditLog.objects.filter(summary='Adjunto de solicitud eliminado.').exists()
        )
        detail = self.client.get(reverse('expense_request_detail', args=[self.request_obj.pk]))
        self.assertContains(detail, 'Adjunto eliminado de la solicitud.')

    def test_foreign_mismatch_and_non_owners_cannot_delete(self):
        created = add_expense_request_attachments(
            expense_request_id=self.request_obj.pk,
            files=[SimpleUploadedFile('keep.pdf', PDF_BYTES)],
            title='Keep',
            actor=self.operator,
        )[0]
        other = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('10.00'),
            purpose='Otra solicitud',
            requested_date=TEST_DATE,
            actor=self.other_operator,
        )
        other_attachment = add_expense_request_attachments(
            expense_request_id=other.pk,
            files=[SimpleUploadedFile('other.pdf', PDF_BYTES)],
            title='Other',
            actor=self.other_operator,
        )[0]

        self.client.force_login(self.operator)
        mismatch = self.client.post(
            reverse(
                'expense_request_attachment_delete',
                args=[self.request_obj.pk, other_attachment.pk],
            )
        )
        self.assertEqual(mismatch.status_code, 404)
        self.assertTrue(ExpenseRequestAttachment.objects.filter(pk=other_attachment.pk).exists())

        for actor in (self.other_operator, self.committee, self.auditor, self.admin):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.post(self._delete_url(created))
                self.assertIn(response.status_code, {403, 404})
                self.assertTrue(ExpenseRequestAttachment.objects.filter(pk=created.pk).exists())

    def test_terminal_states_freeze_upload_and_delete(self):
        def _pending_with_attachment(purpose, filename):
            pending = create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('14.00'),
                purpose=purpose,
                requested_date=TEST_DATE,
                actor=self.operator,
            )
            attachment = add_expense_request_attachments(
                expense_request_id=pending.pk,
                files=[SimpleUploadedFile(filename, PDF_BYTES)],
                title=purpose,
                actor=self.operator,
            )[0]
            return pending, attachment

        scenarios = []
        approved_req, approved_att = _pending_with_attachment('Freeze approved', 'a.pdf')
        approve_expense_request(approved_req, actor=self.committee)
        scenarios.append(('approved', approved_req, approved_att))

        denied_req, denied_att = _pending_with_attachment('Freeze denied', 'd.pdf')
        deny_expense_request(denied_req, decision_note='No procede', actor=self.committee)
        scenarios.append(('denied', denied_req, denied_att))

        withdrawn_req, withdrawn_att = _pending_with_attachment('Freeze withdrawn', 'w.pdf')
        withdraw_expense_request(withdrawn_req, reason='Ya no se requiere', actor=self.operator)
        scenarios.append(('withdrawn', withdrawn_req, withdrawn_att))

        annulled_req, annulled_att = _pending_with_attachment('Freeze annulled', 'n.pdf')
        annul_expense_request(annulled_req, reason='Error administrativo', actor=self.admin)
        scenarios.append(('annulled', annulled_req, annulled_att))

        fulfill_req, fulfill_att = _pending_with_attachment('Freeze fulfilled', 'f.pdf')
        approved = approve_expense_request(fulfill_req, actor=self.committee)
        fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('14.00'),
            reason='Ejecución',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='Detalle',
            support_file=SimpleUploadedFile('support.pdf', PDF_BYTES),
            support_title='Soporte',
            category='food',
            actor=self.admin,
        )
        scenarios.append(('fulfilled', fulfill_req, fulfill_att))

        for label, request_obj, attachment in scenarios:
            with self.subTest(label=label):
                self.client.force_login(self.operator)
                self.assertEqual(
                    self.client.get(
                        reverse('expense_request_attachment_create', args=[request_obj.pk])
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    self.client.post(
                        reverse(
                            'expense_request_attachment_delete',
                            args=[request_obj.pk, attachment.pk],
                        )
                    ).status_code,
                    404,
                )
                self.assertTrue(
                    ExpenseRequestAttachment.objects.filter(pk=attachment.pk).exists()
                )

    def test_ui_matrix_add_delete_flags(self):
        attachment = add_expense_request_attachments(
            expense_request_id=self.request_obj.pk,
            files=[SimpleUploadedFile('matrix.pdf', PDF_BYTES)],
            title='Matrix',
            actor=self.operator,
        )[0]
        matrix = [
            (self.operator, True, True),
            (self.admin, False, False),
            (self.committee, False, False),
            (self.auditor, False, False),
        ]
        for actor, can_add, can_delete in matrix:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.get(
                    reverse('expense_request_detail', args=[self.request_obj.pk])
                )
                self.assertEqual(
                    response.context['can_add_expense_request_attachment'], can_add
                )
                self.assertEqual(
                    response.context['can_delete_expense_request_attachments'], can_delete
                )
                html = response.content.decode()
                if can_add:
                    self.assertIn('Agregar adjunto', html)
                else:
                    self.assertNotIn('Agregar adjunto', html)
                if can_delete:
                    self.assertIn('Eliminar adjunto', html)
                else:
                    self.assertNotIn('Eliminar adjunto', html)
                # All authorized readers see protected download.
                self.assertIn(
                    reverse(
                        'expense_request_attachment_download',
                        args=[self.request_obj.pk, attachment.pk],
                    ),
                    html,
                )
                self.assertNotIn('/media/', html)

        approve_expense_request(self.request_obj, actor=self.committee)
        self.client.force_login(self.operator)
        frozen = self.client.get(
            reverse('expense_request_detail', args=[self.request_obj.pk])
        )
        self.assertFalse(frozen.context['can_add_expense_request_attachment'])
        self.assertFalse(frozen.context['can_delete_expense_request_attachments'])
        self.assertContains(
            frozen,
            'Los adjuntos están bloqueados porque la solicitud ya fue decidida o cerrada.',
        )
        self.assertContains(
            frozen,
            reverse(
                'expense_request_attachment_download',
                args=[self.request_obj.pk, attachment.pk],
            ),
        )

    def test_user_without_view_attachment_permission_denied_on_file_routes(self):
        attachment = add_expense_request_attachments(
            expense_request_id=self.request_obj.pk,
            files=[SimpleUploadedFile('noperm.pdf', PDF_BYTES)],
            title='No perm',
            actor=self.operator,
        )[0]
        limited = get_user_model().objects.create_user(
            username='er6-no-att-view', password='pass-12345'
        )
        limited.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='operations',
                codename='view_expenserequest',
            )
        )
        self.client.force_login(limited)
        preview = reverse(
            'expense_request_attachment_preview',
            args=[self.request_obj.pk, attachment.pk],
        )
        download = reverse(
            'expense_request_attachment_download',
            args=[self.request_obj.pk, attachment.pk],
        )
        self.assertEqual(self.client.get(preview).status_code, 403)
        self.assertEqual(self.client.get(download).status_code, 403)
