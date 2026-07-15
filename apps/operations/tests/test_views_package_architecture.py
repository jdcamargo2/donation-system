from importlib import import_module

from django.test import SimpleTestCase

from apps.operations import urls, views


EXPECTED_VIEW_MODULES = (
    'allocations',
    'audit',
    'common',
    'dashboard',
    'donations',
    'expenses',
    'exports',
    'institutions',
    'project_updates',
    'projects',
    'supporting_documents',
)
EXPECTED_ROUTE_COUNT = 68
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


class OperationsViewsPackageArchitectureTests(SimpleTestCase):
    def test_public_package_and_every_domain_module_are_importable(self):
        self.assertIs(import_module('apps.operations.views'), views)
        for module_name in EXPECTED_VIEW_MODULES:
            with self.subTest(module=module_name):
                imported = import_module(f'apps.operations.views.{module_name}')
                self.assertEqual(imported.__name__.rsplit('.', 1)[-1], module_name)

    def test_urls_reference_explicitly_reexported_view_classes_without_duplicates(self):
        route_keys = [(pattern.name, str(pattern.pattern)) for pattern in urls.urlpatterns]

        self.assertEqual(len(route_keys), EXPECTED_ROUTE_COUNT)
        self.assertEqual(len(route_keys), len(set(route_keys)))
        self.assertEqual(
            len([pattern.name for pattern in urls.urlpatterns]),
            len({pattern.name for pattern in urls.urlpatterns}),
        )
        for pattern in urls.urlpatterns:
            view_class = pattern.callback.view_class
            with self.subTest(route=pattern.name):
                self.assertIs(getattr(views, view_class.__name__), view_class)
                self.assertIn(view_class.__name__, views.__all__)

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
