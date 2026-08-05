"""Demo seed public vs internal financial scope reconciliation (BUG-E2E-005)."""

from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.operations.models import Donation, Expense, FundAllocation, Project
from apps.operations.services import publish_project, sum_money
from apps.operations.tests.helpers import create_user
from apps.public_portal.selectors import get_public_transparency_summary


@override_settings(DEBUG=True)
class DemoPublicInternalFinancialReconciliationTests(TestCase):
    """
    Documents why internal dashboard totals and public portal totals differ.

    Internal: institution-wide RECEIVED / assigned / spent.
    Public: ACTIVE + is_public projects only; linked donations are full RECEIVED
    amounts tied to at least one visible allocation (not the assigned slice).
    """

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        self.actor = create_user('demo-reconcile-actor')

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def run_seed(self):
        stdout = StringIO()
        secret = 'demo-reconcile-12345'
        with patch.dict('os.environ', {'SIGEDON_DEMO_PASSWORD': secret}):
            call_command(
                'seed_sigedon_demo',
                password=secret,
                stdout=stdout,
            )
        return stdout.getvalue()

    def test_demo_public_metrics_differ_from_internal_by_scope(self):
        self.run_seed()

        received = sum_money(
            Donation.objects.filter(
                status=Donation.Status.RECEIVED,
                currency='USD',
            ),
            'amount',
        )
        assigned = sum_money(
            FundAllocation.objects.exclude(status=FundAllocation.Status.ANNULLED).filter(
                donation__currency='USD',
            ),
            'amount',
        )
        spent = sum_money(
            Expense.objects.exclude(status=Expense.Status.ANNULLED).filter(
                currency='USD',
            ),
            'amount',
        )
        unallocated = max(received - assigned, Decimal('0.00'))

        self.assertEqual(received, Decimal('225000.00'))
        self.assertEqual(assigned, Decimal('160000.00'))
        self.assertEqual(spent, Decimal('9450.00'))
        self.assertEqual(unallocated, Decimal('65000.00'))

        public = get_public_transparency_summary()
        self.assertEqual(public['linked_received_donations_total'], Decimal('200000.00'))
        self.assertEqual(public['total_assigned'], Decimal('40000.00'))
        self.assertEqual(public['total_executed'], Decimal('4500.00'))
        self.assertEqual(public['available_balance'], Decimal('35500.00'))
        # Public linked donations are not institution-wide received funds.
        self.assertNotEqual(public['linked_received_donations_total'], received)
        self.assertNotEqual(public['total_assigned'], assigned)

        private_project = Project.objects.get(code='PRJ-DEMO-002')
        self.assertFalse(private_project.is_public)
        publish_project(project_id=private_project.pk, actor=self.actor)

        after_publish = get_public_transparency_summary()
        self.assertEqual(
            after_publish['linked_received_donations_total'],
            Decimal('200000.00'),
        )
        self.assertEqual(after_publish['total_assigned'], Decimal('75000.00'))
        self.assertEqual(after_publish['total_executed'], Decimal('6700.00'))
        self.assertEqual(after_publish['available_balance'], Decimal('68300.00'))
