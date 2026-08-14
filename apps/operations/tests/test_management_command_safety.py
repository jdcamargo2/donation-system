"""OPS-COMMAND-SAFETY: production guard for seed_sigedon_demo."""

from __future__ import annotations

import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
)


class SeedSigedonDemoProductionGuardTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media)
        self.media_override.enable()

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def _counts(self):
        User = get_user_model()
        return {
            "users": User.objects.count(),
            "institutions": Institution.objects.count(),
            "projects": Project.objects.count(),
            "donations": Donation.objects.count(),
            "allocations": FundAllocation.objects.count(),
            "expenses": Expense.objects.count(),
            "updates": ProjectUpdate.objects.count(),
            "audits": AuditLog.objects.count(),
        }

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_debug_false_refuses_before_mutation(self):
        before = self._counts()
        stdout = StringIO()
        stderr = StringIO()

        with self.assertRaisesMessage(
            CommandError,
            "seed_sigedon_demo is disabled when DEBUG=False.",
        ):
            call_command("seed_sigedon_demo", stdout=stdout, stderr=stderr)

        self.assertEqual(self._counts(), before)
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("Base demo preparada", combined)
        self.assertNotIn("Sincronizando roles", combined)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_debug_false_does_not_invoke_seed_body(self):
        with patch(
            "apps.operations.management.commands.seed_sigedon_demo.seed_sigedon_demo"
        ) as seed_body:
            with self.assertRaises(CommandError):
                call_command("seed_sigedon_demo", stdout=StringIO())
        seed_body.assert_not_called()

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_env_override_hint_cannot_bypass_absolute_refusal(self):
        # Policy A: absolute refusal; no SIGEDON_ALLOW_PRODUCTION_DEMO_SEED bypass.
        before = self._counts()
        with patch.dict(
            "os.environ",
            {"SIGEDON_ALLOW_PRODUCTION_DEMO_SEED": "YES"},
        ):
            with self.assertRaises(CommandError):
                call_command("seed_sigedon_demo", stdout=StringIO())
        self.assertEqual(self._counts(), before)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_no_production_override_cli_flags_exist(self):
        from django.core.management import load_command_class

        command = load_command_class("apps.operations", "seed_sigedon_demo")
        parser = command.create_parser("manage.py", "seed_sigedon_demo")
        destinations = {action.dest for action in parser._actions}
        self.assertNotIn("allow_production", destinations)
        self.assertNotIn("confirm_production_seed", destinations)
        self.assertNotIn("force", destinations)

    @override_settings(DEBUG=True)
    def test_debug_true_retains_expected_behavior_without_printing_password(self):
        stdout = StringIO()
        secret = "demo-local-secret-never-print"

        with patch.dict("os.environ", {"SIGEDON_DEMO_PASSWORD": secret}):
            call_command(
                "seed_sigedon_demo",
                password=secret,
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertIn("Demo credentials configured.", output)
        self.assertNotIn(secret, output)
        self.assertTrue(Project.objects.filter(code="PRJ-DEMO-001").exists())
        self.assertTrue(
            get_user_model().objects.filter(username="admin_demo").exists()
        )

    @override_settings(DEBUG=True)
    def test_debug_true_repeated_invocation_remains_idempotent(self):
        secret = "demo-local-secret-never-print"
        with patch.dict("os.environ", {"SIGEDON_DEMO_PASSWORD": secret}):
            call_command("seed_sigedon_demo", stdout=StringIO())
            call_command("seed_sigedon_demo", stdout=StringIO())

        self.assertEqual(Project.objects.filter(code="PRJ-DEMO-001").count(), 1)
        self.assertEqual(Donation.objects.filter(code="DON-DEMO-001").count(), 1)
        self.assertEqual(
            get_user_model().objects.filter(username="operador_demo").count(),
            1,
        )
