from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.operations.admin import DonationAdmin, FundAllocationAdmin, ProjectAdmin
from apps.operations.models import AuditLog, Donation, Expense, FundAllocation, Project
from apps.operations.services import (
    allocation_has_effective_expenses,
    annul_donation,
    annul_fund_allocation,
    annul_project,
    finish_project,
    update_fund_allocation,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense,
    create_project,
)


VALID_REASON = 'Registro anulado por una causa operativa documentada.'


class TerminalActionServiceTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username='terminal-service-actor',
            password='pass-12345',
        )

    def test_finish_project_persists_metadata_and_one_audit(self):
        project = create_project(code='PRJ-FINISH', name='Proyecto a terminar')
        project.status = Project.Status.ACTIVE
        project.save(update_fields=('status', 'updated_at'))

        finished = finish_project(project.pk, actor=self.actor)

        self.assertEqual(finished.status, Project.Status.CLOSED)
        self.assertEqual(finished.terminal_by, self.actor)
        self.assertIsNotNone(finished.terminal_at)
        self.assertEqual(finished.terminal_reason, 'Proyecto terminado.')
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(project.pk),
            ).count(),
            1,
        )

    def test_project_annulment_requires_no_non_annulled_allocations(self):
        project = create_project(code='PRJ-ANNUL', name='Proyecto a anular')
        allocation = create_allocation(project=project)

        with self.assertRaises(ValidationError):
            annul_project(project.pk, actor=self.actor, reason=VALID_REASON)
        project.refresh_from_db()
        self.assertNotEqual(project.status, Project.Status.ANNULLED)

        annul_fund_allocation(allocation.pk, actor=self.actor, reason=VALID_REASON)
        annulled = annul_project(project.pk, actor=self.actor, reason=VALID_REASON)
        self.assertEqual(annulled.status, Project.Status.ANNULLED)

    def test_donation_annulment_requires_no_non_annulled_allocations(self):
        donation = create_donation(amount=Decimal('100.00'))
        allocation = create_allocation(donation=donation, amount=Decimal('40.00'))

        with self.assertRaises(ValidationError):
            annul_donation(donation.pk, actor=self.actor, reason=VALID_REASON)

        annul_fund_allocation(allocation.pk, actor=self.actor, reason=VALID_REASON)
        annulled = annul_donation(donation.pk, actor=self.actor, reason=VALID_REASON)
        self.assertEqual(annulled.status, Donation.Status.ANNULLED)
        self.assertEqual(annulled.terminal_reason, VALID_REASON)
        self.assertEqual(annulled.terminal_by, self.actor)

    def test_allocation_annulment_releases_balance_and_is_audited_once(self):
        donation = create_donation(amount=Decimal('100.00'))
        allocation = create_allocation(donation=donation, amount=Decimal('60.00'))
        self.assertEqual(donation.available_balance, Decimal('40.00'))

        annulled = annul_fund_allocation(
            allocation.pk,
            actor=self.actor,
            reason=VALID_REASON,
        )
        donation.refresh_from_db()

        self.assertEqual(annulled.status, FundAllocation.Status.ANNULLED)
        self.assertEqual(donation.available_balance, Decimal('100.00'))
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                entity_id=str(allocation.pk),
                model_name='Asignación de fondos',
            ).count(),
            1,
        )
        original_metadata = (annulled.terminal_reason, annulled.terminal_at, annulled.terminal_by_id)
        with self.assertRaises(ValidationError):
            annul_fund_allocation(allocation.pk, actor=self.actor, reason='Segundo intento válido.')
        annulled.refresh_from_db()
        self.assertEqual(
            (annulled.terminal_reason, annulled.terminal_at, annulled.terminal_by_id),
            original_metadata,
        )

    def test_only_effective_expenses_block_allocation_annulment(self):
        for index, (status, expected) in enumerate((
            (Expense.Status.REGISTERED, True),
            (Expense.Status.VALIDATED, True),
            (Expense.Status.CANCELLED, False),
            (Expense.Status.ANNULLED, False),
        )):
            with self.subTest(status=status):
                allocation = create_allocation(
                    donation=create_donation(code=f'DON-EXP-{index}', amount=Decimal('100.00')),
                    project=create_project(code=f'PRJ-{status}'),
                )
                create_expense(allocation=allocation, status=status)
                self.assertEqual(allocation_has_effective_expenses(allocation), expected)
                if expected:
                    with self.assertRaises(ValidationError):
                        annul_fund_allocation(allocation.pk, actor=self.actor, reason=VALID_REASON)
                else:
                    self.assertEqual(
                        annul_fund_allocation(allocation.pk, actor=self.actor, reason=VALID_REASON).status,
                        FundAllocation.Status.ANNULLED,
                    )

    def test_terminal_allocation_cannot_be_updated_by_service(self):
        allocation = create_allocation()
        annul_fund_allocation(allocation.pk, actor=self.actor, reason=VALID_REASON)

        with self.assertRaises(ValidationError):
            update_fund_allocation(
                allocation=allocation,
                donation=allocation.donation,
                project=allocation.project,
                budget_category=allocation.budget_category,
                amount=allocation.amount,
                responsible_person=allocation.responsible_person,
                allocation_date=allocation.allocation_date,
                status=allocation.status,
                notes='Intento de edición',
            )


class TerminalActionViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='terminal-view-actor',
            password='pass-12345',
        )
        self.client.force_login(self.user)

    def test_project_finish_get_is_safe_and_post_closes(self):
        project = create_project(code='PRJ-VIEW-FINISH')
        project.status = Project.Status.ACTIVE
        project.save(update_fields=('status', 'updated_at'))
        url = reverse('project_finish', args=[project.pk])

        self.assertEqual(self.client.get(url).status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ACTIVE)

        self.assertRedirects(self.client.post(url), reverse('project_detail', args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.CLOSED)
        self.assertEqual(self.client.get(reverse('project_update', args=[project.pk])).status_code, 403)

    def test_annulment_view_requires_valid_reason_and_preserves_failure(self):
        donation = create_donation()
        url = reverse('donation_annul', args=[donation.pk])

        response = self.client.post(url, {'reason': '   '})

        self.assertEqual(response.status_code, 200)
        donation.refresh_from_db()
        self.assertNotEqual(donation.status, Donation.Status.ANNULLED)
        self.assertContains(response, 'Este campo es obligatorio')
        self.assertFalse(
            AuditLog.objects.filter(entity_id=str(donation.pk), action=AuditLog.Action.ANNULLED).exists()
        )

    def test_terminal_entities_hide_edit_and_reject_generic_terminal_route(self):
        donation = create_donation()
        annul_donation(donation.pk, actor=self.user, reason=VALID_REASON)

        detail = self.client.get(reverse('donation_detail', args=[donation.pk]))
        self.assertNotContains(detail, reverse('donation_update', args=[donation.pk]))
        self.assertContains(detail, VALID_REASON)
        self.assertEqual(self.client.get(reverse('donation_update', args=[donation.pk])).status_code, 403)

        response = self.client.post(
            reverse('donation_status_transition', args=[donation.pk, Donation.Status.RECEIVED]),
            follow=True,
        )
        donation.refresh_from_db()
        self.assertEqual(donation.status, Donation.Status.ANNULLED)
        self.assertContains(response, 'no está permitida')

    def test_terminal_routes_preserve_permissions_and_404(self):
        project = create_project(code='PRJ-PERM-TERMINAL')
        limited = get_user_model().objects.create_user('terminal-limited', password='pass-12345')
        self.client.force_login(limited)

        self.assertEqual(self.client.get(reverse('project_annul', args=[project.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('project_annul', args=[project.pk]), {'reason': VALID_REASON}).status_code, 403)

        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('project_annul', args=[999999])).status_code, 404)

    def test_admin_terminal_fields_and_entities_are_readonly(self):
        project = create_project(code='PRJ-ADMIN-TERMINAL')
        project.status = Project.Status.CLOSED
        project.save(update_fields=('status', 'updated_at'))

        donation = create_donation(code='DON-ADMIN-TERMINAL')
        allocation = create_allocation(
            donation=donation,
            project=create_project(code='PRJ-ADMIN-ALLOCATION'),
        )
        for admin_class, model, instance in (
            (ProjectAdmin, Project, project),
            (DonationAdmin, Donation, donation),
            (FundAllocationAdmin, FundAllocation, allocation),
        ):
            model_admin = admin_class(model, admin.site)
            readonly = model_admin.get_readonly_fields(request=None, obj=instance)
            self.assertIn('terminal_reason', readonly)
            self.assertIn('terminal_at', readonly)
            self.assertIn('terminal_by', readonly)
        self.assertIn('name', ProjectAdmin(Project, admin.site).get_readonly_fields(None, project))
