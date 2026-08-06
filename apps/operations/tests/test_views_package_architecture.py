from importlib import import_module

from django.test import SimpleTestCase

from apps.operations import urls, views


EXPECTED_VIEW_MODULES = (
    'allocations',
    'audit',
    'common',
    'dashboard',
    'donations',
    'expense_requests',
    'expenses',
    'exports',
    'institutions',
    'project_milestones',
    'project_updates',
    'projects',
    'protected_files',
    'supporting_documents',
    'user_access',
)

# Primary architecture contract: approved route names grouped by domain.
# Numeric totals are secondary sanity checks only.
EXPECTED_ROUTE_GROUPS = {
    'core': frozenset({
        'dashboard',
    }),
    'institutions': frozenset({
        'institution_list',
        'institution_create',
        'institution_detail',
        'institution_legal_document_preview',
        'institution_legal_document_download',
        'institution_update',
        'institution_delete',
    }),
    'projects': frozenset({
        'project_list',
        'project_export_csv',
        'project_create',
        'project_detail',
        'project_update_chunk',
        'project_update',
        'project_finish',
        'project_publish',
        'project_unpublish',
        'project_milestone_add',
        'project_milestone_edit',
        'project_milestone_complete',
        'project_milestone_reopen',
        'project_milestone_delete',
        'project_milestone_move_up',
        'project_milestone_move_down',
        'project_document_create',
        'project_document_preview',
        'project_document_download',
        'project_document_delete',
        'project_supporting_document_preview',
        'project_supporting_document_download',
    }),
    'project_updates': frozenset({
        'project_update_create_for_project',
        'project_update_list',
        'project_update_create',
        'project_update_detail',
        'project_update_update',
        'project_update_publish',
        'project_update_review_create',
        'project_update_review_detail',
        'project_update_review_decision_create',
        'project_update_review_decision_detail',
        'project_update_remediation_create',
        'project_update_remediation_detail',
        'project_update_remediation_update',
        'project_update_remediation_submit',
        'project_update_remediation_resolve',
        'project_update_remediation_attachment_create',
        'project_update_remediation_attachment_delete',
        'project_update_remediation_attachment_preview',
        'project_update_remediation_attachment_download',
        'project_update_attachment_create',
        'project_update_attachment_preview',
        'project_update_attachment_download',
        'project_update_attachment_delete',
        'project_update_attachment_publish',
        'project_update_attachment_unpublish',
        'project_update_delete',
    }),
    'donations': frozenset({
        'donation_list',
        'donation_export_csv',
        'donation_create',
        'donation_detail',
        'donation_update',
        'donation_delete',
        'donation_annul',
        'donation_status_transition',
    }),
    'allocations': frozenset({
        'allocation_list',
        'allocation_export_csv',
        'allocation_create',
        'allocation_detail',
        'allocation_update',
        'allocation_delete',
        'allocation_annul',
        'allocation_finish',
    }),
    'expense_requests': frozenset({
        'expense_request_list',
        'expense_request_create',
        'expense_request_create_choose_project',
        'expense_request_create_for_project',
        'expense_request_detail',
        'expense_request_update',
        'expense_request_withdraw',
        'expense_request_approve',
        'expense_request_deny',
        'expense_request_annul',
        'expense_request_fulfill',
        'expense_request_attachment_create',
        'expense_request_attachment_delete',
        'expense_request_attachment_preview',
        'expense_request_attachment_download',
    }),
    'expenses': frozenset({
        'expense_list',
        'expense_export_csv',
        'expense_create',
        'expense_detail',
        'expense_update',
        'expense_delete',
        'expense_annul',
        'supporting_document_create_for_expense',
        'supporting_document_preview',
        'supporting_document_download',
        'supporting_document_delete',
    }),
    'audit': frozenset({
        'audit_log_list',
    }),
    'user_access': frozenset({
        'user_access_list',
        'user_access_create',
        'user_access_detail',
        'user_access_update',
        'user_access_activate',
        'user_access_deactivate',
        'user_access_reset_password',
    }),
}

EXPECTED_ROUTE_NAMES = frozenset().union(*EXPECTED_ROUTE_GROUPS.values())
# Secondary sanity: 99 pre-user-access routes + 7 user-access routes.
EXPECTED_ROUTE_COUNT = 106
USER_ACCESS_VIEW_NAMES = (
    'UserAccessListView',
    'UserAccessCreateView',
    'UserAccessDetailView',
    'UserAccessUpdateView',
    'UserAccessActivateView',
    'UserAccessDeactivateView',
    'UserAccessResetPasswordView',
)
INCIDENTAL_NAMES_MUST_STAY_PRIVATE = (
    'Donation',
    'CreateView',
    'ProjectForm',
    'create_expense',
    'DonationForm',
    'ValidationError',
    'transaction',
    'with_donation_list_metrics',
    'AuditMixin',
    'apply_list_filters',
    'FilteredCsvExportView',
)
REPRESENTATIVE_VIEW_METADATA = {
    'DashboardView': ('web/dashboard.html', None, None),
    'InstitutionListView': (
        'web/institution_list.html',
        'operations.view_institution',
        None,
    ),
    'ProjectDetailView': (
        'web/project_detail.html',
        'operations.view_project',
        None,
    ),
    'ProjectUpdateRemediationDetailView': (
        'web/project_update_remediation_detail.html',
        'operations.view_projectupdateremediation',
        None,
    ),
    'DonationListView': (
        'web/donation_list.html',
        'operations.view_donation',
        None,
    ),
    'FundAllocationListView': (
        'web/allocation_list.html',
        'operations.view_fundallocation',
        None,
    ),
    'ExpenseListView': (
        'web/expense_list.html',
        'operations.view_expense',
        None,
    ),
    'SupportingDocumentCreateForExpenseView': (
        'web/supporting_document_form.html',
        'operations.add_supportingdocument',
        None,
    ),
    'AuditLogListView': (
        'web/audit_log_list.html',
        'operations.view_auditlog',
        None,
    ),
    'ProjectCsvExportView': (None, 'operations.view_project', None),
}


