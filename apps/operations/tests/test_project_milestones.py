from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.operations.milestones import (
    MilestoneProgress,
    MilestoneProgressStatus,
    get_milestone_progress,
)
from apps.operations.models import AuditLog, Project, ProjectMilestone
from apps.operations.tests.helpers import create_project


class ProjectMilestoneModelTests(TestCase):
    def setUp(self):
        self.project = create_project()
        self.user = get_user_model().objects.create_user(username='milestone-user')

    def build_milestone(self, **overrides):
        """
        PRE: overrides contains ProjectMilestone field values for one proposed test record.
        POST: returns an unsaved milestone with valid pending defaults unless explicitly overridden.
        """
        values = {
            'project': self.project,
            'title': 'Primer entregable verificable',
            'position': 1,
            'created_by': self.user,
        }
        values.update(overrides)
        return ProjectMilestone(**values)

    def assert_model_invalid(self, milestone, field):
        """
        PRE: milestone is expected to violate model validation for field.
        POST: asserts full_clean rejects the record and identifies the invalid field.
        """
        with self.assertRaises(ValidationError) as raised:
            milestone.full_clean()
        self.assertIn(field, raised.exception.message_dict)

    def test_valid_pending_milestone_can_be_created(self):
        milestone = self.build_milestone()

        milestone.full_clean()
        milestone.save()

        self.assertFalse(milestone.is_completed)
        self.assertIsNone(milestone.completed_at)
        self.assertIsNone(milestone.completed_by)
        self.assertIn(milestone.title, str(milestone))

    def test_valid_completed_milestone_requires_actor_and_date(self):
        completed_at = timezone.now()
        milestone = self.build_milestone(
            is_completed=True,
            completed_at=completed_at,
            completed_by=self.user,
        )

        milestone.full_clean()
        milestone.save()

        self.assertEqual(milestone.completed_at, completed_at)
        self.assertEqual(milestone.completed_by, self.user)

    def test_position_zero_is_rejected_by_validation_and_database(self):
        self.assert_model_invalid(self.build_milestone(position=0), 'position')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProjectMilestone.objects.create(
                    project=self.project,
                    title='Posición inválida',
                    position=0,
                )

    def test_duplicate_position_is_rejected_within_same_project(self):
        ProjectMilestone.objects.create(project=self.project, title='Uno', position=1)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProjectMilestone.objects.create(project=self.project, title='Dos', position=1)

    def test_same_position_is_allowed_in_different_projects(self):
        other_project = create_project(code='PRJ-MILESTONE-OTHER')

        first = ProjectMilestone.objects.create(project=self.project, title='Uno', position=1)
        second = ProjectMilestone.objects.create(project=other_project, title='Dos', position=1)

        self.assertEqual(first.position, second.position)

    def test_pending_milestone_rejects_completion_date(self):
        self.assert_model_invalid(
            self.build_milestone(completed_at=timezone.now()),
            'completed_at',
        )

    def test_pending_milestone_rejects_completion_actor(self):
        self.assert_model_invalid(self.build_milestone(completed_by=self.user), 'completed_by')

    def test_completed_milestone_rejects_missing_completion_date(self):
        self.assert_model_invalid(
            self.build_milestone(is_completed=True, completed_by=self.user),
            'completed_at',
        )

    def test_new_completed_milestone_rejects_missing_completion_actor(self):
        self.assert_model_invalid(
            self.build_milestone(is_completed=True, completed_at=timezone.now()),
            'completed_by',
        )

    def test_database_rejects_completion_state_without_required_metadata(self):
        invalid_states = (
            {
                'title': 'Pendiente con fecha',
                'is_completed': False,
                'completed_at': timezone.now(),
            },
            {
                'title': 'Pendiente con actor',
                'is_completed': False,
                'completed_by': self.user,
            },
            {
                'title': 'Completado sin fecha',
                'is_completed': True,
                'completed_by': self.user,
            },
        )

        for position, invalid_state in enumerate(invalid_states, start=1):
            with self.subTest(title=invalid_state['title']):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        ProjectMilestone.objects.create(
                            project=self.project,
                            position=position,
                            **invalid_state,
                        )

    def test_blank_or_whitespace_only_title_is_rejected(self):
        for title in ('', '   '):
            with self.subTest(title=repr(title)):
                self.assert_model_invalid(self.build_milestone(title=title), 'title')

    def test_default_ordering_uses_position_then_primary_key(self):
        other_project = create_project(code='PRJ-MILESTONE-ORDER')
        later_position = ProjectMilestone.objects.create(
            project=self.project, title='Posición dos', position=2
        )
        first_at_position_one = ProjectMilestone.objects.create(
            project=self.project, title='Primero en posición uno', position=1
        )
        second_at_position_one = ProjectMilestone.objects.create(
            project=other_project, title='Segundo en posición uno', position=1
        )

        self.assertEqual(ProjectMilestone._meta.ordering, ('position', 'pk'))
        self.assertEqual(
            list(ProjectMilestone.objects.all()),
            [first_at_position_one, second_at_position_one, later_position],
        )

    def test_deleting_project_cascades_to_milestones(self):
        milestone = ProjectMilestone.objects.create(
            project=self.project, title='Hito eliminable', position=1
        )

        self.project.delete()

        self.assertFalse(ProjectMilestone.objects.filter(pk=milestone.pk).exists())

    def test_deleting_creator_sets_created_by_to_null(self):
        milestone = ProjectMilestone.objects.create(
            project=self.project,
            title='Hito con creador',
            position=1,
            created_by=self.user,
        )

        self.user.delete()
        milestone.refresh_from_db()

        self.assertIsNone(milestone.created_by)

    def test_deleting_completer_preserves_historical_completion(self):
        completed_at = timezone.now()
        milestone = ProjectMilestone.objects.create(
            project=self.project,
            title='Hito completado',
            position=1,
            is_completed=True,
            completed_at=completed_at,
            completed_by=self.user,
        )

        self.user.delete()
        milestone.refresh_from_db()
        milestone.full_clean()

        self.assertTrue(milestone.is_completed)
        self.assertEqual(milestone.completed_at, completed_at)
        self.assertIsNone(milestone.completed_by)


