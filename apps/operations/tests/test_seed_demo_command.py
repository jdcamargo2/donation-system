import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.operations.models import Donation, Expense, Project, ProjectUpdate
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR, ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN


class SeedSigedonDemoCommandTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def run_seed(self):
        with patch.dict(
            'os.environ',
            {
                'SIGEDON_DEMO_USERNAME': 'demo-test',
                'SIGEDON_DEMO_EMAIL': 'demo-test@sigedon.local',
                'SIGEDON_DEMO_PASSWORD': 'demo-test-12345',
            },
        ):
            call_command('seed_sigedon_demo', stdout=StringIO())

    def test_seed_command_creates_demo_operational_data(self):
        self.run_seed()

        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())
        self.assertTrue(Donation.objects.filter(code='DON-DEMO-001').exists())
        self.assertTrue(ProjectUpdate.objects.filter(status=ProjectUpdate.Status.APPROVED).exists())
        self.assertFalse(Donation.objects.exclude(currency='USD').exists())
        self.assertFalse(Expense.objects.exclude(currency='USD').exists())

    def test_seed_command_creates_role_demo_users(self):
        self.run_seed()

        expected_users = {
            'admin_sigedon': ROLE_SIGEDON_ADMIN,
            'campo_sigedon': ROLE_FIELD_OPERATOR,
            'auditor_sigedon': ROLE_EXTERNAL_AUDITOR,
        }
        for username, role_name in expected_users.items():
            with self.subTest(username=username):
                user = get_user_model().objects.get(username=username)
                self.assertTrue(user.groups.filter(name=role_name).exists())
                self.assertTrue(Group.objects.filter(name=role_name).exists())

    def test_seed_command_is_idempotent_for_key_entities(self):
        self.run_seed()
        self.run_seed()

        self.assertEqual(Project.objects.filter(code='PRJ-DEMO-001').count(), 1)
        self.assertEqual(Project.objects.filter(code='PRJ-DEMO-002').count(), 1)
        self.assertEqual(Donation.objects.filter(code='DON-DEMO-001').count(), 1)
        self.assertEqual(ProjectUpdate.objects.filter(title='Entrega alimentaria aprobada Demo').count(), 1)
