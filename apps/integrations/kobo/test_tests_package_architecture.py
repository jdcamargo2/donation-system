import ast
import importlib
from pathlib import Path
from unittest import TestCase

from django.test.runner import DiscoverRunner

from apps.integrations.kobo import tests as kobo_tests


FUNCTIONAL_TEST_COUNT = 386
TEST_MODULES = (
    "test_association",
    "test_attachments",
    "test_client",
    "test_concurrency",
    "test_contracts",
    "test_discovery",
    "test_importers",
    "test_import_contracts",
    "test_processing",
    "test_prioritized_microprojects",
    "test_prioritization_assessments",
    "test_review",
    "test_routing",
    "test_submissions",
    "test_territorial_models",
    "test_territorial_profiles",
    "test_territorial_routing",
    "test_webhook",
)
MODULES_ALLOWED_TO_IMPORT_SERVICES = {
    "test_association",
    "test_discovery",
    "test_importers",
    "test_import_contracts",
    "test_processing",
    "test_prioritized_microprojects",
    "test_prioritization_assessments",
    "test_routing",
    "test_submissions",
    "test_territorial_profiles",
    "test_territorial_routing",
    "test_webhook",
}


def _flatten_suite(suite):
    # PRE: suite is a Django/unittest suite containing tests or nested suites.
    # POST: yields every contained test exactly once in discovery order.
    for item in suite:
        if hasattr(item, "__iter__"):
            yield from _flatten_suite(item)
        else:
            yield item


class KoboTestsPackageArchitectureTests(TestCase):
    def test_functional_tests_are_split_without_discovery_or_dependency_drift(self):
        tests_directory = Path(kobo_tests.__file__).parent
        self.assertTrue(hasattr(kobo_tests, "__path__"))
        self.assertFalse((tests_directory.parent / "tests.py").exists())

        class_owners = {}
        for module_name in TEST_MODULES:
            importlib.import_module(f"apps.integrations.kobo.tests.{module_name}")
            tree = ast.parse((tests_directory / f"{module_name}.py").read_text())
            imported_modules = {
                node.module
                for node in tree.body
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertNotIn("apps.integrations.kobo.views", imported_modules)
            if module_name not in MODULES_ALLOWED_TO_IMPORT_SERVICES:
                self.assertFalse(
                    any(
                        imported_module.startswith(
                            "apps.integrations.kobo.services"
                        )
                        for imported_module in imported_modules
                    )
                )
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    self.assertNotIn(node.name, class_owners)
                    class_owners[node.name] = module_name

        helpers_tree = ast.parse((tests_directory / "helpers.py").read_text())
        for node in helpers_tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(base) for base in node.bases}
            self.assertFalse(
                bases
                & {"SimpleTestCase", "TestCase", "TransactionTestCase"}
            )

        suite = DiscoverRunner(verbosity=0).build_suite(
            ["apps.integrations.kobo.tests"]
        )
        discovered_tests = list(_flatten_suite(suite))
        test_ids = [test.id() for test in discovered_tests]
        self.assertEqual(len(test_ids), FUNCTIONAL_TEST_COUNT)
        self.assertEqual(len(test_ids), len(set(test_ids)))
