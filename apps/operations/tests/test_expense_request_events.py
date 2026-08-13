from decimal import Decimal
from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase

from apps.operations.models import (
    ExpenseRequestEvent,
    ExpenseRequestEventImmutableError,
    OperationalCodeSequence,
)
from apps.operations.tests.helpers import create_expense_request, create_user


class ExpenseRequestEventImmutabilityTests(TestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        self.user = create_user(username='er-event-actor')
        self.request = create_expense_request(requested_by=self.user)
        self.event = ExpenseRequestEvent.objects.create(
            expense_request=self.request,
            event_type=ExpenseRequestEvent.EventType.CREATED,
            actor=self.user,
            from_status='',
            to_status=self.request.status,
            requested_amount=self.request.requested_amount,
            allocation_balance_before=Decimal('60.00'),
            allocation_balance_after=Decimal('60.00'),
            metadata={'source': 'test'},
        )

    def test_event_insert_succeeds(self):
        self.assertIsNotNone(self.event.pk)
        self.assertEqual(
            str(self.event),
            f'{self.request.code} · Solicitud creada',
        )

    def test_instance_update_is_rejected(self):
        self.event.reason = 'Alterado'

        with self.assertRaises(ExpenseRequestEventImmutableError):
            self.event.save()

        self.assertEqual(
            ExpenseRequestEvent.objects.get(pk=self.event.pk).reason,
            '',
        )

    def test_queryset_update_is_rejected(self):
        with self.assertRaises(ExpenseRequestEventImmutableError):
            ExpenseRequestEvent.objects.filter(pk=self.event.pk).update(reason='Alterado')

    def test_instance_delete_is_rejected(self):
        with self.assertRaises(ExpenseRequestEventImmutableError):
            self.event.delete()

        self.assertTrue(ExpenseRequestEvent.objects.filter(pk=self.event.pk).exists())

    def test_queryset_delete_is_rejected(self):
        with self.assertRaises(ExpenseRequestEventImmutableError):
            ExpenseRequestEvent.objects.filter(pk=self.event.pk).delete()

    def test_bulk_update_is_rejected(self):
        self.event.reason = 'Alterado'

        with self.assertRaises(ExpenseRequestEventImmutableError):
            ExpenseRequestEvent.objects.bulk_update([self.event], ['reason'])

    def test_metadata_defaults_to_independent_dictionaries(self):
        first = ExpenseRequestEvent(
            expense_request=self.request,
            event_type=ExpenseRequestEvent.EventType.UPDATED,
            actor=self.user,
            requested_amount=self.request.requested_amount,
            allocation_balance_before=Decimal('60.00'),
            allocation_balance_after=Decimal('60.00'),
        )
        second = ExpenseRequestEvent(
            expense_request=self.request,
            event_type=ExpenseRequestEvent.EventType.UPDATED,
            actor=self.user,
            requested_amount=self.request.requested_amount,
            allocation_balance_before=Decimal('60.00'),
            allocation_balance_after=Decimal('60.00'),
        )
        first.metadata['marker'] = 'one'
        self.assertNotIn('marker', second.metadata)
        first.save()
        second.save()
        self.assertEqual(
            ExpenseRequestEvent.objects.get(pk=second.pk).metadata,
            {},
        )

    def test_event_ordering_is_deterministic(self):
        later = ExpenseRequestEvent.objects.create(
            expense_request=self.request,
            event_type=ExpenseRequestEvent.EventType.UPDATED,
            actor=self.user,
            requested_amount=self.request.requested_amount,
            allocation_balance_before=Decimal('60.00'),
            allocation_balance_after=Decimal('60.00'),
        )
        ordered = list(
            ExpenseRequestEvent.objects.filter(expense_request=self.request).values_list(
                'pk', flat=True
            )
        )

        self.assertEqual(ordered, sorted(ordered))
        self.assertIn(self.event.pk, ordered)
        self.assertIn(later.pk, ordered)

    def test_deleting_request_is_protected_by_events(self):
        with self.assertRaises(ProtectedError):
            self.request.delete()


@skipUnless(connection.vendor == 'postgresql', 'Requires the PostgreSQL append-only trigger.')
class ExpenseRequestEventPostgresqlTriggerTests(TransactionTestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        for namespace, prefix in (
            ('project', 'PRJ'),
            ('donation', 'DON'),
            ('fund_allocation', 'ASG'),
            ('expense', 'GAS'),
        ):
            OperationalCodeSequence.objects.update_or_create(
                namespace=namespace,
                defaults={'prefix': prefix, 'next_value': 1},
            )
        self.user = create_user(username='er-event-pg')
        self.request = create_expense_request(requested_by=self.user)
        self.event = ExpenseRequestEvent.objects.create(
            expense_request=self.request,
            event_type=ExpenseRequestEvent.EventType.CREATED,
            actor=self.user,
            requested_amount=self.request.requested_amount,
            allocation_balance_before=Decimal('60.00'),
            allocation_balance_after=Decimal('60.00'),
        )

    def test_direct_sql_update_is_rejected(self):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE operations_expenserequestevent SET reason = %s WHERE id = %s',
                        ['Alterado por SQL directo.', self.event.pk],
                    )

        self.assertIn('append-only', str(raised.exception))
        self.assertEqual(
            ExpenseRequestEvent.objects.get(pk=self.event.pk).reason,
            '',
        )

    def test_direct_sql_delete_is_rejected(self):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'DELETE FROM operations_expenserequestevent WHERE id = %s',
                        [self.event.pk],
                    )

        self.assertIn('append-only', str(raised.exception))
        self.assertTrue(ExpenseRequestEvent.objects.filter(pk=self.event.pk).exists())