class MilestoneProgressTests(TestCase):
    def assert_progress(self, milestones, **expected):
        """
        PRE: milestones is an iterable and expected names MilestoneProgress fields.
        POST: asserts the pure helper returns an immutable result with all expected values.
        """
        progress = get_milestone_progress(milestones)
        self.assertIsInstance(progress, MilestoneProgress)
        for field, value in expected.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(progress, field), value)
        return progress

    def test_zero_milestones_is_undefined(self):
        self.assert_progress(
            [],
            total=0,
            completed=0,
            percentage=None,
            status=MilestoneProgressStatus.UNDEFINED,
            label='Sin hitos definidos',
            is_completed=False,
        )

    def test_progress_cases_use_deterministic_integer_rounding(self):
        cases = (
            ([False], 0, 0, '0 % · En progreso', False),
            ([True], 1, 100, '100 % · Completado', True),
            ([True, False], 1, 50, '50 % · En progreso', False),
            ([True, True, False], 2, 67, '67 % · En progreso', False),
            ([True, True, True, False], 3, 75, '75 % · En progreso', False),
        )

        for states, completed, percentage, label, all_completed in cases:
            with self.subTest(states=states):
                self.assert_progress(
                    [SimpleNamespace(is_completed=state) for state in states],
                    total=len(states),
                    completed=completed,
                    percentage=percentage,
                    status=(
                        MilestoneProgressStatus.COMPLETED
                        if all_completed
                        else MilestoneProgressStatus.IN_PROGRESS
                    ),
                    label=label,
                    is_completed=all_completed,
                )

    def test_list_input_is_not_mutated(self):
        milestones = [
            SimpleNamespace(is_completed=True, marker='first'),
            SimpleNamespace(is_completed=False, marker='second'),
        ]
        snapshot = [(id(item), vars(item).copy()) for item in milestones]

        get_milestone_progress(milestones)

        self.assertEqual([(id(item), vars(item)) for item in milestones], snapshot)

    def test_evaluated_queryset_requires_no_additional_query(self):
        project = create_project(code='PRJ-MILESTONE-QUERYSET')
        ProjectMilestone.objects.create(project=project, title='Uno', position=1)
        ProjectMilestone.objects.create(project=project, title='Dos', position=2)
        milestones = project.milestones.all()
        list(milestones)

        with self.assertNumQueries(0):
            progress = get_milestone_progress(milestones)

        self.assertEqual(progress.total, 2)
        self.assertEqual(progress.completed, 0)

    def test_progress_does_not_depend_on_or_change_project_status(self):
        project = create_project(code='PRJ-MILESTONE-CLOSED')
        project.status = Project.Status.CLOSED
        project.save(update_fields=('status', 'updated_at'))
        ProjectMilestone.objects.create(
            project=project,
            title='Finalizado',
            position=1,
            is_completed=True,
            completed_at=timezone.now(),
            completed_by=get_user_model().objects.create_user(username='closed-completer'),
        )

        progress = get_milestone_progress(list(project.milestones.all()))
        project.refresh_from_db()

        self.assertTrue(progress.is_completed)
        self.assertEqual(progress.status, MilestoneProgressStatus.COMPLETED)
        self.assertEqual(project.status, Project.Status.CLOSED)


class MilestoneAuditCatalogTests(TestCase):
    def test_milestone_actions_fit_field_and_are_available_as_choices(self):
        expected_actions = {'completed', 'reopened', 'reordered', 'deleted'}
        action_field = AuditLog._meta.get_field('action')
        choice_values = {value for value, _label in action_field.choices}

        self.assertTrue(expected_actions <= choice_values)
        self.assertTrue(all(len(value) <= action_field.max_length for value in expected_actions))

    def test_catalog_extension_does_not_change_existing_audit_event(self):
        audit = AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Project',
            entity_id='historical-project',
            entity_label='Proyecto histórico',
            summary='Project created.',
        )

        audit.refresh_from_db()

        self.assertEqual(audit.action, AuditLog.Action.CREATED)
        self.assertEqual(audit.summary, 'Project created.')