def _route_names():
    return [pattern.name for pattern in urls.urlpatterns]


def _route_keys():
    return [(pattern.name, str(pattern.pattern)) for pattern in urls.urlpatterns]


class OperationsViewsPackageArchitectureTests(SimpleTestCase):
    def test_expected_route_groups_are_disjoint_and_complete(self):
        seen = set()
        overlapping = set()
        for group_name, names in EXPECTED_ROUTE_GROUPS.items():
            collision = seen & names
            if collision:
                overlapping.update(collision)
            seen.update(names)
            with self.subTest(group=group_name):
                self.assertTrue(names, msg=f'Route group {group_name!r} must not be empty')

        actual = set(_route_names())
        missing = EXPECTED_ROUTE_NAMES - actual
        unexpected = actual - EXPECTED_ROUTE_NAMES
        self.assertFalse(
            overlapping,
            msg=f'Duplicate route names across EXPECTED_ROUTE_GROUPS: {sorted(overlapping)}',
        )
        self.assertEqual(
            EXPECTED_ROUTE_NAMES,
            actual,
            msg=(
                f'missing={sorted(missing)}; unexpected={sorted(unexpected)}; '
                f'duplicates_across_groups={sorted(overlapping)}'
            ),
        )
        self.assertEqual(
            len(EXPECTED_ROUTE_NAMES),
            EXPECTED_ROUTE_COUNT,
            msg='Secondary count must match the union of approved route groups',
        )

    def test_public_package_and_every_domain_module_are_importable(self):
        self.assertIs(import_module('apps.operations.views'), views)
        for module_name in EXPECTED_VIEW_MODULES:
            with self.subTest(module=module_name):
                imported = import_module(f'apps.operations.views.{module_name}')
                self.assertEqual(imported.__name__.rsplit('.', 1)[-1], module_name)

    def test_urls_reference_explicitly_reexported_view_classes_without_duplicates(self):
        route_keys = _route_keys()
        route_names = _route_names()
        actual_names = set(route_names)
        missing = EXPECTED_ROUTE_NAMES - actual_names
        unexpected = actual_names - EXPECTED_ROUTE_NAMES
        duplicate_names = sorted(
            name for name in actual_names if route_names.count(name) > 1
        )
        duplicate_keys = sorted(
            key for key in set(route_keys) if route_keys.count(key) > 1
        )

        missing_modules = []
        for module_name in EXPECTED_VIEW_MODULES:
            try:
                import_module(f'apps.operations.views.{module_name}')
            except ModuleNotFoundError:
                missing_modules.append(module_name)
        self.assertEqual(
            actual_names,
            EXPECTED_ROUTE_NAMES,
            msg=(
                f'missing={sorted(missing)}; unexpected={sorted(unexpected)}; '
                f'duplicate_names={duplicate_names}; duplicate_keys={duplicate_keys}; '
                f'missing_view_modules={missing_modules}'
            ),
        )
        self.assertEqual(
            len(route_keys),
            EXPECTED_ROUTE_COUNT,
            msg=(
                f'Secondary route count mismatch; missing={sorted(missing)}; '
                f'unexpected={sorted(unexpected)}; duplicate_names={duplicate_names}'
            ),
        )
        self.assertEqual(
            len(route_keys),
            len(set(route_keys)),
            msg=f'Duplicate route pattern/name combinations: {duplicate_keys}',
        )
        self.assertEqual(
            len(route_names),
            len(actual_names),
            msg=f'Duplicate route names: {duplicate_names}',
        )
        self.assertIn('project_update_chunk', actual_names)
        for pattern in urls.urlpatterns:
            view_class = pattern.callback.view_class
            with self.subTest(route=pattern.name):
                self.assertIs(getattr(views, view_class.__name__), view_class)
                self.assertIn(view_class.__name__, views.__all__)

    def test_user_access_views_are_reexported(self):
        for class_name in USER_ACCESS_VIEW_NAMES:
            with self.subTest(view=class_name):
                self.assertTrue(hasattr(views, class_name))
                self.assertIn(class_name, views.__all__)
                self.assertTrue(callable(getattr(views, class_name)))

    def test_facade_exports_only_url_bound_view_classes(self):
        url_view_names = {
            pattern.callback.view_class.__name__ for pattern in urls.urlpatterns
        }
        self.assertEqual(set(views.__all__), url_view_names)
        for name in INCIDENTAL_NAMES_MUST_STAY_PRIVATE:
            with self.subTest(name=name):
                self.assertNotIn(name, views.__all__)
                self.assertFalse(hasattr(views, name))

    def test_demonstrated_external_imports_continue_to_resolve(self):
        for class_name in views.__all__:
            with self.subTest(view=class_name):
                self.assertTrue(callable(getattr(views, class_name)))

    def test_representative_view_metadata_matches_the_pre_split_contract(self):
        for class_name, expected in REPRESENTATIVE_VIEW_METADATA.items():
            view_class = getattr(views, class_name)
            actual = (
                getattr(view_class, 'template_name', None),
                getattr(view_class, 'permission_required', None),
                getattr(view_class, 'success_url', None),
            )
            with self.subTest(view=class_name):
                self.assertEqual(actual, expected)
