from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.models import AuditLog, Donation, FundAllocation
from apps.operations.tests.helpers import create_donation, create_institution, create_project


class DonationDetailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='donation-detail-admin', password='pass-12345',
        )
        self.client.force_login(self.user)
        self.donor = create_institution(name='Donante del detalle')
        self.donation = create_donation(code='DON-DETAIL-001', donor=self.donor)
        self.donation.received_date = date(2026, 7, 8)
        self.donation.objective = 'Objetivo del detalle.'
        self.donation.restrictions = 'Restricción única.'
        self.donation.save(update_fields=('received_date', 'objective', 'restrictions', 'updated_at'))

    def create_user_with_permissions(self, username, *codenames):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        return user

    def create_allocation(self, *, code, donation, project):
        """
        PRE: code is unique and donation/project are persisted test fixtures.
        POST: creates an allocation without depending on a preserved test database sequence.
        """
        return FundAllocation.objects.create(
            code=code,
            donation=donation,
            project=project,
            budget_category='health_psychosocial',
            amount=Decimal('10.00'),
            allocation_date=date(2026, 7, 8),
        )

    def create_registered_donation(self, *, code):
        donation = create_donation(code=code, donor=self.donor, status=Donation.Status.REGISTERED)
        donation.received_date = date(2026, 7, 9)
        donation.save(update_fields=('received_date', 'updated_at'))
        return donation

    def header_actions_html(self, content):
        return content.split('class="ops-header-actions"', 1)[1].split(
            'ops-donation-financial-summary', 1,
        )[0]

    def dropdown_menu_html(self, content):
        marker = 'ops-donation-detail-action-menu'
        self.assertIn(marker, content)
        return content.split(marker, 1)[1].split('</ul>', 1)[0]

    def test_header_shows_identity_once_and_links_donor_when_allowed(self):
        response = self.client.get(reverse('donation_detail', args=[self.donation.pk]))
        content = response.content.decode()

        self.assertContains(response, self.donation.code)
        self.assertContains(response, self.donation.get_status_display())
        self.assertContains(response, 'Fecha de recepción: 8 de julio de 2026')
        self.assertContains(response, reverse('institution_detail', args=[self.donor.pk]))
        self.assertEqual(content.count('class="badge ops-status-badge"'), 1)
        self.assertNotContains(response, 'Progreso de asignación')

        viewer = self.create_user_with_permissions('donation-only-viewer', 'view_donation')
        self.client.force_login(viewer)
        response = self.client.get(reverse('donation_detail', args=[self.donation.pk]))
        self.assertContains(response, self.donor.name)
        self.assertNotContains(response, reverse('institution_detail', args=[self.donor.pk]))

    def test_contextual_actions_and_delete_confirmation_preserve_fallbacks(self):
        response = self.client.get(reverse('donation_detail', args=[self.donation.pk]))
        delete_url = reverse('donation_delete', args=[self.donation.pk])
        content = response.content.decode()

        self.assertContains(response, reverse('allocation_create'))
        self.assertContains(response, reverse('donation_update', args=[self.donation.pk]))
        self.assertContains(response, 'aria-label="Más acciones para DON-DETAIL-001"')
        self.assertIn(f'href="{delete_url}"', content)
        self.assertIn(f'id="donation-delete-form" method="post" action="{delete_url}"', content)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'data-confirm-title="¿Eliminar esta donación?"')
        self.assertContains(response, 'web/js/confirm_actions.js')

        registered = self.create_registered_donation(code='DON-DETAIL-REGISTERED')
        transition_url = reverse(
            'donation_status_transition', args=[registered.pk, Donation.Status.RECEIVED],
        )
        transition_response = self.client.get(
            reverse('donation_detail', args=[registered.pk])
        )
        self.assertContains(
            transition_response,
            f'id="donation-receive-form-{registered.pk}"',
        )
        self.assertContains(transition_response, 'method="post"')
        self.assertContains(transition_response, f'action="{transition_url}"')
        self.assertContains(transition_response, 'Cambiar a Recibida')

        annulled = create_donation(code='DON-DETAIL-ANNULLED', donor=self.donor)
        annulled.status = Donation.Status.ANNULLED
        annulled.save(update_fields=('status', 'updated_at'))
        response = self.client.get(reverse('donation_detail', args=[annulled.pk]))
        self.assertNotContains(response, reverse('allocation_create'))
        self.assertNotContains(response, reverse('donation_update', args=[annulled.pk]))
        self.assertNotContains(response, 'Cambiar a Recibida')
        self.assertNotContains(response, f'id="donation-receive-form-{annulled.pk}"')

    def test_registered_receive_action_is_visible_outside_dropdown_with_confirmation(self):
        registered = self.create_registered_donation(code='DON-DETAIL-RECEIVE-UI')
        editor = self.create_user_with_permissions(
            'donation-receive-editor',
            'view_donation',
            'change_donation',
            'add_fundallocation',
        )
        self.client.force_login(editor)

        response = self.client.get(reverse('donation_detail', args=[registered.pk]))
        content = response.content.decode()
        header_actions = self.header_actions_html(content)
        dropdown_menu = self.dropdown_menu_html(content)
        transition_url = reverse(
            'donation_status_transition', args=[registered.pk, Donation.Status.RECEIVED],
        )
        form_id = f'donation-receive-form-{registered.pk}'
        receive_button_start = header_actions.index('Cambiar a Recibida')
        button_markup = header_actions[
            header_actions.rindex('<button', 0, receive_button_start):
            header_actions.index('</button>', receive_button_start) + len('</button>')
        ]
        form_markup = header_actions[
            header_actions.index(f'id="{form_id}"'):
            header_actions.index('</form>', header_actions.index(f'id="{form_id}"')) + len('</form>')
        ]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content.count('Cambiar a Recibida'), 1)
        self.assertIn(form_id, header_actions)
        self.assertLess(
            header_actions.index(f'id="{form_id}"'),
            header_actions.index('class="dropdown"'),
        )
        self.assertNotIn('Cambiar a Recibida', dropdown_menu)
        self.assertIn('method="post"', form_markup)
        self.assertIn(f'action="{transition_url}"', form_markup)
        self.assertIn('name="csrfmiddlewaretoken"', form_markup)
        self.assertIn('btn', button_markup)
        self.assertIn('btn-outline-primary', button_markup)
        self.assertNotIn('dropdown-item', button_markup)
        self.assertIn('data-confirm-action', button_markup)
        self.assertIn(f'data-confirm-form="{form_id}"', button_markup)
        self.assertIn(
            'data-confirm-title="¿Marcar esta donación como recibida?"',
            button_markup,
        )
        self.assertIn(
            'data-confirm-text="La donación quedará disponible para su gestión y asignación. '
            'No podrá volver al estado Registrada; solo podrá anularse si no tiene asignaciones activas."',
            button_markup,
        )
        self.assertIn(
            'data-confirm-confirm-label="Sí, marcar como recibida"',
            button_markup,
        )
        self.assertIn('data-confirm-variant="warning"', button_markup)
        self.assertContains(response, 'web/js/confirm_actions.js')

    def test_receive_action_order_is_volver_receive_nueva_asignacion_mas(self):
        registered = self.create_registered_donation(code='DON-DETAIL-RECEIVE-ORDER')
        editor = self.create_user_with_permissions(
            'donation-receive-order',
            'view_donation',
            'change_donation',
            'add_fundallocation',
            'delete_donation',
        )
        self.client.force_login(editor)

        response = self.client.get(reverse('donation_detail', args=[registered.pk]))
        header_actions = self.header_actions_html(response.content.decode())

        volver_pos = header_actions.index('>Volver</a>')
        receive_pos = header_actions.index('Cambiar a Recibida')
        nueva_pos = header_actions.index('Nueva asignación')
        mas_pos = header_actions.index('>Más</button>')

        self.assertLess(volver_pos, receive_pos)
        self.assertLess(receive_pos, nueva_pos)
        self.assertLess(nueva_pos, mas_pos)

    def test_received_donation_hides_receive_action(self):
        response = self.client.get(reverse('donation_detail', args=[self.donation.pk]))
        content = response.content.decode()

        self.assertNotContains(response, 'Cambiar a Recibida')
        self.assertNotContains(response, f'id="donation-receive-form-{self.donation.pk}"')
        self.assertContains(response, reverse('allocation_create'))
        self.assertContains(response, 'aria-label="Más acciones para DON-DETAIL-001"')
        self.assertContains(response, reverse('donation_update', args=[self.donation.pk]))
        self.assertContains(response, reverse('donation_annul', args=[self.donation.pk]))
        self.assertContains(response, reverse('donation_delete', args=[self.donation.pk]))
        self.assertNotIn('Cambiar a Recibida', self.dropdown_menu_html(content))

    def test_unauthorized_user_does_not_see_receive_action(self):
        registered = self.create_registered_donation(code='DON-DETAIL-RECEIVE-DENIED')
        viewer = self.create_user_with_permissions(
            'donation-receive-viewer',
            'view_donation',
        )
        self.client.force_login(viewer)

        response = self.client.get(reverse('donation_detail', args=[registered.pk]))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Cambiar a Recibida')
        self.assertNotContains(response, f'id="donation-receive-form-{registered.pk}"')
        self.assertNotContains(
            response,
            'data-confirm-title="¿Marcar esta donación como recibida?"',
        )
        self.assertNotContains(response, 'web/js/confirm_actions.js')
        self.assertNotIn('ops-donation-detail-action-menu', content)

    def test_unauthorized_post_receive_transition_is_forbidden(self):
        registered = self.create_registered_donation(code='DON-DETAIL-RECEIVE-POST-403')
        viewer = self.create_user_with_permissions(
            'donation-receive-post-viewer',
            'view_donation',
        )
        self.client.force_login(viewer)
        audit_count = AuditLog.objects.count()
        transition_url = reverse(
            'donation_status_transition', args=[registered.pk, Donation.Status.RECEIVED],
        )

        response = self.client.post(transition_url)

        registered.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(registered.status, Donation.Status.REGISTERED)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_receive_without_delete_still_loads_confirm_script(self):
        registered = self.create_registered_donation(code='DON-DETAIL-RECEIVE-SCRIPT')
        editor = self.create_user_with_permissions(
            'donation-receive-no-delete',
            'view_donation',
            'change_donation',
        )
        self.client.force_login(editor)

        response = self.client.get(reverse('donation_detail', args=[registered.pk]))

        self.assertContains(response, 'Cambiar a Recibida')
        self.assertContains(response, 'web/js/confirm_actions.js')
        self.assertNotContains(response, 'donation-delete-form')

    def test_detail_limits_allocations_in_stable_order_and_uses_existing_filter(self):
        allocations = []
        for index in range(6):
            allocation = self.create_allocation(
                code=f'ASG-DON-{index}',
                donation=self.donation,
                project=create_project(code=f'PRJ-DON-{index}'),
            )
            allocation.allocation_date = date(2026, 7, index + 1)
            allocation.save(update_fields=('allocation_date', 'updated_at'))
            allocations.append(allocation)

        response = self.client.get(reverse('donation_detail', args=[self.donation.pk]))

        self.assertEqual(response.context['donation_allocation_count'], 6)
        self.assertTrue(response.context['has_more_donation_allocations'])
        recent = response.context['recent_donation_allocations']
        self.assertEqual([allocation.pk for allocation in recent], [allocation.pk for allocation in reversed(allocations[1:])])
        self.assertTrue(all(
            'project' in allocation._state.fields_cache
            for allocation in recent
        ))
        self.assertContains(response, 'Mostrando 5 de 6')
        self.assertContains(response, f'{reverse("allocation_list")}?q={self.donation.code}')
        self.assertNotContains(response, allocations[0].code)

    def test_empty_optional_fields_and_allocations_are_not_rendered(self):
        donation = create_donation(code='DON-DETAIL-EMPTY', donor=self.donor)
        donation.objective = ''
        donation.restrictions = ''
        donation.commitment_date = None
        donation.support_reference = ''
        donation.save(update_fields=('objective', 'restrictions', 'commitment_date', 'support_reference', 'updated_at'))

        response = self.client.get(reverse('donation_detail', args=[donation.pk]))

        self.assertContains(response, 'Información de la donación')
        self.assertNotContains(response, 'Objetivo')
        self.assertNotContains(response, 'Restricciones de uso')
        self.assertNotContains(response, 'Fecha de compromiso')
        self.assertNotContains(response, 'Referencia de soporte')
        self.assertNotContains(response, '>-<')
        self.assertContains(response, 'Sin asignaciones vinculadas')
        self.assertContains(response, 'Información de registro')

    def test_allocation_rows_do_not_add_queries_or_preload_expenses(self):
        one_allocation_donation = create_donation(code='DON-DETAIL-ONE', donor=self.donor)
        many_allocations_donation = create_donation(code='DON-DETAIL-MANY', donor=self.donor)
        self.create_allocation(
            code='ASG-DON-ONE', donation=one_allocation_donation,
            project=create_project(code='PRJ-DON-ONE'),
        )
        for index in range(6):
            self.create_allocation(
                code=f'ASG-DON-MANY-{index}',
                donation=many_allocations_donation,
                project=create_project(code=f'PRJ-DON-MANY-{index}'),
            )

        self.client.get(reverse('donation_detail', args=[one_allocation_donation.pk]))
        with CaptureQueriesContext(connection) as one_allocation_queries:
            self.client.get(reverse('donation_detail', args=[one_allocation_donation.pk]))
        with CaptureQueriesContext(connection) as many_allocation_queries:
            response = self.client.get(reverse('donation_detail', args=[many_allocations_donation.pk]))

        self.assertEqual(len(one_allocation_queries), len(many_allocation_queries))
        self.assertNotIn('expenses', '\n'.join(query['sql'] for query in many_allocation_queries).lower())
        self.assertTrue(all(
            'project' in allocation._state.fields_cache
            for allocation in response.context['recent_donation_allocations']
        ))
